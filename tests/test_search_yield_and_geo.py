import pytest
from datahunt.tools.search import is_valid_job_url
from datahunt.agents.hard_filter import HardFilter
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.agents.normalizer import NormalizedJob
from datahunt.agents.job_analysis import JobAnalysisAgent

def test_is_valid_job_url_rejects_people_and_profiles():
    # Applicant/candidate profiles on GulfTalent, Bayt, etc. must be False
    assert is_valid_job_url('https://www.gulftalent.com/people/mohammad-tanzeel-ur-rehman-11634609') is False
    assert is_valid_job_url('https://www.bayt.com/en/candidates/ahmed-ali-12345/') is False
    assert is_valid_job_url('https://example.com/talent/view/123') is False
    assert is_valid_job_url('https://example.com/cv/download/john-doe') is False
    assert is_valid_job_url('https://boards.greenhouse.io/company/jobs/12345/confirmation') is False
    assert is_valid_job_url('https://boards.greenhouse.io/company/jobs/12345/submitted') is False

def test_is_valid_job_url_accepts_indeed_vjk_and_direct_jobs():
    # Indeed with vjk parameter must be True
    assert is_valid_job_url('https://ae.indeed.com/q-ai-engineer-jobs.html?vjk=966e82849cf7aafb') is True
    assert is_valid_job_url('https://www.gulftalent.com/uae/jobs/genai-engineer-530798') is True
    assert is_valid_job_url('https://boards.greenhouse.io/scaleai/jobs/4359637005') is True

def test_hard_filter_rejects_foreign_country_remote():
    hf = HardFilter()
    req = JobSearchRequest(raw_query='Find AI jobs in Saudi or UAE', job_title='AI Engineer', location='Saudi or UAE')
    
    # Local job in Dubai -> Pass
    j_dubai = NormalizedJob(raw_id='1', title='AI Engineer', company='Acme', location='Dubai, UAE', remote_status='onsite')
    # Local job in Riyadh -> Pass
    j_riyadh = NormalizedJob(raw_id='2', title='AI Engineer', company='Acme', location='Riyadh, Saudi Arabia', remote_status='onsite')
    # Truly agnostic global remote -> Pass
    j_global = NormalizedJob(raw_id='3', title='AI Engineer', company='Acme', location='Remote', remote_status='remote')
    # Foreign US remote -> Must be REJECTED
    j_us = NormalizedJob(raw_id='4', title='AI Engineer', company='Acme', location='Denver, CO; Chicago, IL (Remote); United States (Remote)', remote_status='remote')
    # Foreign UK onsite -> Must be REJECTED
    j_uk = NormalizedJob(raw_id='5', title='AI Engineer', company='Acme', location='London, UK', remote_status='onsite')

    passed, rejected = hf.apply([j_dubai, j_riyadh, j_global, j_us, j_uk], req)
    passed_ids = [j.raw_id for j in passed]
    rejected_ids = [j.raw_id for j, r in rejected]

    assert '1' in passed_ids
    assert '2' in passed_ids
    assert '3' in passed_ids
    assert '4' in rejected_ids
    assert '5' in rejected_ids

def test_job_analysis_gives_full_score_to_regional_cities():
    ja = JobAnalysisAgent()
    req = JobSearchRequest(raw_query='Find AI jobs in Saudi or UAE', job_title='AI Engineer', location='Saudi or UAE')
    
    j_dubai = NormalizedJob(raw_id='1', title='AI Engineer', company='Acme', location='Dubai, UAE', remote_status='onsite')
    j_riyadh = NormalizedJob(raw_id='2', title='AI Engineer', company='Acme', location='Riyadh, Saudi Arabia', remote_status='onsite')
    j_remote = NormalizedJob(raw_id='3', title='AI Engineer', company='Acme', location='Remote / Unspecified', remote_status='remote')

    res = ja.analyze_and_rank([j_dubai, j_riyadh, j_remote], req)
    dubai_res = next(r for r in res if r.job.raw_id == '1')
    riyadh_res = next(r for r in res if r.job.raw_id == '2')
    remote_res = next(r for r in res if r.job.raw_id == '3')

    # Dubai & Riyadh must have location score = 1.0
    assert dubai_res.location_match_score == 1.0 or any('Direct location match' in m for m in dubai_res.matching_requirements)
    assert riyadh_res.location_match_score == 1.0 or any('Direct location match' in m for m in riyadh_res.matching_requirements)
    # Dubai and Riyadh should have higher relevance than generic remote
    assert dubai_res.relevance_score >= remote_res.relevance_score

def test_is_valid_job_url_rejects_subdomain_people_and_talents():
    assert is_valid_job_url("https://people.bayt.com/lalithambica-akunuri-93039847/") is False
    assert is_valid_job_url("https://talent.example.com/john-smith") is False
    assert is_valid_job_url("https://candidate.example.com/view/456") is False
    assert is_valid_job_url("https://candidates.example.com/cv/789") is False

def test_prefetched_job_document_cache():
    from datahunt.tools.fetch import FetchTool, store_prefetched_job_document
    url = "https://api-jobs.example.com/role/12345"
    store_prefetched_job_document(url, "Senior AI Engineer at Acme", "# Senior AI Engineer\n\nAcme Corp is hiring in Dubai.")
    
    fetch_tool = FetchTool()
    res = fetch_tool.execute(url=url)
    assert res.success is True
    assert res.data.title == "Senior AI Engineer at Acme"
    assert "Acme Corp is hiring in Dubai" in res.data.extracted_text
    assert "prefetched_api_document" in res.data.policy_flags
