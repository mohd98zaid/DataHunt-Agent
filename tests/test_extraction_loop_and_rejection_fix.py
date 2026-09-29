import pytest
from unittest.mock import MagicMock
from datahunt.models import ExtractedRecord, SourceDocument, VerificationStatus
from datahunt.agent.state import AgentState
from datahunt.agent.runtime import AgentRuntime
from datahunt.tools.extract import extract_job_records_from_document


def test_extraction_loop_prevents_duplicate_processing_across_iterations():
    """Verify that documents are extracted exactly once across multiple fetch/extract cycles."""
    extract_tool = MagicMock()
    
    # Doc 1 yields 1 record; Doc 2 yields 0 records; Doc 3 yields 1 record
    def fake_extract(doc, spec, run_id):
        if doc.id == "doc_abc1":
            return MagicMock(
                success=True,
                data=[
                    ExtractedRecord(
                        id="rec_1",
                        run_id=run_id,
                        source_document_id=doc.id,
                        record_type="job_listing",
                        fields={"title": "GenAI Engineer", "company": "TechCo", "location": "Dubai, UAE"},
                    )
                ]
            )
        elif doc.id == "doc_abc2":
            return MagicMock(success=True, data=[])
        elif doc.id == "doc_abc3":
            return MagicMock(
                success=True,
                data=[
                    ExtractedRecord(
                        id="rec_2",
                        run_id=run_id,
                        source_document_id=doc.id,
                        record_type="job_listing",
                        fields={"title": "Prompt Engineer", "company": "Corp", "location": "Riyadh, Saudi Arabia"},
                    )
                ]
            )
        return MagicMock(success=True, data=[])

    extract_tool.execute.side_effect = fake_extract

    runtime = AgentRuntime(
        client=MagicMock(),
        search=MagicMock(),
        fetch=MagicMock(),
        extract=extract_tool,
        verify=MagicMock(),
        dedupe=MagicMock(),
        export=MagicMock(),
    )
    state = AgentState(
        request="Find GenAI Engineer jobs in Saudi or UAE",
        run_id="run_test_1",
        task_id="task_test_1",
        mode="jobs",
    )

    doc1 = SourceDocument(
        id="doc_abc1",
        run_id="run_test_1",
        requested_url="https://jobs.lever.co/techco/123",
        domain="lever.co",
        extracted_text="# GenAI Engineer\nLocation: Dubai, UAE\nApply: https://jobs.lever.co/techco/123",
    )
    doc2 = SourceDocument(
        id="doc_abc2",
        run_id="run_test_1",
        requested_url="https://company.com/empty",
        domain="company.com",
        extracted_text="Terms of service and privacy policy with no job postings.",
    )

    # Iteration 1: doc1 and doc2 fetched
    state.fetched_docs.extend([doc1, doc2])
    runtime._act_extract(state, lambda evt, data: None)

    # First extraction should process both documents
    assert "doc_abc1" in state.extracted_doc_ids
    assert "doc_abc2" in state.extracted_doc_ids
    assert len(state.raw_records) == 1
    assert extract_tool.execute.call_count == 2

    # Iteration 2: Add doc3 (doc1 and doc2 remain in state.fetched_docs)
    doc3 = SourceDocument(
        id="doc_abc3",
        run_id="run_test_1",
        requested_url="https://boards.greenhouse.io/corp/jobs/456",
        domain="greenhouse.io",
        extracted_text="# Prompt Engineer\nLocation: Riyadh, Saudi Arabia\nApply: https://boards.greenhouse.io/corp/jobs/456",
    )
    state.fetched_docs.append(doc3)

    runtime._act_extract(state, lambda evt, data: None)

    # doc1 and doc2 must NOT be re-extracted! Only doc3 should be processed
    assert "doc_abc3" in state.extracted_doc_ids
    # extract.execute was called only 3 times in total (1 for doc1, 1 for doc2, 1 for doc3)
    assert extract_tool.execute.call_count == 3
    assert len(state.raw_records) == 2


def test_distinct_roles_filters_unrelated_jobs_by_topic():
    """Verify that career pages with headings for unrelated roles do not extract non-topic jobs."""
    doc_text = """
# Open Positions at GlobalCorp

## Senior Accountant
Location: Dubai, UAE
Salary: $5,000 / mo
Manage books and tax returns.

## HR Specialist
Location: Riyadh, Saudi Arabia
Handle recruitment and employee relations.

## Generative AI Engineer
Location: Dubai, UAE
Build LLM systems and RAG pipelines.
"""
    doc_meta = {
        "url": "https://jobs.lever.co/globalcorp/open-roles",
        "domain": "lever.co",
        "title": "Open Positions at GlobalCorp",
    }

    # When searching for GenAI Engineer:
    records = extract_job_records_from_document(
        doc_text=doc_text,
        doc_metadata=doc_meta,
        topic="GenAI Engineer in Saudi or UAE",
        geography="Saudi or UAE",
    )

    # Should only extract the GenAI Engineer, NOT Accountant or HR Specialist
    titles = [
        (r.get("fields", {}).get("title") or r.get("title", ""))
        for r in records
    ]
    assert any("AI" in t or "Generative" in t for t in titles)
    assert not any("Accountant" in t for t in titles)
    assert not any("HR" in t for t in titles)


def test_runtime_finalize_includes_duplicate_records_count():
    """Verify that _finalize returns records_duplicate and doesn't conflate duplicates with rejections."""
    dedupe_mock = MagicMock()
    runtime = AgentRuntime(
        client=MagicMock(),
        search=MagicMock(),
        fetch=MagicMock(),
        extract=MagicMock(),
        verify=MagicMock(),
        dedupe=dedupe_mock,
        export=MagicMock(),
    )
    state = AgentState(
        request="GenAI Engineer",
        run_id="run_test_2",
        task_id="task_test_2",
        mode="jobs",
    )
    rec1 = ExtractedRecord(
        id="rec_test_unique_1",
        run_id="run_test_2",
        record_type="job_listing",
        fields={"title": "GenAI Engineer", "company": "TechCorp", "location": "Dubai"},
        verification_status=VerificationStatus.VERIFIED,
        confidence=0.95,
    )
    rec2 = ExtractedRecord(
        id="rec_test_dup_2",
        run_id="run_test_2",
        record_type="job_listing",
        fields={"title": "GenAI Engineer", "company": "TechCorp", "location": "Dubai"},
        verification_status=VerificationStatus.DUPLICATE,
        confidence=0.90,
    )
    state.verified_records = [rec1]
    state.duplicate_records = [rec2]
    state.qualified_records = [rec1]

    # Dedupe returns unique_records = [rec1]
    dedupe_mock.execute.return_value = MagicMock(
        success=True,
        data={"unique_records": [rec1]}
    )

    res = runtime._finalize(state, lambda e, d: None)
    assert res["records_verified"] == 1
    assert res["records_duplicate"] == 1
    assert "records_duplicate" in res
