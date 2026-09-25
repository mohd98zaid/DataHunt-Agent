from typing import Optional
from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator

class MarketIntelAgent(BaseAgent):
    """
    Autonomous Market & Competitive Intelligence Agent.
    Specialized in SaaS pricing models, feature comparison matrices,
    competitor teardowns, and market differentiation analysis.
    """
    default_max_records: int = 25
    default_freshness_days: Optional[int] = 14
    default_output_format: str = "json"

    def __init__(self, orchestrator: Optional[ResearchOrchestrator] = None, model: Optional[str] = None):
        super().__init__(
            name="Market Intel AI Agent",
            mode="market",
            description="Autonomous competitor landscape mapper analyzing SaaS pricing, feature matrices, and market positioning.",
            capabilities=[
                "Automated competitor discovery and feature matrix generation",
                "SaaS and API pricing tier comparisons (seat, usage, token)",
                "Market positioning, strengths, weaknesses & target audience analysis",
                "Downloadable structured exports in JSON, CSV, or Excel"
            ],
            orchestrator=orchestrator,
            model=model
        )
