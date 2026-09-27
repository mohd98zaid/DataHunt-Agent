"""
Shared Evidence Store.

Central thread-safe evidence abstraction storing facts, primary evidence citations,
and detected contradictions, queryable by entity, claim, and source.
"""
from typing import Any, Dict, List, Optional
from threading import RLock
from datahunt.models.shared_intel import (
    SharedEvidence, Fact, ContradictionItem, EvidenceRelationship
)


class EvidenceStore:
    """
    Central repository for facts, raw source evidence, and cross-source contradictions.
    """
    def __init__(self):
        self._lock = RLock()
        self._evidence: Dict[str, SharedEvidence] = {}  # evidence_id -> SharedEvidence
        self._facts: Dict[str, Fact] = {}  # fact_id -> Fact
        self._contradictions: Dict[str, ContradictionItem] = {}  # conflict_id -> ContradictionItem

        # Inverted index mappings for O(1) entity lookups
        self._entity_evidence_map: Dict[str, List[str]] = {}
        self._entity_facts_map: Dict[str, List[str]] = {}
        self._entity_contradictions_map: Dict[str, List[str]] = {}

    def add_evidence(self, evidence: SharedEvidence) -> str:
        with self._lock:
            self._evidence[evidence.evidence_id] = evidence
            if evidence.entity_id not in self._entity_evidence_map:
                self._entity_evidence_map[evidence.entity_id] = []
            if evidence.evidence_id not in self._entity_evidence_map[evidence.entity_id]:
                self._entity_evidence_map[evidence.entity_id].append(evidence.evidence_id)
            return evidence.evidence_id

    def add_fact(self, fact: Fact) -> str:
        with self._lock:
            self._facts[fact.fact_id] = fact
            if fact.entity_id not in self._entity_facts_map:
                self._entity_facts_map[fact.entity_id] = []
            if fact.fact_id not in self._entity_facts_map[fact.entity_id]:
                self._entity_facts_map[fact.entity_id].append(fact.fact_id)
            return fact.fact_id

    def add_contradiction(self, contradiction: ContradictionItem) -> str:
        with self._lock:
            self._contradictions[contradiction.conflict_id] = contradiction
            if contradiction.entity_id not in self._entity_contradictions_map:
                self._entity_contradictions_map[contradiction.entity_id] = []
            if contradiction.conflict_id not in self._entity_contradictions_map[contradiction.entity_id]:
                self._entity_contradictions_map[contradiction.entity_id].append(contradiction.conflict_id)
            return contradiction.conflict_id

    def evidence_for_entity(self, entity_id: str) -> List[SharedEvidence]:
        with self._lock:
            ev_ids = self._entity_evidence_map.get(entity_id, [])
            return [self._evidence[eid] for eid in ev_ids if eid in self._evidence]

    def evidence_for_claim(self, claim: str) -> List[SharedEvidence]:
        with self._lock:
            target = claim.lower().strip()
            return [
                ev for ev in self._evidence.values()
                if target in ev.claim.lower() or ev.claim.lower() in target
            ]

    def facts_for_entity(self, entity_id: str, field: Optional[str] = None) -> List[Fact]:
        with self._lock:
            fact_ids = self._entity_facts_map.get(entity_id, [])
            facts = [self._facts[fid] for fid in fact_ids if fid in self._facts]
            if field:
                facts = [f for f in facts if f.field.lower() == field.lower()]
            return facts

    def sources_for_entity(self, entity_id: str) -> List[str]:
        with self._lock:
            sources = set()
            for ev in self.evidence_for_entity(entity_id):
                if ev.source_url:
                    sources.add(ev.source_url)
            for f in self.facts_for_entity(entity_id):
                if f.source_url:
                    sources.add(f.source_url)
                elif f.source:
                    sources.add(f.source)
            return sorted(list(sources))

    def contradictions_for_entity(self, entity_id: str) -> List[ContradictionItem]:
        with self._lock:
            c_ids = self._entity_contradictions_map.get(entity_id, [])
            return [self._contradictions[cid] for cid in c_ids if cid in self._contradictions]

    def all_evidence(self) -> List[SharedEvidence]:
        with self._lock:
            return list(self._evidence.values())

    def all_facts(self) -> List[Fact]:
        with self._lock:
            return list(self._facts.values())

    def clear(self) -> None:
        with self._lock:
            self._evidence.clear()
            self._facts.clear()
            self._contradictions.clear()
            self._entity_evidence_map.clear()
            self._entity_facts_map.clear()
            self._entity_contradictions_map.clear()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_evidence": len(self._evidence),
                "total_facts": len(self._facts),
                "total_contradictions": len(self._contradictions),
                "tracked_entities": list(self._entity_evidence_map.keys()),
            }
