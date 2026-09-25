from typing import List, Optional
from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator
from agent.job_agent import JobHuntAgent
from agent.research_agent import DeepResearchAgent
from agent.market_agent import MarketIntelAgent

def get_agent(mode: str = "research", model: Optional[str] = None) -> BaseAgent:
    """Factory helper to obtain an initialized specialized autonomous agent."""
    orch = ResearchOrchestrator(model=model)
    m = (mode or "research").lower()
    if m == "jobs":
        return JobHuntAgent(orchestrator=orch, model=model)
    elif m == "market":
        return MarketIntelAgent(orchestrator=orch, model=model)
    else:
        return DeepResearchAgent(orchestrator=orch, model=model)

def list_agents():
    """List available autonomous agent specifications."""
    return [
        JobHuntAgent().get_info(),
        DeepResearchAgent().get_info(),
        MarketIntelAgent().get_info(),
    ]

__all__ = [
    "BaseAgent",
    "ResearchOrchestrator",
    "JobHuntAgent",
    "DeepResearchAgent",
    "MarketIntelAgent",
    "get_agent",
    "list_agents",
]
