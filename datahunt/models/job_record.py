"""
Canonical JobRecord schema for DataHunt Job Agent.
Fulfills Section 12 requirements: strongly typed job record for discovery,
verification, hard qualification, and ranking.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

from datahunt.models.evidence import Evidence


class JobRecord(BaseModel):
    """
    Dedicated canonical job record model.
    Used for final qualified job results with transparent scoring and evidence.
    """
    id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    title: str = ""
    company: str = ""
    location: str = ""

    url: str = ""
    canonical_url: str = ""

    source: str = ""
    source_type: str = "ats"  # ats | direct_company | job_board | regional

    description: Optional[str] = None
    employment_type: str = "full_time"
    remote_type: str = "onsite"  # remote | hybrid | onsite | unknown

    posted_at: Optional[str] = None
    discovered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    experience_min: Optional[int] = None
    experience_max: Optional[int] = None

    explicit_skills: List[str] = Field(default_factory=list)
    responsibilities: List[str] = Field(default_factory=list)

    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = "USD"

    verification_status: str = "verified"  # verified | unverified | rejected
    qualification_status: str = "QUALIFIED"  # QUALIFIED | DISQUALIFIED | NEEDS_REVIEW
    qualification_reasons: List[str] = Field(default_factory=list)

    match_score: float = 0.0  # 0 to 100
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)

    evidence: List[Evidence] = Field(default_factory=list)
    raw_fields: Dict[str, Any] = Field(default_factory=dict)
