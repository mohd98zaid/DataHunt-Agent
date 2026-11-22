import pytest
from fastapi.testclient import TestClient
from datahunt.agent.cv_intelligence import (
    CVProfile,
    parse_cv_heuristic,
    score_job_eligibility,
    rank_jobs_for_cv,
    generate_search_directives_from_cv,
    generate_tailored_cv_bullet_points
)
from datahunt.api import app

client = TestClient(app)

SAMPLE_CV_TEXT = """
Mohammad Zaid
GenAI Architect | Agentic AI Engineering Lead | 5.8+ Years | TCS
mohd98zaid@gmail.com | LinkedIn | GitHub
PROFESSIONAL SUMMARY
Senior GenAI Architect and Agentic AI Engineering Lead with 5.8+ years at Tata Consultancy Services (TCS), with delivery experience across Healthcare, BFSI (Capital Markets) and Aviation. Expert in Large Language Model (LLM) applications, Retrieval Augmented Generation (RAG), multi-agent systems on LangGraph, Model Context Protocol (MCP), and MLOps on Azure and AWS. Seeking Senior GenAI Architect roles in Saudi Arabia, the UAE, Kuwait and the wider GCC.
TARGET ROLES: Senior GenAI Architect, Agentic AI Engineering Lead
CORE COMPETENCIES:
LangGraph, LangChain, RAG, Agentic AI, Multi-Agent Systems, Claude, GPT, OpenAI, Azure OpenAI, AWS Bedrock, Python, FastAPI, Docker, Kubernetes, Terraform, FAISS, KDB+/q, SQL
"""

def test_parse_cv_heuristic():
    profile = parse_cv_heuristic(SAMPLE_CV_TEXT)
    assert profile.candidate_name == "Mohammad Zaid"
    assert profile.years_of_experience == 5.8
    assert "Senior / Lead / Architect" in profile.seniority_level
    assert any("GenAI" in r or "Architect" in r for r in profile.target_roles)
    assert "Saudi Arabia" in profile.target_regions
    assert "UAE" in profile.target_regions
    assert "LangGraph" in profile.core_skills
    assert "Python" in profile.core_skills


def test_score_job_eligibility_platinum_fit():
    profile = parse_cv_heuristic(SAMPLE_CV_TEXT)
    job = {
        "id": "job-test-1",
        "fields": {
            "title": "Senior GenAI Architect - Agentic Systems",
            "company": "Aramco Digital",
            "location": "Riyadh, Saudi Arabia",
            "description": "Designing multi-agent workflows using LangGraph and RAG with Python on Azure.",
            "skills": ["LangGraph", "RAG", "Python", "Azure"]
        }
    }
    scored = score_job_eligibility(job, profile)
    assert scored["match_score"] >= 70
    assert scored["match_grade"] in ("PLATINUM", "GOLD")
    assert scored["breakdown"]["geo"] == 20
    assert scored["breakdown"]["role"] >= 25
    assert "LangGraph" in scored["matching_skills"]


def test_rank_jobs_for_cv():
    profile = parse_cv_heuristic(SAMPLE_CV_TEXT)
    jobs = [
        {
            "id": "j1",
            "fields": {
                "title": "Junior Java Developer",
                "company": "Tech Corp",
                "location": "London, UK",
                "description": "Java spring boot backend"
            }
        },
        {
            "id": "j2",
            "fields": {
                "title": "Lead Agentic AI Architect",
                "company": "Dubai AI Lab",
                "location": "Dubai, UAE",
                "description": "LangGraph RAG Claude Python"
            }
        }
    ]
    ranked = rank_jobs_for_cv(jobs, profile, min_score=10)
    assert len(ranked) == 2
    assert ranked[0]["job_id"] == "j2"
    assert ranked[0]["match_score"] > ranked[-1]["match_score"]


def test_generate_tailored_bullets():
    profile = parse_cv_heuristic(SAMPLE_CV_TEXT)
    job = {
        "id": "j3",
        "title": "Senior AI Engineer",
        "company": "Emirates NBD",
        "location": "Dubai, UAE"
    }
    tweaks = generate_tailored_cv_bullet_points(job, profile)
    assert tweaks["job_title"] == "Senior AI Engineer"
    assert tweaks["company"] == "Emirates NBD"
    assert len(tweaks["bullet_points"]) == 3
    assert "LangGraph" in tweaks["bullet_points"][0]


def test_cv_api_endpoints():
    # 1. Parse endpoint
    res_parse = client.post("/api/cv/parse", json={"content": SAMPLE_CV_TEXT})
    assert res_parse.status_code == 200
    data = res_parse.json()
    assert data["status"] == "success"
    assert data["profile"]["candidate_name"] == "Mohammad Zaid"

    # 2. Match endpoint
    res_match = client.post("/api/cv/match", json={"content": SAMPLE_CV_TEXT, "min_score": 30, "limit": 10})
    assert res_match.status_code == 200
    data_match = res_match.json()
    assert data_match["status"] == "success"
    assert "total_jobs_evaluated" in data_match

    # 3. Directives endpoint
    res_dir = client.post("/api/cv/directives", json={"content": SAMPLE_CV_TEXT})
    assert res_dir.status_code == 200
    assert "suggested_query" in res_dir.json()["directives"]
