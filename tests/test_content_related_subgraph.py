"""
Unit tests for app/core/graph/subgraphs/content_related_subgraph.py —
the dispatch between the implemented "enrolment_issues" sub-category
(enrolment_tools / ENROLMENT_ISSUES_SYSTEM_PROMPT) and every other,
still-stubbed, content_related_issue sub-category (stub_tools /
STUB_SUBGRAPH_SYSTEM_PROMPT), plus the greeting-name extraction this
subgraph adds on top of BaseSubgraph (same pattern as
ProfileUserManagementSubgraph / RecognitionEngagementSubgraph).
"""

import json
from unittest.mock import patch

from app.core.graph.subgraphs.content_related_subgraph import ContentRelatedSubgraph
from app.core.tools.enrolment_tools import get_enrolment_tools
from app.core.tools.stub_tools import get_stub_tools
from app.core.utils.prompt_templates import (
    ENROLMENT_ISSUES_SYSTEM_PROMPT,
    RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT,
    STUB_SUBGRAPH_SYSTEM_PROMPT,
)


def _tool_result(tool, summary_dict):
    return {"tool": tool, "summary": json.dumps(summary_dict)}


# ── _is_implemented ──────────────────────────────────────────────────────────

class TestIsImplemented:
    def test_enrolment_issues_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "enrolment_issues"}) is True

    def test_rating_feedback_issue_is_implemented(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({"sub_category": "unable_to_submit_rating_feedback"}) is True

    def test_other_sub_categories_are_stubbed(self):
        subgraph = ContentRelatedSubgraph()

        for sub_category in [
            "course_program_progress_issue",
            "content_resource_not_opening",
            "event_related_issue",
            "certificate_issue",
            "",
        ]:
            assert subgraph._is_implemented({"sub_category": sub_category}) is False

    def test_missing_sub_category_is_stubbed(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._is_implemented({}) is False


# ── system_prompt ─────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_enrolment_issues_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "enrolment_issues", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == ENROLMENT_ISSUES_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_rating_feedback_issue_uses_dedicated_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "unable_to_submit_rating_feedback", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == RATING_FEEDBACK_ISSUE_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_other_sub_category_uses_stub_prompt(self):
        subgraph = ContentRelatedSubgraph()
        state = {"sub_category": "certificate_issue", "main_category": "content_related_issue"}

        prompt = subgraph.system_prompt(state)

        assert prompt == STUB_SUBGRAPH_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )

    def test_missing_state_fields_default(self):
        subgraph = ContentRelatedSubgraph()

        prompt = subgraph.system_prompt({"sub_category": "enrolment_issues"})

        assert prompt == ENROLMENT_ISSUES_SYSTEM_PROMPT.format(
            email="unknown", main_category="content_related_issue"
        )


# ── get_tools ─────────────────────────────────────────────────────────────────

class TestGetTools:
    def test_enrolment_issues_returns_enrolment_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "enrolment_issues"})

        assert {t.name for t in tools} == {t.name for t in get_enrolment_tools()}

    def test_rating_feedback_issue_returns_no_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "unable_to_submit_rating_feedback"})

        assert tools == []

    def test_other_sub_category_returns_stub_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({"sub_category": "event_related_issue"})

        assert tools == get_stub_tools()

    def test_missing_sub_category_returns_stub_tools(self):
        subgraph = ContentRelatedSubgraph()

        tools = subgraph.get_tools({})

        assert tools == get_stub_tools()


# ── _extract_first_name / execute_node ──────────────────────────────────────

class TestExtractFirstName:
    def test_found_via_eligibility_profile(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("get_user_eligibility_profile", {"found": True, "first_name": "Meera"})]

        assert subgraph._extract_first_name(tool_results) == "Meera"

    def test_ignores_unrelated_tools(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("search_course_or_program", {"first_name": "Should Not Match"})]

        assert subgraph._extract_first_name(tool_results) is None

    def test_no_tool_results(self):
        subgraph = ContentRelatedSubgraph()

        assert subgraph._extract_first_name([]) is None

    def test_malformed_json_is_skipped_not_raised(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [{"tool": "get_user_eligibility_profile", "summary": "not valid json"}]

        assert subgraph._extract_first_name(tool_results) is None

    def test_found_false_with_no_first_name(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [_tool_result("get_user_eligibility_profile", {"found": False})]

        assert subgraph._extract_first_name(tool_results) is None

    def test_uses_last_matching_result(self):
        subgraph = ContentRelatedSubgraph()
        tool_results = [
            _tool_result("get_user_eligibility_profile", {"first_name": "First"}),
            _tool_result("get_user_eligibility_profile", {"first_name": "Second"}),
        ]

        assert subgraph._extract_first_name(tool_results) == "Second"


class TestExecuteNode:
    @patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node")
    def test_merges_first_name_when_found(self, mock_super_execute):
        mock_super_execute.return_value = {
            "tool_results": [_tool_result("get_user_eligibility_profile", {"first_name": "Meera"})],
        }
        subgraph = ContentRelatedSubgraph()

        result = subgraph.execute_node({"ticket_id": "t1"})

        assert result["user_first_name"] == "Meera"

    @patch("app.core.graph.subgraphs.base_subgraph.BaseSubgraph.execute_node")
    def test_no_key_added_when_name_not_found(self, mock_super_execute):
        mock_super_execute.return_value = {"tool_results": []}
        subgraph = ContentRelatedSubgraph()

        result = subgraph.execute_node({"ticket_id": "t1"})

        assert "user_first_name" not in result
