import time
from enum import Enum
from typing import Optional, Tuple
from .state import AgentState, SearchIterationStats, get_unprocessed_records

class AgentAction(str, Enum):
    CRAWL_DIRECT = "CRAWL_DIRECT"
    SEARCH = "SEARCH"
    FETCH = "FETCH"
    EXTRACT = "EXTRACT"
    VERIFY = "VERIFY"
    ANALYZE = "ANALYZE"
    EXPAND_SEARCH = "EXPAND_SEARCH"
    STOP = "STOP"

MIN_YIELD_THRESHOLD = 0.10  # less than 10% new candidates = diminishing returns
CONSECUTIVE_LOW_YIELD_LIMIT = 5  # stop after 5 consecutive low-yield iterations (was 4)

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
        3. Pending work: Fetch candidate URLs -> Extract -> Verify/Qualify
        4. Discovery search queue / search plan
        5. Pending direct crawl tasks (ATS APIs, regional boards, career pages)
        """
        now = time.time()
        search_deadline = getattr(state, "effective_search_deadline", 0.0) or getattr(state, "search_deadline", 0.0) or getattr(state, "deadline", 0.0)
        hard_deadline = getattr(state, "effective_hard_deadline", 0.0) or getattr(state, "hard_deadline", 0.0) or (
            (search_deadline + getattr(state, "drain_budget_seconds", 600.0)) if search_deadline > 0 else 0.0
        )

        if hard_deadline > 0 and now >= hard_deadline:
            return AgentAction.STOP, "Hard Deadline reached"

        if state.iteration >= state.max_iterations:
            return AgentAction.STOP, f"Max iterations ({state.max_iterations}) reached"

        # In jobs mode, target refers to QUALIFIED results ONLY. In general mode, verified records.
        effective_count = len(state.qualified_records) if state.mode in ("jobs", "job") else len(state.verified_records)

        unverified_records = get_unprocessed_records(state)
        has_pending_verification = bool(
            unverified_records
            and state.raw_record_generation > getattr(state, "last_verified_generation", -1)
        )
        has_pending_candidates = bool(state.candidate_urls)

        # ── Search Cutoff / Drain Phase ─────────────────────────────
        # After search deadline (e.g. 30min), disallow ANY new searches or direct crawls,
        # but complete all existing searched items (fetch candidate URLs, extract, verify).
        is_search_expired = search_deadline > 0 and now >= search_deadline
        if is_search_expired:
            if has_pending_verification:
                return AgentAction.VERIFY, f"{len(unverified_records)} records need verification (completing existing candidates after search Deadline)"
            if has_pending_candidates:
                return AgentAction.FETCH, f"{len(state.candidate_urls)} candidates to fetch (completing existing candidates after search Deadline)"
            if effective_count > 0:
                return AgentAction.STOP, f"Search Deadline reached; all existing candidates processed, returning {effective_count} results found"
            return AgentAction.STOP, "Search Deadline reached; all existing candidates processed (no results qualified)"

        # If discovery engine is active, evaluate dynamic coverage and balanced universe stopping
        # When continuous_time_governed is enabled, searches run for the full time budget (until search_deadline).
        if state.discovery_state:
            from datahunt.agent.discovery_engine import DiscoveryEngine
            engine = DiscoveryEngine(state.discovery_budget)
            stop_flag, stop_reason, stop_msg = engine.should_stop(state.discovery_state, state)
            if stop_flag:
                return AgentAction.STOP, stop_msg
        else:
            is_fast = getattr(state, "collection_mode", None) != "thorough"
            has_pending = bool(
                (state.search_plan and state.search_plan_index < len(state.search_plan))
                or state.candidate_urls
            )
            if effective_count >= state.target_results:
                if is_fast or not has_pending:
                    return AgentAction.STOP, f"Target results reached ({effective_count}/{state.target_results})"

            if state.consecutive_low_yield_iterations >= CONSECUTIVE_LOW_YIELD_LIMIT:
                if effective_count > 0:
                    if is_fast or not has_pending:
                        return AgentAction.STOP, f"Diminishing returns: {CONSECUTIVE_LOW_YIELD_LIMIT} consecutive low-yield iterations, returning {effective_count} results found"
                elif state.search_plan and state.search_plan_index < len(state.search_plan):
                    return AgentAction.EXPAND_SEARCH, "Low yield from current strategy, trying next tier"

        # 1. Verify and deterministically qualify unverified raw records
        unverified_records = get_unprocessed_records(state)
        if (
            unverified_records
            and state.raw_record_generation > getattr(state, "last_verified_generation", -1)
        ):
            return AgentAction.VERIFY, f"{len(unverified_records)} records need verification"

        # 2. Fetch candidate URLs if available
        if state.candidate_urls:
            return AgentAction.FETCH, f"{len(state.candidate_urls)} candidates to fetch"

        # 3. If there are active search tasks in discovery queue or search plan, execute search.
        # Search is intentionally ahead of queued direct crawls: a blocked board must not
        # consume the run deadline while regional discovery queries are still available.
        if state.discovery_state and state.discovery_state.task_queue and state.search_calls < state.max_search_calls:
            count = len(state.discovery_state.task_queue)
            return AgentAction.SEARCH, f"Searching discovery queue ({count} tasks)"

        if state.search_plan and state.search_plan_index < len(state.search_plan) and state.search_calls < state.max_search_calls:
            return AgentAction.SEARCH, f"Searching tier {state.search_plan_index + 1}/{len(state.search_plan)}"

        # 4. Direct source crawl queue: ATS public APIs, regional boards, company career pages
        has_pending_crawls = bool(
            state.discovery_state
            and getattr(state.discovery_state, "pending_crawl_tasks", None)
        )
        if state.mode in ("jobs", "job") and has_pending_crawls:
            count = len(state.discovery_state.pending_crawl_tasks)
            return AgentAction.CRAWL_DIRECT, f"Direct source crawling ({count} tasks in queue)"

        # Standalone direct crawl only runs after the available search work is exhausted.
        if (
            state.mode in ("jobs", "job")
            and not getattr(state, "crawl_done", False)
            and not (state.discovery_state and state.discovery_state.task_queue)
            and not (state.search_plan and state.search_plan_index < len(state.search_plan))
        ):
            return AgentAction.CRAWL_DIRECT, "Direct multi-source board/ATS crawl (pre-search)"

        # 5. Advance discovery round if task queue is empty and budget permits
        max_rounds = getattr(state.discovery_budget, "max_expansion_rounds", 12) if state.discovery_budget else 12
        if (
            state.discovery_state is not None
            and not state.discovery_state.task_queue
            and state.discovery_state.current_round < max_rounds
            and state.search_calls < state.max_search_calls
        ):
            return AgentAction.SEARCH, f"Advancing discovery round (R{state.discovery_state.current_round + 1})"

        if state.search_calls >= state.max_search_calls and not state.candidate_urls and not has_pending_crawls:
            return AgentAction.STOP, "Search budget exhausted with no candidates"

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
            # BUT: also reset the counter if we found new qualified results — keeps search alive when fruitful
            if qualified_new > 0:
                state.consecutive_low_yield_iterations = 0
            elif new_candidates == 0 or stats.yield_rate < MIN_YIELD_THRESHOLD:
                state.consecutive_low_yield_iterations += 1
            else:
                state.consecutive_low_yield_iterations = 0
        else:
            if stats.yield_rate < MIN_YIELD_THRESHOLD and new_candidates < 3:
                state.consecutive_low_yield_iterations += 1
            else:
                state.consecutive_low_yield_iterations = 0
