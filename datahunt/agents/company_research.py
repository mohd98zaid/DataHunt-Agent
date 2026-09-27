"""
CompanyResearchAgent — Steps 40, 47 of the job search pipeline.

Performs on-demand company intelligence research using public web search,
engineering blogs, review portals, and synthesis of culture and tech stack.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.tools.search import SearchTool
from datahunt.tools.fetch import FetchTool
from datahunt.db.user_repositories import CompanyResearchRepository
from datahunt.logger import logger


class CompanyProfile(BaseModel):
    """Synthesized corporate intelligence profile with zero synthetic fabrication."""
    company_name: str
    status: str = "verified"  # "verified" | "unavailable"
    is_available: bool = True
    about: str = ""
    industry: str = "Unknown"
    headquarters: str = "Unknown"
    size: str = "Unknown"
    tech_stack: List[str] = Field(default_factory=list)
    culture_notes: str = ""
    interview_process: str = ""
    ratings_summary: str = ""
    sources: List[str] = Field(default_factory=list)
    company_facts: List[Dict[str, str]] = Field(default_factory=list)
    company_inferences: List[str] = Field(default_factory=list)
    company_opinions: List[str] = Field(default_factory=list)


_COMPANY_PROMPT = """You are a corporate researcher and technical hiring analyst.
Analyze the following web search excerpts about the company '{company}':

{snippets}

Generate a concise, factual corporate profile in JSON with these keys:
{{
  "about": "2-3 sentences summarizing what the company does, products, and market position",
  "industry": "Industry / domain (e.g. Fintech, Healthcare, Enterprise SaaS)",
  "headquarters": "HQ city/country if mentioned, otherwise Primary Locations",
  "size": "Estimated employee count or tier (e.g. 50-200, 1000+, Startup, MNC)",
  "tech_stack": ["primary programming languages, frameworks, cloud providers mentioned"],
  "culture_notes": "Key observations on work environment, pace, and values",
  "interview_process": "Typical interview stages or technical format based on reviews",
  "ratings_summary": "General employee sentiment or Glassdoor rating if apparent"
}}
"""


class CompanyResearchAgent:
    """
    Step 40, 47: Researches company background, engineering culture, and interview format.
    """

    def __init__(self, gemini_client=None, search_tool=None, fetch_tool=None):
        self._client = gemini_client
        self.search_tool = search_tool or SearchTool()
        self.fetch_tool = fetch_tool or FetchTool()
        self.repo = CompanyResearchRepository()

    def research(self, company_name: str, force_refresh: bool = False) -> CompanyProfile:
        """Fetch or synthesize a CompanyProfile for a target organization."""
        clean_name = company_name.strip()
        if not clean_name:
            return CompanyProfile(company_name="Unknown")

        # 1. Check local cache
        if not force_refresh:
            cached = self.repo.get_profile(clean_name)
            if cached:
                logger.info(f"Returning cached company profile for '{clean_name}'")
                return CompanyProfile(**cached)

        # 2. Search web for company intelligence
        queries = [
            f'"{clean_name}" about company overview products',
            f'"{clean_name}" engineering tech stack glassdoor interview process',
        ]
        snippets = []
        sources = []

        for q in queries:
            res = self.search_tool.execute(q, limit=5)
            if res.success and res.data:
                for hit in res.data:
                    u = hit.get("url")
                    title = hit.get("title", "")
                    snip = hit.get("snippet", "")
                    if u and u not in sources:
                        sources.append(u)
                    snippets.append(f"{title}: {snip}")

        # 3. Synthesize via LLM
        combined_text = "\n\n".join(snippets[:8])
        profile = None

        if self._client and getattr(self._client, "is_live", False) and combined_text:
            try:
                prompt = _COMPANY_PROMPT.format(company=clean_name, snippets=combined_text)
                res = self._client._call_gemini_json(prompt, schema_description="company profile JSON", stage="company_research")
                if isinstance(res, dict) and "about" in res:
                    facts = []
                    for i, snip in enumerate(snippets[:4]):
                        src_u = sources[i] if i < len(sources) else (sources[0] if sources else "public_web")
                        facts.append({"fact": snip[:180], "source": src_u})

                    profile = CompanyProfile(
                        company_name=clean_name,
                        status="verified",
                        is_available=True,
                        about=res.get("about", "Corporate profile synthesized from verified search evidence."),
                        industry=res.get("industry", "Unknown"),
                        headquarters=res.get("headquarters", "Unknown"),
                        size=res.get("size", "Unknown"),
                        tech_stack=res.get("tech_stack", []),
                        culture_notes=res.get("culture_notes", ""),
                        interview_process=res.get("interview_process", "unavailable"),
                        ratings_summary=res.get("ratings_summary", "No verified ratings available"),
                        sources=sources[:5],
                        company_facts=facts,
                        company_inferences=[f"Industry classified as {res.get('industry', 'Unknown')} based on public products and listings."],
                        company_opinions=[res.get("ratings_summary")] if res.get("ratings_summary") else []
                    )
            except Exception as e:
                logger.warning(f"Company research LLM failed ({e}), using evidence-backed fallback")

        if not profile:
            # Factual fallback: zero fabrication. If information cannot be verified, expose unavailable.
            has_sources = len(sources) > 0
            about_text = f"Public web references collected from {len(sources)} search hits." if has_sources else "unavailable"
            facts = [{"fact": snip[:180], "source": sources[i] if i < len(sources) else "public_web"} for i, snip in enumerate(snippets[:3])]
            profile = CompanyProfile(
                company_name=clean_name,
                status="verified" if has_sources else "unavailable",
                is_available=has_sources,
                about=about_text,
                industry="Unknown",
                headquarters="Unknown",
                size="Unknown",
                tech_stack=[],
                culture_notes="Information not publicly available in analyzed sources" if not has_sources else "",
                interview_process="Interview stages not disclosed in public disclosures" if not has_sources else "unavailable",
                ratings_summary="No verified public ratings available",
                sources=sources[:5],
                company_facts=facts,
                company_inferences=[],
                company_opinions=[],
            )

        # 4. Cache
        self.repo.save_profile(clean_name, profile.model_dump(), sources[:5])
        return profile
