"""
Tests for:
1. Broadened GenAI title matching (LLMOps, NLP, Applied Scientist, etc.)
2. GCC/MENA location fuzzy match preventing unverified_location rejections
3. Unverified_location NOT added for region-hinted job locations
4. Consecutive low-yield counter resets on qualified_new > 0
"""
import types
import pytest
from datahunt.agent.policies import (
    match_title_relevance, match_location, MatchStatus, QualificationPolicy
)
from datahunt.agent.decision import DecisionEngine, CONSECUTIVE_LOW_YIELD_LIMIT
from datahunt.agent.state import AgentState


# ─── 1. Title matching ────────────────────────────────────────────────────────

@pytest.mark.parametrize("job_title,expected", [
    ("LLM Engineer", MatchStatus.MATCH),
    ("LLM Developer", MatchStatus.MATCH),
    ("LLMOps Engineer", MatchStatus.MATCH),
    ("NLP Engineer", MatchStatus.MATCH),
    ("Machine Learning Engineer", MatchStatus.MATCH),
    ("Applied Scientist", MatchStatus.MATCH),
    ("AI Research Scientist", MatchStatus.MATCH),
    ("AI Researcher", MatchStatus.MATCH),
    ("AI Specialist", MatchStatus.MATCH),
    ("Deep Learning Engineer", MatchStatus.MATCH),
    ("ML Researcher", MatchStatus.MATCH),
    ("Accountant", MatchStatus.MISMATCH),
    ("Sales Manager", MatchStatus.MISMATCH),
    ("HR Manager", MatchStatus.MISMATCH),
])
def test_genai_title_variants_match(job_title, expected):
    """All common AI/ML job titles should MATCH a GenAI Engineer query."""
    status, score, reason = match_title_relevance("GenAI Engineer", job_title)
    assert status == expected, f"Expected {expected} for '{job_title}', got {status} ({reason})"


def test_llm_developer_matches_genai():
    status, score, reason = match_title_relevance("GenAI Engineer", "LLM Developer")
    assert status == MatchStatus.MATCH
    assert score >= 0.80


def test_nlp_researcher_matches_genai():
    status, score, reason = match_title_relevance("GenAI Engineer", "NLP Researcher")
    assert status == MatchStatus.MATCH


# ─── 2. Location fuzzy match ──────────────────────────────────────────────────

@pytest.mark.parametrize("actual_loc", [
    "Gulf Region",
    "GCC Countries",
    "Middle East",
    "Middle East and North Africa",
    "Gulf",
    "Dubai, UAE",
    "Riyadh",
    "Saudi Arabia",
    "KSA",
    "Abu Dhabi",
    "Emirates",
])
def test_gcc_locations_match_saudi_or_uae(actual_loc):
    """All GCC/MENA region strings should MATCH when user asks for Saudi or UAE."""
    status, reason = match_location("Saudi or UAE", actual_loc)
    assert status == MatchStatus.MATCH, f"Expected MATCH for '{actual_loc}', got {status} ({reason})"


def test_qatar_does_not_match_uae_only():
    """Doha/Qatar should be a MISMATCH for UAE-only request (separate GCC country)."""
    status, reason = match_location("UAE", "Doha, Qatar")
    assert status == MatchStatus.MISMATCH


def test_qatar_matches_gcc_request():
    """Doha/Qatar SHOULD match when user asks for GCC."""
    status, reason = match_location("GCC", "Doha, Qatar")
    assert status == MatchStatus.MATCH, f"Qatar should match GCC request: {reason}"



def test_us_location_mismatches_uae():
    status, reason = match_location("UAE", "New York, USA")
    assert status == MatchStatus.MISMATCH


def test_unknown_location_not_gcc():
    """A non-GCC unrecognised location should still return UNKNOWN (not MATCH)."""
    status, reason = match_location("UAE", "Somewhere Unknown City")
    assert status in (MatchStatus.UNKNOWN, MatchStatus.MISMATCH)


