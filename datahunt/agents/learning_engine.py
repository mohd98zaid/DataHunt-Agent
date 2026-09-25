"""
LearningEngine — Steps 62-64 of the job search pipeline.

Analyzes explicit user interactions (save, ignore, apply) and career outcomes
to dynamically learn preference weights, improving future candidate rankings.
"""
from typing import Any, Dict, List, Optional
from datahunt.db.user_repositories import UserPreferencesRepository
from datahunt.agents.normalizer import NormalizedJob
from datahunt.logger import logger


class LearningEngine:
    """
    Steps 62-64: Continuous preference learning from user interaction signals.
    """

    def __init__(self, repo: Optional[UserPreferencesRepository] = None):
        self.repo = repo or UserPreferencesRepository()

    def record_save(self, job_fields: Dict[str, Any], user_id: str = "default") -> None:
        """User saved a job -> Positive reinforcement for title, company, skills, and location."""
        prefs = self.repo.get_preferences(user_id)
        pos = prefs.get("positive_signals", {})

        title = str(job_fields.get("title") or "").lower().strip()
        comp = str(job_fields.get("company") or "").lower().strip()
        loc = str(job_fields.get("location") or "").lower().strip()

        if title:
            pos[f"title::{title}"] = pos.get(f"title::{title}", 0) + 1.0
        if comp:
            pos[f"comp::{comp}"] = pos.get(f"comp::{comp}", 0) + 0.5
        if loc:
            pos[f"loc::{loc}"] = pos.get(f"loc::{loc}", 0) + 0.5

        # Record skills
        for s in job_fields.get("skills", []):
            s_low = str(s).lower().strip()
            pos[f"skill::{s_low}"] = pos.get(f"skill::{s_low}", 0) + 0.8

        prefs["positive_signals"] = pos
        self.repo.update_preferences(user_id, prefs)
        logger.info(f"LearningEngine: Registered SAVE signal for '{title}' at '{comp}'")

    def record_ignore(self, job_fields: Dict[str, Any], reason: str = "other", user_id: str = "default") -> None:
        """User ignored/rejected a job -> Negative reinforcement based on reason."""
        prefs = self.repo.get_preferences(user_id)
        neg = prefs.get("negative_signals", {})

        title = str(job_fields.get("title") or "").lower().strip()
        comp = str(job_fields.get("company") or "").lower().strip()
        loc = str(job_fields.get("location") or "").lower().strip()

        if reason == "wrong_location" and loc:
            neg[f"loc::{loc}"] = neg.get(f"loc::{loc}", 0) + 2.0
        elif reason == "disliked_company" and comp:
            neg[f"comp::{comp}"] = neg.get(f"comp::{comp}", 0) + 3.0
            # Also add to ignored companies list
            ignored_comps = prefs.get("ignored_companies", [])
            if comp not in ignored_comps:
                ignored_comps.append(comp)
            prefs["ignored_companies"] = ignored_comps
        elif reason == "wrong_role" and title:
            neg[f"title::{title}"] = neg.get(f"title::{title}", 0) + 2.0
        else:
            if comp:
                neg[f"comp::{comp}"] = neg.get(f"comp::{comp}", 0) + 0.5

        prefs["negative_signals"] = neg
        self.repo.update_preferences(user_id, prefs)
        logger.info(f"LearningEngine: Registered IGNORE ({reason}) signal for '{title}' at '{comp}'")

    def record_application_outcome(self, job_fields: Dict[str, Any], outcome: str, user_id: str = "default") -> None:
        """Positive reinforcement multiplier on interview/offer milestones."""
        if outcome in ("interview", "offer"):
            multiplier = 3.0 if outcome == "offer" else 1.5
            prefs = self.repo.get_preferences(user_id)
            pos = prefs.get("positive_signals", {})
            title = str(job_fields.get("title") or "").lower().strip()
            if title:
                pos[f"title::{title}"] = pos.get(f"title::{title}", 0) + multiplier
            prefs["positive_signals"] = pos
            self.repo.update_preferences(user_id, prefs)

    def calculate_preference_boost(self, job: NormalizedJob, user_id: str = "default") -> float:
        """
        Calculate an adjustment multiplier (-0.25 to +0.25) to tune relevance score.
        """
        prefs = self.repo.get_preferences(user_id)
        pos = prefs.get("positive_signals", {})
        neg = prefs.get("negative_signals", {})

        title_low = job.title.lower()
        comp_low = job.company.lower()
        loc_low = job.location.lower()

        boost = 0.0

        # Positive affinity boosts
        for k, weight in pos.items():
            if k.startswith("title::") and k[7:] in title_low:
                boost += min(weight * 0.03, 0.10)
            elif k.startswith("comp::") and k[6:] in comp_low:
                boost += min(weight * 0.04, 0.10)
            elif k.startswith("loc::") and k[5:] in loc_low:
                boost += min(weight * 0.02, 0.05)
            elif k.startswith("skill::") and any(k[7:] in s.lower() for s in job.skills):
                boost += min(weight * 0.02, 0.06)

        # Negative affinity penalties
        for k, weight in neg.items():
            if k.startswith("title::") and k[7:] in title_low:
                boost -= min(weight * 0.05, 0.15)
            elif k.startswith("comp::") and k[6:] in comp_low:
                boost -= min(weight * 0.08, 0.25)
            elif k.startswith("loc::") and k[5:] in loc_low:
                boost -= min(weight * 0.05, 0.15)

        return round(max(min(boost, 0.25), -0.25), 2)
