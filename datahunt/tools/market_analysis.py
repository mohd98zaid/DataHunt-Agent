"""
Deterministic Market Analysis & Technical Indicator Calculations.

All indicators are calculated directly in code from raw price/volume series.
No LLM hallucination of indicator values or scoring components.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import math
import re

from datahunt.models.market import (
    SourceTier, FreshnessCategory, MarketEvidence, MarketCandidate,
    MarketIntentSpec, ClaimType
)


def calculate_sma(prices: List[float], period: int = 20) -> Optional[float]:
    """Calculate Simple Moving Average."""
    if not prices or len(prices) < period or period <= 0:
        return None
    window = prices[-period:]
    return round(sum(window) / period, 2)


def calculate_ema(prices: List[float], period: int = 20) -> Optional[float]:
    """Calculate Exponential Moving Average."""
    if not prices or len(prices) < period or period <= 0:
        return None
    multiplier = 2.0 / (period + 1)
    # Seed EMA with initial SMA
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * multiplier + ema
    return round(ema, 2)


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI 14).
    Uses Wilder's smoothing method.
    """
    if not prices or len(prices) <= period or period <= 0:
        return None

    gains: List[float] = []
    losses: List[float] = []

    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    if len(gains) < period:
        return None

    # Initial average gain/loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed average
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def calculate_macd(
    prices: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9
) -> Dict[str, Optional[float]]:
    """
    Calculate MACD Line, Signal Line, and Histogram.
    """
    res: Dict[str, Optional[float]] = {
        "macd": None,
        "signal": None,
        "histogram": None
    }
    if not prices or len(prices) < (slow + signal_period):
        return res

    # Calculate fast and slow EMAs across the series
    def get_ema_series(series: List[float], n: int) -> List[float]:
        if len(series) < n:
            return []
        mult = 2.0 / (n + 1)
        curr = sum(series[:n]) / n
        emas = [curr]
        for val in series[n:]:
            curr = (val - curr) * mult + curr
            emas.append(curr)
        return emas

    fast_emas = get_ema_series(prices, fast)
    slow_emas = get_ema_series(prices, slow)

    # Align them: slow_emas starts at index `slow - 1`
    offset = slow - fast
    aligned_fast = fast_emas[offset:]
    if len(aligned_fast) != len(slow_emas):
        min_len = min(len(aligned_fast), len(slow_emas))
        aligned_fast = aligned_fast[-min_len:]
        slow_emas = slow_emas[-min_len:]

    macd_line = [f - s for f, s in zip(aligned_fast, slow_emas)]
    if len(macd_line) < signal_period:
        return res

    signal_line_emas = get_ema_series(macd_line, signal_period)
    if not signal_line_emas:
        return res

    final_macd = macd_line[-1]
    final_signal = signal_line_emas[-1]
    final_hist = final_macd - final_signal

    return {
        "macd": round(final_macd, 2),
        "signal": round(final_signal, 2),
        "histogram": round(final_hist, 2)
    }


def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[float]:
    """Calculate Average True Range (ATR 14)."""
    if not highs or not lows or not closes:
        return None
    n = min(len(highs), len(lows), len(closes))
    if n <= period:
        return None

    tr_list: List[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    atr = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period

    return round(atr, 2)


def calculate_volume_ratio(current_volume: Optional[float], avg_volume: Optional[float]) -> Optional[float]:
    """Calculate ratio of current volume to average volume (e.g. 20-day avg)."""
    if not current_volume or not avg_volume or avg_volume <= 0:
        return None
    return round(current_volume / avg_volume, 2)


def calculate_weekly_return(prices: List[float]) -> Optional[float]:
    """Calculate percentage return over the last 5 trading days."""
    if not prices or len(prices) < 2:
        return None
    start_idx = max(0, len(prices) - 5)
    start_price = prices[start_idx]
    end_price = prices[-1]
    if start_price <= 0:
        return None
    return round(((end_price - start_price) / start_price) * 100.0, 2)


def classify_freshness(timestamp_str: Optional[str]) -> FreshnessCategory:
    """
    Classify evidence freshness:
    < 24 hours: VERY_RECENT
    1–3 days: RECENT
    4–7 days: RELEVANT
    8–30 days: CONTEXT
    > 30 days: BACKGROUND
    """
    if not timestamp_str:
        return FreshnessCategory.RELEVANT

    now = datetime.now(timezone.utc)
    parsed_dt = None

    # Try ISO formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed_dt = datetime.strptime(timestamp_str.strip(), fmt)
            break
        except (ValueError, TypeError):
            continue

    if not parsed_dt:
        return FreshnessCategory.RELEVANT

    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)

    delta = abs(now - parsed_dt)
    days = delta.total_seconds() / 86400.0

    if days < 1.0:
        return FreshnessCategory.VERY_RECENT
    elif days <= 3.0:
        return FreshnessCategory.RECENT
    elif days <= 7.0:
        return FreshnessCategory.RELEVANT
    elif days <= 30.0:
        return FreshnessCategory.CONTEXT
    else:
        return FreshnessCategory.BACKGROUND


