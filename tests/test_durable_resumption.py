"""
Tests proving durable resumption of the job collection pipeline across restarts.
Verifies that crawl tasks, pagination cursors, seen IDs/URLs, and partial states
survive database persistence and reconstruct into clean, duplicate-free complete collections.
"""
from datetime import datetime, timezone
import uuid
import pytest
from unittest.mock import MagicMock, patch

from datahunt.db import run_migrations, CheckpointRepository, get_connection
from datahunt.agent.state import AgentState, AgentStatus
from datahunt.agent.discovery_models import CrawlTask, CrawlTaskStatus, DiscoveryState, SourceCoverageMatrix
from datahunt.sources.crawlers import (
    MultiSourceCrawler,
    CrawlBudget,
    crawl_lever_company,
    crawl_workable_company,
    crawl_smartrecruiters_company,
    _DomainRateLimiter,
)
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.decision import AgentAction
from datahunt.models import ExtractedRecord


@pytest.fixture(autouse=True)
def init_db():
    run_migrations()


def create_test_run(run_id: str, task_id: str):
    conn = get_connection()
    now_str = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO research_tasks 
            (id, request_text, normalized_spec_json, policy_version, prompt_version, created_at, updated_at)
            VALUES (?, ?, '{"topic": "test request"}', '1.0', '1.0', ?, ?);
            """,
            (task_id, "test request", now_str, now_str)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO research_runs
            (id, task_id, status, budget_json, created_at, updated_at)
            VALUES (?, ?, 'planned', '{}', ?, ?);
            """,
            (run_id, task_id, now_str, now_str)
        )


