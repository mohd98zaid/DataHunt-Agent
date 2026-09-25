from typing import Any, Dict, List
from datahunt.logger import logger
from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.tools.base import ToolResult

class DedupeTool:
    name = "deduplicate_records"
    description = "Deterministic multi-tier record deduplication preserving all supporting evidence."

    def execute(self, records: List[ExtractedRecord]) -> ToolResult:
        if not records:
            return ToolResult(success=True, data={"unique_records": [], "duplicates_count": 0})

        canonical_map: Dict[str, ExtractedRecord] = {}
        duplicates: List[ExtractedRecord] = []
        clusters: Dict[str, List[str]] = {}

        for record in records:
            # Prioritize composite entity identity key (company::title::location)
            # This prevents collapsing distinct jobs, products, or concepts discovered on the same page
            key = None
            if record.identity_key and record.identity_key.strip():
                key = f"ident::{record.identity_key.strip().lower()}"
            elif record.canonical_url and record.canonical_url.strip():
                key = f"url::{record.canonical_url.strip().lower()}"
            else:
                key = f"id::{record.id}"

            if key in canonical_map:
                primary = canonical_map[key]
                record.verification_status = VerificationStatus.DUPLICATE
                duplicates.append(record)
                
                # Merge supporting evidence from duplicate into primary record
                existing_ev_keys = {
                    (e.field_name, e.source_document_id, e.evidence_text)
                    for e in primary.evidence
                }
                for ev in record.evidence:
                    ev_key = (ev.field_name, ev.source_document_id, ev.evidence_text)
                    if ev_key not in existing_ev_keys:
                        # Re-point record_id to primary
                        ev.record_id = primary.id
                        primary.evidence.append(ev)
                        existing_ev_keys.add(ev_key)

                clusters[primary.id].append(record.id)
                logger.info(f"Collapsed duplicate record {record.id} into canonical {primary.id} under key: {key}")
            else:
                canonical_map[key] = record
                clusters[record.id] = [record.id]

        unique_records = list(canonical_map.values())
        return ToolResult(
            success=True,
            data={
                "unique_records": unique_records,
                "duplicates_count": len(duplicates),
                "clusters": clusters
            }
        )
