import json
import re
import time
import threading
from typing import Any, Dict, List, Optional

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode, compute_backoff
from datahunt.logger import logger
from datahunt.models import ResearchSpec, RunBudget, DateFilter, Geography, SourcePolicy
from datahunt.models.intent import ResearchIntent, ResearchOutputType, ResearchIntentSpec
from datahunt.llm.prompts import (
    SHARED_SYSTEM_PROMPT,
    INTAKE_USER_TEMPLATE,
    PLANNER_TEMPLATE,
    SEARCH_TRIAGE_TEMPLATE,
    EXTRACTION_TEMPLATE,
    VERIFICATION_TEMPLATE,
    DEDUPLICATION_TEMPLATE,
    SUMMARY_TEMPLATE,
    RESEARCH_SYNTHESIS_TEMPLATE,
    MARKET_SYNTHESIS_TEMPLATE,
    DIRECT_ANSWER_SYNTHESIS_TEMPLATE,
)
from datahunt.llm.gemini_client import (
    synthesize_direct_answer,
    synthesize_local_market_dossier,
    synthesize_local_research_dossier,
)
from datahunt.tracing import traceable

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Default NVIDIA Preview Endpoints
DEFAULT_NVIDIA_BASE_URL = getattr(settings, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1") or "https://integrate.api.nvidia.com/v1"
DEFAULT_PRIMARY_MODEL = getattr(settings, "NVIDIA_PRIMARY_MODEL", "openai/gpt-oss-20b") or "openai/gpt-oss-20b"
DEFAULT_FALLBACK_MODEL = getattr(settings, "NVIDIA_FALLBACK_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b") or "nvidia/nemotron-3.5-lightning-30b-a3b"

# Global cooldowns and pacing for NVIDIA NIM (40 RPM limit)
_nvidia_cooldowns: Dict[str, float] = {}
_nvidia_cooldown_lock = threading.Lock()
_last_nvidia_call_time: float = 0.0
_nvidia_pacing_lock = threading.Lock()


def record_nvidia_cooldown(model: str, seconds: float, reason: str = "") -> None:
    """Mark a model as cooling down until time.time() + seconds."""
    with _nvidia_cooldown_lock:
        _nvidia_cooldowns[model] = time.time() + seconds
        logger.warning(f"[NVIDIA Model Pool] Set {seconds:.0f}s cooldown on '{model}' ({reason})")


def get_nvidia_cooldown_remaining(model: str) -> float:
    """Return remaining cooldown seconds for a model (0.0 if healthy)."""
    with _nvidia_cooldown_lock:
        return max(0.0, _nvidia_cooldowns.get(model, 0.0) - time.time())


def _pace_nvidia_request(min_interval: float = 0.15) -> None:
    """Prevent micro-bursts against NVIDIA 40 RPM quota."""
    global _last_nvidia_call_time
    with _nvidia_pacing_lock:
        now = time.time()
        elapsed = now - _last_nvidia_call_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_nvidia_call_time = time.time()


def _clean_json_text(text: str) -> str:
    """Strip markdown formatting, code fences, and extract inner JSON if necessary."""
    if not text:
        return "{}"
    cleaned = text.strip()
    # Strip markdown block formatting
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # If it parses directly, return
    try:
        json.loads(cleaned)
        return cleaned
    except Exception:
        pass

    # Check which outermost delimiter appears first: '{' or '['
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")

    delims = []
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        delims = [r"(\{.*\})", r"(\[.*\])"]
    elif first_bracket != -1:
        delims = [r"(\[.*\])", r"(\{.*\})"]
    else:
        delims = [r"(\{.*\})", r"(\[.*\])"]

    for pattern in delims:
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            cand = match.group(1).strip()
            try:
                json.loads(cand)
                return cand
            except Exception:
                pass

    return cleaned


class NvidiaClient:
    """
    Adapter for NVIDIA NIM API (OpenAI-compatible) with free preview endpoints:
      - Primary Model: openai/gpt-oss-20b (fast, structured JSON, tool-calling)
      - Fallback Model: nvidia/nemotron-3.5-lightning-30b-a3b (high-speed reasoning model)
    Enforces automatic failover, 40 RPM burst pacing, cooldown tracking,
    and deterministic offline fallbacks.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        on_model_switch: Optional[Any] = None,
    ):
        self.api_key = (api_key if api_key is not None else settings.NVIDIA_API_KEY).strip()
        self.base_url = (base_url or getattr(settings, "NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL)).strip()

        # Normalize model selection and map backward-compatible aliases
        raw_model = (model or "").strip()
        default_primary = getattr(settings, "NVIDIA_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL)
        default_fallback = getattr(settings, "NVIDIA_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)

        if not raw_model or raw_model.lower() == "auto":
            self.primary_model = default_primary
            self.fallback_model = fallback_model or default_fallback
            self._mode = "auto"
        elif any(k in raw_model.lower() for k in ("primary", "gpt-oss", "lite")):
            self.primary_model = default_primary
            self.fallback_model = fallback_model or default_fallback
            self._mode = "primary"
        elif any(k in raw_model.lower() for k in ("fallback", "nemotron", "flash", "3.8")):
            self.primary_model = default_fallback
            self.fallback_model = default_primary
            self._mode = "fallback"
        else:
            self.primary_model = raw_model
            self.fallback_model = fallback_model or default_fallback
            self._mode = "dedicated"

        self.last_active_model: str = self.primary_model
        self.on_model_switch = on_model_switch
        self.max_tokens = int(getattr(settings, "NVIDIA_MAX_OUTPUT_TOKENS", 2048))
        self.temperature = float(getattr(settings, "NVIDIA_TEMPERATURE", 0.1))

        self.candidate_pool = [self.primary_model, self.fallback_model]

        self._is_live_override = None
        self._client = None
        if self.api_key and OPENAI_AVAILABLE:
            try:
                self._client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    timeout=45.0,
                    max_retries=1,
                )
            except Exception as e:
                logger.error(f"Failed to initialize NVIDIA NIM client: {e}")

    @property
    def model(self) -> str:
        """Return currently active model name in real time."""
        return getattr(self, "last_active_model", None) or self.primary_model

    @model.setter
    def model(self, value: str) -> None:
        self.primary_model = value
        self.last_active_model = value

    @property
    def mode(self) -> str:
        """Return current model routing mode ('auto', 'primary', 'fallback', 'dedicated')."""
        return getattr(self, "_mode", "auto")

    @property
    def is_live(self) -> bool:
        """Return True only if API key is provided and client initialized."""
        if hasattr(self, "_is_live_override") and self._is_live_override is not None:
            return self._is_live_override
        return bool(self.api_key and self._client is not None)

    @is_live.setter
    def is_live(self, value: bool) -> None:
        self._is_live_override = value

    def get_candidate_models_for_stage(self, stage: str) -> List[str]:
        """Return candidate models ordered by health and stage appropriateness."""
        now = time.time()
        with _nvidia_cooldown_lock:
            healthy = [m for m in self.candidate_pool if now >= _nvidia_cooldowns.get(m, 0.0)]
            cooling = [m for m in self.candidate_pool if now < _nvidia_cooldowns.get(m, 0.0)]
            cooling.sort(key=lambda m: _nvidia_cooldowns.get(m, 0.0))
        return healthy + cooling if (healthy or cooling) else [self.primary_model]

    def get_model_for_stage(self, stage: str) -> str:
        candidates = self.get_candidate_models_for_stage(stage)
        top = candidates[0] if candidates else self.primary_model
        logger.info(f"[NVIDIA Model Router] Stage '{stage}' -> Model '{top}'")
        return top

    def get_fallback_model(self, current_model: str) -> Optional[str]:
        """Return next fallback model if current model failed."""
        if current_model == self.primary_model:
            return self.fallback_model
        return None

    @traceable(run_type="llm", name="DataHunt.NvidiaJsonCall")
    def _call_nvidia_json(self, prompt: str, schema_description: str = "json", stage: str = "general") -> Any:
        """
        Call NVIDIA NIM endpoint requesting structured JSON output with automatic
        failover from primary model (openai/gpt-oss-20b) to fallback model
        (nvidia/nemotron-3.5-lightning-30b-a3b).
        """
        if not self.is_live:
            raise DataHuntError(
                ErrorCode.MODEL_AUTH_FAILED,
                user_message="NVIDIA NIM API key is not configured.",
                operator_message="NVIDIA_API_KEY environment variable is empty."
            )

        candidate_models = self.get_candidate_models_for_stage(stage)
        last_error = None

        for m_idx, current_model in enumerate(candidate_models):
            cooldown_left = get_nvidia_cooldown_remaining(current_model)
            if cooldown_left > 0:
                if m_idx < len(candidate_models) - 1:
                    continue
                elif cooldown_left <= 2.0:
                    time.sleep(cooldown_left)

            _pace_nvidia_request(0.15)

            # Track active model and notify subscribers in real time
            if current_model != getattr(self, "last_active_model", None):
                prev_model = getattr(self, "last_active_model", self.primary_model)
                self.last_active_model = current_model
                if callable(getattr(self, "on_model_switch", None)):
                    try:
                        self.on_model_switch(current_model, f"Failover switch from {prev_model}")
                    except Exception as ex:
                        logger.debug(f"Error in on_model_switch callback: {ex}")
            else:
                self.last_active_model = current_model

            system_instruction = f"{SHARED_SYSTEM_PROMPT}\nYou MUST output valid JSON only. Format: {schema_description}."
            # Ensure sufficient tokens for reasoning models (nemotron uses reasoning tokens)
            req_tokens = max(self.max_tokens, 2048) if "nemotron" in current_model else self.max_tokens

            attempts = 2 if len(candidate_models) == 1 else 1
            curr_prompt = prompt

            for attempt in range(1, attempts + 1):
                try:
                    response = self._client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": curr_prompt},
                        ],
                        temperature=self.temperature,
                        max_tokens=req_tokens,
                        response_format={"type": "json_object"} if "gpt" in current_model else None,
                    )

                    choice = response.choices[0]
                    content = choice.message.content or ""

                    # For reasoning models (e.g. nemotron), if content is empty check reasoning_content
                    if not content.strip() and hasattr(choice.message, "reasoning_content"):
                        reasoning = getattr(choice.message, "reasoning_content", "") or ""
                        cleaned_candidate = _clean_json_text(reasoning)
                        try:
                            return json.loads(cleaned_candidate)
                        except Exception:
                            pass

                    cleaned_text = _clean_json_text(content)
                    try:
                        return json.loads(cleaned_text)
                    except json.JSONDecodeError as jde:
                        if attempt == 1 and attempts > 1:
                            logger.warning(f"Malformed JSON from NVIDIA {current_model}. Reprompting: {jde}")
                            curr_prompt = f"{prompt}\n\nYour previous response was invalid JSON: {jde}. Return strictly valid JSON."
                            continue
                        raise DataHuntError(
                            ErrorCode.MODEL_SCHEMA_INVALID,
                            user_message="NVIDIA model returned invalid JSON format.",
                            operator_message=f"JSONDecodeError: {jde} on output: {content[:200]}"
                        )

                except DataHuntError:
                    raise
                except Exception as exc:
                    exc_str = str(exc)
                    last_error = exc
                    is_quota = "429" in exc_str or "rate" in exc_str.lower() or "quota" in exc_str.lower()
                    is_unavailable = any(code in exc_str for code in ("500", "502", "503", "504")) or "unavailable" in exc_str.lower()
                    is_not_found = "404" in exc_str

                    if is_quota:
                        record_nvidia_cooldown(current_model, 60.0, "429 RATE_LIMIT")
                    elif is_unavailable:
                        record_nvidia_cooldown(current_model, 30.0, "503 UNAVAILABLE")
                    elif is_not_found:
                        record_nvidia_cooldown(current_model, 86400.0, "404 NOT_FOUND")
                    elif "timeout" in exc_str.lower():
                        record_nvidia_cooldown(current_model, 15.0, "TIMEOUT")

                    is_transient = is_quota or is_unavailable or is_not_found
                    if is_transient and m_idx < len(candidate_models) - 1:
                        next_m = candidate_models[m_idx + 1]
                        logger.warning(f"NVIDIA model '{current_model}' failed ({exc_str[:100]}), failover to '{next_m}'")
                        break

                    if attempt < attempts and not is_quota:
                        backoff = compute_backoff(attempt)
                        logger.warning(f"NVIDIA {current_model} retry wait {backoff:.1f}s")
                        time.sleep(backoff)
                        continue

                    if m_idx == len(candidate_models) - 1:
                        if is_quota:
                            raise DataHuntError(
                                ErrorCode.MODEL_RATE_LIMITED,
                                user_message="NVIDIA NIM rate limit exceeded. Please try again shortly.",
                                operator_message=f"NVIDIA 429 rate limit: {exc}"
                            )
                        elif is_unavailable:
                            raise DataHuntError(
                                ErrorCode.MODEL_UNAVAILABLE,
                                user_message="NVIDIA NIM service is temporarily unavailable.",
                                operator_message=f"NVIDIA service unavailable: {exc}"
                            )
                        else:
                            raise DataHuntError(
                                ErrorCode.INTERNAL_ERROR,
                                user_message="An error occurred communicating with NVIDIA AI model.",
                                operator_message=f"NVIDIA exception: {exc}"
                            )

    @traceable(run_type="llm", name="DataHunt.NvidiaTextCall")
    def _call_nvidia_text(self, prompt: str, stage: str = "general") -> Optional[str]:
        """Call NVIDIA NIM requesting raw text output with model failover."""
        if not self.is_live:
            return None

        candidate_models = self.get_candidate_models_for_stage(stage)
        for m_idx, current_model in enumerate(candidate_models):
            cooldown_left = get_nvidia_cooldown_remaining(current_model)
            if cooldown_left > 0:
                if m_idx < len(candidate_models) - 1:
                    continue
                elif cooldown_left <= 2.0:
                    time.sleep(cooldown_left)

            _pace_nvidia_request(0.15)

            # Track active model and notify subscribers in real time
            if current_model != getattr(self, "last_active_model", None):
                prev_model = getattr(self, "last_active_model", self.primary_model)
                self.last_active_model = current_model
                if callable(getattr(self, "on_model_switch", None)):
                    try:
                        self.on_model_switch(current_model, f"Failover switch from {prev_model}")
                    except Exception as ex:
                        logger.debug(f"Error in on_model_switch callback: {ex}")
            else:
                self.last_active_model = current_model

            try:
                response = self._client.chat.completions.create(
                    model=current_model,
                    messages=[
                        {"role": "system", "content": SHARED_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                choice = response.choices[0]
                text = choice.message.content or ""
                if not text.strip() and hasattr(choice.message, "reasoning_content"):
                    text = getattr(choice.message, "reasoning_content", "") or ""
                if text.strip():
                    return text.strip()
            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str:
                    record_nvidia_cooldown(current_model, 60.0, "429 RATE_LIMIT")
                elif any(c in exc_str for c in ("500", "502", "503", "504")):
                    record_nvidia_cooldown(current_model, 30.0, "503 UNAVAILABLE")
                logger.warning(f"NVIDIA text generation failed on '{current_model}' ({exc_str[:100]}), failover...")
                continue
        return None

    # Backward compatibility aliases for existing codebase callers
    def _call_gemini_json(self, prompt: str, schema_description: str = "json", stage: str = "general") -> Any:
        return self._call_nvidia_json(prompt, schema_description=schema_description, stage=stage)

    def _call_gemini_text(self, prompt: str, stage: str = "general") -> Optional[str]:
        return self._call_nvidia_text(prompt, stage=stage)

    @traceable(run_type="chain", name="DataHunt.NormalizeRequest")
    def normalize_request(self, request_text: str, operator_defaults: Optional[Dict[str, Any]] = None) -> ResearchSpec:
        """Convert natural language request into a validated ResearchSpec."""
        defaults = operator_defaults or {}
        max_records = defaults.get("max_records", 50)
        freshness_days = defaults.get("freshness_days", 7)
        output_format = defaults.get("output_format", "json")
        contact_policy = defaults.get("contact_policy", "business_public_only")
        allowed_domains = defaults.get("allowed_domains", [])
        blocked_domains = defaults.get("blocked_domains", [])

        agent_mode = defaults.get("agent_mode")
        from datahunt.agents.intent_router import IntentRouter
        router = IntentRouter(gemini_client=self if getattr(self, "is_live", False) else None)
        intent_spec = router.classify(request_text, agent_mode)

        if intent_spec.intent == ResearchIntent.JOB_SEARCH and agent_mode != "research":
            default_fields = ["title", "company", "location", "salary", "application_url", "description", "skills"]
            default_assumptions = ["Targeting public job boards and official company careers pages"]
            resolved_mode = "jobs"
        elif intent_spec.intent == ResearchIntent.MARKET_RESEARCH:
            default_fields = ["company_name", "product_name", "pricing_model", "target_audience", "key_features", "website_url"]
            default_assumptions = ["Autonomous market research and competitive feature landscape mapping"]
            resolved_mode = "market"
        else:
            default_fields = [
                "topic", "section_title", "summary", "key_points", "source_url", "source_title",
                "name", "category", "core_capabilities"
            ]
            default_assumptions = [f"Synthesizing direct, evidence-backed research answer for '{intent_spec.intent.value}'"]
            resolved_mode = "research" if agent_mode in (None, "auto", "research") else agent_mode

        # Determine geographic intent
        req_lower = request_text.lower()
        geo_names = []
        geo_country = None
        if any(k in req_lower for k in ("saudi", "riyadh", "jeddah", "ksa", "dammam")):
            geo_names.append("Saudi Arabia")
            geo_country = "SA"
        if any(k in req_lower for k in ("dubai", "uae", "abu dhabi", "emirates", "sharjah")):
            geo_names.append("UAE")
            if not geo_country:
                geo_country = "AE"
        if any(k in req_lower for k in ("india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai")):
            geo_names.append("India")
            if not geo_country:
                geo_country = "IN"
        if any(k in req_lower for k in ("london", "uk", "united kingdom", "england")):
            geo_names.append("UK")
            if not geo_country:
                geo_country = "GB"
        if any(k in req_lower for k in ("us", "usa", "united states", "america", "san francisco", "new york")):
            geo_names.append("USA")
            if not geo_country:
                geo_country = "US"
        if any(k in req_lower for k in ("singapore",)):
            geo_names.append("Singapore")
            if not geo_country:
                geo_country = "SG"
        if any(k in req_lower for k in ("germany", "berlin", "munich")):
            geo_names.append("Germany")
            if not geo_country:
                geo_country = "DE"
        if any(k in req_lower for k in ("canada", "toronto", "vancouver")):
            geo_names.append("Canada")
            if not geo_country:
                geo_country = "CA"

        if len(geo_names) > 1:
            geo_name = " OR ".join(geo_names)
            geo_country = None
        elif len(geo_names) == 1:
            geo_name = geo_names[0]
        else:
            geo_name = None

        if self.is_live:
            try:
                prompt = INTAKE_USER_TEMPLATE.format(
                    request_text=request_text,
                    max_records=max_records,
                    freshness_days=freshness_days,
                    output_format=output_format,
                    contact_policy=contact_policy,
                    allowed_domains=json.dumps(allowed_domains),
                    blocked_domains=json.dumps(blocked_domains),
                )
                data = self._call_nvidia_json(prompt, schema_description="ResearchSpec schema", stage="intake")
                if not isinstance(data, dict):
                    data = {}
                if "topic" not in data or not data["topic"]:
                    data["topic"] = data.get("task_name") or data.get("query") or data.get("name") or request_text
                if "max_records" not in data:
                    data["max_records"] = max_records
                data["agent_mode"] = resolved_mode

                if resolved_mode == "jobs":
                    data["requested_fields"] = ["title", "company", "location", "salary", "application_url", "description", "skills"]
                elif resolved_mode == "market":
                    data["requested_fields"] = ["company_name", "product_name", "pricing_model", "target_audience", "key_features", "website_url"]
                elif resolved_mode == "research":
                    data["requested_fields"] = ["name", "category", "description", "core_capabilities", "key_components", "documentation_url"]
                elif "requested_fields" not in data or not data["requested_fields"]:
                    data["requested_fields"] = default_fields

                if geo_name and (not data.get("geography") or not (isinstance(data.get("geography"), dict) and data["geography"].get("name"))):
                    data["geography"] = {"name": geo_name, "country": geo_country}

                data["intent_spec"] = intent_spec
                return ResearchSpec(**data)
            except Exception as exc:
                logger.warning(f"Live NVIDIA request normalization failed ({exc}), falling back to deterministic parser")

        return ResearchSpec(
            status="ready",
            topic=request_text,
            geography=Geography(name=geo_name, country=geo_country),
            date_filter=DateFilter(kind="posted_at", after=None, before=None),
            requested_fields=default_fields,
            max_records=max_records,
            agent_mode=resolved_mode,
            source_policy=SourcePolicy(allowed_domains=allowed_domains, blocked_domains=blocked_domains),
            contact_policy=contact_policy,
            quality_bar="every required field needs evidence or null",
            assumptions=default_assumptions,
            intent_spec=intent_spec,
        )

    @traceable(run_type="chain", name="DataHunt.PlanResearch")
    def plan_research(self, spec: ResearchSpec, budget: RunBudget) -> Dict[str, Any]:
        """Create search queries and research plan from specification."""
        if self.is_live:
            try:
                prompt = PLANNER_TEMPLATE.format(
                    research_spec_json=spec.model_dump_json(),
                    budget_json=budget.model_dump_json(),
                    max_queries=budget.max_search_queries,
                )
                plan = self._call_nvidia_json(prompt, schema_description="Planner schema", stage="plan")
                if isinstance(plan, list):
                    plan = {"queries": plan}
                elif not isinstance(plan, dict):
                    plan = {}
                raw_q = plan.get("queries") or plan.get("search_queries") or []
                norm_q = []
                for q in raw_q:
                    if isinstance(q, str):
                        norm_q.append({"query": q, "purpose": "discovery", "expected_source_type": "technical_doc"})
                    elif isinstance(q, dict):
                        norm_q.append(q)
                if norm_q:
                    plan["queries"] = norm_q
                    return plan
            except Exception as e:
                logger.warning(f"Live NVIDIA planner failed ({e}), generating deterministic high-yield queries...")

        topic = spec.topic or "Technology Research"
        intent_spec = getattr(spec, "intent_spec", None)
        if not intent_spec:
            from datahunt.agents.intent_router import IntentRouter
            intent_spec = IntentRouter(gemini_client=self if getattr(self, "is_live", False) else None).classify(topic, getattr(spec, "agent_mode", "auto"))

        is_job_query = (intent_spec.intent == ResearchIntent.JOB_SEARCH) and (getattr(spec, "agent_mode", None) != "research")
        is_market_query = (intent_spec.intent == ResearchIntent.MARKET_RESEARCH)
        is_how_to = (intent_spec.intent == ResearchIntent.HOW_TO)
        is_explanation = (intent_spec.intent == ResearchIntent.EXPLANATION)
        is_comparison = (intent_spec.intent == ResearchIntent.COMPARISON)
        is_factual = (intent_spec.intent == ResearchIntent.FACTUAL_RESEARCH)

        if is_job_query:
            geo_name = spec.geography.name if spec.geography and spec.geography.name else ""
            geo_str = f" {geo_name}" if geo_name and geo_name.lower() not in topic.lower() else ""

            clean_topic = topic.strip()
            clean_topic = re.sub(r"\b(?:having|with|minimum|at least)?\s*\d+\+?\s*(?:years?|yrs?)(?:\s*of)?(?:\s*experience)?\b", "", clean_topic, flags=re.I)
            clean_topic = re.sub(r"\b(?:find|search|get|me|i want|looking for|need a|show me)\b", "", clean_topic, flags=re.I)
            clean_topic = re.sub(r"\b(?:jobs? in|jobs? for|hiring for|hiring in|openings? in|vacanc(?:y|ies) in)\b", "", clean_topic, flags=re.I)
            clean_topic = re.sub(r"\b(?:jobs?|careers?|vacanc(?:y|ies)|openings?|roles?|positions?)\b", "", clean_topic, flags=re.I)
            clean_topic = re.sub(r"\s+", " ", clean_topic).strip()
            if not clean_topic:
                clean_topic = "AI Engineer"

            queries = [
                {"query": f"{clean_topic}{geo_str} site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:workable.com",
                 "purpose": "ats_harvest", "expected_source_type": "ats_portal"},
                {"query": f"{clean_topic}{geo_str} site:job-boards.greenhouse.io",
                 "purpose": "greenhouse_harvest", "expected_source_type": "ats_portal"},
                {"query": f"{clean_topic}{geo_str} site:jobs.lever.co",
                 "purpose": "lever_harvest", "expected_source_type": "ats_portal"},
                {"query": f"{clean_topic}{geo_str} site:jobs.ashbyhq.com",
                 "purpose": "ashby_harvest", "expected_source_type": "ats_portal"},
                {"query": f"{clean_topic}{geo_str} site:apply.workable.com",
                 "purpose": "workable_harvest", "expected_source_type": "ats_portal"},
                {"query": f"{clean_topic}{geo_str} site:boards.greenhouse.io",
                 "purpose": "greenhouse_boards", "expected_source_type": "ats_portal"},
                {"query": f"{clean_topic}{geo_str} site:linkedin.com/jobs",
                 "purpose": "linkedin_harvest", "expected_source_type": "job_board"},
                {"query": f"{clean_topic}{geo_str} site:indeed.com",
                 "purpose": "indeed_harvest", "expected_source_type": "job_board"},
                {"query": f"{clean_topic}{geo_str} site:glassdoor.com/job",
                 "purpose": "glassdoor_harvest", "expected_source_type": "job_board"},
                {"query": f"{clean_topic}{geo_str} site:wellfound.com",
                 "purpose": "wellfound_harvest", "expected_source_type": "startup_board"},
                {"query": f"{clean_topic}{geo_str} site:remoteok.com",
                 "purpose": "remoteok_harvest", "expected_source_type": "remote_board"},
                {"query": f"{clean_topic}{geo_str} site:weworkremotely.com",
                 "purpose": "weworkremotely_harvest", "expected_source_type": "remote_board"},
                {"query": f"{clean_topic}{geo_str} site:bayt.com",
                 "purpose": "bayt_harvest", "expected_source_type": "regional_board"},
                {"query": f"{clean_topic}{geo_str} site:naukrigulf.com",
                 "purpose": "naukrigulf_harvest", "expected_source_type": "regional_board"},
                {"query": f"{clean_topic}{geo_str} site:gulftalent.com",
                 "purpose": "gulftalent_harvest", "expected_source_type": "regional_board"},
                {"query": f"{clean_topic}{geo_str} careers site:careers page jobs apply",
                 "purpose": "company_careers", "expected_source_type": "company_careers"},
                {"query": f"\"{clean_topic}\"{geo_str} \"apply now\" OR \"apply here\" -site:linkedin.com",
                 "purpose": "direct_apply_harvest", "expected_source_type": "company_careers"},
                {"query": f"{clean_topic}{geo_str} jobs posted today",
                 "purpose": "fresh_harvest_today", "expected_source_type": "job_board"},
                {"query": f"{clean_topic}{geo_str} jobs posted this week",
                 "purpose": "fresh_harvest_week", "expected_source_type": "job_board"},
                {"query": f"{clean_topic}{geo_str} full-time hiring salary",
                 "purpose": "fulltime_salary_sweep", "expected_source_type": "job_board"},
            ]
        elif is_market_query or getattr(spec, "agent_mode", None) == "market":
            from datahunt.tools.search import generate_market_research_queries
            clean_topic = topic.strip().rstrip("?").strip()
            queries = [
                {"query": q, "purpose": "market_intelligence", "expected_source_type": "pricing_page"}
                for q in generate_market_research_queries(clean_topic)
            ]
        elif is_how_to:
            clean_topic = re.sub(r"^(?:how\s+(?:do|can|to)\s+(?:i\s+)?)\s*", "", topic, flags=re.I).strip().rstrip("?").strip()
            queries = [
                {"query": f"how to {clean_topic} step by step guide documentation", "purpose": "step_by_step_guide", "expected_source_type": "guide"},
                {"query": f"{clean_topic} best practices official documentation", "purpose": "best_practices", "expected_source_type": "official_doc"},
                {"query": f"{clean_topic} requirements tutorial architecture", "purpose": "architecture_tutorial", "expected_source_type": "technical_doc"},
            ]
        elif is_explanation:
            clean_topic = re.sub(r"^(?:what\s+is|explain|how\s+does)\s+", "", topic, flags=re.I).strip().rstrip("?").strip()
            queries = [
                {"query": f"{clean_topic} architecture technical documentation overview", "purpose": "technical_overview", "expected_source_type": "official_doc"},
                {"query": f"{clean_topic} explained key concepts internals", "purpose": "internals_explanation", "expected_source_type": "technical_doc"},
            ]
        else:
            clean_topic = topic.strip().rstrip("?").strip()
            queries = [
                {"query": f"{clean_topic} documentation guide overview", "purpose": "general_overview", "expected_source_type": "technical_doc"},
                {"query": f"{clean_topic} architecture specifications", "purpose": "specifications", "expected_source_type": "official_doc"},
            ]

        return {"queries": queries[:budget.max_search_queries]}

    @traceable(run_type="chain", name="DataHunt.TriageSearchHits")
    def triage_search_hits(self, spec: ResearchSpec, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rank and prioritize search hits based on spec."""
        if not hits:
            return []
        if self.is_live:
            try:
                prompt = SEARCH_TRIAGE_TEMPLATE.format(
                    research_spec_json=spec.model_dump_json(),
                    search_hits_json=json.dumps(hits[:30]),
                )
                triage_res = self._call_nvidia_json(prompt, schema_description="Triage schema", stage="triage")
                if isinstance(triage_res, list):
                    return triage_res
                elif isinstance(triage_res, dict) and "hits" in triage_res:
                    return triage_res["hits"]
            except Exception as e:
                logger.warning(f"Live NVIDIA search triage failed ({e}), using deterministic heuristic")

        # Deterministic triage heuristic
        scored = []
        for h in hits:
            url = h.get("url", "").lower()
            snippet = h.get("snippet", "").lower()
            title = h.get("title", "").lower()
            score = 0.5
            if any(ats in url for ats in ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com")):
                score += 0.4
            elif any(b in url for b in ("linkedin.com/jobs", "indeed.com", "bayt.com", "naukrigulf.com")):
                score += 0.3
            if spec.geography and spec.geography.name and spec.geography.name.lower() in (snippet + title):
                score += 0.2
            scored.append({"url": h.get("url"), "title": h.get("title"), "snippet": h.get("snippet"), "priority_score": min(score, 1.0)})
        scored.sort(key=lambda x: x["priority_score"], reverse=True)
        return scored

    @traceable(run_type="chain", name="DataHunt.ExtractFromDocument")
    def extract_from_document(
        self,
        *args,
        document: Any = None,
        spec: Optional[ResearchSpec] = None,
        doc_text: Optional[str] = None,
        doc_metadata: Optional[Dict[str, Any]] = None,
        record_schema: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Extract structured records from fetched page text with universal parameter support."""
        if args:
            if len(args) == 2 and (hasattr(args[0], "extracted_text") or hasattr(args[0], "content_text")):
                document = args[0]
                spec = args[1]
            elif len(args) >= 1 and isinstance(args[0], ResearchSpec):
                spec = args[0]
                if len(args) >= 2 and doc_text is None:
                    doc_text = args[1]
                if len(args) >= 3 and doc_metadata is None:
                    doc_metadata = args[2]
                if len(args) >= 4 and record_schema is None:
                    record_schema = args[3]
            elif len(args) >= 2 and isinstance(args[1], ResearchSpec):
                document = args[0]
                spec = args[1]

        content = (
            doc_text
            or (getattr(document, "extracted_text", "") if document else "")
            or (getattr(document, "content_text", "") if document else "")
            or (str(document) if isinstance(document, str) else "")
        )
        url = (
            (doc_metadata.get("url") if doc_metadata else "")
            or (getattr(document, "canonical_url", "") if document else "")
            or (getattr(document, "requested_url", "") if document else "")
            or (getattr(document, "url", "") if document else "")
        )
        title = (
            (doc_metadata.get("title") if doc_metadata else "")
            or (getattr(document, "title", "") if document else "")
        )
        doc_id = (
            (doc_metadata.get("id") if doc_metadata else "")
            or (getattr(document, "id", "") if document else "")
            or "doc_unknown"
        )

        if not content.strip():
            return {"records": [], "document_warnings": []}

        if self.is_live:
            try:
                bounded_text = content[:settings.MAX_EXTRACTED_CHARS]
                meta_payload = {"url": url, "title": title, "doc_id": doc_id}
                schema_payload = record_schema or {"requested_fields": spec.requested_fields if spec else []}
                spec_payload = spec.model_dump_json() if spec and hasattr(spec, "model_dump_json") else json.dumps(getattr(spec, "__dict__", {}))
                prompt = EXTRACTION_TEMPLATE.format(
                    research_spec_json=spec_payload,
                    record_schema_json=json.dumps(schema_payload),
                    document_metadata_json=json.dumps(meta_payload),
                    bounded_document_text=bounded_text,
                )
                extracted = self._call_nvidia_json(prompt, schema_description="Extraction schema", stage="extract")
                raw_list = []
                doc_warns = []
                if isinstance(extracted, dict):
                    raw_list = extracted.get("records") or []
                    doc_warns = extracted.get("document_warnings") or []
                elif isinstance(extracted, list):
                    raw_list = extracted

                valid_records = []
                for r in raw_list:
                    if isinstance(r, dict) and any(v for v in r.values() if v):
                        r["document_id"] = doc_id
                        r["source_url"] = url
                        valid_records.append(r)
                if valid_records:
                    return {"records": valid_records, "document_warnings": doc_warns}
            except Exception as e:
                logger.warning(f"Live NVIDIA extraction failed ({e}), using deterministic extractor")

        # Deterministic offline extractor
        from datahunt.tools.extract import _heuristic_extract_job
        fields = _heuristic_extract_job(content, url, title)
        if fields:
            fields["document_id"] = doc_id
            fields["source_url"] = url
            return {"records": [fields], "document_warnings": []}
        return {"records": [], "document_warnings": []}

    @traceable(run_type="chain", name="DataHunt.VerifyRecord")
    def verify_record(
        self,
        record: Any,
        evidence: List[Any],
        required_fields: Optional[List[str]] = None,
        freshness_rule: Optional[Dict[str, Any]] = None,
        contact_policy: str = "business_public_only",
        **kwargs
    ) -> Dict[str, Any]:
        """Verify record against captured evidence."""
        rec_data = record if isinstance(record, dict) else (
            record.data if hasattr(record, "data") else (
                record.fields if hasattr(record, "fields") else {}
            )
        )
        ev_data = evidence if isinstance(evidence, list) else []
        req_f = required_fields or ["title", "company"]
        fresh_rule = freshness_rule or {}

        if self.is_live:
            try:
                prompt = VERIFICATION_TEMPLATE.format(
                    record_json=json.dumps(rec_data),
                    evidence_json=json.dumps(ev_data[:10]),
                    required_fields_json=json.dumps(req_f),
                    freshness_rule_json=json.dumps(fresh_rule),
                    contact_policy=contact_policy,
                )
                return self._call_nvidia_json(prompt, schema_description="Verification schema", stage="verify")
            except Exception as e:
                logger.warning(f"Live NVIDIA verification failed ({e}), using deterministic verification")

        # Deterministic verification
        url = rec_data.get("application_url") or rec_data.get("source_url") or rec_data.get("website_url")
        title = rec_data.get("title") or rec_data.get("job_title") or rec_data.get("name")
        if url and title:
            return {"status": "verified", "confidence": 0.9, "reasoning": "Primary record attributes backed by evidence URL"}
        return {"status": "unverified", "confidence": 0.5, "reasoning": "Missing application URL or title"}

    @traceable(run_type="chain", name="DataHunt.CompareRecordsForDedupe")
    def compare_records_for_dedupe(self, r1: Any, r2: Any) -> Dict[str, Any]:
        """Compare two records for semantic duplicate detection."""
        d1 = r1 if isinstance(r1, dict) else (r1.data if hasattr(r1, "data") else {})
        d2 = r2 if isinstance(r2, dict) else (r2.data if hasattr(r2, "data") else {})

        if self.is_live:
            try:
                prompt = DEDUPLICATION_TEMPLATE.format(
                    record_a_json=json.dumps(d1),
                    record_b_json=json.dumps(d2),
                )
                return self._call_nvidia_json(prompt, schema_description="Dedupe schema", stage="dedupe")
            except Exception as e:
                logger.warning(f"Live NVIDIA dedupe comparison failed ({e}), using deterministic comparison")

        # Deterministic dedupe check
        u1 = (d1.get("application_url") or d1.get("source_url") or "").lower().rstrip("/")
        u2 = (d2.get("application_url") or d2.get("source_url") or "").lower().rstrip("/")
        if u1 and u2 and u1 == u2:
            return {"is_duplicate": True, "confidence": 1.0, "reason": "Exact URL match"}

        t1 = (d1.get("title") or d1.get("job_title") or "").lower()
        t2 = (d2.get("title") or d2.get("job_title") or "").lower()
        c1 = (d1.get("company") or d1.get("company_name") or "").lower()
        c2 = (d2.get("company") or d2.get("company_name") or "").lower()
        if t1 and t2 and c1 and c2 and t1 == t2 and c1 == c2:
            return {"is_duplicate": True, "confidence": 0.95, "reason": "Matching title and company"}

        return {"is_duplicate": False, "confidence": 0.9, "reason": "Distinct attributes"}

    @traceable(run_type="chain", name="DataHunt.SummarizeRun")
    def summarize_run(
        self,
        *args,
        query: str = "",
        query_text: str = "",
        run_metadata: Optional[Dict[str, Any]] = None,
        verified_records: Optional[List[Dict[str, Any]]] = None,
        source_texts: str = "",
        spec: Optional[ResearchSpec] = None,
        intent_spec: Optional[Any] = None,
        agent_mode: str = "auto",
        **kwargs
    ) -> str:
        """Generate structured executive research summary or dossier with universal signature support."""
        if args:
            if isinstance(args[0], dict):
                run_metadata = args[0]
                if len(args) > 1 and verified_records is None:
                    verified_records = args[1]
                if len(args) > 2 and not source_texts:
                    source_texts = args[2]
                if len(args) > 3 and not query_text:
                    query_text = args[3]
            elif isinstance(args[0], str):
                query = args[0]
                if len(args) > 1 and run_metadata is None and isinstance(args[1], dict):
                    run_metadata = args[1]
                if len(args) > 2 and verified_records is None:
                    verified_records = args[2]
                if len(args) > 3 and not source_texts:
                    source_texts = args[3]

        resolved_query = (
            query_text
            or query
            or (run_metadata.get("request_text") if run_metadata else "")
            or (run_metadata.get("topic") if run_metadata else "")
            or "Research Analysis"
        )
        run_meta = run_metadata or {}
        rec_list = verified_records or []

        resolved_mode = agent_mode if agent_mode != "auto" else run_meta.get("agent_mode", "auto")
        if not intent_spec:
            from datahunt.agents.intent_router import IntentRouter
            intent_spec = IntentRouter(gemini_client=self if getattr(self, "is_live", False) else None).classify(resolved_query, resolved_mode)

        is_job = (intent_spec and intent_spec.intent == ResearchIntent.JOB_SEARCH) or (spec and spec.agent_mode == "jobs") or (resolved_mode == "jobs")
        is_market = (intent_spec and intent_spec.intent == ResearchIntent.MARKET_RESEARCH) or (spec and spec.agent_mode == "market") or (resolved_mode == "market")
        is_answer = intent_spec and intent_spec.intent in (
            ResearchIntent.HOW_TO, ResearchIntent.EXPLANATION,
            ResearchIntent.FACTUAL_RESEARCH, ResearchIntent.COMPARISON
        )

        if self.is_live:
            try:
                if is_market:
                    prompt = MARKET_SYNTHESIS_TEMPLATE.format(
                        query_text=resolved_query,
                        run_metadata_json=json.dumps(run_meta),
                        verified_records_json=json.dumps(rec_list[:20]),
                        source_texts=source_texts[:15000] if source_texts else "No source text captured.",
                    )
                elif is_answer or (spec and spec.agent_mode == "research") or resolved_mode == "research":
                    prompt = DIRECT_ANSWER_SYNTHESIS_TEMPLATE.format(
                        query_text=resolved_query,
                        run_metadata_json=json.dumps(run_meta),
                        verified_records_json=json.dumps(rec_list[:20]),
                        source_texts=source_texts[:15000] if source_texts else "No source text captured.",
                    )
                else:
                    prompt = SUMMARY_TEMPLATE.format(
                        run_metadata_json=json.dumps(run_meta),
                        verified_records_json=json.dumps(rec_list[:20]),
                    )

                text_output = self._call_nvidia_text(prompt, stage="summarize")
                if text_output and len(text_output.strip()) > 100:
                    return text_output.strip()
            except Exception as e:
                logger.warning(f"Live NVIDIA synthesis failed ({e}), falling back to deterministic dossier generator")

        # Deterministic synthesis
        if is_market:
            return synthesize_local_market_dossier(
                query=resolved_query,
                run_metadata=run_meta,
                verified_records=rec_list,
                source_texts=source_texts,
            )
        if is_answer:
            return synthesize_direct_answer(
                query=resolved_query,
                run_metadata=run_meta,
                verified_records=rec_list,
                source_texts=source_texts,
                intent_spec=intent_spec,
            )
        return synthesize_local_research_dossier(
            query=resolved_query,
            run_metadata=run_meta,
            verified_records=rec_list,
            source_texts=source_texts,
        )
