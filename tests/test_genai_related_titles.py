import pytest
from datahunt.agent.policies import match_title_relevance, MatchStatus
from datahunt.agents.query_expansion import QueryExpansionAgent, TAXONOMY
from datahunt.agent.discovery_engine import DiscoveryEngine

ALL_GENAI_RELATED_USER_TITLES = [
    "Generative AI Solution Architect",
    "Senior GenAI Architect",
    "AI Solution Architect",
    "Enterprise AI Architect",
    "AI Technical Architect",
    "GenAI Platform Architect",
    "AI Transformation Architect",
    "Intelligent Automation Architect",
    "AI Integration Architect",
    "Conversational AI Architect",
    "LLM Solution Architect",
    "Agentic AI Architect",
    "Responsible AI Architect",
    "AI Strategy Architect",
    "AI Enterprise Solutions Consultant",
    "Azure AI Solution Architect",
    "AWS AI Solutions Architect",
    "Google Cloud AI Architect",
    "Cloud GenAI Architect",
    "Cloud AI/ML Architect",
    "Azure OpenAI Architect",
    "AWS Generative AI Architect",
    "Google Vertex AI Architect",
    "Cloud Data and AI Architect",
    "Multi-Cloud AI Architect",
    "Generative AI Engineer",
    "GenAI Application Engineer",
    "LLM Engineer",
    "LLM Application Developer",
    "AI Agent Engineer",
    "Agentic AI Engineer",
    "RAG Engineer",
    "RAG Application Developer",
    "AI Backend Engineer",
    "AI Software Engineer",
    "AI Full-Stack Developer",
    "AI Integration Engineer",
    "Prompt Engineer",
    "AI Automation Engineer",
    "AI Framework Engineer",
    "AI Platform Engineer",
    "GenAI Platform Engineer",
    "LLMOps Engineer",
    "MLOps Engineer",
    "AI Infrastructure Engineer",
    "Machine Learning Platform Engineer",
    "Vector Database Engineer",
    "AI Data Engineer",
    "AI/ML DevOps Engineer",
    "AI Model Deployment Engineer",
    "GenAI Technical Consultant",
    "AI Solutions Consultant",
    "Generative AI Consultant",
    "AI Advisory Consultant",
    "GenAI Technical Lead",
    "AI Engineering Lead",
    "AI Practice Architect",
    "Principal AI Architect",
    "AI Technology Consultant",
    "Enterprise GenAI Consultant",
    "AI Security Architect",
    "AI Governance Architect",
    "AI Risk and Compliance Specialist",
    "AI Evaluation Engineer",
    "LLM Evaluation Specialist",
    "AI Product Architect",
    "Knowledge Engineering Specialist",
    "Conversational AI Engineer",
    "Multimodal AI Engineer",
    "AI Research Engineer",
    "Senior AI Architect",
    "Principal GenAI Architect",
    "Associate AI Architect",
    "AI Solution Design Architect",
    "GenAI Cloud Consultant",
    "AI Systems Architect",
    "Enterprise GenAI Architect",
    "LLMOps Architect",
    "AI/ML Solution Architect",
    "Machine Learning Architect",
    "Deep Learning Engineer",
    "AI Platform Architect",
    "AI Infrastructure Architect",
    "GenAI Integration Architect",
    "AI Technical Lead",
    "GenAI Developer",
    "AI Application Developer",
    "AI Consultant",
    "AI Innovation Architect",
    "AI Modernization Architect",
    "Generative AI Specialist",
    "GenAI Specialist",
    "AI Technology Architect",
    "AI Delivery Architect",
    "AI Presales Consultant",
    "AI Presales Architect",
    "AI Implementation Consultant",
    "AI Solutions Engineer",
    "AI Product Manager",
    "GenAI Product Manager",
    "AI Program Architect",
    "AI Enterprise Architect",
    "AI Data Architect",
    "AI Governance Specialist",
    "AI Workflow Automation Engineer",
    "AI Orchestration Engineer",
    "Enterprise AI Consultant",
    "OpenAI Solutions Architect",
    "Microsoft Copilot Architect",
    "Azure Machine Learning Architect",
    "AWS Bedrock Architect",
    "Google Vertex AI Engineer",
    "AI Cloud Engineer",
    "GenAI Solutions Engineer",
    "LLM Application Architect",
    "RAG Solution Architect",
    "AI Agent Architect",
    "AI Automation Architect",
    "AI Security Specialist",
    "AI Operations Engineer",
    "AI Platform Consultant",
    "Generative AI Technical Architect",
]


@pytest.mark.parametrize("job_title", ALL_GENAI_RELATED_USER_TITLES)
def test_all_122_genai_related_titles_qualify_for_genai_search(job_title):
    status, score, reason = match_title_relevance("GenAI", job_title)
    assert status == MatchStatus.MATCH, f"Expected '{job_title}' to match GenAI query, got {status} ({score}): {reason}"
    assert score >= 0.80, f"Expected high confidence score for '{job_title}', got {score}"


def test_discovery_engine_hit_matches_role_for_genai_architects_and_specialists():
    engine = DiscoveryEngine()
    test_hits = [
        ("https://example.com/job1", "Generative AI Solution Architect", "Leading enterprise AI architecture"),
        ("https://example.com/job2", "LLM Solution Architect", "Designing RAG and Agentic systems"),
        ("https://example.com/job3", "AWS Bedrock Architect", "Building generative AI applications on AWS"),
        ("https://example.com/job4", "Microsoft Copilot Architect", "Enterprise copilot implementation"),
        ("https://example.com/job5", "Vector Database Engineer", "Vector embeddings and search pipelines"),
        ("https://example.com/job6", "AI Full-Stack Developer", "Next.js frontend with LangChain backend"),
    ]
    for url, title, snippet in test_hits:
        matches = engine._hit_matches_role(url, title, snippet, "GenAI Engineer")
        assert matches is True, f"Expected {title} to match GenAI Engineer role filter"


def test_query_expansion_taxonomy_contains_genai_architects():
    genai_tax = TAXONOMY.get("genai")
    assert genai_tax is not None
    titles = genai_tax["titles"]
    assert "Generative AI Solution Architect" in titles
    assert "AI Solution Architect" in titles
    assert "Enterprise AI Architect" in titles
    assert "Agentic AI Engineer" in titles
    assert "RAG Engineer" in titles
