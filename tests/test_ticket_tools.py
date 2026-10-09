"""
Unit tests for notify_user in app/core/tools/ticket_tools.py — the placeholder
unmasking applied just before the Zoho draft is created.
"""

from unittest.mock import patch

import pytest

from app.core.tools.ticket_tools import notify_user


@pytest.fixture
def zoho():
    with patch("app.core.tools.ticket_tools.update_zoho_ticket_direct") as m, \
         patch("app.core.tools.ticket_tools.log_ticket_outcome"):
        yield m


def _state(**over):
    base = {
        "ticket_id": "T1",
        "email": "owner@x.gov.in",
        "final_response": "Current Email ID: {{USER_EMAIL}}",
        "is_resolved": True,
    }
    base.update(over)
    return base


class TestNotifyUserUnmasking:
    def test_user_email_token_resolved_from_state_email(self, zoho):
        out = notify_user(_state())

        assert out["final_response"] == "Current Email ID: owner@x.gov.in"
        zoho.assert_called_once_with("T1", "Current Email ID: owner@x.gov.in", "Resolved")

    def test_tool_supplied_tokens_are_resolved_alongside_user_email(self, zoho):
        out = notify_user(_state(
            final_response="{{USER_EMAIL}} -> {{NEW_CONTACT}}",
            spoc_replacements={"{{NEW_CONTACT}}": "9876543210"},
        ))

        assert out["final_response"] == "owner@x.gov.in -> 9876543210"

    def test_tool_supplied_user_email_is_not_overridden_by_state_email(self, zoho):
        out = notify_user(_state(spoc_replacements={"{{USER_EMAIL}}": "tool@x.gov.in"}))

        assert out["final_response"] == "Current Email ID: tool@x.gov.in"

    def test_no_state_email_leaves_user_email_token_untouched(self, zoho):
        out = notify_user(_state(email=""))

        assert out["final_response"] == "Current Email ID: {{USER_EMAIL}}"

    def test_empty_response_is_left_alone(self, zoho):
        out = notify_user(_state(final_response=""))

        assert out["final_response"] == ""

    def test_state_spoc_replacements_not_mutated(self, zoho):
        original = {"{{NEW_CONTACT}}": "x@y.com"}
        notify_user(_state(spoc_replacements=original))

        assert original == {"{{NEW_CONTACT}}": "x@y.com"}

    def test_escalated_ticket_skips_zoho_draft(self, zoho):
        notify_user(_state(escalated_to_human=True))

        zoho.assert_not_called()
