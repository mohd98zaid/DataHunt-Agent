from typing import Optional
from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator

class DeepResearchAgent(BaseAgent):
    """
    Autonomous Deep Technical Research & Synthesis Agent.
    Specialized in exploring technical documentation, analyzing framework architectures,
    and synthesizing authoritative Markdown dossiers with zero hallucinated records.
    """
    default_max_records: int = 20
    default_freshness_days: Optional[int] = 30
    default_output_format: str = "md"

    def __init__(self, orchestrator: Optional[ResearchOrchestrator] = None, model: Optional[str] = None):
        super().__init__(
            name="Deep Research AI Agent",
            mode="research",
            description="Autonomous technical intelligence agent for documentation exploration, architecture teardowns, and knowledge synthesis.",
            capabilities=[
                "Multi-query autonomous search across technical corpora & official documentation",
                "Architectural teardowns, LCEL workflows, and code pattern extraction",
                "Authoritative Markdown Executive Intelligence Dossiers",
                "Strict zero-hallucination citation anchors with primary source links"
            ],
            orchestrator=orchestrator,
            model=model
        )
