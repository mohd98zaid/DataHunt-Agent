import time
import pytest
from datahunt.llm.gemini_client import (
    GeminiClient,
    FREE_TIER_LITE_POOL,
    FREE_TIER_FLASH_POOL,
    FREE_TIER_PRO_POOL,
    ALL_FREE_TIER_MODELS,
    record_model_cooldown,
    get_model_cooldown_remaining,
    reset_model_cooldowns,
    _pace_request,
)


@pytest.fixture(autouse=True)
def clean_cooldowns():
    reset_model_cooldowns()
    yield
    reset_model_cooldowns()


def test_model_pool_definitions():
    """Verify model pools contain distinct, active free-tier models."""
    assert len(FREE_TIER_LITE_POOL) >= 3
    assert len(FREE_TIER_FLASH_POOL) >= 3
    assert len(ALL_FREE_TIER_MODELS) >= 6
    assert "gemini-flash-lite-latest" in FREE_TIER_LITE_POOL
    assert "gemini-flash-latest" in FREE_TIER_FLASH_POOL


def test_stage_routing_lite_vs_flash():
    """High-throughput tasks should route to Lite pool; reasoning tasks to Flash pool."""
    client = GeminiClient(api_key="dummy-key", model="auto")

    # High-throughput stage
    lite_candidates = client.get_candidate_models_for_stage("extract")
    assert lite_candidates[0] in FREE_TIER_LITE_POOL

    # Deep-reasoning stage
    reasoning_candidates = client.get_candidate_models_for_stage("summarize")
    assert reasoning_candidates[0] in FREE_TIER_FLASH_POOL


def test_round_robin_rotation():
    """Consecutive calls rotate healthy candidates to distribute quota."""
    client = GeminiClient(api_key="dummy-key", model="auto")

    first_order = client.get_candidate_models_for_stage("extract")
    second_order = client.get_candidate_models_for_stage("extract")

    # If pool has > 1 healthy model, consecutive top models should rotate
    if len(FREE_TIER_LITE_POOL) > 1:
        assert first_order[0] != second_order[0]


def test_cooldown_demotes_failing_model():
    """Models marked in cooldown must be demoted behind healthy alternatives."""
    client = GeminiClient(api_key="dummy-key", model="auto")

    # Put primary model on cooldown
    target = FREE_TIER_LITE_POOL[0]
    record_model_cooldown(target, 60.0, "Test 429 quota")

    assert get_model_cooldown_remaining(target) > 0.0

    candidates = client.get_candidate_models_for_stage("extract")

    # The cooling model must NOT be the first candidate when healthy alternatives exist
    assert candidates[0] != target
    # The cooling model must still be in the candidate list as a last resort
    assert target in candidates


def test_micro_pacing_throttling():
    """Micro-pacing ensures back-to-back calls have minimal delay to avoid burst limits."""
    t0 = time.time()
    _pace_request(0.08)
    _pace_request(0.08)
    elapsed = time.time() - t0
    assert elapsed >= 0.07
