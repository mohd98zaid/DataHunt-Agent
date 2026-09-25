from abc import ABC
from typing import Any, Dict, List, Optional

class BaseAgent(ABC):
    """
    Abstract Base Class for specialized autonomous AI agents.
    """
    default_max_records: int = 50
    default_freshness_days: Optional[int] = 7
    default_output_format: str = "json"

    def __init__(
        self,
        name: str,
        mode: str,
        description: str,
        capabilities: Optional[List[str]] = None,
        orchestrator: Optional[Any] = None,
        model: Optional[str] = None
    ):
        self.name = name
        self.mode = mode
        self.description = description
        self.capabilities = capabilities or []
        self._orchestrator = orchestrator
        self._model = model

    @property
    def orchestrator(self):
        if self._orchestrator is None:
            from agent.orchestrator import ResearchOrchestrator
            self._orchestrator = ResearchOrchestrator(model=self._model)
        return self._orchestrator

    def run(
        self,
        query: str,
        max_records: Optional[int] = None,
        freshness_days: Optional[int] = None,
        output_format: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Execute autonomous mission directive using agent-specific defaults."""
        eff_max_records = max_records if max_records is not None else self.default_max_records
        eff_freshness = freshness_days if freshness_days is not None else self.default_freshness_days
        eff_format = output_format if output_format is not None else self.default_output_format

        task, run = self.orchestrator.create_task_and_run(
            request_text=query,
            max_records=eff_max_records,
            freshness_days=eff_freshness,
            output_format=eff_format,
            agent_mode=self.mode,
            **kwargs
        )
        return self.orchestrator.execute_run(run.id)

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "description": self.description,
            "capabilities": self.capabilities,
        }
