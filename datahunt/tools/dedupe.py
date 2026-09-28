import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
from datahunt.logger import logger
from datahunt.models import ExtractedRecord, VerificationStatus
from datahunt.tools.base import ToolResult
from datahunt.tools.search import canonicalize_url, generate_job_fingerprint


def is_board_or_directory_url(url: Optional[str]) -> bool:
    """
    Return True if URL is an index, aggregate, board root, or multi-job listing page
    rather than a direct, specific job opening.
    Such URLs must never be used as standalone deduplication keys across individual jobs.
    """
    if not url:
        return True
    u_lower = str(url).lower().strip()
    try:
        parsed = urlparse(u_lower)
    except Exception:
        return True
    hostname = parsed.hostname or ""
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]

    if not segments:
        return True

    # Greenhouse: boards.greenhouse.io/<company> is a board; specific job has /jobs/\d+ or gh_jid=\d+ or /job/
    if "greenhouse.io" in hostname:
        has_job_id = bool(
            re.search(r"/jobs/\d+", path)
            or re.search(r"gh_jid=\d+", parsed.query)
            or re.search(r"/job\b", path)
        )
        return not has_job_id

    # Lever: jobs.lever.co/<company> is a board; specific job has at least 2 segments
    if "lever.co" in hostname:
        return len(segments) < 2

    # Ashby: jobs.ashbyhq.com/<company> is a board; specific job has at least 2 segments
    if "ashbyhq.com" in hostname:
        return len(segments) < 2

    # Workable: apply.workable.com/<company> is a board; specific job has /j/<id>
    if "workable.com" in hostname:
        return not bool(re.search(r"/j/[a-z0-9]+", path))

    # General directory/career board patterns
    last_seg = segments[-1] if segments else ""
    if last_seg in ("careers", "jobs", "openings", "vacancies", "all-jobs", "positions", "search", "browse"):
        return True
    if path in ("/careers", "/jobs", "/openings", "/vacancies", "/about/careers", "/en/careers", "/us/careers"):
        return True

    return False


