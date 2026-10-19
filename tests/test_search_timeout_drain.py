"""
Regression tests for Search Cutoff & Pipeline Drain:
Ensures that after search deadline (e.g. 30min), NO new searches or crawls occur,
but ALL existing discovered candidates and unverified records are fetched,
extracted, verified, and output cleanly.
"""
import time
from typing import Dict, Any
from unittest.mock import MagicMock

import pytest

from datahunt.agent.decision import AgentAction, DecisionEngine
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agent.discovery_models import DiscoveryBudget, DiscoveryState, SearchTask
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.state import AgentState
from datahunt.models import ExtractedRecord, SourceDocument, VerificationStatus


def make_test_state(**kwargs) -> AgentState:
    defaults = dict(
        request="Find Senior AI Engineer jobs in Dubai",
        run_id="run_drain_test",
        task_id="task_drain_test",
        mode="jobs",
        target_results=10,
        max_iterations=50,
        max_search_calls=30,
        max_fetch_calls=100,
    )
    defaults.update(kwargs)
    return AgentState(**defaults)


def test_search_deadline_blocks_new_searches_and_direct_crawls():
    """When search deadline is reached, SEARCH and CRAWL_DIRECT are blocked, but pending candidate URLs are fetched."""
    engine = DecisionEngine()
    past_search_deadline = time.time() - 5.0
    future_hard_deadline = time.time() + 600.0

    state = make_test_state(
        search_deadline=past_search_deadline,
        hard_deadline=future_hard_deadline,
        candidate_urls=[
            {"url": "https://jobs.lever.co/company1/job1", "title": "AI Engineer"},
            {"url": "https://boards.greenhouse.io/company2/job2", "title": "ML Engineer"},
        ],
        search_plan=[{"query": "AI Engineer Dubai", "tier": 1, "purpose": "primary"}],
    )

    action, reason = engine.decide(state)
    assert action == AgentAction.FETCH
    assert "candidates to fetch" in reason
    assert action != AgentAction.SEARCH
    assert action != AgentAction.CRAWL_DIRECT


def test_search_deadline_drains_all_unverified_records_to_verify():
    """When search deadline is reached and candidates have been fetched into raw records, VERIFY processes them."""
    engine = DecisionEngine()
    past_search_deadline = time.time() - 5.0
    future_hard_deadline = time.time() + 600.0

    rec = ExtractedRecord(
        run_id="run_drain_test",
        record_type="job_listing",
        fields={"title": "Generative AI Engineer", "company": "TechCorp", "location": "Dubai, UAE"},
    )

    state = make_test_state(
        search_deadline=past_search_deadline,
        hard_deadline=future_hard_deadline,
        candidate_urls=[],
        raw_records=[rec],
        raw_record_generation=1,
        last_verified_generation=-1,
    )

    action, reason = engine.decide(state)
    assert action == AgentAction.VERIFY
    assert "records need verification" in reason


def test_search_deadline_stops_cleanly_only_after_all_candidates_and_records_completed():
    """After search deadline, once candidate URLs are empty and all records are verified, agent stops cleanly with output."""
    engine = DecisionEngine()
    past_search_deadline = time.time() - 5.0
    future_hard_deadline = time.time() + 600.0

    rec = ExtractedRecord(
        run_id="run_drain_test",
        record_type="job_listing",
        fields={"title": "Generative AI Engineer", "company": "TechCorp", "location": "Dubai, UAE"},
        verification_status=VerificationStatus.VERIFIED,
        confidence=0.92,
    )

    state = make_test_state(
        search_deadline=past_search_deadline,
        hard_deadline=future_hard_deadline,
        candidate_urls=[],
        raw_records=[rec],
        verified_records=[rec],
        qualified_records=[rec],
        raw_record_generation=1,
        last_verified_generation=1,  # already verified
    )

    action, reason = engine.decide(state)
    assert action == AgentAction.STOP
    assert "Search Deadline reached; all existing candidates processed" in reason
    assert "returning 1 results found" in reason


def test_hard_deadline_terminates_even_with_pending_work_as_safety_brake():
    """If hard deadline is exceeded (e.g. 30min + drain timeout), agent stops unconditionally to prevent hangs."""
    engine = DecisionEngine()
    past_hard_deadline = time.time() - 1.0

    state = make_test_state(
        hard_deadline=past_hard_deadline,
        search_deadline=past_hard_deadline - 600.0,
        candidate_urls=[{"url": "https://slow-site.com/job1"}],
    )

    action, reason = engine.decide(state)
    assert action == AgentAction.STOP
    assert "Hard Deadline reached" in reason


def test_discovery_engine_should_stop_allows_draining_when_search_deadline_reached():
    """DiscoveryEngine should_stop() does not stop while has_pending_work is True, then stops once drained."""
    budget = DiscoveryBudget(max_search_requests=20)
    engine = DiscoveryEngine(budget=budget)
    past_search_deadline = time.time() - 5.0
    future_hard_deadline = time.time() + 600.0

    state = make_test_state(
        search_deadline=past_search_deadline,
        hard_deadline=future_hard_deadline,
        candidate_urls=[{"url": "https://example.com/job"}],
    )
    disc_state = DiscoveryState()

    # While candidates exist, should_stop should return False (allow drain)
    stop_flag, stop_reason, msg = engine.should_stop(disc_state, state)
    assert not stop_flag

    # Once candidates are cleared and no unverified records remain, should_stop returns True
    state.candidate_urls = []
    stop_flag, stop_reason, msg = engine.should_stop(disc_state, state)
    assert stop_flag
    assert "Search deadline reached" in msg


