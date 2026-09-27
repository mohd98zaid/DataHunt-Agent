"""
Common Shared Evidence Model for DataHunt Agents.
Used by Job Agent, Research Agent, and Market Agent.
Domain-specific metadata can be attached in the `metadata` dictionary.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import uuid


class Evidence(BaseModel):
    """
    Canonical evidence item connecting a claim or factual extraction to its primary source.
    """
    id: str = Field(default_factory=lambda: f"evi_{uuid.uuid4().hex[:12]}")
    claim: str
    source_url: str = ""
    source_title: str = ""
    source_type: str = "web"  # official_doc, primary, company, news, market_data, etc.
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    published_at: Optional[str] = None
    evidence_text: Optional[str] = None
    confidence: float = 0.8
    sentiment: Optional[str] = "neutral"  # bullish, bearish, neutral
    is_counter_evidence: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)
