import json
from datetime import datetime, timezone, timedelta
import pytest

from datahunt.tools.search import generate_ats_queries, SearchTool, MockSearchProvider, SearchHit
from datahunt.tools.extract import parse_job_timestamp, extract_jsonld_job_posting, ExtractTool
from datahunt.models import SourceDocument, ResearchSpec

def test_generate_ats_queries():
    queries = generate_ats_queries("find senior AI engineer jobs in dubai 0sec")
    assert len(queries) >= 4
    assert any("site:greenhouse.io" in q for q in queries)
    assert any("site:jobs.lever.co" in q for q in queries)
    assert any("site:jobs.ashbyhq.com" in q for q in queries)
    assert any("site:apply.workable.com" in q for q in queries)

def test_parse_job_timestamp_zero_sec():
    base = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    
    # 0 sec patterns
    for text in ["0 sec ago", "0 seconds ago", "0s ago", "just now", "just posted", "moments ago", "new"]:
        iso, age, badge = parse_job_timestamp(text, base_time=base)
        assert age == 0, f"Expected 0 for '{text}', got {age}"
        assert "0-SEC" in badge or "JUST NOW" in badge

def test_parse_job_timestamp_relative_intervals():
    base = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

    # 15 seconds
    iso, age, badge = parse_job_timestamp("15 sec ago", base_time=base)
    assert age == 15
    assert "15s ago" in badge

    # 10 minutes
    iso, age, badge = parse_job_timestamp("10 minutes ago", base_time=base)
    assert age == 600
    assert "10m ago" in badge

    # 3 hours
    iso, age, badge = parse_job_timestamp("3 hours ago", base_time=base)
    assert age == 10800
    assert "3h ago" in badge

    # Today
    iso, age, badge = parse_job_timestamp("today", base_time=base)
    assert age == 3600
    assert "Today" in badge

def test_parse_job_timestamp_iso():
    base = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    iso_str = "2026-09-08T11:58:00Z"
    iso, age, badge = parse_job_timestamp(iso_str, base_time=base)
    assert age == 120
    assert "2m ago" in badge

def test_extract_jsonld_job_posting():
    mock_jsonld = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "Principal Generative AI Engineer",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Anthropic AI Labs"
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "San Francisco",
                "addressRegion": "CA",
                "addressCountry": "USA"
            }
        },
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {
                "minValue": 280000,
                "maxValue": 350000,
                "unitText": "YEAR"
            }
        },
        "datePosted": "2026-09-08T11:59:30Z",
        "url": "https://jobs.lever.co/anthropic/genai-lead"
    }

    doc_text = f"--- STRUCTURED JOB SCHEMA (JSON-LD) ---\n{json.dumps(mock_jsonld)}\n--- END STRUCTURED SCHEMA ---\nJob Description and overview..."
    metadata = {"title": "Careers at Anthropic", "domain": "jobs.lever.co", "url": "https://jobs.lever.co/anthropic/genai-lead"}

    records = extract_jsonld_job_posting(doc_text, metadata)
    assert len(records) == 1
    rec = records[0]
    f = rec["fields"]
    assert f["title"] == "Principal Generative AI Engineer"
    assert f["company"] == "Anthropic AI Labs"
    assert "San Francisco" in f["location"]
    assert "280000" in f["salary"]
    assert f["application_url"] == "https://jobs.lever.co/anthropic/genai-lead"
    assert rec["record_confidence"] == 1.0

def test_search_tool_with_freshness():
    mock_hit = SearchHit(
        url="https://job-boards.greenhouse.io/test/job/123",
        title="Staff AI Engineer",
        snippet="Hiring immediately. Posted 0 sec ago.",
        source_domain="job-boards.greenhouse.io"
    )
    provider = MockSearchProvider(predefined_hits=[mock_hit])
    tool = SearchTool(provider=provider)
    result = tool.execute("AI engineer", limit=5, freshness_days=1)
    assert result.success is True
    assert len(result.data) == 1
    assert result.data[0]["title"] == "Staff AI Engineer"

def test_is_substantive_record():
    from datahunt.tools.extract import is_substantive_record

    # Refusal container with all nulls
    null_container = {
        "title": None,
        "company": None,
        "location": None,
        "reasons": ["All requested non-null fields require evidence; since no matching job records exist, all fields are correctly set to null."],
        "checks": ["Since no matching records exist, all fields are null."]
    }
    assert is_substantive_record(null_container) is False

    # Empty dictionary
    assert is_substantive_record({}) is False

    # Valid job record
    assert is_substantive_record({"title": "Staff AI Engineer", "company": "OpenAI"}) is True

    # Valid general knowledge entity
    assert is_substantive_record({"name": "LangChain", "category": "LLM Orchestration Framework"}) is True

def test_general_vs_job_intent_spec():
    from datahunt.llm.gemini_client import GeminiClient
    client = GeminiClient(api_key="")

    # General technical research query
    general_spec = client.normalize_request("what is langchain")
    assert "name" in general_spec.requested_fields
    assert "category" in general_spec.requested_fields
    assert "title" not in general_spec.requested_fields

    # Job search query
    job_spec = client.normalize_request("find senior python developer jobs in london")
    assert "title" in job_spec.requested_fields
    assert "company" in job_spec.requested_fields