def classify_source_tier(url_or_domain: str) -> Tuple[SourceTier, str]:
    """
    Categorize source trust tier and canonical source name.
    TIER 1: Official exchanges, company IR, SEBI
    TIER 2: Major financial press, market data providers
    TIER 3: Established financial portals & brokers
    TIER 4: Blogs, unknown / forecast sites
    """
    lower = (url_or_domain or "").lower()

    # Tier 1
    if any(d in lower for d in ("nseindia.com", "bseindia.com", "sebi.gov.in", "/investor-relations", "/ir/", "investor.")):
        if "nseindia.com" in lower:
            return SourceTier.TIER_1, "NSE India Official"
        elif "bseindia.com" in lower:
            return SourceTier.TIER_1, "BSE India Official"
        elif "sebi.gov.in" in lower:
            return SourceTier.TIER_1, "SEBI Regulatory Filing"
        return SourceTier.TIER_1, "Official Company IR"

    # Tier 2
    if any(d in lower for d in ("economictimes.indiatimes.com", "moneycontrol.com", "livemint.com", "business-standard.com", "cnbctv18.com", "reuters.com", "bloomberg.com", "financialexpress.com")):
        if "moneycontrol.com" in lower:
            return SourceTier.TIER_2, "Moneycontrol"
        elif "economictimes.indiatimes.com" in lower:
            return SourceTier.TIER_2, "Economic Times"
        elif "livemint.com" in lower:
            return SourceTier.TIER_2, "Mint"
        elif "business-standard.com" in lower:
            return SourceTier.TIER_2, "Business Standard"
        elif "cnbctv18.com" in lower:
            return SourceTier.TIER_2, "CNBC-TV18"
        elif "reuters.com" in lower:
            return SourceTier.TIER_2, "Reuters"
        elif "bloomberg.com" in lower:
            return SourceTier.TIER_2, "Bloomberg"
        return SourceTier.TIER_2, "Financial Express"

    if any(d in lower for d in ("tradingview.com", "finance.yahoo.com", "screener.in", "trendlyne.com", "tickertape.in")):
        if "tradingview.com" in lower:
            return SourceTier.TIER_2, "TradingView"
        elif "finance.yahoo.com" in lower:
            return SourceTier.TIER_2, "Yahoo Finance"
        elif "screener.in" in lower:
            return SourceTier.TIER_2, "Screener.in"
        elif "trendlyne.com" in lower:
            return SourceTier.TIER_2, "Trendlyne"
        return SourceTier.TIER_2, "TickerTape"

    # Tier 3
    if any(d in lower for d in ("zerodha.com", "groww.in", "motilaloswal.com", "angelone.in", "icicidirect.com", "hdfcsec.com", "kotaksecurities.com", "investing.com")):
        return SourceTier.TIER_3, "Financial Research / Broker Portal"

    # Tier 4
    return SourceTier.TIER_4, "External Web Source"