def test_runtime_end_to_end_search_timeout_drain_and_output_generation():
    """
    End-to-end integration test:
    Simulate a run where search deadline expires after collecting candidate URLs.
    AgentRuntime must NOT execute any new searches, must fetch all candidates,
    verify them, and return them in the final result output.
    """
    mock_search = MagicMock()
    mock_fetch = MagicMock()
    mock_extract = MagicMock()
    mock_verify = MagicMock()
    mock_client = MagicMock()

    # Candidate page HTML content
    doc1 = SourceDocument(
        id="doc_1",
        run_id="run_drain_test",
        requested_url="https://jobs.lever.co/techcorp/123",
        domain="jobs.lever.co",
        extracted_text="Job Title: GenAI Engineer\nCompany: TechCorp\nLocation: Dubai, UAE\nRequirements: Python, LLM",
        http_status=200,
    )
    doc2 = SourceDocument(
        id="doc_2",
        run_id="run_drain_test",
        requested_url="https://boards.greenhouse.io/aiminds/456",
        domain="boards.greenhouse.io",
        extracted_text="Job Title: AI Engineer\nCompany: AIMinds\nLocation: Dubai, UAE\nRequirements: PyTorch, Agents",
        http_status=200,
    )

    def fetch_execute(url, run_id=None, **kwargs):
        data_doc = doc1 if "lever" in url else doc2
        return MagicMock(success=True, data=data_doc)

    mock_fetch.execute.side_effect = fetch_execute

    rec1 = ExtractedRecord(
        id="rec_1",
        run_id="run_drain_test",
        record_type="job_listing",
        source_document_id="doc_1",
        canonical_url="https://jobs.lever.co/techcorp/123",
        fields={"title": "GenAI Engineer", "company": "TechCorp", "location": "Dubai, UAE"},
    )
    rec2 = ExtractedRecord(
        id="rec_2",
        run_id="run_drain_test",
        record_type="job_listing",
        source_document_id="doc_2",
        canonical_url="https://boards.greenhouse.io/aiminds/456",
        fields={"title": "AI Engineer", "company": "AIMinds", "location": "Dubai, UAE"},
    )

    def extract_execute(doc, spec, run_id=None):
        data_rec = [rec1] if "lever" in doc.requested_url else [rec2]
        return MagicMock(success=True, data=data_rec)

    mock_extract.execute.side_effect = extract_execute

    def verify_execute(record, **kwargs):
        record.verification_status = VerificationStatus.VERIFIED
        record.confidence = 0.95
        return MagicMock(success=True, data=record)

    mock_verify.execute.side_effect = verify_execute

    mock_dedupe = MagicMock()
    mock_dedupe.execute.side_effect = lambda recs: MagicMock(success=True, data={"unique_records": recs})

    runtime = AgentRuntime(
        client=mock_client,
        search=mock_search,
        fetch=mock_fetch,
        extract=mock_extract,
        verify=mock_verify,
        dedupe=mock_dedupe,
        export=MagicMock(),
    )

    from datahunt.agents.query_understanding import JobSearchRequest
    job_req = JobSearchRequest(
        raw_query="Find Senior AI Engineer jobs in Dubai",
        job_title="AI Engineer",
        location="Dubai",
        locations=["Dubai", "UAE"],
    )

    # State has expired search deadline (simulating 30min passed) and 2 candidate URLs in queue
    past_search_deadline = time.time() - 2.0
    state = make_test_state(
        canonical_job_request=job_req,
        search_deadline=past_search_deadline,
        hard_deadline=time.time() + 600.0,
        candidate_urls=[
            {"url": "https://jobs.lever.co/techcorp/123", "title": "GenAI Engineer"},
            {"url": "https://boards.greenhouse.io/aiminds/456", "title": "AI Engineer"},
        ],
        search_plan=[{"query": "AI Engineer Dubai", "tier": 1, "purpose": "primary"}],
    )

    events = []
    def emit_fn(evt_type, data):
        events.append((evt_type, data))

    # Run the runtime decision loop
    result = runtime.run(state, emit_fn)

    # 1. Zero new search queries executed
    assert mock_search.execute.call_count == 0

    # 2. Both candidates were fetched
    assert mock_fetch.execute.call_count == 2

    # 3. Both documents were extracted
    assert mock_extract.execute.call_count == 2

    # 4. Final output includes both completed records
    assert len(result["records"]) == 2
    rec_titles = [r.fields.get("title") for r in result["records"]]
    assert "GenAI Engineer" in rec_titles
    assert "AI Engineer" in rec_titles

    # 5. Completion reason documents that all candidates were processed after search deadline
    assert "Search Deadline reached; all existing candidates processed" in state.completion_reason
