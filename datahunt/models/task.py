from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

class TaskStatus(str, Enum):
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"

class DateFilter(BaseModel):
    kind: str = "posted_at"
    after: Optional[str] = None
    before: Optional[str] = None

class Geography(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None

class SourcePolicy(BaseModel):
    allowed_domains: List[str] = Field(default_factory=list)
    blocked_domains: List[str] = Field(default_factory=list)

class ResearchSpec(BaseModel):
    status: str = "ready"  # ready | needs_clarification | refused
    topic: str
    geography: Geography = Field(default_factory=Geography)
    date_filter: DateFilter = Field(default_factory=DateFilter)
    requested_fields: List[str] = Field(
        default_factory=lambda: ["title", "company", "location", "posted_at", "application_url"]
    )
    max_records: int = 50
    freshness_days: Optional[int] = None
    agent_mode: str = "auto"
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    contact_policy: str = "business_public_only"
    quality_bar: str = "every required field needs evidence or null"
    assumptions: List[str] = Field(default_factory=list)
    clarifying_questions: List[str] = Field(default_factory=list)
    refusal_reason: Optional[str] = None

class ResearchTask(BaseModel):
    id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    request_text: str
    normalized_spec: ResearchSpec
    agent_mode: str = "auto"
    policy_version: str = "v1.0"
    prompt_version: str = "planner.v1"
    requested_output_format: str = "json"
    max_records: int = 50
    status: TaskStatus = TaskStatus.ACCEPTED
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
