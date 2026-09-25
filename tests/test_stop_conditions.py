"""Tests for all agent stop conditions."""
import time
import pytest
from datahunt.agent.state import AgentState
from datahunt.agent.decision import AgentAction, DecisionEngine


def make_state(**kwargs) -> AgentState:
    defaults = dict(request="test", run_id="r1", task_id="t1", target_results=10, max_iterations=20, max_search_calls=30, max_fetch_calls=100)
    defaults.update(kwargs)
    return AgentState(**defaults)


class TestStopConditions:
    def setup_method(self):
        self.engine = DecisionEngine()

    def test_deadline_stop(self):
        state = make_state(deadline=time.time() - 10)
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Deadline" in reason

    def test_max_iterations_stop(self):
        state = make_state(max_iterations=5)
        state.iteration = 5
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP

    def test_target_results_satisfied_stop(self):
        state = make_state(target_results=3)
        state.verified_records = [object(), object(), object()]
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Target results" in reason

    def test_search_budget_exhausted_with_no_candidates_stop(self):
        state = make_state(max_search_calls=5)
        state.search_calls = 5
        state.candidate_urls = []
        state.search_plan = []
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP

    def test_diminishing_returns_with_results_stop(self):
        state = make_state()
        state.verified_records = [object()]
        state.consecutive_low_yield_iterations = 2
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Diminishing returns" in reason

    def test_no_capacity_with_no_results_stop(self):
        state = make_state()
        state.search_plan = []  # no plan
        state.candidate_urls = []
        state.fetched_docs = []
        state.raw_records = []
        state.verified_records = []
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP

    def test_no_capacity_with_results_stop(self):
        state = make_state()
        state.search_plan = []
        state.candidate_urls = []
        state.verified_records = [object(), object()]
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP

    def test_should_stop_is_consistent_with_decide(self):
        state = make_state(deadline=time.time() - 1)
        action, _ = self.engine.decide(state)
        stopped, _ = self.engine.should_stop(state)
        assert (action == AgentAction.STOP) == stopped
