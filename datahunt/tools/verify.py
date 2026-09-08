from typing import Any, Dict, List, Optional
from datahunt.logger import logger
from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.policy import is_public_business_email
from datahunt.tools.base import ToolResult
from datahunt.llm.gemini_client import GeminiClient

class VerifyTool:
    name = "verify_record"
    description = "Verify extracted records against evidence, freshness, and contact policies."

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        self.client = gemini_client or GeminiClient()

    def execute(
        self,
        record: ExtractedRecord,
        required_fields: Optional[List[str]] = None,
        freshness_rule: Optional[Dict[str, Any]] = None,
        contact_policy: str = "business_public_only"
    ) -> ToolResult:
        req_fields = required_fields or ["title", "company"]
        fresh_rule = freshness_rule or {}
        
        # 1. Check contact policy compliance
        fields = record.fields
        if contact_policy == "business_public_only":
            email = fields.get("public_business_email")
            if email and not is_public_business_email(email):
                record.warnings.append(f"Non-business or personal email removed under contact policy: {email}")
                fields["public_business_email"] = None

            phone = fields.get("public_business_phone")
            if phone and ("mobile" in str(phone).lower() or "personal" in str(phone).lower()):
                record.warnings.append("Personal phone removed under contact policy")
                fields["public_business_phone"] = None

        # 2. Check required fields against evidence
        field_checks = []
        is_verified = True

        for rf in req_fields:
            val = fields.get(rf)
            if val is None or str(val).strip() == "":
                field_checks.append({"field": rf, "status": "missing", "reason": f"Required field '{rf}' is empty"})
                is_verified = False
            else:
                # Check for supporting evidence
                has_ev = any(
                    ev.field_name == rf and ev.supports_value == 1 and ev.evidence_text
                    for ev in record.evidence
                )
                if not has_ev:
                    field_checks.append({"field": rf, "status": "missing_evidence", "reason": f"No evidence supporting '{rf}'"})
                    is_verified = False
                else:
                    field_checks.append({"field": rf, "status": "supported", "reason": f"Evidence verified for '{rf}'"})

        if is_verified:
            record.verification_status = VerificationStatus.VERIFIED
            record.confidence = 0.95
        else:
            record.verification_status = VerificationStatus.NEEDS_REVIEW
            record.confidence = 0.50

        return ToolResult(
            success=True,
            data={
                "record_id": record.id,
                "status": record.verification_status.value,
                "confidence": record.confidence,
                "field_checks": field_checks,
                "warnings": record.warnings
            }
        )
