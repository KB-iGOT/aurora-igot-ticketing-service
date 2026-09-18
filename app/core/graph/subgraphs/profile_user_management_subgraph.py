"""
subgraphs/profile_user_management_subgraph.py
------------------------------------------------
Specialist subgraph for Profile & User Management issues on iGOT Karmayogi.

Categories handled (from CATEGORY_SUBCATEGORY_MAP → profile_and_user_management):
  - Access Revoked                          [SOP-A1 — implemented, both transfer-already-
                                              raised and no-transfer-raised-yet cases]
  - Email / Mobile already registered       [SOP-A2 — implemented: domain check, duplicate-
                                              registration check, and confirm-then-escalate
                                              flow for accounts already linked elsewhere]
  - Profile Verification / Verified Badge   [not yet implemented — escalates]
  - Designation / Group Not verified        [not yet implemented — escalates]
  - Profile Update                          [SOP-P1/P2/P3/P4 implemented — Name Update,
                                              Display Name Update, Designation Not Found,
                                              Email/Mobile OTP Not Received]

All tools are sourced from app.core.tools.profile_user_management_tools.
The full SOP is embedded in PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT — no KB lookup required.
"""

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.profile_user_management_tools import (
    get_profile_user_management_tools,
)
from app.core.utils.prompt_templates import PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ProfileUserManagementSubgraph(BaseSubgraph):

    CATEGORY = "profile_and_user_management"

    def system_prompt(self, state: TicketState) -> str:
        return PROFILE_USER_MANAGEMENT_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "profile_and_user_management"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_profile_user_management_tools()

    # ── Greeting name fix ────────────────────────────────────────────────────
    #
    # Same fix as CaAparSubgraph/RecognitionEngagementSubgraph: the email
    # greeting falls back to "there" when intake's own name lookup doesn't
    # surface the real first name. get_user_transfer_request_details (SOP-A1)
    # and get_user_profile (SOP-A2, aliased get_own_profile_details) both
    # already return firstName for the ticket owner's own account.

    _NAME_SOURCE_TOOLS = ("get_user_transfer_request_details", "get_user_profile")

    def execute_node(self, state: TicketState) -> TicketState:
        result = super().execute_node(state)
        first_name = self._extract_first_name(result.get("tool_results") or [])
        if first_name:
            result = {**result, "user_first_name": first_name}
        return result

    def _extract_first_name(self, tool_results: list) -> str | None:
        for r in reversed(tool_results):
            if r.get("tool") not in self._NAME_SOURCE_TOOLS:
                continue
            try:
                data = json.loads(r["summary"])
            except Exception:
                continue
            first_name = data.get("firstName") or data.get("first_name")
            if first_name:
                return first_name
        return None

    # ── Genuine dead-end -> real human hand-off, no automated email ──────────
    #
    # SOP-A1's "neither MDO nor YP/SPOC found" dead end (STEP 3 and Edge Case 2)
    # should reach a human agent with no automated email sent, unlike every
    # other escalate=true case in this codebase (which still sends a full
    # email). Reuses the ALREADY-EXISTING low-confidence gate in router_node —
    # no changes needed to main_graph.py or router_node.py: leaving the draft
    # empty makes quality_gate fail once (empty final_response), which sends
    # the ticket back to router_node; setting confidence low there makes
    # router_node's existing confidence check route it to human_queue directly.
    # Detected structurally — escalated_to_human=True AND the last
    # get_yp_am_details call came back found=false — not by matching reason text.

    def decide_node(self, state: TicketState) -> TicketState:
        result = super().decide_node(state)
        if result.get("escalated_to_human") and self._yp_am_lookup_failed(result.get("tool_results") or []):
            logger.info(
                f"[{self.CATEGORY}] Genuine dead end (no MDO/YP found) — no automated "
                f"email; routing to human_queue via the existing low-confidence gate."
            )
            result = {
                **result,
                "resolution_draft": "",
                "confidence": 0.0,
                "escalation_reason": "Access Revoked: no MDO Admin or YP/SPOC contact found for the target organization.",
            }
        return result

    def _yp_am_lookup_failed(self, tool_results: list) -> bool:
        for r in reversed(tool_results):
            if r.get("tool") != "get_yp_am_details":
                continue
            try:
                data = json.loads(r["summary"])
            except Exception:
                continue
            return not data.get("found", False)
        return False


# Singleton — compiled once at import time
profile_user_management_subgraph = ProfileUserManagementSubgraph().build()