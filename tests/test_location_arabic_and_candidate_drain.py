import types
import time
import pytest

from datahunt.agent.policies import (
    qualify_job,
    match_location,
    MatchStatus,
    GEO_ALIAS_GROUPS,
)
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.state import AgentState
from datahunt.models.job_spec import normalize_geo_name


def test_arabic_and_iso_location_normalization():
    assert normalize_geo_name("ae") == "UAE"
    assert normalize_geo_name("sa") == "Saudi Arabia"
    assert normalize_geo_name("أبو ظبي") == "Abu Dhabi"
    assert normalize_geo_name("الرياض") == "Riyadh"
    assert normalize_geo_name("دبي") == "Dubai"
    assert normalize_geo_name("السعودية") == "Saudi Arabia"


def test_arabic_and_iso_match_location():
    # UAE with Arabic and ISO code
    status, reason = match_location("Saudi Arabia or UAE", "أبو ظبي, AE")
    assert status == MatchStatus.MATCH
    assert "UAE" in reason

    # Riyadh with Arabic and SA code
    status, reason = match_location("Saudi Arabia or UAE", "الرياض, SA")
    assert status == MatchStatus.MATCH
    assert "SAUDI" in reason

    # Foreign European location returns MISMATCH
    status, reason = match_location("Saudi Arabia or UAE", "Gothenburg, Sweden")
    assert status == MatchStatus.MISMATCH


def test_qualify_job_arabic_abu_dhabi():
    req = types.SimpleNamespace(
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location="Saudi Arabia",
        experience_min=0,
        experience_max=6,
        raw_query="Find GenAI Engineer jobs in Saudi or UAE with 0-6 years experience",
        alternative_titles=[],
        explicit_skills=[],
        inferred_skills=[],
        salary_min=None,
        salary_currency=None,
        remote_allowed=True,
        excluded_titles=[],
        excluded_companies=[],
    )
    rec = {
        "title": "Generative AI Engineer – Agentic AI | Azure",
        "company": "HireOn",
        "location": "أبو ظبي, AE",
        "experience_min": 4,
        "experience_max": 6,
    }
    result = qualify_job(rec, req)
    assert result.qualified is True
    assert result.score >= 0.8
    assert result.rejection_reasons == []


def test_candidate_triage_filters_foreign_and_retains_gulf():
    state = AgentState(
        run_id="run_test",
        task_id="task_test",
        request="Find GenAI Engineer in Saudi or UAE",
        locations=["Saudi Arabia", "UAE"],
        mode="jobs",
    )
    runtime = object.__new__(AgentRuntime)

    # Foreign candidate from global ATS crawl
    foreign_hit = {
        "url": "https://boards.greenhouse.io/volvo/jobs/123",
        "title": "GenAI Engineer",
        "snippet": "Join our AI team",
        "location": "Gothenburg, Sweden",
    }
    assert runtime._is_promising_candidate(foreign_hit, state) is False

    # Arabic UAE candidate
    uae_hit = {
        "url": "https://ae.linkedin.com/jobs/view/123",
        "title": "Generative AI Engineer",
        "snippet": "AI development",
        "location": "أبو ظبي, AE",
    }
    assert runtime._is_promising_candidate(uae_hit, state) is True


def test_candidate_drain_preservation_under_hard_deadline():
    state = AgentState(
        run_id="run_test",
        task_id="task_test",
        request="Find GenAI Engineer in Saudi or UAE",
        locations=["Saudi Arabia", "UAE"],
        mode="jobs",
        collection_mode="thorough",
        fetch_calls=300,
        max_fetch_calls=300,
        deadline=time.time() - 100,  # search phase expired
        search_deadline=time.time() - 100,
        hard_deadline=time.time() + 600,  # 10 minutes grace remain for drain
    )

    state.candidate_urls = [
        {"url": f"https://example.com/job_{i}", "title": "GenAI Engineer", "location": "Dubai"}
        for i in range(50)
    ]

    fetched_batches = []
    class DummyFetch:
        def execute(self, url, run_id):
            fetched_batches.append(url)
            return types.SimpleNamespace(success=True, data={"url": url})

    runtime = AgentRuntime(
        client=None,
        search=None,
        fetch=DummyFetch(),
        extract=None,
        verify=None,
        dedupe=None,
        export=None,
    )

    def dummy_emit(event, data):
        pass

    # First fetch call should drain 25 candidates without dropping the rest
    runtime._act_fetch(state, dummy_emit)

    assert len(fetched_batches) == 25
    assert len(state.candidate_urls) == 25  # Remaining 25 preserved!
    assert state.candidate_urls != []
