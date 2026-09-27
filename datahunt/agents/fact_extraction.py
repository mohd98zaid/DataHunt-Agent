"""
Fact Extraction Agent.

Transforms unstructured source text into strongly-typed, atomic Fact objects.
Never ranks or recommends.
Preserves numerical quantities and original currency/measurement units
(e.g., ₹ crore, USD million, %, headcount) without silent conversions.
"""
import re
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from datahunt.models.shared_intel import Fact
from datahunt.logger import logger


# Pattern definitions for financial, corporate, and career facts
_CURRENCY_UNIT_PATTERN = (
    r"(?:₹|rs\.?|inr|\$|usd|€|eur)\s*[\d,]+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|mn|million|bn|billion)?"
)

_FINANCIAL_PATTERNS = [
    (
        "revenue",
        re.compile(r"(?:revenue|sales|topline|turnover)\s*(?:of|was|is|reached|at)?\s*((?:[₹\$€]|usd|inr|eur|rs\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|million|mn|billion|bn|usd|inr)?)", re.I),
    ),
    (
        "net_profit",
        re.compile(r"(?:net profit|pat|net income|profit after tax)\s*(?:of|was|is|reached|at)?\s*((?:[₹\$€]|usd|inr|eur|rs\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|million|mn|billion|bn|usd|inr)?)", re.I),
    ),
    (
        "operating_margin",
        re.compile(r"(?:operating margin|ebitda margin|ebit margin)\s*(?:of|was|is|at)?\s*([\d,]+(?:\.\d+)?\s*%)|([\d,]+(?:\.\d+)?\s*%)\s*(?:operating margin|ebitda margin|ebit margin)", re.I),
    ),
    (
        "market_cap",
        re.compile(r"(?:market cap(?:italization)?)\s*(?:of|was|is|at)?\s*((?:[₹\$€]|usd|inr|eur|rs\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|million|mn|billion|bn|usd|inr)?)", re.I),
    ),
]

_CORP_PATTERNS = [
    (
        "employee_count",
        re.compile(r"(?:headcount|employees|workforce|staff)\s*(?:of|is|was|exceeds|approx\.?|total)?\s*([\d,]+)\+?", re.I),
    ),
    (
        "headquarters",
        re.compile(r"(?:headquartered in|headquarters in|based in)\s+([A-Za-z\s,]+?)(?:\.|\;|\n|$)", re.I),
    ),
    (
        "founded_year",
        re.compile(r"(?:founded in|established in|incorporated in)\s+(\d{4})", re.I),
    ),
]

_JOB_PATTERNS = [
    (
        "experience_years",
        re.compile(r"(\d+(?:\s*-\s*\d+)?)\s*(?:\+)?\s*(?:years?|yrs?)(?:\s+of)?\s+experience", re.I),
    ),
    (
        "salary",
        re.compile(r"(?:salary|compensation|package|ctc)\s*(?:of|is|:)?\s*((?:[₹\$€]|usd|inr|eur|rs\.?)?\s*[\d,]+(?:\.\d+)?\s*(?:-\s*[\d,]+(?:\.\d+)?)?\s*(?:lpa|k|per annum|a year|\/yr)?)", re.I),
    ),
]


def _extract_unit(raw_match: str) -> Optional[str]:
    raw_lower = raw_match.lower()
    if "cr" in raw_lower or "crore" in raw_lower:
        return "₹ crore" if ("₹" in raw_match or "rs" in raw_lower or "inr" in raw_lower) else "crore"
    if "lakh" in raw_lower:
        return "₹ lakh" if ("₹" in raw_match or "rs" in raw_lower or "inr" in raw_lower) else "lakh"
    if "billion" in raw_lower or "bn" in raw_lower:
        return "USD billion" if ("$" in raw_match or "usd" in raw_lower) else "billion"
    if "million" in raw_lower or "mn" in raw_lower:
        return "USD million" if ("$" in raw_match or "usd" in raw_lower) else "million"
    if "%" in raw_lower:
        return "%"
    if "lpa" in raw_lower:
        return "LPA"
    if "$" in raw_match or "usd" in raw_lower:
        return "USD"
    if "₹" in raw_match or "inr" in raw_lower or "rs" in raw_lower:
        return "INR"
    return None


class FactExtractionAgent:
    """
    Extracts atomic, verifiable facts from source text without interpretation or ranking.
    """
    def __init__(self, gemini_client: Optional[Any] = None):
        self.client = gemini_client

    def extract_facts(
        self,
        text: str,
        entity_id: str,
        source_url: str = "",
        domain: str = "general",
        published_at: Optional[str] = None,
    ) -> List[Fact]:
        """
        Parses text for quantitative and qualitative facts associated with entity_id.
        """
        if not text:
            return []

        facts: List[Fact] = []
        patterns = []

        if domain in ("market", "finance", "company"):
            patterns.extend(_FINANCIAL_PATTERNS)
            patterns.extend(_CORP_PATTERNS)
        elif domain in ("jobs", "career"):
            patterns.extend(_JOB_PATTERNS)
            patterns.extend(_CORP_PATTERNS)
        else:
            patterns.extend(_FINANCIAL_PATTERNS)
            patterns.extend(_CORP_PATTERNS)
            patterns.extend(_JOB_PATTERNS)

        for field_name, regex in patterns:
            for match in regex.finditer(text):
                val_str = (match.group(1) or match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1) or "").strip()
                if not val_str:
                    continue
                unit = _extract_unit(val_str)
                # Store fact
                fact = Fact(
                    entity_id=entity_id,
                    field=field_name,
                    value=val_str,
                    unit=unit,
                    source=source_url,
                    source_url=source_url,
                    published_at=published_at,
                    confidence=0.90,
                    raw_text=match.group(0),
                )
                facts.append(fact)

        logger.info(f"FactExtractionAgent: Extracted {len(facts)} facts for entity {entity_id}")
        return facts
