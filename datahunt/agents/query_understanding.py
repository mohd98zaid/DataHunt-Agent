"""
QueryUnderstandingAgent — Step 3-7 of the job search pipeline.

Converts a raw natural-language query into a structured JobSearchRequest.
Identifies missing required fields and surfaces a clarification question when needed.
"""
import json
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from datahunt.logger import logger


# ─────────────────────────────────────────────
# Regional Currency Mapping & Detection
# ─────────────────────────────────────────────

LOCATION_TO_CURRENCY: Dict[str, str] = {
    # Middle East / GCC / MENA
    "saudi": "SAR", "saudi arabia": "SAR", "ksa": "SAR", "riyadh": "SAR", "jeddah": "SAR", "dammam": "SAR", "khobar": "SAR",
    "uae": "AED", "united arab emirates": "AED", "emirates": "AED", "dubai": "AED", "abu dhabi": "AED", "sharjah": "AED", "ajman": "AED",
    "qatar": "QAR", "doha": "QAR",
    "kuwait": "KWD", "kuwait city": "KWD",
    "bahrain": "BHD", "manama": "BHD",
    "oman": "OMR", "muscat": "OMR",
    "egypt": "EGP", "cairo": "EGP", "alexandria": "EGP",

    # South Asia
    "india": "INR", "bangalore": "INR", "bengaluru": "INR", "delhi": "INR", "new delhi": "INR", "ncr": "INR",
    "gurgaon": "INR", "gurugram": "INR", "noida": "INR", "mumbai": "INR", "pune": "INR", "hyderabad": "INR",
    "chennai": "INR", "kolkata": "INR", "ahmedabad": "INR",
    "pakistan": "PKR", "karachi": "PKR", "lahore": "PKR", "islamabad": "PKR",
    "bangladesh": "BDT", "dhaka": "BDT",
    "sri lanka": "LKR", "colombo": "LKR",

    # Europe & UK
    "uk": "GBP", "united kingdom": "GBP", "britain": "GBP", "great britain": "GBP", "england": "GBP", "scotland": "GBP",
    "wales": "GBP", "london": "GBP", "manchester": "GBP", "birmingham": "GBP", "edinburgh": "GBP",
    "germany": "EUR", "berlin": "EUR", "munich": "EUR", "frankfurt": "EUR", "hamburg": "EUR",
    "france": "EUR", "paris": "EUR", "lyon": "EUR",
    "netherlands": "EUR", "amsterdam": "EUR", "rotterdam": "EUR",
    "ireland": "EUR", "dublin": "EUR",
    "spain": "EUR", "madrid": "EUR", "barcelona": "EUR",
    "italy": "EUR", "milan": "EUR", "rome": "EUR",
    "switzerland": "CHF", "zurich": "CHF", "geneva": "CHF",
    "poland": "PLN", "warsaw": "PLN",
    "sweden": "SEK", "stockholm": "SEK",
    "norway": "NOK", "oslo": "NOK",
    "denmark": "DKK", "copenhagen": "DKK",

    # Americas
    "united states": "USD", "usa": "USD", "us": "USD", "america": "USD",
    "canada": "CAD", "toronto": "CAD", "vancouver": "CAD", "montreal": "CAD", "ottawa": "CAD", "calgary": "CAD",
    "brazil": "BRL", "mexico": "MXN",

    # Asia Pacific
    "singapore": "SGD",
    "australia": "AUD", "sydney": "AUD", "melbourne": "AUD", "brisbane": "AUD", "perth": "AUD",
    "new zealand": "NZD", "auckland": "NZD",
    "japan": "JPY", "tokyo": "JPY", "osaka": "JPY",
    "south korea": "KRW", "korea": "KRW", "seoul": "KRW",
    "hong kong": "HKD",
    "taiwan": "TWD",
    "malaysia": "MYR", "kuala lumpur": "MYR",
    "indonesia": "IDR", "jakarta": "IDR",
    "philippines": "PHP", "manila": "PHP",
    "thailand": "THB", "bangkok": "THB",
    "vietnam": "VND",
    "south africa": "ZAR",
}


