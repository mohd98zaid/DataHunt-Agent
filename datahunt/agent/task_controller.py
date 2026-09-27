"""
Task Controller.

The central coordinating controller of DataHunt-Agent.
Dispatches shared intelligence agents (Source Intelligence, Entity Resolution,
Fact Extraction, Evidence Store, Freshness, Contradiction, Coverage, Risk)
and executes iterative, gap-driven research workflows with strict budget enforcement
and fault isolation.
"""
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from datahunt.models.shared_intel import (
    SourcePlan, EntityIdentity, Fact, SharedEvidence, SharedCoverageState,
    RiskItem, ContradictionItem, FreshnessRating, EntityType
)
from datahunt.evidence.store import EvidenceStore
from datahunt.agents.source_intelligence import SourceIntelligenceAgent
from datahunt.agents.entity_resolution import EntityResolutionAgent
from datahunt.agents.fact_extraction import FactExtractionAgent
from datahunt.agents.evidence import EvidenceAgent
from datahunt.agents.freshness import FreshnessAgent
from datahunt.agents.contradiction import ContradictionAgent
from datahunt.agents.coverage import CoverageAgent
from datahunt.agents.risk import RiskAgent
from datahunt.tools.search import SearchTool
from datahunt.tools.fetch import FetchTool
from datahunt.logger import logger


