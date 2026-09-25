import pytest
from unittest.mock import MagicMock

from datahunt.agent.discovery_models import (
    SearchTaskType,
    StopReason,
    DiscoveredSourceType,
    SearchTask,
    DiscoveryBudget,
    DiscoveryRoundTelemetry,
    SourceCoverageMatrix,
    DiscoveryState,
)
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agent.state import AgentState
from datahunt.agent.decision import AgentAction, DecisionEngine
from datahunt.tools.search import (
    SearchHit,
    MockSearchProvider,
    SearchTool,
    SearchProviderRegistry,
)


def test_coverage_matrix():
    matrix = SourceCoverageMatrix(target_regions=["Saudi Arabia", "UAE"])
    assert not matrix.is_covered("ats")
    assert not matrix.is_balanced(min_categories=3)
    assert matrix.coverage_ratio() == 0.0

    # Record search on ATS in Saudi Arabia
    matrix.record_search("ats", "Saudi Arabia")
    assert matrix.is_covered("ats")
    assert matrix.is_covered("ats", "Saudi Arabia")
    assert not matrix.is_covered("ats", "UAE")

    # Record hit on Regional Board in UAE
    matrix.record_hit("regional_board", "UAE")
    assert matrix.is_covered("regional_board")
    assert matrix.is_covered("regional_board", "UAE")

    # Record hit on Company Career Page
    matrix.record_hit("company_career_page", "general")
    assert matrix.is_covered("company_career_page")

    # Balanced with 3 categories covered
    assert matrix.is_balanced(min_categories=3)
    assert matrix.coverage_ratio() > 0.4
    uncovered = matrix.get_uncovered_categories(["ats", "regional_board", "major_board"])
    assert "major_board" in uncovered
    assert "ats" not in uncovered


def test_dynamic_ats_discovery():
    engine = DiscoveryEngine()
    state = AgentState(
        request="Find GenAI Engineer jobs in Saudi or UAE",
        run_id="run_ats_test",
        task_id="task_ats_test",
        mode="jobs",
        explicit_titles=["GenAI Engineer"],
        locations=["Saudi Arabia", "UAE"],
    )
    discovery_state = DiscoveryState(
        coverage_matrix=SourceCoverageMatrix(target_regions=["Saudi Arabia", "UAE"])
    )

    parent_task = SearchTask(
        id="t0",
        task_type=SearchTaskType.GENERAL_SEARCH,
        query="GenAI Engineer jobs UAE",
        source="duckduckgo",
        source_type=DiscoveredSourceType.MAJOR_BOARD,
        depth=0,
        round=1,
    )

    # Search hit pointing directly to an Ashby job board
    ashby_hit = {
        "url": "https://jobs.ashbyhq.com/noor-ai/8394-genai-engineer-dubai",
        "title": "GenAI Engineer - Dubai at Noor AI",
        "snippet": "Noor AI is looking for a Generative AI Engineer to join our Dubai office.",
        "source_domain": "jobs.ashbyhq.com",
    }

    new_tasks = engine.process_search_hits([ashby_hit], parent_task, state, discovery_state)

    # Verify company and ATS registered
    assert "noor-ai" in discovery_state.discovered_ats
    assert discovery_state.discovered_ats["noor-ai"]["platform"] == "ashby"
    assert "noor-ai" in discovery_state.discovered_companies

    # Verify dynamic ATS task created
    ats_tasks = [t for t in new_tasks if t.task_type == SearchTaskType.ATS_SEARCH]
    assert len(ats_tasks) >= 1
    assert "jobs.ashbyhq.com/noor-ai" in ats_tasks[0].query
    assert ats_tasks[0].priority == 1
    assert ats_tasks[0].depth == 1
    assert ats_tasks[0].round == 2


def test_dynamic_company_discovery_from_text():
    engine = DiscoveryEngine()
    state = AgentState(
        request="Find GenAI Engineer jobs in UAE",
        run_id="run_comp_test",
        task_id="task_comp_test",
        mode="jobs",
        explicit_titles=["GenAI Engineer"],
        locations=["UAE"],
    )
    discovery_state = DiscoveryState()
    parent_task = SearchTask(
        id="t0",
        task_type=SearchTaskType.BOARD_SEARCH,
        query="GenAI Engineer jobs UAE",
        source="bayt.com",
        source_type=DiscoveredSourceType.REGIONAL_BOARD,
        depth=0,
        round=1,
    )

    hit = {
        "url": "https://bayt.com/job/12345/senior-genai-engineer",
        "title": "Senior GenAI Engineer at Careem Technologies",
        "snippet": "Careem Technologies is looking for a Senior GenAI Engineer in Dubai.",
        "source_domain": "bayt.com",
    }

    new_tasks = engine.process_search_hits([hit], parent_task, state, discovery_state)

    # Verify company detected from 'at Careem Technologies'
    assert any("careem" in k for k in discovery_state.discovered_companies.keys())
    comp_tasks = [t for t in new_tasks if t.task_type == SearchTaskType.COMPANY_SEARCH]
    assert len(comp_tasks) >= 1
    assert "careem" in comp_tasks[0].query.lower()
    assert comp_tasks[0].depth == 1


