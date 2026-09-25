from agent import (
    BaseAgent,
    ResearchOrchestrator,
    JobHuntAgent,
    DeepResearchAgent,
    MarketIntelAgent,
    get_agent,
    list_agents,
)

def test_agent_directory_exports_and_factory():
    agents = list_agents()
    assert len(agents) == 3
    modes = [a["mode"] for a in agents]
    assert "jobs" in modes
    assert "research" in modes
    assert "market" in modes

    job_agent = get_agent("jobs")
    assert isinstance(job_agent, JobHuntAgent)
    assert job_agent.mode == "jobs"

    research_agent = get_agent("research")
    assert isinstance(research_agent, DeepResearchAgent)
    assert research_agent.mode == "research"

    market_agent = get_agent("market")
    assert isinstance(market_agent, MarketIntelAgent)
    assert market_agent.mode == "market"

def test_backward_compatibility_imports():
    from datahunt.orchestrator import ResearchOrchestrator as OldOrchestrator
    from datahunt.agent import DeepResearchAgent as OldResearchAgent

    assert OldOrchestrator is ResearchOrchestrator
    assert OldResearchAgent is DeepResearchAgent
