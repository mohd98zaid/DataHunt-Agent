"""
DataHunt Source Models — Source types, source registry definitions, and run health tracking.
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class JobSourceType(str, Enum):
    MAJOR_JOB_BOARD = "major_job_board"
    REGIONAL_JOB_BOARD = "regional_job_board"
    NICHE_JOB_BOARD = "niche_job_board"
    REMOTE_JOB_BOARD = "remote_job_board"
    AI_TECH_JOB_BOARD = "ai_tech_job_board"
    COMPANY_CAREER = "company_career"
    ATS = "ats"
    SEARCH_ENGINE_INDEX = "search_engine_index"
    RECRUITER_BOARD = "recruiter_board"
    COMMUNITY_JOB_BOARD = "community_job_board"


class SourceStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    NO_RESULTS = "no_results"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class JobSource(BaseModel):
    """Configuration and metadata for an individual job source."""
    id: str
    name: str
    source_type: JobSourceType
    domains: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)  # ISO codes or regional tags (e.g. "SA", "AE", "IN", "GLOBAL")
    capabilities: Dict[str, bool] = Field(
        default_factory=lambda: {
            "keyword": True,
            "location": True,
            "pagination": True,
            "freshness": True,
        }
    )
    method: str = "adapter"  # "adapter" | "search_index" | "direct_api"
    enabled: bool = True
    priority: int = 1        # 1 (highest) to 5 (lowest)


class SourceRunResult(BaseModel):
    """Execution telemetry and health record for a single source during a search run."""
    source_id: str
    source_name: str = ""
    status: SourceStatus = SourceStatus.SUCCESS
    queries_executed: int = 0
    raw_results: int = 0
    unique_results: int = 0
    qualified_results: int = 0
    errors: List[str] = Field(default_factory=list)
    latency: float = 0.0


class RawJob(BaseModel):
    """Raw extracted job posting before full normalization."""
    source_id: str
    source_name: str = ""
    source_job_id: Optional[str] = None
    title: str = ""
    company: str = ""
    location: Optional[str] = None
    url: str
    apply_url: Optional[str] = None
    description: Optional[str] = None
    posted_at: Optional[str] = None
    salary_raw: Optional[str] = None
    experience_raw: Optional[str] = None
    raw_data: Dict[str, Any] = Field(default_factory=dict)
