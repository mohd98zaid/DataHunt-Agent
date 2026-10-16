"""
Scale and Interruption Test Suite.
Validates realistic multi-source scale and interruption resilience:
- > 25 sources (30 mock sources)
- > 50 pages per source (55 pages per source)
- > 1,000 jobs per source (1,100 jobs per source)
- > 20,000 total jobs across sources (26,400 raw jobs)
- Duplicate postings across multiple sources
- Realistic mix of successful, blocked (403/429), and transient-failure sources
- Demonstrates thorough mode completion with full budget
- Demonstrates preservation of unfinished work with PARTIAL status on restricted budget
- Demonstrates seamless resumption without re-requesting completed pages or dropping jobs.
"""
from datetime import datetime, timezone
import uuid
import pytest
from unittest.mock import MagicMock, patch

from datahunt.db import run_migrations, CheckpointRepository, get_connection
from datahunt.agent.state import AgentState
from datahunt.agent.discovery_models import CrawlTask, CrawlTaskStatus, DiscoveryState
from datahunt.sources.crawlers import (
    MultiSourceCrawler,
    CrawlBudget,
    CrawlResult,
    CrawledJob,
    _DomainRateLimiter,
    crawl_lever_company,
)


@pytest.fixture(autouse=True)
def setup_db():
    run_migrations()


def create_test_run(run_id: str, task_id: str):
    conn = get_connection()
    now_str = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO research_tasks 
            (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at)
            VALUES (?, ?, '{"topic": "Scale Search"}', '1.0', '1.0', ?, ?);
            """,
            (task_id, "Scale Search", now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO research_runs
            (id, task_id, status, budget_json, created_at, updated_at)
            VALUES (?, ?, 'planned', '{}', ?, ?);
            """,
            (run_id, task_id, now_str, now_str)
        )


def generate_mock_page_data(company_idx: int, page_num: int, jobs_per_page: int = 20):
    """Generate 20 deterministic jobs per page. Injects cross-company duplicates for 10% of jobs."""
    jobs = []
    for j_idx in range(jobs_per_page):
        global_idx = (page_num * jobs_per_page) + j_idx
        # Cross-company duplicate on index divisible by 10
        is_dup = (global_idx % 10 == 0)
        c_name = "SharedCorp" if is_dup else f"Company_{company_idx}"
        title = f"Staff AI Engineer {global_idx % 50}" if is_dup else f"GenAI Specialist {company_idx}_{global_idx}"
        jobs.append({
            "id": f"job_{company_idx}_{global_idx}",
            "text": title,
            "hostedUrl": f"https://jobs.lever.co/{c_name}/{company_idx}_{global_idx}",
            "categories": {"location": "Dubai, UAE"},
            "createdAt": 1709280000000,
        })
    return jobs


def test_scale_dataset_thorough_collection_and_deduplication():
    """
    Scenario 1: Full Scale (>25 sources, >50 pages/source, >20,000 total jobs).
    Thorough mode processes the dataset with full budget, handles mixed blocked/success sources,
    and deduplicates cross-source duplicates.
    """
    num_sources = 30
    pages_per_source = 55  # > 50 pages
    jobs_per_page = 20     # 55 * 20 = 1,100 jobs/source (> 1,000)
    # Total available: ~24 * 1100 = 26,400 raw jobs (> 20,000)

    tasks = []
    for i in range(num_sources):
        t = CrawlTask(
            id=f"scale_task_{i:02d}",
            source="lever.co",
            source_type="ats",
            ats_platform="lever",
            company=f"comp_{i:02d}",
            url=f"https://jobs.lever.co/comp_{i:02d}",
            metadata={"limit": jobs_per_page},
        )
        tasks.append(t)

    # Mock dispatcher:
    # Sources 0..23: 200 OK with 55 pages of 20 jobs each (exhausted on page 55)
    # Sources 24..26: 403 Forbidden (Blocked)
    # Sources 27..29: 500 Server Error (Transient / Retry)
    def mock_safe_get_handler(url, **kwargs):
        # Extract company from url
        # https://api.lever.co/v0/postings/comp_XX?mode=json&limit=100&skip=YY
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        comp = path.split("/")[-1]
        c_idx = int(comp.replace("comp_", ""))
        params = urllib.parse.parse_qs(parsed.query)
        skip = int(params.get("skip", [0])[0])
        page = skip // jobs_per_page

        if 24 <= c_idx <= 26:
            m = MagicMock()
            m.status_code = 403
            return m
        elif 27 <= c_idx <= 29:
            m = MagicMock()
            m.status_code = 500
            return m
        else:
            if page >= pages_per_source:
                m = MagicMock()
                m.status_code = 200
                m.json = lambda: []
                return m
            raw_jobs = generate_mock_page_data(c_idx, page, jobs_per_page)
            m = MagicMock()
            m.status_code = 200
            m.json = lambda: raw_jobs
            return m

    budget = CrawlBudget(
        max_sources=50,
        max_pages_per_source=60,
        max_total_pages=5000,
        max_total_jobs=35000,
        per_domain_delay=0.0,
    )
    crawler = MultiSourceCrawler(budget=budget)

    with patch("datahunt.sources.crawlers._safe_get", side_effect=mock_safe_get_handler):
        result = crawler.crawl_tasks(tasks, role_keywords=["AI", "GenAI"])

    # Verify scale metrics
    assert result.sources_discovered == 30
    assert result.sources_crawled == 24
    assert result.sources_blocked >= 3
    # Total jobs extracted > 20,000
    assert result.jobs_extracted >= 20000

    # Verify task outcomes:
    # Successful sources must be COMPLETED
    for i in range(24):
        assert tasks[i].status == CrawlTaskStatus.COMPLETED
    # Blocked sources must be BLOCKED
    for i in range(24, 27):
        assert tasks[i].status == CrawlTaskStatus.BLOCKED
    # Failing sources must be RETRY_PENDING or FAILED
    for i in range(27, 30):
        assert tasks[i].status in (CrawlTaskStatus.RETRY_PENDING, CrawlTaskStatus.FAILED)

    # Verify duplicate merging
    deduped = result.merge_jobs()
    assert len(deduped) < len(result.crawled_jobs)  # Duplicates were successfully merged
    assert len(deduped) >= 20000