def test_durable_resumption_lever_pagination_across_runtime_reconstruction():
    """
    Test Step-by-Step Durable Resumption:
    1. Collect several pages (pages 1-2 of 4 total pages, 200 of 400 jobs).
    2. Process halt: save checkpoint to SQLite database.
    3. Completely reconstruct a new AgentRuntime instance from scratch.
    4. Call resume(run_id) to restore from database checkpoint.
    5. Crawl continues from saved cursor (skip=200) for pages 3-4.
    6. Verify that all 400 jobs are present exactly once without duplication or omission.
    """
    run_id = f"test_run_{uuid.uuid4().hex[:8]}"
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    create_test_run(run_id, task_id)

    # 4 pages of 100 jobs = 400 jobs total (Lever API page size = 100)
    all_raw_pages = [
        [{"id": f"job_l_{i}", "text": f"GenAI Engineer {i}", "hostedUrl": f"https://jobs.lever.co/corp/{i}", "categories": {"location": "Dubai"}} for i in range(0, 100)],
        [{"id": f"job_l_{i}", "text": f"GenAI Engineer {i}", "hostedUrl": f"https://jobs.lever.co/corp/{i}", "categories": {"location": "Dubai"}} for i in range(100, 200)],
        [{"id": f"job_l_{i}", "text": f"GenAI Engineer {i}", "hostedUrl": f"https://jobs.lever.co/corp/{i}", "categories": {"location": "Dubai"}} for i in range(200, 300)],
        [{"id": f"job_l_{i}", "text": f"GenAI Engineer {i}", "hostedUrl": f"https://jobs.lever.co/corp/{i}", "categories": {"location": "Dubai"}} for i in range(300, 400)],
    ]

    # Phase 1: Run with budget limited to 2 pages
    budget_phase1 = CrawlBudget(max_pages_per_source=2, max_total_jobs=1000)
    rate_limiter = _DomainRateLimiter(0)
    task1 = CrawlTask(
        id="lever_corp_1",
        source="lever.co",
        source_type="ats",
        ats_platform="lever",
        company="corp",
        url="https://jobs.lever.co/corp",
    )

    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: all_raw_pages[0]),
            MagicMock(status_code=200, json=lambda: all_raw_pages[1]),
        ]
        jobs_p1 = crawl_lever_company("corp", ["GenAI"], budget_phase1, rate_limiter, lambda e: None, task=task1)

    assert len(jobs_p1) == 200
    assert task1.status == CrawlTaskStatus.PARTIAL
    assert task1.metadata.get("skip") == 200
    assert set(task1.metadata.get("seen_skips", [])) == {0, 100}

    # Initialize State and save checkpoint to DB
    state = AgentState(
        request="GenAI jobs in UAE",
        run_id=run_id,
        task_id=task_id,
        mode="jobs",
    )
    state.discovery_state = DiscoveryState()
    state.discovery_state.partial_crawl_tasks.append(task1)
    for j in jobs_p1:
        state.seen_canonical_urls.add(j.url)
        state.candidate_urls.append({"url": j.url, "title": j.title, "crawled": True})
        state.raw_records.append(ExtractedRecord(
            id=j.url,
            run_id=run_id,
            document_id=j.url,
            fields={"title": j.title, "company": j.company, "location": j.location, "apply_url": j.apply_url},
            confidence=0.9,
        ))

    ckpt_repo = CheckpointRepository()
    ckpt_id = ckpt_repo.save_checkpoint(run_id, state.to_checkpoint_dict(), seq=1)
    assert ckpt_id.startswith("chk_")

    # Verify database has checkpoint
    latest = ckpt_repo.get_latest_checkpoint(run_id)
    assert latest is not None
    assert latest["run_id"] == run_id
    saved_task = latest["discovery_state"]["partial_crawl_tasks"][0]
    assert saved_task["status"] == "PARTIAL"
    assert saved_task["metadata"]["skip"] == 200

    # Phase 2: Simulate process crash / complete reconstruction
    del state
    del task1

    mock_client = MagicMock()
    mock_search = MagicMock()
    mock_fetch = MagicMock()
    mock_extract = MagicMock()
    mock_verify = MagicMock()
    mock_dedupe = MagicMock()
    mock_export = MagicMock()

    # Reconstruct new runtime
    runtime2 = AgentRuntime(
        client=mock_client,
        search=mock_search,
        fetch=mock_fetch,
        extract=mock_extract,
        verify=mock_verify,
        dedupe=mock_dedupe,
        export=mock_export,
    )

    # Resume from checkpoint: pages 3 and 4 returned
    with patch("datahunt.sources.crawlers._safe_get") as mock_get_p2:
        mock_get_p2.side_effect = [
            MagicMock(status_code=200, json=lambda: all_raw_pages[2]),
            MagicMock(status_code=200, json=lambda: all_raw_pages[3]),
            MagicMock(status_code=200, json=lambda: []),  # End of results
        ]
        # Make decision engine stop after CRAWL_DIRECT
        with patch.object(runtime2.decision_engine, "decide") as mock_decide:
            mock_decide.side_effect = [
                (AgentAction.CRAWL_DIRECT, "Crawl direct partial sources"),
                (AgentAction.STOP, "All sources exhausted"),
            ]
            events = []
            res = runtime2.resume(run_id, lambda t, d: events.append((t, d)))

    assert res["status"] in ("completed", "partial")
    # Verify that all 400 candidate URLs are in state
    latest_ckpt = ckpt_repo.get_latest_checkpoint(run_id)
    resumed_state = AgentState.from_checkpoint_dict(latest_ckpt)

    # Collect all unique job IDs collected
    job_ids = set()
    for cand in resumed_state.candidate_urls:
        url = cand["url"]
        jid = url.split("/")[-1]
        job_ids.add(int(jid))

    assert len(job_ids) == 400
    assert min(job_ids) == 0
    assert max(job_ids) == 399
    # Verify exactly 400 unique records without duplication
    assert len(resumed_state.seen_canonical_urls) == 400


