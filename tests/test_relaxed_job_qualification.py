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


class TestJobDeletionAndClonePurge:
    def test_delete_job_purges_all_duplicate_snapshots(self):
        import json
        import uuid
        from datetime import datetime, timezone
        from datahunt.db import get_connection, JobTrackingRepository, TaskRepository, RunRepository
        from datahunt.models import ResearchTask, ResearchRun, ResearchSpec

        task_repo = TaskRepository()
        run_repo = RunRepository()
        task = ResearchTask(request_text="Clone purge test", normalized_spec=ResearchSpec(topic="Jobs"))
        task_repo.create_task(task)
        run1 = ResearchRun(task_id=task.id)
        run_repo.create_run(run1)
        run2 = ResearchRun(task_id=task.id)
        run_repo.create_run(run2)

        repo = JobTrackingRepository()
        conn = get_connection()
        now_str = datetime.now(timezone.utc).isoformat()

        # Insert 3 duplicate clone snapshots of the exact same job across 2 runs
        # Different IDs, same normalized title + company, same canonical_url
        c1 = f"rec_clone_{uuid.uuid4().hex[:8]}"
        c2 = f"rec_clone_{uuid.uuid4().hex[:8]}"
        c3 = f"rec_clone_{uuid.uuid4().hex[:8]}"

        for rec_id, r_id, conf in [(c1, run1.id, 0.8), (c2, run1.id, 0.9), (c3, run2.id, 0.85)]:
            conn.execute(
                """INSERT OR REPLACE INTO extracted_records 
                   (id, run_id, record_type, canonical_url, fields_json, confidence, verification_status, created_at, updated_at)
                   VALUES (?, ?, 'job_listing', ?, ?, ?, 'verified', ?, ?);""",
                (rec_id, r_id, "https://careers.google.com/jobs/results/12345", json.dumps({"title": "Staff AI Engineer", "company": "Google Cloud", "location": "Remote"}), conf, now_str, now_str)
            )
        conn.commit()
        conn.close()

        # list_jobs() should return exactly 1 deduplicated entry for this job
        jobs = repo.list_jobs(limit=100)
        matching = [
            j for j in jobs
            if j["fields"].get("title") == "Staff AI Engineer" and j["fields"].get("company") == "Google Cloud"
        ]
        assert len(matching) == 1
        visible_id = matching[0]["id"]

        # Delete using the visible ID
        deleted = repo.delete_job(visible_id)
        assert deleted is True

        # Now verify that NO clones remain in list_jobs() AND none in extracted_records
        jobs_after = repo.list_jobs(limit=100)
        matching_after = [
            j for j in jobs_after
            if j["fields"].get("title") == "Staff AI Engineer" and j["fields"].get("company") == "Google Cloud"
        ]
        assert len(matching_after) == 0

        conn = get_connection()
        remaining_rows = conn.execute(
            "SELECT count(*) as cnt FROM extracted_records WHERE id IN (?, ?, ?);",
            (c1, c2, c3)
        ).fetchone()["cnt"]
        conn.close()
        assert remaining_rows == 0


