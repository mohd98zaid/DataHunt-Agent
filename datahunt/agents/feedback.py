"""
Feedback & Preference Learning Agent.

Collects and models explicit user interaction feedback (accepted, rejected, saved, opened, ignored).
Learns soft preference weights (±0.05 ranking modifiers) without ever altering hard query constraints.
Core invariant: Current user intent and explicit query bounds always override historical preference history.
"""
from typing import Any, Dict, List, Optional
from datahunt.db.user_repositories import UserPreferencesRepository
from datahunt.logger import logger


class FeedbackAgent:
    """
    Manages explicit user feedback and computes non-overriding preference weight adjustments.
    """
    def __init__(self, repo: Optional[UserPreferencesRepository] = None):
        self.repo = repo or UserPreferencesRepository()

    def record_feedback(
        self,
        user_id: str,
        item_id: str,
        action: str,  # "accepted", "rejected", "saved", "opened", "ignored"
        item_type: str,
        attributes: Dict[str, Any],
    ) -> None:
        """
        Records an interaction signal and updates the user's positive or negative feature weights.
        """
        prefs = self.repo.get_preferences(user_id)
        pos = prefs.setdefault("positive_signals", {})
        neg = prefs.setdefault("negative_signals", {})

        company = str(attributes.get("company") or "").lower().strip()
        skills = [str(s).lower().strip() for s in attributes.get("skills", [])]
        industry = str(attributes.get("industry") or "").lower().strip()

        if action in ("accepted", "saved", "opened"):
            delta = 1.0 if action == "accepted" else (0.8 if action == "saved" else 0.3)
            if company:
                pos[f"comp::{company}"] = pos.get(f"comp::{company}", 0.0) + delta
            if industry:
                pos[f"ind::{industry}"] = pos.get(f"ind::{industry}", 0.0) + delta
            for sk in skills:
                pos[f"skill::{sk}"] = pos.get(f"skill::{sk}", 0.0) + (delta * 0.5)

        elif action in ("rejected", "ignored"):
            delta = 1.5 if action == "rejected" else 0.5
            if company:
                neg[f"comp::{company}"] = neg.get(f"comp::{company}", 0.0) + delta
            if industry:
                neg[f"ind::{industry}"] = neg.get(f"ind::{industry}", 0.0) + delta

        self.repo.update_preferences(user_id, prefs)
        logger.info(f"FeedbackAgent: Logged '{action}' action for item '{item_id}' (User: {user_id})")

    def get_soft_preference_boost(
        self,
        user_id: str,
        item_attributes: Dict[str, Any],
    ) -> float:
        """
        Computes a bounded ranking modifier (-0.05 to +0.05).
        NEVER disqualifies or qualifies items on its own.
        """
        prefs = self.repo.get_preferences(user_id)
        pos = prefs.get("positive_signals", {})
        neg = prefs.get("negative_signals", {})

        company = str(item_attributes.get("company") or "").lower().strip()
        industry = str(item_attributes.get("industry") or "").lower().strip()
        skills = [str(s).lower().strip() for s in item_attributes.get("skills", [])]

        raw_score = 0.0
        if company:
            raw_score += pos.get(f"comp::{company}", 0.0) * 0.02
            raw_score -= neg.get(f"comp::{company}", 0.0) * 0.03
        if industry:
            raw_score += pos.get(f"ind::{industry}", 0.0) * 0.01
            raw_score -= neg.get(f"ind::{industry}", 0.0) * 0.02
        for sk in skills:
            raw_score += pos.get(f"skill::{sk}", 0.0) * 0.01

        # Strictly bounded between -0.05 and +0.05
        return max(-0.05, min(0.05, round(raw_score, 3)))
