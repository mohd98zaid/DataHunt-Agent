"""
DataHunt-Agent Precision Hardening Verification Suite.

Tests the precision hardening pass guarantees:
1. Multi-location OR semantics & hierarchy (Saudi Arabia OR UAE; Dubai OR Abu Dhabi).
2. Explicit vs. Inferred skills:
   - Explicit skill mismatch -> Hard disqualification (is_qualified = False).
   - Inferred skill mismatch -> Ranking score penalty only (never hard gates).
3. Hard filter permanent elimination: disqualified jobs cannot be rescued by ranking.
4. Multi-source deduplication across ATS ID, URL, apply URL, and company+title+location fingerprint.
5. Company research zero fabrication (unavailable status, empty tech stack on failure).
6. Market entity banning (TradingView, IndiaTimes, Stockezee, Hmatrading, etc.).
7. Authoritative NSE symbol universe enforcement (random strings rejected).
8. Market current price vs. target price separation.
9. Golden end-to-end integration tests for Job, Research, and Market agents.
"""
import pytest
from unittest.mock import MagicMock
from typing import Dict, Any, List

from datahunt.agent.policies import (
    qualify_job, match_location, MatchStatus, QualificationResult
)
from datahunt.agents.query_understanding import QueryUnderstandingAgent, JobSearchRequest
from datahunt.agents.hard_filter import HardFilterAgent
from datahunt.tools.dedupe import DedupeTool
from datahunt.agents.company_research import CompanyResearchAgent
from datahunt.agents.market_validator import (
    MarketValidator, AUTHORITATIVE_NSE_SYMBOLS, BANNED_ENTITIES
)
from datahunt.models.market import (
    MarketCandidate, MarketEvidence, SourceTier, FreshnessCategory, MarketEntityType
)
from datahunt.models.record import ExtractedRecord, VerificationStatus


# =============================================================================
# 1. AUTHORITATIVE LOCATION OR SEMANTICS & HIERARCHY
# =============================================================================
def test_location_or_semantics_gulf_countries():
    # User query specifies: Saudi Arabia OR UAE
    status_riyadh, loc_riyadh = match_location(
        ["Saudi Arabia", "UAE"], "Riyadh, Saudi Arabia"
    )
    assert status_riyadh == MatchStatus.MATCH
    assert "Saudi Arabia" in loc_riyadh

    status_dubai, loc_dubai = match_location(
        ["Saudi Arabia", "UAE"], "Dubai, United Arab Emirates"
    )
    assert status_dubai == MatchStatus.MATCH
    assert "UAE" in loc_dubai or "United Arab Emirates" in loc_dubai

    status_ad, loc_ad = match_location(
        ["Saudi Arabia", "UAE"], "Abu Dhabi"
    )
    assert status_ad == MatchStatus.MATCH

    # Rejection of out-of-scope locations
    status_london, _ = match_location(
        ["Saudi Arabia", "UAE"], "London, United Kingdom"
    )
    assert status_london == MatchStatus.MISMATCH

    status_ny, _ = match_location(
        ["Saudi Arabia", "UAE"], "New York, NY"
    )
    assert status_ny == MatchStatus.MISMATCH


def test_location_or_semantics_city_level():
    status_dubai, _ = match_location(
        ["Dubai", "Abu Dhabi"], "Dubai, UAE"
    )
    assert status_dubai == MatchStatus.MATCH

    status_ad, _ = match_location(
        ["Dubai", "Abu Dhabi"], "Abu Dhabi"
    )
    assert status_ad == MatchStatus.MATCH

    status_doha, _ = match_location(
        ["Dubai", "Abu Dhabi"], "Doha, Qatar"
    )
    assert status_doha == MatchStatus.MISMATCH


