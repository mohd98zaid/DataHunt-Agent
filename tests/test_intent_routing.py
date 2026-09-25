import pytest
from datahunt.agents.intent_router import IntentRouter
from datahunt.models.intent import ResearchIntent, ResearchOutputType

def test_explain_how_to_travel_to_space():
    router = IntentRouter()
    spec = router.classify("explain me how to travel to space")
    assert spec.intent in (ResearchIntent.HOW_TO, ResearchIntent.EXPLANATION)
    assert spec.requested_output == ResearchOutputType.ANSWER
    assert spec.intent != ResearchIntent.JOB_SEARCH

def test_how_do_i_become_ai_engineer():
    router = IntentRouter()
    spec = router.classify("how do I become an AI engineer")
    assert spec.intent == ResearchIntent.HOW_TO
    assert spec.requested_output == ResearchOutputType.ANSWER
    # Crucial: Must NOT be job search despite the word "engineer"
    assert spec.intent != ResearchIntent.JOB_SEARCH

def test_find_ai_engineer_jobs_dubai():
    router = IntentRouter()
    spec = router.classify("find AI engineer jobs in Dubai")
    assert spec.intent == ResearchIntent.JOB_SEARCH
    assert spec.requested_output == ResearchOutputType.JOB_RESULTS

def test_compare_react_vs_vue():
    router = IntentRouter()
    spec = router.classify("compare React vs Vue")
    assert spec.intent == ResearchIntent.COMPARISON
    assert spec.requested_output == ResearchOutputType.COMPARISON

def test_what_is_quantum_computing():
    router = IntentRouter()
    spec = router.classify("what is quantum computing")
    assert spec.intent == ResearchIntent.EXPLANATION
    assert spec.requested_output == ResearchOutputType.ANSWER

def test_latest_news_james_webb():
    router = IntentRouter()
    spec = router.classify("latest news about James Webb space telescope")
    assert spec.intent in (ResearchIntent.NEWS_RESEARCH, ResearchIntent.CURRENT_STATUS)
    assert spec.requested_output == ResearchOutputType.ANSWER

def test_market_size_ev_europe():
    router = IntentRouter()
    spec = router.classify("market size of electric vehicles in Europe")
    assert spec.intent == ResearchIntent.MARKET_RESEARCH
    assert spec.requested_output == ResearchOutputType.MARKET_INTEL

def test_spacex_company_profile():
    router = IntentRouter()
    spec = router.classify("SpaceX company profile and valuation")
    assert spec.intent == ResearchIntent.COMPANY_RESEARCH
    assert spec.requested_output == ResearchOutputType.COMPANY_PROFILE

def test_python_developer_salary_berlin():
    router = IntentRouter()
    spec = router.classify("Python developer salary in Berlin")
    assert spec.intent == ResearchIntent.FACTUAL_RESEARCH
    assert spec.requested_output == ResearchOutputType.ANSWER
    # Must NOT be job search despite "developer"
    assert spec.intent != ResearchIntent.JOB_SEARCH

def test_top_ai_startups_2025():
    router = IntentRouter()
    spec = router.classify("top AI startups in 2025")
    assert spec.intent == ResearchIntent.LIST_RESEARCH
    assert spec.requested_output == ResearchOutputType.ENTITY_LIST

def test_who_is_demis_hassabis():
    router = IntentRouter()
    spec = router.classify("who is Demis Hassabis")
    assert spec.intent == ResearchIntent.PEOPLE_RESEARCH
    assert spec.requested_output in (ResearchOutputType.PEOPLE_RESULTS, ResearchOutputType.ANSWER)

def test_remote_frontend_developer_jobs_hiring():
    router = IntentRouter()
    spec = router.classify("remote frontend developer jobs hiring now")
    assert spec.intent == ResearchIntent.JOB_SEARCH
    assert spec.requested_output == ResearchOutputType.JOB_RESULTS

def test_operator_mode_jobs_override():
    router = IntentRouter()
    spec = router.classify("quantum computing", agent_mode="jobs")
    assert spec.intent == ResearchIntent.JOB_SEARCH
    assert spec.requested_output == ResearchOutputType.JOB_RESULTS

def test_operator_mode_market_override():
    router = IntentRouter()
    spec = router.classify("quantum computing", agent_mode="market")
    assert spec.intent == ResearchIntent.MARKET_RESEARCH
    assert spec.requested_output == ResearchOutputType.MARKET_INTEL

def test_operator_mode_research_blocks_job_search():
    router = IntentRouter()
    spec = router.classify("how do I become an AI engineer", agent_mode="research")
    assert spec.intent == ResearchIntent.HOW_TO
    assert spec.requested_output == ResearchOutputType.ANSWER
    assert spec.intent != ResearchIntent.JOB_SEARCH
