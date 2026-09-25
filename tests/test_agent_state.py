"""Tests for AgentState and DecisionEngine."""
import time
import pytest
from datahunt.agent.state import AgentState, AgentStatus, SearchIterationStats
from datahunt.agent.decision import AgentAction, DecisionEngine, MIN_YIELD_THRESHOLD


def make_state(**kwargs) -> AgentState:
    defaults = dict(request="test query", run_id="run_test", task_id="task_test", target_results=10, max_iterations=20, max_search_calls=30)
    defaults.update(kwargs)
    return AgentState(**defaults)


class TestAgentState:
    def test_initial_status(self):
        state = make_state()
        assert state.status == AgentStatus.INITIALIZING

    def test_add_observation(self):
        state = make_state()
        state.add_observation("found 5 jobs")
        assert "found 5 jobs" in state.observations

    def test_add_warning(self):
        state = make_state()
        state.add_warning("deadline near")
        assert "deadline near" in state.warnings

    def test_record_action(self):
        state = make_state()
        state.record_action("searched")
        assert "searched" in state.actions_taken

    def test_search_iteration_yield_rate(self):
        stats = SearchIterationStats(
            iteration=1, query="ai jobs uae", total_hits=20,
            new_candidates=10, verified_new=0, duplicates=5, failed_fetches=2
        )
        assert stats.yield_rate == 0.5

    def test_search_iteration_yield_zero_hits(self):
        stats = SearchIterationStats(
            iteration=1, query="q", total_hits=0,
            new_candidates=0, verified_new=0, duplicates=0, failed_fetches=0
        )
        assert stats.yield_rate == 0.0


class TestDecisionEngine:
    def setup_method(self):
        self.engine = DecisionEngine()

    def test_stop_on_deadline(self):
        state = make_state(deadline=time.time() - 1)  # already passed
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Deadline" in reason

    def test_stop_on_max_iterations(self):
        state = make_state(max_iterations=3)
        state.iteration = 3
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Max iterations" in reason

    def test_stop_when_target_reached(self):
        state = make_state(target_results=5)
        state.verified_records = [object()] * 5  # 5 dummy records
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Target results" in reason

    def test_search_when_plan_available(self):
        state = make_state()
        state.search_plan = [{"query": "genai jobs uae", "tier": 1, "purpose": "ats"}]
        state.search_plan_index = 0
        action, reason = self.engine.decide(state)
        assert action == AgentAction.SEARCH

    def test_fetch_when_candidates_pending(self):
        state = make_state()
        state.search_plan = []  # no more searches
        state.candidate_urls = [{"url": "https://example.com/job/1"}]
        action, reason = self.engine.decide(state)
        assert action == AgentAction.FETCH

    def test_stop_on_consecutive_low_yield(self):
        state = make_state()
        state.verified_records = [object()]  # has results
        state.consecutive_low_yield_iterations = 2
        action, reason = self.engine.decide(state)
        assert action == AgentAction.STOP
        assert "Diminishing returns" in reason

    def test_expand_search_on_low_yield_no_results(self):
        state = make_state()
        state.verified_records = []
        state.consecutive_low_yield_iterations = 2
        state.search_plan = [{"query": "q1"}, {"query": "q2"}]
        state.search_plan_index = 1  # one more query
        action, reason = self.engine.decide(state)
        assert action == AgentAction.EXPAND_SEARCH

    def test_record_low_yield_increments_counter(self):
        state = make_state()
        # Low yield: only 1 new out of 20 hits
        self.engine.record_search_iteration(state, "q", 20, 1)
        assert state.consecutive_low_yield_iterations == 1

    def test_record_good_yield_resets_counter(self):
        state = make_state()
        state.consecutive_low_yield_iterations = 2
        # Good yield: 10 new out of 20 hits
        self.engine.record_search_iteration(state, "q", 20, 10)
        assert state.consecutive_low_yield_iterations == 0

    def test_should_stop_returns_bool(self):
        state = make_state(deadline=time.time() - 1)
        stopped, reason = self.engine.should_stop(state)
        assert stopped is True
        assert isinstance(reason, str)
