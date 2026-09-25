"""
Comprehensive tests for the 70-step intelligent job search pipeline and specialized agents.
"""
import pytest
from fastapi.testclient import TestClient
from datahunt.api import app
from datahunt.agents import (
    QueryUnderstandingAgent, JobSearchRequest, ClarificationQuestion,
    QueryExpansionAgent, ExpandedQuery,
    SearchPlannerAgent, SearchTask,
    DataNormalizer, NormalizedJob,
    HardFilter,
    JobAnalysisAgent, JobMatchResult,
    CompanyResearchAgent,
    InterviewPrepAgent,
    LearningEngine,
    JobMonitor
)


def test_query_understanding_extraction():
    qu = QueryUnderstandingAgent()
    req = qu.understand("Find remote AI Engineer jobs in India with 2-5 years experience and salary above 15 LPA")
    assert isinstance(req, JobSearchRequest)
    assert "ai engineer" in req.job_title.lower()
    assert req.remote_status == "remote"
    assert req.location == "India"
    assert req.experience_min == 2
    assert req.experience_max == 5
    assert req.salary_min == 1500000.0
    assert req.salary_currency == "INR"


def test_local_currency_resolution():
    from datahunt.agents import detect_local_currency

    # 1. Direct detector tests
    assert detect_local_currency("Saudi Arabia") == "SAR"
    assert detect_local_currency("Riyadh, KSA") == "SAR"
    assert detect_local_currency("Dubai, UAE") == "AED"
    assert detect_local_currency("Abu Dhabi") == "AED"
    assert detect_local_currency("Bengaluru, India") == "INR"
    assert detect_local_currency("London, UK") == "GBP"
    assert detect_local_currency("Berlin, Germany") == "EUR"
    assert detect_local_currency("Toronto, Canada") == "CAD"
    assert detect_local_currency("Singapore") == "SGD"
    assert detect_local_currency("Doha, Qatar") == "QAR"
    assert detect_local_currency("Kuwait City") == "KWD"
    assert detect_local_currency("Sydney, Australia") == "AUD"

    # 2. QueryUnderstandingAgent local currency assignment
    qu = QueryUnderstandingAgent()
    req_gulf = qu.understand("Find AI Engineer jobs in Saudi or UAE with 0-5 years experience")
    assert req_gulf.salary_currency in ("SAR", "AED")

    req_dubai = qu.understand("Find Senior AI Engineer jobs in Dubai")
    assert req_dubai.salary_currency == "AED"

    req_london = qu.understand("Find Machine Learning Engineer in London")
    assert req_london.salary_currency == "GBP"

    req_explicit_usd = qu.understand("Find AI Engineer in Dubai with $120k salary")
    assert req_explicit_usd.salary_currency == "USD"

    # 3. DataNormalizer local currency assignment
    norm = DataNormalizer()
    job_uae = norm.normalize({"title": "AI Engineer", "location": "Dubai, UAE", "salary": "25000 - 35000 / month"})
    assert job_uae.salary_currency == "AED"
    assert job_uae.salary_min_annual == 300000.0

    job_ksa = norm.normalize({"title": "ML Engineer", "location": "Riyadh, Saudi Arabia", "salary": "30,000 SAR / month"})
    assert job_ksa.salary_currency == "SAR"
    assert job_ksa.salary_min_annual == 360000.0



def test_query_understanding_clarification_on_empty():
    qu = QueryUnderstandingAgent()
    req = qu.understand("")
    # Either falls back or requests clarification
    assert hasattr(req, "job_title") or isinstance(req, ClarificationQuestion)


def test_query_expansion_taxonomy():
    qe = QueryExpansionAgent()
    req = JobSearchRequest(raw_query="AI Engineer", job_title="AI Engineer", location="India", remote_status="remote")
    expanded = qe.expand(req)
    assert isinstance(expanded, ExpandedQuery)
    assert len(expanded.all_titles) >= 2
    assert any("machine learning" in t.lower() or "ml engineer" in t.lower() for t in expanded.all_titles)
    # must_have_skills contains ONLY user-explicit skills (none here — user gave no skills)
    # Inferred/taxonomy skills go to nice_to_have_skills
    assert len(expanded.must_have_skills) == 0  # no explicit skills in the query
    assert len(expanded.nice_to_have_skills) >= 2  # taxonomy expansion should produce inferred skills


def test_search_planner_multi_tier():
    sp = SearchPlannerAgent()
    req = JobSearchRequest(raw_query="AI Engineer in Dubai", job_title="AI Engineer", location="Dubai", remote_status="onsite")
    expanded = ExpandedQuery(primary_title="AI Engineer", all_titles=["AI Engineer", "ML Engineer"], must_have_skills=["Python", "PyTorch"])
    tasks = sp.plan(req, expanded)
    assert len(tasks) >= 5
    assert any(t.source_type == "ats" for t in tasks)
    assert any(t.source_type in ("regional_board", "job_board") for t in tasks)


