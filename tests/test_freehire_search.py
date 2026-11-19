from unittest.mock import MagicMock, patch

from datahunt.tools.search import FreeHireSearchProvider, HybridSearchProvider, SearchProviderRegistry


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def test_freehire_provider_maps_public_jobs_to_search_hits():
    provider = FreeHireSearchProvider(
        base_url="https://freehire.test/api/v1",
        enabled=True,
        max_requests=2,
    )
    payload = {
        "data": [{
            "public_slug": "senior-ai-engineer-acme",
            "title": "Senior AI Engineer",
            "company": "Acme AI",
            "location": "Dubai, UAE",
            "url": "https://jobs.example.com/acme/123",
            "description": "<p>Build production AI systems.</p>",
        }]
    }

    with patch("datahunt.tools.search.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _response(payload)
        hits = provider.search('site:boards.greenhouse.io "AI Engineer" Dubai', limit=5)

    assert len(hits) == 1
    assert hits[0].url == "https://jobs.example.com/acme/123"
    assert hits[0].source_domain == "freehire.me"
    assert "Acme AI" in hits[0].title
    assert "Build production AI systems." in hits[0].snippet
    request = client_cls.return_value.__enter__.return_value.get.call_args
    assert request.kwargs["params"]["q"] == '"AI Engineer" Dubai'
    assert request.kwargs["params"]["q_fields"] == "title,company,description,location"


def test_freehire_provider_translates_region_and_experience_filters():
    provider = FreeHireSearchProvider(
        base_url="https://freehire.test/api/v1",
        enabled=True,
        max_requests=1,
    )

    with patch("datahunt.tools.search.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _response({"data": []})
        provider.search("AI Engineer jobs in Saudi Arabia or UAE with 0-6 years experience", limit=2)

    params = client_cls.return_value.__enter__.return_value.get.call_args.kwargs["params"]
    assert params["countries"] == ["sa", "ae"]
    assert params["experience_years_min"] == 0
    assert params["experience_years_max"] == 6


def test_freehire_provider_applies_domain_policy_to_result_urls():
    provider = FreeHireSearchProvider(
        base_url="https://freehire.test/api/v1",
        enabled=True,
        max_requests=1,
    )
    payload = {"data": [{
        "public_slug": "role",
        "title": "AI Engineer",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
    }]}

    with patch("datahunt.tools.search.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _response(payload)
        hits = provider.search("AI Engineer jobs", limit=1, allowed_domains=["greenhouse.io"])

    assert len(hits) == 1


def test_freehire_provider_uses_catalogue_page_for_direct_apply_jobs():
    provider = FreeHireSearchProvider(
        base_url="https://freehire.test/api/v1",
        enabled=True,
        max_requests=1,
    )
    payload = {"data": [{"public_slug": "community-role", "title": "AI Engineer"}]}

    with patch("datahunt.tools.search.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _response(payload)
        hits = provider.search("AI Engineer jobs", limit=1)

    assert hits[0].url == "https://freehire.me/jobs/community-role"


def test_hybrid_search_prefers_freehire_for_job_queries_and_falls_back():
    provider = HybridSearchProvider(fallback_to_mock=False)
    provider.freehire = MagicMock()
    provider.ddg = MagicMock()
    provider.job_board = MagicMock()
    provider.llm_search = MagicMock()
    provider.freehire.search.return_value = []
    provider.ddg.search.return_value = []
    provider.job_board.search.return_value = []
    provider.llm_search.search.return_value = []

    provider.search('AI Engineer jobs in Dubai', limit=3)

    provider.freehire.search.assert_called_once()
    provider.ddg.search.assert_called_once()


def test_freehire_provider_is_available_from_registry():
    registry = SearchProviderRegistry()

    assert isinstance(registry.get("freehire"), FreeHireSearchProvider)
    assert registry.get("fh") is registry.get("freehire")
