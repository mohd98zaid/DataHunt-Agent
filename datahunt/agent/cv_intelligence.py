"""
DataHunt CV Intelligence & Autonomous Candidate Matching Engine
----------------------------------------------------------------
Provides end-to-end CV parsing (.docx, .pdf, .txt, .md), eligibility scoring,
confidence matching against discovered jobs, and AI-driven ATS tweak generation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("datahunt.cv_intelligence")


@dataclass
class CVProfile:
    candidate_name: str = ""
    headline: str = ""
    years_of_experience: float = 0.0
    seniority_level: str = "Mid-Senior"
    current_location: str = ""
    target_roles: List[str] = field(default_factory=list)
    target_regions: List[str] = field(default_factory=list)
    core_skills: List[str] = field(default_factory=list)
    secondary_skills: List[str] = field(default_factory=list)
    domains: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    key_achievements: List[str] = field(default_factory=list)
    summary: str = ""
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("raw_text", None)
        return d


def extract_text_from_docx(file_path_or_bytes: str | bytes) -> str:
    """Extract plain text paragraphs from a .docx file using standard library zipfile."""
    try:
        import io
        source = io.BytesIO(file_path_or_bytes) if isinstance(file_path_or_bytes, bytes) else file_path_or_bytes
        with zipfile.ZipFile(source) as z:
            xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for p in tree.iter(f"{ns}p"):
                texts = [node.text for node in p.iter(f"{ns}t") if node.text]
                if texts:
                    paragraphs.append("".join(texts))
            return "\n".join(paragraphs)
    except Exception as ex:
        logger.error(f"Failed to extract text from docx: {ex}")
        raise ValueError(f"Could not read DOCX document: {ex}")


def extract_raw_text(file_path_or_bytes: str | bytes, filename: str = "") -> str:
    """Extract plain text from various file formats (.docx, .txt, .md, etc.)."""
    is_bytes = isinstance(file_path_or_bytes, bytes)
    ext = Path(filename).suffix.lower() if filename else (Path(file_path_or_bytes).suffix.lower() if isinstance(file_path_or_bytes, str) else "")

    if ext == ".docx" or (is_bytes and file_path_or_bytes[:4] == b"PK\x03\x04"):
        return extract_text_from_docx(file_path_or_bytes)
    
    if is_bytes:
        try:
            return file_path_or_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_path_or_bytes.decode("latin-1", errors="ignore")
    
    # Path string
    p = Path(file_path_or_bytes)
    if not p.exists():
        raise FileNotFoundError(f"CV file not found: {file_path_or_bytes}")
    
    if p.suffix.lower() == ".docx":
        return extract_text_from_docx(str(p))
    
    return p.read_text(encoding="utf-8", errors="replace")


def parse_cv_heuristic(raw_text: str) -> CVProfile:
    """Heuristic rule-based extractor for candidate CVs, ensuring zero-dependency accuracy."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    candidate_name = lines[0] if lines else "Candidate"
    
    # Clean name if title or contact info got attached
    candidate_name = re.sub(r'[\(\|,].*$', '', candidate_name).strip()

    # Headline
    headline = ""
    if len(lines) > 1 and ("|" in lines[1] or "Architect" in lines[1] or "Engineer" in lines[1] or "Lead" in lines[1]):
        headline = lines[1]

    # Years of experience
    yoe = 0.0
    yoe_match = re.search(r'(\d+(?:\.\d+)?)\+?\s*(?:years|yrs)', raw_text, re.I)
    if yoe_match:
        try:
            yoe = float(yoe_match.group(1))
        except ValueError:
            yoe = 5.0

    # Seniority level
    if yoe >= 8.0:
        seniority = "Principal / Director"
    elif yoe >= 5.0 or "Architect" in raw_text or "Lead" in raw_text:
        seniority = "Senior / Lead / Architect"
    elif yoe >= 3.0:
        seniority = "Mid-Senior"
    else:
        seniority = "Junior / Associate"

    # Target Regions
    target_regions = []
    geo_keywords = {
        "Saudi Arabia": ["saudi", "riyadh", "jeddah", "neom", "dammam", "khobar", "ksa"],
        "UAE": ["uae", "dubai", "abu dhabi", "sharjah", "emirates"],
        "Kuwait": ["kuwait"],
        "Qatar": ["qatar", "doha"],
        "Bahrain": ["bahrain"],
        "Oman": ["oman", "muscat"],
        "GCC": ["gcc", "gulf"],
        "Remote": ["remote", "hybrid"]
    }
    raw_lower = raw_text.lower()
    for canonical_geo, kws in geo_keywords.items():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', raw_lower) for kw in kws):
            target_regions.append(canonical_geo)
    if not target_regions:
        target_regions = ["Saudi Arabia", "UAE", "GCC"]

    # Target Roles
    target_roles = []
    role_pattern = re.search(r'(?:Target Roles|Seeking)\s*[:\s]*([^\n\.]+)', raw_text, re.I)
    if role_pattern:
        raw_roles_str = re.sub(r'\b(?:roles?\s+in|in\s+Saudi|in\s+the\s+UAE|in\s+Kuwait|in\s+GCC).*$', '', role_pattern.group(1), flags=re.I).strip()
        extracted = [r.strip() for r in re.split(r'[,|/]| and ', raw_roles_str) if r.strip()]
        geo_exclusion = {"uae", "the uae", "saudi", "saudi arabia", "kuwait", "qatar", "gcc", "the wider gcc", "wider gcc", "middle east"}
        for r in extracted:
            clean_r = r.strip()
            if len(clean_r) > 2 and clean_r.lower() not in geo_exclusion and clean_r not in target_roles:
                target_roles.append(clean_r)
    if not target_roles:
        target_roles = ["Senior GenAI Architect", "Agentic AI Engineering Lead", "Lead AI Engineer"]

    # Core Tech Skills extraction
    skills_vocabulary = [
        "LangGraph", "LangChain", "RAG", "Agentic AI", "Multi-Agent Systems",
        "Claude", "GPT", "OpenAI", "Anthropic", "Azure OpenAI", "Microsoft Foundry",
        "AWS Bedrock", "AWS SageMaker", "Python", "FastAPI", "Docker", "Kubernetes",
        "Terraform", "Argo CD", "CI/CD", "FAISS", "Chroma", "Pinecone",
        "Model Context Protocol (MCP)", "MCP", "Prompt Engineering", "LLM Evaluation",
        "Fine-tuning", "LoRA", "QLoRA", "LangSmith", "LlamaIndex", "CrewAI", "AutoGen",
        "KDB+/q", "SQL", "PostgreSQL", "Redis", "Kafka", "Playwright", "ONNX"
    ]
    core_skills = []
    secondary_skills = []
    for skill in skills_vocabulary:
        pattern = r'\b' + re.escape(skill.lower()) + r'\b'
        if re.search(pattern, raw_lower):
            if skill in ("LangGraph", "LangChain", "RAG", "Agentic AI", "Multi-Agent Systems", "Python", "FastAPI", "Claude", "OpenAI", "Azure OpenAI", "AWS Bedrock", "MCP"):
                core_skills.append(skill)
            else:
                secondary_skills.append(skill)

    # Domains
    domain_map = {
        "Healthcare": ["healthcare", "health", "clinical", "hospital", "pharma"],
        "BFSI & Capital Markets": ["bfsi", "capital markets", "trade surveillance", "fintech", "banking", "fca", "dfsa", "cbuae"],
        "Aviation": ["aviation", "airline", "flight"],
        "Enterprise Engineering": ["enterprise", "microservices", "systems"]
    }
    domains = []
    for d_name, d_kws in domain_map.items():
        if any(re.search(r'\b' + re.escape(k) + r'\b', raw_lower) for k in d_kws):
            domains.append(d_name)

    # Summary
    summary_match = re.search(r'PROFESSIONAL SUMMARY\s*\n+([^\n]+(?:\n[^\n]+){1,4})', raw_text, re.I)
    summary = summary_match.group(1).strip() if summary_match else headline

    return CVProfile(
        candidate_name=candidate_name,
        headline=headline,
        years_of_experience=yoe,
        seniority_level=seniority,
        target_roles=target_roles,
        target_regions=target_regions,
        core_skills=core_skills,
        secondary_skills=secondary_skills,
        domains=domains,
        summary=summary,
        raw_text=raw_text
    )


