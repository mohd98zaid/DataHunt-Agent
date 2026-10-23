"""
Autonomous Multi-Wave Technical & Deep Research Discovery Engine.

Executes an adaptive, evidence-driven multi-wave research loop:
Wave 1: Primary & Authoritative Documentation (Official docs, GitHub, RFCs, project specs)
Wave 2: Deep Technical & Comparative Analysis (Architecture, state management, scalability, benchmarks)
Wave 3: Contradiction & Nuance Verification (Trade-offs, limitations, counter-evidence, real-world issues)

Classifies source quality tiers, collects canonical Evidence objects, and synthesizes
authoritative Markdown dossiers with zero hallucinated records.
"""
from datetime import datetime, timezone
from enum import Enum
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from datahunt.config import settings
from datahunt.logger import logger
from datahunt.models.evidence import Evidence
from datahunt.models.intent import ResearchIntent, ResearchIntentSpec, ResearchOutputType
from datahunt.models.record import ExtractedRecord, RecordEvidence, VerificationStatus
from datahunt.tools.search import canonicalize_url, is_ad_or_tracking_url
from datahunt.agent.research_report import synthesize_report


class ResearchSourceTier(str, Enum):
    OFFICIAL_DOCS = "OFFICIAL_DOCUMENTATION"
    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    ACADEMIC = "ACADEMIC_SOURCE"
    ENGINEERING_BLOG = "ENGINEERING_BLOG"
    TECH_COMMUNITY = "TECH_COMMUNITY"
    GENERAL_WEB = "GENERAL_WEB"
    LOW_QUALITY = "LOW_QUALITY"


TIER_CONFIDENCE_WEIGHTS = {
    ResearchSourceTier.OFFICIAL_DOCS: 1.0,
    ResearchSourceTier.PRIMARY_SOURCE: 0.95,
    ResearchSourceTier.ACADEMIC: 0.95,
    ResearchSourceTier.ENGINEERING_BLOG: 0.85,
    ResearchSourceTier.TECH_COMMUNITY: 0.65,
    ResearchSourceTier.GENERAL_WEB: 0.50,
    ResearchSourceTier.LOW_QUALITY: 0.25,
}

OFFICIAL_DOMAINS = {
    "github.com",
    "python.org",
    "pypi.org",
    "docs.python.org",
    "langchain-ai.github.io",
    "docs.langchain.com",
    "crewai.com",
    "docs.crewai.com",
    "docs.anthropic.com",
    "platform.openai.com",
    "kubernetes.io",
    "fastapi.tiangolo.com",
    "pydantic.dev",
    "huggingface.co",
    "pytorch.org",
    "tensorflow.org",
}

ACADEMIC_DOMAINS = {
    "arxiv.org",
    "acm.org",
    "ieee.org",
    "semanticscholar.org",
    "research.google",
    "nature.com",
    "science.org",
}

ENGINEERING_BLOG_DOMAINS = {
    "martinfowler.com",
    "highscalability.com",
    "netflixtechblog.com",
    "engineering.fb.com",
    "aws.amazon.com",
    "cloud.google.com",
    "blog.cloudflare.com",
    "dropbox.tech",
    "uber.com",
}


def classify_research_source(url: str, title: str = "") -> Tuple[ResearchSourceTier, float]:
    """
    Deterministically classifies a URL and title into a ResearchSourceTier
    and calculates its base confidence weight.
    """
    if not url:
        return ResearchSourceTier.GENERAL_WEB, 0.5

    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().rstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        path = (parsed.path or "").lower()
    except Exception:
        return ResearchSourceTier.GENERAL_WEB, 0.5

    # Check official domains or official subdomains
    if domain in OFFICIAL_DOMAINS or any(domain.endswith(f".{od}") for od in OFFICIAL_DOMAINS):
        return ResearchSourceTier.OFFICIAL_DOCS, TIER_CONFIDENCE_WEIGHTS[ResearchSourceTier.OFFICIAL_DOCS]

    if domain.startswith("docs.") or "/docs/" in path or "/documentation/" in path or domain.endswith(".readthedocs.io"):
        return ResearchSourceTier.OFFICIAL_DOCS, TIER_CONFIDENCE_WEIGHTS[ResearchSourceTier.OFFICIAL_DOCS]

    # Academic domains
    if domain in ACADEMIC_DOMAINS or domain.endswith(".edu"):
        return ResearchSourceTier.ACADEMIC, TIER_CONFIDENCE_WEIGHTS[ResearchSourceTier.ACADEMIC]

    # Engineering blogs
    if domain in ENGINEERING_BLOG_DOMAINS or "engineering." in domain or "techblog." in domain:
        return ResearchSourceTier.ENGINEERING_BLOG, TIER_CONFIDENCE_WEIGHTS[ResearchSourceTier.ENGINEERING_BLOG]

    # Community discussions
    if any(k in domain for k in ("stackoverflow.com", "reddit.com", "news.ycombinator.com", "dev.to")):
        return ResearchSourceTier.TECH_COMMUNITY, TIER_CONFIDENCE_WEIGHTS[ResearchSourceTier.TECH_COMMUNITY]

    return ResearchSourceTier.GENERAL_WEB, TIER_CONFIDENCE_WEIGHTS[ResearchSourceTier.GENERAL_WEB]


