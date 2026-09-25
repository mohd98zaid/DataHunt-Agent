import pytest
from unittest.mock import MagicMock
from datahunt.models import SourceDocument, ResearchSpec, RunBudget
from datahunt.models.intent import ResearchIntent, ResearchOutputType
from datahunt.agents.intent_router import IntentRouter
from datahunt.llm.gemini_client import GeminiClient, synthesize_direct_answer
from datahunt.tools.extract import ExtractTool, extract_answer_knowledge_records
from agent.orchestrator import ResearchOrchestrator


def test_space_travel_intent_and_query_planning():
    """Verify 'explain me how to travel to space' routes to HOW_TO/EXPLANATION and generates targeted queries."""
    client = GeminiClient()
    client.is_live = False
    spec = client.normalize_request("explain me how to travel to space", {"agent_mode": "auto"})

    # 1. Intent routing verification
    assert spec.intent_spec is not None
    assert spec.intent_spec.intent in (ResearchIntent.HOW_TO, ResearchIntent.EXPLANATION)
    assert spec.intent_spec.requested_output == ResearchOutputType.ANSWER
    assert spec.intent_spec.intent != ResearchIntent.JOB_SEARCH
    assert spec.agent_mode == "research"

    # 2. Plan research verification (must NOT generate ATS job queries)
    budget = RunBudget(max_search_queries=10, max_pages=10, max_output_records=10)
    plan = client.plan_research(spec, budget)
    queries = [q["query"] if isinstance(q, dict) else str(q) for q in plan.get("queries", [])]

    # Verify no ATS / career sites in queries
    for q in queries:
        q_low = q.lower()
        assert "greenhouse.io" not in q_low
        assert "lever.co" not in q_low
        assert "ashbyhq.com" not in q_low
        assert "workable.com" not in q_low
        assert "site:linkedin.com/jobs" not in q_low
        assert "site:indeed.com" not in q_low


def test_space_travel_extraction_filters_metadata_and_extracts_facts():
    """Verify that extraction strips metadata noise and extracts structured answer facts."""
    doc_text = """
# STRUCTURED PAGE METADATA
Author: Editorial Staff
Last Indexed: 2026-09-01
Category: Space Exploration

# Commercial Spaceflight Options
Currently there are multiple ways for civilians to travel to space. Suborbital spaceflights provide several minutes of weightlessness at altitudes above 80 km.
- Flights are offered by Blue Origin and Virgin Galactic
- Tickets cost between $450,000 and $500,000
- Training takes 2-3 days before launch

# Orbital Missions to the ISS
For longer journeys, private orbital spaceflights take private astronauts to low Earth orbit and the International Space Station.
- Missions utilize the SpaceX Crew Dragon spacecraft
- Passengers spend 8 to 14 days aboard the ISS
- Rigorous medical checks and 6-12 months of training are required

# END PAGE METADATA
Page 1 of 5
Cookie Policy and Terms of Use
"""
    doc = SourceDocument(
        id="doc_space_test",
        run_id="run_test_space",
        requested_url="https://example.com/space-travel-guide",
        canonical_url="https://example.com/space-travel-guide",
        title="Comprehensive Space Travel Guide",
        domain="example.com",
        extracted_text=doc_text,
    )

    client = GeminiClient()
    client.is_live = False
    spec = client.normalize_request("explain me how to travel to space", {"agent_mode": "auto"})

    extract_tool = ExtractTool(gemini_client=client)
    res = extract_tool.execute(doc, spec, run_id="run_test_space")

    assert res.success is True
    records = res.data
    assert len(records) > 0

    extracted_titles = [
        r.fields.get("section_title") or r.fields.get("name") or r.fields.get("title")
        for r in records
    ]

    # Crucial: Metadata tags must NOT be extracted as entities!
    for t in extracted_titles:
        assert t is not None
        assert "STRUCTURED PAGE METADATA" not in t.upper()
        assert "END PAGE METADATA" not in t.upper()
        assert "COOKIE" not in t.upper()

    # Confirms real sections were extracted
    assert any("Commercial Spaceflight" in t for t in extracted_titles)


def test_space_travel_synthesis_direct_answer():
    """Verify synthesis produces a direct, authoritative answer rather than career intelligence."""
    records = [
        {
            "section_title": "Commercial Spaceflight Options",
            "summary": "Suborbital flights reach altitudes over 80 km providing several minutes of microgravity.",
            "key_points": ["Blue Origin New Shepard", "Virgin Galactic", "Cost: $450k+"],
            "source_url": "https://example.com/suborbital"
        },
        {
            "section_title": "Orbital Missions to ISS",
            "summary": "Private missions to the International Space Station aboard SpaceX Crew Dragon.",
            "key_points": ["Axiom Space missions", "Multi-day stays in LEO", "Comprehensive training required"],
            "source_url": "https://example.com/orbital"
        }
    ]

    answer = synthesize_direct_answer(
        query="explain me how to travel to space",
        run_metadata={"pages_fetched": 3},
        verified_records=records,
        source_texts="Source URL: https://example.com/space\nCommercial space travel guide..."
    )

    # Must contain direct explanation
    assert "# 🧭 Research Intelligence: explain me how to travel to space" in answer
    assert "Executive Answer & Overview" in answer
    assert "Commercial Suborbital" in answer
    assert "Orbital" in answer
    assert "SpaceX" in answer
    assert "Blue Origin" in answer
    assert "Verified Sources" in answer

    # Must NOT contain career intelligence artifacts
    assert "Career Radar Intelligence" not in answer
    assert "Verified Job Opportunities" not in answer
    assert "Execution Protocols & Methods" not in answer


def test_how_do_i_become_ai_engineer_not_job_search():
    """Verify 'how do I become an AI engineer' does not trigger job search pipeline."""
    client = GeminiClient()
    client.is_live = False
    spec = client.normalize_request("how do I become an AI engineer", {"agent_mode": "auto"})

    assert spec.intent_spec.intent == ResearchIntent.HOW_TO
    assert spec.intent_spec.requested_output == ResearchOutputType.ANSWER
    assert spec.agent_mode == "research"


def test_find_python_jobs_preserves_job_pipeline():
    """Verify job search queries continue to route to JOB_SEARCH and generate ATS queries."""
    client = GeminiClient()
    client.is_live = False
    spec = client.normalize_request("find senior python engineer jobs in London", {"agent_mode": "auto"})

    assert spec.intent_spec.intent == ResearchIntent.JOB_SEARCH
    assert spec.intent_spec.requested_output == ResearchOutputType.JOB_RESULTS
    assert spec.agent_mode == "jobs"

    budget = RunBudget(max_search_queries=15, max_pages=15, max_output_records=10)
    plan = client.plan_research(spec, budget)
    queries = [q["query"] if isinstance(q, dict) else str(q) for q in plan.get("queries", [])]

    # Verify ATS query generation is preserved
    ats_present = any("greenhouse.io" in q or "lever.co" in q or "ashbyhq.com" in q for q in queries)
    assert ats_present is True
