from typing import Any, Dict, List, Optional
from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.policy import is_public_business_email
from datahunt.tools.base import ToolResult
from datahunt.llm.gemini_client import GeminiClient

GEO_SYNONYMS = {
    "uae": ["uae", "united arab emirates", "dubai", "abu dhabi", "sharjah", "ajman", "al ain", "ras al khaimah", "middle east", "mena", "gcc", "ae"],
    "dubai": ["dubai", "uae", "united arab emirates", "abu dhabi", "sharjah", "ajman", "al ain", "ras al khaimah", "middle east", "mena", "gcc", "difc", "internet city", "silicon oasis", "business bay", "jlt", "ae"],
    "saudi": ["saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom", "middle east", "mena", "gcc", "sa"],
    "saudi arabia": ["saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom", "middle east", "mena", "gcc", "sa"],
    "ksa": ["saudi", "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "khobar", "neom", "middle east", "mena", "gcc", "sa"],
    "india": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "gurgaon", "noida", "in"],
    "uk": ["uk", "united kingdom", "london", "england", "scotland", "manchester", "birmingham", "gb"],
    "london": ["london", "uk", "united kingdom", "england"],
    "us": ["us", "usa", "united states", "america", "san francisco", "new york", "ca", "ny", "seattle", "austin", "boston", "california"],
    "usa": ["us", "usa", "united states", "america", "san francisco", "new york", "ca", "ny", "seattle", "austin", "boston", "california"],
    "singapore": ["singapore", "sg"],
    "germany": ["germany", "berlin", "munich", "frankfurt", "de"],
    "canada": ["canada", "toronto", "vancouver", "montreal", "ca"]
}

def check_geographic_compliance(job_location: Optional[str], requested_geo: Optional[str]) -> bool:
    if not requested_geo or not str(requested_geo).strip():
        return True
    if not job_location or not str(job_location).strip():
        return True

    geo_lower = str(requested_geo).lower().strip()
    loc_lower = str(job_location).lower().strip()

    # Remote / Worldwide / Anywhere is always compliant
    if any(rem in loc_lower for rem in ("remote", "anywhere", "worldwide", "global", "unspecified")):
        return True

    # Support composite queries like "Saudi or UAE", "Dubai and Riyadh"
    allowed_terms = set()
    parts = [p.strip() for p in geo_lower.replace(" or ", ",").replace(" and ", ",").replace("/", ",").split(",") if p.strip()]
    for p in parts:
        terms = GEO_SYNONYMS.get(p)
        if terms:
            allowed_terms.update(terms)
        else:
            allowed_terms.add(p)

    if any(term in loc_lower for term in allowed_terms):
        return True

    # If location explicitly contains a conflicting major hub outside allowed terms, it's a conflict
    KNOWN_HUBS = {
        "san francisco", "new york", "london", "singapore", "dubai", "seattle",
        "austin", "boston", "toronto", "berlin", "chicago", "los angeles", "florida", "tokyo", "paris", "riyadh"
    }
    if any(hub in loc_lower for hub in KNOWN_HUBS if hub not in allowed_terms):
        return False

    return True

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
        contact_policy: str = "business_public_only",
        geography_rule: Optional[str] = None
    ) -> ToolResult:
        req_fields = required_fields or ["title", "company"]
        
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

        # 3. Check geographic compliance if rule is provided
        if geography_rule and geography_rule.strip():
            loc = fields.get("location")
            if loc and not check_geographic_compliance(loc, geography_rule):
                record.warnings.append(f"Location '{loc}' does not match requested geography '{geography_rule}'")
                field_checks.append({
                    "field": "location",
                    "status": "mismatch",
                    "reason": f"Location '{loc}' conflicts with requested geography '{geography_rule}'"
                })
                is_verified = False

        # 4. Check job listing validity (reject login portals, directory roots, aggregator titles)
        if record.record_type == "job_listing":
            t_lower = str(fields.get("title") or "").strip().lower()
            c_lower = str(fields.get("company") or "").strip().lower()
            if any(lp in t_lower for lp in ("sign in", "log in", "login", "mygreenhouse")):
                record.warnings.append("Job title is a login portal page")
                is_verified = False
            elif t_lower and c_lower and (t_lower == c_lower or t_lower.replace(" ", "") == c_lower.replace(" ", "")):
                record.warnings.append("Job title matches company name (directory root)")
                is_verified = False

        if is_verified:
            record.verification_status = VerificationStatus.VERIFIED
            record.confidence = 0.95
        else:
            record.verification_status = VerificationStatus.NEEDS_REVIEW
            record.confidence = 0.45

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
