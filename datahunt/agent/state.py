from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

class AgentStatus(str, Enum):
    INITIALIZING = "initializing"
    PLANNING = "planning"
    CRAWLING = "crawling"
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
    seen_source_job_ids: set = field(default_factory=set)
    seen_job_fingerprints: set = field(default_factory=set)
    source_run_results: Dict[str, Any] = field(default_factory=dict)

    fetched_docs: List[Any] = field(default_factory=list)
    extracted_doc_ids: Set[str] = field(default_factory=set)
    raw_records: List[Any] = field(default_factory=list)
    verified_records: List[Any] = field(default_factory=list)
    qualified_records: List[Any] = field(default_factory=list)
    disqualified_records: List[Any] = field(default_factory=list)
    rejected_records: List[Any] = field(default_factory=list)
    duplicate_records: List[Any] = field(default_factory=list)
    processed_record_ids: Set[str] = field(default_factory=set)
    raw_record_generation: int = 0
    last_verified_generation: int = -1
    rejection_reasons_tally: Dict[str, int] = field(default_factory=dict)
    source_distribution: Dict[str, int] = field(default_factory=dict)
    # Crawl telemetry (populated by _act_crawl_direct)
    crawl_sources_discovered: int = 0
    crawl_sources_crawled: int = 0
    crawl_sources_blocked: int = 0
    crawl_pages_fetched: int = 0
    crawl_job_links_discovered: int = 0
    crawl_done: bool = False  # True after first crawl pass completes
    
    observations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    search_iterations: List[SearchIterationStats] = field(default_factory=list)
    consecutive_low_yield_iterations: int = 0
    
    iteration: int = 0
    search_calls: int = 0
    fetch_calls: int = 0
    llm_calls: int = 0
    pages_failed: int = 0
    
    max_iterations: int = 200
    max_search_calls: int = 150
    max_fetch_calls: int = 300
    max_llm_calls: int = 100
    target_results: int = 50
    deadline: float = 0.0  # unix timestamp
    
    status: AgentStatus = AgentStatus.INITIALIZING
    completion_reason: Optional[str] = None
    actions_taken: List[str] = field(default_factory=list)
    discovery_state: Optional[Any] = None
    discovery_budget: Optional[Any] = None
    collection_mode: Optional[str] = None  # None / "thorough" / "fast"
    coverage_report: Dict[str, Any] = field(default_factory=dict)
    can_resume: bool = False

    def add_observation(self, obs: str):
        self.observations.append(obs)

    def add_warning(self, w: str):
        self.warnings.append(w)

    def record_action(self, action: str):
        self.actions_taken.append(action)

    def to_checkpoint_dict(self) -> Dict[str, Any]:
        """Serialize complete state including queues, cursors, and records for durable resumption."""
        rec_to_dict = lambda r: (r.model_dump() if hasattr(r, "model_dump") else (r.__dict__ if hasattr(r, "__dict__") else dict(r)))
        return {
            "request": self.request,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "mode": self.mode,
            "collection_mode": self.collection_mode,
            "canonical_job_request": (
                self.canonical_job_request.model_dump()
                if hasattr(self.canonical_job_request, "model_dump")
                else (self.canonical_job_request.__dict__ if hasattr(self.canonical_job_request, "__dict__") else None)
            ),
            "explicit_location": self.explicit_location,
            "locations": list(self.locations),
            "location_operator": self.location_operator,
            "explicit_experience_min": self.explicit_experience_min,
            "explicit_experience_max": self.explicit_experience_max,
            "explicit_skills": list(self.explicit_skills),
            "explicit_titles": list(self.explicit_titles),
            "remote_allowed": self.remote_allowed,
            "expanded_titles": list(self.expanded_titles),
            "expanded_skills": list(self.expanded_skills),
            "inferred_skills": list(self.inferred_skills),
            "search_plan": list(self.search_plan),
            "search_plan_index": self.search_plan_index,
            "candidate_urls": list(self.candidate_urls),
            "seen_urls": list(self.seen_urls),
            "seen_search_urls": list(self.seen_search_urls),
            "seen_canonical_urls": list(self.seen_canonical_urls),
            "seen_document_ids": list(self.seen_document_ids),
            "seen_application_urls": list(self.seen_application_urls),
            "seen_source_job_ids": list(self.seen_source_job_ids),
            "seen_job_fingerprints": list(self.seen_job_fingerprints),
            "extracted_doc_ids": list(self.extracted_doc_ids),
            "processed_record_ids": list(self.processed_record_ids),
            "raw_records": [rec_to_dict(r) for r in self.raw_records],
            "verified_records": [rec_to_dict(r) for r in self.verified_records],
            "qualified_records": [rec_to_dict(r) for r in self.qualified_records],
            "disqualified_records": [rec_to_dict(r) for r in self.disqualified_records],
            "rejected_records": [rec_to_dict(r) for r in self.rejected_records],
            "duplicate_records": [rec_to_dict(r) for r in self.duplicate_records],
            "raw_record_generation": self.raw_record_generation,
            "last_verified_generation": self.last_verified_generation,
            "rejection_reasons_tally": dict(self.rejection_reasons_tally),
            "source_distribution": dict(self.source_distribution),
            "crawl_sources_discovered": self.crawl_sources_discovered,
            "crawl_sources_crawled": self.crawl_sources_crawled,
            "crawl_sources_blocked": self.crawl_sources_blocked,
            "crawl_pages_fetched": self.crawl_pages_fetched,
            "crawl_job_links_discovered": self.crawl_job_links_discovered,
            "crawl_done": self.crawl_done,
            "observations": list(self.observations),
            "warnings": list(self.warnings),
            "iteration": self.iteration,
            "search_calls": self.search_calls,
            "fetch_calls": self.fetch_calls,
            "llm_calls": self.llm_calls,
            "pages_failed": self.pages_failed,
            "max_iterations": self.max_iterations,
            "max_search_calls": self.max_search_calls,
            "max_fetch_calls": self.max_fetch_calls,
            "max_llm_calls": self.max_llm_calls,
            "target_results": self.target_results,
            "deadline": self.deadline,
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "completion_reason": self.completion_reason,
            "actions_taken": list(self.actions_taken),
            "can_resume": self.can_resume,
            "coverage_report": dict(self.coverage_report),
            "discovery_state": self.discovery_state.to_dict() if hasattr(self.discovery_state, "to_dict") else None,
        }

    @classmethod
    def from_checkpoint_dict(cls, d: Dict[str, Any]) -> "AgentState":
        from datahunt.models import ExtractedRecord
        from datahunt.agent.discovery_models import DiscoveryState
        status_val = d.get("status", AgentStatus.INITIALIZING.value)
        try:
            status_enum = AgentStatus(status_val)
        except Exception:
            status_enum = AgentStatus.INITIALIZING

        state = cls(
            request=d["request"],
            run_id=d["run_id"],
            task_id=d["task_id"],
            objective=d.get("objective", ""),
            mode=d.get("mode", "jobs"),
            collection_mode=d.get("collection_mode"),
            explicit_location=d.get("explicit_location"),
            locations=list(d.get("locations", [])),
            location_operator=d.get("location_operator", "OR"),
            explicit_experience_min=d.get("explicit_experience_min"),
            explicit_experience_max=d.get("explicit_experience_max"),
            explicit_skills=list(d.get("explicit_skills", [])),
            explicit_titles=list(d.get("explicit_titles", [])),
            remote_allowed=d.get("remote_allowed"),
            expanded_titles=list(d.get("expanded_titles", [])),
            expanded_skills=list(d.get("expanded_skills", [])),
            inferred_skills=list(d.get("inferred_skills", [])),
            search_plan=list(d.get("search_plan", [])),
            search_plan_index=d.get("search_plan_index", 0),
            candidate_urls=list(d.get("candidate_urls", [])),
            seen_urls=set(d.get("seen_urls", [])),
            seen_search_urls=set(d.get("seen_search_urls", [])),
            seen_canonical_urls=set(d.get("seen_canonical_urls", [])),
            seen_document_ids=set(d.get("seen_document_ids", [])),
            seen_application_urls=set(d.get("seen_application_urls", [])),
            seen_source_job_ids=set(d.get("seen_source_job_ids", [])),
            seen_job_fingerprints=set(d.get("seen_job_fingerprints", [])),
            extracted_doc_ids=set(d.get("extracted_doc_ids", [])),
            processed_record_ids=set(d.get("processed_record_ids", [])),
            raw_records=[ExtractedRecord(**r) if isinstance(r, dict) else r for r in d.get("raw_records", [])],
            verified_records=[ExtractedRecord(**r) if isinstance(r, dict) else r for r in d.get("verified_records", [])],
            qualified_records=[ExtractedRecord(**r) if isinstance(r, dict) else r for r in d.get("qualified_records", [])],
            disqualified_records=[ExtractedRecord(**r) if isinstance(r, dict) else r for r in d.get("disqualified_records", [])],
            rejected_records=[ExtractedRecord(**r) if isinstance(r, dict) else r for r in d.get("rejected_records", [])],
            duplicate_records=[ExtractedRecord(**r) if isinstance(r, dict) else r for r in d.get("duplicate_records", [])],
            raw_record_generation=d.get("raw_record_generation", 0),
            last_verified_generation=d.get("last_verified_generation", -1),
            rejection_reasons_tally=dict(d.get("rejection_reasons_tally", {})),
            source_distribution=dict(d.get("source_distribution", {})),
            crawl_sources_discovered=d.get("crawl_sources_discovered", 0),
            crawl_sources_crawled=d.get("crawl_sources_crawled", 0),
            crawl_sources_blocked=d.get("crawl_sources_blocked", 0),
            crawl_pages_fetched=d.get("crawl_pages_fetched", 0),
            crawl_job_links_discovered=d.get("crawl_job_links_discovered", 0),
            crawl_done=d.get("crawl_done", False),
            observations=list(d.get("observations", [])),
            warnings=list(d.get("warnings", [])),
            iteration=d.get("iteration", 0),
            search_calls=d.get("search_calls", 0),
            fetch_calls=d.get("fetch_calls", 0),
            llm_calls=d.get("llm_calls", 0),
            pages_failed=d.get("pages_failed", 0),
            max_iterations=d.get("max_iterations", 200),
            max_search_calls=d.get("max_search_calls", 150),
            max_fetch_calls=d.get("max_fetch_calls", 300),
            max_llm_calls=d.get("max_llm_calls", 100),
            target_results=d.get("target_results", 50),
            deadline=d.get("deadline", 0.0),
            status=status_enum,
            completion_reason=d.get("completion_reason"),
            actions_taken=list(d.get("actions_taken", [])),
            can_resume=d.get("can_resume", False),
            coverage_report=dict(d.get("coverage_report", {})),
        )
        if d.get("canonical_job_request"):
            try:
                from datahunt.agents.query_understanding import JobSearchRequest
                state.canonical_job_request = JobSearchRequest(**d["canonical_job_request"])
            except Exception:
                pass
        if d.get("discovery_state"):
            state.discovery_state = DiscoveryState.from_dict(d["discovery_state"])
        return state


def get_unprocessed_record_ids(state: AgentState) -> Set[str]:
    """Return set of all record IDs that have reached any terminal processing state."""
    return (
        getattr(state, "processed_record_ids", set())
        | {r.id for r in getattr(state, "verified_records", []) if hasattr(r, "id")}
        | {r.id for r in getattr(state, "rejected_records", []) if hasattr(r, "id")}
        | {r.id for r in getattr(state, "qualified_records", []) if hasattr(r, "id")}
        | {r.id for r in getattr(state, "disqualified_records", []) if hasattr(r, "id")}
        | {r.id for r in getattr(state, "duplicate_records", []) if hasattr(r, "id")}
    )


def get_unprocessed_records(state: AgentState) -> List[Any]:
    """Return raw records that have not yet been verified or categorized into a terminal state."""
    processed_ids = get_unprocessed_record_ids(state)
    return [r for r in getattr(state, "raw_records", []) if getattr(r, "id", None) not in processed_ids]

