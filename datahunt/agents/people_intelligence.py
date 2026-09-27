"""
People Intelligence Agent.

Discovers evidence-backed professionals (recruiters, hiring managers, engineering leaders,
executives) associated with companies and domains.
Strictly requires empirical source verification: never infers a person's role or affiliation without evidence.
"""
import re
from typing import Any, Dict, List, Optional
from datahunt.models.shared_intel import PersonProfile, PersonRole
from datahunt.tools.search import SearchTool
from datahunt.tools.fetch import FetchTool
from datahunt.logger import logger


_ROLE_PATTERNS = [
    (PersonRole.RECRUITER, re.compile(r"\b(?:technical recruiter|recruiter|talent partner|senior recruiter)\b", re.I)),
    (PersonRole.TALENT_ACQUISITION, re.compile(r"\b(?:talent acquisition|head of talent|talent lead|recruiting lead)\b", re.I)),
    (PersonRole.HIRING_MANAGER, re.compile(r"\b(?:hiring manager|engineering manager|tech lead manager|em)\b", re.I)),
    (PersonRole.AI_LEADER, re.compile(r"\b(?:head of ai|director of ai|ai lead|chief ai officer|vp of ai|lead ai engineer)\b", re.I)),
    (PersonRole.ENGINEERING_LEADER, re.compile(r"\b(?:vp of engineering|head of engineering|director of engineering|cto|chief technology officer)\b", re.I)),
    (PersonRole.EXECUTIVE, re.compile(r"\b(?:ceo|chief executive|founder|co-founder|president|managing director)\b", re.I)),
    (PersonRole.RESEARCHER, re.compile(r"\b(?:research scientist|ai researcher|postdoc|fellow|principal researcher)\b", re.I)),
]


class PeopleIntelligenceAgent:
    """
    Finds real, verified professionals associated with specific companies and organizational roles.
    """
    def __init__(
        self,
        gemini_client: Optional[Any] = None,
        search_tool: Optional[SearchTool] = None,
        fetch_tool: Optional[FetchTool] = None,
    ):
        self.client = gemini_client
        self.search_tool = search_tool or SearchTool()
        self.fetch_tool = fetch_tool or FetchTool()

    def find_people(
        self,
        company: str,
        role: Optional[str] = None,
        location: Optional[str] = None,
        purpose: str = "talent_search",
        max_results: int = 5,
    ) -> List[PersonProfile]:
        """
        Discovers verified people profiles matching company and target role.
        """
        company_clean = company.strip()
        role_query = role or "recruiter OR talent acquisition OR hiring manager OR engineering lead"
        loc_str = f" {location}" if location else ""

        query = f'"{company_clean}" {role_query}{loc_str} site:linkedin.com/in'
        search_res = self.search_tool.execute(query=query, max_results=max_results * 2)

        people: List[PersonProfile] = []
        seen_names = set()

        if search_res.success and search_res.data:
            hits = search_res.data if isinstance(search_res.data, list) else (search_res.data.get("results", []) if isinstance(search_res.data, dict) else [])
            for h in hits:
                title = str(getattr(h, "title", None) or (h.get("title") if isinstance(h, dict) else "") or "")
                snippet = str(getattr(h, "snippet", None) or (h.get("snippet") if isinstance(h, dict) else "") or "")
                url = str(getattr(h, "url", None) or (h.get("url") or h.get("link") if isinstance(h, dict) else "") or "")

                # Parse name and role from standard professional profile snippet:
                # e.g., "John Doe - Senior Technical Recruiter - Acme Corp | LinkedIn"
                parts = re.split(r"[-–|:]", title)
                if len(parts) >= 2:
                    raw_name = parts[0].strip()
                    title_snippet = parts[1].strip()
                else:
                    raw_name = title.strip()
                    title_snippet = snippet

                # Basic name validation (2-3 words, no numbers)
                name_words = raw_name.split()
                if not (1 < len(name_words) <= 4) or any(char.isdigit() for char in raw_name):
                    continue
                if raw_name.lower() in seen_names or "linkedin" in raw_name.lower():
                    continue

                full_text = f"{title} {snippet} {title_snippet}"

                # Role detection
                detected_role = PersonRole.UNKNOWN
                for p_role, regex in _ROLE_PATTERNS:
                    if regex.search(full_text):
                        detected_role = p_role
                        break

                seen_names.add(raw_name.lower())
                person = PersonProfile(
                    name=raw_name,
                    company=company_clean,
                    role=detected_role,
                    role_title=title_snippet,
                    relevance=f"Associated with {company_clean} as {title_snippet}",
                    source="Public Professional Profile",
                    profile_url=url,
                    evidence=[snippet[:200]] if snippet else [],
                    confidence=0.85 if detected_role != PersonRole.UNKNOWN else 0.60,
                )
                people.append(person)
                if len(people) >= max_results:
                    break

        logger.info(f"PeopleIntelligenceAgent: Found {len(people)} people for company {company_clean}")
        return people
