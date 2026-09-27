"""
Comprehensive Test Suite for Market Intelligence Engine.

Covers:
- Golden Test: End-to-end multi-wave NSE top 10 stocks with mixed mock evidence
- Negative Test: Rejection of publishers/websites (Indiatimes, Businessworld, TradingView)
- Deduplication Test: Multi-source mentions merged into 1 canonical candidate
- Coverage Test: Dynamic source discovery without early fixed-budget termination
- Evidence Test: High-quality multi-source evidence outranks low-quality hype
- Technical Indicators Test: Deterministic calculations for SMA, EMA, RSI, MACD, ATR, volume ratio
- Intent Parsing Test: Structured intent extraction without hallucinated constraints
"""
import pytest
from unittest.mock import MagicMock

from datahunt.models.market import (
    MarketIntentSpec, MarketCandidate, MarketEvidence, SourceTier,
    FreshnessCategory, ClaimType, MarketAssetType, MarketTimeHorizon,
    CandidateStage
)
from datahunt.agents.market_intent import MarketIntentParser
from datahunt.agents.market_validator import MarketValidator, BANNED_ENTITIES
from datahunt.tools.market_analysis import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_macd,
    calculate_atr, calculate_volume_ratio, calculate_weekly_return,
    classify_freshness, classify_source_tier, score_market_candidate
)
from datahunt.agent.market_discovery import MarketDiscoveryEngine


# =============================================================================
# 1. INTENT PARSING TESTS (Section 3)
# =============================================================================
def test_market_intent_parser_nse_weekly():
    parser = MarketIntentParser()
    spec = parser.parse("give top 10 stock probably perform this week in nse")

    assert spec.market == "NSE"
    assert spec.country == "India"
    assert spec.asset_type == MarketAssetType.EQUITY
    assert spec.time_horizon == MarketTimeHorizon.ONE_WEEK
    assert spec.requested_count == 10
    assert spec.need_technical is True
    assert spec.need_news is True
    assert spec.need_risk_analysis is True


def test_market_intent_parser_bse_intraday():
    parser = MarketIntentParser()
    spec = parser.parse("best 5 intraday stocks for today in bse")

    assert spec.market == "BSE"
    assert spec.country == "India"
    assert spec.time_horizon == MarketTimeHorizon.ONE_DAY
    assert spec.requested_count == 5


# =============================================================================
# 2. NEGATIVE TESTS: REJECT WEBSITES & PUBLISHERS (Section 57)
# =============================================================================
def test_negative_rejects_publishers_and_websites():
    validator = MarketValidator()

    # Banned publishers & websites MUST be recognized as banned
    assert validator.is_banned_or_source("Indiatimes") is True
    assert validator.is_banned_or_source("Businessworld") is True
    assert validator.is_banned_or_source("TradingView") is True
    assert validator.is_banned_or_source("Moneycontrol") is True
    assert validator.is_banned_or_source("Reuters") is True
    assert validator.is_banned_or_source("IPO") is True
    assert validator.is_banned_or_source("Stock market article") is True

    # Normalization MUST return None for banned entities
    sym, name = validator.normalize_symbol("Indiatimes", "Indiatimes Tech")
    assert sym is None
    assert name is None

    sym, name = validator.normalize_symbol("Businessworld", "Businessworld Media")
    assert sym is None

    sym, name = validator.normalize_symbol("TradingView", "TradingView Charts")
    assert sym is None

    # When raw records contain publishers, validator filters them completely out
    raw_records = [
        {"symbol": "Indiatimes", "company_name": "Indiatimes", "url": "https://economictimes.indiatimes.com"},
        {"symbol": "Businessworld", "company_name": "Businessworld", "url": "https://businessworld.in"},
        {"symbol": "TradingView", "company_name": "TradingView", "url": "https://tradingview.com"},
        {"symbol": "TATAPOWER", "company_name": "Tata Power", "url": "https://nseindia.com"},
    ]
    validated = validator.validate_and_deduplicate(raw_records, exchange="NSE")

    # MUST only contain TATAPOWER, never Indiatimes or Businessworld
    assert len(validated) == 1
    assert validated[0].symbol == "TATAPOWER"
    assert validated[0].company_name == "Tata Power"