def parse_cv_with_nvidia(raw_text: str, client: Any) -> CVProfile:
    """Use NVIDIA NIM to perform structured, semantic CV extraction."""
    heuristic = parse_cv_heuristic(raw_text)
    if not client or not getattr(client, "is_live", False):
        return heuristic

    prompt = (
        "Extract the candidate's career profile from the following CV text into a valid JSON object.\n"
        "Schema:\n"
        "{\n"
        '  "candidate_name": "Full Name",\n'
        '  "headline": "Current Professional Title or Headline",\n'
        '  "years_of_experience": 5.8,\n'
        '  "seniority_level": "Senior / Lead / Architect",\n'
        '  "target_roles": ["Senior GenAI Architect", "Agentic AI Engineering Lead", ...],\n'
        '  "target_regions": ["Saudi Arabia", "UAE", "Dubai", "Riyadh", "GCC"],\n'
        '  "core_skills": ["LangGraph", "RAG", "MCP", "Python", ...],\n'
        '  "secondary_skills": ["Docker", "Kubernetes", "KDB+/q", ...],\n'
        '  "domains": ["Healthcare", "BFSI", "Aviation"],\n'
        '  "summary": "2-3 sentence executive synopsis"\n'
        "}\n\n"
        f"CV TEXT (first 4000 characters):\n{raw_text[:4000]}"
    )

    try:
        data = client._call_nvidia_json(prompt, schema_description="CVProfile", stage="extract")
        if isinstance(data, dict) and data.get("candidate_name"):
            return CVProfile(
                candidate_name=str(data.get("candidate_name") or heuristic.candidate_name),
                headline=str(data.get("headline") or heuristic.headline),
                years_of_experience=float(data.get("years_of_experience") or heuristic.years_of_experience),
                seniority_level=str(data.get("seniority_level") or heuristic.seniority_level),
                target_roles=list(data.get("target_roles") or heuristic.target_roles),
                target_regions=list(data.get("target_regions") or heuristic.target_regions),
                core_skills=list(data.get("core_skills") or heuristic.core_skills),
                secondary_skills=list(data.get("secondary_skills") or heuristic.secondary_skills),
                domains=list(data.get("domains") or heuristic.domains),
                summary=str(data.get("summary") or heuristic.summary),
                raw_text=raw_text
            )
    except Exception as ex:
        logger.warning(f"NVIDIA CV extraction failed, using heuristic: {ex}")

    return heuristic


