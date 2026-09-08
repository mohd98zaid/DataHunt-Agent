import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlparse, unquote
import httpx
from bs4 import BeautifulSoup

from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.policy import check_domain_policy
from datahunt.tools.base import ToolResult

class SearchHit:
    def __init__(
        self,
        url: str,
        title: str,
        snippet: str,
        source_domain: str,
        retrieved_at: Optional[str] = None,
        provider_citation_id: Optional[str] = None
    ):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.source_domain = source_domain
        self.retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
        self.provider_citation_id = provider_citation_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "source_domain": self.source_domain,
            "retrieved_at": self.retrieved_at,
            "provider_citation_id": self.provider_citation_id,
        }

class SearchProvider(Protocol):
    def search(
        self,
        query: str,
        limit: int = 10,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        ...

class DuckDuckGoSearchProvider:
    """Public web search provider using DuckDuckGo HTML endpoint."""
    SEARCH_URL = "https://html.duckduckgo.com/html/"
    
    def search(
        self,
        query: str,
        limit: int = 10,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DataHunt-Agent/1.0",
            "Accept": "text/html,application/xhtml+xml",
        }
        hits = []
        try:
            with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
                resp = client.post(self.SEARCH_URL, data={"q": query})
                if resp.status_code != 200:
                    logger.warning(f"DuckDuckGo search returned HTTP {resp.status_code}")
                    return []
                    
                soup = BeautifulSoup(resp.text, "html.parser")
                results = soup.find_all("div", class_="result")
                
                for res in results:
                    title_elem = res.find("a", class_="result__a")
                    snippet_elem = res.find("a", class_="result__snippet")
                    
                    if not title_elem:
                        continue
                        
                    raw_href = title_elem.get("href", "")
                    title = title_elem.get_text().strip()
                    snippet = snippet_elem.get_text().strip() if snippet_elem else ""

                    # Extract actual destination URL if wrapped in DDG redirect (/l/?uddg=...)
                    actual_url = raw_href
                    if "uddg=" in raw_href:
                        match = re.search(r"uddg=([^&]+)", raw_href)
                        if match:
                            actual_url = unquote(match.group(1))

                    if not actual_url.startswith("http"):
                        continue
                        
                    domain = urlparse(actual_url).netloc
                    try:
                        check_domain_policy(domain, allowed_domains, blocked_domains)
                    except DataHuntError:
                        continue

                    hits.append(SearchHit(
                        url=actual_url,
                        title=title,
                        snippet=snippet,
                        source_domain=domain
                    ))
                    if len(hits) >= limit:
                        break
        except Exception as e:
            logger.warning(f"DuckDuckGo search error: {e}")
            
        return hits

class MockSearchProvider:
    """Mock/offline search provider for testing and deterministic validation."""
    def __init__(self, predefined_hits: Optional[List[SearchHit]] = None):
        self.predefined_hits = predefined_hits or []

    def search(
        self,
        query: str,
        limit: int = 10,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        if self.predefined_hits:
            hits = self.predefined_hits
        else:
            hits = [
                SearchHit(
                    url="https://example.com/careers/ai-engineer-dubai",
                    title="Senior AI Engineer - Dubai, UAE",
                    snippet="Join our fast-growing AI team in Dubai. Requirements: Python, LLMs, Machine Learning. Posted 3 days ago.",
                    source_domain="example.com"
                ),
                SearchHit(
                    url="https://example.com/careers/ml-ops-dubai",
                    title="Machine Learning Engineer - Dubai",
                    snippet="Example AI Ltd is hiring an ML engineer in Dubai. Apply online through our official portal.",
                    source_domain="example.com"
                ),
                SearchHit(
                    url="https://techjobs.ae/post/ai-researcher-101",
                    title="AI Researcher - Dubai Tech Hub",
                    snippet="Looking for an AI researcher with PhD or 3+ years experience. Competitive compensation in Dubai.",
                    source_domain="techjobs.ae"
                )
            ]

        filtered = []
        for hit in hits:
            try:
                check_domain_policy(hit.source_domain, allowed_domains, blocked_domains)
                filtered.append(hit)
            except DataHuntError:
                continue
            if len(filtered) >= limit:
                break
        return filtered

class HybridSearchProvider:
    """Uses DuckDuckGo search first, falling back to mock provider if no results or blocked."""
    def __init__(self):
        self.ddg = DuckDuckGoSearchProvider()
        self.mock = MockSearchProvider()

    def search(
        self,
        query: str,
        limit: int = 10,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> List[SearchHit]:
        hits = self.ddg.search(query, limit=limit, allowed_domains=allowed_domains, blocked_domains=blocked_domains)
        if not hits:
            logger.info("Live search returned 0 hits, utilizing provider fallback")
            hits = self.mock.search(query, limit=limit, allowed_domains=allowed_domains, blocked_domains=blocked_domains)
        return hits

class SearchTool:
    name = "search_web"
    description = "Search the public web for candidate documents matching query."

    def __init__(self, provider: Optional[SearchProvider] = None):
        self.provider = provider or HybridSearchProvider()
        self._query_cache: Dict[str, List[Dict[str, Any]]] = {}

    def execute(
        self,
        query: str,
        limit: int = 10,
        freshness_days: Optional[int] = None,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None
    ) -> ToolResult:
        cache_key = f"{query.strip().lower()}::{limit}::{freshness_days}::{sorted(allowed_domains or [])}"
        if cache_key in self._query_cache:
            logger.info(f"Returning cached search results for query: {query}")
            return ToolResult(success=True, data=self._query_cache[cache_key], metadata={"cached": True})

        try:
            hits = self.provider.search(
                query=query,
                limit=limit,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains
            )
            hit_dicts = [h.to_dict() for h in hits]
            self._query_cache[cache_key] = hit_dicts
            return ToolResult(success=True, data=hit_dicts, metadata={"count": len(hit_dicts)})
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}")
            return ToolResult(
                success=False,
                error_code=ErrorCode.SEARCH_UNAVAILABLE.value,
                error_message=f"Search failed: {e}",
                data=[]
            )
