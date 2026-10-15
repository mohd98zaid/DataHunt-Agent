import json
import pytest
from unittest.mock import MagicMock, patch

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.llm.nvidia_client import (
    NvidiaClient,
    _clean_json_text,
    record_nvidia_cooldown,
    get_nvidia_cooldown_remaining,
    _pace_nvidia_request,
)
from datahunt.models import ResearchSpec, RunBudget, DateFilter, Geography, SourcePolicy
from datahunt.models.intent import ResearchIntent, ResearchIntentSpec


def test_clean_json_text_pure_json():
    raw = '{"status": "ok", "count": 10}'
    assert _clean_json_text(raw) == raw


def test_clean_json_text_markdown_fence():
    raw = "```json\n{\"status\": \"ok\", \"count\": 10}\n```"
    assert _clean_json_text(raw) == '{"status": "ok", "count": 10}'


def test_clean_json_text_reasoning_wrapper():
    raw = "Here is the result:\n```\n[{\"title\": \"AI Engineer\"}]\n```\nHope that helps!"
    res = _clean_json_text(raw)
    parsed = json.loads(res)
    assert isinstance(parsed, list)
    assert parsed[0]["title"] == "AI Engineer"


def test_nvidia_client_init_and_candidate_models():
    client = NvidiaClient(api_key="test-key", model="openai/gpt-oss-20b", fallback_model="nvidia/nemotron-3.5-lightning-30b-a3b")
    assert client.primary_model == "openai/gpt-oss-20b"
    assert client.fallback_model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert client.candidate_pool == ["openai/gpt-oss-20b", "nvidia/nemotron-3.5-lightning-30b-a3b"]

    candidates = client.get_candidate_models_for_stage("plan")
    assert candidates[0] == "openai/gpt-oss-20b"
    assert client.get_model_for_stage("plan") == "openai/gpt-oss-20b"
    assert client.get_fallback_model("openai/gpt-oss-20b") == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert client.get_fallback_model("nvidia/nemotron-3.5-lightning-30b-a3b") is None


def test_nvidia_client_cooldown_tracking():
    model = "test/model-cooling"
    record_nvidia_cooldown(model, 10.0, "testing cooldown")
    assert get_nvidia_cooldown_remaining(model) > 5.0


def test_nvidia_client_offline_normalize_request():
    client = NvidiaClient(api_key="")
    assert not client.is_live
    spec = client.normalize_request("Find GenAI Engineers in Saudi or UAE", {"max_records": 25})
    assert isinstance(spec, ResearchSpec)
    assert spec.max_records == 25
    assert spec.geography is not None
    assert "Saudi Arabia" in spec.geography.name
    assert "UAE" in spec.geography.name


def test_nvidia_client_offline_plan_research():
    client = NvidiaClient(api_key="")
    spec = client.normalize_request("Find GenAI Engineers in Saudi or UAE", operator_defaults={"agent_mode": "jobs"})
    budget = RunBudget(max_search_queries=15, max_pages_fetched=30)
    plan = client.plan_research(spec, budget)
    assert "queries" in plan
    assert len(plan["queries"]) <= 15
    assert any("greenhouse" in q["query"].lower() or "lever" in q["query"].lower() for q in plan["queries"])


def test_nvidia_client_offline_triage_hits():
    client = NvidiaClient(api_key="")
    spec = client.normalize_request("Find GenAI Engineers in Dubai")
    hits = [
        {"url": "https://random-blog.com/post", "title": "Random Blog", "snippet": "tech news"},
        {"url": "https://boards.greenhouse.io/corp/jobs/123", "title": "GenAI Engineer in Dubai", "snippet": "Join our AI team in Dubai"},
    ]
    triaged = client.triage_search_hits(spec, hits)
    assert len(triaged) == 2
    # Greenhouse hit with location match should have higher priority score
    assert triaged[0]["url"] == "https://boards.greenhouse.io/corp/jobs/123"
    assert triaged[0]["priority_score"] > triaged[1]["priority_score"]