def score_job_eligibility(job: Dict[str, Any], cv: CVProfile) -> Dict[str, Any]:
    """
    Compute multidimensional match confidence (0-100%) between a candidate CV and a job posting.
    Breakdown:
      - Role & Seniority Match: 30%
      - Technical Skills Match: 40%
      - Geographic Alignment:   20%
      - Industry / Domain Fit:  10%
    """
    fields = job.get("fields") or {}
    job_id = job.get("job_id") or job.get("id", "")
    title = (job.get("title") or fields.get("title") or fields.get("name") or "").strip()
    company = (job.get("company") or fields.get("company") or fields.get("company_name") or "Direct Employer").strip()
    location = (job.get("location") or fields.get("location") or "Unspecified").strip()
    description = (job.get("description") or fields.get("description") or fields.get("job_description") or "").strip()
    extracted_text = job.get("extracted_text") or ""
    raw_skills = fields.get("skills") or []
    skills_str = " ".join(raw_skills) if isinstance(raw_skills, list) else str(raw_skills)
    canonical_url = job.get("canonical_url") or fields.get("application_url") or ""

    text_corpus = f"{title}\n{description}\n{extracted_text}\n{skills_str}\n{location}".lower()
    title_lower = title.lower()

    # 1. Role & Seniority Match (0 - 30 pts)
    role_score = 0
    # High-value role matches
    if any(r.lower() in title_lower for r in ["genai", "generative ai", "agentic", "llm", "ai architect", "solutions architect"]):
        role_score += 20
    elif any(r.lower() in title_lower for r in ["ai engineer", "machine learning", "ml engineer", "ai specialist", "data and ai"]):
        role_score += 15
    elif "ai" in title_lower or "engineer" in title_lower or "developer" in title_lower:
        role_score += 10

    # Seniority alignment
    if any(s in title_lower for s in ["architect", "lead", "staff", "principal", "head"]):
        role_score += 10
    elif any(s in title_lower for s in ["senior", "sr."]):
        role_score += 8
    elif any(s in title_lower for s in ["specialist", "engineer"]):
        role_score += 5
    role_score = min(30, role_score)

    # 2. Technical Skills Match (0 - 40 pts)
    all_candidate_skills = cv.core_skills + cv.secondary_skills
    matching_skills: List[str] = []
    missing_critical: List[str] = []

    for s in all_candidate_skills:
        # Check if skill appears in job title, description, or requirements
        s_clean = s.lower().replace("-", " ")
        if re.search(r'\b' + re.escape(s_clean) + r'\b', text_corpus) or re.search(r'\b' + re.escape(s.lower()) + r'\b', text_corpus):
            matching_skills.append(s)

    # Additional standard in-demand skills to check for gaps
    benchmark_skills = ["LangGraph", "LangChain", "RAG", "Python", "FastAPI", "Docker", "Kubernetes", "Azure OpenAI", "AWS Bedrock", "Fine-tuning"]
    for b in benchmark_skills:
        if b.lower() in text_corpus and b not in matching_skills:
            missing_critical.append(b)

    # Score based on volume and depth of technical overlap
    skills_score = min(40, int(len(matching_skills) * 8))
    if any(k in matching_skills for k in ["LangGraph", "RAG", "Agentic AI", "Python"]):
        skills_score = max(skills_score, 24)

    # 3. Geographic Alignment (0 - 20 pts)
    geo_score = 0
    loc_lower = location.lower()
    
    is_saudi = any(k in loc_lower for k in ["saudi", "riyadh", "jeddah", "neom", "dammam", "khobar", "ksa"])
    is_uae = any(k in loc_lower for k in ["uae", "dubai", "abu dhabi", "sharjah", "emirates"])
    is_gcc = any(k in loc_lower for k in ["kuwait", "qatar", "bahrain", "oman", "gcc", "middle east"])
    is_remote = any(k in loc_lower for k in ["remote", "anywhere", "work from home"])

    if is_saudi and any(r in cv.target_regions for r in ["Saudi Arabia", "Riyadh", "GCC"]):
        geo_score = 20
    elif is_uae and any(r in cv.target_regions for r in ["UAE", "Dubai", "GCC"]):
        geo_score = 20
    elif is_gcc or is_remote:
        geo_score = 16
    elif loc_lower in ("unspecified", "remote / unspecified", ""):
        geo_score = 10
    else:
        geo_score = 4

    # 4. Industry / Domain Fit (0 - 10 pts)
    domain_score = 0
    matched_domains = []
    if any(k in text_corpus for k in ["health", "hospital", "clinical", "medical"]):
        domain_score += 5
        matched_domains.append("Healthcare")
    if any(k in text_corpus for k in ["bank", "bfsi", "fintech", "capital market", "trade", "investment", "trading", "financial services"]):
        domain_score += 5
        matched_domains.append("BFSI / Capital Markets")
    if any(k in text_corpus for k in ["aviation", "airline", "flight"]):
        domain_score += 5
        matched_domains.append("Aviation")
    domain_score = min(10, domain_score)

    total_score = min(100, role_score + skills_score + geo_score + domain_score)

    # Determine Grade
    if total_score >= 85:
        match_grade = "PLATINUM"
        eligibility = "Direct Fit: Core GenAI architecture stack, target region, and seniority align directly."
    elif total_score >= 70:
        match_grade = "GOLD"
        eligibility = "Strong Fit: Strong skill overlap with minor domain or tooling customization needed."
    elif total_score >= 55:
        match_grade = "SILVER"
        eligibility = "Moderate Match: Viable role; requires highlighting specific enterprise delivery experience."
    else:
        match_grade = "BRONZE"
        eligibility = "Peripheral: Title or geographical mismatch with candidate core focus."

    # Generate targeted tweaks
    recommended_tweaks = []
    if missing_critical:
        recommended_tweaks.append(f"Emphasize experience with {', '.join(missing_critical[:3])} in executive summary and project bullets.")
    if is_saudi:
        recommended_tweaks.append("Highlight readiness for immediate short-notice relocation to Saudi Arabia / KSA and GCC regulatory awareness.")
    elif is_uae:
        recommended_tweaks.append("Mention CBUAE / DFSA regulatory compliance exposure and Dubai enterprise AI delivery.")
    if "agentic" in title_lower or "multi agent" in title_lower:
        recommended_tweaks.append("Lead with LangGraph Multi-Agent PR Review System and LLM Judge evaluation harness.")

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "canonical_url": canonical_url,
        "match_score": total_score,
        "match_grade": match_grade,
        "breakdown": {
            "role": role_score,
            "skills": skills_score,
            "geo": geo_score,
            "domain": domain_score
        },
        "matching_skills": matching_skills,
        "missing_critical": missing_critical,
        "matched_domains": matched_domains,
        "eligibility_summary": eligibility,
        "recommended_tweaks": recommended_tweaks
    }


