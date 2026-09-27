"""
Market Intent Parser.
Extracts structured market parameters from user queries without hallucinating constraints.
"""
from typing import Optional
import re

from datahunt.models.market import (
    MarketIntentSpec, MarketAssetType, MarketTimeHorizon
)
from datahunt.logger import logger


class MarketIntentParser:
    """
    Deterministically parses user queries into a MarketIntentSpec.
    """

    def parse(self, query: str) -> MarketIntentSpec:
        clean_q = query.strip()
        lower = clean_q.lower()

        # 1. Market & Exchange Detection
        market = "NSE"
        country = "India"
        if "bse" in lower or "bombay stock" in lower:
            market = "BSE"
        elif "nasdaq" in lower or "nyse" in lower or "us stock" in lower or "wall street" in lower:
            market = "US"
            country = "United States"
        elif "global" in lower or "crypto" in lower:
            market = "GLOBAL"
            country = "Global"
        else:
            # Default to NSE if Indian indicators (NSE, Nifty, Sensex, etc.) or generic stock query
            market = "NSE"
            country = "India"

        # 2. Asset Type Detection
        asset_type = MarketAssetType.EQUITY
        if any(w in lower for w in ("index", "indices", "nifty 50", "bank nifty", "sensex")):
            if not any(w in lower for w in ("stock", "stocks", "equities", "shares", "companies")):
                asset_type = MarketAssetType.INDEX
        elif any(w in lower for w in ("commodity", "crude", "gold", "silver")):
            asset_type = MarketAssetType.COMMODITY
        elif any(w in lower for w in ("crypto", "bitcoin", "ethereum")):
            asset_type = MarketAssetType.CRYPTO

        # 3. Time Horizon Detection
        time_horizon = MarketTimeHorizon.ONE_WEEK
        if any(w in lower for w in ("today", "intraday", "day trading", "tomorrow")):
            time_horizon = MarketTimeHorizon.ONE_DAY
        elif any(w in lower for w in ("this week", "next week", "weekly", "1 week", "one week", "few days")):
            time_horizon = MarketTimeHorizon.ONE_WEEK
        elif any(w in lower for w in ("this month", "next month", "monthly", "1 month", "one month")):
            time_horizon = MarketTimeHorizon.ONE_MONTH
        elif any(w in lower for w in ("long term", "1 year", "investing", "multibagger", "future", "long-term")):
            time_horizon = MarketTimeHorizon.LONG_TERM

        # 4. Requested Count Detection
        requested_count = 10
        count_match = re.search(r"\b(top|best|find|give|pick)\s+(\d{1,2})\b", lower)
        if count_match:
            try:
                requested_count = int(count_match.group(2))
                requested_count = max(1, min(50, requested_count))
            except (ValueError, TypeError):
                requested_count = 10
        else:
            digit_match = re.search(r"\b(\d{1,2})\s+(stocks?|equit|shares?)\b", lower)
            if digit_match:
                try:
                    requested_count = int(digit_match.group(1))
                    requested_count = max(1, min(50, requested_count))
                except (ValueError, TypeError):
                    requested_count = 10

        # 5. Objective Formulation
        objective = f"identify {market} stocks with evidence of potentially favorable conditions for horizon {time_horizon.value}"

        # 6. Flag Dependencies
        # For short-term (1_week / 1_day), technical, volume, news, and market context are essential
        need_technical = True
        need_news = True
        need_market_context = True
        need_risk_analysis = True
        need_fundamental = True if time_horizon in (MarketTimeHorizon.ONE_MONTH, MarketTimeHorizon.LONG_TERM) else False

        spec = MarketIntentSpec(
            raw_query=clean_q,
            market=market,
            asset_type=asset_type,
            country=country,
            time_horizon=time_horizon,
            requested_count=requested_count,
            objective=objective,
            need_news=need_news,
            need_technical=need_technical,
            need_fundamental=need_fundamental,
            need_market_context=need_market_context,
            need_risk_analysis=need_risk_analysis,
        )
        logger.info(f"MarketIntent parsed: market={market}, horizon={time_horizon.value}, count={requested_count}")
        return spec
