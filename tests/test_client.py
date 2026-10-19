from mcp_ai_assistant.client import (
    AssistantClient,
    InProcessTransport,
    LLMPlanner,
    RuleBasedPlanner,
)
from mcp_ai_assistant.policy import Scope, ToolPolicy


def _client(server, scopes=None):
    policy = ToolPolicy(granted_scopes=scopes or {Scope.READ, Scope.WRITE})
    return AssistantClient(InProcessTransport(server), policy=policy)


def test_discovery(server):
    tools = _client(server).discover_tools()
    assert len(tools) == 5


def test_single_step_order_question(server):
    answer = _client(server).ask("What is the status of order ORD-1001?")
    assert [s.tool for s in answer.steps] == ["order_lookup"]
    assert "delivered" in answer.answer_text
    assert any("ORD-1001" in c for c in (c.render() for c in answer.citations))


def test_multi_step_chain_order_then_stock(server):
    answer = _client(server).ask(
        "What is the status of order ORD-1003 and do we have stock cover for its items?"
    )
    tools_used = [s.tool for s in answer.steps]
    assert tools_used[0] == "order_lookup"
    assert tools_used.count("inventory_check") == 2  # SKU-2003 and SKU-2005
    assert "LOW STOCK" in answer.answer_text  # SKU-2003 is below reorder point


def test_ticket_question(server):
    answer = _client(server).ask("Show me the open critical tickets")
    assert [s.tool for s in answer.steps] == ["ticket_search"]
    assert "TKT-5003" in answer.answer_text


def test_metric_question(server):
    answer = _client(server).ask("Give me a revenue summary for 2026-Q3")
    assert [s.tool for s in answer.steps] == ["metric_summary"]
    assert "4190500" in answer.answer_text


def test_write_denied_by_client_policy(server):
    client = _client(server, scopes={Scope.READ})
    answer = client.ask("Create ticket: Dock door sensor offline at warehouse")
    assert answer.steps == []
    assert len(answer.denied) == 1
    assert "create_ticket" in answer.denied[0]


def test_write_allowed_with_scope(server):
    answer = _client(server).ask("Create ticket: Dock door sensor offline at warehouse")
    assert [s.tool for s in answer.steps] == ["create_ticket"]
    assert "Created ticket TKT-5007" in answer.answer_text


def test_unmatched_request_returns_guidance(server):
    answer = _client(server).ask("What is the weather like today?")
    assert answer.steps == []
    assert "could not map" in answer.answer_text


def test_rule_planner_is_deterministic(server):
    planner = RuleBasedPlanner()
    tools = _client(server).discover_tools()
    first = planner.plan("status of order ORD-1002", tools)
    second = planner.plan("status of order ORD-1002", tools)
    assert [(s.tool, s.arguments) for s in first] == [(s.tool, s.arguments) for s in second]


def test_llm_planner_uses_valid_json_plan(server):
    def fake_llm(prompt: str) -> str:
        assert "order_lookup" in prompt
        return '[{"tool": "order_lookup", "arguments": {"order_id": "ORD-1004"}}]'

    client = AssistantClient(
        InProcessTransport(server), planner=LLMPlanner(fake_llm), policy=ToolPolicy()
    )
    answer = client.ask("anything at all")
    assert [s.tool for s in answer.steps] == ["order_lookup"]
    assert "ORD-1004" in answer.answer_text


def test_llm_planner_falls_back_on_garbage(server):
    client = AssistantClient(
        InProcessTransport(server),
        planner=LLMPlanner(lambda prompt: "I am a language model, not JSON"),
        policy=ToolPolicy(),
    )
    answer = client.ask("Show me the open critical tickets")
    assert [s.tool for s in answer.steps] == ["ticket_search"]
    assert "TKT-5003" in answer.answer_text
