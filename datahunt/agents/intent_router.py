import re
from typing import Any, Dict, List, Optional
from datahunt.logger import logger
from datahunt.models.intent import ResearchIntent, ResearchOutputType, ResearchIntentSpec

INTENT_ROUTER_SYSTEM_PROMPT = """You are the DataHunt Canonical Intent Router.
Your job is to analyze the user's raw query and classify their fundamental intent and requested output format.

DO NOT classify a query as job_search merely because it mentions a profession or job title like "engineer", "developer", "designer", or "role".
Queries like "how do I become an AI engineer" are HOW_TO / educational guidance, NOT job searches.
Queries like "explain the role of an architect" are EXPLANATION, NOT job searches.
Queries like "what is the salary of a data scientist" are FACTUAL_RESEARCH, NOT job searches.
Queries like "compare React vs Vue for developers" are COMPARISON, NOT job searches.

Only classify as job_search if the user is actively seeking open job postings, vacancies, hiring leads, or applying to positions (e.g. "find AI engineer jobs in Dubai", "remote frontend developer jobs hiring now").

Classify into one of these intents:
- job_search: actively hunting or seeking job openings, vacancies, hiring listings
- how_to: procedural guidance, steps, tutorials, roadmaps ("how do I...", "steps to...")
- explanation: educational explanation, concept breakdown ("what is...", "explain me...", "why...")
- comparison: comparing two or more entities, technologies, or options ("compare...", "vs", "difference between")
- factual_research: specific facts, data points, salaries, technical stats ("salary of...", "what are the specs of...")
- list_research: ranked or curated lists ("top 10...", "list of best...")
- company_research: deep dive into a specific company ("company profile...", "valuation of...", "who owns...")
- market_research: market sizing, TAM/SAM, competitive landscape, industry trends ("market size of...", "saas market trends")
- people_research: background or biography of an individual ("who is...", "biography of...")
- news_research: recent events, breaking news ("latest news about...")
- current_status: live state or current status of an ongoing event or technology
- deep_research: extensive exploratory multi-faceted research

Requested Output Types:
- answer: direct synthesis, structured explanation, guidance, facts
- job_results: list of job postings
- comparison: structured comparison matrix / pros & cons
- company_profile: company overview, metrics, business model
- people_results: person profile / bio
- entity_list: ranked or curated list of entities
- market_intel: market sizing, industry analysis

Answer Styles: direct, structured_list, comparison_table, dossier, profile

Return a valid JSON object matching the schema."""


