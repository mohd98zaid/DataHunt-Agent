import pytest
from unittest.mock import MagicMock
from datahunt.tools.search import generate_ats_queries, SearchHit, MockSearchProvider
from datahunt.tools.extract import (
    extract_job_records_from_document,
    extract_jsonld_job_posting,
    ExtractTool
)
from datahunt.models import SourceDocument, ResearchSpec, RunBudget, ExtractedRecord
from agent.orchestrator import ResearchOrchestrator
from agent.job_agent import JobHuntAgent

def test_generate_ats_queries_cleaning():
    # Test query with filler prepositions and search command words
    queries = generate_ats_queries("find genAI jobs in dubai 0sec latest")
    assert len(queries) >= 4
    # Ensure filler words (find, jobs, 0sec, latest) are stripped but location (in dubai) is preserved
    # New format uses site:boards.greenhouse.io and site:job-boards.greenhouse.io
    assert any("greenhouse.io" in q.lower() and "genai" in q.lower() for q in queries)
    assert any("jobs.lever.co" in q.lower() and "genai" in q.lower() for q in queries)
    # Location "dubai" should be preserved in queries
    assert any("dubai" in q.lower() for q in queries)

    # Test query with platform names and roles
    queries2 = generate_ats_queries("remote python staff engineer jobs lever greenhouse")
    assert any("greenhouse.io" in q for q in queries2)
    assert any("python staff engineer" in q.lower() for q in queries2)


def test_extract_job_records_from_document_greenhouse_pattern():
    meta = {
        "url": "https://job-boards.greenhouse.io/scaleai/jobs/4591298005",
        "title": "Job Application for Senior Software Engineer, GenAI at Scale AI",
        "domain": "job-boards.greenhouse.io"
    }
    text = """
    Senior Software Engineer, GenAI
    San Francisco, CA; New York, NY
    About Scale
    Scale is building AI foundations.
    Base pay range: $180,000 - $240,000 per year
    0 sec ago
    """
    records = extract_job_records_from_document(text, meta, "GenAI")
    assert len(records) == 1
    fields = records[0]["fields"]
    assert "Senior Software Engineer, GenAI" in fields["title"]
    assert fields["company"] == "Scale AI"
    assert "San Francisco, CA" in fields["location"]
    assert "$180,000 - $240,000" in fields["salary"]
    assert fields["posted_age_seconds"] == 0
    assert "0-SEC" in fields["freshness_badge"] or "JUST NOW" in fields["freshness_badge"]
    assert fields["application_url"] == meta["url"]

def test_extract_job_records_from_document_meta_redirect():
    meta = {
        "url": "https://www.databricks.com/company/careers/open-positions/job?gh_jid=7011263002",
        "title": "PhD GenAI Research Scientist Intern - Databricks",
        "domain": "databricks.com"
    }
    text = """--- STRUCTURED PAGE METADATA ---
TITLE: PhD GenAI Research Scientist Intern - Databricks
DESCRIPTION: PhD GenAI Research Scientist Intern, San Francisco, California. Join us!
URL: https://www.databricks.com/company/careers/university-recruiting/phd-genai-research-scientist-intern-7011263002
--- END PAGE METADATA ---
PhD GenAI Research Scientist Intern
San Francisco, California
Apply Now
"""
    records = extract_job_records_from_document(text, meta, "GenAI")
    assert len(records) == 1
    fields = records[0]["fields"]
    assert "PhD GenAI Research Scientist Intern" in fields["title"]
    assert fields["company"] == "Databricks"
    assert "San Francisco" in fields["location"]
    assert fields["employment_type"] == "Internship"
    assert "7011263002" in fields["application_url"]

