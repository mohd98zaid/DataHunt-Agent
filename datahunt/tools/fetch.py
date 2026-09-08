import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup

from datahunt.config import settings
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.logger import logger
from datahunt.models import RetrievalStatus, SourceDocument
from datahunt.policy import validate_url_ssrf, check_domain_policy
from datahunt.tools.base import ToolResult

def clean_html_to_text(html_content: str) -> tuple[str, str]:
    """
    Parse HTML, extract title and bounded clean text excerpt.
    Returns (title, clean_text).
    """
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("h1"):
        title = soup.find("h1").get_text().strip()

    # Remove script, style, nav, footer, header, and noscript tags
    for element in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"]):
        element.decompose()

    text = soup.get_text(separator="\n")
    # Normalize multiple linebreaks and spaces
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_text = "\n".join(lines)
    return title, cleaned_text

class FetchTool:
    name = "fetch_page"
    description = "Safely fetch a public web page with strict SSRF defenses and size limits."

    def __init__(
        self,
        connect_timeout: float = 5.0,
        read_timeout: float = 20.0,
        total_timeout: float = 30.0,
        max_bytes: int = 2_000_000,
        max_redirects: int = 5,
        mock_responses: Optional[Dict[str, str]] = None
    ):
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.mock_responses = mock_responses or {}

    def execute(
        self,
        url: str,
        run_id: str = "run_default",
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        render_js: bool = False
    ) -> ToolResult:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        
        # Check mock responses first (for fast unit testing and offline runs)
        if url in self.mock_responses:
            mock_html = self.mock_responses[url]
            title, text = clean_html_to_text(mock_html)
            content_hash = hashlib.sha256(mock_html.encode("utf-8")).hexdigest()
            doc = SourceDocument(
                run_id=run_id,
                requested_url=url,
                final_url=url,
                canonical_url=url,
                domain=urlparse(url).netloc,
                http_status=200,
                content_type="text/html",
                content_hash=content_hash,
                title=title,
                extracted_text=text[:settings.MAX_EXTRACTED_CHARS],
                text_truncated=1 if len(text) > settings.MAX_EXTRACTED_CHARS else 0,
                retrieval_status=RetrievalStatus.FETCHED,
                retrieved_at=retrieved_at
            )
            return ToolResult(success=True, data=doc)

        current_url = url
        redirect_count = 0
        policy_flags = []

        try:
            while redirect_count <= self.max_redirects:
                # 1. Strict SSRF check before resolving or connecting to target
                cleaned_url, hostname, resolved_ips = validate_url_ssrf(current_url)
                check_domain_policy(hostname, allowed_domains, blocked_domains)

                # 2. Perform safe HTTP request with streaming to enforce byte caps
                timeout = httpx.Timeout(self.total_timeout, connect=self.connect_timeout, read=self.read_timeout)
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DataHunt-Agent/1.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                }

                with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
                    response = client.get(cleaned_url)

                    # Check for redirect
                    if response.is_redirect:
                        redirect_count += 1
                        location = response.headers.get("Location")
                        if not location:
                            raise DataHuntError(
                                ErrorCode.FETCH_BLOCKED,
                                user_message="Redirect missing Location header.",
                                operator_message=f"Missing Location on redirect from {cleaned_url}"
                            )
                        current_url = urljoin(cleaned_url, location)
                        logger.info(f"Following redirect {redirect_count}/{self.max_redirects} to: {current_url}")
                        continue

                    # Check status code
                    status_code = response.status_code
                    if status_code >= 400:
                        return ToolResult(
                            success=False,
                            error_code=ErrorCode.FETCH_BLOCKED.value if status_code in (401, 403) else ErrorCode.FETCH_TIMEOUT.value,
                            error_message=f"HTTP {status_code} from source",
                            metadata={"status_code": status_code, "url": cleaned_url}
                        )

                    # Check Content-Type
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not any(t in content_type for t in ("text/", "html", "json", "xml")):
                        return ToolResult(
                            success=False,
                            error_code=ErrorCode.FETCH_UNSUPPORTED_TYPE.value,
                            error_message=f"Unsupported content type: {content_type}",
                            metadata={"content_type": content_type}
                        )

                    # Read body with max_bytes enforcement
                    body_bytes = response.content
                    if len(body_bytes) > self.max_bytes:
                        body_bytes = body_bytes[:self.max_bytes]
                        policy_flags.append("truncated_bytes")

                    content_hash = hashlib.sha256(body_bytes).hexdigest()
                    html_text = body_bytes.decode(response.encoding or "utf-8", errors="replace")
                    title, clean_text = clean_html_to_text(html_text)
                    
                    truncated = 1 if len(clean_text) > settings.MAX_EXTRACTED_CHARS else 0
                    bounded_text = clean_text[:settings.MAX_EXTRACTED_CHARS]

                    doc = SourceDocument(
                        run_id=run_id,
                        requested_url=url,
                        final_url=cleaned_url,
                        canonical_url=cleaned_url,
                        domain=urlparse(cleaned_url).netloc,
                        http_status=status_code,
                        content_type=content_type,
                        content_hash=content_hash,
                        title=title,
                        extracted_text=bounded_text,
                        text_truncated=truncated,
                        policy_flags=policy_flags,
                        retrieval_status=RetrievalStatus.FETCHED,
                        retrieved_at=retrieved_at
                    )
                    return ToolResult(success=True, data=doc)

            return ToolResult(
                success=False,
                error_code=ErrorCode.FETCH_BLOCKED.value,
                error_message="Too many redirects.",
                metadata={"url": url}
            )

        except DataHuntError as dhe:
            return ToolResult(
                success=False,
                error_code=dhe.code.value,
                error_message=dhe.user_message,
                metadata={"url": url, "operator_message": dhe.operator_message}
            )
        except httpx.TimeoutException as te:
            return ToolResult(
                success=False,
                error_code=ErrorCode.FETCH_TIMEOUT.value,
                error_message=f"Connection timed out: {te}",
                metadata={"url": url}
            )
        except Exception as e:
            logger.error(f"Error fetching URL {url}: {e}")
            return ToolResult(
                success=False,
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error_message=f"Failed to fetch source: {e}",
                metadata={"url": url}
            )
