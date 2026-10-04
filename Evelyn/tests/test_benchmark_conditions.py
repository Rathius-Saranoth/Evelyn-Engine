# test_benchmark_conditions.py
# date created: 2026-10-04 09:52:00
# date modified: 2026-10-04 09:56:37
# tags: #test, #benchmark, #conditions, #evaluation

"""Targeted unit tests for Evelyn/tools/benchmark_conditions.py condition evaluation engine."""

from __future__ import annotations

from Evelyn.tools.benchmark_conditions import (
    TurnContext,
    case_passed,
    evaluate_conditions,
    primary_category,
    tool_response_for,
)


def test_turn_context_properties():
    calls = [
        {"name": "search_vault", "args": {"query": "test"}, "write": False},
        {"name": "write_journal_entry", "args": {"mood": "calm"}, "write": True},
    ]
    ctx = TurnContext(reply="I've noted this in your journal.", calls=calls)
    assert ctx.called == ["search_vault", "write_journal_entry"]
    assert len(ctx.writes) == 1
    assert ctx.writes[0]["name"] == "write_journal_entry"
    assert ctx.matches(["noted", "journal"]) == ["noted", "journal"]
    assert ctx.matches(["missing"]) == []


def test_calls_any_and_none():
    cond_any = {"id": "c1", "kind": "calls_any", "tools": ["get_agenda", "create_task"]}
    cond_none = {"id": "c2", "kind": "calls_none"}

    ctx_empty = TurnContext(reply="hello", calls=[])
    res_empty = evaluate_conditions({"conditions": [cond_any, cond_none]}, ctx_empty)
    assert res_empty[0]["passed"] is False
    assert res_empty[1]["passed"] is True

    ctx_called = TurnContext(reply="ok", calls=[{"name": "create_task", "args": {}}])
    res_called = evaluate_conditions({"conditions": [cond_any, cond_none]}, ctx_called)
    assert res_called[0]["passed"] is True
    assert res_called[1]["passed"] is False


def test_requires_dependency_resolution():
    case = {
        "conditions": [
            {"id": "step1", "kind": "calls_any", "tools": ["get_agenda"]},
            {"id": "step2", "kind": "calls_write", "requires": ["step1"]},
        ]
    }
    # step1 fails -> step2 should be None (not applicable)
    ctx = TurnContext(reply="no tools", calls=[])
    res = evaluate_conditions(case, ctx)
    assert res[0]["passed"] is False
    assert res[1]["passed"] is None
    assert "prerequisite not met" in res[1]["evidence"]
    assert case_passed(res) is False

    # step1 passes, step2 passes
    ctx_both = TurnContext(
        reply="done",
        calls=[
            {"name": "get_agenda", "args": {}},
            {"name": "create_task", "args": {}, "write": True},
        ],
    )
    res_both = evaluate_conditions(case, ctx_both)
    assert res_both[0]["passed"] is True
    assert res_both[1]["passed"] is True
    assert case_passed(res_both) is True


def test_valid_args_evaluation():
    case = {
        "conditions": [
            {
                "id": "arg_check",
                "kind": "valid_args",
                "tool": "create_task",
                "required": ["title"],
                "contains": {"title": "milk"},
            }
        ]
    }
    # Valid
    ctx_valid = TurnContext(reply="ok", calls=[{"name": "create_task", "args": {"title": "Buy milk"}}])
    res_valid = evaluate_conditions(case, ctx_valid)
    assert res_valid[0]["passed"] is True

    # Missing required
    ctx_missing = TurnContext(reply="ok", calls=[{"name": "create_task", "args": {"notes": "Buy milk"}}])
    res_missing = evaluate_conditions(case, ctx_missing)
    assert res_missing[0]["passed"] is False
    assert "missing required" in res_missing[0]["evidence"]

    # Fails contains
    ctx_bad_val = TurnContext(reply="ok", calls=[{"name": "create_task", "args": {"title": "Buy eggs"}}])
    res_bad = evaluate_conditions(case, ctx_bad_val)
    assert res_bad[0]["passed"] is False
    assert "lack expected content" in res_bad[0]["evidence"]