# =============================================================================
# 3. DEDUPLICATION TESTS (Section 59)
# =============================================================================
def test_deduplication_single_canonical_candidate_with_merged_evidence():
    validator = MarketValidator()

    # 5 independent sources mentioning Tata Power
    raw_records = [
        {
            "symbol": "TATAPOWER",
            "company_name": "Tata Power",
            "source_url": "https://nseindia.com/get-quotes/equity?symbol=TATAPOWER",
            "evidence": "NSE closing price ₹412.50 with 1.8x average volume surge.",
            "current_price": 412.50,
            "change_1w": 4.5,
        },
        {
            "symbol": "TATAPOWER",
            "company_name": "Tata Power Company Ltd",
            "source_url": "https://economictimes.indiatimes.com/tata-power/stocks",
            "evidence": "Tata Power secures mega solar order win in Rajasthan.",
        },
        {
            "symbol": "TATAPOWER.NS",
            "company_name": "Tata Power",
            "source_url": "https://moneycontrol.com/india/stockpricequote/power-generation-distribution/tatapower/TP",
            "evidence": "Moneycontrol technical setup shows breakout above 20 EMA.",
        },
        {
            "symbol": "TATAPOWER",
            "company_name": "Tata Power",
            "source_url": "https://tradingview.com/symbols/NSE-TATAPOWER/",
            "evidence": "RSI at 62 indicates healthy bullish momentum without overbought conditions.",
        },
        {
            "symbol": "TATAPOWER",
            "company_name": "Tata Power",
            "source_url": "https://tatapower.com/investor-relations/announcements",
            "evidence": "Official company filing confirms robust Q3 earnings growth.",
        },
    ]

    validated = validator.validate_and_deduplicate(raw_records, exchange="NSE")

    # MUST produce exactly 1 candidate!
    assert len(validated) == 1
    cand = validated[0]
    assert cand.symbol == "TATAPOWER"
    assert cand.company_name == "Tata Power"
    assert cand.current_price == 412.50
    assert cand.change_1w == 4.5
    # All 5 sources tracked
    assert len(cand.sources_seen) == 5
    # All 5 evidence items merged
    assert len(cand.evidence_items) == 5


# =============================================================================
# 4. DETERMINISTIC TECHNICAL ANALYSIS TESTS (Section 25)
# =============================================================================
def test_deterministic_sma_and_ema():
    # 25 constant prices
    flat_prices = [100.0] * 25
    assert calculate_sma(flat_prices, 20) == 100.0
    assert calculate_ema(flat_prices, 20) == 100.0

    # Trending prices: 1 to 20
    trend = [float(i) for i in range(1, 21)]
    sma = calculate_sma(trend, 20)
    assert sma == 10.5  # sum 1..20 = 210 / 20 = 10.5

    # Insufficient length returns None (does not crash or hallucinate)
    assert calculate_sma([10.0, 20.0], 20) is None
    assert calculate_ema([10.0, 20.0], 20) is None


def test_deterministic_rsi():
    # 15 upward price steps -> RSI should be high (near 100)
    up_prices = [100.0 + (i * 2.0) for i in range(20)]
    rsi_up = calculate_rsi(up_prices, 14)
    assert rsi_up is not None
    assert rsi_up > 80.0

    # 15 downward price steps -> RSI should be low (near 0)
    down_prices = [200.0 - (i * 2.0) for i in range(20)]
    rsi_down = calculate_rsi(down_prices, 14)
    assert rsi_down is not None
    assert rsi_down < 20.0

    # Insufficient data
    assert calculate_rsi([100.0, 105.0], 14) is None


def test_deterministic_macd():
    # 35 prices trending up
    prices = [100.0 + (i * 1.5) for i in range(40)]
    macd_res = calculate_macd(prices, fast=12, slow=26, signal_period=9)
    assert macd_res["macd"] is not None
    assert macd_res["signal"] is not None
    assert macd_res["histogram"] is not None
    # In strong uptrend, fast EMA > slow EMA, MACD line is positive
    assert macd_res["macd"] > 0


def test_deterministic_volume_ratio_and_weekly_return():
    # Volume ratio
    assert calculate_volume_ratio(2500000, 1000000) == 2.5
    assert calculate_volume_ratio(None, 1000000) is None

    # Weekly return
    prices = [100.0, 101.0, 102.0, 103.0, 105.0]
    assert calculate_weekly_return(prices) == 5.0


