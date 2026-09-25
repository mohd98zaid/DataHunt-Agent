"""
DataNormalizer — Step 20-21 of the job search pipeline.

Normalizes heterogeneous extracted job posting fields into a canonical,
typed NormalizedJob data structure with unified salaries, experience ranges,
and standardized locations.
"""
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.agents.query_understanding import detect_local_currency


class NormalizedJob(BaseModel):
    """Canonical, strongly-typed representation of an extracted job posting."""
    # Identification
    raw_id: str = ""
    job_id: Optional[str] = None
    canonical_url: str = ""
    source_url: Optional[str] = None
    source_domain: str = ""

    # Job Details
    title: str = ""
    normalized_title: str = ""
    company: str = ""
    normalized_company: str = ""
    location: str = ""
    normalized_location: str = ""
    remote_status: str = "onsite"  # remote | hybrid | onsite | unknown

    # Compensation
    salary_raw: Optional[str] = None
    salary_min_annual: Optional[float] = None
    salary_max_annual: Optional[float] = None
    salary_currency: str = "USD"
    salary_period: str = "annual"  # annual | monthly | hourly

    # Requirements
    experience_min_years: Optional[int] = None
    experience_max_years: Optional[int] = None
    skills: List[str] = Field(default_factory=list)
    qualifications: List[str] = Field(default_factory=list)
    responsibilities: List[str] = Field(default_factory=list)
    employment_type: str = "full_time"

    # Freshness & Status
    posted_date: Optional[str] = None
    posted_age_seconds: Optional[int] = None
    freshness_label: str = "Recently"
    closing_date: Optional[str] = None
    is_active: bool = True

    # Provenance
    confidence: float = 0.8
    raw_fields: Dict[str, Any] = Field(default_factory=dict)


