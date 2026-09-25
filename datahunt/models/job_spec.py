"""
Canonical JobSearchSpec model for DataHunt job discovery engine.
Single canonical representation of a job-search request across the entire pipeline.
Provides complete backward compatibility with JobSearchRequest.
"""
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# Geographic normalization mapping
GEO_CANONICAL_NAMES = {
    "saudi": "Saudi Arabia",
    "saudi arabia": "Saudi Arabia",
    "ksa": "Saudi Arabia",
    "riyadh": "Riyadh",
    "jeddah": "Jeddah",
    "dammam": "Dammam",
    "khobar": "Khobar",
    "neom": "NEOM",
    "mecca": "Mecca",
    "medina": "Medina",
    "uae": "UAE",
    "united arab emirates": "UAE",
    "emirates": "UAE",
    "dubai": "Dubai",
    "abu dhabi": "Abu Dhabi",
    "sharjah": "Sharjah",
    "ajman": "Ajman",
    "ras al khaimah": "Ras Al Khaimah",
    "fujairah": "Fujairah",
    "uk": "UK",
    "united kingdom": "UK",
    "england": "England",
    "london": "London",
    "us": "USA",
    "usa": "USA",
    "united states": "USA",
    "india": "India",
    "bangalore": "Bangalore",
    "bengaluru": "Bengaluru",
}


def normalize_geo_name(name: str) -> str:
    """Normalize geographical names and aliases to canonical names."""
    if not name or not isinstance(name, str):
        return ""
    clean = name.strip()
    return GEO_CANONICAL_NAMES.get(clean.lower(), clean.title() if len(clean) > 3 else clean.upper())


