"""
Entity Resolution Agent.

Determines whether search results and extracted mentions refer to the same
real-world entity across Companies, Stocks, People, Jobs, and Events.
Uses deterministic identifiers, ticker mappings, domain pinning, and strict alias resolution.
Prevents false-positive entity merging based on shallow name similarity.
"""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from datahunt.models.shared_intel import EntityIdentity, EntityType
from datahunt.agents.market_validator import AUTHORITATIVE_NSE_SYMBOLS, KNOWN_NSE_MAPPINGS, BANNED_ENTITIES
from datahunt.logger import logger


# Common corporate suffixes for canonicalization
_LEGAL_SUFFIXES = [
    r"\bltd\b", r"\blimited\b", r"\binc\b", r"\bincorporated\b",
    r"\bcorp\b", r"\bcorporation\b", r"\bllc\b", r"\bpvt\b",
    r"\bprivate\b", r"\bco\b", r"\bcompany\b", r"\bplc\b", r"\bsa\b",
]
_SUFFIX_RE = re.compile(r"|".join(_LEGAL_SUFFIXES), re.IGNORECASE)


def _strip_corporate_suffix(name: str) -> str:
    cleaned = _SUFFIX_RE.sub("", name)
    cleaned = re.sub(r"[\.,\-]", " ", cleaned)
    return " ".join(cleaned.split()).strip()


class EntityResolutionAgent:
    """
    Deterministically resolves and clusters real-world entity identities.
    """
    def __init__(self, gemini_client: Optional[Any] = None):
        self.client = gemini_client
        self._registry: Dict[str, EntityIdentity] = {}  # canonical_key -> EntityIdentity

    def resolve_stock(self, symbol_or_name: str, exchange: str = "NSE") -> Optional[EntityIdentity]:
        """
        Resolves an equity name or ticker to its canonical exchange identity.
        Rejects non-equities, news platforms, and unlisted symbols.
        """
        if not symbol_or_name:
            return None

        raw = symbol_or_name.strip()
        upper_sym = raw.upper().split(".")[0].strip()
        lower_name = raw.lower().strip()

        # Check banned publishers
        if any(banned in lower_name for banned in BANNED_ENTITIES):
            return None

        canonical_sym = None
        canonical_name = None

        if upper_sym in AUTHORITATIVE_NSE_SYMBOLS:
            canonical_sym = upper_sym
            canonical_name = upper_sym
        elif lower_name in KNOWN_NSE_MAPPINGS:
            canonical_sym = KNOWN_NSE_MAPPINGS[lower_name]
            canonical_name = canonical_sym
        else:
            # Check for substring match in company name mappings (unidirectional, len >= 4)
            for k_name, k_sym in KNOWN_NSE_MAPPINGS.items():
                if len(k_name) >= 4 and k_name in lower_name:
                    canonical_sym = k_sym
                    canonical_name = k_sym
                    break

        if not canonical_sym:
            return None

        # Build stable key: e.g. "stock::nse::infy"
        key = f"stock::{exchange.lower()}::{canonical_sym.lower()}"
        if key in self._registry:
            existing = self._registry[key]
            if raw not in existing.aliases:
                existing.aliases.append(raw)
            return existing

        identity = EntityIdentity(
            entity_type=EntityType.STOCK,
            canonical_name=canonical_sym,
            aliases=[raw, f"{canonical_sym}.{exchange.upper()}", f"{canonical_sym}.NS"],
            identifiers={
                "exchange": exchange.upper(),
                "symbol": canonical_sym,
                "ticker": f"{exchange.upper()}:{canonical_sym}",
            },
            confidence=1.0,
        )
        self._registry[key] = identity
        return identity

    def resolve_company(
        self,
        company_name: str,
        domain: Optional[str] = None,
        identifiers: Optional[Dict[str, str]] = None,
    ) -> EntityIdentity:
        """
        Resolves a company by official domain, stock symbol, or normalized legal name.
        """
        raw_name = company_name.strip()
        norm_name = _strip_corporate_suffix(raw_name).lower()
        clean_domain = None

        if domain:
            d = domain.lower().strip()
            if "://" in d:
                try:
                    d = urlparse(d).netloc
                except Exception:
                    pass
            if d.startswith("www."):
                d = d[4:]
            clean_domain = d

        # Primary deterministic key: official domain
        if clean_domain:
            key = f"company::domain::{clean_domain}"
        elif identifiers and "symbol" in identifiers:
            key = f"company::symbol::{identifiers['symbol'].lower()}"
        else:
            key = f"company::name::{norm_name}"

        if key in self._registry:
            existing = self._registry[key]
            if raw_name not in existing.aliases:
                existing.aliases.append(raw_name)
            if clean_domain and not existing.official_domain:
                existing.official_domain = clean_domain
            return existing

        aliases = [raw_name]
        if norm_name and norm_name != raw_name.lower():
            aliases.append(norm_name.title())

        identity = EntityIdentity(
            entity_type=EntityType.COMPANY,
            canonical_name=raw_name,
            aliases=aliases,
            official_domain=clean_domain,
            identifiers=identifiers or {},
            confidence=0.95 if clean_domain else 0.85,
        )
        self._registry[key] = identity
        return identity

    def resolve_job(
        self,
        company: str,
        title: str,
        location: str,
        job_id: Optional[str] = None,
        apply_url: Optional[str] = None,
    ) -> EntityIdentity:
        """
        Resolves a job opportunity using ATS ID, application URL, or canonical fingerprint.
        """
        comp_norm = _strip_corporate_suffix(company).lower()
        title_norm = title.lower().strip()
        loc_norm = location.lower().strip()

        if job_id:
            key = f"job::id::{comp_norm}::{job_id.lower()}"
        elif apply_url:
            # Strip query params
            clean_url = apply_url.split("?")[0].rstrip("/")
            key = f"job::url::{clean_url.lower()}"
        else:
            key = f"job::fp::{comp_norm}::{title_norm}::{loc_norm}"

        if key in self._registry:
            return self._registry[key]

        identity = EntityIdentity(
            entity_type=EntityType.JOB,
            canonical_name=f"{title.strip()} at {company.strip()}",
            aliases=[f"{title.strip()} - {company.strip()}"],
            identifiers={
                "company": company.strip(),
                "title": title.strip(),
                "location": location.strip(),
                "job_id": job_id or "",
                "apply_url": apply_url or "",
            },
            confidence=0.95 if job_id or apply_url else 0.80,
        )
        self._registry[key] = identity
        return identity

    def resolve_person(
        self,
        name: str,
        company: str,
        role: Optional[str] = None,
        profile_url: Optional[str] = None,
    ) -> EntityIdentity:
        """
        Resolves a professional person entity. Never merges individuals with same name at different companies.
        """
        p_name = name.strip()
        comp_norm = _strip_corporate_suffix(company).lower()

        if profile_url:
            clean_prof = profile_url.split("?")[0].rstrip("/").lower()
            key = f"person::url::{clean_prof}"
        else:
            key = f"person::name::{p_name.lower()}::{comp_norm}"

        if key in self._registry:
            existing = self._registry[key]
            if role and role not in existing.aliases:
                existing.aliases.append(role)
            return existing

        identity = EntityIdentity(
            entity_type=EntityType.PERSON,
            canonical_name=p_name,
            aliases=[f"{p_name} ({company.strip()})"],
            identifiers={
                "company": company.strip(),
                "role": role or "",
                "profile_url": profile_url or "",
            },
            confidence=0.90 if profile_url else 0.75,
        )
        self._registry[key] = identity
        return identity