class DataNormalizer:
    """
    Step 20-21: Normalizes job fields into canonical NormalizedJob instances.
    """

    def normalize(self, raw_fields: Dict[str, Any], record_id: str = "", canonical_url: str = "") -> NormalizedJob:
        """Parse, clean, and standardize an extracted job dictionary."""
        title = str(raw_fields.get("title") or raw_fields.get("name") or "Job Opportunity").strip()
        company = str(raw_fields.get("company") or raw_fields.get("hiring_organization") or "Unknown Company").strip()
        loc = str(raw_fields.get("location") or "").strip()
        
        # 1. Normalize Company Name (strip common corporate legal suffixes)
        norm_company = re.sub(r'(?i)\b(inc|llc|ltd|pvt|limited|corp|corporation|gmbh|co)\b\.?', '', company)
        norm_company = re.sub(r'\s+', ' ', norm_company).strip(" .,-|") or company

        # 2. Normalize Title (clean prefixes, punctuation, levels)
        norm_title = re.sub(r'(?i)\b(senior|sr\.?|junior|jr\.?|lead|principal|staff|associate)\b', '', title)
        norm_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', norm_title)
        norm_title = re.sub(r'[-/|].*$', '', norm_title)
        norm_title = re.sub(r'\s+', ' ', norm_title).strip() or title

        # 3. Normalize Location and Remote Status
        remote_status = "onsite"
        loc_lower = loc.lower()
        title_lower = title.lower()
        combined_text = f"{title_lower} {loc_lower} {str(raw_fields.get('employment_type', '')).lower()}"

        if any(k in combined_text for k in ("remote", "wfh", "work from home", "anywhere", "virtual")):
            remote_status = "remote"
        elif "hybrid" in combined_text:
            remote_status = "hybrid"
        elif any(k in combined_text for k in ("onsite", "on-site", "in office", "office")):
            remote_status = "onsite"

        norm_loc = loc
        if "remote" in loc_lower and loc_lower != "remote":
            norm_loc = re.sub(r'(?i)\b(remote|wfh)\b', '', loc).strip(" ,-|")
        if not norm_loc and remote_status == "remote":
            norm_loc = "Remote"

        # 4. Normalize Experience
        exp_min, exp_max = None, None
        exp_str = str(raw_fields.get("experience") or raw_fields.get("experience_years") or "")
        exp_match = re.search(r'(\d+)\s*[-–to]+\s*(\d+)', exp_str)
        if exp_match:
            exp_min, exp_max = int(exp_match.group(1)), int(exp_match.group(2))
        else:
            exp_single = re.search(r'(\d+)\+?', exp_str)
            if exp_single:
                exp_min = int(exp_single.group(1))

        # 5. Normalize Salary
        salary_raw = str(raw_fields.get("salary") or "").strip()
        salary_min, salary_max, currency, period = self._parse_salary(salary_raw, location=norm_loc or loc)

        # 6. Normalize Skills
        raw_skills = raw_fields.get("skills", [])
        if isinstance(raw_skills, str):
            raw_skills = [s.strip() for s in raw_skills.split(",") if s.strip()]
        elif not isinstance(raw_skills, list):
            raw_skills = []
        clean_skills = list(dict.fromkeys([s.strip().title() for s in raw_skills if s.strip()]))

        # 7. Timestamps
        posted_date = raw_fields.get("posted_date")
        posted_age_seconds = raw_fields.get("posted_age_seconds")
        freshness_label = raw_fields.get("freshness_label") or "Recent"

        try:
            confidence_val = float(raw_fields.get("confidence") or 0.85)
        except (ValueError, TypeError):
            confidence_val = 0.85

        return NormalizedJob(
            raw_id=record_id,
            job_id=str(raw_fields.get("job_id") or ""),
            canonical_url=canonical_url or raw_fields.get("application_url") or "",
            source_url=raw_fields.get("source_url") or canonical_url,
            source_domain=raw_fields.get("domain") or "",
            title=title,
            normalized_title=norm_title,
            company=company,
            normalized_company=norm_company,
            location=loc,
            normalized_location=norm_loc,
            remote_status=remote_status,
            salary_raw=salary_raw or None,
            salary_min_annual=salary_min,
            salary_max_annual=salary_max,
            salary_currency=currency,
            salary_period=period,
            experience_min_years=exp_min,
            experience_max_years=exp_max,
            skills=clean_skills,
            employment_type=str(raw_fields.get("employment_type") or "full_time"),
            posted_date=posted_date,
            posted_age_seconds=posted_age_seconds,
            freshness_label=freshness_label,
            confidence=confidence_val,
            raw_fields=raw_fields
        )

    def _parse_salary(self, text: str, location: str = ""):
        """Parse compensation text into annualized numbers and local regional currency."""
        default_curr = detect_local_currency(location, default="USD") if location else "USD"
        if not text:
            return None, None, default_curr, "annual"

        currency = default_curr
        t_low = text.lower()
        if "₹" in text or "inr" in t_low or "lpa" in t_low or "lakh" in t_low:
            currency = "INR"
        elif "£" in text or "gbp" in t_low:
            currency = "GBP"
        elif "€" in text or "eur" in t_low:
            currency = "EUR"
        elif "aed" in t_low or "dirham" in t_low or "dhs" in t_low:
            currency = "AED"
        elif "sar" in t_low or "riyal" in t_low or "sr" in t_low:
            currency = "SAR"
        elif "qar" in t_low:
            currency = "QAR"
        elif "kwd" in t_low:
            currency = "KWD"
        elif "cad" in t_low or "c$" in t_low:
            currency = "CAD"
        elif "aud" in t_low or "a$" in t_low:
            currency = "AUD"
        elif "sgd" in t_low or "s$" in t_low:
            currency = "SGD"
        elif "$" in text or "usd" in t_low:
            currency = "USD"

        # Indian LPA parsing (e.g. "15 - 25 LPA")
        lpa_range = re.search(r'(\d+(?:\.\d+)?)\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakh|lac)', text, re.IGNORECASE)
        if lpa_range:
            return float(lpa_range.group(1)) * 100_000, float(lpa_range.group(2)) * 100_000, "INR", "annual"

        lpa_single = re.search(r'(\d+(?:\.\d+)?)\s*(?:lpa|lakh|lac)', text, re.IGNORECASE)
        if lpa_single:
            val = float(lpa_single.group(1)) * 100_000
            return val, val, "INR", "annual"

        # Monthly parsing for regional salaries (e.g. "15,000 - 25,000 AED / month" or "12k - 18k /mo")
        is_monthly = bool(re.search(r'(?:/\s*mo(?:nth)?|per\s*month|\bmonth(?:ly)?\b|p\.?m\.?)', t_low))
        multiplier = 12.0 if is_monthly else 1.0

        # Standard K/annual parsing (e.g. "120k - 160k" or "$120,000")
        k_range = re.search(r'[\$]?(\d+)\s*k?\s*[-–to]+\s*[\$]?(\d+)\s*k', text, re.IGNORECASE)
        if k_range:
            v1, v2 = float(k_range.group(1)), float(k_range.group(2))
            if v1 < 1000: v1 *= 1000
            if v2 < 1000: v2 *= 1000
            return v1 * multiplier, v2 * multiplier, currency, "monthly" if is_monthly else "annual"

        # Numbers range e.g. "15,000 - 25,000"
        num_range = re.search(r'(\d{1,3}(?:,\d{3})+|\d+)\s*[-–to]+\s*(\d{1,3}(?:,\d{3})+|\d+)', text)
        if num_range:
            v1 = float(num_range.group(1).replace(",", ""))
            v2 = float(num_range.group(2).replace(",", ""))
            return v1 * multiplier, v2 * multiplier, currency, "monthly" if is_monthly else "annual"

        # Single number e.g. "30,000 SAR / month" or "$120,000" or "80k"
        single_k = re.search(r'[$€£₹]?\s*(\d+(?:\.\d+)?)\s*k\b', text, re.IGNORECASE)
        if single_k:
            v = float(single_k.group(1)) * 1000.0
            return v * multiplier, v * multiplier, currency, "monthly" if is_monthly else "annual"

        single_num = re.search(r'(\d{1,3}(?:,\d{3})+|\b\d{4,}\b)', text)
        if single_num:
            v = float(single_num.group(1).replace(",", ""))
            return v * multiplier, v * multiplier, currency, "monthly" if is_monthly else "annual"

        return None, None, currency, "annual"
