from typing import Any, Dict, List, Optional, Protocol
from pydantic import BaseModel, Field

class ToolResult(BaseModel):
    success: bool
    data: Any = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class BaseTool(Protocol):
    name: str
    description: str

    def execute(self, *args, **kwargs) -> ToolResult:
        ...