def rank_jobs_for_cv(jobs: List[Dict[str, Any]], cv: CVProfile, min_score: int = 40) -> List[Dict[str, Any]]:
    """Rank a collection of jobs against a parsed CVProfile in descending order of match confidence."""
    scored_list = []
    for job in jobs:
        scored = score_job_eligibility(job, cv)
        if scored["match_score"] >= min_score:
            scored_list.append(scored)
    
    scored_list.sort(key=lambda x: (x["match_score"], x["breakdown"]["skills"], x["breakdown"]["role"]), reverse=True)
    return scored_list


def generate_search_directives_from_cv(cv: CVProfile) -> Dict[str, Any]:
    """Generate high-yield DataHunt job search directives tailored specifically to the candidate CV."""
    roles_str = " and ".join(cv.target_roles[:2]) if cv.target_roles else "GenAI Architect and Agentic AI Lead"
    geos_str = " and ".join(cv.target_regions[:2]) if cv.target_regions else "Saudi Arabia and UAE"
    
    query = f"Find {roles_str} in {geos_str}"
    
    ats_queries = [
        f'site:jobs.ashbyhq.com ("GenAI" OR "Agentic AI" OR "AI Architect") ("{geo}")'
        for geo in cv.target_regions[:2]
    ]
    ats_queries.extend([
        f'site:boards.greenhouse.io ("Generative AI" OR "LLM" OR "AI Lead") ("{geo}")'
        for geo in cv.target_regions[:2]
    ])

    return {
        "suggested_query": query,
        "target_roles": cv.target_roles,
        "target_regions": cv.target_regions,
        "experience_years": cv.years_of_experience,
        "core_keywords": cv.core_skills[:8],
        "ats_queries": ats_queries
    }


