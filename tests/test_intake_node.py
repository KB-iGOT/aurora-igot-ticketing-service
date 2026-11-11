import json
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage
from app.core.graph.nodes.intake_node import intake_node

def test_intake_node_continuation():
    # If is_continuation is True, it should bypass junk detection and classification
    state = {
        "ticket_id": "test_123",
        "email": "user@gov.in",
        "message": "Continuing conversation...",
        "is_continuation": True,
        "category": "certificate",
        "route_to": "certificate_subgraph",
        "quality_reroute_count": 1
    }
    
    with patch("app.core.graph.nodes.intake_node.detect_junk") as mock_detect_junk:
        new_state = intake_node(state)
        
        # Verify that detect_junk was never called
        mock_detect_junk.assert_not_called()
        
        # Verify state preservation
        assert new_state["is_resolved"] is False
        assert new_state["escalated_to_human"] is False
        assert new_state["category"] == "certificate"
        assert new_state["route_to"] == "certificate_subgraph"
        assert len(new_state["graph_plan"]) == 1
        assert new_state["graph_plan"][0]["is_continuation"] is True

def test_intake_node_junk_detection():
    # If a message is classified as junk with high confidence, route it to junk handling
    state = {
        "ticket_id": "test_123",
        "email": "user@gov.in",
        "message": "Hello hello! How are you doing?",
        "is_continuation": False
    }
    
    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(True, 0.9, "Irrelevant greeting")) as mock_detect, \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:
        
        new_state = intake_node(state)
        
        mock_detect.assert_called_once_with("Hello hello! How are you doing?", ticket_id="test_123", email="user@gov.in")
        mock_llm.invoke.assert_not_called()  # Standard classification should be bypassed
        
        assert new_state["is_junk"] is True
        assert new_state["junk_reason"] == "Irrelevant greeting"
        assert new_state["category"] == "junk"
        assert new_state["main_category"] == "junk"
        assert new_state["confidence"] == 0.9
        assert new_state["is_resolved"] is True
        assert "final_response" in new_state
        assert "graph_plan" in new_state

def test_intake_node_standard_flow():
    # Standard query classification and enrichment
    state = {
        "ticket_id": "test_123",
        "email": "user@gov.in",
        "message": "I completed 'Digital Literacy' but my certificate is missing.",
        "is_continuation": False
    }
    
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "category": "certificate",
        "main_category": "certificate",
        "confidence": 0.95,
        "reason": "Missing course completion certificate request."
    })
    mock_llm_response.usage_metadata = {}
    
    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")) as mock_detect, \
         patch("app.core.graph.nodes.intake_node.fetch_sop_categories", return_value=["certificate", "course"]) as mock_sop, \
         patch("app.core.graph.nodes.intake_node.ENABLED_CATEGORIES", ["*"]), \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:
        
        # Mock LLM classifier response
        mock_llm.invoke.return_value = mock_llm_response
        
        new_state = intake_node(state)
        
        # Verifications
        mock_detect.assert_called_once()
        mock_sop.assert_called_once()
        mock_llm.invoke.assert_called_once()
        
        assert new_state["category"] == "certificate"
        assert new_state["main_category"] == "certificate"
        assert new_state["sop_categories"] == ["certificate", "course"]
        assert new_state["confidence"] == 0.95
        assert new_state["is_resolved"] is False
        assert new_state["enriched_context"]["classification_reason"] == "Missing course completion certificate request."


def test_intake_node_disabled_category_skips_flow():
    # If classified category is not in ENABLED_CATEGORIES, gracefully skip execution
    state = {
        "ticket_id": "test_disabled_123",
        "email": "user@gov.in",
        "message": "Enrolled in course but progress not updating.",
        "is_continuation": False
    }

    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "category": "course",
        "main_category": "course",
        "confidence": 0.90,
        "reason": "Course progress issue."
    })
    mock_llm_response.usage_metadata = {}

    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")), \
         patch("app.core.graph.nodes.intake_node.fetch_sop_categories", return_value=["certificate", "course"]), \
         patch("app.core.graph.nodes.intake_node.ENABLED_CATEGORIES", ["certificate"]), \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:

        mock_llm.invoke.return_value = mock_llm_response

        new_state = intake_node(state)

        # Verification: flow is gracefully skipped and completed without updating Zoho
        assert new_state["category"] == "course"
        assert new_state["is_resolved"] is True
        assert new_state["is_category_disabled"] is True
        assert new_state["graph_plan"][-1]["is_category_disabled"] is True


