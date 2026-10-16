# DataHunt-Agent — Comprehensive Job Discovery & Collection Audit Report

## 1. Executive Summary

A comprehensive end-to-end audit of the DataHunt-Agent job collection and discovery pipeline was conducted across all system tiers: query normalization, search query formulation, source discovery and registry, HTTP page retrieval, ATS JSON API ingestion, LLM/heuristic extraction, deduplication, policy-based qualification (title, geo, experience, skills, freshness), database persistence, FastAPI REST endpoints, and the frontend UI/Kanban views.

The audit revealed that incomplete job discovery was not caused by external source blockage alone, but primarily by **artificial internal bottlenecks and silent slicing constraints** distributed across the engine. Specifically, hardcoded slices (`[:2]`, `[:3]`, `[:50]`, `limit=500`) truncated candidate pools and discarded entire geographic sub-regions (e.g., cutting Saudi Arabia entirely from ATS queries during multi-location "UAE OR Saudi Arabia" searches).

---

## 2. Inventory of Bottlenecks, Slices, and Root Causes

### 2.1 Geographic and ATS Query Slicing (`datahunt/agent/discovery_engine.py`)
- **Root Cause 1 (ATS Location Slicing)**: In `DiscoveryEngine.generate_initial_tasks` (lines 226–228), the engine constructed ATS queries using:
  ```python
  ats_hubs = target_locations[:2] if target_locations else ([primary_loc] if primary_loc else [""])
  ```
  In a dual-region search (e.g. `UAE OR Saudi Arabia`), `target_locations` is populated as `["Dubai", "Abu Dhabi", "Sharjah", "Riyadh", "Jeddah", "UAE", "Saudi Arabia"]`. Slicing `[:2]` restricted all Greenhouse, Lever, Ashby, and Workable queries exclusively to `["Dubai", "Abu Dhabi"]`. Riyadh, Jeddah, Dammam, and Saudi Arabia were **completely eliminated from ATS discovery**.
- **Root Cause 2 (Web Sweep Slicing)**: In line 308, the direct in-depth web sweep used:
  ```python
  sweep_locs = target_locations[:3] if target_locations else ([loc_str] if loc_str else [""])
  ```
  This again dropped all Saudi Arabia locations from general web searches.
- **Root Cause 3 (Regional Board Single-Hub Slicing)**: In lines 132–135, regional job boards (Naukrigulf, Bayt, Gulftalent) were queried using only `primary_loc` (`target_locations[0]`, which was "Dubai"). In multi-region searches, neither Saudi Arabia nor Riyadh was ever queried on these regional portals.
- **Root Cause 4 (Missing India Regional Expansion)**: In `discovery_engine.py`, while Gulf locations were parsed and mapped to regional boards, India searches did not expand into primary tech hubs (Bengaluru, Hyderabad, Pune, Gurgaon/Noida, Mumbai) nor trigger Indian job portals (Naukri.com, Instahyre, Foundit.in) in the discovery task queue.

### 2.2 Task Controller Bottlenecks (`datahunt/agent/task_controller.py`)
- **Root Cause 5 (Search Query Cap)**: Line 95 hardcoded:
  ```python
  queries_to_run = source_plan.search_queries[:3] or [query]
  ```
  This silently dropped all planned search queries beyond the first three, regardless of available search budget or time allowance.
- **Root Cause 6 (Hits per Query Cap)**: Line 100 passed `max_results=5` to `search_tool.execute()`, arbitrarily capping discovery to at most 5 results per query.
- **Root Cause 7 (Gap-Filling Truncation)**: Line 200 sliced missing coverage gaps to `missing_gaps[:2]`, and line 207 executed gap searches with `max_results=2`.

### 2.3 ATS Ingestion Truncation (`datahunt/tools/fetch.py`)
- **Root Cause 8 (Greenhouse 50-Job Truncation)**: In `fetch_greenhouse_jobs_api()` (line 192), the JSON API parser iterated with:
  ```python
  for job in jobs[:50]:
  ```
  For large enterprise job boards (which often list 100–500 open roles), any role beyond index 49 was dropped without evaluation.

### 2.4 Freshness and Date Filtering Inconsistencies (`datahunt/agent/policies.py`)
- **Root Cause 9 (Default Stale Threshold Discarding All-Time Jobs)**: In `policies.py` lines 793–811, `max_freshness_days` defaulted to `getattr(job_req, "freshness_days", None) or 60`. In an "all-time" or unconstrained search, active postings older than 60 days (or those bearing previous year timestamps like 2025) were disqualified as `stale_job`.
- **Root Cause 10 (Strict 24h Ambiguity)**: For 24h / 0sec queries, timestamps with unknown recency must be explicitly separated from verified 24h postings rather than ambiguously counted or dropped without distinction.

