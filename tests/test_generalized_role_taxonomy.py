import pytest
from datahunt.agent.policies import match_title_relevance, MatchStatus, ROLE_TAXONOMY
from datahunt.agents.query_expansion import TAXONOMY
from datahunt.agent.discovery_engine import DiscoveryEngine


TEST_CASES = [
    # 1. Cloud
    ("Cloud Engineer", "Cloud Solutions Architect", MatchStatus.MATCH),
    ("Cloud Engineer", "AWS Solutions Architect", MatchStatus.MATCH),
    ("Cloud Engineer", "Azure Solutions Architect", MatchStatus.MATCH),
    ("Cloud Engineer", "Cloud Infrastructure Engineer", MatchStatus.MATCH),
    ("Cloud Engineer", "Cloud Platform Engineer", MatchStatus.MATCH),
    ("Cloud Engineer", "Cloud Security Engineer", MatchStatus.MATCH),
    ("Cloud Engineer", "Site Reliability Engineer", MatchStatus.MATCH),
    ("Cloud Engineer", "Chief Accountant", MatchStatus.MISMATCH),

    # 2. DevOps
    ("DevOps Engineer", "Site Reliability Engineer", MatchStatus.MATCH),
    ("DevOps Engineer", "SRE", MatchStatus.MATCH),
    ("DevOps Engineer", "Platform Engineer", MatchStatus.MATCH),
    ("DevOps Engineer", "Cloud Infrastructure Engineer", MatchStatus.MATCH),
    ("DevOps Engineer", "DevSecOps Engineer", MatchStatus.MATCH),
    ("DevOps Engineer", "Build and Release Engineer", MatchStatus.MATCH),
    ("DevOps Engineer", "HR Specialist", MatchStatus.MISMATCH),

    # 3. Data Engineering
    ("Data Engineer", "Big Data Engineer", MatchStatus.MATCH),
    ("Data Engineer", "Data Platform Engineer", MatchStatus.MATCH),
    ("Data Engineer", "Analytics Engineer", MatchStatus.MATCH),
    ("Data Engineer", "ETL Developer", MatchStatus.MATCH),
    ("Data Engineer", "Data Warehouse Architect", MatchStatus.MATCH),
    ("Data Engineer", "Data Pipeline Developer", MatchStatus.MATCH),
    ("Data Engineer", "Graphic Designer", MatchStatus.MISMATCH),

    # 4. Cybersecurity
    ("Cybersecurity Engineer", "Information Security Engineer", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "Infosec Engineer", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "Security Architect", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "SOC Analyst", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "Penetration Tester", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "AppSec Engineer", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "Cloud Security Architect", MatchStatus.MATCH),
    ("Cybersecurity Engineer", "Sales Manager", MatchStatus.MISMATCH),

    # 5. Mobile
    ("Mobile Developer", "iOS Developer", MatchStatus.MATCH),
    ("Mobile Developer", "Android Developer", MatchStatus.MATCH),
    ("Mobile Developer", "Flutter Developer", MatchStatus.MATCH),
    ("Mobile Developer", "React Native Developer", MatchStatus.MATCH),
    ("Mobile Developer", "Swift Developer", MatchStatus.MATCH),
    ("Mobile Developer", "Mobile Solutions Architect", MatchStatus.MATCH),
    ("Mobile Developer", "Full Stack Web Developer", MatchStatus.MISMATCH),

    # 6. QA
    ("QA Engineer", "SDET", MatchStatus.MATCH),
    ("QA Engineer", "Software Development Engineer in Test", MatchStatus.MATCH),
    ("QA Engineer", "Test Automation Engineer", MatchStatus.MATCH),
    ("QA Engineer", "Quality Assurance Engineer", MatchStatus.MATCH),
    ("QA Engineer", "Automation Test Specialist", MatchStatus.MATCH),
    ("QA Engineer", "Lead QA Engineer", MatchStatus.MATCH),
    ("QA Engineer", "Backend Developer", MatchStatus.MISMATCH),

    # 7. Solutions Architecture
    ("Solutions Architect", "Enterprise Architect", MatchStatus.MATCH),
    ("Solutions Architect", "Technical Architect", MatchStatus.MATCH),
    ("Solutions Architect", "Systems Architect", MatchStatus.MATCH),
    ("Solutions Architect", "Infrastructure Architect", MatchStatus.MATCH),
    ("Solutions Architect", "Software Architect", MatchStatus.MATCH),
    ("Solutions Architect", "Integration Architect", MatchStatus.MATCH),
    ("Solutions Architect", "Junior Manual Tester", MatchStatus.MISMATCH),

    # 8. Backend
    ("Backend Engineer", "API Developer", MatchStatus.MATCH),
    ("Backend Engineer", "Distributed Systems Engineer", MatchStatus.MATCH),
    ("Backend Engineer", "Server Engineer", MatchStatus.MATCH),
    ("Backend Engineer", "Java Backend Developer", MatchStatus.MATCH),
    ("Backend Engineer", "Python Backend Engineer", MatchStatus.MATCH),
    ("Backend Engineer", "Content Writer", MatchStatus.MISMATCH),

    # 9. Frontend
    ("Frontend Engineer", "UI Engineer", MatchStatus.MATCH),
    ("Frontend Engineer", "React Developer", MatchStatus.MATCH),
    ("Frontend Engineer", "Next.js Developer", MatchStatus.MATCH),
    ("Frontend Engineer", "Design Systems Engineer", MatchStatus.MATCH),
    ("Frontend Engineer", "DevOps Engineer", MatchStatus.MISMATCH),
]


@pytest.mark.parametrize("req_title, job_title, expected_status", TEST_CASES)
def test_generalized_role_family_associations(req_title, job_title, expected_status):
    status, score, reason = match_title_relevance(req_title, job_title)
    assert status == expected_status, f"Expected {expected_status} for '{job_title}' under '{req_title}', got {status} ({score}): {reason}"
    if expected_status == MatchStatus.MATCH:
        assert score >= 0.80, f"Expected score >= 0.80 for related role, got {score}"


def test_discovery_engine_hit_matches_role_generalized():
    engine = DiscoveryEngine()
    assert engine._hit_matches_role("https://example.com/job", "AWS Solutions Architect", "Cloud architecture", "Cloud Engineer") is True
    assert engine._hit_matches_role("https://example.com/job", "Site Reliability Engineer", "Platform infra", "DevOps Engineer") is True
    assert engine._hit_matches_role("https://example.com/job", "Analytics Engineer", "Data pipelines with dbt", "Data Engineer") is True
    assert engine._hit_matches_role("https://example.com/job", "Security Architect", "AppSec and cloud security", "Cybersecurity Engineer") is True


def test_query_expansion_covers_all_major_domains():
    expected_domains = [
        "cloud engineer", "data engineer", "cybersecurity engineer",
        "mobile developer", "qa engineer", "solutions architect", "product manager"
    ]
    for domain in expected_domains:
        assert domain in TAXONOMY, f"Expected domain '{domain}' in TAXONOMY"
        assert len(TAXONOMY[domain]["titles"]) >= 4, f"Expected at least 4 associated titles for '{domain}'"
