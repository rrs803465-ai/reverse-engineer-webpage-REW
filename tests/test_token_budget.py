"""Tests for token budget enforcement."""

import pytest
from rea.agent.token_budget import BudgetExceededError, TokenBudget


def test_initial_state():
    b = TokenBudget(max_input_tokens=1000, max_output_tokens=500)
    assert b.used_input == 0
    assert b.used_output == 0
    assert b.remaining_input() == 1000
    assert b.remaining_output() == 500


def test_record_request():
    b = TokenBudget(max_input_tokens=1000, max_output_tokens=500)
    b.record_request(100, 50)
    assert b.used_input == 100
    assert b.used_output == 50
    assert b.remaining_input() == 900


def test_input_exceeded():
    b = TokenBudget(max_input_tokens=100)
    b.record_request(90, 0)
    with pytest.raises(BudgetExceededError, match="Input token"):
        b.check_input(20)


def test_output_exceeded():
    b = TokenBudget(max_output_tokens=100)
    b.record_request(0, 95)
    with pytest.raises(BudgetExceededError, match="Output token"):
        b.check_output(10)


def test_request_limit():
    b = TokenBudget(max_api_requests=3)
    for _ in range(3):
        b.record_request(1, 1)
    with pytest.raises(BudgetExceededError, match="request limit"):
        b.check_requests()


def test_cost_limit():
    b = TokenBudget(max_cost_usd=0.01)
    b.record_request(0, 0, cost_usd=0.009)
    with pytest.raises(BudgetExceededError, match="Cost budget"):
        b.check_cost(estimated_cost=0.002)


def test_no_cost_limit():
    b = TokenBudget(max_cost_usd=None)
    b.check_cost(100.0)   # Should not raise.


def test_iteration_limit():
    b = TokenBudget(max_iterations=2)
    b.record_iteration()
    b.record_iteration()
    with pytest.raises(BudgetExceededError, match="iteration limit"):
        b.check_iterations()


def test_tool_call_limit():
    b = TokenBudget(max_tool_calls=3)
    for _ in range(3):
        b.record_tool_call()
    with pytest.raises(BudgetExceededError, match="Tool call limit"):
        b.check_tool_calls()


def test_check_all_passes():
    b = TokenBudget(
        max_input_tokens=1000, max_output_tokens=500,
        max_api_requests=100, max_iterations=50, max_tool_calls=100
    )
    b.check_all(est_input=100, est_output=50)   # Should not raise.


def test_summary_keys():
    b = TokenBudget()
    s = b.summary()
    assert "input_tokens" in s
    assert "output_tokens" in s
    assert "api_requests" in s
    assert "estimated_cost_usd" in s
