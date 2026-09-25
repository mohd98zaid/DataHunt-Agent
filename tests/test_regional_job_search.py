import pytest
from datahunt.llm.gemini_client import GeminiClient
from datahunt.agents.query_understanding import QueryUnderstandingAgent
from datahunt.agents.query_expansion import QueryExpansionAgent
from datahunt.agents.search_planner import SearchPlannerAgent
from datahunt.tools.search import generate_ats_queries, generate_broad_job_queries, is_valid_job_url
from datahunt.tools.extract import extract_job_records_from_document


def test_regional_job_spec_normalization():
    client = GeminiClient()
    spec = client.normalize_request("Find GenAI Engineer jobs in Saudi or UAE with 0-5 years experience")
    assert spec.agent_mode == "jobs"
    assert "title" in spec.requested_fields
    assert "company" in spec.requested_fields
    assert "location" in spec.requested_fields
    assert "application_url" in spec.requested_fields
    assert spec.geography is not None
    assert spec.geography.name in ("Saudi Arabia", "UAE")


def test_experience_clause_stripping_and_regional_queries():
    query = "Find GenAI Engineer jobs in Saudi or UAE with 0-5 years experience"
    ats_queries = generate_ats_queries(query)
    
    # Verify experience string is not polluting queries
    for q in ats_queries:
        assert "0-5 years" not in q
        assert "with 0-5" not in q

    # Verify regional queries are included at the top for Gulf
    assert any("gulftalent.com" in q for q in ats_queries)
    assert any("bayt.com" in q for q in ats_queries)
    assert any("naukrigulf.com" in q for q in ats_queries)


def test_search_planner_prioritizes_regional_boards():
    qu = QueryUnderstandingAgent()
    qe = QueryExpansionAgent()
    sp = SearchPlannerAgent()

    req = qu.understand("Find GenAI Engineer jobs in Saudi or UAE with 0-5 years experience")
    expanded = qe.expand(req)
    tasks = sp.plan(req, expanded)

    # Priority 1 tasks should contain regional boards
    p1_queries = [t.query for t in tasks if t.priority == 1]
    assert any("gulftalent.com" in q for q in p1_queries)
    assert any("bayt.com" in q for q in p1_queries)


def test_multi_job_markdown_extraction():
    sample_board_doc = """# Canonical — Open Positions

## GenAI Research Engineer
Location: Riyadh, Saudi Arabia
Department: Artificial Intelligence
Posted: 2026-09-08T10:00:00Z
Apply: https://boards.greenhouse.io/canonical/jobs/998877

## Senior Cloud Engineer
Location: London, UK
Department: Cloud
Posted: 2026-09-07T12:00:00Z
Apply: https://boards.greenhouse.io/canonical/jobs/112233
"""
    doc_meta = {
        "id": "doc_canonical_test",
        "url": "https://boards.greenhouse.io/canonical",
        "title": "Canonical Careers",
        "domain": "boards.greenhouse.io"
    }

    records = extract_job_records_from_document(
        sample_board_doc,
        doc_meta,
        topic="GenAI Engineer in Saudi Arabia"
    )

    assert len(records) >= 1
    riyadh_job = next((r for r in records if "GenAI" in r["title"]), None)
    assert riyadh_job is not None
    assert riyadh_job["company"] == "Canonical"
    assert "Riyadh" in riyadh_job["location"]
    assert riyadh_job["application_url"] == "https://boards.greenhouse.io/canonical/jobs/998877"