def test_dynamic_job_board_discovery():
    engine = DiscoveryEngine()
    state = AgentState(
        request="Find GenAI Engineer jobs",
        run_id="run_board_test",
        task_id="task_board_test",
        mode="jobs",
        explicit_titles=["GenAI Engineer"],
    )
    discovery_state = DiscoveryState()
    parent_task = SearchTask(
        id="t0",
        task_type=SearchTaskType.GENERAL_SEARCH,
        query="GenAI Engineer jobs",
        source="general",
        source_type=DiscoveredSourceType.MAJOR_BOARD,
        depth=0,
        round=1,
    )

    board_hit = {
        "url": "https://gulftechjobs.com/careers/ai-researcher-101",
        "title": "AI Researcher - Dubai",
        "snippet": "Apply online through gulftechjobs careers portal.",
        "source_domain": "gulftechjobs.com",
    }

    new_tasks = engine.process_search_hits([board_hit], parent_task, state, discovery_state)
    assert "gulftechjobs.com" in discovery_state.discovered_boards
    board_tasks = [t for t in new_tasks if t.task_type == SearchTaskType.BOARD_SEARCH]
    assert len(board_tasks) >= 1
    assert "site:gulftechjobs.com" in board_tasks[0].query


def test_discovery_rounds_progression():
    engine = DiscoveryEngine()
    state = AgentState(
        request="Find GenAI Engineer jobs in Saudi Arabia and UAE",
        run_id="run_rounds_test",
        task_id="task_rounds_test",
        mode="jobs",
        explicit_titles=["GenAI Engineer"],
        locations=["Saudi Arabia", "UAE"],
    )
    discovery_state = DiscoveryState()
    discovery_state.completed_tasks.append(
        SearchTask(
            id="r1_t1",
            task_type=SearchTaskType.ATS_SEARCH,
            query="site:boards.greenhouse.io GenAI",
            source="boards.greenhouse.io",
            source_type=DiscoveredSourceType.ATS_PORTAL,
            priority=1,
            round=1,
        )
    )

    # Round 2: Source expansion & pagination
    discovery_state.current_round = 2
    r2_tasks = engine.generate_next_round_tasks(discovery_state, None, state)
    assert any(t.page == 2 for t in r2_tasks)

    # Round 3: Company & ATS deep dive
    discovery_state.current_round = 3
    discovery_state.discovered_ats["falcon-frontier"] = {
        "domain": "boards.greenhouse.io",
        "company": "falcon-frontier",
    }
    r3_tasks = engine.generate_next_round_tasks(discovery_state, None, state)
    assert any("falcon-frontier" in t.query for t in r3_tasks)

    # Round 4: Geographic deep dive
    discovery_state.current_round = 4
    r4_tasks = engine.generate_next_round_tasks(discovery_state, None, state)
    assert any("Riyadh" in t.query or "Dubai" in t.query for t in r4_tasks)

    # Round 5: Title & Skill synonyms
    discovery_state.current_round = 5
    r5_tasks = engine.generate_next_round_tasks(discovery_state, None, state)
    assert any("LLM Engineer" in t.query or "AI Engineer" in t.query for t in r5_tasks)


def test_search_provider_registry_and_pagination():
    registry = SearchProviderRegistry()
    assert "duckduckgo" in registry.list_providers()
    assert "mock" in registry.list_providers()
    assert "hybrid" in registry.list_providers()

    default_prov = registry.get_default()
    assert default_prov is not None

    # Test pagination in MockSearchProvider
    mock_prov = MockSearchProvider()
    p1_hits = mock_prov.search("AI Engineer jobs", limit=2, page=1)
    p2_hits = mock_prov.search("AI Engineer jobs", limit=2, page=2)

    assert len(p1_hits) > 0
    assert len(p2_hits) > 0
    # Page 1 and Page 2 should return distinct URLs
    p1_urls = {h.url for h in p1_hits}
    p2_urls = {h.url for h in p2_hits}
    assert p1_urls != p2_urls


def test_decision_engine_with_discovery_state():
    decision_engine = DecisionEngine()
    budget = DiscoveryBudget(max_search_requests=10, max_expansion_rounds=4)

    state = AgentState(
        request="Find GenAI Engineer jobs in UAE",
        run_id="run_dec_test",
        task_id="task_dec_test",
        mode="jobs",
        target_results=5,
        max_search_calls=10,
        discovery_budget=budget,
    )
    matrix = SourceCoverageMatrix(target_regions=["UAE"])
    state.discovery_state = DiscoveryState(coverage_matrix=matrix)

    # Simulate 5 qualified records but only 1 source category covered (not balanced)
    state.qualified_records = [MagicMock() for _ in range(5)]
    matrix.record_hit("major_board", "UAE")
    state.discovery_state.task_queue.append(
        SearchTask(
            id="q1",
            task_type=SearchTaskType.ATS_SEARCH,
            query="site:jobs.ashbyhq.com GenAI",
            source="jobs.ashbyhq.com",
            source_type=DiscoveredSourceType.ATS_PORTAL,
            round=2,
        )
    )

    # When coverage is not balanced and tasks remain, decide does NOT prematurely stop!
    action, reason = decision_engine.decide(state)
    assert action == AgentAction.SEARCH

    # Now balance the coverage matrix with ATS and regional boards
    matrix.record_hit("ats", "UAE")
    matrix.record_hit("regional_board", "UAE")

    # Now that coverage is balanced, should_stop evaluates True
    action, reason = decision_engine.decide(state)
    assert action == AgentAction.STOP
    assert "Target results" in reason
