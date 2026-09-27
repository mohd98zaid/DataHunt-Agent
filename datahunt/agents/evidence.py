"""
Evidence Agent.

Manages first-class SharedEvidence objects connecting factual claims to verifiable
source texts, URLs, and relationships (SUPPORTS, CONTRADICTS, MENTIONS, DERIVED_FROM).
Rejects unsubstantiated claims and maintains source provenance.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from datahunt.models.shared_intel import (
    SharedEvidence, EvidenceRelationship, EvidenceConfidence
)
from datahunt.logger import logger


class EvidenceAgent:
    """
    Evaluates text passages against claims and generates verified SharedEvidence instances.
    """
    def __init__(self, gemini_client: Optional[Any] = None):
        self.client = gemini_client

    def build_evidence(
        self,
        claim: str,
        entity_id: str,
        source_url: str,
        supporting_text: str,
        relationship: EvidenceRelationship = EvidenceRelationship.SUPPORTS,
        published_at: Optional[str] = None,
        confidence: EvidenceConfidence = EvidenceConfidence.HIGH,
    ) -> SharedEvidence:
        """
        Constructs a structured SharedEvidence object.
        """
        evidence = SharedEvidence(
            claim=claim.strip(),
            entity_id=entity_id,
            source_url=source_url,
            relationship=relationship,
            published_at=published_at,
            supporting_text=supporting_text.strip(),
            confidence=confidence,
        )
        logger.info(f"EvidenceAgent: Built evidence for claim '{claim[:40]}...' on entity {entity_id}")
        return evidence

    def evaluate_claim_in_text(
        self,
        claim: str,
        text: str,
        entity_id: str,
        source_url: str,
        published_at: Optional[str] = None,
    ) -> Optional[SharedEvidence]:
        """
        Checks whether source text supports, contradicts, or mentions the claim.
        """
        if not claim or not text:
            return None

        claim_low = claim.lower()
        text_low = text.lower()

        # Keywords extraction from claim
        tokens = [w for w in re_tokenize(claim_low) if len(w) > 3]
        if not tokens:
            return None

        matching_tokens = [t for t in tokens if t in text_low]
        match_ratio = len(matching_tokens) / len(tokens)

        if match_ratio < 0.3:
            return None  # Unrelated text

        # Check for contradiction / negative markers
        negations = ["not", "denied", "refuted", "false", "disputed", "rejected", "contrary", "no longer"]
        has_negation = any(neg in text_low for neg in negations)

        # Locate sentence containing most matching tokens
        sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
        best_sentence = ""
        best_score = 0
        for s in sentences:
            s_low = s.lower()
            score = sum(1 for t in tokens if t in s_low)
            if score > best_score:
                best_score = score
                best_sentence = s

        relationship = EvidenceRelationship.SUPPORTS
        confidence = EvidenceConfidence.HIGH if match_ratio > 0.7 else EvidenceConfidence.MEDIUM

        if has_negation and match_ratio >= 0.5:
            # Check if negation is in the same best sentence
            if any(neg in best_sentence.lower() for neg in negations):
                relationship = EvidenceRelationship.CONTRADICTS
        elif match_ratio < 0.6:
            relationship = EvidenceRelationship.MENTIONS

        return self.build_evidence(
            claim=claim,
            entity_id=entity_id,
            source_url=source_url,
            supporting_text=best_sentence or text[:250],
            relationship=relationship,
            published_at=published_at,
            confidence=confidence,
        )


def re_tokenize(s: str) -> List[str]:
    import re
    return re.findall(r"\b\w+\b", s)
