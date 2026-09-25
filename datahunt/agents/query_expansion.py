"""
QueryExpansionAgent — Step 8 of the job search pipeline.

Expands primary job titles, skills, and industry terminology into related
titles, search keywords, and negative keywords to ensure broad and deep recall.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.logger import logger
from datahunt.agents.query_understanding import JobSearchRequest


class ExpandedTitle(BaseModel):
    """A single expanded job title with provenance metadata."""
    title: str
    is_explicit: bool = False  # True only if the user typed this title themselves


class ExpandedQuery(BaseModel):
    """Result of expanding a JobSearchRequest with synonyms and related skills."""
    primary_title: str
    # Titles the user explicitly mentioned (from req.job_title / req.alternative_titles)
    explicit_titles: List[str] = Field(default_factory=list)
    # Titles inferred/expanded by taxonomy or LLM (is_explicit=False)
    inferred_titles: List[str] = Field(default_factory=list)
    # Flat merged list (explicit first) for backward-compatible consumers
    all_titles: List[str] = Field(default_factory=list)

    # Skills explicitly mentioned by the user → must satisfy
    must_have_skills: List[str] = Field(default_factory=list)
    # Skills inferred/expanded from context → nice to have but not required
    nice_to_have_skills: List[str] = Field(default_factory=list)

    search_keywords: List[str] = Field(default_factory=list)
    negative_keywords: List[str] = Field(default_factory=list)


# ─────────────────────────────────────────────
# Common Taxonomy Dictionary (Fast deterministic fallback)
# ─────────────────────────────────────────────
TAXONOMY = {
    "genai engineer": {
        "titles": ["AI Engineer", "Generative AI Engineer", "LLM Engineer", "Machine Learning Engineer", "ML Engineer", "Applied AI Engineer"],
        "skills": ["Python", "LangChain", "LlamaIndex", "PyTorch", "Hugging Face", "LLMs", "RAG", "Prompt Engineering"],
        "negatives": ["Sales", "Recruiter", "Intern (unpaid)"]
    },
    "generative ai engineer": {
        "titles": ["GenAI Engineer", "AI Engineer", "LLM Engineer", "Machine Learning Engineer", "Applied AI Engineer"],
        "skills": ["Python", "LangChain", "PyTorch", "LLMs", "RAG", "Transformers"],
        "negatives": ["Sales", "Recruiter"]
    },
    "ai engineer": {
        "titles": ["GenAI Engineer", "Generative AI Engineer", "Machine Learning Engineer", "ML Engineer", "Applied AI Engineer", "Deep Learning Engineer", "MLOps Engineer", "LLM Engineer"],
        "skills": ["Python", "PyTorch", "TensorFlow", "Transformers", "LangChain", "LLMs", "Docker", "FastAPI"],
        "negatives": ["Sales", "Recruiter", "Intern (unpaid)"]
    },
    "machine learning engineer": {
        "titles": ["AI Engineer", "ML Engineer", "Data Scientist", "Applied Scientist", "MLOps Engineer"],
        "skills": ["Python", "Scikit-Learn", "PyTorch", "TensorFlow", "Pandas", "SQL", "Cloud"],
        "negatives": ["Sales", "HR"]
    },
    "data scientist": {
        "titles": ["Machine Learning Scientist", "Data Analyst", "Applied Scientist", "Statistician", "Quantitative Analyst"],
        "skills": ["Python", "R", "SQL", "Pandas", "NumPy", "Statistics", "Tableau", "PowerBI"],
        "negatives": ["Data Entry"]
    },
    "software engineer": {
        "titles": ["Software Developer", "Full Stack Engineer", "Backend Engineer", "Systems Engineer", "Application Developer"],
        "skills": ["Python", "Java", "Go", "TypeScript", "JavaScript", "SQL", "Git", "REST APIs"],
        "negatives": ["Support Technician"]
    },
    "backend engineer": {
        "titles": ["Backend Developer", "Software Engineer - Backend", "Server Engineer", "API Engineer"],
        "skills": ["Node.js", "Python", "Go", "Java", "PostgreSQL", "Redis", "Kafka", "Docker", "Kubernetes"],
        "negatives": ["UI Designer", "Frontend Only"]
    },
    "frontend engineer": {
        "titles": ["Frontend Developer", "UI Engineer", "Web Developer", "React Developer"],
        "skills": ["JavaScript", "TypeScript", "React", "Next.js", "HTML", "CSS", "Tailwind"],
        "negatives": ["DevOps", "Database Administrator"]
    },
    "devops engineer": {
        "titles": ["Site Reliability Engineer", "SRE", "Cloud Engineer", "Infrastructure Engineer", "Platform Engineer"],
        "skills": ["AWS", "Kubernetes", "Docker", "Terraform", "CI/CD", "Linux", "Ansible", "Prometheus"],
        "negatives": ["Frontend Developer"]
    }
}

_EXPANSION_PROMPT = """You are a technical recruiter and search query expansion specialist.
Analyze the user's job search request:
Title: {title}
Given alternative titles: {alt_titles}
Skills explicitly mentioned by user: {explicit_skills}
Location: {location}
Remote: {remote}

