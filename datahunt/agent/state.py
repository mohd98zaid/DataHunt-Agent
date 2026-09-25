from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum

class AgentStatus(str, Enum):
    INITIALIZING = "initializing"
    PLANNING = "planning"
    SEARCHING = "searching"
    FETCHING = "fetching"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    ANALYZING = "analyzing"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"

@dataclass
class SearchIterationStats:
    iteration: int
    query: str
    total_hits: int
    new_candidates: int  # not seen before
    verified_new: int = 0    # newly verified in this iteration
    qualified_new: int = 0   # newly qualified in this iteration
    relevant_new: int = 0    # newly relevant in this iteration
    duplicates: int = 0
    failed_fetches: int = 0

    @property
    def yield_rate(self) -> float:
        """Fraction of hits that became new candidates."""
        return self.new_candidates / max(self.total_hits, 1)

    @property
    def qualified_yield_rate(self) -> float:
        """Fraction of hits that became qualified jobs."""
        return self.qualified_new / max(self.total_hits, 1)

@dataclass
class AgentState:
    request: str
    run_id: str
    task_id: str
    
    objective: str = ""
    mode: str = "auto"  # jobs | research | market | auto

    # Canonical job request
    canonical_job_request: Optional[Any] = None
    
    # Explicit constraints from user (never modify these)
    explicit_location: Optional[str] = None
    locations: List[str] = field(default_factory=list)
    location_operator: str = "OR"
    explicit_experience_min: Optional[int] = None
    explicit_experience_max: Optional[int] = None
    explicit_skills: List[str] = field(default_factory=list)
    explicit_titles: List[str] = field(default_factory=list)
    remote_allowed: Optional[bool] = None
    
    # Expanded (recall-only, never hard requirements)
    expanded_titles: List[str] = field(default_factory=list)
    expanded_skills: List[str] = field(default_factory=list)
    inferred_skills: List[str] = field(default_factory=list)
    
    search_plan: List[Dict] = field(default_factory=list)  # [{query, tier, purpose}]
    search_plan_index: int = 0  # next query to execute
    
    candidate_urls: List[Dict] = field(default_factory=list)
    seen_urls: set = field(default_factory=set)
    seen_search_urls: set = field(default_factory=set)
    seen_canonical_urls: set = field(default_factory=set)
    seen_document_ids: set = field(default_factory=set)
    seen_application_urls: set = field(default_factory=set)
    seen_job_fingerprints: set = field(default_factory=set)

    fetched_docs: List[Any] = field(default_factory=list)
    raw_records: List[Any] = field(default_factory=list)
    verified_records: List[Any] = field(default_factory=list)
    qualified_records: List[Any] = field(default_factory=list)
    disqualified_records: List[Any] = field(default_factory=list)
    rejected_records: List[Any] = field(default_factory=list)
    
    observations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    search_iterations: List[SearchIterationStats] = field(default_factory=list)
    consecutive_low_yield_iterations: int = 0
    
    iteration: int = 0
    search_calls: int = 0
    fetch_calls: int = 0
    llm_calls: int = 0
    pages_failed: int = 0
    
    max_iterations: int = 10
    max_search_calls: int = 30
    max_fetch_calls: int = 120
    max_llm_calls: int = 50
    target_results: int = 20
    deadline: float = 0.0  # unix timestamp
    
    status: AgentStatus = AgentStatus.INITIALIZING
    completion_reason: Optional[str] = None
    actions_taken: List[str] = field(default_factory=list)

    def add_observation(self, obs: str):
        self.observations.append(obs)

    def add_warning(self, w: str):
        self.warnings.append(w)

    def record_action(self, action: str):
        self.actions_taken.append(action)
