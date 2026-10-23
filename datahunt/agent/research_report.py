"""Pure report rendering for the multi-wave research engine."""

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from datahunt.logger import logger
from datahunt.models.evidence import Evidence
from datahunt.models.intent import ResearchIntent, ResearchIntentSpec


def synthesize_report(
    *,
    query: str,
    intent_spec: ResearchIntentSpec,
    evidence: list[Evidence],
    documents: list[Any],
    queries: list[str],
    client: Any = None,
    classify_source: Callable[[str, str], tuple[Any, float]],
) -> str:
    """Use the configured LLM when available, otherwise render deterministic Markdown."""
    if client and getattr(client, "is_live", False):
        try:
            source_texts = ""
            for doc in documents[:8]:
                snippet = (getattr(doc, "extracted_text", "") or "").strip()
                url = getattr(doc, "requested_url", "") or getattr(doc, "canonical_url", "")
                if snippet:
                    source_texts += f"Source URL: {url}\n{snippet[:2500]}\n\n---\n\n"
            records_data = [{"topic": query, "claim": ev.claim, "source_url": ev.source_url} for ev in evidence[:10]]
            report = client.summarize_run(
                run_metadata={"topic": query, "pages_fetched": len(documents), "records_verified": len(evidence)},
                verified_records=records_data,
                source_texts=source_texts,
                query_text=query,
                agent_mode="research",
            )
            if report and len(report.strip()) > 100:
                return report
        except Exception as exc:  # noqa: BLE001 - provider failures must fall back to deterministic output
            logger.warning("Live research report synthesis failed (%s); using deterministic report", exc)

    primary = [ev for ev in evidence if not ev.is_counter_evidence]
    counter = [ev for ev in evidence if ev.is_counter_evidence]
    seen_urls = set()
    bibliography = []
    for doc in documents:
        url = getattr(doc, "requested_url", "")
        title = getattr(doc, "title", "") or url
        if url and url not in seen_urls:
            seen_urls.add(url)
            tier, _ = classify_source(url, title)
            bibliography.append(f"[{len(bibliography) + 1}] **{title}** — `{tier.value}`\n   <{url}>")
    sources_section = "\n\n".join(bibliography) if bibliography else "_No external sources recorded._"
    comparison = intent_spec.intent == ResearchIntent.COMPARISON or "compare" in query.lower() or " vs " in query.lower()
    if comparison:
        return synthesize_comparison_report(
            query=query,
            primary_evidence=primary,
            counter_evidence=counter,
            sources_section=sources_section,
            queries_count=len(queries),
            docs_count=len(documents),
        )
    return synthesize_general_research_report(
        query=query,
        intent_spec=intent_spec,
        primary_evidence=primary,
        counter_evidence=counter,
        sources_section=sources_section,
        queries_count=len(queries),
        docs_count=len(documents),
    )


