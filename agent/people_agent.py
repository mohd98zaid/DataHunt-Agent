from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator


class PeopleScoutAgent(BaseAgent):
    """Discover verified recruiters, hiring managers, and engineering leaders."""

    default_max_records: int = 20
    default_freshness_days: int | None = 30
    default_output_format: str = "json"

    def __init__(self, orchestrator: ResearchOrchestrator | None = None, model: str | None = None):
        super().__init__(
            name="People Scout AI Agent",
            mode="people",
            description="Finds public, evidence-backed recruiter and hiring contacts without fabricating private contact data.",
            capabilities=[
                "Recruiter, talent acquisition, hiring manager, and engineering leader discovery",
                "Public professional profile verification",
                "Business contact-page and public email discovery",
                "Zero-fabrication contact handling",
            ],
            orchestrator=orchestrator,
            model=model,
        )