Generate related job titles, related search keywords, must-have vs nice-to-have skills, and negative keywords to exclude false positives.

IMPORTANT RULES:
- must_have_skills: ONLY include skills the user explicitly mentioned. Do NOT infer or add skills here.
- nice_to_have_skills: expanded/inferred skills that would be beneficial but user did not mention.
- all_titles: inferred/expanded titles only (do NOT repeat user's titles here).

Return ONLY a JSON object:
{{
  "all_titles": ["3 to 6 closely related professional titles (inferred, not user's originals)"],
  "must_have_skills": [],
  "nice_to_have_skills": ["secondary helpful skills inferred from the role"],
  "search_keywords": ["specific search terms that appear on actual job postings"],
  "negative_keywords": ["irrelevant terms like intern, unpaid, sales, lead generator if not asked"]
}}
"""


class QueryExpansionAgent:
    """
    Step 8: Generates related titles, keywords, and skill variations.
    """

    def __init__(self, gemini_client=None):
        self._client = gemini_client

    def expand(self, req: JobSearchRequest) -> ExpandedQuery:
        """Expand user search request into broader query terms.

        Separation contract
        -------------------
        explicit_titles  — titles the user typed (req.job_title + req.alternative_titles).
        inferred_titles  — expansions added by taxonomy / LLM (is_explicit=False).
        must_have_skills — ONLY skills the user explicitly provided in req.skills.
                           NEVER populated with inferred skills.
        nice_to_have_skills — inferred/expanded skills from taxonomy or LLM.
        """
        primary_title = req.job_title or "Software Engineer"

        # Collect what the user explicitly stated
        user_explicit_titles: List[str] = list(dict.fromkeys(
            [primary_title] + list(req.alternative_titles)
        ))
        user_explicit_skills: List[str] = list(req.skills)  # exactly what the user said

        # Try LLM first if available
        if self._client and getattr(self._client, "is_live", False):
            try:
                prompt = _EXPANSION_PROMPT.format(
                    title=primary_title,
                    alt_titles=", ".join(req.alternative_titles),
                    explicit_skills=", ".join(user_explicit_skills) or "none",
                    location=req.location or "Any",
                    remote=req.remote_status
                )
                res = self._client._call_gemini_json(prompt, schema_description="query expansion JSON", stage="expansion")
                if isinstance(res, dict) and "all_titles" in res:
                    # LLM returns only inferred titles; deduplicate against explicit
                    explicit_set_lower = {t.lower() for t in user_explicit_titles}
                    inferred: List[str] = [
                        t for t in res.get("all_titles", [])
                        if t.lower() not in explicit_set_lower
                    ]
                    all_titles = list(dict.fromkeys(user_explicit_titles + inferred))
                    return ExpandedQuery(
                        primary_title=primary_title,
                        explicit_titles=user_explicit_titles,
                        inferred_titles=inferred,
                        all_titles=all_titles,
                        # must_have = only what user explicitly said (LLM must not add here)
                        must_have_skills=user_explicit_skills,
                        nice_to_have_skills=res.get("nice_to_have_skills", []),
                        search_keywords=res.get("search_keywords", [primary_title]),
                        negative_keywords=res.get("negative_keywords", ["unpaid", "internship"] if req.employment_type != "internship" else [])
                    )
            except Exception as e:
                logger.warning(f"QueryExpansionAgent LLM call failed ({e}), using taxonomy fallback")

        # ── Deterministic taxonomy fallback ──────────────────────────────────
        t_key = primary_title.lower().strip()
        matched_tax = None
        for k, v in TAXONOMY.items():
            if k in t_key or t_key in k:
                matched_tax = v
                break

        inferred_titles: List[str] = []
        inferred_skills: List[str] = []
        negatives = ["unpaid"]

        if matched_tax:
            explicit_set_lower = {t.lower() for t in user_explicit_titles}
            for t in matched_tax["titles"]:
                if t.lower() not in explicit_set_lower:
                    inferred_titles.append(t)
            for s in matched_tax["skills"]:
                # Only add to inferred if the user did NOT already mention it
                if s not in user_explicit_skills:
                    inferred_skills.append(s)
            negatives.extend(matched_tax.get("negatives", []))

        all_titles = list(dict.fromkeys(user_explicit_titles + inferred_titles))

        return ExpandedQuery(
            primary_title=primary_title,
            explicit_titles=user_explicit_titles,
            inferred_titles=inferred_titles,
            all_titles=all_titles,
            # must_have: ONLY what the user explicitly said — never inferred
            must_have_skills=user_explicit_skills,
            # nice_to_have: taxonomy-inferred skills (capped at 10)
            nice_to_have_skills=inferred_skills[:10],
            search_keywords=[primary_title] + all_titles[:3],
            negative_keywords=list(set(negatives))
        )