def synthesize_comparison_report(
    *,
    query: str,
    primary_evidence: list[Evidence],
    counter_evidence: list[Evidence],
    sources_section: str,
    queries_count: int,
    docs_count: int,
) -> str:
    parts = [p.strip(" .?!\"'") for p in re.split(r"\b(?:vs\.?|versus|compare|and)\b", query, flags=re.IGNORECASE)]
    parts = [part for part in parts if len(part) > 1]
    e1 = parts[0] if parts else "Framework A"
    e2 = parts[1] if len(parts) > 1 else "Framework B"
    e2 = re.split(r"\s+for\s+", e2, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    highlights = "\n".join(f"- **[{ev.source_type}]** {ev.claim} ([Source]({ev.source_url}))" for ev in primary_evidence[:5])
    highlights = highlights or f"- Evaluated the documented architecture and operating model for {e1} and {e2}."
    caveats = "\n".join(f"- ⚠️ **{ev.claim}** ([Reference]({ev.source_url}))" for ev in counter_evidence[:4])
    caveats = caveats or "- Trade-offs depend on implementation complexity, operating cost, and required control."
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# ⚖️ Technical Architecture Comparison: {e1} vs {e2}

**Research Objective:** {query}  
**Synthesis Pipeline:** Multi-Wave Technical Discovery ({queries_count} targeted search queries across {docs_count} verified sources)  
**Timestamp:** {timestamp}

---

## 1. Executive Summary & Verdict

This comparison evaluates **{e1}** and **{e2}** using the collected evidence rather than assuming that one architecture fits every workload.

**Production Recommendation:** Prefer the option whose documented control model, operational burden, and evidence-backed capabilities match the target system.

---

## 2. Head-to-Head Architectural Matrix

| Evaluation Dimension | {e1} | {e2} | Architectural Trade-Off |
| :--- | :--- | :--- | :--- |
| **State Management** | See verified findings below | See verified findings below | Compare documented persistence and state boundaries |
| **Human-in-the-Loop** | See verified findings below | See verified findings below | Compare pause, review, and recovery controls |
| **Documented capabilities** | See verified findings below | See verified findings below | Evidence coverage matters more than generic labels |
| **Operational fit** | Depends on deployment context | Depends on deployment context | Simplicity versus control |
| **Failure modes** | Review source-backed caveats | Review source-backed caveats | Validate before production |

---

## 3. Core Architectural Analysis

Use the cited findings below to compare state, control flow, scalability, and operating constraints for the requested workload.

---

## 4. Key Evidence & Verified Findings
{highlights}

---

## 5. Production Limitations, Nuances & Caveats
{caveats}

---

## 6. Authoritative Primary Sources & Citations
{sources_section}
"""


def synthesize_general_research_report(
    *,
    query: str,
    intent_spec: ResearchIntentSpec,
    primary_evidence: list[Evidence],
    counter_evidence: list[Evidence],
    sources_section: str,
    queries_count: int,
    docs_count: int,
) -> str:
    highlights = "\n".join(f"- **[{ev.source_type}]** {ev.claim} ([Source]({ev.source_url}))" for ev in primary_evidence[:6])
    highlights = highlights or "- No primary evidence was extracted from the collected sources."
    caveats = "\n".join(f"- ⚠️ **{ev.claim}** ([Reference]({ev.source_url}))" for ev in counter_evidence[:4])
    caveats = caveats or "- No counter-evidence was extracted from the collected sources."
    topic = intent_spec.topic or query
    overview = next((ev.claim for ev in primary_evidence if ev.claim.strip()), intent_spec.reason or f"Evidence collected for: {query}")
    rows = "\n".join(
        f"| **Finding {idx}** | `{ev.source_type}` | {ev.claim[:120]} | [{urlparse(ev.source_url).netloc or 'Source'}]({ev.source_url}) |"
        for idx, ev in enumerate(primary_evidence[:8], 1)
    ) or "| **Finding 1** | `unavailable` | No extracted findings | [Sources](#) |"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# 📄 Comprehensive Technical Research Paper: {query}

> **Document Type:** Evidence-backed research report  
> **Topic:** {topic} • **Classification:** `{intent_spec.intent.value.upper()}` • **Discovery Waves:** 3 ({queries_count} queries across {docs_count} verified endpoints) • **Generated:** {timestamp}

### Abstract & Executive Summary
This report summarizes the collected evidence for **{topic}** and keeps claims tied to the retrieved sources.

**Keywords:** {topic}, evidence, constraints, implementation

---

## 1. Executive Answer & Technical Overview

{overview}

---

## 2. Core Principles & Architecture

{highlights}

---

## 3. Subsystem Taxonomy & Detailed Capability Matrix

| Subsystem / Primitive | Tier / Classification | Verified Technical Capability | Primary Citation |
| :--- | :--- | :--- | :--- |
{rows}

---

## 4. Practical Implementation & Production Engineering Patterns

Use the source links above as the implementation boundary. Any missing operational detail should be verified before adoption.

---

## 5. Comparative Trade-offs & Production Dimensions

Compare documented capabilities, cost, latency, reliability, and integration constraints for the specific workload.

---

## 6. Nuances, Constraints & Edge Cases

{caveats}

---

## 7. Concluding Verdict & Research Horizons

The evidence supports the findings above; unresolved questions should remain explicit rather than being filled with generic assumptions.

---

## 8. Verified Sources & Primary Documentation

{sources_section}
"""
