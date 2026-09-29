"""
Comprehensive verification test suite for DataHunt-Agent runtime, crawler,
discovery loop, and verification fixes.

Covers the 12 architectural/runtime issues:
  1. Direct crawling happens after useful source discovery (or when tasks queued).
  2. Direct crawler uses HTTP fallback retrieval without forbidden words in logs.
  3. crawl_done does not prevent crawling sources discovered in later iterations.
  4. Multi-region queries crawl all target regions (e.g. Saudi Arabia AND UAE).
  5. Direct crawlers use broad title matching, preserving AI/GenAI/ML role variants.
  6. Career pages are enqueued and crawled when companies are discovered.
  7. Production search never produces fake example.com mock records.
  8. Gemini quota exhaustion falls back gracefully without breaking search.
  9. Verification processes each record exactly once and converges without looping.
  10. VerificationStatus import and enum scoping validity.
  11. Source productivity prioritizes qualified jobs over raw candidate counts.
  12. Runtime loop reaches terminal convergence without hitting max iteration cap.
"""
import pytest
from unittest.mock import MagicMock, patch

from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.agent.state import AgentState, AgentStatus, get_unprocessed_records
from datahunt.agent.decision import AgentAction, DecisionEngine
from datahunt.agent.discovery_models import (
    CrawlTask,
    CrawlTaskStatus,
    DiscoveryBudget,
    DiscoveryState,
    SearchTask,
    SearchTaskType,
    DiscoveredSourceType,
)
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.sources.crawlers import (
    MultiSourceCrawler,
    CrawlBudget,
    is_broadly_relevant_job_title,
    _safe_get,
    SafeCrawlerResponse,
)
from datahunt.tools.search import HybridSearchProvider, SearchHit
from datahunt.agents.query_understanding import QueryUnderstandingAgent, JobSearchRequest


# ─────────────────────────────────────────────────────────────────────────────
# 1. Direct Crawling Happens After Useful Source Discovery
# ─────────────────────────────────────────────────────────────────────────────

def test_initial_search_runs_before_blind_crawling_when_discovery_queue_has_tasks():
    state = AgentState(
        request="GenAI Engineer in UAE",
        run_id="run_1",
        task_id="task_1",
        mode="jobs",
        max_search_calls=15,
        target_results=5,
    )
    budget = DiscoveryBudget()
    state.discovery_budget = budget
    state.discovery_state = DiscoveryState()
    
    # Discovery engine formulated initial discovery tasks
    state.discovery_state.task_queue.append(SearchTask(
        id="init_1",
        task_type=SearchTaskType.ATS_SEARCH,
        query='site:boards.greenhouse.io "GenAI Engineer" UAE',
        source="boards.greenhouse.io",
        source_type=DiscoveredSourceType.ATS_PORTAL,
        priority=1,
    ))

    engine = DecisionEngine()
    action, reason = engine.decide(state)
    # Search must run first to discover companies and endpoints
    assert action == AgentAction.SEARCH
    assert "Searching discovery queue" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 2. Direct Crawler Uses HTTP Fallback Retrieval & Safe Logging
# ─────────────────────────────────────────────────────────────────────────────

@patch("httpx.Client.get")
@patch("ddgs.DDGS")
def test_crawler_fallback_retrieval_on_http_403(mock_ddgs_cls, mock_httpx_get, caplog):
    # Simulate a 403 Forbidden response from a job board
    blocked_resp = MagicMock()
    blocked_resp.status_code = 403
    mock_httpx_get.return_value = blocked_resp

    mock_instance = MagicMock()
    mock_ddgs_cls.return_value = mock_instance
    mock_instance.extract.return_value = {
        "content": "<html><body><h1>Careers at Tech Co</h1><a href='/jobs/1'>AI Engineer</a></body></html>" * 3
    }

    import logging
    with caplog.at_level(logging.INFO):
        resp = _safe_get("https://boards.greenhouse.io/techco/jobs")
        assert resp is not None
        assert resp.status_code == 200
        assert "AI Engineer" in resp.text
        # Ensure the forbidden word "bypassed" is never used in logs
        assert "bypassed" not in caplog.text.lower()
        assert "fallback retrieval succeeded" in caplog.text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 3. crawl_done Does Not Lock Out Newly Discovered Sources
# ─────────────────────────────────────────────────────────────────────────────

def test_crawl_done_does_not_prevent_crawling_sources_discovered_later():
    state = AgentState(
        request="GenAI Engineer in UAE",
        run_id="run_3",
        task_id="task_3",
        mode="jobs",
        max_search_calls=15,
        target_results=5,
    )
    state.crawl_done = True  # initial pre-search crawl already completed
    state.discovery_state = DiscoveryState()
    
    # Search in round 2 discovered a new ATS company
    state.discovery_state.enqueue_crawl_task(CrawlTask(
        id="crawl_discovered_co",
        source="boards.greenhouse.io",
        source_type="ats",
        url="https://boards.greenhouse.io/cartlow/jobs",
        company="cartlow",
        ats_platform="greenhouse",
        priority=1,
    ))

    engine = DecisionEngine()
    action, reason = engine.decide(state)
    assert action == AgentAction.CRAWL_DIRECT
    assert "Direct source crawling (1 tasks in queue)" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 4. Multi-Region Crawling Covers All Target Locations
