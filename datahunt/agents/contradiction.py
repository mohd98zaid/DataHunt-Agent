"""
Contradiction Agent.

Detects and analyzes conflicting facts or claims across independent sources.
Never silently defaults to the first-encountered value; marks unresolved conflicts
with explicit reasoning, timestamps, and confidence boundaries.
"""
import re
from typing import Any, Dict, List, Optional
from datahunt.models.shared_intel import Fact, ContradictionItem, ContradictionStatus
from datahunt.logger import logger


def _extract_number(val: Any) -> Optional[float]:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        nums = re.findall(r"[\d,]+(?:\.\d+)?", val)
        if nums:
            try:
                return float(nums[0].replace(",", ""))
            except Exception:
                return None
    return None


class ContradictionAgent:
    """
    Identifies discrepancies across multi-source facts and evidence.
    """
    def detect_conflicts(self, facts: List[Fact]) -> List[ContradictionItem]:
        """
        Groups facts by (entity_id, field) and flags conflicting values.
        """
        grouped: Dict[tuple[str, str], List[Fact]] = {}
        for f in facts:
            key = (f.entity_id, f.field.lower())
            grouped.setdefault(key, []).append(f)

        contradictions: List[ContradictionItem] = []

        for (ent_id, field_name), fact_list in grouped.items():
            if len(fact_list) < 2:
                continue

            # Compare pairs of facts
            for i in range(len(fact_list)):
                for j in range(i + 1, len(fact_list)):
                    fa = fact_list[i]
                    fb = fact_list[j]

                    if fa.source == fb.source and fa.source:
                        continue  # Same source reporting twice

                    val_a = str(fa.value).strip().lower()
                    val_b = str(fb.value).strip().lower()

                    if val_a == val_b:
                        continue  # Concordant values

                    num_a = _extract_number(fa.value)
                    num_b = _extract_number(fb.value)

                    is_conflict = False
                    reason = ""

                    if num_a is not None and num_b is not None:
                        # Numerical difference test
                        max_val = max(abs(num_a), abs(num_b))
                        if max_val > 0:
                            diff_pct = abs(num_a - num_b) / max_val
                            if diff_pct > 0.05:  # Greater than 5% divergence
                                is_conflict = True
                                reason = f"Numerical value divergence: {fa.value} vs {fb.value} ({diff_pct*100:.1f}% difference)"
                    else:
                        # Qualitative string conflict
                        if val_a not in val_b and val_b not in val_a:
                            is_conflict = True
                            reason = f"Qualitative value divergence: '{fa.value}' vs '{fb.value}'"

                    if is_conflict:
                        # Evaluate if one can be resolved by recency or official domain
                        status = ContradictionStatus.UNRESOLVED
                        resolved_val = None

                        if fa.published_at and fb.published_at and fa.published_at != fb.published_at:
                            reason += f" (Timestamps differ: {fa.published_at} vs {fb.published_at})"

                        conflict = ContradictionItem(
                            entity_id=ent_id,
                            claim=f"Conflicting values reported for {field_name}",
                            source_a=fa.source_url or fa.source,
                            source_b=fb.source_url or fb.source,
                            timestamp_a=fa.published_at,
                            timestamp_b=fb.published_at,
                            value_a=fa.value,
                            value_b=fb.value,
                            unit=fa.unit or fb.unit,
                            possible_reason=reason,
                            resolution_status=status,
                            resolved_value=resolved_val,
                        )
                        contradictions.append(conflict)
                        logger.warning(
                            f"ContradictionAgent: Detected conflict on entity {ent_id}, field '{field_name}': {reason}"
                        )

        return contradictions
