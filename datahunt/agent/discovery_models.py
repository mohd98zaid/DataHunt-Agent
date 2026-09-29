from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class SearchTaskType(str, Enum):
    BOARD_SEARCH = "BOARD_SEARCH"
    REGIONAL_BOARD_SEARCH = "REGIONAL_BOARD_SEARCH"
    ATS_SEARCH = "ATS_SEARCH"
    COMPANY_SEARCH = "COMPANY_SEARCH"
    CAREER_PAGE_SEARCH = "CAREER_PAGE_SEARCH"
    GENERAL_SEARCH = "GENERAL_SEARCH"


class StopReason(str, Enum):
    TARGET_QUALIFIED_REACHED = "TARGET_QUALIFIED_REACHED"
    COVERAGE_COMPLETE_WITH_SATISFACTION = "COVERAGE_COMPLETE_WITH_SATISFACTION"
    DIMINISHING_RETURNS = "DIMINISHING_RETURNS"
    SOURCE_UNIVERSE_EXHAUSTED = "SOURCE_UNIVERSE_EXHAUSTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    NO_RESULTS_FOUND = "NO_RESULTS_FOUND"


class DiscoveredSourceType(str, Enum):
    MAJOR_BOARD = "MAJOR_BOARD"
    REGIONAL_BOARD = "REGIONAL_BOARD"
    NICHE_TECH_BOARD = "NICHE_TECH_BOARD"
    REMOTE_BOARD = "REMOTE_BOARD"
    ATS_PORTAL = "ATS_PORTAL"
    COMPANY_CAREER_PAGE = "COMPANY_CAREER_PAGE"
    COMMUNITY_FORUM = "COMMUNITY_FORUM"


@dataclass
class SearchTask:
    id: str
    task_type: SearchTaskType
    query: str
    source: str
    source_type: DiscoveredSourceType
    priority: int = 2  # 1 (highest) to 5 (lowest)
    depth: int = 0     # 0 = root, 1 = derived, etc.
    round: int = 1     # 1 to 6
    page: int = 1
    reason: str = ""
    company: Optional[str] = None
    ats_platform: Optional[str] = None
    location: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class CrawlTaskStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass
class CrawlTask:
    id: str
    source: str
    source_type: str  # "ats" | "regional_board" | "company_career_page" | "major_board"
    url: str
    company: Optional[str] = None
    ats_platform: Optional[str] = None
    location: Optional[str] = None
    query: Optional[str] = None
    priority: int = 2
    depth: int = 0
    page: int = 1
    status: CrawlTaskStatus = CrawlTaskStatus.PENDING
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryBudget:
    max_runtime_seconds: float = 120.0
    max_concurrent_tasks: int = 6
    max_source_discoveries: int = 50
    max_company_discoveries: int = 40
    max_ats_discoveries: int = 40
    max_fetches: int = 50
    max_search_requests: int = 60
    max_expansion_rounds: int = 6
    max_llm_calls: int = 25


@dataclass
class DiscoveryRoundTelemetry:
    run_id: str = ""
    round: int = 1
    task_type: str = ""
    source: str = ""
    query: str = ""
    depth: int = 0
    results_count: int = 0
    new_jobs_count: int = 0
    new_qualified_count: int = 0
    new_sources_count: int = 0
    duration_ms: float = 0.0
    stop_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SourceCoverageMatrix:
    """
    Tracks coverage across source categories (ATS, regional boards, major boards,
    niche tech, remote, company career pages) and user-requested geographic regions.
    """

    CATEGORIES = [
        "ats",
        "regional_board",
        "major_board",
        "niche_tech",
        "remote_board",
        "company_career_page",
    ]

    def __init__(self, target_regions: Optional[List[str]] = None):
        self.target_regions: List[str] = [r.strip().lower() for r in (target_regions or ["general"])]
        # Map: category -> Set of regions searched
        self.searched: Dict[str, Set[str]] = {cat: set() for cat in self.CATEGORIES}
        # Map: category -> count of hits found
        self.hits_by_category: Dict[str, int] = {cat: 0 for cat in self.CATEGORIES}

    def record_search(self, category: str, region: str = "general"):
        c = category.lower()
        if c not in self.searched:
            self.searched[c] = set()
        r = region.strip().lower() if region else "general"
        self.searched[c].add(r)

    def record_hit(self, category: str, region: str = "general"):
        c = category.lower()
        if c in self.hits_by_category:
            self.hits_by_category[c] += 1
        else:
            self.hits_by_category[c] = 1
        self.record_search(category, region)

    def is_covered(self, category: str, region: Optional[str] = None) -> bool:
        c = category.lower()
        if c not in self.searched or not self.searched[c]:
            return False
        if not region:
            return len(self.searched[c]) > 0
        r = region.strip().lower()
        return r in self.searched[c] or "general" in self.searched[c]

    def get_uncovered_categories(self, required_categories: Optional[List[str]] = None) -> List[str]:
        check_cats = required_categories or ["ats", "regional_board", "major_board"]
        return [c for c in check_cats if not self.is_covered(c)]

    def coverage_ratio(self) -> float:
        total = len(self.CATEGORIES)
        covered = sum(1 for c in self.CATEGORIES if self.is_covered(c))
        return round(covered / max(total, 1), 2)

    def is_balanced(self, min_categories: int = 3) -> bool:
        """Returns True if at least min_categories distinct source categories were searched."""
        covered = sum(1 for c in self.CATEGORIES if self.is_covered(c))
        return covered >= min_categories