def test_nvidia_client_call_nvidia_json_failover():
    client = NvidiaClient(api_key="test-key")
    client.is_live = True

    # Mock OpenAI client
    mock_openai = MagicMock()
    client._client = mock_openai

    # Primary model fails with 429 rate limit
    # Fallback model succeeds
    first_choice = MagicMock()
    second_choice = MagicMock()
    second_choice.message.content = '{"job_title": "GenAI Lead", "company": "TechCorp"}'
    second_choice.message.reasoning_content = None

    mock_openai.chat.completions.create.side_effect = [
        Exception("Error 429: Too Many Requests"),
        MagicMock(choices=[second_choice]),
    ]

    result = client._call_nvidia_json("test prompt", schema_description="Job JSON", stage="extract")
    assert result["job_title"] == "GenAI Lead"
    assert result["company"] == "TechCorp"
    assert mock_openai.chat.completions.create.call_count == 2


def test_nvidia_client_reasoning_model_fallback_parsing():
    client = NvidiaClient(api_key="test-key")
    client.is_live = True

    mock_openai = MagicMock()
    client._client = mock_openai

    # Test reasoning content extraction when content is empty
    choice = MagicMock()
    choice.message.content = ""
    choice.message.reasoning_content = "Thinking through... ```json\n{\"status\": \"verified\"}\n```"

    mock_openai.chat.completions.create.return_value = MagicMock(choices=[choice])

    result = client._call_nvidia_json("test prompt", stage="verify")
    assert result == {"status": "verified"}


def test_nvidia_client_backward_compat_aliases():
    client = NvidiaClient(api_key="test-key")
    client.is_live = True

    mock_openai = MagicMock()
    client._client = mock_openai

    choice = MagicMock()
    choice.message.content = '{"ok": true}'
    mock_openai.chat.completions.create.return_value = MagicMock(choices=[choice])

    # Test _call_gemini_json alias
    res_json = client._call_gemini_json("test json")
    assert res_json == {"ok": True}

    # Test _call_gemini_text alias
    choice.message.content = "Plain text answer"
    res_text = client._call_gemini_text("test text")
    assert res_text == "Plain text answer"


def test_nvidia_client_model_and_mode_properties():
    # Default auto
    client_auto = NvidiaClient(api_key="test-key", model="auto")
    assert client_auto.model == "openai/gpt-oss-20b"
    assert client_auto.mode == "auto"

    # Primary alias
    client_primary = NvidiaClient(api_key="test-key", model="primary")
    assert client_primary.model == "openai/gpt-oss-20b"
    assert client_primary.mode == "primary"

    # Fallback / Nemotron alias
    client_nemotron = NvidiaClient(api_key="test-key", model="nemotron")
    assert client_nemotron.model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert client_nemotron.mode == "fallback"


def test_nvidia_client_on_model_switch_callback():
    switches = []

    def on_switch(new_model, reason):
        switches.append((new_model, reason))

    client = NvidiaClient(api_key="test-key", on_model_switch=on_switch)
    client.is_live = True

    mock_openai = MagicMock()
    client._client = mock_openai

    # Make first call succeed with fallback model to trigger switch
    choice = MagicMock()
    choice.message.content = '{"ok": true}'
    mock_openai.chat.completions.create.return_value = MagicMock(choices=[choice])

    client._client.chat.completions.create = MagicMock(return_value=MagicMock(choices=[choice]))
    # Force cooldown on primary model so fallback is used
    record_nvidia_cooldown("openai/gpt-oss-20b", 30.0, "testing failover")
    
    result = client._call_nvidia_json("test failover prompt")
    assert result == {"ok": True}
    assert client.model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert len(switches) >= 1
    assert switches[0][0] == "nvidia/nemotron-3.5-lightning-30b-a3b"

