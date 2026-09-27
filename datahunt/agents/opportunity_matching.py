"""
Opportunity Matching Agent.

Matches user profiles and search criteria against discovered opportunities.
Strictly enforces hard constraints (mandatory skills, location OR boundaries, max experience)
so that non-qualifying opportunities can never be rescued by soft relevance scores.
"""
from typing import Any, Dict, List, Optional
from datahunt.models.shared_intel import OpportunityMatch
from datahunt.agent.policies import match_location, match_experience, MatchStatus
from datahunt.logger import logger


class OpportunityMatchingAgent:
    """
    Evaluates candidate job/career opportunities against candidate preferences.
    """
    def match_opportunity(
        self,
        candidate_id: str,
        opportunity_data: Dict[str, Any],
        user_preferences: Dict[str, Any],
    ) -> OpportunityMatch:
        """
        Determines overall qualification, score breakdown, and requirement gaps.
        """
        target_locations = user_preferences.get("locations", [])
        if not target_locations and user_preferences.get("location"):
            target_locations = [user_preferences["location"]]
        loc_op = user_preferences.get("location_operator", "OR")
        remote_allowed = user_preferences.get("remote_allowed", True)

        req_skills = [str(s).lower().strip() for s in user_preferences.get("explicit_skills", []) if str(s).strip()]
        inf_skills = [str(s).lower().strip() for s in user_preferences.get("inferred_skills", []) if str(s).strip()]
        max_exp = user_preferences.get("explicit_experience_max")
        min_exp = user_preferences.get("explicit_experience_min")
        salary_min = user_preferences.get("salary_min")

        opp_loc = str(opportunity_data.get("location") or "")
        opp_remote = opportunity_data.get("is_remote", False) or "remote" in opp_loc.lower()
        opp_skills = [str(s).lower().strip() for s in opportunity_data.get("skills", [])]
        opp_desc = str(opportunity_data.get("description") or "").lower()
        opp_title = str(opportunity_data.get("title") or "").lower()

        matching_factors: List[str] = []
        missing_requirements: List[str] = []
        risk_factors: List[str] = []
        is_qualified = True
        base_score = 0.50

        # 1. Location Hard Gate
        if target_locations:
            loc_matched = False
            for target_loc in target_locations:
                l_stat, _ = match_location(target_loc, opp_loc, remote_allowed)
                if l_stat == MatchStatus.MATCH:
                    loc_matched = True
                    break
            if loc_matched:
                matching_factors.append(f"Location matched: {opp_loc}")
                base_score += 0.20
            else:
                is_qualified = False
                missing_requirements.append(f"Location mismatch: Job is in '{opp_loc}', user requires {target_locations}")

        # 2. Mandatory Explicit Skills Hard Gate
        for skill in req_skills:
            if skill in opp_skills or skill in opp_desc or skill in opp_title:
                matching_factors.append(f"Explicit skill matched: {skill}")
                base_score += 0.10
            else:
                is_qualified = False
                missing_requirements.append(f"Mandatory explicit skill missing: '{skill}'")

        # 3. Soft Inferred Skills
        for skill in inf_skills:
            if skill in opp_skills or skill in opp_desc or skill in opp_title:
                matching_factors.append(f"Inferred skill matched: {skill}")
                base_score += 0.05
            else:
                risk_factors.append(f"Secondary preferred skill not listed: '{skill}'")

        # 4. Experience Gate
        opp_exp = opportunity_data.get("experience_years") or opportunity_data.get("experience_max")
        if max_exp is not None and opp_exp is not None:
            try:
                opp_exp_val = float(opp_exp)
                if opp_exp_val > float(max_exp) + 1.0:
                    is_qualified = False
                    missing_requirements.append(f"Required experience ({opp_exp_val}y) exceeds target max ({max_exp}y)")
                else:
                    matching_factors.append("Experience requirement within target boundary")
            except (ValueError, TypeError):
                pass

        final_score = max(0.0, min(1.0, round(base_score, 2)))
        if not is_qualified:
            final_score = min(final_score, 0.40)  # Capped for non-qualified

        explanation = (
            f"Qualified with {len(matching_factors)} matches."
            if is_qualified
            else f"Disqualified: {'; '.join(missing_requirements)}"
        )

        return OpportunityMatch(
            candidate_id=candidate_id,
            match_score=final_score,
            is_qualified=is_qualified,
            matching_factors=matching_factors,
            missing_requirements=missing_requirements,
            risk_factors=risk_factors,
            explanation=explanation,
        )
