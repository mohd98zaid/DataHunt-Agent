"""
Market Candidate & Symbol Validator.

Ensures only genuine listed equities/stocks enter the market pipeline.
Strictly rejects websites, publishers, general articles, and non-actionable entities.
Deduplicates multiple mentions into canonical candidates.
"""
from typing import Dict, List, Optional, Set, Tuple
import re

from datahunt.models.market import MarketCandidate, MarketEvidence, CandidateStage
from datahunt.logger import logger

# Banned publishers, websites, sources, and generic non-stock entities
BANNED_ENTITIES: Set[str] = {
    "indiatimes",
    "businessworld",
    "tradingview",
    "moneycontrol",
    "livemint",
    "mint",
    "economictimes",
    "economic times",
    "business standard",
    "business-standard",
    "reuters",
    "bloomberg",
    "cnbc-tv18",
    "cnbc tv18",
    "cnbc",
    "et now",
    "financial express",
    "financialexpress",
    "screener",
    "trendlyne",
    "tickertape",
    "zerodha",
    "groww",
    "angelone",
    "angel one",
    "ipo",
    "ipo news",
    "stock market",
    "share market",
    "stock market article",
    "nse",
    "bse",
    "sensex",
    "nifty",
    "nifty 50",
    "bank nifty",
    "g2",
    "capterra",
    "trustradius",
    "google",
    "duckduckgo",
    "wikipedia",
    "youtube",
    "yahoo",
    "yahoo finance",
    "investing.com",
    "market pulse",
    "dalal street",
}

# Known Indian Equities (Company Name -> Canonical Symbol)
KNOWN_NSE_MAPPINGS: Dict[str, str] = {
    "tata power": "TATAPOWER",
    "tata power company": "TATAPOWER",
    "tatapower": "TATAPOWER",
    "reliance": "RELIANCE",
    "reliance industries": "RELIANCE",
    "tcs": "TCS",
    "tata consultancy services": "TCS",
    "infosys": "INFY",
    "infy": "INFY",
    "hdfc bank": "HDFCBANK",
    "hdfcbank": "HDFCBANK",
    "icici bank": "ICICIBANK",
    "icicibank": "ICICIBANK",
    "state bank of india": "SBIN",
    "sbi": "SBIN",
    "sbin": "SBIN",
    "bharti airtel": "BHARTIARTL",
    "airtel": "BHARTIARTL",
    "bhartiartl": "BHARTIARTL",
    "larsen & toubro": "LT",
    "larsen and toubro": "LT",
    "l&t": "LT",
    "itc": "ITC",
    "itc limited": "ITC",
    "tata motors": "TATAMOTORS",
    "tatamotors": "TATAMOTORS",
    "maruti": "MARUTI",
    "maruti suzuki": "MARUTI",
    "sun pharma": "SUNPHARMA",
    "sun pharmaceutical": "SUNPHARMA",
    "sunpharma": "SUNPHARMA",
    "bajaj finance": "BAJFINANCE",
    "bajfinance": "BAJFINANCE",
    "hcl tech": "HCLTECH",
    "hcl technologies": "HCLTECH",
    "hcltech": "HCLTECH",
    "wipro": "WIPRO",
    "adani enterprises": "ADANIENT",
    "adanient": "ADANIENT",
    "adani ports": "ADANIPORTS",
    "jsw steel": "JSWSTEEL",
    "jswsteel": "JSWSTEEL",
    "tata steel": "TATASTEEL",
    "tatasteel": "TATASTEEL",
    "coal india": "COALINDIA",
    "coalindia": "COALINDIA",
    "ntpc": "NTPC",
    "power grid": "POWERGRID",
    "powergrid": "POWERGRID",
    "power grid corporation": "POWERGRID",
    "ongc": "ONGC",
    "oil and natural gas": "ONGC",
    "titan": "TITAN",
    "titan company": "TITAN",
    "kotak bank": "KOTAKBANK",
    "kotak mahindra bank": "KOTAKBANK",
    "kotakbank": "KOTAKBANK",
    "bharat electronics": "BEL",
    "bel": "BEL",
    "bhel": "BHEL",
    "hindustan aeronautics": "HAL",
    "hal": "HAL",
    "vedanta": "VEDL",
    "vedl": "VEDL",
    "zomato": "ZOMATO",
    "trent": "TRENT",
    "jio financial": "JIOFIN",
    "jiofin": "JIOFIN",
    "suzlon": "SUZLON",
    "suzlon energy": "SUZLON",
    "irfc": "IRFC",
    "indian railway finance": "IRFC",
}