# ─────────────────────────────────────────────────────────────────────────────

def test_multi_region_crawling_generates_independent_configs_for_all_regions():
    crawler = MultiSourceCrawler(budget=CrawlBudget(max_workers=1))
    
    with patch("datahunt.sources.crawlers.crawl_regional_board_html") as mock_regional:
        mock_regional.return_value = []
        crawler.crawl(
            role_keywords=["GenAI Engineer"],
            locations=["Saudi Arabia OR UAE"],
            discovered_ats={},
            discovered_companies={},
            is_gulf=True,
        )

        crawled_urls = [call.kwargs.get("listing_url", "") for call in mock_regional.call_args_list]
        
        # Verify both Saudi Arabia and UAE were targeted in regional crawls
        has_saudi = any("saudi" in u.lower() for u in crawled_urls)
        has_uae = any("uae" in u.lower() or "dubai" in u.lower() for u in crawled_urls)
        assert has_saudi, "Multi-region crawl failed to crawl Saudi Arabia"
        assert has_uae, "Multi-region crawl failed to crawl UAE"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Direct Crawlers Use Broad Role Matching, Preserving Variants
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Generative AI Engineer", True),
    ("Applied AI Engineer", True),
    ("AI Engineer", True),
    ("LLM Engineer", True),
    ("Machine Learning Engineer - NLP/RAG", True),
    ("Lead Foundation Model Researcher", True),
    ("", True),  # Link stubs without title should be inspected
    ("Full Stack React Developer", False),
    ("Accountant and Bookkeeper", False),
    ("Cybersecurity Operations Analyst", False),
])
def test_broadly_relevant_job_title_preserves_variants(title, expected):
    keywords = ["GenAI Engineer"]
    assert is_broadly_relevant_job_title(title, keywords) is expected


# ─────────────────────────────────────────────────────────────────────────────
# 6. Career Pages Are Crawled After Companies Discovered
# ─────────────────────────────────────────────────────────────────────────────

def test_company_career_page_enqueued_and_crawled():
    engine = DiscoveryEngine()
    disc_state = DiscoveryState()
    state = AgentState(request="GenAI Engineer in UAE", run_id="r6", task_id="t6", mode="jobs")
    
    hits = [
        {
            "url": "https://careem.com/careers/genai-lead-123",
            "title": "Careem Careers - GenAI Lead",
            "snippet": "Join Careem in Dubai as a GenAI Lead engineer.",
            "source_domain": "careem.com",
        }
    ]
    task = SearchTask(id="st6", task_type=SearchTaskType.GENERAL_SEARCH, query="test", source="web", source_type=DiscoveredSourceType.MAJOR_BOARD)
    
    engine.process_search_hits(hits, task, state, disc_state)
    
    # Verify crawl task was enqueued
    pending = disc_state.pending_crawl_tasks
    assert len(pending) >= 1
    assert any("careem" in (t.company or "").lower() or "careem.com" in t.source for t in pending)
    assert any(t.source_type == "company_career_page" for t in pending)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Production Search Never Falls Back to Mock example.com Records
# ─────────────────────────────────────────────────────────────────────────────

def test_production_search_never_returns_mock_example_com():
    # Production initializes HybridSearchProvider with default fallback_to_mock=False
    provider = HybridSearchProvider(fallback_to_mock=False)
    
    with patch.object(provider.ddg, "search", return_value=[]), \
         patch.object(provider.job_board, "search", return_value=[]), \
         patch.object(provider.llm_search, "search", return_value=[]):
        hits = provider.search("GenAI Engineer in Dubai")
        
        assert hits == [], "Live search must return empty list on provider exhaustion"
        assert not any("example.com" in h.url for h in hits)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Gemini Quota Exhaustion Degrades Gracefully
# ─────────────────────────────────────────────────────────────────────────────

def test_gemini_quota_exhaustion_deterministic_fallback():
    mock_client = MagicMock()
    mock_client.is_live = True
    from datahunt.errors import DataHuntError, ErrorCode
    mock_client._call_gemini_json.side_effect = DataHuntError(
        ErrorCode.MODEL_RATE_LIMITED,
        user_message="Rate limited 429",
        operator_message="Gemini quota exhausted"
    )

    qu = QueryUnderstandingAgent(gemini_client=mock_client)
    res = qu.understand("Looking for Generative AI Engineer in Dubai with 3-5 years exp")
    
    # Must succeed via deterministic rule fallback without crashing
    assert isinstance(res, JobSearchRequest)
    assert "AI" in res.job_title or "Generative" in res.job_title
    assert "dubai" in (res.location or "").lower()