def detect_local_currency(location: Optional[str], default: str = "USD") -> str:
    """
    Derive the regional / domestic currency code from a location string or country name.
    """
    if not location or not isinstance(location, str):
        return default

    loc_lower = location.lower()

    # 1. Check multi-word keys first (e.g. "saudi arabia", "united arab emirates", "united kingdom")
    for loc_key, curr in LOCATION_TO_CURRENCY.items():
        if " " in loc_key and loc_key in loc_lower:
            return curr

    # 2. Check token match
    tokens = re.findall(r'[a-zA-Z]+', loc_lower)
    for tok in tokens:
        if tok in LOCATION_TO_CURRENCY:
            return LOCATION_TO_CURRENCY[tok]

    return default


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

class ClarificationQuestion(BaseModel):
    """Returned when the query is missing required information."""
    question: str
    missing_fields: List[str]
    assumed_defaults: Dict[str, Any] = Field(default_factory=dict)


from datahunt.models.job_spec import JobSearchSpec, JobSearchRequest, normalize_geo_name, GEO_CANONICAL_NAMES

def _canonical_geo_name(name: str) -> str:
    """Normalize geographical aliases to canonical country/region names."""
    return normalize_geo_name(name)



# ─────────────────────────────────────────────
# LLM prompt
# ─────────────────────────────────────────────

_UNDERSTAND_PROMPT = """You are a job search query parser. Extract structured information from the user's job query.

User query: "{query}"

Return ONLY a valid JSON object with these fields:
{{
  "job_title": "primary job title (string, required)",
  "alternative_titles": ["list of alternative job titles"],
  "skills": ["list of technical/soft skills mentioned or implied"],
  "location": "city, country, or region (null if not mentioned)",
  "cities": ["list of specific cities if multiple mentioned"],
  "remote_status": "remote | hybrid | onsite | any",
  "experience_min": null or integer (minimum years),
  "experience_max": null or integer (maximum years),
  "salary_min": null or number (in local currency, annualised),
  "salary_max": null or number,
  "salary_currency": "INR | USD | GBP | AED | EUR | etc.",
  "employment_type": "full_time | contract | part_time | internship | any",
  "industry": null or string,
  "company_prefs": ["specific company names or types like startup/MNC"],
  "freshness_days": integer (default 30),
  "missing_fields": ["list of important fields that were not mentioned"],
  "clarification_needed": true or false,
  "clarification_question": null or string (one clear question to ask user if clarification_needed is true)
}}

Rules:
- job_title is REQUIRED. If absent, set clarification_needed=true and ask for it.
- If location is missing for a non-remote role, add "location" to missing_fields but do NOT block — set clarification_needed=false and proceed.
- Currency MUST be local: Set salary_currency to the regional local currency code of the target location (e.g. SAR for Saudi Arabia, AED for UAE/Dubai, INR for India, GBP for UK, EUR for Europe, CAD for Canada, SGD for Singapore, QAR for Qatar) unless the user explicitly requested a specific foreign currency (like USD).
- For Indian salary context: "15 LPA" = 1500000 INR annual, "15 lakhs" = 1500000.
- For "2-5 years experience": experience_min=2, experience_max=5.
- For "3+ years": experience_min=3, experience_max=null.
- Be liberal with alternative_titles: if role is "AI Engineer", include "ML Engineer", "Machine Learning Engineer", "Applied Scientist", etc.
- Extract skills even if only implied by the job title (e.g., "Python Engineer" implies Python).
- Only ask one clarification question. Prefer to proceed with reasonable defaults.
"""