@dataclass
class DiscoveryState:
    discovered_sources: Dict[str, DiscoveredSourceType] = field(default_factory=dict)
    searched_sources: Set[str] = field(default_factory=set)
    pending_sources: Set[str] = field(default_factory=set)
    failed_sources: Set[str] = field(default_factory=set)

    discovered_companies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    discovered_ats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    discovered_boards: Set[str] = field(default_factory=set)

    executed_queries: Set[str] = field(default_factory=set)
    task_queue: List[SearchTask] = field(default_factory=list)
    completed_tasks: List[SearchTask] = field(default_factory=list)

    pending_crawl_tasks: List[CrawlTask] = field(default_factory=list)
    active_crawl_tasks: List[CrawlTask] = field(default_factory=list)
    completed_crawl_tasks: List[CrawlTask] = field(default_factory=list)
    failed_crawl_tasks: List[CrawlTask] = field(default_factory=list)
    crawled_sources: Set[str] = field(default_factory=set)

    def enqueue_crawl_task(self, task: CrawlTask) -> bool:
        """Deduplicate crawl tasks by canonical source identity."""
        key = f"{task.source_type}:{task.source}:{task.company or ''}:{task.location or ''}:{task.page}".lower()
        if key in self.crawled_sources:
            return False
        for existing in self.pending_crawl_tasks:
            ex_key = f"{existing.source_type}:{existing.source}:{existing.company or ''}:{existing.location or ''}:{existing.page}".lower()
            if ex_key == key:
                return False
        self.pending_crawl_tasks.append(task)
        return True

    current_round: int = 1
    coverage_matrix: SourceCoverageMatrix = field(default_factory=SourceCoverageMatrix)
    round_yields: Dict[int, float] = field(default_factory=dict)
    round_novel_candidates: Dict[int, int] = field(default_factory=dict)
    telemetry_logs: List[DiscoveryRoundTelemetry] = field(default_factory=list)
    consecutive_low_yield_rounds: int = 0
    source_productivity: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def record_task_productivity(
        self,
        source: str,
        raw_hits: int = 0,
        candidate_jobs: int = 0,
        unique_jobs: int = 0,
        verified_jobs: int = 0,
        qualified_jobs: int = 0,
        duplicate_jobs: int = 0,
        rejected_jobs: int = 0,
        fetch_failures: int = 0,
        extraction_failures: int = 0,
        verification_failures: int = 0,
        hits: int = 0,
        valid_jobs: int = 0,
        failed: bool = False,
        queries_executed: int = 0,
    ):
        """
        Record search and qualification yield metrics for adaptive discovery.
        Tracks useful qualified jobs and penalizes duplicate-heavy and low-yield sources.
        """
        s = source.lower().strip()
        if s not in self.source_productivity:
            self.source_productivity[s] = {
                "queries_executed": 0,
                "raw_hits": 0,
                "hits": 0,
                "candidate_jobs": 0,
                "valid_jobs": 0,
                "unique_jobs": 0,
                "verified_jobs": 0,
                "qualified_jobs": 0,
                "duplicate_jobs": 0,
                "rejected_jobs": 0,
                "fetch_failures": 0,
                "extraction_failures": 0,
                "verification_failures": 0,
                "failures": 0,
                "qualified_yield": 0.0,
                "duplicate_rate": 0.0,
                "rejection_rate": 0.0,
                "productivity_score": 1.0,
            }
        data = self.source_productivity[s]
        data["queries_executed"] += queries_executed if queries_executed > 0 else (1 if (raw_hits or hits or failed) else 0)

        effective_raw_hits = raw_hits or hits
        effective_candidates = candidate_jobs or valid_jobs

        data["raw_hits"] += effective_raw_hits
        data["hits"] = data["raw_hits"]
        data["candidate_jobs"] += effective_candidates
        data["valid_jobs"] = data["candidate_jobs"]
        data["unique_jobs"] += unique_jobs
        data["verified_jobs"] += verified_jobs
        data["qualified_jobs"] += qualified_jobs
        data["duplicate_jobs"] += duplicate_jobs
        data["rejected_jobs"] += rejected_jobs
        data["fetch_failures"] += fetch_failures
        data["extraction_failures"] += extraction_failures
        data["verification_failures"] += verification_failures
        if failed:
            data["failures"] += 1

        total_unique = max(data["unique_jobs"], 1)
        qualified_yield = data["qualified_jobs"] / total_unique
        data["qualified_yield"] = round(qualified_yield, 3)

        total_discovered = max(data["unique_jobs"] + data["duplicate_jobs"], 1)
        duplicate_rate = data["duplicate_jobs"] / total_discovered
        data["duplicate_rate"] = round(duplicate_rate, 3)

        total_evaluated = max(data["verified_jobs"] + data["rejected_jobs"], 1)
        rejection_rate = data["rejected_jobs"] / total_evaluated
        data["rejection_rate"] = round(rejection_rate, 3)

        # Useful yield-driven productivity score (Section 5)
        if data["qualified_jobs"] > 0:
            base_score = 1.0 + (qualified_yield * 2.0)
        elif data["duplicate_jobs"] > 0 and data["unique_jobs"] == 0:
            base_score = 0.05
        elif data["rejected_jobs"] > 0 or data["duplicate_jobs"] > 0:
            base_score = 0.20
        else:
            base_score = 1.0

        dup_penalty = duplicate_rate * 0.4
        rej_penalty = rejection_rate * 0.3
        data["productivity_score"] = max(round(base_score - dup_penalty - rej_penalty, 3), 0.05)
