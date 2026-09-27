from typing import Optional
from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator

class MarketIntelAgent(BaseAgent):
    """
    Autonomous Multi-Source Market & Competitive Intelligence Agent.
    Specialized in equity research, stock candidate discovery, multi-source financial
    evidence analysis, technical setups, as well as competitive and SaaS intelligence.
    """
    default_max_records: int = 25
    default_freshness_days: Optional[int] = 14
    default_output_format: str = "json"

    def __init__(self, orchestrator: Optional[ResearchOrchestrator] = None, model: Optional[str] = None):
        super().__init__(
            name="Market Intel AI Agent",
            mode="market",
            description="Autonomous market intelligence engine analyzing equities, stock momentum, technical setups, fundamentals, and competitor landscapes.",
            capabilities=[
                "Multi-wave stock & equity discovery across indices, sectors, and momentum channels",
                "Deterministic technical indicator analysis (SMA, EMA, RSI, MACD, Volume ratio)",
                "Multi-source evidence verification, risk analysis, and counter-evidence audits",
                "Automated competitor discovery, SaaS pricing matrices, and positioning analysis",
                "Downloadable structured exports in JSON, CSV, or Excel"
            ],
            orchestrator=orchestrator,
            model=model
        )