class ResearchDiscoveryEngine:
    """
    Autonomous multi-wave technical research & evidence synthesis engine.
    """

    def __init__(
        self,
        search_tool: Any,
        fetch_tool: Any,
        gemini_client: Optional[Any] = None,
    ):
        self.search = search_tool
        self.fetch = fetch_tool
        self.client = gemini_client

    def run_research(
        self,
        query: str,
        intent_spec: Optional[ResearchIntentSpec] = None,
        max_runtime_seconds: int = 60,
        emit: Optional[Callable[[str, Dict], None]] = None,
        run_id: str = "research_run",
    ) -> Dict[str, Any]:
        """
        Executes multi-wave research discovery, evidence collection, and synthesis.
        """
        start_time = time.time()
        deadline = start_time + max_runtime_seconds

        def _emit(event_type: str, data: Dict[str, Any]):
            if emit:
                try:
                    emit(event_type, data)
                except Exception as e:
                    logger.debug(f"[ResearchDiscoveryEngine] Event emission error: {e}")

        # Resolve intent spec if not passed
        if not intent_spec:
            from datahunt.agents.intent_router import IntentRouter
            intent_spec = IntentRouter(gemini_client=self.client).classify(query, agent_mode="research")

        _emit("research.intent", {
            "intent": intent_spec.intent.value,
            "topic": intent_spec.topic,
            "answer_style": intent_spec.answer_style,
            "requested_output": intent_spec.requested_output.value,
        })

        seen_canonical_urls: Set[str] = set()
        collected_evidence: List[Evidence] = []
        fetched_documents: List[Any] = []
        search_queries_run: List[str] = []
        total_fetches = 0
        stop_reason = "COVERAGE_SATISFIED"

        # ------------------------------------------------------------------
        # WAVE 1: Primary & Official Documentation Discovery
        # ------------------------------------------------------------------
        _emit("phase.change", {
            "phase": "RESEARCH_WAVE_1",
            "message": "Wave 1: Querying official documentation & primary technical sources",
            "thought": "Hunting for primary specifications and official project sources",
        })

        wave_1_queries = self._generate_wave_1_queries(query, intent_spec)
        wave_1_docs = self._execute_search_and_fetch_wave(
            wave_1_queries,
            max_fetches=5,
            deadline=deadline,
            seen_urls=seen_canonical_urls,
            run_id=run_id,
            emit_fn=_emit,
            wave_num=1
        )
        fetched_documents.extend(wave_1_docs)
        search_queries_run.extend(wave_1_queries)
        total_fetches += len(wave_1_docs)

        # Extract Wave 1 evidence
        for doc in wave_1_docs:
            evs = self._extract_evidence_from_doc(doc, wave=1, query=query)
            collected_evidence.extend(evs)

        # ------------------------------------------------------------------
        # WAVE 2: Deep Technical & Comparative Analysis
        # ------------------------------------------------------------------
        if time.time() < deadline:
            _emit("phase.change", {
                "phase": "RESEARCH_WAVE_2",
                "message": "Wave 2: Analyzing architecture, benchmarks & production patterns",
                "thought": "Deep dive into architectural trade-offs, state management, and scalability",
            })

            wave_2_queries = self._generate_wave_2_queries(query, intent_spec, collected_evidence)
            wave_2_docs = self._execute_search_and_fetch_wave(
                wave_2_queries,
                max_fetches=5,
                deadline=deadline,
                seen_urls=seen_canonical_urls,
                run_id=run_id,
                emit_fn=_emit,
                wave_num=2
            )
            fetched_documents.extend(wave_2_docs)
            search_queries_run.extend(wave_2_queries)
            total_fetches += len(wave_2_docs)

            for doc in wave_2_docs:
                evs = self._extract_evidence_from_doc(doc, wave=2, query=query)
                collected_evidence.extend(evs)

        # ------------------------------------------------------------------
        # WAVE 3: Contradiction & Nuance Verification
        # ------------------------------------------------------------------
        if time.time() < deadline:
            _emit("phase.change", {
                "phase": "RESEARCH_WAVE_3",
                "message": "Wave 3: Checking limitations, trade-offs & counter-evidence",
                "thought": "Identifying known failure modes, drawbacks, and edge cases",
            })

            wave_3_queries = self._generate_wave_3_queries(query, intent_spec)
            wave_3_docs = self._execute_search_and_fetch_wave(
                wave_3_queries,
                max_fetches=3,
                deadline=deadline,
                seen_urls=seen_canonical_urls,
                run_id=run_id,
                emit_fn=_emit,
                wave_num=3
            )
            fetched_documents.extend(wave_3_docs)
            search_queries_run.extend(wave_3_queries)
            total_fetches += len(wave_3_docs)

            for doc in wave_3_docs:
                evs = self._extract_evidence_from_doc(doc, wave=3, query=query, is_counter=True)
                collected_evidence.extend(evs)

        if time.time() >= deadline:
            stop_reason = "DEADLINE_REACHED"

        # ------------------------------------------------------------------
        # STAGE 4: Synthesis & Evidence-backed Dossier Generation
        # ------------------------------------------------------------------
        _emit("phase.change", {
            "phase": "SYNTHESIS",
            "message": f"Synthesizing findings across {len(collected_evidence)} evidence points",
            "thought": "Structuring definitive technical report with explicit citations",
        })

        synthesis_markdown = self._synthesize_report(
            query=query,
            intent_spec=intent_spec,
            evidence=collected_evidence,
            documents=fetched_documents,
            queries=search_queries_run,
        )

        # Convert evidence into research records for database persistence and table rendering
        research_records = self._build_research_records(collected_evidence, fetched_documents, run_id=run_id)

        _emit("phase.change", {
            "phase": "COMPLETED",
            "message": f"Research complete: {len(collected_evidence)} evidence points across {len(fetched_documents)} sources",
            "thought": "Complete",
        })

        return {
            "status": "completed" if collected_evidence or fetched_documents else "partial",
            "answer_markdown": synthesis_markdown,
            "evidence": collected_evidence,
            "source_documents": fetched_documents,
            "records": research_records,
            "queries_executed": search_queries_run,
            "pages_fetched": total_fetches,
            "stop_reason": stop_reason,
            "intent_spec": intent_spec,
        }

    # ------------------------------------------------------------------
    # Query Generation Strategies
    # ------------------------------------------------------------------

    def _clean_search_topic(self, query: str, intent_spec: ResearchIntentSpec) -> str:
        """Derives a concise, search-friendly topic without conversational question filler."""
        subject = (intent_spec.topic or "").strip()
        q_clean = query.strip(" .?!\"'")
        if not subject or len(subject) >= len(q_clean):
            subject = re.sub(
                r"^(?:what\s+is|who\s+is|explain|tell\s+me\s+about|how\s+does|overview\s+of|search\s+for|find)\s+",
                "",
                q_clean,
                flags=re.I
            ).strip(" .?!\"'")
        return subject or q_clean

    def _generate_wave_1_queries(self, query: str, intent_spec: ResearchIntentSpec) -> List[str]:
        """Official documentation, specifications, and primary sources."""
        q_clean = query.strip(" .?!\"'")
        subject = self._clean_search_topic(query, intent_spec)
        queries = []

        if intent_spec.intent == ResearchIntent.COMPARISON:
            parts = re.split(r"\b(?:vs\.?|versus|compare|and)\b", q_clean, flags=re.IGNORECASE)
            entities = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]
            if len(entities) >= 2:
                queries.append(f"{entities[0]} official documentation architecture")
                queries.append(f"{entities[1]} official documentation architecture")
                queries.append(f"{entities[0]} {entities[1]} comparison official")
            else:
                queries.append(f"{subject} official documentation architecture")
                queries.append(f"{subject} architecture overview")
        elif intent_spec.intent == ResearchIntent.HOW_TO:
            queries.append(f"{subject} official guide documentation")
            queries.append(f"{subject} technical implementation steps")
        elif intent_spec.intent == ResearchIntent.COMPANY_RESEARCH:
            queries.append(f"{subject} official site company profile overview")
            queries.append(f"{subject} valuation funding business model")
        else:
            queries.append(f"{subject} official documentation architecture")
            queries.append(f"{subject} technical report OR specification OR paper")
            queries.append(f"{subject} architecture overview docs")

        return queries[:3]

    def _generate_wave_2_queries(
        self,
        query: str,
        intent_spec: ResearchIntentSpec,
        wave_1_evidence: List[Evidence]
    ) -> List[str]:
        """Deep technical queries on architecture, benchmarks, and production patterns."""
        q_clean = query.strip(" .?!\"'")
        subject = self._clean_search_topic(query, intent_spec)
        queries = []

        if intent_spec.intent == ResearchIntent.COMPARISON:
            queries.append(f"{q_clean} production benchmarks performance scalability")
            queries.append(f"{q_clean} state management error handling recovery")
            queries.append(f"{q_clean} architecture trade-offs real world engineering")
        elif intent_spec.intent == ResearchIntent.HOW_TO:
            queries.append(f"{subject} best practices production architecture")
            queries.append(f"{subject} code examples design patterns")
        else:
            queries.append(f"{subject} benchmarks performance architecture")
            queries.append(f"{subject} deep dive design patterns implementation")
            queries.append(f"{subject} paper OR arxiv OR \"technical report\"")

        return queries[:3]

    def _generate_wave_3_queries(self, query: str, intent_spec: ResearchIntentSpec) -> List[str]:
        """Contradiction, trade-off, and failure mode queries."""
        subject = self._clean_search_topic(query, intent_spec)
        queries = [
            f"{subject} limitations trade-offs drawbacks",
            f"{subject} production issues failure modes bottlenecks",
        ]
        return queries[:2]

    # ------------------------------------------------------------------
    # Network Search & Fetch Wave
    # ------------------------------------------------------------------

    def _execute_search_and_fetch_wave(
        self,
        queries: List[str],
        max_fetches: int,
        deadline: float,
        seen_urls: Set[str],
        run_id: str,
        emit_fn: Callable[[str, Dict], None],
        wave_num: int,
    ) -> List[Any]:
        candidate_hits = []

        for q in queries:
            if time.time() >= deadline:
                break
            try:
                res = self.search.execute(query=q, limit=8)
                if res.success and res.data:
                    for hit in res.data:
                        u = hit.get("url")
                        if not u or is_ad_or_tracking_url(u):
                            continue
                        canon = canonicalize_url(u)
                        if canon and canon not in seen_urls and not is_ad_or_tracking_url(canon):
                            seen_urls.add(canon)
                            candidate_hits.append(hit)
            except Exception as e:
                logger.warning(f"[ResearchDiscoveryEngine] Wave {wave_num} search failed for '{q}': {e}")

        # Prioritize candidates by source tier
        def _hit_rank(h):
            u = h.get("url", "")
            t, score = classify_research_source(u, h.get("title", ""))
            return -score

        candidate_hits.sort(key=_hit_rank)
        to_fetch = candidate_hits[:max_fetches]

        fetched_docs = []
        for hit in to_fetch:
            if time.time() >= deadline:
                break
            u = hit.get("url")
            try:
                f_res = self.fetch.execute(url=u, run_id=run_id)
                if f_res.success and f_res.data:
                    doc = f_res.data
                    fetched_docs.append(doc)
                    emit_fn("research.page_fetched", {
                        "url": u,
                        "title": doc.title or hit.get("title", ""),
                        "wave": wave_num,
                    })
            except Exception as fe:
                logger.warning(f"[ResearchDiscoveryEngine] Fetch failed for {u}: {fe}")

        return fetched_docs

    # ------------------------------------------------------------------
    # Evidence Extraction
    # ------------------------------------------------------------------

    def _extract_evidence_from_doc(
        self,
        doc: Any,
        wave: int,
        query: str,
        is_counter: bool = False
    ) -> List[Evidence]:
        """Extracts structured Evidence objects from a SourceDocument."""
        url = getattr(doc, "requested_url", "") or getattr(doc, "canonical_url", "")
        title = getattr(doc, "title", "") or "Source Document"
        text = getattr(doc, "extracted_text", "") or ""

        if not text:
            return []

        tier, base_conf = classify_research_source(url, title)
        evidence_list = []

        # 1. Clean meta description if present in structured metadata block
        meta_desc = ""
        meta_match = re.search(r"--- STRUCTURED PAGE METADATA ---\s*(.*?)\s*--- END PAGE METADATA ---", text, re.DOTALL)
        if meta_match:
            for line in meta_match.group(1).splitlines():
                if line.startswith("DESCRIPTION:"):
                    meta_desc = line[len("DESCRIPTION:"):].strip()
                    break

        if meta_desc and len(meta_desc) > 30:
            evidence_list.append(Evidence(
                claim=meta_desc,
                source_url=url,
                source_title=title,
                source_type=tier.value,
                confidence=min(1.0, round(base_conf + 0.05, 2)),
                sentiment="neutral",
                is_counter_evidence=False,
                metadata={
                    "wave": wave,
                    "query": query,
                    "domain": urlparse(url).netloc,
                    "is_meta_description": True,
                }
            ))

        # 2. Strip structured headers from page text before extracting paragraphs
        clean_text = re.sub(
            r"--- STRUCTURED (?:JOB SCHEMA \(JSON-LD\)|PAGE METADATA) ---.*?--- END (?:STRUCTURED SCHEMA|PAGE METADATA) ---\s*",
            "",
            text,
            flags=re.DOTALL
        )

        boilerplate_markers = (
            "skip to main content",
            "fetch the complete documentation index",
            "documentation index fetch",
            "/llms.txt",
            "cookie policy",
            "privacy policy",
            "all rights reserved",
            "table of contents",
            "sign in to continue",
            "terms of service",
        )

        raw_paragraphs = [p.strip() for p in clean_text.split("\n\n") if len(p.strip()) > 40]
        counter_keywords = {"limitation", "drawback", "bottleneck", "issue", "tradeoff", "trade-off", "latency", "overhead", "complex", "difficulty", "risk", "pitfall"}

        for p in raw_paragraphs:
            if len(evidence_list) >= 6:
                break
            p_lower = p.lower()
            if any(bm in p_lower for bm in boilerplate_markers):
                continue
            if p.count("\n") > 5 and sum(len(l) for l in p.splitlines()) / (p.count("\n") + 1) < 25:
                continue

            snippet = " ".join(p.split())
            if len(snippet) > 300:
                snippet = snippet[:297] + "..."

            if meta_desc and snippet in meta_desc:
                continue

            has_counter_signal = any(k in snippet.lower() for k in counter_keywords)
            flagged_counter = is_counter or has_counter_signal

            ev = Evidence(
                claim=snippet,
                source_url=url,
                source_title=title,
                source_type=tier.value,
                confidence=round(base_conf, 2),
                sentiment="negative" if flagged_counter else "neutral",
                is_counter_evidence=flagged_counter,
                metadata={
                    "wave": wave,
                    "query": query,
                    "domain": urlparse(url).netloc,
                }
            )
            evidence_list.append(ev)

        return evidence_list

    # ------------------------------------------------------------------
    # Report Synthesis
    # ------------------------------------------------------------------

    def _synthesize_report(
        self,
        query: str,
        intent_spec: ResearchIntentSpec,
        evidence: List[Evidence],
        documents: List[Any],
        queries: List[str],
    ) -> str:
        """Generates an evidence-backed authoritative Markdown technical dossier."""
        return synthesize_report(
            query=query,
            intent_spec=intent_spec,
            evidence=evidence,
            documents=documents,
            queries=queries,
            client=self.client,
            classify_source=classify_research_source,
        )

    def _build_research_records(
        self,
        evidence_list: List[Evidence],
        documents: List[Any],
        run_id: str
    ) -> List[ExtractedRecord]:
        """Maps collected Evidence into ExtractedRecord models."""
        import uuid
        document_ids = {}
        for document in documents:
            document_id = getattr(document, "id", None)
            if not document_id:
                continue
            for url in (getattr(document, "requested_url", None), getattr(document, "canonical_url", None)):
                if url:
                    document_ids[canonicalize_url(url)] = document_id
        records = []
        for ev in evidence_list:
            rec_id = f"rec_ev_{uuid.uuid4().hex[:12]}"
            source_document_id = document_ids.get(canonicalize_url(ev.source_url))
            record_evidence = []
            if source_document_id:
                record_evidence.append(RecordEvidence(
                    record_id=rec_id,
                    source_document_id=source_document_id,
                    field_name="claim",
                    evidence_text=ev.claim,
                    locator={"source_url": ev.source_url, "source_title": ev.source_title},
                ))
            rec = ExtractedRecord(
                id=rec_id,
                run_id=run_id,
                source_document_id=source_document_id,
                record_type="research_evidence",
                canonical_url=ev.source_url,
                fields={
                    "topic": ev.metadata.get("query", ""),
                    "claim": ev.claim,
                    "source_url": ev.source_url,
                    "source_title": ev.source_title,
                    "source_type": ev.source_type,
                    "is_counter_evidence": ev.is_counter_evidence,
                    "sentiment": ev.sentiment,
                },
                confidence=ev.confidence,
                verification_status=VerificationStatus.VERIFIED,
                evidence=record_evidence,
            )
            records.append(rec)
        return records