def test_data_normalizer():
    norm = DataNormalizer()
    raw = {
        "title": "Senior AI Engineer (LLMs/GenAI) - Remote",
        "company": "Datacamp Inc.",
        "location": "Dubai, UAE",
        "salary": "20 - 30 LPA",
        "experience": "3-6 years",
        "skills": ["python", "pytorch", "docker"],
        "employment_type": "Full-time"
    }
    job = norm.normalize(raw, record_id="rec_test123", canonical_url="https://job-boards.greenhouse.io/test/jobs/1")
    assert isinstance(job, NormalizedJob)
    assert job.normalized_company == "Datacamp"
    assert "ai engineer" in job.normalized_title.lower()
    assert job.remote_status == "remote"
    assert job.salary_min_annual == 2000000.0
    assert job.salary_max_annual == 3000000.0
    assert job.salary_currency == "INR"
    assert job.experience_min_years == 3
    assert job.experience_max_years == 6
    assert "Python" in job.skills


def test_hard_filter():
    hf = HardFilter()
    norm = DataNormalizer()
    req = JobSearchRequest(raw_query="Remote AI in India", job_title="AI Engineer", location="India", remote_status="remote")

    job_valid = norm.normalize({"title": "AI Engineer", "location": "India", "remote_status": "remote"})
    job_wrong_loc = norm.normalize({"title": "AI Engineer", "location": "London, UK", "remote_status": "onsite"})

    passed, rejected = hf.apply([job_valid, job_wrong_loc], req)
    assert len(passed) == 1
    assert passed[0].location == "India"
    assert len(rejected) == 1
    assert "London" in rejected[0][1] or "remote" in rejected[0][1].lower()


def test_job_analysis_scoring():
    analyzer = JobAnalysisAgent()
    norm = DataNormalizer()
    req = JobSearchRequest(raw_query="Remote AI Engineer", job_title="AI Engineer", remote_status="remote", skills=["Python", "PyTorch"])

    job = norm.normalize({
        "title": "Senior AI Engineer",
        "company": "TechCorp",
        "location": "Remote",
        "skills": ["Python", "PyTorch", "Docker"],
        "employment_type": "Full-time"
    })
    results = analyzer.analyze_and_rank([job], req)
    assert len(results) == 1
    m = results[0]
    assert m.relevance_score >= 0.70
    assert m.match_level in ("exceptional", "strong", "good")
    assert len(m.matching_requirements) >= 2
    assert "match" in m.match_explanation.lower()


def test_learning_engine_boost():
    le = LearningEngine()
    norm = DataNormalizer()
    job = norm.normalize({"title": "Principal AI Engineer", "company": "TargetCorp", "location": "Bangalore"})

    # Record save for TargetCorp
    le.record_save({"title": "Principal AI Engineer", "company": "TargetCorp", "location": "Bangalore", "skills": ["Python"]})
    boost = le.calculate_preference_boost(job)
    assert boost > 0.0

    # Record ignore
    le.record_ignore({"title": "Principal AI Engineer", "company": "SpamCorp", "location": "Bangalore"}, reason="disliked_company")
    spam_job = norm.normalize({"title": "Principal AI Engineer", "company": "SpamCorp", "location": "Bangalore"})
    pen = le.calculate_preference_boost(spam_job)
    assert pen < 0.0


def test_interview_prep_agent():
    agent = InterviewPrepAgent()
    pack = agent.prepare(role_title="AI Engineer", company_name="DeepMind", skills=["PyTorch", "Transformers"])
    assert pack.role_title == "AI Engineer"
    assert pack.company_name == "DeepMind"
    assert len(pack.technical_questions) >= 1
    assert len(pack.behavioral_questions) >= 1
    assert len(pack.questions_for_interviewer) >= 2

    feedback = agent.evaluate_answer("AI Engineer", "Tell me about Transformers", "I used self-attention mechanisms to train an LLM.")
    assert "score" in feedback


def test_company_research_agent():
    agent = CompanyResearchAgent()
    profile = agent.research("OpenAI")
    assert profile.company_name == "OpenAI"
    assert len(profile.about) > 0


def test_api_lifecycle_endpoints():
    client = TestClient(app)

    # 1. Company research
    res = client.post("/api/company/research", json={"company_name": "Stripe"})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # 2. Interview prep
    res = client.post("/api/interview/prep", json={"role_title": "MLOps Engineer", "company_name": "Databricks"})
    assert res.status_code == 200
    assert len(res.json()["interview_prep"]["technical_questions"]) >= 1

    # 3. Interview eval
    res = client.post("/api/interview/evaluate", json={"role_title": "MLOps Engineer", "question": "What is CI/CD?", "answer": "Continuous integration tests code."})
    assert res.status_code == 200
    assert "score" in res.json()["evaluation"]

    # 4. Saved jobs listing
    res = client.get("/api/jobs/saved")
    assert res.status_code == 200

    # 5. Monitor events
    res = client.get("/api/monitor/events")
    assert res.status_code == 200
