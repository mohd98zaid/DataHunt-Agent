"""
Coverage Agent.

Measures research completeness across discovered entities against required dimensions
(technical data, fundamental data, news corroboration, counter-evidence, freshness).
Identifies explicit gaps and generates targeted follow-up queries for the Task Controller.
"""
from typing import Any, Dict, List, Optional
from datahunt.models.shared_intel import SharedCoverageState, CoverageGap, Fact, SharedEvidence, EvidenceRelationship
from datahunt.logger import logger


class CoverageAgent:
    """
    Evaluates evidence coverage across candidate entities and pinpoints missing data gaps.
    """
    def evaluate_coverage(
        self,
        task_id: str,
        domain: str,
        candidates: List[Any],
        facts_by_entity: Optional[Dict[str, List[Fact]]] = None,
        evidence_by_entity: Optional[Dict[str, List[SharedEvidence]]] = None,
        target_count: int = 10,
    ) -> SharedCoverageState:
        """
        Calculates coverage completeness metrics and enumerates explicit gaps.
        """
        f_map = facts_by_entity or {}
        e_map = evidence_by_entity or {}

        total_discovered = len(candidates)
        validated_candidates = []
        for c in candidates:
            # Check validation flag or valid identity
            is_valid = getattr(c, "is_valid", True)
            if hasattr(c, "verification_status"):
                is_valid = is_valid and str(c.verification_status).lower() not in ("rejected", "duplicate")
            if is_valid:
                validated_candidates.append(c)

        total_validated = len(validated_candidates)

        tech_count = 0
        fund_count = 0
        news_count = 0
        counter_count = 0
        fresh_count = 0

        gaps: List[CoverageGap] = []

        for c in validated_candidates:
            ent_id = getattr(c, "id", None) or getattr(c, "entity_id", None) or getattr(c, "symbol", "")
            c_name = getattr(c, "canonical_name", None) or getattr(c, "name", None) or getattr(c, "company", None) or getattr(c, "title", str(ent_id))

            c_facts = f_map.get(ent_id, [])
            c_evidence = e_map.get(ent_id, [])

            # Check technical data
            has_tech = any(
                f.field in ("price", "current_price", "rsi", "macd", "sma_20")
                for f in c_facts
            ) or hasattr(c, "current_price") and getattr(c, "current_price") is not None
            if has_tech:
                tech_count += 1
            elif domain == "market":
                gaps.append(CoverageGap(
                    entity_id=str(ent_id),
                    entity_name=str(c_name),
                    missing_category="technical_data",
                    description=f"Missing current price or technical indicators for {c_name}",
                    suggested_queries=[f"{c_name} share price CMP NSE technical analysis"],
                ))

            # Check fundamentals / corporate data
            has_fund = any(
                f.field in ("revenue", "net_profit", "market_cap", "employee_count", "headquarters")
                for f in c_facts
            ) or getattr(c, "fundamentals", None)
            if has_fund:
                fund_count += 1
            elif domain in ("market", "company"):
                gaps.append(CoverageGap(
                    entity_id=str(ent_id),
                    entity_name=str(c_name),
                    missing_category="fundamental_data",
                    description=f"Missing fundamental financial disclosures for {c_name}",
                    suggested_queries=[f"{c_name} quarterly earnings revenue profit investor relations"],
                ))

            # Check news corroboration
            has_news = len(c_evidence) >= 2 or any("news" in (e.source_url or "").lower() for e in c_evidence)
            if has_news:
                news_count += 1
            else:
                gaps.append(CoverageGap(
                    entity_id=str(ent_id),
                    entity_name=str(c_name),
                    missing_category="news_evidence",
                    description=f"Fewer than 2 corroborating source citations for {c_name}",
                    suggested_queries=[f"{c_name} latest developments announcement news"],
                ))

            # Check counter-evidence / risk assessment
            has_counter = any(
                e.relationship == EvidenceRelationship.CONTRADICTS or "risk" in e.claim.lower()
                for e in c_evidence
            ) or getattr(c, "bearish_risks", None)
            if has_counter:
                counter_count += 1
            else:
                gaps.append(CoverageGap(
                    entity_id=str(ent_id),
                    entity_name=str(c_name),
                    missing_category="counter_evidence",
                    description=f"Missing counter-evidence or risk assessment for {c_name}",
                    suggested_queries=[f"{c_name} risks challenges concerns downgrade"],
                ))

            # Check freshness
            if c_evidence:
                fresh_count += 1

        # Evaluate sufficiency
        is_sufficient = (
            total_validated >= target_count
            and (fund_count >= int(0.7 * target_count) if domain in ("market", "company") else True)
            and (counter_count >= int(0.5 * target_count))
        )

        denom = max(1, target_count * 4)
        numerator = min(denom, total_validated + fund_count + news_count + counter_count)
        completion_ratio = round(numerator / denom, 2)

        state = SharedCoverageState(
            task_id=task_id,
            domain=domain,
            candidates_discovered=total_discovered,
            candidates_validated=total_validated,
            technical_data_count=tech_count,
            fundamental_data_count=fund_count,
            news_count=news_count,
            counter_evidence_count=counter_count,
            fresh_evidence_count=fresh_count,
            missing_gaps=gaps,
            is_sufficient=is_sufficient,
            completion_ratio=completion_ratio,
        )
        logger.info(
            f"CoverageAgent: Evaluated domain '{domain}'. Validated: {total_validated}/{target_count}, "
            f"Sufficient: {is_sufficient}, Gaps: {len(gaps)}"
        )
        return state