def score_market_candidate(
    cand: MarketCandidate,
    intent: MarketIntentSpec
) -> Tuple[float, str, str, Dict[str, float]]:
    """
    Deterministic scoring and evidence confidence evaluation.
    Components:
    - Momentum: 0–20
    - Volume: 0–15
    - Technical: 0–20
    - Sector strength: 0–15
    - Catalyst / Corporate Events: 0–15
    - Fundamentals: 0–10
    - Risk adjustment: -20–0
    - Evidence quality: 0–5

    Returns:
    (signal_score [0..100], evidence_confidence [HIGH|MEDIUM|LOW], signal_direction [POSITIVE|MIXED|HIGH_RISK|INSUFFICIENT_EVIDENCE], breakdown_dict)
    """
    breakdown: Dict[str, float] = {}

    # 1. Momentum (0–20)
    w_return = cand.change_1w
    if w_return is None and cand.prices and len(cand.prices) >= 2:
        w_return = calculate_weekly_return(cand.prices)

    momentum_score = 0.0
    if w_return is not None:
        if 1.0 <= w_return <= 8.0:
            momentum_score = 16.0 + min(4.0, (w_return - 1.0) / 7.0 * 4.0)
        elif 8.0 < w_return <= 15.0:
            momentum_score = 15.0  # Strong but near short-term resistance
        elif w_return > 15.0:
            momentum_score = 10.0  # Overextended / chase risk
        elif 0.0 <= w_return < 1.0:
            momentum_score = 8.0
        elif -3.0 <= w_return < 0.0:
            momentum_score = 4.0
        else:
            momentum_score = 0.0  # Severe downtrend
    elif any("momentum" in e.claim.lower() or "gain" in e.claim.lower() for e in cand.evidence_items):
        momentum_score = 10.0  # Qualitative momentum mention
    breakdown["momentum"] = round(momentum_score, 1)

    # 2. Volume (0–15)
    vol_ratio = cand.volume_ratio
    if vol_ratio is None and cand.volume and cand.average_volume:
        vol_ratio = calculate_volume_ratio(cand.volume, cand.average_volume)

    vol_score = 0.0
    if vol_ratio is not None:
        if vol_ratio >= 2.0:
            vol_score = 15.0
        elif vol_ratio >= 1.5:
            vol_score = 12.0
        elif vol_ratio >= 1.1:
            vol_score = 8.0
        elif vol_ratio >= 0.8:
            vol_score = 5.0
        else:
            vol_score = 2.0  # Low volume
    elif any("volume" in e.claim.lower() or "breakout" in e.claim.lower() for e in cand.evidence_items):
        vol_score = 7.0
    breakdown["volume"] = round(vol_score, 1)

    # 3. Technical Setup (0–20)
    tech_score = 0.0
    rsi = cand.technicals.get("rsi")
    if rsi is None and cand.prices and len(cand.prices) > 14:
        rsi = calculate_rsi(cand.prices)

    if rsi is not None:
        if 50.0 <= rsi <= 68.0:
            tech_score += 10.0  # Bullish momentum zone
        elif 40.0 <= rsi < 50.0:
            tech_score += 6.0
        elif 68.0 < rsi <= 75.0:
            tech_score += 5.0  # Approaching overbought
        elif rsi > 75.0:
            tech_score += 2.0  # Overbought
        else:
            tech_score += 2.0  # Oversold or weak

    # Moving average support / breakout
    sma20 = cand.technicals.get("sma20")
    if sma20 is None and cand.prices and len(cand.prices) >= 20:
        sma20 = calculate_sma(cand.prices, 20)
    curr_p = cand.current_price or (cand.prices[-1] if cand.prices else None)

    if curr_p and sma20:
        if curr_p > sma20:
            tech_score += 7.0
        else:
            tech_score += 1.0
    elif any("breakout" in e.claim.lower() or "support" in e.claim.lower() for e in cand.evidence_items):
        tech_score += 6.0

    # MACD confirmation
    macd_data = cand.technicals.get("macd")
    if isinstance(macd_data, dict):
        hist = macd_data.get("histogram")
        if hist is not None and hist > 0:
            tech_score += 3.0

    tech_score = min(20.0, tech_score)
    breakdown["technical"] = round(tech_score, 1)

    # 4. Sector Strength (0–15)
    sec_score = 0.0
    if cand.sector:
        high_priority_sectors = ("it", "bank", "auto", "metal", "energy", "pharma")
        if any(s in cand.sector.lower() for s in high_priority_sectors):
            sec_score = 12.0
        else:
            sec_score = 8.0
    elif any("sector" in e.claim.lower() for e in cand.evidence_items):
        sec_score = 8.0
    else:
        sec_score = 4.0
    breakdown["sector_strength"] = round(sec_score, 1)

    # 5. Catalyst / Corporate Events (0–15)
    cat_score = 0.0
    if cand.corporate_actions or cand.news_events:
        cat_score = min(15.0, len(cand.corporate_actions) * 5.0 + len(cand.news_events) * 3.0)
    else:
        catalyst_evis = [e for e in cand.evidence_items if e.claim_type in (ClaimType.COMPANY_FACT, ClaimType.NEWS) and e.sentiment == "bullish"]
        cat_score = min(15.0, len(catalyst_evis) * 4.0)
    breakdown["catalyst"] = round(cat_score, 1)

    # 6. Fundamentals (0–10)
    fund_score = 5.0  # neutral baseline
    if cand.revenue_growth is not None:
        if cand.revenue_growth > 15.0:
            fund_score += 3.0
        elif cand.revenue_growth < 0:
            fund_score -= 2.0
    if cand.profit_growth is not None:
        if cand.profit_growth > 15.0:
            fund_score += 2.0
        elif cand.profit_growth < 0:
            fund_score -= 2.0
    if cand.pe_ratio is not None:
        if 10.0 <= cand.pe_ratio <= 40.0:
            fund_score += 1.0
    fund_score = max(0.0, min(10.0, fund_score))
    breakdown["fundamentals"] = round(fund_score, 1)

    # 7. Risk Adjustment (-20–0)
    risk_deduction = 0.0
    risk_factors = list(cand.risk_factors)
    bearish_evis = [e for e in cand.evidence_items if e.sentiment == "bearish" or e.is_counter_evidence]

    if risk_factors:
        risk_deduction += len(risk_factors) * 4.0
    if bearish_evis:
        risk_deduction += len(bearish_evis) * 3.0
    if cand.conflicts:
        risk_deduction += len(cand.conflicts) * 5.0

    risk_deduction = min(20.0, risk_deduction)
    breakdown["risk_adjustment"] = -round(risk_deduction, 1)

    # 8. Evidence Quality & Breadth (0–5)
    eq_score = 0.0
    tier_counts = {SourceTier.TIER_1: 0, SourceTier.TIER_2: 0, SourceTier.TIER_3: 0, SourceTier.TIER_4: 0}
    for e in cand.evidence_items:
        tier_counts[e.source_tier] = tier_counts.get(e.source_tier, 0) + 1

    if tier_counts[SourceTier.TIER_1] > 0:
        eq_score += 2.5
    if tier_counts[SourceTier.TIER_2] >= 2:
        eq_score += 2.0
    elif tier_counts[SourceTier.TIER_2] >= 1:
        eq_score += 1.0
    if len(cand.sources_seen) >= 3:
        eq_score += 0.5

    eq_score = min(5.0, eq_score)
    breakdown["evidence_quality"] = round(eq_score, 1)

    # Total Raw Score (0 to 100)
    raw_score = (
        momentum_score +
        vol_score +
        tech_score +
        sec_score +
        cat_score +
        fund_score -
        risk_deduction +
        eq_score
    )
    final_score = max(0.0, min(100.0, round(raw_score, 1)))

    # Evaluate Evidence Confidence (independent from score)
    # High confidence requires: at least 3 distinct sources, Tier 1 or multiple Tier 2, and both technical & news/catalyst evidence
    has_tier1 = tier_counts[SourceTier.TIER_1] > 0
    has_tier2 = tier_counts[SourceTier.TIER_2] >= 2
    distinct_sources = len(set(cand.sources_seen))
    total_evidences = len(cand.evidence_items)

    if (has_tier1 or has_tier2) and distinct_sources >= 3 and total_evidences >= 3:
        confidence = "HIGH"
    elif distinct_sources >= 2 and total_evidences >= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Evaluate Signal Direction
    if total_evidences < 2:
        signal_direction = "INSUFFICIENT_EVIDENCE"
    elif risk_deduction >= 12.0 or len(cand.conflicts) > 0:
        signal_direction = "HIGH_RISK" if risk_deduction >= 16.0 else "MIXED"
    elif final_score >= 65.0 and confidence in ("HIGH", "MEDIUM"):
        signal_direction = "POSITIVE"
    elif final_score < 40.0:
        signal_direction = "NEGATIVE"
    else:
        signal_direction = "MIXED"

    return final_score, confidence, signal_direction, breakdown
