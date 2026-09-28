import pytest
from datahunt.tools.dedupe import DedupeTool
from datahunt.models import ExtractedRecord, RecordEvidence, VerificationStatus

def test_dedupe_by_canonical_url_and_evidence_merge():
    ev1 = RecordEvidence(record_id="r1", source_document_id="doc1", field_name="title", evidence_text="ML Engineer")
    ev2 = RecordEvidence(record_id="r2", source_document_id="doc2", field_name="title", evidence_text="ML Engineer Listing 2")

    rec1 = ExtractedRecord(
        id="r1",
        run_id="run_1",
        canonical_url="https://company.com/job/100",
        fields={"title": "ML Engineer", "company": "AI Co"},
        evidence=[ev1]
    )
    rec2 = ExtractedRecord(
        id="r2",
        run_id="run_1",
        canonical_url="https://company.com/job/100",
        fields={"title": "ML Engineer", "company": "AI Co"},
        evidence=[ev2]
    )

    tool = DedupeTool()
    res = tool.execute([rec1, rec2])
    assert res.success is True
    unique = res.data["unique_records"]
    assert len(unique) == 1
    assert res.data["duplicates_count"] == 1
    # Evidence from both documents must be retained on canonical record
    primary = unique[0]
    assert len(primary.evidence) == 2
    ev_texts = {e.evidence_text for e in primary.evidence}
    assert "ML Engineer" in ev_texts
    assert "ML Engineer Listing 2" in ev_texts

def test_dedupe_distinct_records():
    rec1 = ExtractedRecord(
        id="r1",
        run_id="run_1",
        canonical_url="https://company.com/job/1",
        identity_key="aico::ml::dubai::20260901",
        fields={"title": "ML Engineer"}
    )
    rec2 = ExtractedRecord(
        id="r2",
        run_id="run_1",
        canonical_url="https://company.com/job/2",
        identity_key="other::dev::remote::20260901",
        fields={"title": "Backend Developer"}
    )
    tool = DedupeTool()
    res = tool.execute([rec1, rec2])
    assert len(res.data["unique_records"]) == 2
    assert res.data["duplicates_count"] == 0


def test_dedupe_does_not_collapse_distinct_jobs_on_same_board():
    """Distinct job titles on the same ATS company board must NEVER collapse into each other."""
    rec1 = ExtractedRecord(
        id="rec_sw_ai",
        run_id="run_1",
        canonical_url="https://boards.greenhouse.io/devrev",
        identity_key="devrev::software engineer applied ai::remote",
        fields={"title": "Software Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5722574004"}
    )
    rec2 = ExtractedRecord(
        id="rec_fwd_ai",
        run_id="run_1",
        canonical_url="https://boards.greenhouse.io/devrev",
        identity_key="devrev::forward deployed engineer applied ai::tokyo",
        fields={"title": "Forward Deployed Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5837052004"}
    )
    rec3 = ExtractedRecord(
        id="rec_cust_eng",
        run_id="run_1",
        canonical_url="https://boards.greenhouse.io/devrev",
        identity_key="devrev::customer engineer::argentina",
        fields={"title": "Customer Engineer", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5837053004"}
    )

    tool = DedupeTool()
    res = tool.execute([rec1, rec2, rec3])
    assert res.success is True
    unique = res.data["unique_records"]
    assert len(unique) == 3
    assert res.data["duplicates_count"] == 0
    unique_titles = {r.fields["title"] for r in unique}
    assert "Software Engineer - Applied AI" in unique_titles
    assert "Forward Deployed Engineer - Applied AI" in unique_titles
    assert "Customer Engineer" in unique_titles


def test_dedupe_collapses_identical_jobs_on_same_board():
    """Repeated scrapes of the exact same position must collapse to one canonical record."""
    rec1 = ExtractedRecord(
        id="rec_sw_1",
        run_id="run_1",
        canonical_url="https://boards.greenhouse.io/devrev",
        identity_key="devrev::software engineer applied ai::remote",
        fields={"title": "Software Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5722574004"}
    )
    rec2 = ExtractedRecord(
        id="rec_sw_2",
        run_id="run_1",
        canonical_url="https://boards.greenhouse.io/devrev",
        identity_key="devrev::software engineer applied ai::remote",
        fields={"title": "Software Engineer - Applied AI", "company": "Devrev", "application_url": "https://job-boards.greenhouse.io/devrev/jobs/5722574004"}
    )

    tool = DedupeTool()
    res = tool.execute([rec1, rec2])
    assert res.success is True
    assert len(res.data["unique_records"]) == 1
    assert res.data["duplicates_count"] == 1
    assert rec2.verification_status == VerificationStatus.DUPLICATE
    assert res.data["unique_records"][0].id == "rec_sw_1"


def test_is_board_or_directory_url():
    from datahunt.tools.dedupe import is_board_or_directory_url

    # Board roots — MUST return True
    assert is_board_or_directory_url("https://boards.greenhouse.io/devrev") is True
    assert is_board_or_directory_url("https://jobs.lever.co/supabase") is True
    assert is_board_or_directory_url("https://jobs.ashbyhq.com/openai") is True
    assert is_board_or_directory_url("https://company.com/careers") is True
    assert is_board_or_directory_url("https://company.com/jobs") is True
    assert is_board_or_directory_url("") is True
    assert is_board_or_directory_url(None) is True

    # Specific job postings — MUST return False
    assert is_board_or_directory_url("https://job-boards.greenhouse.io/devrev/jobs/5722574004") is False
    assert is_board_or_directory_url("https://boards.greenhouse.io/devrev?gh_jid=5722574004") is False
    assert is_board_or_directory_url("https://jobs.lever.co/supabase/3c1d9b32-8417-4f93-b684-21396a581452") is False
    assert is_board_or_directory_url("https://jobs.ashbyhq.com/openai/4c264211-5e26-4448-9366-24ba0c634458") is False
    assert is_board_or_directory_url("https://apply.workable.com/resend/j/849B3120AA/") is False


def test_are_titles_compatible():
    from datahunt.tools.dedupe import are_titles_compatible

    assert are_titles_compatible("Software Engineer - Applied AI", "Software Engineer - Applied AI") is True
    assert are_titles_compatible("Senior ML Engineer", "ML Engineer") is True
    assert are_titles_compatible("Customer Engineer", "Software Engineer - Applied AI") is False
    assert are_titles_compatible("EMEA Lead Sales Engineering", "Junior Forward Deployed Engineer") is False
    assert are_titles_compatible("Accountant", "Staff AI Engineer") is False