# =============================================================================
# 5. EVIDENCE QUALITY VS HYPE TEST (Section 60)
# =============================================================================
def test_evidence_quality_outranks_single_source_hype():
    intent = MarketIntentSpec(raw_query="top stock in nse", requested_count=10)

    # Candidate 1: 1 low-quality blog with hyper-bullish words
    cand_hype = MarketCandidate(
        symbol="HYPECORP",
        company_name="Hype Corp",
        exchange="NSE",
        sources_seen=["https://unknown-penny-stock-blog.com/post1"],
        change_1w=25.0,  # extreme chase
        evidence_items=[
            MarketEvidence(
                claim="Guaranteed multibagger stock will rise 500% tomorrow!",
                source_url="https://unknown-penny-stock-blog.com/post1",
                source_name="Unknown Blog",
                source_tier=SourceTier.TIER_4,
                sentiment="bullish",
            )
        ]
    )

    # Candidate 2: High quality multi-source verified stock
    cand_solid = MarketCandidate(
        symbol="TATAPOWER",
        company_name="Tata Power",
        exchange="NSE",
        sources_seen=[
            "https://nseindia.com/quote",
            "https://economictimes.indiatimes.com",
            "https://tradingview.com",
        ],
        current_price=415.0,
        change_1w=4.5,
        volume=2500000,
        average_volume=1200000,
        volume_ratio=2.08,
        sector="Energy & Power",
        technicals={"rsi": 62.5, "sma20": 405.0},
        revenue_growth=18.5,
        bullish_evidence=[
            "Official Q3 earnings beat with strong margin expansion.",
            "Consolidated above 20 EMA with 2x volume expansion.",
        ],
        evidence_items=[
            MarketEvidence(
                claim="NSE verified order win disclosures.",
                source_url="https://nseindia.com/quote",
                source_name="NSE India Official",
                source_tier=SourceTier.TIER_1,
                sentiment="bullish",
            ),
            MarketEvidence(
                claim="Economic Times notes sector tailwinds.",
                source_url="https://economictimes.indiatimes.com",
                source_name="Economic Times",
                source_tier=SourceTier.TIER_2,
                sentiment="bullish",
            ),
            MarketEvidence(
                claim="TradingView technical confirmation.",
                source_url="https://tradingview.com",
                source_name="TradingView",
                source_tier=SourceTier.TIER_2,
                sentiment="bullish",
            ),
        ]
    )

    score_hype, conf_hype, sig_hype, _ = score_market_candidate(cand_hype, intent)
    score_solid, conf_solid, sig_solid, _ = score_market_candidate(cand_solid, intent)

    # Solid multi-source candidate MUST have higher score and higher confidence
    assert score_solid > score_hype
    assert conf_solid == "HIGH"
    assert conf_hype == "LOW"
    assert sig_solid == "POSITIVE"


# =============================================================================
# 6. COUNTER-EVIDENCE & CONFLICT PENALTY TEST (Section 31 & 33)
# =============================================================================
def test_counter_evidence_penalizes_score_and_flags_risk():
    intent = MarketIntentSpec(raw_query="top stock in nse", requested_count=10)

    # Candidate with positive news BUT severe counter-evidence
    cand_conflicted = MarketCandidate(
        symbol="RISKYCORP",
        company_name="Risky Corp",
        exchange="NSE",
        sources_seen=["https://economictimes.indiatimes.com"],
        change_1w=2.0,
        risk_factors=[
            "Promoter pledging increased to 65%.",
            "SEBI forensic audit underway regarding revenue recognition.",
            "Facing strong technical resistance at 200 DMA.",
        ],
        conflicts=["Positive revenue growth vs active regulatory accounting investigation."],
        evidence_items=[
            MarketEvidence(
                claim="Revenue up 10% in quarterly results.",
                source_tier=SourceTier.TIER_2,
                sentiment="bullish",
            ),
            MarketEvidence(
                claim="SEBI scrutiny over debt repayment delays.",
                source_tier=SourceTier.TIER_1,
                sentiment="bearish",
                is_counter_evidence=True,
            ),
        ]
    )

    score, conf, sig_dir, breakdown = score_market_candidate(cand_conflicted, intent)

    # Risk deduction MUST be applied
    assert breakdown["risk_adjustment"] < 0
    # Signal direction should reflect the severe risk / conflict
    assert sig_dir in ("HIGH_RISK", "MIXED")


