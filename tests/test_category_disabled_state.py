"""KB-16119: the is_category_disabled flag must survive the LangGraph state schema."""
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from app.core.graph.main_graph import after_intake, early_exit_node
from app.core.graph.state import TicketState
from app.core.tools import zoho_tools


def _disabled_intake(state):
    return {**state, "is_category_disabled": True, "is_resolved": True, "is_junk": False}


def _build_graph():
    g = StateGraph(TicketState)
    g.add_node("intake_node", _disabled_intake)
    g.add_node("router_node", lambda s: s)
    g.add_node("early_exit", early_exit_node)
    g.add_edge(START, "intake_node")
    g.add_conditional_edges(
        "intake_node", after_intake, {"router_node": "router_node", "early_exit": "early_exit"}
    )
    g.add_edge("router_node", END)
    g.add_edge("early_exit", END)
    return g.compile()


def test_state_declares_is_category_disabled():
    assert "is_category_disabled" in TicketState.__annotations__


@patch("app.core.tools.ticket_tools.log_ticket_outcome")
@patch("app.core.tools.zoho_tools.add_zoho_ticket_comment")
def test_disabled_flag_reaches_early_exit_through_graph(mock_comment, mock_log):
    result = _build_graph().invoke({"ticket_id": "T1"})

    assert result["is_category_disabled"] is True
    mock_comment.assert_called_once_with("T1", zoho_tools.OUT_OF_SCOPE_COMMENT)
    assert mock_log.call_args.kwargs["outcome"] == "category_disabled"