# ─────────────────────────────────────────────────────────────────────────────
# 9. Verification State Machine Converges Without Endless Loops
# ─────────────────────────────────────────────────────────────────────────────

def test_verification_dedup_and_convergence_prevents_infinite_loop():
    state = AgentState(request="test", run_id="r9", task_id="t9", mode="jobs")
    
    rec1 = ExtractedRecord(id="rec_1", run_id="r9", fields={"title": "GenAI Engineer", "company": "Co A"})
    rec2 = ExtractedRecord(id="rec_2", run_id="r9", fields={"title": "GenAI Engineer", "company": "Co A"})  # Duplicate
    state.raw_records.extend([rec1, rec2])
    state.raw_record_generation = 1
    state.last_verified_generation = -1

    decision_engine = DecisionEngine()
    
    # 1. Verification needed initially
    action, reason = decision_engine.decide(state)
    assert action == AgentAction.VERIFY
    
    # Simulate _act_verify processing: rec1 qualified, rec2 marked duplicate
    state.processed_record_ids.add(rec1.id)
    state.processed_record_ids.add(rec2.id)
    rec1.verification_status = VerificationStatus.VERIFIED
    rec2.verification_status = VerificationStatus.DUPLICATE
    state.qualified_records.append(rec1)
    state.verified_records.append(rec1)
    state.duplicate_records.append(rec2)
    state.last_verified_generation = state.raw_record_generation

    # Invariant: unprocessed records must be empty
    assert len(get_unprocessed_records(state)) == 0

    # 2. Decision engine must NOT choose VERIFY again
    action2, reason2 = decision_engine.decide(state)
    assert action2 != AgentAction.VERIFY


# ─────────────────────────────────────────────────────────────────────────────
# 10. VerificationStatus Import & Enum Scoping
# ─────────────────────────────────────────────────────────────────────────────

def test_verification_status_scoping():
    from datahunt.models import VerificationStatus
    assert VerificationStatus.VERIFIED == "verified"
    assert VerificationStatus.REJECTED == "rejected"
    assert VerificationStatus.NEEDS_REVIEW == "needs_review"
    assert VerificationStatus.DUPLICATE == "duplicate"
    assert VerificationStatus.UNVERIFIED == "unverified"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Discovery Productivity Tracks Qualified Yield
# ─────────────────────────────────────────────────────────────────────────────

def test_discovery_productivity_rewards_qualified_jobs():
    disc_state = DiscoveryState()
    
    # Source A produces 5 qualified jobs
    disc_state.record_task_productivity(
        source="boards.greenhouse.io",
        raw_hits=10,
        candidate_jobs=5,
        qualified_jobs=5,
        duplicate_jobs=0,
    )

    # Source B produces 10 duplicates and 0 qualified jobs
    disc_state.record_task_productivity(
        source="low_yield_aggregator.com",
        raw_hits=20,
        candidate_jobs=10,
        qualified_jobs=0,
        duplicate_jobs=10,
    )

    score_a = disc_state.source_productivity["boards.greenhouse.io"]["productivity_score"]
    score_b = disc_state.source_productivity["low_yield_aggregator.com"]["productivity_score"]
    
    assert score_a > score_b, f"Expected Source A score ({score_a}) > Source B score ({score_b})"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Runtime Loop Convergence Without Max Iterations Cap
# ─────────────────────────────────────────────────────────────────────────────

def test_runtime_stops_on_satisfied_results_without_reaching_max_cap():
    state = AgentState(
        request="GenAI Engineer in UAE",
        run_id="r12",
        task_id="t12",
        mode="jobs",
        target_results=2,
        max_iterations=20,
    )
    state.discovery_state = DiscoveryState()
    
    rec_a = ExtractedRecord(id="ra", run_id="r12", fields={"title": "GenAI Engineer", "location": "Dubai, UAE"})
    rec_b = ExtractedRecord(id="rb", run_id="r12", fields={"title": "AI Engineer", "location": "Abu Dhabi, UAE"})
    state.qualified_records.extend([rec_a, rec_b])
    state.verified_records.extend([rec_a, rec_b])
    state.processed_record_ids.update({"ra", "rb"})
    state.last_verified_generation = state.raw_record_generation

    # Ensure coverage matrix has balanced categories
    state.discovery_state.coverage_matrix.record_hit("ats", "uae")
    state.discovery_state.coverage_matrix.record_hit("regional_board", "uae")

    engine = DecisionEngine()
    action, reason = engine.decide(state)
    
    assert action == AgentAction.STOP
    assert "Target results" in reason and "reached" in reason
    assert state.iteration < state.max_iterations