class JobSearchSpec(BaseModel):
    """
    Canonical representation of a job-search request.
    Consumed by all job-search components in the pipeline.
    """
    raw_query: str = ""

    titles: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)

    locations: List[str] = Field(default_factory=list)
    location_operator: str = "OR"  # "OR" | "AND"

    experience_min: Optional[int] = None
    experience_max: Optional[int] = None

    explicit_skills: List[str] = Field(default_factory=list)
    inferred_skills: List[str] = Field(default_factory=list)

    remote: Optional[bool] = None

    employment_types: List[str] = Field(default_factory=lambda: ["full_time"])

    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = "USD"

    freshness_days: Optional[int] = 30

    excluded_titles: List[str] = Field(default_factory=list)
    excluded_companies: List[str] = Field(default_factory=list)

    max_results: int = 50

    requested_sources: Optional[List[str]] = None

    # Backward compatibility fields for JobSearchRequest callers
    job_title: Optional[str] = None
    alternative_titles: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    cities: List[str] = Field(default_factory=list)
    remote_status: Optional[str] = "any"
    remote_allowed: Optional[bool] = None
    employment_type: Optional[str] = "full_time"
    industry: Optional[str] = None
    company_prefs: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_question: Optional[str] = None

    @field_validator("salary_currency", mode="before")
    @classmethod
    def _validate_currency(cls, v):
        return v if (v and isinstance(v, str) and v.strip()) else "USD"

    @field_validator("remote_status", mode="before")
    @classmethod
    def _validate_remote_status(cls, v):
        return v if (v and isinstance(v, str) and v.strip()) else "any"

    @field_validator("employment_type", mode="before")
    @classmethod
    def _validate_emp_type(cls, v):
        return v if (v and isinstance(v, str) and v.strip()) else "full_time"

    @field_validator(
        "titles", "keywords", "locations", "explicit_skills", "inferred_skills",
        "employment_types", "excluded_titles", "excluded_companies",
        "alternative_titles", "skills", "cities", "company_prefs", "missing_fields",
        mode="before"
    )
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item).strip() for item in v if item is not None and str(item).strip()]
        s = str(v).strip()
        return [s] if s else []

    def model_post_init(self, __context: Any) -> None:
        """Synchronize canonical and compatibility fields."""
        # 1. Sync titles and job_title / alternative_titles
        if self.job_title and self.job_title.strip():
            jt = self.job_title.strip()
            if jt not in self.titles:
                self.titles.insert(0, jt)
        elif self.titles:
            self.job_title = self.titles[0]
        else:
            self.job_title = ""

        if self.alternative_titles:
            for at in self.alternative_titles:
                if at not in self.titles:
                    self.titles.append(at)
        elif len(self.titles) > 1:
            self.alternative_titles = self.titles[1:]

        # 2. Sync locations and location string
        if self.locations:
            clean_locs = []
            for l in self.locations:
                nl = normalize_geo_name(l)
                if nl and nl not in clean_locs:
                    clean_locs.append(nl)
            self.locations = clean_locs
            if not self.location:
                sep = " or " if self.location_operator.upper() == "OR" else ", "
                self.location = sep.join(self.locations)
        elif self.location:
            loc_str = self.location.strip()
            if " or " in loc_str.lower():
                self.location_operator = "OR"
                parts = [p.strip() for p in re.split(r'\s+or\s+', loc_str, flags=re.IGNORECASE) if p.strip()]
                self.locations = [normalize_geo_name(p) for p in parts]
            elif " and " in loc_str.lower():
                self.location_operator = "AND"
                parts = [p.strip() for p in re.split(r'\s+and\s+', loc_str, flags=re.IGNORECASE) if p.strip()]
                self.locations = [normalize_geo_name(p) for p in parts]
            else:
                self.locations = [normalize_geo_name(loc_str)]

        if not self.cities and self.locations:
            self.cities = list(self.locations)

        # 3. Sync skills: explicit_skills and skills
        if self.skills and not self.explicit_skills:
            self.explicit_skills = list(self.skills)
        elif self.explicit_skills and not self.skills:
            self.skills = list(self.explicit_skills)

        # 4. Sync remote & remote_allowed & remote_status
        if self.remote is not None:
            if self.remote_allowed is None:
                self.remote_allowed = self.remote
            if not self.remote_status or self.remote_status == "any":
                self.remote_status = "remote" if self.remote else "onsite"
        elif self.remote_status:
            r_low = self.remote_status.lower()
            if r_low == "remote":
                self.remote = True
                self.remote_allowed = True
            elif r_low == "onsite":
                self.remote = False
                self.remote_allowed = False
            else:
                self.remote = None
                self.remote_allowed = True
        else:
            self.remote_allowed = True

        # 5. Sync employment_types
        if not self.employment_types and self.employment_type:
            self.employment_types = [self.employment_type]
        elif self.employment_types and not self.employment_type:
            self.employment_type = self.employment_types[0]

        # 6. Auto-detect regional currency if location is provided and default USD (unless user explicitly specified USD/$)
        has_explicit_usd = any(sig in (self.raw_query or "").lower() for sig in ("$", "usd", "dollar", "bucks"))
        if not has_explicit_usd and self.location and (not self.salary_currency or self.salary_currency == "USD"):
            loc_low = self.location.lower()
            if any(k in loc_low for k in ("saudi", "riyadh", "jeddah", "dammam", "ksa")):
                self.salary_currency = "SAR"
            elif any(k in loc_low for k in ("uae", "dubai", "abu dhabi", "sharjah", "emirates")):
                self.salary_currency = "AED"
            elif any(k in loc_low for k in ("india", "bangalore", "mumbai", "delhi", "pune", "hyderabad")):
                self.salary_currency = "INR"
            elif any(k in loc_low for k in ("uk", "london", "england", "united kingdom")):
                self.salary_currency = "GBP"
            elif any(k in loc_low for k in ("germany", "france", "berlin", "paris", "europe")):
                self.salary_currency = "EUR"

    def to_search_hint(self) -> str:
        """Compact string representation for search engines."""
        parts = [self.titles[0] if self.titles else (self.job_title or "Engineer")]
        if self.locations:
            parts.append(f"in {' or '.join(self.locations)}")
        elif self.location:
            parts.append(f"in {self.location}")
        if self.remote:
            parts.append("remote")
        if self.experience_min is not None and self.experience_max is not None:
            parts.append(f"{self.experience_min}-{self.experience_max} years")
        elif self.experience_min is not None:
            parts.append(f"{self.experience_min}+ years")
        if self.explicit_skills:
            parts.append(", ".join(self.explicit_skills[:4]))
        return " ".join(parts)


# Type alias for complete backward compatibility
JobSearchRequest = JobSearchSpec
