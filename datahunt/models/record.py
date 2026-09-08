from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"

class RetrievalStatus(str, Enum):
    CANDIDATE = "candidate"
    FETCHED = "fetched"
    CACHED = "cached"
    BLOCKED = "blocked"
    FAILED = "failed"
    TOO_LARGE = "too_large"

class SourceDocument(BaseModel):
    id: str = Field(default_factory=lambda: f"doc_{uuid.uuid4().hex[:12]}")
    run_id: str
    requested_url: str
    final_url: Optional[str] = None
    canonical_url: Optional[str] = None
    domain: str
    source_type: str = "web"
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    content_hash: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    title: Optional[str] = None
    extracted_text: Optional[str] = None
    text_truncated: int = 0
    policy_flags: List[str] = Field(default_factory=list)
    retrieval_status: RetrievalStatus = RetrievalStatus.FETCHED
    retrieved_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class RecordEvidence(BaseModel):
    id: str = Field(default_factory=lambda: f"evi_{uuid.uuid4().hex[:12]}")
    record_id: str
    source_document_id: str
    field_name: str
    evidence_text: Optional[str] = None
    locator: Any = Field(default_factory=dict)
    evidence_type: str = "page_text"
    supports_value: int = 1
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ExtractedRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:12]}")
    run_id: str
    source_document_id: Optional[str] = None
    record_type: str = "job_listing"
    identity_key: Optional[str] = None
    canonical_url: Optional[str] = None
    fields: Dict[str, Any] = Field(default_factory=dict)
    normalized_fields: Dict[str, Any] = Field(default_factory=dict)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    confidence: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)
    evidence: List[RecordEvidence] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ExportRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:12]}")
    run_id: str
    format: str
    file_name: str
    storage_key: str
    sha256: Optional[str] = None
    row_count: int = 0
    include_evidence: int = 1
    expires_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
