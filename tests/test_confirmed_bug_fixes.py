import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from datahunt.agent.policies import QualificationPolicy
from datahunt.agents import JobSearchRequest
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.sources.crawlers import _safe_get
from datahunt.tools.extract import extract_jsonld_job_posting
from datahunt.tools.fetch import FetchTool, clear_fetch_cache
from datahunt.tools.verify import check_geographic_compliance


def test_safe_get_validates_redirect_target_before_following():
    redirect = httpx.Response(
        302,
        headers={"Location": "http://127.0.0.1/private"},
        request=httpx.Request("GET", "https://public.test/start"),
    )
    validation_calls = []

    def validate(url):
        validation_calls.append(url)
        if "127.0.0.1" in url:
            raise DataHuntError(
                ErrorCode.FETCH_BLOCKED,
                user_message="private target",
                operator_message="private redirect target",
            )
        return url, "93.184.216.34"

    with patch("datahunt.sources.crawlers.validate_url_ssrf", side_effect=validate), \
         patch("httpx.Client.get", return_value=redirect) as mock_get:
        assert _safe_get("https://public.test/start") is None

    assert validation_calls == [
        "https://public.test/start",
        "http://127.0.0.1/private",
    ]
    mock_get.assert_called_once()


def test_fetch_enforces_max_bytes_during_stream_read(monkeypatch):
    clear_fetch_cache()
    consumed = []

    def iter_bytes(chunk_size):
        consumed.append(b"<html><body>")
        yield consumed[-1]
        consumed.append(b"x" * 100)
        yield consumed[-1]
        raise AssertionError("stream should stop once the byte limit is crossed")

    response = SimpleNamespace(
        status_code=200,
        headers={"Content-Type": "text/html"},
        is_redirect=False,
        encoding="utf-8",
        iter_bytes=iter_bytes,
    )

    class StreamingClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def build_request(self, method, url):
            return (method, url)

        def send(self, request, stream=False):
            assert stream is True
            return response

    monkeypatch.setattr(
        "datahunt.tools.fetch.validate_url_ssrf",
        lambda url: (url, "93.184.216.34"),
    )
    monkeypatch.setattr("datahunt.tools.fetch.httpx.Client", StreamingClient)

    result = FetchTool(max_bytes=20).execute("https://public.test/job")

    assert result.success is True
    assert result.data.policy_flags == ["truncated_bytes"]
    assert len(b"".join(consumed)) > 20


def test_fetch_enforces_redirect_limit_without_an_extra_request(monkeypatch):
    clear_fetch_cache()
    requests_sent = []

    redirect = httpx.Response(
        302,
        headers={"Location": "/next"},
        request=httpx.Request("GET", "https://public.test/start"),
    )
    final = httpx.Response(
        200,
        headers={"Content-Type": "text/html"},
        content=b"<html><title>Job</title><body>AI Engineer</body></html>",
        request=httpx.Request("GET", "https://public.test/next"),
    )

    class RedirectClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def build_request(self, method, url):
            return (method, url)

        def send(self, request, stream=False):
            requests_sent.append(request)
            return redirect if len(requests_sent) == 1 else final

    monkeypatch.setattr("datahunt.tools.fetch.validate_url_ssrf", lambda url: (url, "93.184.216.34"))
    monkeypatch.setattr("datahunt.tools.fetch.httpx.Client", RedirectClient)

    result = FetchTool(max_redirects=1).execute("https://public.test/start")

    assert result.success is False
    assert result.error_message == "Too many redirects."
    assert len(requests_sent) == 1


def test_geographic_aliases_match_tokens_not_substrings():
    assert check_geographic_compliance("Berlin, Germany", "India") is False
    assert check_geographic_compliance("Toronto, Canada", "USA") is False
    assert check_geographic_compliance("Bangalore, India", "India") is True
    assert check_geographic_compliance("New York, USA", "USA") is True


def test_undated_jsonld_listing_keeps_unknown_freshness_and_fails_24h_filter():
    posting = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "AI Engineer",
        "hiringOrganization": {"name": "Example AI"},
        "jobLocation": {"address": {"addressLocality": "Dubai", "addressCountry": "UAE"}},
        "url": "https://jobs.example.test/ai-engineer",
    }
    doc_text = (
        "--- STRUCTURED JOB SCHEMA (JSON-LD) ---\n"
        f"{json.dumps(posting)}\n"
        "--- END STRUCTURED SCHEMA ---"
    )
    records = extract_jsonld_job_posting(
        doc_text,
        {"domain": "jobs.example.test", "url": posting["url"]},
    )

    fields = records[0]["fields"]
    assert fields["posted_at"] is None
    assert fields["posted_age_seconds"] is None
    assert fields["freshness_badge"] == "UNKNOWN"

    result = QualificationPolicy().qualify(
        fields,
        JobSearchRequest(
            raw_query="AI Engineer jobs in Dubai 24h",
            job_title="AI Engineer",
            target_locations=["Dubai"],
            freshness_days=1,
        ),
    )
    assert result.qualified is False
    assert any(reason.startswith("unverified_date:") for reason in result.rejection_reasons)