def test_extract_tool_fallback_when_llm_rate_limited():
    mock_client = MagicMock()
    mock_client.is_live = True
    # Simulate Gemini 429 rate limit exception
    mock_client.extract_from_document.side_effect = Exception("429 RESOURCE_EXHAUSTED")

    tool = ExtractTool(mock_client)
    doc = SourceDocument(
        run_id="run_test",
        requested_url="https://job-boards.greenhouse.io/anthropic/jobs/123",
        canonical_url="https://job-boards.greenhouse.io/anthropic/jobs/123",
        domain="job-boards.greenhouse.io",
        title="Job Application for Research Scientist, Alignment at Anthropic",
        extracted_text="""
        Research Scientist, Alignment
        San Francisco, CA
        Salary: $250,000 - $350,000
        just now
        """
    )
    spec = ResearchSpec(
        topic="Research Scientist",
        agent_mode="jobs",
        requested_fields=["title", "company", "location", "posted_at", "application_url"],
        max_records=5
    )

    res = tool.execute(doc, spec, "run_test")
    assert res.success is True
    assert len(res.data) == 1
    rec = res.data[0]
    assert rec.record_type == "job_listing"
    assert "Research Scientist" in rec.fields["title"]
    assert rec.fields["company"] == "Anthropic"
    assert rec.fields["posted_age_seconds"] == 0

def test_job_hunt_agent_orchestrator_integration():
    mock_hits = [
        SearchHit(
            url="https://job-boards.greenhouse.io/testco/jobs/999",
            title="Job Application for Senior GenAI Engineer at TestCo",
            snippet="TestCo is hiring Senior GenAI Engineer in Dubai, UAE.",
            source_domain="job-boards.greenhouse.io"
        )
    ]
    mock_search = MockSearchProvider(mock_hits)

    agent = JobHuntAgent()
    orchestrator = agent.orchestrator
    orchestrator.search_tool.provider = mock_search

    # Run in jobs mode
    task, run = orchestrator.create_task_and_run(
        request_text="GenAI Roles",
        max_records=5,
        freshness_days=1,
        agent_mode="jobs"
    )
    assert task.agent_mode == "jobs"
    assert "title" in task.normalized_spec.requested_fields
    assert "company" in task.normalized_spec.requested_fields

def test_is_valid_job_url_filters_login_and_board_roots():
    from datahunt.tools.search import is_valid_job_url
    # Login and authentication URLs -> must be False
    assert is_valid_job_url("https://onboarding.greenhouse.io/users/sign_in") is False
    assert is_valid_job_url("https://app2.greenhouse.io/users/sign_in") is False
    assert is_valid_job_url("https://my.greenhouse.io/users/sign_in") is False
    assert is_valid_job_url("https://jobs.lever.co/signin") is False

    # Company root boards without job IDs -> must be False
    assert is_valid_job_url("https://job-boards.greenhouse.io/scaleai") is False
    assert is_valid_job_url("https://job-boards.greenhouse.io/zscaler?error=true") is False
    assert is_valid_job_url("https://jobs.lever.co/anomaly") is False
    assert is_valid_job_url("https://jobs.lever.co/lemnis") is False
    assert is_valid_job_url("https://jobs.ashbyhq.com/openai") is False
    assert is_valid_job_url("https://apply.workable.com/anthropic") is False

    # Aggregator search / category listings -> must be False
    assert is_valid_job_url("https://www.founditgulf.com/search/ai-and-genai-capabilities-jobs-in-dubai") is False
    assert is_valid_job_url("https://www.remotedxb.com/skill/genai") is False
    assert is_valid_job_url("https://www.naukrigulf.com/genai-engineer-jobs-in-uae") is False

    # Real job postings -> must be True
    assert is_valid_job_url("https://job-boards.greenhouse.io/scaleai/jobs/4591298005") is True
    assert is_valid_job_url("https://job-boards.eu.greenhouse.io/ruyaai/jobs/4944185101") is True
    assert is_valid_job_url("https://www.databricks.com/company/careers/open-positions/job?gh_jid=7011263002") is True
    assert is_valid_job_url("https://jobs.lever.co/palantir/ff1029bd-bb6d-4d78-a03e-5f9744d0b798/apply") is True
    assert is_valid_job_url("https://jobs.lever.co/appen/48d3c273-f973-4d7e-95d0-596cef93de56") is True
    assert is_valid_job_url("https://jobs.ashbyhq.com/scale/b5a03421-1234-5678-9abc-def012345678") is True
    assert is_valid_job_url("https://apply.workable.com/anthropic/j/ABC123XYZ/") is True