_CLARIFY_PROMPT_TEMPLATE = """The user said: "{query}"

We asked: "{question}"

The user replied: "{answer}"

Update the original search request with the new information. Return a complete updated JSON with all fields from the original request, incorporating the user's answer. Use the same JSON schema as before."""


# ─────────────────────────────────────────────
# Deterministic fallback parser
# ─────────────────────────────────────────────

def _deterministic_parse(query: str) -> Dict[str, Any]:
    """Rule-based fallback when LLM is unavailable."""
    q_lower = query.lower()

    # Remote detection
    remote_status = "any"
    if any(k in q_lower for k in ("remote", "wfh", "work from home", "fully remote")):
        remote_status = "remote"
    elif "hybrid" in q_lower:
        remote_status = "hybrid"
    elif any(k in q_lower for k in ("onsite", "on-site", "on site", "office")):
        remote_status = "onsite"

    # Location & Multi-Region parsing
    location = None
    locations = []
    location_operator = "OR"
    loc_match = re.search(r'\b(?:in|at|for)\s+([A-Za-z\s,]+?)(?:\s+(?:with|having|paying|salary|\d+\+?\s*years?|\d+\s*[-–to]+)\b|\s*$)', query, re.IGNORECASE)
    if loc_match:
        raw_loc = loc_match.group(1).strip().rstrip(",")
        if " or " in raw_loc.lower():
            location_operator = "OR"
            parts = [p.strip() for p in re.split(r'\s+or\s+', raw_loc, flags=re.IGNORECASE) if p.strip()]
            locations = [_canonical_geo_name(p) for p in parts]
            location = " or ".join(locations)
        elif " and " in raw_loc.lower():
            location_operator = "AND"
            parts = [p.strip() for p in re.split(r'\s+and\s+', raw_loc, flags=re.IGNORECASE) if p.strip()]
            locations = [_canonical_geo_name(p) for p in parts]
            location = " and ".join(locations)
        else:
            locations = [_canonical_geo_name(raw_loc)]
            location = locations[0]
    else:
        # Check known location keys
        found_keys = []
        for key in LOCATION_TO_CURRENCY:
            if re.search(rf'\b{re.escape(key)}\b', q_lower):
                found_keys.append(_canonical_geo_name(key))
        if found_keys:
            locations = found_keys
            location = " or ".join(found_keys)

    # Experience
    exp_match = re.search(r'(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs?)', q_lower)
    exp_min, exp_max = None, None
    if exp_match:
        exp_min, exp_max = int(exp_match.group(1)), int(exp_match.group(2))
    else:
        exp_match2 = re.search(r'(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)', q_lower)
        if exp_match2:
            exp_min = int(exp_match2.group(1))

    # Salary & Regional Currency
    salary_min = None
    currency = detect_local_currency(location, default="USD")
    lpa_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|lac)', q_lower)
    k_match = re.search(r'[$€£₹]?\s*(\d+(?:\.\d+)?)\s*k\b', q_lower)
    if lpa_match:
        salary_min = float(lpa_match.group(1)) * 100_000
    elif k_match:
        salary_min = float(k_match.group(1)) * 1_000

    if any(sig in q_lower for sig in ("$", "usd", "dollar", "bucks")):
        currency = "USD"
    elif lpa_match or any(sig in q_lower for sig in ("inr", "₹")):
        currency = "INR"
    elif any(sig in q_lower for sig in ("sar", "riyal", "sr")):
        currency = "SAR"
    elif any(sig in q_lower for sig in ("aed", "dirham", "dhs")):
        currency = "AED"
    elif any(sig in q_lower for sig in ("gbp", "pound", "£")):
        currency = "GBP"
    elif any(sig in q_lower for sig in ("eur", "euro", "€")):
        currency = "EUR"

    # Employment type
    emp_type = "full_time"
    if "contract" in q_lower or "freelance" in q_lower:
        emp_type = "contract"
    elif "internship" in q_lower or "intern" in q_lower:
        emp_type = "internship"
    elif "part time" in q_lower or "part-time" in q_lower:
        emp_type = "part_time"

    # Job title — first meaningful noun phrase
    # Strip filler words and take first 3-4 words
    filler = re.sub(r'\b(find|search|get|me|latest|fresh|remote|hybrid|onsite|jobs?|openings?|roles?)\b', '', query, flags=re.IGNORECASE)
    filler = re.sub(r'\b(?:in|at|for)\s+.*?(?=\s+(?:with|having|paying|salary)\b|\s*$)', '', filler, flags=re.IGNORECASE)
    filler = re.sub(r'\bwith\s+.+', '', filler, flags=re.IGNORECASE)
    title = re.sub(r'\s+', ' ', filler).strip()[:60] or query[:60]

    return {
        "raw_query": query,
        "job_title": title,
        "alternative_titles": [],
        "skills": [],
        "explicit_skills": [],
        "inferred_skills": [],
        "location": location,
        "locations": locations,
        "location_operator": location_operator,
        "cities": locations if locations else ([location] if location else []),
        "remote_status": remote_status,
        "remote_allowed": remote_status in ("any", "remote", "hybrid"),
        "experience_min": exp_min,
        "experience_max": exp_max,
        "salary_min": salary_min,
        "salary_max": None,
        "salary_currency": currency,
        "employment_type": emp_type,
        "employment_types": [emp_type],
        "industry": None,
        "company_prefs": [],
        "freshness_days": 30,
        "missing_fields": [],
        "clarification_needed": False,
        "clarification_question": None,
    }