# =============================================================================
# 7. GOLDEN END-TO-END TEST (Section 56)
# =============================================================================
def test_golden_market_discovery_engine_end_to_end():
    # Mock search tool returning realistic multi-wave market results
    mock_search = MagicMock()

    def mock_execute(query, limit=12, **kwargs):
        q_lower = query.lower()
        res = MagicMock()
        res.success = True

        if "weekly trend" in q_lower or "benchmark" in q_lower:
            # Wave 1 context
            res.data = [
                {
                    "title": "Nifty 50 weekly outlook: Index consolidates near 24,800",
                    "url": "https://economictimes.indiatimes.com/markets/nifty-weekly-trend",
                    "snippet": "Nifty 50 showed constructive weekly breadth with bullish accumulation in energy and IT.",
                }
            ]
        elif "sectoral" in q_lower:
            # Wave 2 sector discovery
            res.data = [
                {
                    "title": "Sectoral leaders: Nifty Energy, IT, and Auto lead gains",
                    "url": "https://moneycontrol.com/news/business/sector-performance",
                    "snippet": "Nifty IT gained 2.4% while Energy gained 3.1% on strong institutional inflows.",
                }
            ]
        elif "counter_evidence" in kwargs.get("source_type", "") or "negative" in q_lower or "risk" in q_lower:
            # Wave 5 counter-evidence
            res.data = [
                {
                    "title": "Stock analysis: Key resistance levels and risks",
                    "url": "https://livemint.com/market/risks",
                    "snippet": "Near-term profit booking possible around upper resistance boundary.",
                }
            ]
        else:
            # Wave 3 & 4: candidates (TATAPOWER, RELIANCE, INFY, TCS, LT, plus bad entities like Indiatimes)
            res.data = [
                {
                    "title": "Tata Power (TATAPOWER) rallies on ₹1,200 crore order win",
                    "url": "https://nseindia.com/quote/TATAPOWER",
                    "snippet": "Tata Power share price touched ₹418.50 (+4.2%) with strong breakout momentum.",
                },
                {
                    "title": "Reliance Industries (RELIANCE) expands green energy capex",
                    "url": "https://economictimes.indiatimes.com/reliance",
                    "snippet": "Reliance gains 1.8% as retail and telecom operations show steady profit growth.",
                },
                {
                    "title": "Infosys (INFY) signs large enterprise GenAI deal",
                    "url": "https://business-standard.com/infosys-results",
                    "snippet": "Infosys shares rise with RSI at 61 confirming positive technical setup.",
                },
                {
                    "title": "Top Stock Market News Today on Indiatimes",
                    "url": "https://indiatimes.com/market-news",
                    "snippet": "Indiatimes brings you the latest stock market news and IPO analysis.",
                },
            ]
        return res

    mock_search.execute.side_effect = mock_execute
    mock_fetch = MagicMock()

    engine = MarketDiscoveryEngine(
        search_tool=mock_search,
        fetch_tool=mock_fetch,
        gemini_client=None,
    )

    result = engine.run_market_research(
        query="give top 10 stock probably perform this week in nse",
        max_runtime_seconds=30,
    )

    # 1. Intent correctly parsed
    assert result["intent"]["market"] == "NSE"
    assert result["intent"]["time_horizon"] == "1_week"
    assert result["intent"]["requested_count"] == 10

    # 2. Coverage tracked
    assert result["coverage"]["stop_reason"] in ("COVERAGE_COMPLETE", "DIMINISHING_RETURN")
    assert len(result["coverage"]["sectors_checked"]) > 0

    # 3. Records returned
    records = result["records"]
    assert len(records) > 0

    # 4. Negative test assertion: Banned publishers must NOT be in records
    symbols = [r["symbol"] for r in records]
    company_names = [r["company_name"] for r in records]
    for banned in ("Indiatimes", "Businessworld", "TradingView"):
        assert banned not in symbols
        assert banned not in company_names

    # 5. Verified stocks must be present
    assert any(s in symbols for s in ("TATAPOWER", "RELIANCE", "INFY"))

    # 6. Report follows Section 50 format
    md = result["report_markdown"]
    assert "# 📊 MARKET OUTLOOK: NSE" in md
    assert "Market Regime & Macro Context" in md
    assert "Top Monitored Candidates" in md
    assert "Why It Appears (Supporting Evidence)" in md
    assert "Counter-Evidence & Key Risk Factors" in md
    assert "Disclaimer" in md
