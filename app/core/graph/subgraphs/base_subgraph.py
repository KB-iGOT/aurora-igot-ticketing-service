"""
subgraphs/base_subgraph.py
--------------------------
Base class for all iGOT Karmayogi Resolution specialist subgraphs.

Every subgraph runs the same three-node loop:

    Plan  →  Execute  →  Decide
      ↑________________________| (if not resolved and retries left)

State input:  email + message  (the only user-provided fields)
Subclasses override:
  - CATEGORY        : str label shown in logs
  - get_tools()     : list of LangChain tools available to this subgraph
  - system_prompt() : SOP-domain-specific instructions for the LLM
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from app.core.graph.state import TicketState
from app.core.utils.constants import GraphStage, get_llm_model
from app.core.utils.prompt_templates import (
    BASE_DECIDE_HUMAN_MESSAGE,
    BASE_DECIDE_SYSTEM,
    BASE_EXECUTE_HUMAN_MESSAGE,
    BASE_EXECUTE_SYSTEM_SUFFIX,
    BASE_PLAN_HUMAN_MESSAGE,
)
from app.core.utils.token_tracker import token_tracker

logger = logging.getLogger(__name__)

_llm = ChatGoogleGenerativeAI(model=get_llm_model(GraphStage.SUBGRAPH_PLANNING), temperature=0)

# Separate LLM for the tool-calling execute loop — must use a NON-thinking model.
# gemini-3.5-flash embeds a `thought_signature` in every tool call response;
# LangChain strips that signature during message serialization, causing a Gemini
# 400 on the next iteration. gemini-2.5-flash has no thinking capability at all,
# so there is never a thought_signature to lose.
_llm_execute = ChatGoogleGenerativeAI(
    model=get_llm_model(GraphStage.SUBGRAPH_EXECUTION),
    temperature=0,
)


from app.core.utils.ticket_tracker import ticket_tracker


_EMAIL_RE  = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MOBILE_RE = re.compile(r"(?:\+?91[-\s]?)?[6-9]\d{9}\b")

# Context cues used to tell which of several emails/mobiles mentioned in a ticket
# is the NEW one the user wants registered vs. the OLD one being replaced. Tickets
# often mention both in one sentence, e.g. "still showing x@old.com ... the
# updated mail id is y@new.com" — purely regex-based, never sent to the LLM.
_NEW_CUES = re.compile(
    r"\b(?:new|updated?|change(?:d)?|switch(?:ed)?|register(?:ed)?|correct)\b",
    re.IGNORECASE,
)
_OLD_CUES = re.compile(
    r"\b(?:old|previous|existing|current(?:ly)?|still|showing|shows|"
    r"not\s+function(?:al|ing)?|not\s+working|no\s+longer|inactive)\b",
    re.IGNORECASE,
)
_CONTEXT_WINDOW = 60


def _classify_contact_context(raw_message: str, start: int) -> str:
    """Classify a matched email/mobile as 'new', 'old', or 'unknown' from the
    nearest NEW/OLD cue word appearing before it in the raw text."""
    window = raw_message[max(0, start - _CONTEXT_WINDOW):start]
    new_pos = max((m.start() for m in _NEW_CUES.finditer(window)), default=-1)
    old_pos = max((m.start() for m in _OLD_CUES.finditer(window)), default=-1)
    if new_pos == old_pos == -1:
        return "unknown"
    return "new" if new_pos > old_pos else "old"


def _recover_new_contact(raw_message: str, owner_email: str) -> str | None:
    """Recover the real "new" email/mobile the user wants to update to from the
    RAW (unmasked) ticket message.

    execute_node's tool-calling LLM only ever sees a PII-masked copy of the
    message (real emails/phones replaced with `<EMAIL_ADDRESS>` / `<PHONE_NUMBER>`
    placeholders), so any `new_email` / `new_contact` tool argument it produces
    is just that placeholder, not a usable value. Tools take these arguments
    (rather than `email`, which IS already securely re-injected from state)
    because the value they need is the NEW contact the user is asking to
    switch to — which is never the ticket owner's own (already known) email
    and only ever appears in the free-text message.

    When a ticket mentions BOTH the old and new contact (very common — "my
    profile still shows X, please update it to Y"), naively excluding the
    owner's known email and taking "the other one" can return the OLD contact
    instead of the new one, e.g. when the owner's known email IS the new one,
    or isn't mentioned at all. So we first prefer whichever candidate has an
    explicit "new"-style cue word near it, and only fall back to the
    owner-exclusion heuristic when the context is ambiguous.
    """
    raw_message = raw_message or ""
    owner_norm = (owner_email or "").strip().lower()

    def _pick(pattern: "re.Pattern[str]") -> str | None:
        matches = list(pattern.finditer(raw_message))
        if not matches:
            return None
        tagged = [(m.group(0), _classify_contact_context(raw_message, m.start())) for m in matches]

        new_tagged = [v for v, tag in tagged if tag == "new"]
        if new_tagged:
            return new_tagged[-1]

        old_values = {v.strip().lower() for v, tag in tagged if tag == "old"}
        candidates = [v for v, _ in tagged if v.strip().lower() != owner_norm and v.strip().lower() not in old_values]
        if candidates:
            return candidates[-1]

        candidates = [v for v, _ in tagged if v.strip().lower() != owner_norm]
        if candidates:
            return candidates[-1]

        return tagged[-1][0]

    return _pick(_EMAIL_RE) or _pick(_MOBILE_RE)


def _plan_step(ticket_id: str, node: str, detail: str, **extra) -> dict:
    """Create a single graph_plan step dict and push to Elasticsearch."""
    step_dict = {"node": node, "detail": detail, "timestamp": datetime.now().strftime("%H:%M:%S"), **extra}
    if ticket_id and ticket_id != "unknown":
        ticket_tracker.add_step(ticket_id, node, detail, extra)
    return step_dict


class BaseSubgraph(ABC):

    CATEGORY: str = "base"

    @abstractmethod
    def get_tools(self, state: TicketState) -> list:
        """Return LangChain tools available to this subgraph."""
        ...

    @abstractmethod
    def system_prompt(self, state: TicketState) -> str:
        """Return the system prompt for this subgraph's LLM calls."""
        ...

    # ── Node: Plan ────────────────────────────────────────────────────────────

    def plan_node(self, state: TicketState) -> TicketState:
        tid           = state.get("ticket_id", "unknown")
        retry         = state.get("retry_count", 0)
        email         = state.get("email", "unknown")
        message       = state.get("message", "")
        main_category = state.get("main_category", self.CATEGORY)
        current_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[{self.CATEGORY}] plan_node attempt={retry + 1} ticket={tid}")

        prev_text = ""
        previous_results = state.get("tool_results") or []
        if previous_results:
            prev_text = "\n\nPrevious tool results:\n" + "\n".join(
                f"- {r.get('tool')}: {r.get('summary', '')}" for r in previous_results
            )

        feedback_text = ""
        if state.get("quality_gate_feedback"):
            feedback_text = f"\n\n[QUALITY GATE FEEDBACK]\n{state.get('quality_gate_feedback')}"

        # ── Build conversation context (continuations only) ───────────────────
        from app.core.utils.helpers import mask_pii_default

        convo_text = ""
        if state.get("is_continuation"):
            history = state.get("conversation_messages") or []
            if history:
                lines = []
                for m in history:
                    role = "User" if m.get("role") == "user" else "Agent"
                    masked_content = mask_pii_default(m.get('content', ''))
                    lines.append(f"  [{role}] {masked_content}")
                convo_text = (
                    "\n\n[CONTINUATION — this is a follow-up to an open ticket]\n"
                    "Conversation so far:\n" + "\n".join(lines) +
                    "\n\nThe user's latest message (above as 'User Message') is a reply to the agent's last question."
                )

        messages = [
            SystemMessage(content=self.system_prompt(state)),
            HumanMessage(content=BASE_PLAN_HUMAN_MESSAGE.format(
                current_time=current_time,
                main_category=main_category,
                message=mask_pii_default(message),
                convo_text=convo_text,
                prev_text=prev_text + feedback_text,
            )),
        ]

        try:
            plan_resp = _llm.invoke(messages)
            plan = plan_resp.content.strip()
            _usage = getattr(plan_resp, "usage_metadata", None) or {}
            token_tracker.record(
                ticket_id=state.get("ticket_id", ""),
                email=email,
                model=_llm.model,
                prompt_tokens=_usage.get("input_tokens", 0),
                completion_tokens=_usage.get("output_tokens", 0),
                total_tokens=_usage.get("total_tokens", 0),
                node="plan_node",
                category=self.CATEGORY,
            )
        except Exception as e:
            logger.error(f"[{self.CATEGORY}] plan_node LLM error: {e}")
            plan = "Fallback: search KB with main_category filter and give SOP-aligned answer."

        step = _plan_step(
            tid,
            f"{self.CATEGORY}/plan_node",
            f"Attempt {retry + 1}: Generated SOP resolution plan.",
            attempt=retry + 1,
            plan_summary=plan,
        )
        return {**state, "plan": plan, "graph_plan": list(state.get("graph_plan") or []) + [step]}

    # ── Node: Execute ─────────────────────────────────────────────────────────

    def execute_node(self, state: TicketState) -> TicketState:
        from app.core.utils.helpers import mask_pii_default

        tid           = state.get("ticket_id", "unknown")
        email         = state.get("email", "unknown")
        message       = state.get("message", "")
        main_category = state.get("main_category", self.CATEGORY)
        current_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[{self.CATEGORY}] execute_node ticket={tid}")

        tools          = self.get_tools(state)
        tool_map       = {t.name: t for t in tools}
        llm_with_tools = _llm_execute.bind_tools(tools)

        messages = [
            SystemMessage(content=self.system_prompt(state) + BASE_EXECUTE_SYSTEM_SUFFIX),
            HumanMessage(content=BASE_EXECUTE_HUMAN_MESSAGE.format(
                current_time=current_time,
                main_category=main_category,
                message=mask_pii_default(message),
                plan=state.get("plan", ""),
            )),
        ]

        accumulated_results = list(state.get("tool_results") or [])

        try:
            for _ in range(8):
                response = llm_with_tools.invoke(messages)
                # Capture token usage from every execute LLM call
                _usage = getattr(response, "usage_metadata", None) or {}
                token_tracker.record(
                    ticket_id=state.get("ticket_id", ""),
                    email=email,
                    model=_llm_execute.model,
                    prompt_tokens=_usage.get("input_tokens", 0),
                    completion_tokens=_usage.get("output_tokens", 0),
                    total_tokens=_usage.get("total_tokens", 0),
                    node="execute_node",
                    category=self.CATEGORY,
                )
                messages.append(response)

                if not response.tool_calls:
                    break

                for tc in response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = dict(tc["args"])
                    tool_id   = tc["id"]
                    
                    # Securely inject real user email from state if the tool takes an email argument
                    if "email" in tool_args:
                        logger.info(f"[{self.CATEGORY}] secure tool email injection for tool='{tool_name}'")
                        tool_args["email"] = state.get("email", "")

                    # Recover the real new-contact value for tools that take `new_email` /
                    # `new_contact` — the LLM only saw a PII-masked message, so its value
                    # here is just a `<EMAIL_ADDRESS>` placeholder, not a usable argument.
                    for arg_name in ("new_email", "new_contact"):
                        if arg_name in tool_args:
                            recovered = _recover_new_contact(message, state.get("email", ""))
                            if recovered:
                                logger.info(f"[{self.CATEGORY}] secure new-contact recovery for tool='{tool_name}'")
                                tool_args[arg_name] = recovered
                        
                    logger.debug(f"[{self.CATEGORY}] tool={tool_name} args={tool_args}")
                    try:
                        fn = tool_map.get(tool_name)
                        result = fn.invoke(tool_args) if fn else f"Tool '{tool_name}' not available."
                    except Exception as e:
                        result = f"Tool '{tool_name}' failed: {e}"
                        logger.warning(f"[{self.CATEGORY}] {result}")

                    result_str = result if isinstance(result, str) else json.dumps(result, default=str)

                    # Extract _spoc_replacements if present in tool result
                    spoc_map = dict(state.get("spoc_replacements") or {})
                    if isinstance(result_str, str) and "_spoc_replacements" in result_str:
                        try:
                            parsed_res = json.loads(result_str)
                            if isinstance(parsed_res, dict) and "_spoc_replacements" in parsed_res:
                                spoc_map.update(parsed_res.pop("_spoc_replacements"))
                                result_str = json.dumps(parsed_res, indent=2)
                        except Exception as e:
                            logger.debug(f"[{self.CATEGORY}] error stripping _spoc_replacements: {e}")

                    messages.append(ToolMessage(content=result_str, tool_call_id=tool_id))
                    accumulated_results.append({
                        "tool":    tool_name,
                        "args":    tool_args,
                        "summary": result_str,
                    })
        except Exception as e:
            logger.error(f"[{self.CATEGORY}] execute_node error: {e}")

        tools_called = [
            {"tool": r["tool"], "summary": r["summary"]}
            for r in accumulated_results[len(state.get("tool_results") or []):]
        ]
        step = _plan_step(
            tid,
            f"{self.CATEGORY}/execute_node",
            f"Executed {len(tools_called)} tool call(s).",
            tools_called=tools_called,
        )
        return {
            **state,
            "spoc_replacements": spoc_map if 'spoc_map' in locals() else state.get("spoc_replacements"),
            "tool_results": accumulated_results,
            "graph_plan": list(state.get("graph_plan") or []) + [step]
        }

    # ── Node: Decide ──────────────────────────────────────────────────────────

    def decide_node(self, state: TicketState) -> TicketState:
        from app.core.utils.helpers import mask_pii_default

        tid           = state.get("ticket_id", "unknown")
        retry         = state.get("retry_count", 0)
        email         = state.get("email", "unknown")
        message       = state.get("message", "")
        main_category = state.get("main_category", self.CATEGORY)
        current_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[{self.CATEGORY}] decide_node attempt={retry + 1} ticket={tid}")

        tool_summary = "\n".join(
            f"- {r.get('tool')}: {mask_pii_default(r.get('summary', ''))}"
            for r in (state.get("tool_results") or [])
        )

        plan_content = state.get("plan", "")
        if state.get("quality_gate_feedback"):
            plan_content += f"\n\n[QUALITY GATE FEEDBACK]\n{state.get('quality_gate_feedback')}"

        messages = [
            SystemMessage(content=self.system_prompt(state) + "\n\n" + BASE_DECIDE_SYSTEM),
            HumanMessage(content=BASE_DECIDE_HUMAN_MESSAGE.format(
                current_time=current_time,
                main_category=main_category,
                message=mask_pii_default(message),
                plan=plan_content,
                tool_summary=tool_summary or "None",
            )),
        ]

        is_resolved         = False
        needs_clarification = False
        escalate            = False

        draft               = ""
        reason              = ""
        try:
            decide_resp = _llm.invoke(messages)
            raw = decide_resp.content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            _usage = getattr(decide_resp, "usage_metadata", None) or {}
            token_tracker.record(
                ticket_id=state.get("ticket_id", ""),
                email=email,
                model=_llm.model,
                prompt_tokens=_usage.get("input_tokens", 0),
                completion_tokens=_usage.get("output_tokens", 0),
                total_tokens=_usage.get("total_tokens", 0),
                node="decide_node",
                category=self.CATEGORY,
            )
            parsed              = json.loads(raw)
            is_resolved         = bool(parsed.get("resolved", False))
            needs_clarification = bool(parsed.get("needs_clarification", False))
            escalate            = bool(parsed.get("escalate", False))
            draft               = parsed.get("draft", "")
            reason              = parsed.get("reason", "")

            if needs_clarification or escalate:
                is_resolved = True
                if escalate:
                    logger.info(f"[{self.CATEGORY}] decide: escalate → exiting loop. draft='{draft[:120]}'")
                else:
                    logger.info(f"[{self.CATEGORY}] decide: needs_clarification → exiting loop. question='{draft[:120]}'")
            else:
                logger.info(f"[{self.CATEGORY}] decide: resolved={is_resolved} reason={reason}")

        except Exception as e:
            logger.error(f"[{self.CATEGORY}] decide_node LLM error: {e}")

        if escalate:
            outcome = "escalate"
        elif needs_clarification:
            outcome = "needs_clarification"
        elif is_resolved:
            outcome = "resolved"
        else:
            outcome = "retry"

        step = _plan_step(
            tid,
            f"{self.CATEGORY}/decide_node",
            f"Attempt {retry + 1}: outcome='{outcome}'. {reason}",
            attempt=retry + 1,
            outcome=outcome,
            draft_preview=draft if draft else "",
        )
        return {
            **state,
            "is_resolved":        is_resolved,
            "needs_clarification": needs_clarification,
            "escalated_to_human": escalate,
            "partial_match":       False,
            "resolution_draft":   draft if is_resolved else state.get("resolution_draft", ""),
            "retry_count":        retry + 1,
            "graph_plan":         list(state.get("graph_plan") or []) + [step],
        }

    # ── Conditional edge ──────────────────────────────────────────────────────

    def should_retry(self, state: TicketState) -> Literal["plan_node", "done"]:
        if state.get("is_resolved"):
            return "done"
        if state.get("retry_count", 0) >= state.get("max_retries", 3):
            logger.warning(f"[{self.CATEGORY}] Max retries reached — forcing done.")
            return "done"
        return "plan_node"

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self) -> "CompiledGraph":
        g = StateGraph(TicketState)
        g.add_node("plan_node",    self.plan_node)
        g.add_node("execute_node", self.execute_node)
        g.add_node("decide_node",  self.decide_node)
        g.set_entry_point("plan_node")
        g.add_edge("plan_node", "execute_node")
        g.add_edge("execute_node", "decide_node")
        g.add_conditional_edges(
            "decide_node",
            self.should_retry,
            {"plan_node": "plan_node", "done": END}
        )
        return g.compile()
