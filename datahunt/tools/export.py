import csv
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import openpyxl

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.models import ExportRecord, ExtractedRecord, ResearchRun
from datahunt.policy import escape_csv_formula
from datahunt.tools.base import ToolResult

class ExportTool:
    name = "export_results"
    description = "Export verified records to CSV, JSON, or XLSX with provenance and formula injection defense."

    def __init__(self, export_dir: Optional[Path] = None):
        self.export_dir = export_dir or settings.EXPORT_DIR
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        run_id: str,
        records: List[ExtractedRecord],
        format: str = "json",
        run_metadata: Optional[Dict[str, Any]] = None,
        columns: Optional[List[str]] = None
    ) -> ToolResult:
        format = format.lower()
        if format not in ("json", "csv", "xlsx"):
            return ToolResult(
                success=False,
                error_code=ErrorCode.INVALID_REQUEST.value,
                error_message=f"Unsupported export format: {format}"
            )

        storage_key = f"export_{uuid.uuid4().hex}"
        file_name = f"{storage_key}.{format}"
        target_path = self.export_dir / file_name

        meta = run_metadata or {}
        meta["export_timestamp"] = datetime.now(timezone.utc).isoformat()
        meta["record_count"] = len(records)
        meta["run_id"] = run_id

        # Determine column list from records or provided columns
        if not columns:
            col_set = set()
            for r in records:
                col_set.update(r.fields.keys())
            # Put standard job fields first if present
            preferred_order = ["title", "company", "location", "posted_at", "application_url"]
            columns = [c for c in preferred_order if c in col_set] + sorted([c for c in col_set if c not in preferred_order])
            if not columns:
                columns = ["title", "company", "location", "posted_at", "application_url"]

        try:
            temp_fd, temp_path_str = tempfile.mkstemp(suffix=f".{format}", dir=str(self.export_dir))
            os.close(temp_fd)
            temp_path = Path(temp_path_str)

            if format == "json":
                self._write_json(temp_path, records, meta)
            elif format == "csv":
                self._write_csv(temp_path, records, columns, meta)
            elif format == "xlsx":
                self._write_xlsx(temp_path, records, columns, meta)

            # Compute SHA-256
            sha256_hash = hashlib.sha256()
            with open(temp_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256_hash.update(chunk)
            file_hash = sha256_hash.hexdigest()

            # Atomic rename
            temp_path.replace(target_path)

            export_record = ExportRecord(
                run_id=run_id,
                format=format,
                file_name=file_name,
                storage_key=str(target_path),
                sha256=file_hash,
                row_count=len(records)
            )

            logger.info(f"Export created successfully: {file_name} ({len(records)} rows, sha256: {file_hash[:8]}...)")
            return ToolResult(
                success=True,
                data={
                    "export_id": export_record.id,
                    "file_name": file_name,
                    "file_path": str(target_path),
                    "row_count": len(records),
                    "sha256": file_hash,
                    "format": format
                }
            )
        except Exception as e:
            logger.error(f"Failed to export results for run {run_id}: {e}")
            return ToolResult(
                success=False,
                error_code=ErrorCode.EXPORT_FAILED.value,
                error_message=f"Export generation failed: {e}"
            )

    def _write_json(self, path: Path, records: List[ExtractedRecord], meta: Dict[str, Any]) -> None:
        data = {
            "metadata": meta,
            "records": [
                {
                    "id": r.id,
                    "fields": r.fields,
                    "canonical_url": r.canonical_url,
                    "verification_status": r.verification_status.value,
                    "confidence": r.confidence,
                    "evidence": [e.model_dump() for e in r.evidence],
                    "warnings": r.warnings,
                }
                for r in records
            ]
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _write_csv(self, path: Path, records: List[ExtractedRecord], columns: List[str], meta: Dict[str, Any]) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            # Header
            header = columns + ["source_url", "verification_status"]
            writer.writerow(header)
            for r in records:
                row = []
                for col in columns:
                    val = r.fields.get(col)
                    row.append(escape_csv_formula(val))
                row.append(escape_csv_formula(r.canonical_url or ""))
                row.append(escape_csv_formula(r.verification_status.value))
                writer.writerow(row)

        # Write sidecar metadata JSON
        sidecar_path = path.with_suffix(".meta.json")
        with open(sidecar_path, "w", encoding="utf-8") as sf:
            json.dump(meta, sf, indent=2)

    def _write_xlsx(self, path: Path, records: List[ExtractedRecord], columns: List[str], meta: Dict[str, Any]) -> None:
        wb = openpyxl.Workbook()
        
        # Sheet 1: Results
        ws_data = wb.active
        ws_data.title = "Results"
        headers = columns + ["source_url", "verification_status"]
        ws_data.append(headers)
        
        for r in records:
            row = []
            for col in columns:
                val = r.fields.get(col)
                row.append(str(val) if val is not None else "")
            row.append(r.canonical_url or "")
            row.append(r.verification_status.value)
            ws_data.append(row)

        # Sheet 2: Metadata & Provenance
        ws_meta = wb.create_sheet(title="Metadata")
        ws_meta.append(["Property", "Value"])
        for k, v in meta.items():
            ws_meta.append([str(k), str(v)])

        wb.save(path)