### 2.5 Experience Qualification on Ambiguity (`datahunt/agent/policies.py` & `datahunt/tools/extract.py`)
- **Root Cause 11 (Unstated Experience and Entry-Level Vocabulary)**:
  - When job descriptions or search snippets omitted experience requirements, the engine needed an explicit unverified policy so valid candidates are retained and marked `experience_verified=False`.
  - In `extract.py`, regex patterns looked only for digit ranges (`\d+-\d+ years`) and missed common natural language terms such as "Entry Level", "Fresher", "Junior", and "Intern", which should extract to `0-2 years`.
  - For range `0-6 years`, boundary conditions such as "3+ years" or "at least 5 years" must qualify (as 3 <= 6 and 5 <= 6), while "7+ years" or "8-10 years" must disqualify (min 7 > 6).

### 2.6 API & UI Pagination Limitations (`datahunt/api.py`, `datahunt/db/repositories.py`, `frontend/js/jobs.js`)
- **Root Cause 12 (Hard Limit in API)**: In `datahunt/api.py`, `list_jobs` specified `limit: int = Query(200, ge=1, le=500)` without an `offset` parameter or total count headers. Any caller or UI attempting to load more than 500 jobs received an HTTP 422 error.
- **Root Cause 13 (UI Query Hardcoded Limit)**: In `frontend/js/jobs.js`, `loadJobs()` queried `/api/jobs?limit=500`. Once 500 jobs were loaded, the table truncated display and gave the user no pagination mechanism to view records 501+.

---

## 3. Systematic Architecture & Implementation Plan

1. **Discovery Engine (`datahunt/agent/discovery_engine.py`)**:
   - Remove `ats_hubs = target_locations[:2]`. For multi-location requests (e.g. `UAE OR Saudi Arabia`), ensure hubs from all requested jurisdictions (Dubai, Abu Dhabi, Riyadh, Jeddah, Saudi Arabia, UAE) are included in ATS search tasks up to the configured budget.
   - Remove `sweep_locs = target_locations[:3]`. Include representative hubs across all target countries.
   - For regional boards (Naukrigulf, Bayt, Gulftalent), generate queries for both UAE and Saudi hubs when both are requested.
   - Add India hub expansion: when India is specified, expand to Bengaluru, Hyderabad, Pune, Gurgaon, Noida, Mumbai, and India; target Indian job portals (Naukri.com, Instahyre, Foundit.in) and Indian ATS queries without triggering Gulf-specific portals.

2. **Task Controller (`datahunt/agent/task_controller.py`)**:
   - Remove `search_queries[:3]` slice. Execute planned queries bounded by discovery budget and run deadline.
   - Increase default per-query harvest `max_results` from 5 to 20+.
   - Remove `missing_gaps[:2]` slice; iterate over gaps within the allocated time window.

3. **Fetch & ATS Ingestion (`datahunt/tools/fetch.py`)**:
   - Remove `jobs[:50]` cap in `fetch_greenhouse_jobs_api`. Process all returned jobs from public board APIs up to `MAX_EXTRACTED_CHARS`.

4. **Qualification Policies (`datahunt/agent/policies.py` & `datahunt/tools/extract.py`)**:
   - In `policies.py`, recognize "All-Time" searches (`freshness_days` unset, 0, or "all time" in query) and bypass arbitrary stale cutoffs.
   - For 24h / 0sec searches, enforce strict timestamp checking ($\le 86400$ seconds or relative today/hours-ago markers).
   - In `extract.py`, support "entry level", "fresher", "junior", "graduate" mapping to 0–2 years.
   - Preserve unverified experience listings as qualified candidates with `experience_verified=False` / `MatchStatus.UNKNOWN` rather than dropping them.

5. **API & Database Pagination (`datahunt/api.py`, `datahunt/db/repositories.py`, `frontend/js/jobs.js`)**:
   - In `repositories.py`, add `offset: int = 0` and total count support to `list_jobs()`.
   - In `datahunt/api.py`, allow `limit` up to 5000, add `offset: int = Query(0, ge=0)`, and expose `X-Total-Count` header.
   - In `frontend/js/jobs.js`, allow loading all available jobs or paginating dynamically.

6. **Comprehensive Test Suite (`tests/test_comprehensive_job_discovery.py`)**:
   - Automated verification covering Scenarios A through G and all 26 verification points specified in the directive.
