"""
Monitoring Agent.

Enables scheduled and on-demand tracking of saved search tasks, watchlist equities,
and company hiring activity.
Detects state transitions: NEW, UPDATED, REMOVED, PRICE_CHANGED, NEWS_EVENT, STATUS_CHANGED.
Never continuously scrapes in runaway loops; executes strictly on user trigger or configured interval.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from datahunt.models.shared_intel import MonitoringEvent, MonitoringChangeType
from datahunt.tools.fetch import FetchTool
from datahunt.logger import logger


class MonitoringAgent:
    """
    Generalized monitoring service detecting changes across jobs, equities, and companies.
    """
    def __init__(self, fetch_tool: Optional[FetchTool] = None):
        self.fetch_tool = fetch_tool or FetchTool()

    def compare_snapshots(
        self,
        target_id: str,
        target_type: str,
        previous_snapshot: Dict[str, Any],
        current_snapshot: Dict[str, Any],
    ) -> List[MonitoringEvent]:
        """
        Detects discrete diffs between two point-in-time snapshots of an entity or task.
        """
        events: List[MonitoringEvent] = []

        if target_type == "job":
            p_status = previous_snapshot.get("status")
            c_status = current_snapshot.get("status")
            if p_status and c_status and p_status != c_status:
                events.append(
                    MonitoringEvent(
                        target_id=target_id,
                        change_type=MonitoringChangeType.STATUS_CHANGED,
                        previous_state=p_status,
                        current_state=c_status,
                        details=f"Job posting status changed from '{p_status}' to '{c_status}'",
                    )
                )

        elif target_type == "stock":
            p_price = previous_snapshot.get("price") or previous_snapshot.get("current_price")
            c_price = current_snapshot.get("price") or current_snapshot.get("current_price")
            if p_price is not None and c_price is not None:
                try:
                    p_val = float(p_price)
                    c_val = float(c_price)
                    if abs(p_val - c_val) > 0.001:
                        pct_change = ((c_val - p_val) / p_val) * 100.0
                        events.append(
                            MonitoringEvent(
                                target_id=target_id,
                                change_type=MonitoringChangeType.PRICE_CHANGED,
                                previous_state=p_val,
                                current_state=c_val,
                                details=f"Price moved from ₹{p_val:.2f} to ₹{c_val:.2f} ({pct_change:+.2f}%)",
                            )
                        )
                except (ValueError, TypeError):
                    pass

        elif target_type == "company":
            p_news = set(previous_snapshot.get("news", []))
            c_news = set(current_snapshot.get("news", []))
            new_headlines = c_news - p_news
            for h in new_headlines:
                events.append(
                    MonitoringEvent(
                        target_id=target_id,
                        change_type=MonitoringChangeType.NEWS_EVENT,
                        previous_state=None,
                        current_state=h,
                        details=f"New corporate news detected: {h}",
                    )
                )

        logger.info(f"MonitoringAgent: Analyzed target '{target_id}' ({target_type}), detected {len(events)} events")
        return events
