import pytest
from datahunt.config import Settings
from datahunt.errors import DataHuntError, ErrorCode, compute_backoff

def test_settings_defaults():
    settings = Settings()
    assert settings.APP_NAME == "DataHunt"
    assert settings.MAX_RUN_SECONDS > 0
    assert settings.MAX_PAGES_FETCHED > 0
    safe_dict = settings.to_safe_dict()
    assert "gemini_key_masked" in safe_dict
    assert "gemini_key" not in safe_dict

def test_settings_masking():
    settings = Settings()
    settings.GEMINI_API_KEY = "AIzaSyD1234567890abcdef"
    masked = settings.masked_gemini_key()
    assert masked.startswith("AIza")
    assert masked.endswith("cdef")
    assert "..." in masked

def test_error_envelope():
    err = DataHuntError(
        code=ErrorCode.FETCH_TIMEOUT,
        user_message="Page timed out",
        operator_message="GET https://bad.site exceeded 20s",
        run_id="run_123",
        attempt=2
    )
    d_user = err.to_dict(include_operator=False)
    assert d_user["code"] == "FETCH_TIMEOUT"
    assert d_user["retryable"] is True
    assert "operator_message" not in d_user

    d_op = err.to_dict(include_operator=True)
    assert "operator_message" in d_op

def test_compute_backoff():
    d1 = compute_backoff(1, base_delay=1.0, max_delay=10.0, jitter=0.2)
    assert 1.0 <= d1 <= 1.2
    d2 = compute_backoff(2, base_delay=1.0, max_delay=10.0, jitter=0.2)
    assert 2.0 <= d2 <= 2.2