def test_restricted_budget_preserves_work_as_partial_and_resumes():
    """
    Scenario 2 & 3:
    1. A restricted budget (10 pages per source) halts the crawl before source exhaustion.
    2. Work is preserved: task is marked PARTIAL with saved cursor, not COMPLETED.
    3. Checkpoint is saved to the database.
    4. Resuming from checkpoint starts at page 10, fetches remaining 45 pages to page 55.
    5. Zero duplicate requests or dropped jobs upon completion.
    """
    run_id = f"scale_interrupted_{uuid.uuid4().hex[:8]}"
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    create_test_run(run_id, task_id)

    total_pages = 55
    jobs_per_page = 20

    task = CrawlTask(
        id="scale_resume_task",
        source="lever.co",
        source_type="ats",
        ats_platform="lever",
        company="scale_corp",
        url="https://jobs.lever.co/scale_corp",
        metadata={"limit": jobs_per_page},
    )

    def mock_safe_get_interrupted(url, **kwargs):
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        skip = int(params.get("skip", [0])[0])
        page = skip // jobs_per_page
        if page >= total_pages:
            m = MagicMock()
            m.status_code = 200
            m.json = lambda: []
            return m
        m = MagicMock()
        m.status_code = 200
        m.json = lambda: generate_mock_page_data(1, page, jobs_per_page)
        return m

    # Step 1: Run with restricted page budget of 10 pages (collects 200 jobs)
    budget_restricted = CrawlBudget(max_pages_per_source=10, max_total_jobs=50000, per_domain_delay=0.0)
    rate_limiter = _DomainRateLimiter(0)

    with patch("datahunt.sources.crawlers._safe_get", side_effect=mock_safe_get_interrupted):
        jobs_phase1 = crawl_lever_company(
            "scale_corp", ["AI", "GenAI"], budget_restricted, rate_limiter, lambda e: None, task=task
        )

    assert len(jobs_phase1) == 200
    assert task.status == CrawlTaskStatus.PARTIAL
    assert task.metadata.get("skip") == 200
    assert len(task.metadata.get("seen_skips", [])) == 10

    # Step 2: Save to CheckpointRepository
    ckpt_repo = CheckpointRepository()
    state = AgentState(request="GenAI Jobs", run_id=run_id, task_id=task_id, mode="jobs")
    state.discovery_state = DiscoveryState()
    state.discovery_state.partial_crawl_tasks.append(task)
    ckpt_id = ckpt_repo.save_checkpoint(run_id, state.to_checkpoint_dict(), seq=1)
    assert ckpt_id is not None

    # Step 3: Reconstruct from checkpoint
    latest = ckpt_repo.get_latest_checkpoint(run_id)
    restored_state = AgentState.from_checkpoint_dict(latest)
    assert len(restored_state.discovery_state.partial_crawl_tasks) == 1
    resumed_task = restored_state.discovery_state.partial_crawl_tasks[0]
    assert resumed_task.status == CrawlTaskStatus.PARTIAL
    assert resumed_task.metadata["skip"] == 200

    # Step 4: Resume with remaining budget up to 60 pages
    budget_unrestricted = CrawlBudget(max_pages_per_source=60, max_total_jobs=50000, per_domain_delay=0.0)

    with patch("datahunt.sources.crawlers._safe_get", side_effect=mock_safe_get_interrupted):
        jobs_phase2 = crawl_lever_company(
            "scale_corp", ["AI", "GenAI"], budget_unrestricted, rate_limiter, lambda e: None, task=resumed_task
        )

    # 45 pages * 20 jobs = 900 jobs
    assert len(jobs_phase2) == 900
    assert resumed_task.status == CrawlTaskStatus.COMPLETED

    # Total combined jobs: 200 + 900 = 1,100 jobs
    all_jobs = jobs_phase1 + jobs_phase2
    assert len(all_jobs) == 1100
    all_urls = [j.url for j in all_jobs]
    assert len(set(all_urls)) == 1100  # Zero duplicates, zero missing jobs!