def test_lever_title_company_inversion_fix():
    meta = {
        "url": "https://jobs.lever.co/palantir/ff1029bd-bb6d-4d78-a03e-5f9744d0b798/apply",
        "title": "Palantir Technologies - Forward Deployed AI Engineer",
        "domain": "jobs.lever.co"
    }
    text = """
    Forward Deployed AI Engineer
    London, UK
    Palantir Technologies is hiring Forward Deployed AI Engineers.
    """
    records = extract_job_records_from_document(text, meta, "AI Engineer")
    assert len(records) == 1
    fields = records[0]["fields"]
    # Company and Title must NOT be swapped
    assert fields["company"] == "Palantir Technologies"
    assert fields["title"] == "Forward Deployed AI Engineer"
    assert "London" in fields["location"]

def test_extract_job_records_rejects_identical_title_and_company_or_login():
    # Login page
    meta_login = {
        "url": "https://onboarding.greenhouse.io/users/sign_in",
        "title": "Sign In | Greenhouse Onboarding",
        "domain": "onboarding.greenhouse.io"
    }
    assert extract_job_records_from_document("Sign In", meta_login, "Jobs") == []

    # Company root board with same title as company
    meta_root = {
        "url": "https://job-boards.greenhouse.io/scaleai",
        "title": "Jobs at Scale AI",
        "domain": "job-boards.greenhouse.io"
    }
    assert extract_job_records_from_document("Current openings at Scale AI", meta_root, "Jobs") == []

def test_geographic_compliance_verification():
    from datahunt.tools.verify import VerifyTool, check_geographic_compliance
    from datahunt.models import ExtractedRecord, RecordEvidence

    assert check_geographic_compliance("Dubai, UAE", "UAE") is True
    assert check_geographic_compliance("Abu Dhabi", "UAE") is True
    assert check_geographic_compliance("Remote", "UAE") is True
    assert check_geographic_compliance("Worldwide", "UAE") is True
    assert check_geographic_compliance("San Francisco, CA", "UAE") is False
    assert check_geographic_compliance("London, UK", "UAE") is False
    assert check_geographic_compliance("Singapore", "UAE") is False

    tool = VerifyTool()
    rec_sf = ExtractedRecord(
        run_id="run_test",
        source_document_id="doc_1",
        record_type="job_listing",
        canonical_url="https://job-boards.greenhouse.io/scaleai/jobs/123",
        fields={"title": "Staff AI Engineer", "company": "Scale AI", "location": "San Francisco, CA"},
        evidence=[
            RecordEvidence(record_id="r1", source_document_id="d1", field_name="title", evidence_text="Staff AI Engineer", supports_value=1),
            RecordEvidence(record_id="r1", source_document_id="d1", field_name="company", evidence_text="Scale AI", supports_value=1)
        ]
    )
    res_sf = tool.execute(rec_sf, geography_rule="UAE")
    assert rec_sf.verification_status.value == "needs_review"
    assert any("does not match requested geography" in w for w in rec_sf.warnings)

    rec_uae = ExtractedRecord(
        run_id="run_test",
        source_document_id="doc_2",
        record_type="job_listing",
        canonical_url="https://job-boards.greenhouse.io/testco/jobs/456",
        fields={"title": "GenAI Lead", "company": "TestCo", "location": "Dubai, UAE"},
        evidence=[
            RecordEvidence(record_id="r2", source_document_id="d2", field_name="title", evidence_text="GenAI Lead", supports_value=1),
            RecordEvidence(record_id="r2", source_document_id="d2", field_name="company", evidence_text="TestCo", supports_value=1)
        ]
    )
    res_uae = tool.execute(rec_uae, geography_rule="UAE")
    assert rec_uae.verification_status.value == "verified"

