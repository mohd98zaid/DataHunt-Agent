from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

class RunStatus(str, Enum):
    PLANNED = "planned"
    SEARCHING = "searching"
    COLLECTING = "collecting"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    DEDUPLICATING = "deduplicating"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

class RunBudget(BaseModel):
    deadline_seconds: int = 300
    max_tool_steps: int = 30
    max_search_queries: int = 12
    max_pages: int = 40
    max_browser_pages: int = 8
    max_response_bytes: int = 2000000
    max_output_records: int = 250

class RunCounters(BaseModel):
    tool_steps: int = 0
    search_queries: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    pages_blocked: int = 0
    records_extracted: int = 0
    records_verified: int = 0
    records_review: int = 0
    records_rejected: int = 0
    records_duplicate: int = 0

class ResearchRun(BaseModel):
    id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    task_id: str
    status: RunStatus = RunStatus.PLANNED
    attempt: int = 1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    deadline_at: Optional[str] = None
    budget: RunBudget = Field(default_factory=RunBudget)
    counters: RunCounters = Field(default_factory=RunCounters)
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ToolEventStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"

class ToolEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    run_id: str
    step_number: int
    tool_name: str
    request_json: Dict[str, Any] = Field(default_factory=dict)
    response_summary_json: Dict[str, Any] = Field(default_factory=dict)
    status: ToolEventStatus = ToolEventStatus.STARTED
    error_code: Optional[str] = None
    duration_ms: Optional[int] = None
    request_hash: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