def test_durable_resumption_smartrecruiters_offset_across_restarts():
    """Verify SmartRecruiters crawler resumes from offset cursor without skipping or repeating."""
    budget = CrawlBudget(max_pages_per_source=2, max_total_jobs=1000)
    rate_limiter = _DomainRateLimiter(0)

    # 3 pages of 10 jobs = 30 total found
    def make_sr_page(start_idx, count, total=30):
        return {
            "totalFound": total,
            "content": [
                {"id": f"sr_{i}", "name": f"GenAI Lead {i}", "releasedDate": "2026-03-01", "location": {"city": "Riyadh", "country": "Saudi Arabia"}}
                for i in range(start_idx, start_idx + count)
            ]
        }

    task = CrawlTask(
        id="sr_task_1",
        source="smartrecruiters.com",
        source_type="ats",
        ats_platform="smartrecruiters",
        company="techcorp",
        url="https://jobs.smartrecruiters.com/techcorp",
    )

    # Phase 1: 1 page fetched, paused
    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: make_sr_page(0, 10, 30)),
        ]
        # max_pages=1
        budget1 = CrawlBudget(max_pages_per_source=1, max_total_jobs=1000)
        jobs1 = crawl_smartrecruiters_company("techcorp", ["GenAI"], budget1, rate_limiter, lambda e: None, task=task)

    assert len(jobs1) == 10
    assert task.status == CrawlTaskStatus.PARTIAL
    assert task.metadata.get("offset") == 10

    # Save to CheckpointRepository
    run_id = f"sr_run_{uuid.uuid4().hex[:8]}"
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    create_test_run(run_id, task_id)

    ckpt_repo = CheckpointRepository()
    state = AgentState(request="GenAI Riyadh", run_id=run_id, task_id=task_id, mode="jobs")
    state.discovery_state = DiscoveryState()
    state.discovery_state.partial_crawl_tasks.append(task)
    ckpt_repo.save_checkpoint(run_id, state.to_checkpoint_dict(), seq=1)

    # Phase 2: Restore from checkpoint and finish remaining pages
    restored = ckpt_repo.get_latest_checkpoint(run_id)
    restored_state = AgentState.from_checkpoint_dict(restored)
    assert len(restored_state.discovery_state.partial_crawl_tasks) == 1
    resumed_task = restored_state.discovery_state.partial_crawl_tasks[0]
    assert resumed_task.metadata["offset"] == 10

    budget2 = CrawlBudget(max_pages_per_source=10, max_total_jobs=1000)
    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: make_sr_page(10, 10, 30)),
            MagicMock(status_code=200, json=lambda: make_sr_page(20, 10, 30)),
        ]
        jobs2 = crawl_smartrecruiters_company("techcorp", ["GenAI"], budget2, rate_limiter, lambda e: None, task=resumed_task)

    assert len(jobs2) == 20
    assert resumed_task.status == CrawlTaskStatus.COMPLETED
    assert resumed_task.metadata.get("offset") == 30

    all_jobs = jobs1 + jobs2
    assert len(all_jobs) == 30
    all_ids = [j.apply_url.split("/")[-1] for j in all_jobs]
    assert len(set(all_ids)) == 30


def test_durable_resumption_workable_token_across_restarts():
    """Verify Workable crawler resumes with next_token pagination without losing cursor state."""
    budget1 = CrawlBudget(max_pages_per_source=1, max_total_jobs=1000)
    rate_limiter = _DomainRateLimiter(0)

    task = CrawlTask(
        id="wk_task_1",
        source="workable.com",
        source_type="ats",
        ats_platform="workable",
        company="saascorp",
        url="https://apply.workable.com/saascorp",
    )

    page1_resp = {
        "results": [{"id": "wk_1", "title": "GenAI Architect", "shortlink": "https://apply.workable.com/j/1", "location": {"country": "UAE"}}],
        "nextPage": "cursor_token_page2",
    }
    page2_resp = {
        "results": [{"id": "wk_2", "title": "LLM Engineer", "shortlink": "https://apply.workable.com/j/2", "location": {"country": "UAE"}}],
        "nextPage": None,
    }

    # Step 1: First page crawl pauses at page limit
    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: page1_resp)
        jobs1 = crawl_workable_company("saascorp", ["GenAI", "LLM"], budget1, rate_limiter, lambda e: None, task=task)

    assert len(jobs1) == 1
    assert task.status == CrawlTaskStatus.PARTIAL
    assert task.metadata.get("next_token") == "cursor_token_page2"

    # Step 2: Save checkpoint and restore
    run_id = f"wk_run_{uuid.uuid4().hex[:8]}"
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    create_test_run(run_id, task_id)

    ckpt_repo = CheckpointRepository()
    state = AgentState(request="GenAI UAE", run_id=run_id, task_id=task_id, mode="jobs")
    state.discovery_state = DiscoveryState()
    state.discovery_state.partial_crawl_tasks.append(task)
    ckpt_repo.save_checkpoint(run_id, state.to_checkpoint_dict(), seq=1)

    restored = ckpt_repo.get_latest_checkpoint(run_id)
    restored_task = AgentState.from_checkpoint_dict(restored).discovery_state.partial_crawl_tasks[0]
    assert restored_task.metadata["next_token"] == "cursor_token_page2"

    # Step 3: Resume
    budget2 = CrawlBudget(max_pages_per_source=10, max_total_jobs=1000)
    with patch("datahunt.sources.crawlers._safe_get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: page2_resp)
        jobs2 = crawl_workable_company("saascorp", ["GenAI", "LLM"], budget2, rate_limiter, lambda e: None, task=restored_task)

    assert len(jobs2) == 1
    assert restored_task.status == CrawlTaskStatus.COMPLETED
    assert restored_task.metadata.get("next_token") is None

    total_jobs = jobs1 + jobs2
    assert len(total_jobs) == 2
    assert {j.title for j in total_jobs} == {"GenAI Architect", "LLM Engineer"}
