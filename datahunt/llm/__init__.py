# Gemini client (commented out as default in favor of NvidiaClient, but kept available):
# from datahunt.llm.gemini_client import GeminiClient
from datahunt.llm.gemini_client import GeminiClient
from datahunt.llm.nvidia_client import NvidiaClient

# Default LLM Client for DataHunt Workstation
LLMClient = NvidiaClient
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
    REFUSAL_SAFETY_TEMPLATE,
)

__all__ = [
    "NvidiaClient",
    "LLMClient",
    "GeminiClient",
    "SHARED_SYSTEM_PROMPT",
    "INTAKE_USER_TEMPLATE",
    "PLANNER_TEMPLATE",
    "SEARCH_TRIAGE_TEMPLATE",
    "EXTRACTION_TEMPLATE",
    "VERIFICATION_TEMPLATE",
    "DEDUPLICATION_TEMPLATE",
    "SUMMARY_TEMPLATE",
    "RESEARCH_SYNTHESIS_TEMPLATE",
    "REFUSAL_SAFETY_TEMPLATE",
]
