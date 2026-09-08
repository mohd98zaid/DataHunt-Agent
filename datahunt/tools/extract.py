import re
from typing import Any, Dict, List, Optional
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.models import ExtractedRecord, RecordEvidence, ResearchSpec, SourceDocument
from datahunt.tools.base import ToolResult
from datahunt.llm.gemini_client import GeminiClient

def normalize_text_for_identity(text: Optional[str]) -> str:
    """Normalize text by lowercasing and stripping punctuation for dedupe matching."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()

class ExtractTool:
    name = "extract_records"
    description = "Extract evidence-backed records conforming to schema from a document."

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        self.client = gemini_client or GeminiClient()

    def execute(
        self,
        document: SourceDocument,
        spec: ResearchSpec,
        run_id: str
    ) -> ToolResult:
        if not document.extracted_text:
            return ToolResult(
                success=True,
                data=[],
                warnings=["Document contains no extracted text to process."]
            )

        doc_meta = {
            "id": document.id,
            "url": document.canonical_url or document.requested_url,
            "title": document.title,
            "domain": document.domain,
        }
        record_schema = {
            "fields": spec.requested_fields,
            "rules": spec.quality_bar
        }

        try:
            result = self.client.extract_from_document(
                spec=spec,
                doc_text=document.extracted_text,
                doc_metadata=doc_meta,
                record_schema=record_schema
            )
            if isinstance(result, list):
                raw_records = result
                doc_warnings = []
            elif isinstance(result, dict):
                raw_records = result.get("records", [])
                doc_warnings = result.get("document_warnings", [])
            else:
                raw_records = []
                doc_warnings = []
            extracted_objs = []

            for raw_rec in raw_records:
                fields = {}
                raw_ev = []
                warnings = raw_rec.get("warnings", [])
                confidence = raw_rec.get("record_confidence", 1.0)

                if "fields" in raw_rec and isinstance(raw_rec["fields"], dict):
                    fields = raw_rec["fields"]
                    raw_ev = raw_rec.get("field_evidence", [])
                else:
                    # Model returned fields directly on record object
                    for k, v in raw_rec.items():
                        if k in ("warnings", "record_confidence", "document_warnings", "field_evidence"):
                            continue
                        if isinstance(v, dict) and "value" in v:
                            fields[k] = v["value"]
                            ev_info = v.get("evidence")
                            if isinstance(ev_info, dict):
                                quote = ev_info.get("quote") or ev_info.get("evidence_text") or str(v["value"])
                                raw_ev.append({
                                    "field_name": k,
                                    "evidence_text": quote,
                                    "locator": ev_info.get("locator", {}),
                                    "supports_value": True
                                })
                        else:
                            fields[k] = v
                            if v is not None and str(v).strip():
                                raw_ev.append({
                                    "field_name": k,
                                    "evidence_text": str(v),
                                    "locator": {},
                                    "supports_value": True
                                })

                # Build normalized fields
                normalized = {
                    "company": normalize_text_for_identity(fields.get("company")),
                    "title": normalize_text_for_identity(fields.get("title")),
                    "location": normalize_text_for_identity(fields.get("location")),
                    "posted_at": fields.get("posted_at") or "",
                    "application_url": fields.get("application_url") or "",
                }

                # Construct composite identity key
                identity_key = f"{normalized['company']}::{normalized['title']}::{normalized['location']}::{normalized['posted_at']}"

                record_obj = ExtractedRecord(
                    run_id=run_id,
                    source_document_id=document.id,
                    canonical_url=document.canonical_url or document.requested_url,
                    identity_key=identity_key,
                    fields=fields,
                    normalized_fields=normalized,
                    confidence=confidence,
                    warnings=warnings
                )

                # Attach evidence items
                evidence_list = []
                for ev in raw_ev:
                    loc = ev.get("locator", {})
                    if isinstance(loc, str):
                        loc = {"value": loc}
                    elif not isinstance(loc, dict):
                        loc = {}
                    evidence_list.append(
                        RecordEvidence(
                            record_id=record_obj.id,
                            source_document_id=document.id,
                            field_name=ev.get("field_name", "unknown"),
                            evidence_text=ev.get("evidence_text"),
                            locator=loc,
                            supports_value=1 if ev.get("supports_value", True) else 0
                        )
                    )
                record_obj.evidence = evidence_list
                extracted_objs.append(record_obj)

            return ToolResult(
                success=True,
                data=extracted_objs,
                warnings=doc_warnings
            )
        except Exception as e:
            logger.error(f"Extraction failed for document {document.id}: {e}")
            return ToolResult(
                success=False,
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error_message=f"Extraction failed: {e}",
                data=[]
            )
