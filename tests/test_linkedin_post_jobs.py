import pytest
from datahunt.tools.search import is_valid_job_url, generate_ats_queries
from datahunt.tools.extract import extract_job_records_from_document
from datahunt.agent.runtime import AgentRuntime
from datahunt.agent.state import AgentState
from datahunt.agent.discovery_engine import DiscoveryEngine
from datahunt.agents import JobSearchRequest


def test_is_valid_job_url_handles_linkedin_properly():
    # Public hiring posts should be accepted
    assert is_valid_job_url("https://www.linkedin.com/posts/sarah-connor_we-are-hiring-genai-engineer-activity-7123456789-abcd") is True
    assert is_valid_job_url("https://www.linkedin.com/feed/update/urn:li:activity:7123456789") is True
    assert is_valid_job_url("https://www.linkedin.com/pulse/we-are-hiring-ai-engineers-in-dubai") is True
    assert is_valid_job_url("https://www.linkedin.com/jobs/view/123456789") is True

    # Profiles and company directories must still be rejected
    assert is_valid_job_url("https://www.linkedin.com/in/sarah-connor") is False
    assert is_valid_job_url("https://www.linkedin.com/company/neurotech") is False


def test_extract_job_records_from_linkedin_hiring_post():
    title = "Sarah Jenkins on LinkedIn: We are hiring a Senior Generative AI Engineer in Dubai! DM me"
    url = "https://www.linkedin.com/posts/sarah-jenkins_we-are-hiring-genai-activity-712345"
    doc_text = (
        "# Sarah Jenkins on LinkedIn: We are hiring a Senior Generative AI Engineer in Dubai! DM me\n\n"
        "URL: https://www.linkedin.com/posts/sarah-jenkins_we-are-hiring-genai-activity-712345\n\n"
        "Our team at NeuralCorp in Dubai is expanding. We are looking for a Senior Generative AI Engineer "
        "with 3+ years experience in Python, RAG pipelines, and LangChain. "
        "Apply by sending your CV to jobs@neuralcorp.ae or DM me."
    )
    metadata = {
        "title": title,
        "url": url,
        "domain": "linkedin.com"
    }

    records = extract_job_records_from_document(doc_text, metadata, topic="GenAI Engineer", geography="Dubai")
    assert len(records) == 1
    rec = records[0]
    assert "Generative AI Engineer" in rec["title"]
    assert rec["company"] == "NeuralCorp"
    assert "Dubai" in rec["location"]
    assert rec["freshness_badge"] == "💼 LINKEDIN POST"
    assert rec["application_url"] == url


def test_extract_job_records_rejects_job_seeker_posts():
    # A candidate posting that they are looking for a job is NOT a job vacancy
    title = "John Doe on LinkedIn: I am open to work as a GenAI Engineer in Dubai!"
    url = "https://www.linkedin.com/posts/john-doe_open-to-work-genai-activity-712345"
    doc_text = (
        "# John Doe on LinkedIn: I am open to work as a GenAI Engineer in Dubai!\n\n"
        "After 4 years at my previous firm, I am actively seeking a new role as a GenAI Engineer in Dubai or Remote. "
        "Open to work and looking for new opportunities."
    )
    metadata = {
        "title": title,
        "url": url,
        "domain": "linkedin.com"
    }

    records = extract_job_records_from_document(doc_text, metadata, topic="GenAI Engineer", geography="Dubai")
    assert len(records) == 0, "Job seeker posts should not be extracted as job openings"


def test_candidate_triage_filters_job_seeker_posts():
    state = AgentState(
        task_id="t1",
        run_id="r1",
        request="Find GenAI Engineer jobs in Dubai",
        mode="jobs",
        canonical_job_request=JobSearchRequest(
            job_title="GenAI Engineer",
            target_locations=["Dubai"],
            max_experience_years=5
        )
    )

    # Legitimate hiring post
    hiring_cand = {
        "url": "https://www.linkedin.com/posts/recruiter_hiring-genai-engineer-activity-123",
        "title": "Ahmed on LinkedIn: We are hiring a GenAI Engineer in Dubai",
        "snippet": "We have an open role for a GenAI Engineer at our Dubai office."
    }
    assert AgentRuntime._is_promising_candidate(None, hiring_cand, state) is True

    # Job seeker post
    seeker_cand = {
        "url": "https://www.linkedin.com/posts/candidate_opentowork-activity-456",
        "title": "Alex on LinkedIn: Open to work | Seeking a role as GenAI Engineer",
        "snippet": "I am looking for a job as an AI Engineer in Dubai. Connect with me!"
    }
    assert AgentRuntime._is_promising_candidate(None, seeker_cand, state) is False


def test_discovery_engine_generates_linkedin_post_tasks():
    engine = DiscoveryEngine()
    req = JobSearchRequest(job_title="GenAI Engineer", target_locations=["Dubai", "Riyadh"])
    state = AgentState(
        task_id="t2",
        run_id="r2",
        request="Find GenAI Engineer jobs in Gulf",
        mode="jobs",
        locations=["Dubai", "Riyadh"]
    )

    tasks = engine.generate_initial_tasks(req, state)
    post_tasks = [t for t in tasks if "linkedin.com/posts" in t.query]
    assert len(post_tasks) >= 1
    assert any("Dubai" in t.query or "Riyadh" in t.query for t in post_tasks)