# =============================================================================
# 2. EXPLICIT VS INFERRED SKILLS SEPARATION
# =============================================================================
def test_explicit_skill_mismatch_hard_disqualifies():
    # Job requires explicit skill: Rust
    rec = {
        "title": "Backend Engineer",
        "location": "Riyadh, Saudi Arabia",
        "skills": ["Python", "Django", "PostgreSQL"],
        "snippet": "We are seeking a Python backend engineer for our API services."
    }

    req = JobSearchRequest(
        job_title="Backend Engineer",
        locations=["Saudi Arabia"],
        location="Saudi Arabia",
        explicit_skills=["Rust"],
        skills=["Rust"],
        inferred_skills=["FastAPI"],
    )

    # Explicit skill mismatch must cause qualified = False
    res = qualify_job(rec, req)

    assert res.qualified is False
    assert any("missing required explicit skills" in r.lower() or "disqualified" in r.lower() for r in res.reasons)


def test_inferred_skill_mismatch_only_penalizes_ranking():
    # Job requires explicit skill: Python. Inferred skill: Kubernetes.
    rec = {
        "title": "Backend Engineer",
        "location": "Riyadh, Saudi Arabia",
        "skills": ["Python", "PostgreSQL"],
        "snippet": "Backend developer with strong Python experience."
    }

    req = JobSearchRequest(
        job_title="Backend Engineer",
        locations=["Saudi Arabia"],
        location="Saudi Arabia",
        explicit_skills=["Python"],
        skills=["Python"],
        inferred_skills=["Kubernetes"],
    )

    # Explicit skill matches, inferred skill is missing
    res_missing_inf = qualify_job(rec, req)

    # Must NOT be disqualified because only an inferred skill is missing!
    assert res_missing_inf.qualified is True

    rec_with_all = {
        "title": "Backend Engineer",
        "location": "Riyadh, Saudi Arabia",
        "skills": ["Python", "PostgreSQL", "Kubernetes"],
        "snippet": "Backend developer with strong Python and Kubernetes experience."
    }
    res_both = qualify_job(rec_with_all, req)

    # Job having the inferred skill gets higher relevance score
    assert res_both.score > res_missing_inf.score


# =============================================================================
# 3. HARD FILTER PERMANENT ELIMINATION
# =============================================================================
def test_hard_filter_eliminates_candidates_permanently():
    hard_filter = HardFilterAgent()

    records = [
        # Qualified job
        {
            "id": "job_1",
            "title": "GenAI Engineer",
            "company": "Tech Corp",
            "location": "Riyadh, Saudi Arabia",
            "skills": ["Python", "PyTorch"],
            "remote": False,
        },
        # Disqualified by location
        {
            "id": "job_2",
            "title": "GenAI Engineer",
            "company": "Tech Corp",
            "location": "Berlin, Germany",
            "skills": ["Python", "PyTorch"],
            "remote": False,
        },
        # Disqualified by explicit skill
        {
            "id": "job_3",
            "title": "GenAI Engineer",
            "company": "Tech Corp",
            "location": "Dubai, UAE",
            "skills": ["Java", "Spring"],
            "remote": False,
        },
    ]

    req = JobSearchRequest(
        job_title="GenAI Engineer",
        locations=["Saudi Arabia", "UAE"],
        location="Saudi Arabia",
        remote_allowed=False,
        remote_status="onsite",
        explicit_skills=["Python"],
        skills=["Python"],
    )

    passed, rejected = hard_filter.apply(records, req)

    passed_ids = [r["id"] for r in passed]
    rejected_ids = [r[0]["id"] for r in rejected]

    assert "job_1" in passed_ids
    assert "job_2" in rejected_ids
    assert "job_3" in rejected_ids
    assert len(passed) == 1