class MarketValidator:
    """
    Validates candidates, checks symbols, rejects non-equity entities,
    and merges duplicates into canonical records.
    """

    def is_banned_or_source(self, name_or_symbol: str) -> bool:
        """Check if an entity name is a publisher, website, index, or generic term."""
        if not name_or_symbol:
            return True
        clean = name_or_symbol.strip().lower()
        if clean in BANNED_ENTITIES:
            return True
        for banned in BANNED_ENTITIES:
            if banned in clean and len(banned) > 4:
                # E.g. "Indiatimes Tech", "Businessworld Media"
                return True
        return False

    def normalize_symbol(self, raw_symbol: str, raw_name: str, exchange: str = "NSE") -> Tuple[Optional[str], Optional[str]]:
        """
        Normalize symbol and company name.
        Returns: (canonical_symbol, canonical_company_name) or (None, None) if invalid.
        """
        sym_candidate = (raw_symbol or "").strip().upper()
        name_candidate = (raw_name or "").strip()

        # Reject if either matches banned entity list
        if self.is_banned_or_source(sym_candidate) or self.is_banned_or_source(name_candidate):
            return None, None

        # Check known mappings by company name
        lower_name = name_candidate.lower()
        if lower_name in KNOWN_NSE_MAPPINGS:
            can_sym = KNOWN_NSE_MAPPINGS[lower_name]
            can_name = name_candidate if len(name_candidate) > 2 else can_sym
            return can_sym, can_name

        # Check known mappings by symbol
        lower_sym = sym_candidate.lower()
        if lower_sym in KNOWN_NSE_MAPPINGS:
            can_sym = KNOWN_NSE_MAPPINGS[lower_sym]
            can_name = name_candidate if len(name_candidate) > 2 else can_sym
            return can_sym, can_name

        # Clean symbol: strip common suffixes (.NS, .BO, -EQ, :NSE)
        clean_sym = re.sub(r"(\.NS|\.BO|-EQ|:NSE|:BSE)$", "", sym_candidate, flags=re.IGNORECASE).strip()

        # Check if clean_sym is a valid ticker format (2 to 15 uppercase alpha/numbers)
        if re.match(r"^[A-Z0-9]{2,15}$", clean_sym):
            if not self.is_banned_or_source(clean_sym):
                return clean_sym, name_candidate or clean_sym

        # If name is substantial and looks like a company
        if len(name_candidate) >= 3 and not self.is_banned_or_source(name_candidate):
            # Try to derive symbol from name if acronym-like or single word
            name_words = name_candidate.split()
            if len(name_words) == 1 and re.match(r"^[A-Za-z]{3,12}$", name_words[0]):
                return name_words[0].upper(), name_candidate
            # Check partial match in known mappings
            for k_name, k_sym in KNOWN_NSE_MAPPINGS.items():
                if k_name in lower_name or lower_name in k_name:
                    return k_sym, name_candidate

        return None, None

    def validate_and_deduplicate(
        self,
        raw_candidates: List[Dict],
        exchange: str = "NSE"
    ) -> List[MarketCandidate]:
        """
        Validates raw candidate dicts and deduplicates them by (exchange, symbol).
        Combines evidence, sources, and prices for identical stocks.
        """
        candidate_map: Dict[str, MarketCandidate] = {}

        for item in raw_candidates:
            raw_sym = item.get("symbol") or ""
            raw_name = item.get("company_name") or item.get("name") or ""

            symbol, company_name = self.normalize_symbol(raw_sym, raw_name, exchange=exchange)
            if not symbol or not company_name:
                continue

            dedupe_key = f"{exchange.upper()}:{symbol.upper()}"

            source_url = item.get("source_url") or item.get("url") or ""
            source_domain = item.get("source_domain") or item.get("source") or ""

            if dedupe_key not in candidate_map:
                cand = MarketCandidate(
                    symbol=symbol,
                    exchange=exchange,
                    company_name=company_name,
                    sector=item.get("sector"),
                    stage=CandidateStage.VALIDATED,
                    discovery_channel=item.get("channel", "general"),
                    current_price=item.get("current_price"),
                    change_1d=item.get("change_1d"),
                    change_1w=item.get("change_1w"),
                    change_1m=item.get("change_1m"),
                    volume=item.get("volume"),
                    average_volume=item.get("average_volume"),
                    volume_ratio=item.get("volume_ratio"),
                    pe_ratio=item.get("pe_ratio"),
                    revenue_growth=item.get("revenue_growth"),
                    profit_growth=item.get("profit_growth"),
                )
                if source_url:
                    cand.sources_seen.append(source_url)
                elif source_domain:
                    cand.sources_seen.append(source_domain)

                # Add initial evidence
                evidence_text = item.get("evidence") or item.get("snippet") or item.get("claim")
                if evidence_text:
                    from datahunt.tools.market_analysis import classify_source_tier, classify_freshness
                    tier, src_name = classify_source_tier(source_url or source_domain)
                    cand.evidence_items.append(MarketEvidence(
                        claim=str(evidence_text),
                        source_url=source_url,
                        source_name=src_name,
                        source_tier=tier,
                        timestamp=item.get("timestamp"),
                        freshness=classify_freshness(item.get("timestamp")),
                        sentiment=item.get("sentiment", "bullish"),
                        raw_snippet=str(evidence_text)[:300],
                    ))
                candidate_map[dedupe_key] = cand
            else:
                # Merge into existing candidate!
                existing = candidate_map[dedupe_key]
                if source_url and source_url not in existing.sources_seen:
                    existing.sources_seen.append(source_url)
                elif source_domain and source_domain not in existing.sources_seen:
                    existing.sources_seen.append(source_domain)

                # Merge price/volume if not already set
                if existing.current_price is None and item.get("current_price") is not None:
                    existing.current_price = item.get("current_price")
                if existing.change_1w is None and item.get("change_1w") is not None:
                    existing.change_1w = item.get("change_1w")
                if existing.volume is None and item.get("volume") is not None:
                    existing.volume = item.get("volume")
                if existing.volume_ratio is None and item.get("volume_ratio") is not None:
                    existing.volume_ratio = item.get("volume_ratio")
                if not existing.sector and item.get("sector"):
                    existing.sector = item.get("sector")

                # Merge evidence
                evidence_text = item.get("evidence") or item.get("snippet") or item.get("claim")
                if evidence_text:
                    from datahunt.tools.market_analysis import classify_source_tier, classify_freshness
                    tier, src_name = classify_source_tier(source_url or source_domain)
                    # Deduplicate evidence claims
                    if not any(e.claim.strip().lower() == str(evidence_text).strip().lower() for e in existing.evidence_items):
                        existing.evidence_items.append(MarketEvidence(
                            claim=str(evidence_text),
                            source_url=source_url,
                            source_name=src_name,
                            source_tier=tier,
                            timestamp=item.get("timestamp"),
                            freshness=classify_freshness(item.get("timestamp")),
                            sentiment=item.get("sentiment", "bullish"),
                            raw_snippet=str(evidence_text)[:300],
                        ))

        validated_list = list(candidate_map.values())
        logger.info(f"Validated and deduplicated {len(raw_candidates)} raw records into {len(validated_list)} unique candidates")
        return validated_list
