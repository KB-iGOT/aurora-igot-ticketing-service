"""
tools/ticket_tools.py
----------------------
Internal utility functions for the iGOT resolution graph.

These are NOT LangChain tools called by the LLM —
they are plain Python functions called directly by graph nodes.

Functions:
  - quality_gate_check()   — LLM-based quality audit before final delivery
  - send_to_human_queue()  — writes ticket to the human escalation store in ES
  - notify_user()          — delivers the final response to the user
  - log_ticket_outcome()   — writes the execution trace to ES
"""

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.graph.state import TicketState
from app.core.tools.zoho_tools import update_zoho_ticket_direct
from app.core.utils.constants import GraphStage, get_llm_model
from app.core.utils.es_utils import es_manager

logger = logging.getLogger(__name__)


def send_to_human_queue(state: TicketState) -> TicketState:
    """
    Writes the ticket to the human escalation queue.
    In production this would push to a Redis sorted set, a database,
    or a ticketing system (e.g. Freshdesk, Jira Service Management).
    """
    ticket_id = state.get("ticket_id", "unknown")
    reason    = state.get("escalation_reason", "No reason provided")

    logger.info(f"[human_queue] Escalating ticket {ticket_id}: {reason}")

    # ── TODO: replace with your actual queue / ticketing system call ─────────
    # Examples:
    #   redis_client.zadd("human_queue", {json.dumps(state): time.time()})
    #   freshdesk_client.create_ticket(state)
    # ──────────────────────────────────────────────────────────────────────────

    # Zoho is intentionally NOT updated for escalations.
    # A draft reply is only created when the AI successfully resolves a ticket
    # in an enabled category — never for human hand-offs.
    logger.info(f"[human_queue] Skipping Zoho draft for escalated ticket {ticket_id} (HIL hand-off only).")

    log_ticket_outcome(state, outcome="escalated_to_human")

    return {
        **state,
        "final_response":     (
            f"Your request has been escalated to our support team. "
            f"Ticket ID: {ticket_id}. We will get back to you shortly."
        ),
        "escalated_to_human": True,
    }


def notify_user(state: TicketState) -> TicketState:
    """
    Delivers the final_response to the user.
    The delivery mechanism depends on the ticket source:
      - "email"    → send email reply
      - "webhook"  → POST to callback URL
      - "form"     → platform notification
      - "track_a"  → response is returned to the API caller synchronously

    Unmasks any {{PLACEHOLDER}} strings using state.get("spoc_replacements")
    before delivering the final customer-facing response.
    """
    ticket_id = state.get("ticket_id", "unknown")
    source    = state.get("source", "unknown")
    user_id   = state.get("user_id", "unknown")
    response  = state.get("final_response", "")

    # Unmask any ticket-isolated SPOC placeholders (e.g. {{MDO_ADMIN_EMAIL}} -> real email)
    spoc_map = dict(state.get("spoc_replacements") or {})
    if state.get("email"):
        spoc_map.setdefault("{{USER_EMAIL}}", state["email"])
    if spoc_map and response:
        for placeholder, real_val in spoc_map.items():
            response = response.replace(placeholder, str(real_val))
        state = {**state, "final_response": response}

    logger.info(
        f"[notify] Delivering response to user {user_id} "
        f"(ticket {ticket_id}, source {source}): {response[:100]}..."
    )

    # Create a Zoho draft ONLY when the AI genuinely resolved the ticket AND
    # the category is in the enabled list.  Junk, invalid-domain, and
    # unregistered-user early-exit paths all set is_junk=True or arrive here
    # via is_resolved=True without going through a subgraph — we detect those
    # with the combined guard below so they never write to Zoho.
    ai_resolved = (
        not state.get("is_category_disabled")
        and not state.get("is_junk")
        and not state.get("escalated_to_human")
        and state.get("is_resolved", False)
    )
    if ai_resolved:
        update_zoho_ticket_direct(ticket_id, response, "Resolved")
    else:
        logger.info(
            f"[notify] Skipping Zoho draft for ticket {ticket_id} "
            f"(is_category_disabled={state.get('is_category_disabled')}, "
            f"is_junk={state.get('is_junk')}, "
            f"escalated_to_human={state.get('escalated_to_human')}, "
            f"is_resolved={state.get('is_resolved')})"
        )

    if state.get("is_category_disabled"):
        outcome = "category_disabled"
    elif state.get("is_junk"):
        outcome = "junk"
    elif not ai_resolved:
        outcome = "skipped_zoho"
    else:
        outcome = "resolved"
    log_ticket_outcome(state, outcome=outcome)
    return state



def log_ticket_outcome(state: TicketState, outcome: str) -> None:
    """
    Writes the full ticket execution trace to Elasticsearch.
    This is the data that powers analytics and continuous improvement.
    """
    try:
        es_manager.log_interaction(
            user_id=state.get("user_id", "unknown"),
            interface=f"track_b_{state.get('source', 'unknown')}",
            query=f"{state.get('subject', '')} — {state.get('body', '')}",
            response=state.get("final_response", ""),
            metadata={
                "ticket_id":           state.get("ticket_id"),
                "category":            state.get("category"),
                "confidence":          state.get("confidence"),
                "route_to":            state.get("route_to"),
                "retry_count":         state.get("retry_count", 0),
                "quality_passed":      state.get("quality_passed"),
                "quality_issues":      state.get("quality_issues", []),
                "quality_reroute_count": state.get("quality_reroute_count", 0),
                "escalated_to_human":  state.get("escalated_to_human", False),
                "escalation_reason":   state.get("escalation_reason", ""),
                "outcome":             outcome,
                "timestamp":           datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as e:
        logger.warning(f"[log_outcome] ES logging failed for ticket {state.get('ticket_id')}: {e}")