# =============================================================================
# 4. MULTI-TIER JOB DEDUPLICATION
# =============================================================================
def test_multi_source_job_deduplication():
    dedupe_tool = DedupeTool()

    # Two postings for the same job from different sources: LinkedIn vs Greenhouse
    records = [
        ExtractedRecord(
            id="rec_li_1",
            run_id="run_test",
            record_type="job",
            identity_key="rec_li_1",
            fields={
                "title": "Senior AI Engineer",
                "company": "Acme AI",
                "location": "Dubai, UAE",
                "source_url": "https://linkedin.com/jobs/view/12345678",
                "application_url": "https://boards.greenhouse.io/acmeai/jobs/987654",
                "description": "Building state-of-the-art LLM pipelines.",
            },
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.85,
        ),
        ExtractedRecord(
            id="rec_gh_1",
            run_id="run_test",
            record_type="job",
            identity_key="rec_gh_1",
            fields={
                "title": "Senior AI Engineer",
                "company": "Acme AI",
                "location": "Dubai, United Arab Emirates",
                "source_url": "https://boards.greenhouse.io/acmeai/jobs/987654",
                "application_url": "https://boards.greenhouse.io/acmeai/jobs/987654",
                "description": "Join Acme AI to develop LLM architectures.",
            },
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.92,
        ),
    ]

    result = dedupe_tool.execute(records=records)

    # Must collapse into exactly 1 canonical record!
    assert result.success is True
    unique_records = result.data["unique_records"]
    assert len(unique_records) == 1
    canonical = unique_records[0]
    # Greenhouse application url and multi-sources tracked
    assert "boards.greenhouse.io" in str(canonical.fields.get("primary_application_url") or canonical.fields.get("application_url", ""))
    all_sources = canonical.fields.get("all_sources") or canonical.fields.get("sources", [])
    assert any("linkedin" in s.lower() for s in all_sources)
    assert any("greenhouse" in s.lower() for s in all_sources)


# =============================================================================
# 5. COMPANY RESEARCH ZERO FABRICATION
# =============================================================================
def test_company_research_zero_fabrication_on_failure():
    # Client that fails/raises
    failing_client = MagicMock()
    failing_client.generate_structured_json.side_effect = RuntimeError("API unavailable")

    search_tool = MagicMock()
    search_tool.execute.return_value = MagicMock(success=False, data=[])

    agent = CompanyResearchAgent(search_tool=search_tool, gemini_client=failing_client)
    profile = agent.research("Nonexistent Corp XYZ", force_refresh=True)

    # MUST be unavailable status, is_available = False, tech_stack = empty
    assert profile.status == "unavailable"
    assert profile.is_available is False
    assert profile.tech_stack == []
    assert profile.culture_notes == "Information not publicly available in analyzed sources"
    assert profile.interview_process == "Interview stages not disclosed in public disclosures"


# =============================================================================
# 6. MARKET ENTITY BANNING
# =============================================================================
def test_market_entity_banning_publishers_and_tools():
    validator = MarketValidator()

    # Banned entities
    banned_tests = [
        "TradingView",
        "Indiatimes",
        "Businessworld",
        "Moneycontrol",
        "Stockezee",
        "Hmatrading",
        "Economic Times",
        "Mint",
        "Screener",
        "Zerodha",
        "Groww",
        "Angel One",
    ]

    for banned in banned_tests:
        assert validator.is_banned_or_source(banned) is True
        sym, name = validator.normalize_symbol(banned, banned)
        assert sym is None, f"{banned} should be rejected as a stock ticker"


# =============================================================================
# 7. AUTHORITATIVE NSE SYMBOL UNIVERSE VALIDATION
# =============================================================================
def test_authoritative_nse_symbol_validation():
    validator = MarketValidator()

    # Valid authoritative NSE equities
    for valid_sym in ("RELIANCE", "TCS", "INFY", "TATAPOWER", "HDFCBANK", "ICICIBANK", "BEL", "HAL", "ZOMATO"):
        sym, name = validator.normalize_symbol(valid_sym, valid_sym, exchange="NSE")
        assert sym == valid_sym
        assert name is not None

    # Common company names mapped to canonical NSE symbols
    sym, name = validator.normalize_symbol("", "Tata Power Company", exchange="NSE")
    assert sym == "TATAPOWER"

    sym, name = validator.normalize_symbol("", "Reliance Industries", exchange="NSE")
    assert sym == "RELIANCE"

    # Arbitrary strings that are NOT in the authoritative universe MUST be rejected
    for fake in ("STOCKZONE", "GROWW", "BUYNOW", "HOTSTOCKS", "INDIA", "MARKET", "TECH"):
        sym, name = validator.normalize_symbol(fake, fake, exchange="NSE")
        assert sym is None, f"{fake} must NOT be accepted as an authoritative NSE equity"