def test_intake_node_enabled_category_continues_flow():
    # If classified category IS in ENABLED_CATEGORIES, continue normal flow
    state = {
        "ticket_id": "test_enabled_123",
        "email": "user@gov.in",
        "message": "Certificate issue.",
        "is_continuation": False
    }

    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "category": "certificate",
        "main_category": "certificate",
        "confidence": 0.95,
        "reason": "Certificate request."
    })
    mock_llm_response.usage_metadata = {}

    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")), \
         patch("app.core.graph.nodes.intake_node.fetch_sop_categories", return_value=["certificate", "course"]), \
         patch("app.core.graph.nodes.intake_node.ENABLED_CATEGORIES", ["certificate"]), \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:

        mock_llm.invoke.return_value = mock_llm_response

        new_state = intake_node(state)

        # Verification: flow continues normally to router
        assert new_state["category"] == "certificate"
        assert new_state["is_resolved"] is False


def test_update_zoho_ticket_direct_disabled_flag():
    from app.core.tools.zoho_tools import update_zoho_ticket_direct
    with patch("app.core.tools.zoho_tools.ENABLE_ZOHO_TICKET_UPDATE", False), \
         patch("app.services.zoho_service.create_draft_reply") as mock_draft:
        res = update_zoho_ticket_direct("12345", "Resolution content")
        assert res == ""
        mock_draft.assert_not_called()


def test_intake_node_validate_email_true_checks_registration():
    state = {
        "ticket_id": "test_reg_true",
        "email": "unregistered@gov.in",
        "message": "APAR plan issue",
        "is_continuation": False
    }
    with patch("app.core.graph.nodes.intake_node.VALIDATE_EMAIL", True), \
         patch("app.core.graph.nodes.intake_node.is_domain_allowed", return_value=True), \
         patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")), \
         patch("app.core.graph.nodes.intake_node.fetch_user_info", return_value=(False, "")) as mock_fetch_user:
        new_state = intake_node(state)
        mock_fetch_user.assert_called_once_with("unregistered@gov.in")
        assert new_state["is_resolved"] is True
        assert new_state["final_response"] == ""
        assert new_state["main_category"] == "general"


def test_intake_node_validate_email_false_skips_registration_check():
    state = {
        "ticket_id": "test_reg_false",
        "email": "unregistered@gov.in",
        "message": "APAR plan issue",
        "is_continuation": False
    }
    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps({
        "category": "ca_apar_issue",
        "main_category": "ca_apar_issue",
        "confidence": 0.95,
        "reason": "APAR plan issue."
    })
    mock_llm_response.usage_metadata = {}

    with patch("app.core.graph.nodes.intake_node.VALIDATE_EMAIL", False), \
         patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")), \
         patch("app.core.graph.nodes.intake_node.fetch_user_info") as mock_fetch_user, \
         patch("app.core.graph.nodes.intake_node.fetch_sop_categories", return_value=["ca_apar_issue"]), \
         patch("app.core.graph.nodes.intake_node.ENABLED_CATEGORIES", ["*"]), \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:
        mock_llm.invoke.return_value = mock_llm_response
        new_state = intake_node(state)
        mock_fetch_user.assert_not_called()
        assert new_state["category"] == "ca_apar_issue"
        assert new_state["is_resolved"] is False





# ── KB-16119: DISABLED_USE_CASES ─────────────────────────────────────────────

_OMIT = object()


def _run_intake_with_use_case(use_case, disabled_use_cases, enabled_categories=("profile_and_user_management",)):
    """Run intake_node with a classifier reply carrying *use_case* (_OMIT = field absent)."""
    payload = {
        "category": "profile_and_user_management",
        "main_category": "profile_and_user_management",
        "sub_category": "Profile Update",
        "confidence": 0.95,
        "reason": "Profile update request.",
    }
    if use_case is not _OMIT:
        payload["use_case"] = use_case

    mock_llm_response = MagicMock(spec=AIMessage)
    mock_llm_response.content = json.dumps(payload)
    mock_llm_response.usage_metadata = {}

    state = {
        "ticket_id": "test_use_case_123",
        "email": "user@gov.in",
        "message": "Please update my designation.",
        "is_continuation": False,
    }
    with patch("app.core.graph.nodes.intake_node.detect_junk", return_value=(False, 0.0, "")), \
         patch("app.core.graph.nodes.intake_node.fetch_sop_categories", return_value=["profile_and_user_management"]), \
         patch("app.core.graph.nodes.intake_node.ENABLED_CATEGORIES", list(enabled_categories)), \
         patch("app.core.graph.nodes.intake_node.DISABLED_USE_CASES", disabled_use_cases), \
         patch("app.core.graph.nodes.intake_node._llm") as mock_llm:
        mock_llm.invoke.return_value = mock_llm_response
        return intake_node(state)