def are_titles_compatible(t1: Optional[str], t2: Optional[str]) -> bool:
    """
    Return True if two job titles represent the same or substantially compatible role.
    Distinct roles (e.g. 'Customer Engineer' vs 'Software Engineer - Applied AI') return False.
    """
    if not t1 or not t2:
        return True
    str1 = str(t1).strip().lower()
    str2 = str(t2).strip().lower()
    if str1 == str2:
        return True
    c1 = re.sub(r'[^a-z0-9]', '', str1)
    c2 = re.sub(r'[^a-z0-9]', '', str2)
    if c1 == c2:
        return True
    # Substring match if length ratio is very close (e.g. title with vs without trailing location/seniority)
    if (c1 in c2 or c2 in c1) and min(len(c1), len(c2)) / max(len(c1), len(c2)) >= 0.75:
        return True
    STOP_WORDS = {
        'and', 'or', 'in', 'at', 'the', 'of', 'for', 'a', 'an', 'to', 'with', 'on', 'by',
        'senior', 'sr', 'junior', 'jr', 'lead', 'staff', 'principal', 'intern', 'remote',
        'hybrid', 'onsite', 'fulltime', 'parttime'
    }
    toks1 = set(re.findall(r'[a-z0-9]+', str1)) - STOP_WORDS
    toks2 = set(re.findall(r'[a-z0-9]+', str2)) - STOP_WORDS
    if not toks1 or not toks2:
        return True
    overlap = toks1.intersection(toks2)
    union = toks1.union(toks2)
    return len(overlap) / len(union) >= 0.5


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
    Level 2: canonical apply_url (specific job opening only)
    Level 3: canonical job_url (specific job opening only)
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
        keys = []
        source = getattr(job, "source", None) or (job.raw_fields.get("source") if hasattr(job, "raw_fields") else None) or ""
        s_id = getattr(job, "source_job_id", None) or getattr(job, "job_id", None)
        if source and s_id:
            keys.append(f"sid::{source.lower()}::{s_id.lower()}")

        apply_u = getattr(job, "apply_url", None) or getattr(job, "primary_application_url", None)
        if apply_u:
            c_apply = canonicalize_url(apply_u)
            if c_apply and not is_board_or_directory_url(c_apply):
                keys.append(f"app::{c_apply}")

        job_u = getattr(job, "job_url", None) or getattr(job, "canonical_url", None) or getattr(job, "source_url", None)
        if job_u:
            c_job = canonicalize_url(job_u)
            if c_job and not is_board_or_directory_url(c_job):
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
            fp_title = generate_job_fingerprint(comp, title, "")
            keys.append(f"fpt::{fp_title}")

        # Find existing primary record if any key matches with compatible title
        matched_primary_id = None
        for k in keys:
            if k in canonical_map:
                cand_prim = canonical_map[k]
                cand_title = getattr(cand_prim, "normalized_title", None) or getattr(cand_prim, "title", "")
                if are_titles_compatible(title, cand_title):
                    matched_primary_id = cand_prim
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

            # Register missing keys to this primary
            for k in keys:
                if k not in canonical_map:
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
            keys: List[str] = []
            comp = record.fields.get("company") or record.fields.get("company_name") or ""
            title = record.fields.get("title") or record.fields.get("job_title") or ""
            loc = record.fields.get("location") or ""

            if record.identity_key and record.identity_key.strip():
                keys.append(f"ident::{record.identity_key.strip().lower()}")

            src = record.fields.get("source") or ""
            job_id = record.fields.get("job_id") or record.fields.get("source_job_id") or ""
            if src and job_id:
                keys.append(f"sid::{src.lower()}::{job_id.lower()}")

            canon_url = canonicalize_url(record.canonical_url) if record.canonical_url else ""
            if not canon_url:
                u_raw = record.fields.get("source_url") or record.fields.get("url") or ""
                if u_raw:
                    canon_url = canonicalize_url(u_raw)
            if canon_url and not is_board_or_directory_url(canon_url):
                keys.append(f"url::{canon_url}")
                keys.append(f"link::{canon_url}")

            apply_url = record.fields.get("application_url") or record.fields.get("apply_url") or record.fields.get("primary_application_url") or ""
            if apply_url:
                c_apply = canonicalize_url(apply_url)
                if c_apply and not is_board_or_directory_url(c_apply):
                    keys.append(f"app::{c_apply}")
                    keys.append(f"link::{c_apply}")

            if comp and title:
                fp = generate_job_fingerprint(comp, title, loc)
                keys.append(f"fp::{fp}")
                fp_title = generate_job_fingerprint(comp, title, "")
                keys.append(f"fpt::{fp_title}")

            if not keys:
                keys.append(f"id::{record.id}")

            # Check if any candidate key matches an existing primary record with compatible title
            primary: Optional[ExtractedRecord] = None
            matched_key: Optional[str] = None
            for k in keys:
                if k in canonical_map:
                    cand_prim = canonical_map[k]
                    cand_title = cand_prim.fields.get("title") or cand_prim.fields.get("job_title") or ""
                    if are_titles_compatible(title, cand_title):
                        primary = cand_prim
                        matched_key = k
                        break

            if primary is not None:
                record.verification_status = VerificationStatus.DUPLICATE
                duplicates.append(record)

                # Merge sources
                p_sources = list(primary.fields.get("sources", []))
                for s in primary.fields.get("all_sources", []):
                    if s and s not in p_sources:
                        p_sources.append(s)
                if primary.fields.get("source") and primary.fields["source"] not in p_sources:
                    p_sources.append(primary.fields["source"])
                for u in (primary.canonical_url, primary.fields.get("source_url"), primary.fields.get("url")):
                    if u and "://" in str(u):
                        domain_part = str(u).split("://")[-1].split("/")[0].split(".")
                        s_name = domain_part[-2] if len(domain_part) >= 2 else domain_part[0]
                        if s_name and s_name not in p_sources:
                            p_sources.append(s_name)

                r_sources = list(record.fields.get("sources", []))
                for s in record.fields.get("all_sources", []):
                    if s and s not in r_sources:
                        r_sources.append(s)
                if record.fields.get("source") and record.fields["source"] not in r_sources:
                    r_sources.append(record.fields["source"])
                for u in (record.canonical_url, record.fields.get("source_url"), record.fields.get("url")):
                    if u and "://" in str(u):
                        domain_part = str(u).split("://")[-1].split("/")[0].split(".")
                        s_name = domain_part[-2] if len(domain_part) >= 2 else domain_part[0]
                        if s_name and s_name not in r_sources:
                            r_sources.append(s_name)

                for s in r_sources:
                    if s and s not in p_sources:
                        p_sources.append(s)
                primary.fields["sources"] = p_sources
                primary.fields["all_sources"] = p_sources

                # Merge all_source_urls
                p_urls = list(primary.fields.get("all_source_urls", []))
                for u in (primary.canonical_url, primary.fields.get("source_url"), primary.fields.get("url")):
                    if u and u not in p_urls:
                        p_urls.append(u)
                r_urls = list(record.fields.get("all_source_urls", []))
                for u in (record.canonical_url, record.fields.get("source_url"), record.fields.get("url")):
                    if u and u not in r_urls:
                        r_urls.append(u)
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
                logger.info(f"Collapsed duplicate record {record.id} into canonical {primary.id} under key: {matched_key}")
                for k in keys:
                    if k not in canonical_map:
                        canonical_map[k] = primary
            else:
                # Initialize sources and URLs on primary
                init_sources = list(record.fields.get("sources", []))
                if record.fields.get("source") and record.fields["source"] not in init_sources:
                    init_sources.append(record.fields["source"])
                for u in (record.canonical_url, record.fields.get("source_url"), record.fields.get("url")):
                    if u and "://" in str(u):
                        domain_part = str(u).split("://")[-1].split("/")[0].split(".")
                        s_name = domain_part[-2] if len(domain_part) >= 2 else domain_part[0]
                        if s_name and s_name not in init_sources:
                            init_sources.append(s_name)
                record.fields["sources"] = init_sources
                record.fields["all_sources"] = init_sources

                init_urls = list(record.fields.get("all_source_urls", []))
                for u in (record.canonical_url, record.fields.get("source_url"), record.fields.get("url")):
                    if u and u not in init_urls:
                        init_urls.append(u)
                record.fields["all_source_urls"] = init_urls

                if "primary_application_url" not in record.fields:
                    record.fields["primary_application_url"] = record.fields.get("application_url") or record.canonical_url or record.fields.get("source_url")

                for k in keys:
                    canonical_map[k] = record
                clusters[record.id] = [record.id]

        # Retain unique primary instances preserving discovery order
        seen_ids = set()
        unique_records = []
        for r in canonical_map.values():
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                unique_records.append(r)
        return ToolResult(
            success=True,
            data={
                "unique_records": unique_records,
                "duplicates_count": len(duplicates),
                "clusters": clusters
            }
        )

