from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class MarketAssetType(str, Enum):
    EQUITY = "equity"
    INDEX = "index"
    SECTOR = "sector"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    UNKNOWN = "unknown"


class MarketTimeHorizon(str, Enum):
    INTRADAY = "intraday"
    ONE_DAY = "1_day"
    ONE_WEEK = "1_week"
    ONE_MONTH = "1_month"
    LONG_TERM = "long_term"


class MarketRegime(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    MIXED = "MIXED"


class ClaimType(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    COMPANY_FACT = "COMPANY_FACT"
    NEWS = "NEWS"
    ANALYST_OPINION = "ANALYST_OPINION"
    FORECAST = "FORECAST"
    LLM_INFERENCE = "LLM_INFERENCE"


class SourceTier(str, Enum):
    TIER_1 = "TIER_1"  # Official exchange, regulatory filing, official company IR
    TIER_2 = "TIER_2"  # Major financial publication, reputable market-data provider
    TIER_3 = "TIER_3"  # Established financial research site
    TIER_4 = "TIER_4"  # Unknown blog / forecast site


class FreshnessCategory(str, Enum):
    VERY_RECENT = "VERY_RECENT"  # < 24 hours
    RECENT = "RECENT"            # 1–3 days
    RELEVANT = "RELEVANT"        # 4–7 days
    CONTEXT = "CONTEXT"          # 8–30 days
    BACKGROUND = "BACKGROUND"    # > 30 days


class MarketIntentSpec(BaseModel):
    raw_query: str
    market: str = "NSE"
    asset_type: MarketAssetType = MarketAssetType.EQUITY
    country: str = "India"
    time_horizon: MarketTimeHorizon = MarketTimeHorizon.ONE_WEEK
    requested_count: int = 10
    objective: str = "identify stocks with evidence of potentially favorable near-term conditions"
    need_news: bool = True
    need_technical: bool = True
    need_fundamental: bool = True
    need_market_context: bool = True
    need_risk_analysis: bool = True


class MarketEvidence(BaseModel):
    id: str = Field(default_factory=lambda: f"mkt_evi_{uuid.uuid4().hex[:10]}")
    claim: str
    claim_type: ClaimType = ClaimType.NEWS
    source_url: str = ""
    source_name: str = ""
    source_tier: SourceTier = SourceTier.TIER_3
    timestamp: Optional[str] = None
    freshness: FreshnessCategory = FreshnessCategory.RELEVANT
    confidence: float = 0.8
    sentiment: str = "neutral"  # bullish, bearish, neutral
    raw_snippet: Optional[str] = None
    is_counter_evidence: bool = False


class MarketRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"mrec_{uuid.uuid4().hex[:12]}")
    symbol: str
    exchange: str = "NSE"
    company_name: str

    current_price: Optional[float] = None
    price_timestamp: Optional[str] = None

    change_1d: Optional[float] = None
    change_1w: Optional[float] = None
    change_1m: Optional[float] = None

    volume: Optional[int] = None
    average_volume: Optional[int] = None
    volume_ratio: Optional[float] = None

    market_cap: Optional[str] = None
    sector: Optional[str] = None

    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe: Optional[float] = None
    revenue_growth: Optional[float] = None
    profit_growth: Optional[float] = None

    technical_signals: Dict[str, Any] = Field(default_factory=dict)
    news_events: List[Dict[str, Any]] = Field(default_factory=list)
    corporate_actions: List[Dict[str, Any]] = Field(default_factory=list)
    analyst_views: List[Dict[str, Any]] = Field(default_factory=list)

    bullish_evidence: List[str] = Field(default_factory=list)
    bearish_evidence: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)

    source_evidence: List[MarketEvidence] = Field(default_factory=list)
    evidence_timestamp: Optional[str] = None

    signal_score: float = 0.0  # Deterministic score 0-100
    evidence_confidence: str = "MEDIUM"  # HIGH, MEDIUM, LOW
    signal_direction: str = "MIXED"  # POSITIVE, MIXED, HIGH_RISK, INSUFFICIENT_EVIDENCE
    score_breakdown: Dict[str, float] = Field(default_factory=dict)

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CandidateStage(str, Enum):
    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    DEEPLY_RESEARCHED = "DEEPLY_RESEARCHED"
    COUNTER_RESEARCHED = "COUNTER_RESEARCHED"
    SCORED = "SCORED"
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


class MarketCandidate(BaseModel):
    id: str = Field(default_factory=lambda: f"mcand_{uuid.uuid4().hex[:10]}")
    symbol: str
    exchange: str = "NSE"
    company_name: str
    sector: Optional[str] = None
    stage: CandidateStage = CandidateStage.DISCOVERED
    rejection_reason: Optional[str] = None

    discovery_channel: str = "general"  # momentum, volume, technical, fundamental, event, news
    sources_seen: List[str] = Field(default_factory=list)
    evidence_items: List[MarketEvidence] = Field(default_factory=list)

    prices: List[float] = Field(default_factory=list)
    volumes: List[float] = Field(default_factory=list)

    current_price: Optional[float] = None
    price_timestamp: Optional[str] = None
    change_1d: Optional[float] = None
    change_1w: Optional[float] = None
    change_1m: Optional[float] = None
    volume: Optional[int] = None
    average_volume: Optional[int] = None
    volume_ratio: Optional[float] = None

    market_cap: Optional[str] = None
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe: Optional[float] = None
    revenue_growth: Optional[float] = None
    profit_growth: Optional[float] = None

    technicals: Dict[str, Any] = Field(default_factory=dict)
    news_events: List[Dict[str, Any]] = Field(default_factory=list)
    corporate_actions: List[Dict[str, Any]] = Field(default_factory=list)
    analyst_views: List[Dict[str, Any]] = Field(default_factory=list)

    bullish_evidence: List[str] = Field(default_factory=list)
    bearish_evidence: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)

    signal_score: float = 0.0
    evidence_confidence: str = "LOW"
    signal_direction: str = "INSUFFICIENT_EVIDENCE"
    score_breakdown: Dict[str, float] = Field(default_factory=dict)


class MarketCoverage(BaseModel):
    exchanges_checked: List[str] = Field(default_factory=list)
    indices_checked: List[str] = Field(default_factory=list)
    sectors_checked: List[str] = Field(default_factory=list)
    news_sources_checked: List[str] = Field(default_factory=list)
    market_data_sources_checked: List[str] = Field(default_factory=list)
    company_sources_checked: List[str] = Field(default_factory=list)
    candidate_count: int = 0
    validated_candidate_count: int = 0
    deeply_researched_count: int = 0
    stop_reason: Optional[str] = None
    regime: MarketRegime = MarketRegime.MIXED