class TaskController:
    """
    Central controller orchestrating the shared intelligence loop.
    """
    def __init__(
        self,
        gemini_client: Optional[Any] = None,
        search_tool: Optional[SearchTool] = None,
        fetch_tool: Optional[FetchTool] = None,
        evidence_store: Optional[EvidenceStore] = None,
    ):
        self.client = gemini_client
        self.search_tool = search_tool or SearchTool()
        self.fetch_tool = fetch_tool or FetchTool()
        self.evidence_store = evidence_store or EvidenceStore()

        # Shared Intelligence Agents
        self.source_intel = SourceIntelligenceAgent(self.client)
        self.entity_resolver = EntityResolutionAgent(self.client)
        self.fact_extractor = FactExtractionAgent(self.client)
        self.evidence_agent = EvidenceAgent(self.client)
        self.freshness_agent = FreshnessAgent()
        self.contradiction_agent = ContradictionAgent()
        self.coverage_agent = CoverageAgent()
        self.risk_agent = RiskAgent()

    def run_intelligence_pipeline(
        self,
        query: str,
        domain: str = "general",
        target_count: int = 10,
        max_duration_seconds: int = 180,
        user_preferences: Optional[Dict[str, Any]] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the shared intelligence lifecycle:
        Source Intel -> Discovery -> Entity Resolution -> Fact Extraction ->
        Evidence -> Freshness -> Contradiction -> Coverage Gap Search -> Risk -> Synthesis.
        """
        start_time = time.time()
        deadline = start_time + max_duration_seconds

        def emit(event: str, payload: Dict[str, Any]):
            if event_callback:
                try:
                    event_callback(event, payload)
                except Exception as ex:
                    logger.warning(f"Event callback error: {ex}")

        emit("controller.started", {"query": query, "domain": domain, "target": target_count})

        # 1. Source Intelligence Planning
        t_start = time.time()
        source_plan = self.source_intel.plan_sources(domain=domain, query=query)
        emit("source_intel.planned", {
            "primary_sources": source_plan.primary_sources,
            "source_types": [s.value for s in source_plan.source_types],
            "duration_ms": int((time.time() - t_start) * 1000),
        })

        # 2. Discovery Harvesting
        discovered_entities: List[EntityIdentity] = []
        raw_documents: List[Dict[str, Any]] = []
        queries_to_run = source_plan.search_queries[:3] or [query]

        for q in queries_to_run:
            if time.time() > deadline:
                break
            s_res = self.search_tool.execute(query=q, max_results=5)
            if s_res.success and s_res.data:
                hits = s_res.data.get("results", [])
                for h in hits:
                    url = h.get("url") or h.get("link") or ""
                    title = h.get("title") or ""
                    snippet = h.get("snippet") or ""

                    # Resolve entity
                    if domain == "market":
                        stock_ent = self.entity_resolver.resolve_stock(title)
                        if stock_ent and stock_ent not in discovered_entities:
                            discovered_entities.append(stock_ent)
                    elif domain == "jobs":
                        job_ent = self.entity_resolver.resolve_job(
                            company=title.split(" - ")[-1] if " - " in title else "Company",
                            title=title,
                            location="Discovered Location",
                            apply_url=url,
                        )
                        if job_ent and job_ent not in discovered_entities:
                            discovered_entities.append(job_ent)
                    else:
                        comp_ent = self.entity_resolver.resolve_company(company_name=title, domain=url)
                        if comp_ent and comp_ent not in discovered_entities:
                            discovered_entities.append(comp_ent)

                    if url and time.time() <= deadline:
                        f_res = self.fetch_tool.execute(url=url, run_id="task_controller")
                        if f_res.success and f_res.data and hasattr(f_res.data, "extracted_text"):
                            raw_documents.append({
                                "url": url,
                                "title": title,
                                "text": f_res.data.extracted_text[:4000],
                                "published_at": None,
                            })

        # 3. Fact Extraction & Evidence Storage
        all_facts: List[Fact] = []
        all_evidence: List[SharedEvidence] = []

        for ent in discovered_entities:
            for doc in raw_documents:
                # Check relevance
                if ent.canonical_name.lower() in doc["text"].lower() or any(a.lower() in doc["text"].lower() for a in ent.aliases):
                    # Extract facts
                    f_list = self.fact_extractor.extract_facts(
                        text=doc["text"],
                        entity_id=ent.entity_id,
                        source_url=doc["url"],
                        domain=domain,
                    )
                    for f in f_list:
                        self.evidence_store.add_fact(f)
                        all_facts.append(f)

                    # Build evidence
                    ev = self.evidence_agent.evaluate_claim_in_text(
                        claim=f"Operational / market activity for {ent.canonical_name}",
                        text=doc["text"],
                        entity_id=ent.entity_id,
                        source_url=doc["url"],
                    )
                    if ev:
                        self.evidence_store.add_evidence(ev)
                        all_evidence.append(ev)

        # 4. Freshness Assessment
        for ev in all_evidence:
            fresh_eval = self.freshness_agent.evaluate_freshness(
                item_id=ev.evidence_id,
                field="evidence_timestamp",
                published_at=ev.published_at,
                domain=f"{domain}_news" if "market" in domain else "default",
            )
            # Annotate reason if stale
            if fresh_eval.rating in (FreshnessRating.STALE, FreshnessRating.EXPIRED):
                ev.supporting_text += f" [Warning: Source {fresh_eval.rating.value}]"

        # 5. Contradiction Detection
        conflicts = self.contradiction_agent.detect_conflicts(all_facts)
        for conf in conflicts:
            self.evidence_store.add_contradiction(conf)

        # 6. Coverage Measurement
        facts_by_ent = {ent.entity_id: self.evidence_store.facts_for_entity(ent.entity_id) for ent in discovered_entities}
        ev_by_ent = {ent.entity_id: self.evidence_store.evidence_for_entity(ent.entity_id) for ent in discovered_entities}

        coverage_state = self.coverage_agent.evaluate_coverage(
            task_id="ctrl_run",
            domain=domain,
            candidates=discovered_entities,
            facts_by_entity=facts_by_ent,
            evidence_by_entity=ev_by_ent,
            target_count=target_count,
        )

        # 7. Adaptive Gap-Filling Search (if gaps identified and time permits)
        if not coverage_state.is_sufficient and coverage_state.missing_gaps and time.time() < (deadline - 10):
            gap_queries = []
            for g in coverage_state.missing_gaps[:2]:
                gap_queries.extend(g.suggested_queries[:1])

            for gq in gap_queries:
                if time.time() > deadline:
                    break
                logger.info(f"TaskController: Performing adaptive gap search: '{gq}'")
                gap_res = self.search_tool.execute(query=gq, max_results=2)
                if gap_res.success and gap_res.data:
                    for h in gap_res.data.get("results", []):
                        u = h.get("url") or h.get("link")
                        if u:
                            f_res = self.fetch_tool.execute(url=u, run_id="gap_fill")
                            if f_res.success and f_res.data and hasattr(f_res.data, "extracted_text"):
                                raw_documents.append({"url": u, "title": h.get("title", ""), "text": f_res.data.extracted_text[:4000], "published_at": None})

        # 8. Counter-Evidence & Risk Analysis
        all_risks: List[RiskItem] = []
        for ent in discovered_entities:
            ent_docs = [d["text"] for d in raw_documents if ent.canonical_name.lower() in d["text"].lower()]
            corpus = " ".join(ent_docs)
            ent_urls = [d["url"] for d in raw_documents if ent.canonical_name.lower() in d["text"].lower()]
            risks = self.risk_agent.analyze_risks(
                entity_id=ent.entity_id,
                text_corpus=corpus,
                source_urls=ent_urls,
                domain=domain,
            )
            all_risks.extend(risks)

        total_duration = round(time.time() - start_time, 2)
        emit("controller.completed", {
            "entities_count": len(discovered_entities),
            "facts_count": len(all_facts),
            "evidence_count": len(all_evidence),
            "conflicts_count": len(conflicts),
            "risks_count": len(all_risks),
            "duration_seconds": total_duration,
        })

        return {
            "domain": domain,
            "query": query,
            "entities": [e.model_dump() for e in discovered_entities],
            "facts_count": len(all_facts),
            "evidence_count": len(all_evidence),
            "contradictions": [c.model_dump() for c in conflicts],
            "coverage": coverage_state.model_dump(),
            "risks": [r.model_dump() for r in all_risks],
            "duration_seconds": total_duration,
            "evidence_store_stats": self.evidence_store.to_dict(),
        }
