"""
state.py
--------
Shared TypedDict state that flows through every node and subgraph in the
iGOT Karmayogi Resolution Graph.

Input from user / API:  { email, message }
All other fields are populated by the graph nodes themselves.
All fields are optional so nodes only populate what they touch.
"""

from typing import TypedDict


class TicketState(TypedDict, total=False):
    # ── Inbound (user input) ──────────────────────────────────────────────────
    ticket_id:   str          # auto-generated if not provided
    email:       str          # user's iGOT registered email (primary identifier)
    message:     str          # the raw user message / issue description

    # ── Ticket persistence & multi-turn conversation ──────────────────────────
    interaction_id:        str   # user-facing ticket ID (same value as ticket_id)
    conversation_messages: list  # full history: [{role, content, timestamp}, ...]
    is_continuation:       bool  # True when this is a follow-up reply to a clarification

    # ── Enrichment (intake node) ──────────────────────────────────────────────
    sop_categories:    list   # SOP category keys from CATEGORY_SUBCATEGORY_MAP (prompt_templates.py)
    main_category:     str    # matched SOP category used as filter in search_resolution_knowledge
    category:          str    # normalised routing label
    sub_category:      str    # snake_case sub-category key chosen by LLM (e.g. "certificate_not_generated")
    sub_category_label: str   # human-readable sub-category label (e.g. "Certificate is not generated")
    confidence:        float  # 0.0 – 1.0 classification confidence
    enriched_context:  dict   # KB SOP snippets, classification reason, current time, etc.
    user_first_name:   str    # user's first name fetched from get_user_details at intake

    # ── Junk detection (junk_filter node) ─────────────────────────────────────
    is_junk:         bool     # True when message is irrelevant/greeting/spam
    junk_reason:     str      # explanation for junk classification

    # ── Feature-flag skip (intake node) ───────────────────────────────────────
    is_category_disabled: bool  # True when category is not in ENABLED_CATEGORIES or use_case is in DISABLED_USE_CASES

    # ── Routing ───────────────────────────────────────────────────────────────
    route_to:        str      # subgraph node name or "human_queue"
    routing_reason:  str

    # ── Graph plan / audit trail ──────────────────────────────────────────────
    # Each node appends a step dict: { node, detail, timestamp, ...extra }
    graph_plan:      list     # ordered list of step dicts describing agent decisions

    # ── Subgraph execution (Plan → Execute → Decide loop) ────────────────────
    plan:                  str    # step-by-step SOP resolution plan
    tool_results:          list   # accumulated tool call results
    resolution_draft:      str    # latest drafted resolution response
    is_resolved:           bool   # Decide node verdict — True when resolved OR clarification/partial-match
    needs_clarification:   bool   # True when LLM needs more info from user (not an error)
    partial_match:         bool   # True when a likely fuzzy match was found and presented to user
    retry_count:           int    # how many Plan→Execute→Decide loops so far
    max_retries:           int    # hard cap (default 3)

    # ── Quality gate ──────────────────────────────────────────────────────────
    quality_passed:        bool
    quality_issues:        list   # descriptions of problems found
    quality_reroute_count: int    # how many times quality gate re-routed

    # ── Final output & PII post-processing ────────────────────────────────────
    spoc_replacements:   dict     # ticket-isolated placeholder -> real value mappings
    final_response:      str
    escalated_to_human:  bool
    escalation_reason:   str
    handoff_reason:      str      # short reason the model gave when it escalated (shown in the private Zoho comment)
    api_failure:         bool     # a Karmayogi API/tool call failed in the latest execute pass
    error:               str      # populated if something crashes

