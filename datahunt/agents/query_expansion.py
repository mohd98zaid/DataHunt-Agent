"""
QueryExpansionAgent — Step 8 of the job search pipeline.

Expands primary job titles, skills, and industry terminology into related
titles, search keywords, and negative keywords to ensure broad and deep recall.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.logger import logger
from datahunt.agents.query_understanding import JobSearchRequest


class ExpandedQuery(BaseModel):
    """Result of expanding a JobSearchRequest with synonyms and related skills."""
    primary_title: str
    all_titles: List[str] = Field(default_factory=list)
    must_have_skills: List[str] = Field(default_factory=list)
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
Skills: {skills}
Location: {location}
Remote: {remote}

Generate related job titles, related search keywords, must-have vs nice-to-have skills, and negative keywords to exclude false positives.

Return ONLY a JSON object:
{{
  "all_titles": ["primary title", "3 to 6 closely related professional titles"],
  "must_have_skills": ["top core skills"],
  "nice_to_have_skills": ["secondary helpful skills"],
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
        """Expand user search request into broader query terms."""
        primary_title = req.job_title or "Software Engineer"
        
        # Try LLM first if available
        if self._client and getattr(self._client, "is_live", False):
            try:
                prompt = _EXPANSION_PROMPT.format(
                    title=primary_title,
                    alt_titles=", ".join(req.alternative_titles),
                    skills=", ".join(req.skills),
                    location=req.location or "Any",
                    remote=req.remote_status
                )
                res = self._client._call_gemini_json(prompt, schema_description="query expansion JSON", stage="expansion")
                if isinstance(res, dict) and "all_titles" in res:
                    titles = list(dict.fromkeys([primary_title] + req.alternative_titles + res.get("all_titles", [])))
                    return ExpandedQuery(
                        primary_title=primary_title,
                        all_titles=titles,
                        must_have_skills=res.get("must_have_skills", req.skills),
                        nice_to_have_skills=res.get("nice_to_have_skills", []),
                        search_keywords=res.get("search_keywords", [primary_title]),
                        negative_keywords=res.get("negative_keywords", ["unpaid", "internship"] if req.employment_type != "internship" else [])
                    )
            except Exception as e:
                logger.warning(f"QueryExpansionAgent LLM call failed ({e}), using taxonomy fallback")

        # Deterministic taxonomy fallback
        t_key = primary_title.lower().strip()
        matched_tax = None
        for k, v in TAXONOMY.items():
            if k in t_key or t_key in k:
                matched_tax = v
                break

        expanded_titles = [primary_title] + req.alternative_titles
        skills = list(req.skills)
        negatives = ["unpaid"]

        if matched_tax:
            for t in matched_tax["titles"]:
                if t not in expanded_titles:
                    expanded_titles.append(t)
            for s in matched_tax["skills"]:
                if s not in skills:
                    skills.append(s)
            negatives.extend(matched_tax.get("negatives", []))

        return ExpandedQuery(
            primary_title=primary_title,
            all_titles=list(dict.fromkeys(expanded_titles)),
            must_have_skills=skills[:5],
            nice_to_have_skills=skills[5:10],
            search_keywords=[primary_title] + expanded_titles[:3],
            negative_keywords=list(set(negatives))
        )
