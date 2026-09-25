import time
from enum import Enum
from typing import Optional, Tuple
from .state import AgentState, SearchIterationStats

class AgentAction(str, Enum):
    SEARCH = "SEARCH"
    FETCH = "FETCH"
    EXTRACT = "EXTRACT"
    VERIFY = "VERIFY"
    ANALYZE = "ANALYZE"
    EXPAND_SEARCH = "EXPAND_SEARCH"
    STOP = "STOP"

MIN_YIELD_THRESHOLD = 0.10  # less than 10% new candidates = diminishing returns
CONSECUTIVE_LOW_YIELD_LIMIT = 2  # stop after 2 consecutive low-yield iterations

class DecisionEngine:
    """
    Makes the next action decision based on explicit state.
    Application-level rules always override LLM suggestions.
    """

    def decide(self, state: AgentState) -> Tuple[AgentAction, str]:
        """
        Returns (action, reason).
        Order of precedence:
        1. Hard stop conditions (budget, deadline, target qualified results met)
        2. Diminishing returns detection
        3. Pending work (fetch, extract, verify/qualify)
        4. More searching needed
        """
        if state.deadline > 0 and time.time() >= state.deadline:
            return AgentAction.STOP, "Deadline reached"

        if state.iteration >= state.max_iterations:
            return AgentAction.STOP, f"Max iterations ({state.max_iterations}) reached"

        # In jobs mode, target refers to QUALIFIED results. In general mode, verified records.
        effective_count = len(state.qualified_records) if (state.mode in ("jobs", "job") and state.qualified_records) else len(state.verified_records)
        if effective_count >= state.target_results:
            return AgentAction.STOP, f"Target results reached ({effective_count}/{state.target_results})"

        if state.search_calls >= state.max_search_calls and not state.candidate_urls:
            return AgentAction.STOP, "Search budget exhausted with no candidates"

        if state.consecutive_low_yield_iterations >= CONSECUTIVE_LOW_YIELD_LIMIT:
            if effective_count > 0:
                return AgentAction.STOP, f"Diminishing returns: {CONSECUTIVE_LOW_YIELD_LIMIT} consecutive low-yield iterations"
            elif state.search_plan_index < len(state.search_plan):
                return AgentAction.EXPAND_SEARCH, "Low yield from current strategy, trying next tier"

        if state.candidate_urls:
            return AgentAction.FETCH, f"{len(state.candidate_urls)} candidates to fetch"

        if state.fetched_docs:
            unfetched = [d for d in state.fetched_docs if d not in [getattr(r, 'doc_id', None) for r in state.raw_records]]
            if state.raw_records:  # docs fetched, records ready for verification
                unverified = [r for r in state.raw_records if r not in state.verified_records and r not in state.rejected_records and r not in state.disqualified_records]
                if unverified:
                    return AgentAction.VERIFY, f"{len(unverified)} records need verification"

        if state.search_plan_index < len(state.search_plan) and state.search_calls < state.max_search_calls:
            return AgentAction.SEARCH, f"Searching tier {state.search_plan_index + 1}/{len(state.search_plan)}"

        if effective_count > 0:
            return AgentAction.STOP, f"No more search capacity, returning {effective_count} results found"

        return AgentAction.STOP, "No results found and no remaining search capacity"

    def should_stop(self, state: AgentState) -> Tuple[bool, str]:
        """Check all stop conditions explicitly."""
        action, reason = self.decide(state)
        return action == AgentAction.STOP, reason

    def record_search_iteration(
        self,
        state: AgentState,
        query: str,
        total_hits: int,
        new_candidates: int,
        verified_new: int = 0,
        qualified_new: int = 0,
        relevant_new: int = 0,
        duplicates: int = 0,
        failed: int = 0,
    ):
        """Record stats for a search iteration and update diminishing-returns counter."""
        stats = SearchIterationStats(
            iteration=state.iteration,
            query=query,
            total_hits=total_hits,
            new_candidates=new_candidates,
            verified_new=verified_new,
            qualified_new=qualified_new,
            relevant_new=relevant_new,
            duplicates=duplicates,
            failed_fetches=failed,
        )
        state.search_iterations.append(stats)

        # In job search, yield tracks whether new candidates or qualified candidates were obtained
        is_job = state.mode in ("jobs", "job")
        if is_job and (len(state.verified_records) > 0 or len(state.qualified_records) > 0):
            # If we already have some results, consecutive iterations with 0 new candidates count as low yield
            if new_candidates == 0 or stats.yield_rate < MIN_YIELD_THRESHOLD:
                state.consecutive_low_yield_iterations += 1
            else:
                state.consecutive_low_yield_iterations = 0
        else:
            if stats.yield_rate < MIN_YIELD_THRESHOLD and new_candidates < 3:
                state.consecutive_low_yield_iterations += 1
            else:
                state.consecutive_low_yield_iterations = 0
