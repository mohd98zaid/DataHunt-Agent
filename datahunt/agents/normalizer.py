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
    source: str = ""
    source_job_id: Optional[str] = None

    # Job Details
    title: str = ""
    normalized_title: str = ""
    company: str = ""
    normalized_company: str = ""
    location: str = ""
    normalized_location: str = ""
    city: Optional[str] = None
    country: Optional[str] = None
    remote_status: str = "onsite"  # remote | hybrid | onsite | unknown
    remote: Optional[bool] = None

    # Compensation
    salary_raw: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_min_annual: Optional[float] = None
    salary_max_annual: Optional[float] = None
    salary_currency: str = "USD"
    salary_period: str = "annual"  # annual | monthly | hourly

    # Requirements
    experience_min: Optional[int] = None
    experience_max: Optional[int] = None
    experience_min_years: Optional[int] = None
    experience_max_years: Optional[int] = None
    skills: List[str] = Field(default_factory=list)
    qualifications: List[str] = Field(default_factory=list)
    responsibilities: List[str] = Field(default_factory=list)
    employment_type: str = "full_time"
    description: Optional[str] = None

    # Links & Multi-source provenance
    job_url: str = ""
    apply_url: Optional[str] = None
    company_url: Optional[str] = None
    primary_application_url: Optional[str] = None
    all_source_urls: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)

    # Freshness & Status
    posted_at: Optional[str] = None
    posted_date: Optional[str] = None
    posted_age_seconds: Optional[int] = None
    freshness_label: str = "Recently"
    closing_date: Optional[str] = None
    updated_at: Optional[str] = None
    discovered_at: Optional[str] = None
    is_active: bool = True

    # Match / Qualification info
    match_reason: Optional[str] = None
    matched_skills: List[str] = Field(default_factory=list)
    match_level: Optional[str] = None
    relevance_score: float = 0.0

    # Provenance
    confidence: float = 0.8
    raw_fields: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.experience_min is not None and self.experience_min_years is None:
            self.experience_min_years = self.experience_min
        elif self.experience_min_years is not None and self.experience_min is None:
            self.experience_min = self.experience_min_years

        if self.experience_max is not None and self.experience_max_years is None:
            self.experience_max_years = self.experience_max
        elif self.experience_max_years is not None and self.experience_max is None:
            self.experience_max = self.experience_max_years

        if self.salary_min is not None and self.salary_min_annual is None:
            self.salary_min_annual = self.salary_min
        elif self.salary_min_annual is not None and self.salary_min is None:
            self.salary_min = self.salary_min_annual

        if self.salary_max is not None and self.salary_max_annual is None:
            self.salary_max_annual = self.salary_max
        elif self.salary_max_annual is not None and self.salary_max is None:
            self.salary_max = self.salary_max_annual

        if not self.job_url and self.canonical_url:
            self.job_url = self.canonical_url
        elif not self.canonical_url and self.job_url:
            self.canonical_url = self.job_url

        if not self.posted_at and self.posted_date:
            self.posted_at = self.posted_date
        elif not self.posted_date and self.posted_at:
            self.posted_date = self.posted_at

        if not self.primary_application_url:
            self.primary_application_url = self.apply_url or self.canonical_url or self.job_url or None

        if self.remote is None and self.remote_status:
            self.remote = self.remote_status in ("remote", "hybrid")

        if not self.sources and self.source:
            self.sources = [self.source]

        if not self.all_source_urls and (self.canonical_url or self.job_url):
            u = self.canonical_url or self.job_url
            if u:
                self.all_source_urls = [u]


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

        # Resolve source name and domain
        src_name = raw_fields.get("source_name") or raw_fields.get("source") or ""
        src_dom = raw_fields.get("domain") or raw_fields.get("source_domain") or ""
        if not src_name:
            if src_dom:
                src_name = src_dom.split(".")[0].title()
            else:
                src_name = "Public Web"

        job_u = canonical_url or raw_fields.get("job_url") or raw_fields.get("source_url") or ""
        app_u = raw_fields.get("apply_url") or raw_fields.get("application_url") or None
        comp_u = raw_fields.get("company_url") or raw_fields.get("website_url") or None
        desc = raw_fields.get("description") or None

        city = None
        country = None
        if norm_loc:
            parts = [p.strip() for p in norm_loc.split(",") if p.strip()]
            if len(parts) >= 2:
                city = parts[0]
                country = parts[-1]
            elif len(parts) == 1:
                city = parts[0]

        return NormalizedJob(
            raw_id=record_id,
            job_id=str(raw_fields.get("job_id") or ""),
            source_job_id=str(raw_fields.get("source_job_id") or raw_fields.get("job_id") or ""),
            canonical_url=job_u,
            job_url=job_u,
            source_url=raw_fields.get("source_url") or job_u,
            source_domain=src_dom,
            source=src_name,
            apply_url=app_u,
            company_url=comp_u,
            description=desc,
            title=title,
            normalized_title=norm_title,
            company=company,
            normalized_company=norm_company,
            location=loc,
            normalized_location=norm_loc,
            city=city,
            country=country,
            remote_status=remote_status,
            remote=remote_status in ("remote", "hybrid"),
            salary_raw=salary_raw or None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_min_annual=salary_min,
            salary_max_annual=salary_max,
            salary_currency=currency,
            salary_period=period,
            experience_min=exp_min,
            experience_max=exp_max,
            experience_min_years=exp_min,
            experience_max_years=exp_max,
            skills=clean_skills,
            employment_type=str(raw_fields.get("employment_type") or "full_time"),
            posted_date=posted_date,
            posted_at=posted_date,
            posted_age_seconds=posted_age_seconds,
            freshness_label=freshness_label,
            sources=[src_name] if src_name else [],
            all_source_urls=[job_u] if job_u else [],
            primary_application_url=app_u or job_u or None,
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