# ─────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────

class QueryUnderstandingAgent:
    """
    Step 3-7: Parse a natural-language job query into a structured JobSearchRequest.
    Uses Gemini for rich extraction; falls back to deterministic rules if LLM unavailable.
    """

    def __init__(self, gemini_client=None):
        self._client = gemini_client

    _deterministic_parse = staticmethod(_deterministic_parse)

    def understand(self, query: str) -> "JobSearchRequest | ClarificationQuestion":
        """
        Parse query → JobSearchRequest.
        Returns ClarificationQuestion only when job_title is completely absent.
        """
        parsed = self._llm_parse(query) if self._client and getattr(self._client, "is_live", False) else _deterministic_parse(query)

        cleaned = {}
        for k, v in parsed.items():
            if k in JobSearchRequest.model_fields:
                if v is None and k in ("salary_currency", "remote_status", "employment_type", "freshness_days"):
                    continue
                cleaned[k] = v

        cleaned["raw_query"] = query
        try:
            req = JobSearchRequest(**cleaned)
        except Exception as ex:
            logger.warning(f"Error constructing JobSearchRequest: {ex}. Using deterministic fallback.")
            det = _deterministic_parse(query)
            det["raw_query"] = query
            req = JobSearchRequest(**det)

        # Only hard-block if we truly have no job title
        if not req.job_title.strip() and req.clarification_needed:
            return ClarificationQuestion(
                question=req.clarification_question or "What job title or role are you looking for?",
                missing_fields=req.missing_fields or ["job_title"],
                assumed_defaults={}
            )

        return req

    def apply_clarification(self, original_query: str, question: str, answer: str) -> "JobSearchRequest":
        """
        Apply the user's answer to a clarification question and re-parse.
        Step 7: merge clarification answer into the request.
        """
        combined = f"{original_query}. {answer}"
        return self.understand(combined)

    def _llm_parse(self, query: str) -> Dict[str, Any]:
        """Call Gemini to extract structured fields from the query."""
        try:
            prompt = _UNDERSTAND_PROMPT.format(query=query)
            raw = self._client._call_gemini_json(prompt, schema_description="job search request JSON", stage="understand")
            if isinstance(raw, dict):
                return raw
        except Exception as e:
            logger.warning(f"QueryUnderstandingAgent LLM parse failed ({e}), using deterministic fallback")
        return _deterministic_parse(query)