# ─── 3. unverified_location gate ─────────────────────────────────────────────

def _make_job_req(locations):
    """Minimal job request stub."""
    return types.SimpleNamespace(
        job_title="GenAI Engineer",
        titles=["GenAI Engineer"],
        locations=locations,
        location=" or ".join(locations),
        experience_min=0,
        experience_max=6,
        explicit_skills=None,
        inferred_skills=[],
        skills=[],
        salary_min=None,
        salary_currency=None,
        remote_allowed=True,
        remote_status=None,
        alternative_titles=[],
        excluded_titles=[],
        excluded_companies=[],
        raw_query="Find GenAI Engineer jobs in Saudi or UAE with 0-6 years experience",
    )


def _make_job_record(title, location, exp_min=0, exp_max=3):
    return {
        "title": title,
        "company": "ACME Corp",
        "location": location,
        "experience_min": exp_min,
        "experience_max": exp_max,
        "remote": False,
        "remote_status": "onsite",
        "is_active": True,
    }


def test_gulf_region_job_not_rejected_as_unverified():
    """A job saying 'Gulf Region' should NOT be rejected as unverified_location."""
    policy = QualificationPolicy()
    req = _make_job_req(["Saudi Arabia", "UAE"])
    job = _make_job_record("AI Engineer", "Gulf Region")
    result = policy.qualify(job, req)
    assert "unverified_location:Gulf Region" not in result.rejection_reasons, (
        f"Gulf Region was wrongly rejected as unverified. Reasons: {result.rejection_reasons}"
    )


def test_middle_east_job_not_rejected_as_unverified():
    """A job saying 'Middle East' should NOT be rejected as unverified_location."""
    policy = QualificationPolicy()
    req = _make_job_req(["Saudi Arabia", "UAE"])
    job = _make_job_record("LLM Engineer", "Middle East")
    result = policy.qualify(job, req)
    rejection_loc_reasons = [r for r in result.rejection_reasons if "unverified_location" in r]
    assert not rejection_loc_reasons, f"Middle East wrongly flagged: {result.rejection_reasons}"


def test_india_location_rejected_for_uae_request():
    """A job in India should be rejected for a UAE request."""
    policy = QualificationPolicy()
    req = _make_job_req(["UAE"])
    job = _make_job_record("AI Engineer", "Bangalore, India")
    result = policy.qualify(job, req)
    loc_rej = [r for r in result.rejection_reasons if "location" in r.lower()]
    assert loc_rej, f"India should have been rejected for UAE request. Got: {result.rejection_reasons}"


# ─── 4. Decision engine low-yield counter ────────────────────────────────────

def test_low_yield_limit_is_at_least_3():
    """CONSECUTIVE_LOW_YIELD_LIMIT should be at least 3 to avoid premature stopping."""
    assert CONSECUTIVE_LOW_YIELD_LIMIT >= 3, (
        f"Low yield limit is {CONSECUTIVE_LOW_YIELD_LIMIT}, too aggressive — should be >= 3"
    )


def test_qualified_new_resets_low_yield_counter():
    """When qualified_new > 0, the low-yield counter must be reset to 0."""
    engine = DecisionEngine()
    state = AgentState(
        request="Find GenAI jobs in UAE",
        run_id="run_test",
        task_id="task_test",
        mode="jobs",
        target_results=10,
        max_iterations=20,
        max_search_calls=20,
    )
    # Simulate 3 consecutive zero-candidate iterations to build up counter
    for _ in range(3):
        engine.record_search_iteration(state, "q", 5, 0, qualified_new=0)
    before = state.consecutive_low_yield_iterations
    assert before > 0, "Counter should have incremented"

    # Now an iteration with qualified_new=2 should reset it
    engine.record_search_iteration(state, "q2", 3, 2, qualified_new=2)
    assert state.consecutive_low_yield_iterations == 0, (
        f"Counter should reset to 0 when qualified_new>0, got {state.consecutive_low_yield_iterations}"
    )