def generate_tailored_cv_bullet_points(job: Dict[str, Any], cv: CVProfile, client: Any = None) -> Dict[str, Any]:
    """Generate precise CV bullet points tailored to maximize ATS keyword alignment for a specific job."""
    scored = score_job_eligibility(job, cv)
    title = scored["title"] or "Senior AI Engineer / GenAI Architect"
    company = scored["company"] or "Direct Employer"
    location = scored["location"] or "Saudi Arabia / UAE"
    missing = scored["missing_critical"]

    # If NVIDIA LLM is available, produce high-impact, quantified bullets
    if client and getattr(client, "is_live", False):
        prompt = (
            f"You are an executive CV optimization expert for Senior AI & GenAI Architects.\n"
            f"Target Job: '{title}' at '{company}' in '{scored['location']}'.\n"
            f"Candidate Profile: {cv.candidate_name}, {cv.years_of_experience} YOE, skills: {', '.join(cv.core_skills)}.\n"
            f"Job Missing Keywords: {', '.join(missing) if missing else 'None'}.\n\n"
            "Generate 3 optimized, high-impact resume bullet points aligned to this job's requirements, "
            "grounded in the candidate's actual projects (LangGraph multi-agent systems, RAG with FAISS/AST parsing, Azure OpenAI/AWS Bedrock). "
            "Return JSON: {\"tailored_headline\": \"...\", \"bullet_points\": [\"...\", \"...\", \"...\"], \"cover_note_pitch\": \"...\"}"
        )
        try:
            res = client._call_nvidia_json(prompt, schema_description="CVTweaks", stage="summarize")
            if isinstance(res, dict) and res.get("bullet_points"):
                return {
                    "job_title": title,
                    "company": company,
                    "match_score": scored["match_score"],
                    "tailored_headline": res.get("tailored_headline", f"{cv.candidate_name} | {title} | {company} Tailored"),
                    "bullet_points": res.get("bullet_points", []),
                    "cover_note_pitch": res.get("cover_note_pitch", ""),
                    "missing_skills_addressed": missing
                }
        except Exception as ex:
            logger.warning(f"NVIDIA bullet generation failed: {ex}")

    # Deterministic high-grade fallback bullets
    bullets = [
        f"Architected end-to-end Generative AI and multi-agent workflows using LangGraph and LangChain, integrating Claude and GPT models with an independent LLM judge to ensure zero hallucination in production.",
        f"Engineered enterprise RAG pipelines with AST code parsing and FAISS vector indexing, slashing manual search latency by ~50% across large-scale codebases on Azure and AWS.",
        f"Spearheaded production delivery of containerized FastAPI microservices under strict enterprise compliance standards (FCA/DFSA/CBUAE), managing 4-6 engineers with CI/CD on Docker & Kubernetes."
    ]

    return {
        "job_title": title,
        "company": company,
        "match_score": scored["match_score"],
        "tailored_headline": f"{cv.candidate_name} | Senior GenAI Architect & Agentic AI Lead",
        "bullet_points": bullets,
        "cover_note_pitch": (
            f"Dear Hiring Team at {company},\n\n"
            f"I am writing to express my strong interest in the {title} role. With 5.8+ years of software delivery "
            f"at TCS and 3+ years architecting enterprise Generative AI systems (LangGraph multi-agent orchestration, "
            f"production RAG pipelines, and LLMOps on Azure/AWS), I bring direct hands-on leadership and immediate "
            f"relocation availability to {scored['location']}."
        ),
        "missing_skills_addressed": missing
    }
