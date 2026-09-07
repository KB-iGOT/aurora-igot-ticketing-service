"""
subgraphs/recognition_engagement_subgraph.py
----------------------------------------------
Specialist subgraph for recognition & engagement issues on iGOT Karmayogi.

SOP workflows handled (from Agent_SOP_Recognition_Engagement.md):
  SOP-RE1: Karma Points Issue
  SOP-RE2: Weekly Claps Issue
  SOP-RE3: Learning Hours Issue - eHRMS
  SOP-RE4: Learning Hours Issue - Shiksha Path
  SOP-RE5: Learning Hours Issue - SPARROW / APAR
  SOP-RE6: Leader Board Issue

All tools are sourced from app.core.tools.recognition_engagement_tools.
The full SOP is embedded in RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT — no KB lookup required.
"""

import json
import logging

from app.core.graph.state import TicketState
from app.core.graph.subgraphs.base_subgraph import BaseSubgraph
from app.core.tools.recognition_engagement_tools import get_recognition_engagement_tools
from app.core.utils.prompt_templates import RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class RecognitionEngagementSubgraph(BaseSubgraph):

    CATEGORY = "recognition_and_engagement"

    def system_prompt(self, state: TicketState) -> str:
        return RECOGNITION_ENGAGEMENT_SYSTEM_PROMPT.format(
            email=state.get("email", "unknown"),
            main_category=state.get("main_category", "recognition_and_engagement"),
        )

    def get_tools(self, state: TicketState) -> list:
        return get_recognition_engagement_tools()

    # ── Greeting name fix ────────────────────────────────────────────────────
    #
    # Same fix as CaAparSubgraph: the email greeting sometimes falls back to
    # "there" because the name lookup at intake fails. Fix it here by grabbing
    # the first name from a tool result we already have, instead of the user.

    _NAME_SOURCE_TOOLS = ("get_user_first_name", "get_user_profile")

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


# Singleton — compiled once at import time
recognition_engagement_subgraph = RecognitionEngagementSubgraph().build()
