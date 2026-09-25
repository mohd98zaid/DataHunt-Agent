from typing import Any, Dict, List, Optional
from datahunt.logger import logger
from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.tools.base import ToolResult
from datahunt.tools.search import canonicalize_url, generate_job_fingerprint


def is_preferred_application_url(url: Optional[str]) -> bool:
    """Return True if URL is an official ATS or direct company career application."""
    if not url:
        return False
    u = url.lower()
    if any(ats in u for ats in (
        "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
        "smartrecruiters.com", "teamtailor.com", "personio", "myworkdayjobs.com",
        "icims.com", "oraclecloud.com", "successfactors.com", "ats.rippling.com"
    )):
        return True
    if any(agg in u for agg in (
        "indeed.com", "linkedin.com", "glassdoor.com", "bayt.com",
        "gulftalent.com", "naukrigulf.com", "monster.com", "ziprecruiter.com"
    )):
        return False
    return "/careers" in u or "/jobs" in u


def deduplicate_normalized_jobs(jobs: List[Any]) -> List[Any]:
    """
    Multi-level job deduplication:
    Level 1: source + source_job_id (if both present)
    Level 2: canonical apply_url
    Level 3: canonical job_url
    Level 4: normalized company + normalized title + normalized location
    Collapses duplicates into ONE job, preserving all sources and source URLs,
    and preferring company careers / direct ATS as primary application URL.
    """
    if not jobs:
        return []

    canonical_map: Dict[str, Any] = {}
    merged_sources: Dict[str, List[str]] = {}
    merged_urls: Dict[str, List[str]] = {}

    for job in jobs:
        # Determine candidate keys across tiers
        keys = []
        source = getattr(job, "source", None) or (job.raw_fields.get("source") if hasattr(job, "raw_fields") else None) or ""
        s_id = getattr(job, "source_job_id", None) or getattr(job, "job_id", None)
        if source and s_id:
            keys.append(f"sid::{source.lower()}::{s_id.lower()}")

        apply_u = getattr(job, "apply_url", None) or getattr(job, "primary_application_url", None)
        if apply_u:
            c_apply = canonicalize_url(apply_u)
            if c_apply:
                keys.append(f"app::{c_apply}")

        job_u = getattr(job, "job_url", None) or getattr(job, "canonical_url", None) or getattr(job, "source_url", None)
        if job_u:
            c_job = canonicalize_url(job_u)
            if c_job:
                keys.append(f"url::{c_job}")

        comp = getattr(job, "normalized_company", None) or getattr(job, "company", "")
        title = getattr(job, "normalized_title", None) or getattr(job, "title", "")
        loc = getattr(job, "normalized_location", None) or getattr(job, "location", "")
        city = getattr(job, "city", None)
        if comp and title:
            fp = generate_job_fingerprint(comp, title, loc)
            keys.append(f"fp::{fp}")
            if city:
                fp_city = generate_job_fingerprint(comp, title, city)
                keys.append(f"fp_city::{fp_city}")

        # Find existing primary record if any key matches
        matched_primary_id = None
        for k in keys:
            if k in canonical_map:
                matched_primary_id = canonical_map[k]
                break

        job_sources = list(getattr(job, "sources", []) or ([source] if source else []))
        job_urls = list(getattr(job, "all_source_urls", []) or ([job_u] if job_u else []))

        if matched_primary_id is not None:
            primary = matched_primary_id
            # 1. Merge sources
            p_sources = merged_sources.get(primary.raw_id or str(id(primary)), getattr(primary, "sources", []))
            for s in job_sources:
                if s and s not in p_sources:
                    p_sources.append(s)
            primary.sources = p_sources
            merged_sources[primary.raw_id or str(id(primary))] = p_sources

            # 2. Merge all source URLs
            p_urls = merged_urls.get(primary.raw_id or str(id(primary)), getattr(primary, "all_source_urls", []))
            for u in job_urls:
                if u and u not in p_urls:
                    p_urls.append(u)
            primary.all_source_urls = p_urls
            merged_urls[primary.raw_id or str(id(primary))] = p_urls

            # 3. Prefer direct ATS / company career for primary_application_url
            curr_primary_app = getattr(primary, "primary_application_url", None)
            new_candidate_app = apply_u or job_u
            if is_preferred_application_url(new_candidate_app) and not is_preferred_application_url(curr_primary_app):
                primary.primary_application_url = new_candidate_app
                if hasattr(primary, "apply_url"):
                    primary.apply_url = new_candidate_app

            # Register other keys to this primary
            for k in keys:
                canonical_map[k] = primary
        else:
            p_key = keys[0] if keys else f"id::{getattr(job, 'raw_id', id(job))}"
            if not getattr(job, "sources", None):
                job.sources = job_sources
            if not getattr(job, "all_source_urls", None):
                job.all_source_urls = job_urls
            if not getattr(job, "primary_application_url", None):
                job.primary_application_url = apply_u or job_u or None

            merged_sources[job.raw_id or str(id(job))] = job.sources
            merged_urls[job.raw_id or str(id(job))] = job.all_source_urls

            for k in keys:
                canonical_map[k] = job

    # Return unique primary instances maintaining discovery order
    seen = set()
    unique = []
    for item in canonical_map.values():
        item_id = getattr(item, "raw_id", id(item)) or id(item)
        if item_id not in seen:
            seen.add(item_id)
            unique.append(item)
    return unique


