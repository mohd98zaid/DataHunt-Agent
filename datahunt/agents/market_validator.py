"""
Market Candidate & Symbol Validator.

Ensures only genuine listed equities/stocks enter the market pipeline.
Strictly rejects websites, publishers, general articles, and non-actionable entities.
Deduplicates multiple mentions into canonical candidates.
"""
from typing import Dict, List, Optional, Set, Tuple
import re

from datahunt.models.market import MarketCandidate, MarketEvidence, CandidateStage, MarketEntityType
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
    "et markets",
    "et now",
    "business standard",
    "business-standard",
    "reuters",
    "bloomberg",
    "cnbc-tv18",
    "cnbc tv18",
    "cnbc",
    "ndtv profit",
    "ndtv",
    "zeebiz",
    "zee business",
    "financial express",
    "financialexpress",
    "screener",
    "trendlyne",
    "tickertape",
    "zerodha",
    "groww",
    "angelone",
    "angel one",
    "upstox",
    "5paisa",
    "goodreturns",
    "equitypandit",
    "investing.com",
    "stockezee",
    "hmatrading",
    "motilal oswal",
    "hdfc securities",
    "icici direct",
    "sharekhan",
    "kotak securities",
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
    "market pulse",
    "dalal street",
}

# Authoritative Listed NSE Securities Universe (Nifty 50, Nifty Next 50, and key liquid Midcaps)
AUTHORITATIVE_NSE_SYMBOLS: Set[str] = {
    # Nifty 50
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",

    # Nifty Next 50 / Key Large & Midcaps
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "ATGL", "BANKBARODA", "BERGEPAINT", "BHEL", "BOSCHLTD",
    "CANBK", "CHOLAFIN", "COLPAL", "CONCOR", "CUMMINSIND",
    "DABUR", "DIVISLAB", "DLF", "DMART", "GAIL",
    "GODREJCP", "HAL", "HAVELLS", "HINDZINC", "ICICIGI",
    "ICICIPRULI", "IDFCFIRSTB", "INDIGO", "IOC", "IRCTC",
    "IRFC", "JINDALSTEL", "JIOFIN", "JSWENERGY", "LICI",
    "LTIM", "LUPIN", "MARICO", "MAXHEALTH", "MOTHERSON",
    "MUTHOOTFIN", "NAUKRI", "NHPC", "NMDC", "OBEROIRLTY",
    "OFSS", "PAGEIND", "PERSISTENT", "PETRONET", "PFC",
    "PIDILITIND", "PIIND", "PNB", "POLYCAB", "PVRINOX",
    "RECLTD", "RVNL", "SAIL", "SBICARD", "SHREECEM",
    "SIEMENS", "SRF", "SUZLON", "TATACOMM", "TATAELXSI",
    "TATAPOWER", "TORNTPHARM", "TORNTPOWER", "TVSMOTOR", "UNIONBANK",
    "UNITDSPR", "VBL", "VEDL", "VOLTAS", "YESBANK",
    "ZOMATO", "ZYDUSLIFE",

    # Active Liquid Midcaps
    "ASHOKLEY", "AUROPHARMA", "BALKRISIND", "BATAINDIA", "COFORGE",
    "DEEPAKNTR", "ESCORTS", "EXIDEIND", "FEDERALBNK", "GLENMARK",
    "GMRINFRA", "GODREJPROP", "HDFCAMC", "IPCALAB", "JUBLFOOD",
    "KALYANKJIL", "KEI", "KPITTECH", "LAURUSLABS", "LICHSGFIN",
    "L&TFH", "MANAPPURAM", "MFSL", "MPHASIS", "NATIONALUM",
    "NAVINFLUOR", "PEL", "PRESTIGE", "RAMCOCEM", "SONACOMS",
    "STARHEALTH", "SUNTV", "SUPREMEIND", "SYNGENE", "TATACHEM",
    "TATAINVEST", "TRIDENT", "UPL", "ZEEL",
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
    "asian paints": "ASIANPAINT",
    "asian paint": "ASIANPAINT",
    "axis bank": "AXISBANK",
    "bajaj auto": "BAJAJ-AUTO",
    "bajaj finserv": "BAJAJFINSV",
    "bharat petroleum": "BPCL",
    "britannia": "BRITANNIA",
    "cipla": "CIPLA",
    "dr reddy": "DRREDDY",
    "dr reddys": "DRREDDY",
    "eicher motors": "EICHERMOT",
    "grasim": "GRASIM",
    "hindustan unilever": "HINDUNILVR",
    "hul": "HINDUNILVR",
    "indusind bank": "INDUSINDBK",
    "mahindra & mahindra": "M&M",
    "m&m": "M&M",
    "nestle": "NESTLEIND",
    "nestle india": "NESTLEIND",
    "tech mahindra": "TECHM",
    "ultratech cement": "ULTRACEMCO",
    "apollo hospitals": "APOLLOHOSP",
    "adani power": "ADANIPOWER",
    "adani green": "ADANIGREEN",
    "ambuja cements": "AMBUJACEM",
    "bank of baroda": "BANKBARODA",
    "dlf": "DLF",
    "avenue supermarts": "DMART",
    "dmart": "DMART",
    "gail": "GAIL",
    "godrej consumer": "GODREJCP",
    "havells": "HAVELLS",
    "interglobe aviation": "INDIGO",
    "indigo": "INDIGO",
    "indian oil": "IOC",
    "irctc": "IRCTC",
    "jindal steel": "JINDALSTEL",
    "lic": "LICI",
    "life insurance corporation": "LICI",
    "ltimindtree": "LTIM",
    "lupin": "LUPIN",
    "marico": "MARICO",
    "max healthcare": "MAXHEALTH",
    "motherson": "MOTHERSON",
    "naukri": "NAUKRI",
    "info edge": "NAUKRI",
    "nhpc": "NHPC",
    "nmdc": "NMDC",
    "oberoi realty": "OBEROIRLTY",
    "pidilite": "PIDILITIND",
    "punjab national bank": "PNB",
    "polycab": "POLYCAB",
    "rec": "RECLTD",
    "rvnl": "RVNL",
    "rail vikas nigam": "RVNL",
    "sail": "SAIL",
    "steel authority of india": "SAIL",
    "siemens": "SIEMENS",
    "tata communications": "TATACOMM",
    "tata elxsi": "TATAELXSI",
    "tvs motor": "TVSMOTOR",
    "varun beverages": "VBL",
    "voltas": "VOLTAS",
    "yes bank": "YESBANK",
    "zydus lifesciences": "ZYDUSLIFE",
}


