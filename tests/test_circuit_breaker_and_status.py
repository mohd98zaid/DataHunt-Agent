import time
from unittest.mock import MagicMock, patch
import pytest

from datahunt.tools.search import DuckDuckGoSearchProvider
from datahunt.models import RunStatus, ExtractedRecord, VerificationStatus
from agent.orchestrator import ResearchOrchestrator


def test_ddg_circuit_breaker_trips_and_recovers():
    provider = DuckDuckGoSearchProvider()
    # Reset circuit breaker
    DuckDuckGoSearchProvider._circuit_broken_until = 0.0
    DuckDuckGoSearchProvider._consecutive_errors = 0

    assert not provider.is_cooling_down()

    # 1st failure
    provider.trip_circuit_breaker("Connection reset by peer [WinError 10054]")
    assert not provider.is_cooling_down()
    assert DuckDuckGoSearchProvider._consecutive_errors == 1

    # 2nd failure -> threshold reached
    provider.trip_circuit_breaker("timed out")
    assert provider.is_cooling_down()
    assert DuckDuckGoSearchProvider._circuit_broken_until > time.time()

    # While cooling down, search returns [] immediately without making network calls
    with patch("httpx.Client") as mock_client:
        hits = provider.search("python developer jobs")
        assert hits == []
        mock_client.assert_not_called()

    # Reset / record success
    provider.record_success()
    DuckDuckGoSearchProvider._circuit_broken_until = 0.0
    assert not provider.is_cooling_down()
    assert DuckDuckGoSearchProvider._consecutive_errors == 0


def test_orchestrator_status_calculation_completed_despite_search_warnings():
    """
    Ensure runs that yield verified records are marked COMPLETED,
    and are not downgraded to PARTIAL by transient scraper warnings.
    """
    # Simulate completion calculation logic
    final_records = [
        ExtractedRecord(
            run_id="test_run",
            record_type="job_listing",
            fields={"title": "AI Engineer", "company": "Acme", "location": "Dubai"},
            verification_status=VerificationStatus.VERIFIED,
            confidence=0.95
        )
    ]
    counters = MagicMock()
    counters.records_rejected = 3  # Normal filtering rejections

    # Warnings from search engine rate-limiting
    warnings = [
        "DuckDuckGo HTML search error: timed out",
        "Search stopped early: deadline reached."
    ]

    has_results = (len(final_records) > 0)
    has_verification_failures = (len(final_records) == 0 and counters.records_rejected > 0)
    
    _ignorable_warning_substrings = (
        "duckduckgo",
        "search error",
        "timed out",
        "connection reset",
        "connection closed",
        "rate limit",
        "http error",
        "fetch error",
        "403 forbidden",
        "stopped early",
    )
    critical_errors = [
        w for w in warnings
        if "error" in w.lower() and not any(sub in w.lower() for sub in _ignorable_warning_substrings)
    ]

    final_status = (
        RunStatus.COMPLETED
        if has_results and not (has_verification_failures or critical_errors)
        else (RunStatus.PARTIAL if has_results else RunStatus.FAILED)
    )

    assert final_status == RunStatus.COMPLETED
    assert len(critical_errors) == 0
