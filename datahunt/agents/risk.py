"""
Risk Agent.

Hunts for counter-evidence, bearish factors, and corporate/employment vulnerabilities.
Distinguishes KNOWN_RISK (empirically supported in analyzed documents) from
POSSIBLE_RISK (inferred structural risks) and UNKNOWN (no disclosures found).
Never hallucinates synthetic risks in the absence of source evidence.
"""
import re
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from datahunt.models.shared_intel import RiskItem, RiskSeverity, RiskStatus
from datahunt.logger import logger


_RISK_SIGNATURES = [
    (
        "regulatory_probe",
        RiskSeverity.HIGH,
        re.compile(r"\b(?:sebi|sec|investigation|probe|regulatory action|show cause notice|penalty imposed|audit qualification)\b", re.I),
        "Regulatory scrutiny, investigation, or audit qualification disclosed",
    ),
    (
        "promoter_pledge",
        RiskSeverity.HIGH,
        re.compile(r"\b(?:promoter pledge|pledged shares|high pledge|margin call)\b", re.I),
        "High promoter share pledge or margin pressure",
    ),
    (
        "debt_burden",
        RiskSeverity.MEDIUM,
        re.compile(r"\b(?:high debt|debt overhang|default on payment|liquidity crunch|debt-to-equity ratio exceeds)\b", re.I),
        "Elevated leverage or liquidity stress identified",
    ),
    (
        "earnings_downgrade",
        RiskSeverity.MEDIUM,
        re.compile(r"\b(?:earnings miss|target price cut|downgrade|rating cut|margin contraction|weak guidance)\b", re.I),
        "Analyst rating downgrade or quarterly earnings miss reported",
    ),
    (
        "corporate_restructuring",
        RiskSeverity.MEDIUM,
        re.compile(r"\b(?:layoffs|workforce reduction|job cuts|hiring freeze|restructuring|management exit)\b", re.I),
        "Headcount reduction, restructuring, or leadership instability",
    ),
]


class RiskAgent:
    """
    Identifies evidence-backed risks and counter-evidence across corporate and market entities.
    """
    def analyze_risks(
        self,
        entity_id: str,
        text_corpus: str,
        source_urls: Optional[List[str]] = None,
        domain: str = "market",
    ) -> List[RiskItem]:
        """
        Scans provided source text for confirmed risk indicators.
        """
        urls = source_urls or []
        if not text_corpus:
            return [
                RiskItem(
                    entity_id=entity_id,
                    risk_type="information_availability",
                    severity=RiskSeverity.LOW,
                    status=RiskStatus.UNKNOWN,
                    description="No risk disclosures or negative reports identified in analyzed sources",
                    evidence_urls=urls,
                )
            ]

        found_risks: List[RiskItem] = []
        sentences = [s.strip() for s in text_corpus.replace("\n", " ").split(".") if s.strip()]

        for r_type, severity, pattern, desc_template in _RISK_SIGNATURES:
            for s in sentences:
                if pattern.search(s):
                    # Found concrete empirical mention
                    evidence_snippet = s[:200]
                    found_risks.append(
                        RiskItem(
                            entity_id=entity_id,
                            risk_type=r_type,
                            severity=severity,
                            status=RiskStatus.KNOWN_RISK,
                            description=f"{desc_template}: \"{evidence_snippet}\"",
                            evidence_urls=urls,
                        )
                    )
                    break  # One instance per risk type is sufficient

        if not found_risks:
            found_risks.append(
                RiskItem(
                    entity_id=entity_id,
                    risk_type="clean_disclosures",
                    severity=RiskSeverity.LOW,
                    status=RiskStatus.UNKNOWN,
                    description="No material adverse regulatory, debt, or operational risks detected in analyzed sources",
                    evidence_urls=urls,
                )
            )

        logger.info(
            f"RiskAgent: Evaluated entity {entity_id}. Detected {len([r for r in found_risks if r.status == RiskStatus.KNOWN_RISK])} known risks."
        )
        return found_risks