class MarketValidator:
    """
    Validates candidates, checks symbols, rejects non-equity entities,
    and merges duplicates into canonical records.
    """

    def classify_entity_type(self, name_or_symbol: str) -> MarketEntityType:
        """Classifies entity into MarketEntityType (STOCK, INDEX, SECTOR, SOURCE, etc.)."""
        if not name_or_symbol:
            return MarketEntityType.UNKNOWN
        clean = name_or_symbol.strip().lower()
        if self.is_banned_or_source(clean):
            return MarketEntityType.SOURCE
        if clean in ("nifty", "nifty 50", "bank nifty", "sensex", "nifty it", "nifty auto", "nifty metal", "nifty pharma"):
            return MarketEntityType.INDEX
        if clean in ("it", "banking", "finance", "pharma", "metals", "energy", "automobile", "fmcg"):
            return MarketEntityType.SECTOR
        if clean in ("ipo", "fpo", "sme ipo", "mainboard ipo"):
            return MarketEntityType.IPO
        if clean in KNOWN_NSE_MAPPINGS or name_or_symbol.strip().upper() in AUTHORITATIVE_NSE_SYMBOLS:
            return MarketEntityType.STOCK
        return MarketEntityType.COMPANY

    def is_banned_or_source(self, name_or_symbol: str) -> bool:
        """Check if an entity name is a publisher, website, index, or generic term."""
        if not name_or_symbol:
            return True
        clean = name_or_symbol.strip().lower()
        if clean in BANNED_ENTITIES:
            return True
        for banned in BANNED_ENTITIES:
            if banned in clean and len(banned) > 4:
                # E.g. "Indiatimes Tech", "Businessworld Media", "Stockezee Portal"
                return True
        return False

    def normalize_symbol(self, raw_symbol: str, raw_name: str, exchange: str = "NSE") -> Tuple[Optional[str], Optional[str]]:
        """
        Normalize symbol and company name.
        Enforces authoritative exchange listing validation.
        Returns: (canonical_symbol, canonical_company_name) or (None, None) if invalid.
        """
        sym_candidate = (raw_symbol or "").strip().upper()
        name_candidate = (raw_name or "").strip()

        if not sym_candidate and not name_candidate:
            return None, None

        # Reject if either non-empty candidate matches banned entity list
        if sym_candidate and self.is_banned_or_source(sym_candidate):
            return None, None
        if name_candidate and self.is_banned_or_source(name_candidate):
            return None, None

        # Classify entity types; disqualify non-equity entities
        disallowed_types = {
            MarketEntityType.SOURCE,
            MarketEntityType.NEWS,
            MarketEntityType.INDEX,
            MarketEntityType.SECTOR,
            MarketEntityType.IPO,
            MarketEntityType.PERSON,
            MarketEntityType.PRODUCT,
        }
        if sym_candidate:
            sym_type = self.classify_entity_type(sym_candidate)
            if sym_type in disallowed_types:
                return None, None
        if name_candidate:
            name_type = self.classify_entity_type(name_candidate)
            if name_type in disallowed_types:
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

        # For NSE: enforce membership in authoritative universe or known mappings
        if exchange.upper() == "NSE":
            if clean_sym in AUTHORITATIVE_NSE_SYMBOLS:
                return clean_sym, name_candidate or clean_sym

            # Check if name contains a known company name (e.g. "Tata Power Company Ltd" contains "tata power")
            if len(lower_name) >= 4:
                for k_name, k_sym in KNOWN_NSE_MAPPINGS.items():
                    if len(k_name) >= 4 and k_name in lower_name:
                        return k_sym, name_candidate or k_sym

            # Do NOT validate arbitrary strings as stocks if not in authoritative universe!
            return None, None

        # For non-NSE exchanges: check valid ticker format and non-banned
        if re.match(r"^[A-Z0-9]{1,10}$", clean_sym) and not self.is_banned_or_source(clean_sym):
            return clean_sym, name_candidate or clean_sym

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
