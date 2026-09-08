import json
import tempfile
from pathlib import Path
import pytest
from datahunt.tools.export import ExportTool
from datahunt.models import ExtractedRecord, RecordEvidence, VerificationStatus

@pytest.fixture
def temp_export_dir():
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)

def test_export_json_and_sha256(temp_export_dir):
    rec = ExtractedRecord(
        id="r1",
        run_id="run_1",
        canonical_url="https://example.com/job",
        fields={"title": "AI Engineer", "company": "Tech Corp"},
        verification_status=VerificationStatus.VERIFIED,
        evidence=[RecordEvidence(record_id="r1", source_document_id="d1", field_name="title", evidence_text="AI Eng")]
    )
    tool = ExportTool(export_dir=temp_export_dir)
    res = tool.execute(run_id="run_1", records=[rec], format="json", run_metadata={"query": "AI Jobs"})
    
    assert res.success is True
    file_path = Path(res.data["file_path"])
    assert file_path.exists()
    assert res.data["sha256"] is not None
    assert len(res.data["sha256"]) == 64

    content = json.loads(file_path.read_text(encoding="utf-8"))
    assert content["metadata"]["query"] == "AI Jobs"
    assert len(content["records"]) == 1
    assert content["records"][0]["fields"]["title"] == "AI Engineer"

def test_export_csv_formula_injection(temp_export_dir):
    rec = ExtractedRecord(
        id="r1",
        run_id="run_1",
        fields={"title": "=cmd|' /C calc'!A1", "company": "@dangerous"},
        verification_status=VerificationStatus.VERIFIED
    )
    tool = ExportTool(export_dir=temp_export_dir)
    res = tool.execute(run_id="run_1", records=[rec], format="csv")
    assert res.success is True
    file_path = Path(res.data["file_path"])
    csv_text = file_path.read_text(encoding="utf-8-sig")
    assert "'=cmd|" in csv_text
    assert "'@dangerous" in csv_text

def test_export_xlsx(temp_export_dir):
    rec = ExtractedRecord(
        id="r1",
        run_id="run_1",
        fields={"title": "AI Researcher", "company": "Labs"},
        verification_status=VerificationStatus.VERIFIED
    )
    tool = ExportTool(export_dir=temp_export_dir)
    res = tool.execute(run_id="run_1", records=[rec], format="xlsx", run_metadata={"run": "test"})
    assert res.success is True
    file_path = Path(res.data["file_path"])
    assert file_path.exists()
    assert file_path.suffix == ".xlsx"
