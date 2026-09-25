import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from datahunt.api import app
from datahunt.models import ExtractedRecord, VerificationStatus, ExportRecord
from datahunt.policy import escape_csv_formula, is_public_business_email
from datahunt.tools.export import _discover_display_columns, _collect_unique_sources
from datahunt.tools.search import SearchTool
from agent.base import BaseAgent
from agent.job_agent import JobHuntAgent
from agent.research_agent import DeepResearchAgent

client = TestClient(app)

def test_escape_csv_formula_extended():
    """Verify formula injection protection with leading whitespace and symbols."""
    assert escape_csv_formula("   =CMD('calc')") == "'   =CMD('calc')"
    assert escape_csv_formula("\t=1+1") == "'\t=1+1"
    assert escape_csv_formula("\r+data") == "'\r+data"
    assert escape_csv_formula("%0A") == "'%0A"
    assert escape_csv_formula("Normal Text") == "Normal Text"
    assert escape_csv_formula(None) == ""

def test_expanded_generic_business_email():
    """Verify newly added business email prefixes (recruiting, talent, etc.)."""
    assert is_public_business_email("recruiting@google.com") is True
    assert is_public_business_email("talent@netflix.com") is True
    assert is_public_business_email("people@stripe.com") is True
    assert is_public_business_email("apply@anthropic.com") is True
    assert is_public_business_email("user@gmail.com") is False

def test_export_display_helpers():
    """Verify deduplicated export helpers for column extraction and source collection."""
    r1 = ExtractedRecord(
        run_id="run_1",
        record_type="tech",
        fields={"framework": "LangChain", "language": "Python", "stars": 95000, "freshness_badge": "0-SEC"},
        canonical_url="https://github.com/langchain-ai/langchain"
    )
    r2 = ExtractedRecord(
        run_id="run_1",
        record_type="tech",
        fields={"framework": "LlamaIndex", "license": "MIT", "language": "Python"},
        canonical_url="https://github.com/run-llama/llama_index"
    )
    r3 = ExtractedRecord(
        run_id="run_1",
        record_type="tech",
        fields={"framework": "LangGraph"},
        canonical_url="https://github.com/langchain-ai/langchain"  # duplicate URL
    )

    cols = _discover_display_columns([r1, r2, r3], max_cols=3)
    assert len(cols) <= 3
    assert "freshness_badge" not in cols
    assert "framework" in cols

    sources = _collect_unique_sources([r1, r2, r3])
    assert len(sources) == 2  # Deduplicated
    assert "https://github.com/langchain-ai/langchain" in sources
    assert "https://github.com/run-llama/llama_index" in sources

def test_search_tool_cache_key_includes_blocked_domains():
    """Verify that SearchTool cache key differentiates between different blocked_domains."""
    tool = SearchTool()
    tool.provider.search = MagicMock(return_value=[])

    tool.execute(query="python engineer", limit=5, blocked_domains=["evil.com"])
    tool.execute(query="python engineer", limit=5, blocked_domains=["bad.com"])

    # Should have called search twice because blocked_domains differed
    assert tool.provider.search.call_count == 2

def test_api_invalid_verification_status_returns_400():
    """Verify get_run_records returns clean 400 instead of unhandled 500 when status is invalid."""
    resp = client.get("/runs/nonexistent_run_id/records?status=INVALID_STATUS_VALUE")
    assert resp.status_code == 400
    assert "Invalid verification status" in resp.json()["detail"]

def test_base_agent_lazy_orchestrator():
    """Verify BaseAgent lazily creates orchestrator only when needed."""
    agent = JobHuntAgent(model="auto")
    assert agent._orchestrator is None
    # Accessing .orchestrator instantiates it
    orch = agent.orchestrator
    assert orch is not None
    assert agent._orchestrator is not None

def test_agent_subclass_defaults():
    """Verify agent subclasses inherit and properly define defaults."""
    job_agent = JobHuntAgent()
    assert job_agent.default_max_records == 50
    assert job_agent.default_freshness_days == 1
    assert job_agent.default_output_format == "json"

    research_agent = DeepResearchAgent()
    assert research_agent.default_max_records == 20
    assert research_agent.default_freshness_days == 30
    assert research_agent.default_output_format == "md"
