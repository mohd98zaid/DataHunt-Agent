from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class ResearchIntent(str, Enum):
    JOB_SEARCH = "job_search"
    COMPANY_RESEARCH = "company_research"
    PEOPLE_RESEARCH = "people_research"
    CONTACT_RESEARCH = "contact_research"
    HOW_TO = "how_to"
    EXPLANATION = "explanation"
    FACTUAL_RESEARCH = "factual_research"
    COMPARISON = "comparison"
    LIST_RESEARCH = "list_research"
    CURRENT_STATUS = "current_status"
    DEEP_RESEARCH = "deep_research"
    MARKET_RESEARCH = "market_research"
    NEWS_RESEARCH = "news_research"
    UNKNOWN = "unknown"

class ResearchOutputType(str, Enum):
    ANSWER = "answer"
    JOB_RESULTS = "job_results"
    COMPARISON = "comparison"
    COMPANY_PROFILE = "company_profile"
    PEOPLE_RESULTS = "people_results"
    ENTITY_LIST = "entity_list"
    MARKET_INTEL = "market_intel"

class ResearchIntentSpec(BaseModel):
    raw_query: str
    intent: ResearchIntent
    confidence: float = 0.0
    topic: str = ""
    requires_web_search: bool = True
    requires_current_information: bool = False
    entities: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    explicit_constraints: List[str] = Field(default_factory=list)
    answer_style: str = "direct"  # direct | structured_list | comparison_table | dossier | profile
    requested_output: ResearchOutputType = ResearchOutputType.ANSWER
    reason: Optional[str] = None