# =============================================================================
# 8. MARKET PRICE VS TARGET PRICE SEPARATION
# =============================================================================
def test_market_price_vs_target_price_separation():
    from datahunt.agent.market_discovery import MarketDiscoveryEngine

    cand = MarketCandidate(symbol="TATAPOWER", company_name="Tata Power", exchange="NSE")
    engine = MarketDiscoveryEngine(search_tool=MagicMock(), fetch_tool=MagicMock())

    # Mock search returning target price and CMP
    mock_search = MagicMock()
    mock_search.execute.return_value = MagicMock(
        success=True,
        data=[
            {
                "url": "https://nseindia.com/quote/TATAPOWER",
                "title": "Tata Power share price analysis",
                "snippet": "Tata Power trading at ₹412.50. Brokerage sets target price: Rs 520.00 for the quarter.",
            }
        ]
    )
    engine.search_tool = mock_search

    from datahunt.models.market import MarketIntentSpec, MarketCoverage
    engine._wave4_deep_research(
        candidates=[cand],
        intent=MarketIntentSpec(raw_query="Tata Power", market="NSE"),
        coverage=MarketCoverage(),
        deadline=10000000000.0,
        emit=lambda t, d: None,
    )

    # Current price must be 412.50 (CMP), NOT 520.00 (target price)
    assert cand.current_price == 412.50
    # 520.00 must be in analyst views
    assert any(v.get("target_price") == 520.00 for v in cand.analyst_views)


# =============================================================================
# 9. GOLDEN RUN: JOB AGENT END-TO-END QUALIFICATION
# =============================================================================
def test_golden_job_agent_qualification_saudi_uae():
    parser = QueryUnderstandingAgent(gemini_client=None)
    req = parser.understand("Find GenAI Engineer jobs in Saudi Arabia or UAE requiring Python")

    assert "Saudi Arabia" in req.locations or "UAE" in req.locations
    assert req.location_operator == "OR"
    assert "Python" in req.explicit_skills

    # Simulate realistic jobs found by crawler
    jobs = [
        # Job 1: In Riyadh with Python -> Qualified
        {
            "id": "j1",
            "title": "GenAI Systems Engineer",
            "company": "Aramco Digital",
            "location": "Riyadh, Saudi Arabia",
            "skills": ["Python", "LangChain", "FastAPI"],
            "remote": False,
        },
        # Job 2: In Dubai with Python -> Qualified
        {
            "id": "j2",
            "title": "Generative AI Engineer",
            "company": "Emirates AI Lab",
            "location": "Dubai, UAE",
            "skills": ["Python", "PyTorch"],
            "remote": False,
        },
        # Job 3: In London -> Disqualified
        {
            "id": "j3",
            "title": "GenAI Engineer",
            "company": "London Tech",
            "location": "London, UK",
            "skills": ["Python"],
            "remote": False,
        },
        # Job 4: In Riyadh but Java only -> Disqualified
        {
            "id": "j4",
            "title": "GenAI Engineer",
            "company": "Desert Cloud",
            "location": "Riyadh, Saudi Arabia",
            "skills": ["Java", "Spring"],
            "remote": False,
        },
    ]

    hard_filter = HardFilterAgent()
    passed, rejected = hard_filter.apply(jobs, req)

    passed_ids = [j["id"] for j in passed]
    assert "j1" in passed_ids
    assert "j2" in passed_ids
    assert "j3" not in passed_ids
    assert "j4" not in passed_ids