class DedupeTool:
    name = "deduplicate_records"
    description = "Deterministic multi-tier record deduplication preserving all supporting evidence."

    def execute(self, records: List[ExtractedRecord]) -> ToolResult:
        if not records:
            return ToolResult(success=True, data={"unique_records": [], "duplicates_count": 0})

        canonical_map: Dict[str, ExtractedRecord] = {}
        duplicates: List[ExtractedRecord] = []
        clusters: Dict[str, List[str]] = {}

        for record in records:
            key = None
            canon_url = canonicalize_url(record.canonical_url) if record.canonical_url else ""
            if record.identity_key and record.identity_key.strip():
                key = f"ident::{record.identity_key.strip().lower()}"
            elif canon_url:
                key = f"url::{canon_url}"
            elif record.fields.get("title") and record.fields.get("company"):
                key = f"fp::{generate_job_fingerprint(record.fields.get('company'), record.fields.get('title'), record.fields.get('location'))}"
            else:
                key = f"id::{record.id}"

            if key in canonical_map:
                primary = canonical_map[key]
                record.verification_status = VerificationStatus.DUPLICATE
                duplicates.append(record)

                # Merge sources
                p_sources = list(primary.fields.get("sources", []))
                if not p_sources and primary.fields.get("source"):
                    p_sources.append(primary.fields["source"])
                r_sources = list(record.fields.get("sources", []))
                if not r_sources and record.fields.get("source"):
                    r_sources.append(record.fields["source"])
                for s in r_sources:
                    if s and s not in p_sources:
                        p_sources.append(s)
                primary.fields["sources"] = p_sources

                # Merge all_source_urls
                p_urls = list(primary.fields.get("all_source_urls", []))
                if not p_urls and primary.canonical_url:
                    p_urls.append(primary.canonical_url)
                r_urls = list(record.fields.get("all_source_urls", []))
                if not r_urls and record.canonical_url:
                    r_urls.append(record.canonical_url)
                for u in r_urls:
                    if u and u not in p_urls:
                        p_urls.append(u)
                primary.fields["all_source_urls"] = p_urls

                # Prefer direct ATS / company career for primary_application_url
                curr_app = primary.fields.get("primary_application_url") or primary.fields.get("application_url") or primary.canonical_url
                new_app = record.fields.get("primary_application_url") or record.fields.get("application_url") or record.canonical_url
                if is_preferred_application_url(new_app) and not is_preferred_application_url(curr_app):
                    primary.fields["primary_application_url"] = new_app
                    primary.fields["application_url"] = new_app

                # Merge supporting evidence from duplicate into primary record
                existing_ev_keys = {
                    (e.field_name, e.source_document_id, e.evidence_text)
                    for e in primary.evidence
                }
                for ev in record.evidence:
                    ev_key = (ev.field_name, ev.source_document_id, ev.evidence_text)
                    if ev_key not in existing_ev_keys:
                        ev.record_id = primary.id
                        primary.evidence.append(ev)
                        existing_ev_keys.add(ev_key)

                clusters[primary.id].append(record.id)
                logger.info(f"Collapsed duplicate record {record.id} into canonical {primary.id} under key: {key}")
            else:
                # Initialize sources and URLs on primary
                if "sources" not in record.fields and record.fields.get("source"):
                    record.fields["sources"] = [record.fields["source"]]
                if "all_source_urls" not in record.fields and record.canonical_url:
                    record.fields["all_source_urls"] = [record.canonical_url]
                if "primary_application_url" not in record.fields:
                    record.fields["primary_application_url"] = record.fields.get("application_url") or record.canonical_url

                canonical_map[key] = record
                clusters[record.id] = [record.id]

        unique_records = list(canonical_map.values())
        return ToolResult(
            success=True,
            data={
                "unique_records": unique_records,
                "duplicates_count": len(duplicates),
                "clusters": clusters
            }
        )

