from typing import Optional
from agent.base import BaseAgent
from agent.orchestrator import ResearchOrchestrator

class DeepResearchAgent(BaseAgent):
    """
    Universal Research Agent — Autonomous Technical Research & Document Generation.
    Acts as an autonomous research analyst, subject-matter investigator, evidence evaluator,
    and professional technical writer. Produces comprehensive, accurate, well-structured,
    evidence-backed standalone documents with zero fabricated claims.
    """
    default_max_records: int = 20
    default_freshness_days: Optional[int] = 30
    default_output_format: str = "md"

    def __init__(self, orchestrator: Optional[ResearchOrchestrator] = None, model: Optional[str] = None):
        super().__init__(
            name="Universal Research AI Agent",
            mode="research",
            description="Autonomous research analyst and technical writer producing evidence-backed, standalone intelligence documents tailored to the topic domain.",
            capabilities=[
                "Systematic research planning across primary literature, RFCs, official docs, and technical specifications",
                "Critical evidence evaluation distinguishing established facts, vendor claims, benchmarks, and limitations",
                "Dynamic topic-specific document structure (Overview, Background, Methodology, System Investigation, Benchmarks, Trade-offs, and Conclusions)",
                "Publication-grade Markdown and Word document deliverables with traceable primary citation index",
                "Strict research integrity and transparent disclosure of data gaps and unknowns"
            ],
            orchestrator=orchestrator,
            model=model
        )
