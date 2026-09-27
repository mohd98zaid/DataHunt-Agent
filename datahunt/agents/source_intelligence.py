"""
Source Intelligence Agent.

Determines the optimal source universe and tailored search strategies based on
domain, entity type, geography, time horizon, and required facts.
Supports dynamic discovery of new source domains from search hits.
"""
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from datahunt.models.shared_intel import SourcePlan, SourceCategory, EntityType
from datahunt.logger import logger


class SourceIntelligenceAgent:
    """
    Formulates specialized, non-universal source plans tailored to the exact domain task.
    """
    def __init__(self, gemini_client: Optional[Any] = None):
        self.client = gemini_client

    def plan_sources(
        self,
        domain: str,
        query: str,
        entity_type: Optional[EntityType] = None,
        location: Optional[str] = None,
        time_horizon: Optional[str] = None,
        required_facts: Optional[List[str]] = None,
    ) -> SourcePlan:
        """
        Produce a tailored SourcePlan for the given domain and intent.
        """
        dom_norm = (domain or "general").lower().strip()
        loc_norm = (location or "").lower().strip()
        facts = required_facts or []

        primary_sources: List[str] = []
        secondary_sources: List[str] = []
        discovery_sources: List[str] = []
        source_types: List[SourceCategory] = []
        search_queries: List[str] = []

        if dom_norm in ("market", "stocks", "equity", "finance"):
            source_types = [
                SourceCategory.EXCHANGE,
                SourceCategory.COMPANY,
                SourceCategory.REGULATORY,
                SourceCategory.FINANCIAL_DATA,
                SourceCategory.NEWS,
            ]
            primary_sources = [
                "nseindia.com",
                "bseindia.com",
                "sebi.gov.in",
            ]
            secondary_sources = [
                "reuters.com",
                "bloomberg.com",
                "livemint.com",
                "business-standard.com",
            ]
            discovery_sources = [
                "sec.gov",
                "investor relations",
            ]
            # Formulate targeted queries
            search_queries.append(f"{query} NSE stock analysis")
            search_queries.append(f"{query} corporate announcements investor relations")
            search_queries.append(f"{query} financial filings quarterly results")

        elif dom_norm in ("jobs", "career", "employment", "hiring"):
            source_types = [
                SourceCategory.ATS,
                SourceCategory.COMPANY,
                SourceCategory.JOB_BOARD,
                SourceCategory.PROFESSIONAL_NETWORK,
            ]
            primary_sources = [
                "boards.greenhouse.io",
                "jobs.lever.co",
                "jobs.ashbyhq.com",
                "apply.workable.com",
                "smartrecruiters.com",
            ]
            secondary_sources = [
                "linkedin.com/jobs",
                "indeed.com",
            ]
            if "saudi" in loc_norm or "uae" in loc_norm or "dubai" in loc_norm or "riyadh" in loc_norm:
                secondary_sources.extend(["bayt.com", "naukrigulf.com", "gulftalent.com"])
            discovery_sources = ["careers page", "work with us"]
            search_queries.append(f"{query} site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com")
            search_queries.append(f"{query} careers")

        elif dom_norm in ("company", "corporate", "organization"):
            source_types = [
                SourceCategory.COMPANY,
                SourceCategory.OFFICIAL,
                SourceCategory.REGULATORY,
                SourceCategory.NEWS,
            ]
            primary_sources = [
                "official company website",
                "investor relations portal",
                "annual report",
            ]
            secondary_sources = [
                "sec.gov",
                "mca.gov.in",
                "reuters.com",
            ]
            discovery_sources = ["leadership team", "press releases"]
            search_queries.append(f"{query} official website overview")
            search_queries.append(f"{query} leadership business model products")

        elif dom_norm in ("people", "contacts", "recruiter", "talent"):
            source_types = [
                SourceCategory.PROFESSIONAL_NETWORK,
                SourceCategory.COMPANY,
                SourceCategory.OFFICIAL,
            ]
            primary_sources = [
                "company directory",
                "official team page",
                "linkedin.com/in",
            ]
            secondary_sources = [
                "company contact page",
                "author bio",
            ]
            discovery_sources = ["engineering blog authors", "conference speakers"]
            search_queries.append(f"{query} recruiter talent acquisition hiring manager")

        else:  # Technical research or general
            source_types = [
                SourceCategory.OFFICIAL,
                SourceCategory.PRIMARY,
                SourceCategory.RESEARCH,
                SourceCategory.NEWS,
            ]
            primary_sources = [
                "official documentation",
                "github.com",
                "arxiv.org",
                "rfc-editor.org",
            ]
            secondary_sources = [
                "engineering blog",
                "acm.org",
                "ieee.org",
            ]
            discovery_sources = ["tech benchmarks", "specification"]
            search_queries.append(f"{query} documentation specification")
            search_queries.append(f"{query} architecture design principles")

        plan = SourcePlan(
            domain=dom_norm,
            primary_sources=primary_sources,
            secondary_sources=secondary_sources,
            discovery_sources=discovery_sources,
            source_types=source_types,
            search_queries=search_queries,
        )
        logger.info(
            f"SourceIntelligenceAgent: Created plan for domain '{dom_norm}' with {len(primary_sources)} primary sources"
        )
        return plan

    def discover_sources_from_results(self, search_hits: List[Dict[str, Any]]) -> List[str]:
        """
        Dynamically extracts candidate source domains from raw search hits.
        """
        discovered = set()
        for hit in search_hits:
            url = hit.get("url") or hit.get("link") or ""
            if url and "://" in url:
                try:
                    netloc = urlparse(url).netloc.lower()
                    if netloc.startswith("www."):
                        netloc = netloc[4:]
                    if netloc and "." in netloc:
                        discovered.add(netloc)
                except Exception:
                    continue
        return sorted(list(discovered))