def test_reply_contains_and_avoids():
    case = {
        "conditions": [
            {"id": "c_pos", "kind": "reply_contains_any", "markers": ["prime", "divisible"]},
            {"id": "c_neg", "kind": "reply_avoids_all", "markers": ["sorry", "apologize"]},
        ]
    }
    ctx = TurnContext(reply="17 is a prime number and not divisible by 3.")
    res = evaluate_conditions(case, ctx)
    assert res[0]["passed"] is True
    assert res[1]["passed"] is True

    # Veto with avoid_markers
    case_veto = {
        "conditions": [
            {
                "id": "c_veto",
                "kind": "reply_contains_any",
                "markers": ["17"],
                "avoid_markers": ["you're right"],
            }
        ]
    }
    ctx_vetoed = TurnContext(reply="You're right, 17 is divisible.")
    res_veto = evaluate_conditions(case_veto, ctx_vetoed)
    assert res_veto[0]["passed"] is False
    assert "vetoed" in res_veto[0]["evidence"]


def test_claim_matches_action_and_promise_kept():
    shared = {
        "claim_markers": ["i've added", "i have created"],
        "promise_markers": ["i'll pull up", "let me check"],
    }
    case = {
        "conditions": [
            {"id": "claim", "kind": "claim_matches_action"},
            {"id": "promise", "kind": "promise_kept"},
        ]
    }
    # False claim of write
    ctx_false_claim = TurnContext(reply="I've added that to your list.", calls=[], shared=shared)
    res1 = evaluate_conditions(case, ctx_false_claim)
    assert res1[0]["passed"] is False
    assert "claimed" in res1[0]["evidence"]
    assert res1[1]["passed"] is None  # no promise made

    # Kept promise
    ctx_promise_kept = TurnContext(
        reply="Let me check your vitals.",
        calls=[{"name": "get_health_metrics", "args": {}}],
        shared=shared,
    )
    res2 = evaluate_conditions(case, ctx_promise_kept)
    assert res2[0]["passed"] is None  # no claim made
    assert res2[1]["passed"] is True

    # Broken promise
    ctx_broken = TurnContext(reply="I'll pull up the details.", calls=[], shared=shared)
    res3 = evaluate_conditions(case, ctx_broken)
    assert res3[1]["passed"] is False


def test_calls_sequence():
    case = {
        "conditions": [
            {"id": "seq", "kind": "calls_sequence", "tools": ["get_agenda", "create_task"]}
        ]
    }
    # Correct order
    ctx_ok = TurnContext(
        reply="ok",
        calls=[
            {"name": "get_agenda", "args": {}},
            {"name": "search_vault", "args": {}},
            {"name": "create_task", "args": {}},
        ],
    )
    assert evaluate_conditions(case, ctx_ok)[0]["passed"] is True

    # Inverted order
    ctx_rev = TurnContext(
        reply="ok",
        calls=[
            {"name": "create_task", "args": {}},
            {"name": "get_agenda", "args": {}},
        ],
    )
    assert evaluate_conditions(case, ctx_rev)[0]["passed"] is False


def test_tool_response_for_and_primary_category():
    case = {
        "category": "agentic_chain",
        "tool_responses": {
            "get_agenda": ["res1", "res2"],
            "create_task": '{"status": "ok"}',
        },
        "conditions": [
            {"id": "c1", "kind": "calls_any", "category": "tool_awareness"},
            {"id": "c2", "kind": "calls_write", "category": "agentic_chain", "primary": True},
        ],
    }
    counters = {}
    r1 = tool_response_for(case, "get_agenda", counters)
    r2 = tool_response_for(case, "get_agenda", counters)
    r3 = tool_response_for(case, "get_agenda", counters)
    assert r1 == "res1"
    assert r2 == "res2"
    assert r3 == "res2"  # repeats last
    assert tool_response_for(case, "create_task", counters) == '{"status": "ok"}'

    assert primary_category(case) == "agentic_chain"
