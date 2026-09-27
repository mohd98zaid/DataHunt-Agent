"""
Shared Intelligence Domain Models.

Provides unified, type-safe data structures for the shared intelligence layer:
Source Intelligence, Entity Resolution, Fact Extraction, Evidence, Freshness,
Contradiction Detection, Coverage, Risk, Company, People, Contact, News,
Opportunity Matching, and Monitoring.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class SourceCategory(str, Enum):
    OFFICIAL = "OFFICIAL"
    PRIMARY = "PRIMARY"
    REGULATORY = "REGULATORY"
    EXCHANGE = "EXCHANGE"
    COMPANY = "COMPANY"
    FINANCIAL_DATA = "FINANCIAL_DATA"
    NEWS = "NEWS"
    JOB_BOARD = "JOB_BOARD"
    ATS = "ATS"
    PROFESSIONAL_NETWORK = "PROFESSIONAL_NETWORK"
    RESEARCH = "RESEARCH"
    AGGREGATOR = "AGGREGATOR"
    UNKNOWN = "UNKNOWN"


class SourcePlan(BaseModel):
    id: str = Field(default_factory=lambda: f"srcplan_{uuid.uuid4().hex[:10]}")
    domain: str
    primary_sources: List[str] = Field(default_factory=list)
    secondary_sources: List[str] = Field(default_factory=list)
    discovery_sources: List[str] = Field(default_factory=list)
    source_types: List[SourceCategory] = Field(default_factory=list)
    search_queries: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EntityType(str, Enum):
    COMPANY = "COMPANY"
    PERSON = "PERSON"
    STOCK = "STOCK"
    JOB = "JOB"
    ORGANIZATION = "ORGANIZATION"
    PRODUCT = "PRODUCT"
    EVENT = "EVENT"
    SOURCE = "SOURCE"
    UNKNOWN = "UNKNOWN"


class EntityIdentity(BaseModel):
    entity_id: str = Field(default_factory=lambda: f"ent_{uuid.uuid4().hex[:10]}")
    entity_type: EntityType
    canonical_name: str
    aliases: List[str] = Field(default_factory=list)
    identifiers: Dict[str, str] = Field(default_factory=dict)
    official_domain: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Fact(BaseModel):
    fact_id: str = Field(default_factory=lambda: f"fact_{uuid.uuid4().hex[:10]}")
    entity_id: str
    field: str
    value: Any
    unit: Optional[str] = None
    source: str = ""
    source_url: Optional[str] = None
    published_at: Optional[str] = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: float = 1.0
    raw_text: Optional[str] = None


class EvidenceRelationship(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    MENTIONS = "MENTIONS"
    DERIVED_FROM = "DERIVED_FROM"


class EvidenceConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SharedEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"evi_{uuid.uuid4().hex[:10]}")
    claim: str
    entity_id: str
    source_url: str
    source_type: str = "web"
    relationship: EvidenceRelationship = EvidenceRelationship.SUPPORTS
    published_at: Optional[str] = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    supporting_text: str = ""
    confidence: EvidenceConfidence = EvidenceConfidence.HIGH


class FreshnessRating(str, Enum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class FreshnessAssessment(BaseModel):
    assessment_id: str = Field(default_factory=lambda: f"fresh_{uuid.uuid4().hex[:10]}")
    item_id: str
    field: str
    domain: str
    published_at: Optional[str] = None
    age_seconds: Optional[float] = None
    rating: FreshnessRating = FreshnessRating.UNKNOWN
    reason: str = ""
    assessed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContradictionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class ContradictionItem(BaseModel):
    conflict_id: str = Field(default_factory=lambda: f"conf_{uuid.uuid4().hex[:10]}")
    entity_id: str
    claim: str
    source_a: str
    source_b: str
    timestamp_a: Optional[str] = None
    timestamp_b: Optional[str] = None
    value_a: Any = None
    value_b: Any = None
    unit: Optional[str] = None
    possible_reason: str = ""
    resolution_status: ContradictionStatus = ContradictionStatus.UNRESOLVED
    resolved_value: Optional[Any] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CoverageGap(BaseModel):
    entity_id: str
    entity_name: str
    missing_category: str
    description: str
    suggested_queries: List[str] = Field(default_factory=list)


class SharedCoverageState(BaseModel):
    task_id: str
    domain: str
    candidates_discovered: int = 0
    candidates_validated: int = 0
    technical_data_count: int = 0
    fundamental_data_count: int = 0
    news_count: int = 0
    counter_evidence_count: int = 0
    fresh_evidence_count: int = 0
    missing_gaps: List[CoverageGap] = Field(default_factory=list)
    is_sufficient: bool = False
    completion_ratio: float = 0.0
    measured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RiskSeverity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskStatus(str, Enum):
    KNOWN_RISK = "KNOWN_RISK"
    POSSIBLE_RISK = "POSSIBLE_RISK"
    UNKNOWN = "UNKNOWN"


class RiskItem(BaseModel):
    risk_id: str = Field(default_factory=lambda: f"risk_{uuid.uuid4().hex[:10]}")
    entity_id: str
    risk_type: str
    severity: RiskSeverity = RiskSeverity.MEDIUM
    status: RiskStatus = RiskStatus.POSSIBLE_RISK
    description: str
    evidence_urls: List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PersonRole(str, Enum):
    RECRUITER = "RECRUITER"
    TALENT_ACQUISITION = "TALENT_ACQUISITION"
    HIRING_MANAGER = "HIRING_MANAGER"
    ENGINEERING_LEADER = "ENGINEERING_LEADER"
    AI_LEADER = "AI_LEADER"
    EXECUTIVE = "EXECUTIVE"
    EMPLOYEE = "EMPLOYEE"
    RESEARCHER = "RESEARCHER"
    UNKNOWN = "UNKNOWN"


class PersonProfile(BaseModel):
    person_id: str = Field(default_factory=lambda: f"per_{uuid.uuid4().hex[:10]}")
    name: str
    company: str
    role: PersonRole = PersonRole.UNKNOWN
    role_title: str = ""
    relevance: str = ""
    source: str = ""
    profile_url: str = ""
    evidence: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContactType(str, Enum):
    EMAIL = "EMAIL"
    LINKEDIN = "LINKEDIN"
    COMPANY_PAGE = "COMPANY_PAGE"
    PHONE = "PHONE"
    DIRECTORY = "DIRECTORY"
    OTHER = "OTHER"


class ContactInfo(BaseModel):
    contact_id: str = Field(default_factory=lambda: f"cnt_{uuid.uuid4().hex[:10]}")
    entity_id: str
    contact_type: ContactType
    value: str
    source: str
    verified: bool = False
    confidence: float = 1.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CompanyIntelligenceProfile(BaseModel):
    company_id: str = Field(default_factory=lambda: f"cmp_{uuid.uuid4().hex[:10]}")
    canonical_name: str
    official_domain: Optional[str] = None
    industry: str = "Unknown"
    business_model: str = "Unknown"
    locations: List[str] = Field(default_factory=list)
    leadership: List[str] = Field(default_factory=list)
    products: List[str] = Field(default_factory=list)
    financial_information: Dict[str, Any] = Field(default_factory=dict)
    recent_news: List[str] = Field(default_factory=list)
    hiring_activity: str = "Unknown"
    competitors: List[str] = Field(default_factory=list)
    technology: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    is_available: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NewsEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:10]}")
    title: str
    summary: str
    entities: List[str] = Field(default_factory=list)
    date: Optional[str] = None
    sources: List[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    impact: str = "low"
    freshness: FreshnessRating = FreshnessRating.FRESH
    confidence: float = 1.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OpportunityMatch(BaseModel):
    match_id: str = Field(default_factory=lambda: f"match_{uuid.uuid4().hex[:10]}")
    candidate_id: str
    match_score: float = 0.0
    is_qualified: bool = True
    matching_factors: List[str] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
    explanation: str = ""
    calculated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MonitoringChangeType(str, Enum):
    NEW = "NEW"
    UPDATED = "UPDATED"
    REMOVED = "REMOVED"
    PRICE_CHANGED = "PRICE_CHANGED"
    NEWS_EVENT = "NEWS_EVENT"
    STATUS_CHANGED = "STATUS_CHANGED"


class MonitoringEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"mon_{uuid.uuid4().hex[:10]}")
    target_id: str
    change_type: MonitoringChangeType
    previous_state: Any = None
    current_state: Any = None
    details: str
    detected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