def test_intake_node_disabled_use_case_skips_flow():
    new_state = _run_intake_with_use_case(
        "designation_update_request", ["designation_update_request"]
    )

    assert new_state["is_category_disabled"] is True
    assert new_state["is_resolved"] is True
    assert new_state["final_response"] == ""
    # Category/sub-category are still reported so the skip is traceable.
    assert new_state["category"] == "profile_and_user_management"
    assert new_state["sub_category"] == "profile_update"
    step = new_state["graph_plan"][-1]
    assert step["is_category_disabled"] is True


def test_intake_node_each_configured_use_case_is_skipped():
    disabled = ["designation_update_request", "profile_completion_check", "username_change_request"]
    for tag in disabled:
        assert _run_intake_with_use_case(tag, disabled)["is_category_disabled"] is True


def test_intake_node_use_case_match_is_case_and_whitespace_insensitive():
    new_state = _run_intake_with_use_case(
        "  Username_Change_Request ", ["username_change_request"]
    )
    assert new_state["is_category_disabled"] is True


def test_intake_node_use_case_skips_even_when_all_categories_enabled():
    # ENABLED_CATEGORIES=["*"] disables the category check; the use-case flag is independent.
    new_state = _run_intake_with_use_case(
        "profile_completion_check", ["profile_completion_check"], enabled_categories=("*",)
    )
    assert new_state["is_category_disabled"] is True


def test_intake_node_use_case_not_in_disabled_list_continues():
    new_state = _run_intake_with_use_case("designation_update_request", ["username_change_request"])

    assert new_state.get("is_category_disabled") is not True
    assert new_state["is_resolved"] is False
    assert new_state["sub_category"] == "profile_update"


def test_intake_node_empty_use_case_continues():
    new_state = _run_intake_with_use_case("", ["designation_update_request"])
    assert new_state.get("is_category_disabled") is not True
    assert new_state["is_resolved"] is False


def test_intake_node_missing_use_case_field_continues():
    # Older/other classifier replies omit use_case entirely.
    new_state = _run_intake_with_use_case(_OMIT, ["designation_update_request"])
    assert new_state.get("is_category_disabled") is not True
    assert new_state["is_resolved"] is False


def test_intake_node_null_use_case_continues():
    # JSON null must not crash the .strip() normalisation.
    new_state = _run_intake_with_use_case(None, ["designation_update_request"])
    assert new_state.get("is_category_disabled") is not True
    assert new_state["is_resolved"] is False


def test_intake_node_empty_disabled_list_never_skips():
    new_state = _run_intake_with_use_case("designation_update_request", [])
    assert new_state.get("is_category_disabled") is not True
    assert new_state["is_resolved"] is False


def test_intake_node_disabled_category_still_skips_with_empty_use_case():
    # The original category check must keep working alongside the new flag.
    new_state = _run_intake_with_use_case(
        "", [], enabled_categories=("ca_apar_issue",)
    )
    assert new_state["is_category_disabled"] is True


# ── Classifier prompt + shipped flag defaults ────────────────────────────────

def test_classification_prompt_requests_use_case_field():
    from app.core.utils.prompt_templates import CLASSIFICATION_PROMPT

    rendered = CLASSIFICATION_PROMPT.format(categories="a", subcategory_hint="b", message="c")
    assert '"use_case"' in rendered
    for tag in ("designation_update_request", "profile_completion_check", "username_change_request"):
        assert tag in rendered


def test_disabled_use_cases_defaults():
    from app.core.utils.constants import DISABLED_USE_CASES

    assert set(DISABLED_USE_CASES) == {
        "username_change_request",
    }
