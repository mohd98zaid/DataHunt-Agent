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