def test_summarize_run_research_dossier():
    from datahunt.llm.gemini_client import GeminiClient
    client = GeminiClient(api_key="")

    run_meta = {"pages_fetched": 3, "records_verified": 0}
    summary = client.summarize_run(
        run_metadata=run_meta,
        verified_records=[],
        source_texts="Source URL: https://python.langchain.com\nLangChain is a framework for developing applications powered by language models.",
        query_text="what is langchain"
    )
    assert "Research Dossier" in summary or "LangChain" in summary or "3" in summary

def test_agent_modes_spec():
    from datahunt.llm.gemini_client import GeminiClient
    client = GeminiClient(api_key="")

    # Research mode
    spec_research = client.normalize_request("AI tools", operator_defaults={"agent_mode": "research"})
    assert "name" in spec_research.requested_fields
    assert "core_capabilities" in spec_research.requested_fields
    assert "title" not in spec_research.requested_fields

    # Jobs mode
    spec_jobs = client.normalize_request("AI tools", operator_defaults={"agent_mode": "jobs"})
    assert "title" in spec_jobs.requested_fields
    assert "company" in spec_jobs.requested_fields

    # Market mode
    spec_market = client.normalize_request("AI tools", operator_defaults={"agent_mode": "market"})
    assert "company_name" in spec_market.requested_fields
    assert "pricing_model" in spec_market.requested_fields

def test_no_example_corp_fallback_for_documentation():
    from datahunt.llm.gemini_client import GeminiClient
    client = GeminiClient(api_key="")

    spec = client.normalize_request("what is langchain", operator_defaults={"agent_mode": "research"})
    
    # Wikipedia document text
    doc_text = "Indonesia is an island country in Southeast Asia and Oceania between the Indian and Pacific oceans."
    metadata = {"title": "Indonesia - Wikipedia", "url": "https://en.wikipedia.org/wiki/Indonesia"}

    res = client.extract_from_document(spec, doc_text, metadata, {})
    records = res.get("records", [])
    
    # Must NOT produce any fake Example Corp job records
    assert len(records) == 0
    for r in records:
        assert r.get("fields", {}).get("company") != "Example Corp"


def test_parse_job_timestamp_weeks_months_years():
    base = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
    
    # 2 weeks ago
    iso, age, badge = parse_job_timestamp("2 weeks ago", base_time=base)
    assert age == 14 * 86400
    assert "2w ago" in badge

    # 3 months ago
    iso, age, badge = parse_job_timestamp("3 months ago", base_time=base)
    assert age == 90 * 86400
    assert "3mo ago" in badge

    # 1 year ago
    iso, age, badge = parse_job_timestamp("1 year ago", base_time=base)
    assert age == 365 * 86400
    assert "1y ago" in badge


def test_qualify_job_rejects_stale_jobs():
    from datahunt.agent.policies import QualificationPolicy
    from datahunt.agents import JobSearchRequest

    policy = QualificationPolicy()
    req = JobSearchRequest(job_title="GenAI Engineer", target_locations=["Dubai"], freshness_days=30)

    # 1 year old job
    stale_job = {
        "title": "GenAI Engineer",
        "company": "NeuralCorp",
        "location": "Dubai",
        "posted_age_seconds": 365 * 86400,
        "posted_at": "1 year ago"
    }
    res = policy.qualify(stale_job, req)
    assert res.qualified is False
    assert any("stale_job" in r for r in res.rejection_reasons)

    # 5 days old job
    fresh_job = {
        "title": "GenAI Engineer",
        "company": "NeuralCorp",
        "location": "Dubai",
        "posted_age_seconds": 5 * 86400,
        "posted_at": "5 days ago"
    }
    res_fresh = policy.qualify(fresh_job, req)
    assert res_fresh.qualified is True


def test_candidate_triage_rejects_stale_search_snippets():
    from datahunt.agent.runtime import AgentRuntime
    from datahunt.agent.state import AgentState
    from datahunt.agents import JobSearchRequest

    state = AgentState(
        task_id="t_fresh",
        run_id="r_fresh",
        request="Find GenAI Engineer jobs in Dubai",
        mode="jobs",
        canonical_job_request=JobSearchRequest(job_title="GenAI Engineer", target_locations=["Dubai"])
    )

    # Candidate with snippet indicating 1 year ago
    cand_stale = {
        "url": "https://jobs.lever.co/example/123",
        "title": "GenAI Engineer - Dubai",
        "snippet": "We are looking for a GenAI Engineer. Posted 1 year ago on Lever."
    }
    assert AgentRuntime._is_promising_candidate(None, cand_stale, state) is False

    # Candidate with snippet indicating 6 months ago
    cand_6mo = {
        "url": "https://jobs.ashbyhq.com/example/456",
        "title": "GenAI Engineer - Dubai",
        "snippet": "Apply now. Posted 6 months ago."
    }
    assert AgentRuntime._is_promising_candidate(None, cand_6mo, state) is False

    # Candidate with fresh snippet
    cand_fresh = {
        "url": "https://jobs.ashbyhq.com/example/789",
        "title": "GenAI Engineer - Dubai",
        "snippet": "We are hiring an AI Engineer. Posted 2 days ago."
    }
    assert AgentRuntime._is_promising_candidate(None, cand_fresh, state) is True

