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
        hits: int = 0,
        valid_jobs: int = 0,
        unique_jobs: int = 0,
        qualified_jobs: int = 0,
        duplicate_jobs: int = 0,
        rejected_jobs: int = 0,
        failed: bool = False,
    ):
        """Record search yield metrics for adaptive discovery (Sections 13 & 14)."""
        s = source.lower().strip()
        if s not in self.source_productivity:
            self.source_productivity[s] = {
                "queries_executed": 0,
                "hits": 0,
                "valid_jobs": 0,
                "unique_jobs": 0,
                "qualified_jobs": 0,
                "duplicate_jobs": 0,
                "rejected_jobs": 0,
                "failures": 0,
                "productivity_score": 1.0,
            }
        data = self.source_productivity[s]
        data["queries_executed"] += 1
        data["hits"] += hits
        data["valid_jobs"] += valid_jobs
        data["unique_jobs"] += unique_jobs
        data["qualified_jobs"] += qualified_jobs
        data["duplicate_jobs"] += duplicate_jobs
        data["rejected_jobs"] += rejected_jobs
        if failed:
            data["failures"] += 1

        dup_penalty = (data["duplicate_jobs"] / max(data["unique_jobs"] + data["duplicate_jobs"], 1)) * 0.5
        base_score = (data["qualified_jobs"] * 2.0 + data["unique_jobs"]) / max(data["hits"], 1)
        data["productivity_score"] = max(round(base_score - dup_penalty, 3), 0.05)
