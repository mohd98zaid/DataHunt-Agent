from typing import Optional
from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator

class JobHuntAgent(BaseAgent):
    """
    Autonomous 0-Sec ATS Job Radar Agent.
    Specialized in harvesting career postings directly from ATS platforms
    (Greenhouse, Lever, Ashby, Workable) with sub-second freshness detection.
    """
    default_max_records: int = 50
    default_freshness_days: Optional[int] = 1
    default_output_format: str = "json"

    def __init__(self, orchestrator: Optional[ResearchOrchestrator] = None, model: Optional[str] = None):
        super().__init__(
            name="Job Hunt AI Agent",
            mode="jobs",
            description="Autonomous career harvester extracting verified job postings from ATS platforms with 0-sec freshness.",
            capabilities=[
                "Greenhouse, Lever, Ashby, and Workable ATS harvesting",
                "Sub-second timestamp parsing (0s ago, just now, today)",
                "Direct ATS apply link extraction",
                "Automated SQLite Job Application Tracker synchronization"
            ],
            orchestrator=orchestrator,
            model=model
        )
