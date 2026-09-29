"""
Tests for relaxed job qualification rules, anti-role safeguards,
regional cluster hierarchy matching, and duplicate persistence.
"""
import pytest
from datahunt.agent.policies import (
    match_title_relevance,
    match_location,
    qualify_job,
    MatchStatus,
)
from datahunt.agents.query_understanding import JobSearchRequest
from datahunt.models.record import ExtractedRecord, VerificationStatus
from datahunt.tools.search import LiveJobBoardSearchProvider


class TestRelaxedTitleRelevance:
    def test_genuine_ai_titles_match_genai_query(self):
        req_title = "GenAI Engineer"
        genuine_titles = [
            "Artificial Intelligence Consultant",
            "Generative AI Consultant",
            "AI Developer",
            "AI Solutions Engineer",
            "AI Solutions Architect",
            "Prompt Engineer",
            "AI Architect",
            "AI Platform Engineer",
            "Staff Software Engineer, AI",
            "AI Research Engineer",
            "Applied AI Engineer",
            "Machine Learning Engineer",
            "LLM Engineer",
            "RAG Engineer",
        ]
        for title in genuine_titles:
            status, score, reason = match_title_relevance(req_title, title)
            assert status == MatchStatus.MATCH, f"Expected MATCH for '{title}', got {status} ({reason})"
            assert score >= 0.8, f"Expected score >= 0.8 for '{title}', got {score}"

    def test_non_ai_roles_are_disqualified_for_genai_query(self):
        req_title = "GenAI Engineer"
        irrelevant_titles = [
            "Tier III Service Desk Engineer",
            "Inside Sales Contractor",
            "Senior Shopify Developer",
            "DevOps Engineer",
            "Cybersecurity Analyst",
            "Quality Assurance Engineer",
            "HR Recruiter",
        ]
        for title in irrelevant_titles:
            status, score, reason = match_title_relevance(req_title, title)
            assert status == MatchStatus.MISMATCH, f"Expected MISMATCH for '{title}', got {status} ({reason})"
            assert score == 0.0

    def test_general_query_with_stop_words_matches_role_terms(self):
        # Queries composed mostly of stop words like "Lead Engineer"
        status, score, reason = match_title_relevance("Lead Engineer", "Senior Lead Engineer - Backend")
        assert status == MatchStatus.MATCH
        assert score >= 0.5


class TestRegionalLocationMatching:
    def test_middle_east_and_mena_encompass_saudi(self):
        status, reason = match_location("Saudi Arabia", "Remote - MENA")
        assert status == MatchStatus.MATCH
        assert "encompasses" in reason.lower() or "region" in reason.lower()

        status, reason = match_location("Saudi Arabia", "Middle East")
        assert status == MatchStatus.MATCH

        status, reason = match_location("Saudi Arabia", "GCC")
        assert status == MatchStatus.MATCH

    def test_regional_request_matches_member_countries(self):
        status, reason = match_location("Middle East", "Riyadh, Saudi Arabia")
        assert status == MatchStatus.MATCH

        status, reason = match_location("GCC", "Dubai, UAE")
        assert status == MatchStatus.MATCH

    def test_foreign_locations_still_mismatch(self):
        status, reason = match_location("Saudi Arabia", "London, UK")
        assert status == MatchStatus.MISMATCH

        status, reason = match_location("Saudi Arabia", "San Francisco, USA")
        assert status == MatchStatus.MISMATCH


class TestJobQualificationAndScoring:
    def test_disqualified_job_receives_score_cap(self):
        job = {
            "title": "Tier III Service Desk Engineer",
            "location": "Riyadh, Saudi Arabia",
        }
        req = JobSearchRequest(
            job_title="GenAI Engineer",
            locations=["Saudi Arabia"],
        )
        res = qualify_job(job, req)
        assert res.qualified is False
        assert res.title_match == MatchStatus.MISMATCH
        assert res.score <= 0.20

    def test_ai_consultant_qualifies(self):
        job = {
            "title": "Artificial Intelligence Consultant",
            "location": "Riyadh, Saudi Arabia",
        }
        req = JobSearchRequest(
            job_title="GenAI Engineer",
            locations=["Saudi Arabia"],
        )
        res = qualify_job(job, req)
        assert res.qualified is True
        assert res.title_match == MatchStatus.MATCH
        assert res.score >= 0.70