class IntentRouter:
    """
    Canonical intent router for DataHunt.
    Provides deterministic fast-path classification and optional LLM refinement.
    Ensures that queries with profession words ('engineer', 'developer') are not
    mistakenly classified as job searches.
    """

    def __init__(self, gemini_client: Optional[Any] = None):
        self.client = gemini_client

    def classify(self, query: str, agent_mode: Optional[str] = "auto") -> ResearchIntentSpec:
        """
        Classifies user query into canonical ResearchIntentSpec.
        """
        cleaned_query = (query or "").strip()
        mode = (agent_mode or "auto").lower()

        # 1. Operator explicit overrides
        if mode == "jobs":
            return ResearchIntentSpec(
                raw_query=cleaned_query,
                intent=ResearchIntent.JOB_SEARCH,
                confidence=1.0,
                topic=cleaned_query,
                requires_web_search=True,
                requires_current_information=True,
                answer_style="direct",
                requested_output=ResearchOutputType.JOB_RESULTS,
                reason="Operator explicitly selected jobs mode"
            )
        if mode == "market":
            return ResearchIntentSpec(
                raw_query=cleaned_query,
                intent=ResearchIntent.MARKET_RESEARCH,
                confidence=1.0,
                topic=cleaned_query,
                requires_web_search=True,
                requires_current_information=True,
                answer_style="dossier",
                requested_output=ResearchOutputType.MARKET_INTEL,
                reason="Operator explicitly selected market mode"
            )

        # 2. Deterministic classification
        deterministic_spec = self._classify_deterministic(cleaned_query, mode)

        # If deterministic classification is highly confident or operator explicitly chose research mode,
        # we can use deterministic or refine via LLM
        if mode == "research" and deterministic_spec.intent == ResearchIntent.JOB_SEARCH:
            # Operator forced research mode: prevent job search
            deterministic_spec.intent = ResearchIntent.FACTUAL_RESEARCH
            deterministic_spec.requested_output = ResearchOutputType.ANSWER

        # 3. If client is live, try LLM classification for richer entity/constraint extraction
        if self.client and getattr(self.client, "is_live", False):
            try:
                llm_spec = self._classify_llm(cleaned_query, mode)
                if llm_spec:
                    # Guard: if LLM hallucinates job_search on a query that deterministic marked as how_to/explanation/comparison,
                    # trust deterministic!
                    if (
                        llm_spec.intent == ResearchIntent.JOB_SEARCH
                        and deterministic_spec.intent in (
                            ResearchIntent.HOW_TO,
                            ResearchIntent.EXPLANATION,
                            ResearchIntent.COMPARISON,
                            ResearchIntent.FACTUAL_RESEARCH
                        )
                    ):
                        logger.warning(
                            f"[IntentRouter] Overriding LLM job_search misclassification with deterministic {deterministic_spec.intent}"
                        )
                        return deterministic_spec
                    return llm_spec
            except Exception as e:
                logger.warning(f"[IntentRouter] LLM classification error: {e}. Falling back to deterministic.")

        return deterministic_spec

    def _classify_deterministic(self, query: str, mode: str) -> ResearchIntentSpec:
        q_lower = query.lower()

        # Check for News / Current Status
        if re.search(r"\b(?:latest\s+news|recent\s+news|breaking\s+news|news\s+about|latest\s+updates?)\b", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.NEWS_RESEARCH,
                confidence=0.95,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="direct",
                requested_output=ResearchOutputType.ANSWER,
                reason="Matched news research patterns"
            )

        # Check for How-To (educational / procedural)
        # MUST take precedence over job search if query asks "how do I become an AI engineer"
        if re.search(
            r"^(?:how\s+(?:do|can|to|does|would)\s+(?:i|one|you|someone|we)\b|how\s+to\b|steps\s+to\b|guide\s+(?:to|for)\b|roadmap\s+(?:to|for)\b|path\s+to\b|what\s+are\s+the\s+(?:steps|requirements|prerequisites)\s+to\b|tutorial\s+on\b)",
            q_lower
        ) or re.search(r"\b(?:how\s+to\s+travel\s+to\s+space|how\s+do\s+i\s+become)\b", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.HOW_TO,
                confidence=0.95,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=False,
                answer_style="direct",
                requested_output=ResearchOutputType.ANSWER,
                reason="Matched procedural / educational how-to query"
            )

        # Check for Explanation
        if re.search(
            r"^(?:explain(?:\s+(?:to\s+)?me)?\b|what\s+is\b|what\s+are\b|what\s+does\b|why\s+is\b|why\s+does\b|why\s+do\b|how\s+does\b|tell\s+me\s+about\b|describe\b|overview\s+of\b|teach\s+me\b)",
            q_lower
        ):
            # Check if explanation asks "explain me how to travel to space"
            if "how to" in q_lower or "steps" in q_lower:
                return ResearchIntentSpec(
                    raw_query=query,
                    intent=ResearchIntent.HOW_TO,
                    confidence=0.95,
                    topic=self._extract_topic(query),
                    requires_web_search=True,
                    requires_current_information=False,
                    answer_style="direct",
                    requested_output=ResearchOutputType.ANSWER,
                    reason="Matched procedural explanation pattern"
                )
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.EXPLANATION,
                confidence=0.95,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=False,
                answer_style="direct",
                requested_output=ResearchOutputType.ANSWER,
                reason="Matched conceptual explanation pattern"
            )

        # Check for Comparison
        if re.search(r"\b(?:compare\b|vs\.?\b|versus\b|difference\s+between|pros\s+and\s+cons|advantages\s+and\s+disadvantages|tradeoffs?\s+between)\b", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.COMPARISON,
                confidence=0.95,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=False,
                answer_style="comparison_table",
                requested_output=ResearchOutputType.COMPARISON,
                reason="Matched comparative analysis pattern"
            )

        # Check for Salary / Compensation Factual Research
        if re.search(r"\b(?:salary\s+of|salaries|average\s+salary|pay\s+scale|compensation\s+for)\b", q_lower) or (
            "salary" in q_lower and not re.search(r"\b(?:find\s+jobs|hiring|openings?)\b", q_lower)
        ):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.FACTUAL_RESEARCH,
                confidence=0.90,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="direct",
                requested_output=ResearchOutputType.ANSWER,
                reason="Matched factual salary / compensation research"
            )

        # Check for People Research
        if re.search(r"^(?:who\s+is\b|who\s+was\b|biography\s+of\b|background\s+of\b|profile\s+of\b)", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.PEOPLE_RESEARCH,
                confidence=0.90,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=False,
                answer_style="profile",
                requested_output=ResearchOutputType.PEOPLE_RESULTS,
                reason="Matched people research pattern"
            )

        # Check for Company Research
        if re.search(r"\b(?:company\s+profile|valuation\s+of|who\s+owns|about\s+company|funding\s+round|revenue\s+of)\b", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.COMPANY_RESEARCH,
                confidence=0.90,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="profile",
                requested_output=ResearchOutputType.COMPANY_PROFILE,
                reason="Matched company research pattern"
            )

        # Check for Market Research
        if re.search(r"\b(?:market\s+size|market\s+share|market\s+analysis|industry\s+trends|cagr|tam\b|sam\b)\b", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.MARKET_RESEARCH,
                confidence=0.90,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="dossier",
                requested_output=ResearchOutputType.MARKET_INTEL,
                reason="Matched market research pattern"
            )

        # Check for List Research
        if re.search(r"\b(?:top\s+(?:\d+\s+)?[a-zA-Z0-9]|list\s+of\b|ranking\s+of\b|best\s+(?:\d+\s+)?[a-zA-Z0-9])", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.LIST_RESEARCH,
                confidence=0.90,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="structured_list",
                requested_output=ResearchOutputType.ENTITY_LIST,
                reason="Matched list / ranking research pattern"
            )

        # Check for Job Search (requires strong explicit job seeking verbs/phrases)
        is_explicit_job_search = bool(re.search(
            r"\b(?:find\s+(?:me\s+)?jobs?|job\s+(?:openings?|vacanc(?:y|ies)|listings?|opportunities|leads?|hunt|search)|hiring\s+(?:for|now)|open\s+positions?|apply\s+(?:for|to)\s+jobs?|careers?\s+at|recruiting\s+for|work\s+as\s+a(?:n)?)\b",
            q_lower
        ) or re.search(r"\b(?:jobs?\s+(?:in|for|at|around)|remote\s+[a-z\s]+jobs?|[a-z\s]+jobs?\s+hiring)\b", q_lower))

        if is_explicit_job_search and mode != "research":
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.JOB_SEARCH,
                confidence=0.95,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="direct",
                requested_output=ResearchOutputType.JOB_RESULTS,
                reason="Matched explicit job search keywords"
            )

        # Check for Current Status
        if re.search(r"\b(?:current\s+status|latest\s+status|status\s+of|what\s+is\s+happening\s+with)\b", q_lower):
            return ResearchIntentSpec(
                raw_query=query,
                intent=ResearchIntent.CURRENT_STATUS,
                confidence=0.85,
                topic=self._extract_topic(query),
                requires_web_search=True,
                requires_current_information=True,
                answer_style="direct",
                requested_output=ResearchOutputType.ANSWER,
                reason="Matched current status pattern"
            )

        # Default fallback for general knowledge / research query
        return ResearchIntentSpec(
            raw_query=query,
            intent=ResearchIntent.FACTUAL_RESEARCH,
            confidence=0.75,
            topic=self._extract_topic(query),
            requires_web_search=True,
            requires_current_information=False,
            answer_style="direct",
            requested_output=ResearchOutputType.ANSWER,
            reason="Defaulted to factual research answer"
        )

    def _classify_llm(self, query: str, mode: str) -> Optional[ResearchIntentSpec]:
        """Calls Gemini with structured schema."""
        prompt = f"""Analyze the user's research query and classify it.
Operator mode context: {mode}
Query: "{query}"

Respond with a JSON object:
{{
    "intent": "job_search" | "how_to" | "explanation" | "comparison" | "market_research" | "company_research" | "people_research" | "factual_research" | "list_research" | "news_research" | "current_status" | "deep_research",
    "topic": "clean core topic",
    "confidence": 0.95,
    "requires_web_search": true,
    "requires_current_information": false,
    "entities": ["entity1"],
    "locations": ["location1"],
    "explicit_constraints": [],
    "answer_style": "direct" | "structured_list" | "comparison_table" | "dossier" | "profile",
    "requested_output": "answer" | "job_results" | "comparison" | "company_profile" | "people_results" | "entity_list" | "market_intel",
    "reason": "brief rationale"
}}"""

        res = self.client._call_gemini_json(prompt, schema_description="ResearchIntentSpec", stage="intake")
        if isinstance(res, dict) and "intent" in res:
            intent_val = res.get("intent")
            # Validate enum values
            try:
                intent_enum = ResearchIntent(intent_val)
            except ValueError:
                intent_enum = ResearchIntent.FACTUAL_RESEARCH

            output_val = res.get("requested_output", "answer")
            try:
                output_enum = ResearchOutputType(output_val)
            except ValueError:
                output_enum = ResearchOutputType.ANSWER

            return ResearchIntentSpec(
                raw_query=query,
                intent=intent_enum,
                confidence=float(res.get("confidence") or 0.85),
                topic=res.get("topic") or self._extract_topic(query),
                requires_web_search=bool(res.get("requires_web_search", True)),
                requires_current_information=bool(res.get("requires_current_information", False)),
                entities=res.get("entities") or [],
                locations=res.get("locations") or [],
                explicit_constraints=res.get("explicit_constraints") or [],
                answer_style=res.get("answer_style") or "direct",
                requested_output=output_enum,
                reason=res.get("reason")
            )
        return None

    def _extract_topic(self, query: str) -> str:
        """Extract a clean core topic from conversational prefix."""
        t = re.sub(r"^(?:explain(?:\s+(?:to\s+)?me)?|how\s+(?:do|can|to)\s+(?:i|one|you)|what\s+is|what\s+are|why\s+is|tell\s+me\s+about|find(?:\s+me)?\s+jobs?\s+(?:for|in)?)\s*", "", query, flags=re.I).strip()
        return t if t else query
