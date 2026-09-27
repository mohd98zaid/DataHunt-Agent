"""
Freshness Agent.

Evaluates evidence timestamps against domain-specific freshness horizons.
Never silently discards aging or stale data; assigns explicit ratings:
FRESH, AGING, STALE, EXPIRED, UNKNOWN.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from datahunt.models.shared_intel import FreshnessRating, FreshnessAssessment
from datahunt.logger import logger


# Domain-specific maximum age thresholds in seconds
# Format: (fresh_threshold_sec, aging_threshold_sec, stale_threshold_sec)
_THRESHOLDS = {
    # Market price: fresh < 1800s (30m), aging < 86400s (1d), stale < 172800s (2d)
    "market_price": (1800, 86400, 172800),
    # Market news: fresh < 86400s (1d), aging < 259200s (3d), stale < 604800s (7d)
    "market_news": (86400, 259200, 604800),
    # Job posting: fresh < 1209600s (14d), aging < 2592000s (30d), stale < 5184000s (60d)
    "job_posting": (1209600, 2592000, 5184000),
    # Company financials / employees: fresh < 15552000s (6mo), aging < 31536000s (1yr), stale < 63072000s (2yr)
    "company_profile": (15552000, 31536000, 63072000),
    # Annual report: fresh < 31536000s (1yr), aging < 63072000s (2yr), stale < 94608000s (3yr)
    "annual_report": (31536000, 63072000, 94608000),
    # General default: fresh < 7 days, aging < 30 days, stale < 90 days
    "default": (604800, 2592000, 7776000),
}


class FreshnessAgent:
    """
    Evaluates published/retrieved dates and assigns explicit freshness ratings.
    """
    def evaluate_freshness(
        self,
        item_id: str,
        field: str,
        published_at: Optional[str],
        domain: str = "default",
        custom_thresholds: Optional[tuple[int, int, int]] = None,
    ) -> FreshnessAssessment:
        """
        Determines freshness rating based on time delta between publication and now.
        """
        if not published_at:
            return FreshnessAssessment(
                item_id=item_id,
                field=field,
                domain=domain,
                published_at=None,
                age_seconds=None,
                rating=FreshnessRating.UNKNOWN,
                reason="No publication timestamp available in source evidence",
            )

        now = datetime.now(timezone.utc)
        parsed_dt = None

        # Clean string timestamp
        ts_clean = published_at.strip().replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%Y/%m/%d",
        ):
            try:
                parsed_dt = datetime.strptime(ts_clean, fmt)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                break
            except Exception:
                continue

        if not parsed_dt:
            return FreshnessAssessment(
                item_id=item_id,
                field=field,
                domain=domain,
                published_at=published_at,
                age_seconds=None,
                rating=FreshnessRating.UNKNOWN,
                reason=f"Unable to parse timestamp format: '{published_at}'",
            )

        age_seconds = max(0.0, (now - parsed_dt).total_seconds())

        # Select threshold
        fresh_sec, aging_sec, stale_sec = custom_thresholds or _THRESHOLDS.get(domain, _THRESHOLDS["default"])

        if age_seconds <= fresh_sec:
            rating = FreshnessRating.FRESH
            reason = f"Published within fresh window ({int(age_seconds / 3600)}h ago)"
        elif age_seconds <= aging_sec:
            rating = FreshnessRating.AGING
            reason = f"Published within aging window ({int(age_seconds / 86400)}d ago)"
        elif age_seconds <= stale_sec:
            rating = FreshnessRating.STALE
            reason = f"Exceeds aging window, marked stale ({int(age_seconds / 86400)}d ago)"
        else:
            rating = FreshnessRating.EXPIRED
            reason = f"Exceeds maximum allowable age window ({int(age_seconds / 86400)}d ago)"

        return FreshnessAssessment(
            item_id=item_id,
            field=field,
            domain=domain,
            published_at=published_at,
            age_seconds=age_seconds,
            rating=rating,
            reason=reason,
        )
