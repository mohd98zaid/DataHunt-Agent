# Research AI Agent — Architecture

Status: implementation-ready baseline
Date: 2026-09-08

## 1. Purpose and constraints

This project is a general-purpose research agent that accepts a natural-language task, researches public web sources, extracts structured records, verifies important fields, deduplicates results, and exports CSV, Excel, or JSON.

The initial design assumes one small server or workstation, a single SQLite database, one worker process, and bounded concurrency. It is intentionally modular so the web/search adapters and database can be replaced later without changing the user-facing task format.

### In scope

- Research tasks such as “find AI/ML jobs in Dubai posted in the last seven days.”
- Public web search, page retrieval, browser-assisted pages, and extraction from accessible HTML.
- Public business contact details when they are clearly published for business contact purposes.
- Evidence-backed structured records with source URLs, observed timestamps, and confidence.
- CSV, XLSX, and JSON export.

### Out of scope for the MVP

- Login-protected or paywalled collection.
- CAPTCHA solving, anti-bot bypass, stealth scraping, or proxy rotation intended to evade controls.
- Bulk personal-contact harvesting, data-broker enrichment, or unsolicited outreach.
- Autonomous purchases, account creation, message sending, or other external side effects.
- “Find everything” guarantees. The agent must report scope, limits, and partial coverage.

## 2. Design principles

1. **Plan before browsing.** Convert the request into a typed research specification with scope, fields, limits, freshness, and source policy.
2. **Evidence is a first-class value.** Every extracted field should point to a source document and, when possible, a quote or locator.
3. **The model decides; tools execute.** Gemini plans and interprets tool results. Network access, parsing, validation, deduplication, and exports are deterministic application code.
4. **Bound every loop.** Enforce a maximum wall-clock time, tool calls, pages, bytes, records, and estimated model spend.
5. **Treat web content as hostile input.** Page text is data, not instructions. Tool permissions are enforced outside the model.
6. **Prefer useful partial results.** A run can finish with `partial` status when some sources fail, provided limitations are visible.
7. **Keep the model configurable.** The selected Gemini model is an environment setting, not a hard-coded business rule.

## 3. Logical architecture

```text
User / CLI / small web UI
          |
          v
Task API -> Intake + Policy Gate -> Run Orchestrator
                                      |
                         +------------+------------+
                         |                         |
                         v                         v
                  Gemini agent layer       Budget / state store
                 (Interactions API)             (SQLite)
                         |
        +----------------+----------------+
        |                |                |
        v                v                v
   Search adapter   Fetch/browser      Extraction + validation
  (Google Search    adapter             (HTML/text/PDF)
   grounding or     (httpx first;      (schema + evidence)
   provider)        Playwright only
                    when approved)
        \                |                /
         +---------------+---------------+
                         v
                 Verification + dedupe
                         |
                         v
                 Exporter + audit log
                 CSV / XLSX / JSON
```

The application should use a thin orchestration loop rather than a large multi-agent swarm. Separate prompt roles are useful for planning, extraction, and verification, but they can run through the same Gemini client and worker.

## 4. Components and responsibilities

### 4.1 Task API and intake

Accept a text request and optional controls:

```json
{
  "request": "Find 50 AI/ML jobs in Dubai posted in the last 7 days",
  "max_records": 50,
  "freshness_days": 7,
  "allowed_domains": [],
  "blocked_domains": [],
  "requested_fields": ["title", "company", "location", "posted_at", "application_url"],
  "contact_policy": "business_public_only",
  "output_format": "json"
}
```

The intake layer normalizes dates, rejects impossible or unsafe requests, applies account-level limits, and creates a `research_task` before any network request is made.

### 4.2 Run orchestrator

The orchestrator owns state transitions and limits. It should be deterministic and resumable:

```text
accepted -> planned -> searching -> collecting -> extracting
         -> verifying -> deduplicating -> exporting -> completed
                      \-> partial / failed / cancelled
```

The orchestrator, not Gemini, decides whether a tool call is allowed. A tool call is accepted only when it is in the allowlist, within budget, and consistent with the task policy.

### 4.3 Gemini agent layer

Use the Google GenAI SDK and the Gemini Interactions API as the primary model interface for new builds. Keep a narrow adapter in `llm/gemini_client.py` so a future model or endpoint change affects one module.

Recommended configuration:

```text
GEMINI_MODEL=gemini-2.5-flash
GEMINI_API_KEY=<secret>
GEMINI_MAX_OUTPUT_TOKENS=4096
GEMINI_TEMPERATURE=0.1
```

Use a stable, cost-efficient model for planning, extraction, and most verification. Route only difficult or ambiguous cases to a stronger configurable model. Do not rely on a model name embedded in prompts; validate it against the model catalog during deployment.

Use built-in Google Search grounding for broad current-web discovery when it meets the task’s citation and coverage needs. Use custom function tools for application-owned operations such as fetching an approved URL, extracting a page, checking a record, deduplicating, and exporting. Use structured output for planner, extractor, verifier, and exporter results.

The model must never receive the Gemini API key or database credentials. The application owns tool execution and returns only bounded, sanitized tool results.

### 4.4 Search adapter

Responsibilities:

- Run bounded queries derived from the research plan.
- Return result URL, title, snippet, source type, query, and retrieval timestamp.
- Enforce allowed/blocked domains and per-domain quotas.
- Cache identical queries within a run.
- Preserve provider citations or grounding metadata where available.

The adapter can start with Gemini Google Search grounding. Keep a provider interface so a permitted search API can be added later:

```python
class SearchProvider(Protocol):
    def search(self, query: str, *, limit: int, policy: SearchPolicy) -> list[SearchHit]: ...
```

### 4.5 Fetch and browser adapter

Use a normal HTTP client first. Send a descriptive user agent, honor robots.txt and provider terms where applicable, enforce connect/read/total timeouts, cap response size, and reject non-HTTP(S) schemes. Store only the minimum source snapshot or extracted text needed for reproducibility.

Use a real browser only for approved public pages that require JavaScript rendering. Keep browser concurrency at one in the limited-compute deployment. Never use the browser to defeat authentication, CAPTCHA, paywalls, or technical blocks.

### 4.6 Extraction and verification

The extraction stage converts a source document into records that conform to a task-specific schema. It must return `null` for absent fields and must not infer facts that are not supported by the source.

The verification stage checks:

- Required fields and types.
- Source URL and evidence presence.
- Freshness/date rules.
- Cross-source agreement for high-value fields.
- Contact-policy classification.
- Duplicate identity and canonical URL.

Verification may trigger one targeted re-fetch or one alternate-source lookup. It should not start an unbounded research loop.

### 4.7 Deduplication

Use deterministic keys before model-assisted similarity:

1. Canonical URL.
2. Normalized external ID or job ID.
3. Normalized `(company, title, location, posted_date)`.
4. Conservative text similarity only when the above are unavailable.

Keep the winning record and link all supporting evidence. Never silently merge records with conflicting employers or locations; mark them for review.

### 4.8 Exporter

Generate exports from verified database records, never directly from model output. Include a metadata sheet or sidecar JSON containing run ID, query, retrieval time, source count, limits, and warnings. CSV fields must have stable headers. XLSX exports should be generated in streaming/write-only mode when available.

## 5. Tool contracts

All tool inputs and outputs are JSON-schema validated. The model receives descriptions and small result payloads; large page bodies stay in the application.

### `search_web`

Input: `query`, `limit`, `freshness_days`, `allowed_domains`, `blocked_domains`.

Output: `hits[]` with `url`, `title`, `snippet`, `source_domain`, `retrieved_at`, and optional `provider_citation_id`.

### `fetch_page`

Input: `url`, `render_js` (boolean), `max_bytes`.

Output: `document_id`, `final_url`, `status_code`, `content_type`, `title`, `text_excerpt`, `retrieved_at`, `truncated`, `policy_flags`.

### `extract_records`

Input: `document_id`, `record_schema`, `field_rules`.

Output: `records[]`, where each record contains fields, `field_evidence[]`, `source_document_id`, and `extraction_warnings[]`.

### `verify_record`

Input: `record_id`, `required_fields`, `freshness_rule`, `contact_policy`.

Output: `status` (`verified`, `needs_review`, or `rejected`), field checks, conflicts, and next action.

### `deduplicate_records`

Input: `record_ids`, `identity_rules`.

Output: clusters, canonical record IDs, merged evidence IDs, and unresolved conflicts.

### `export_results`

Input: `run_id`, `format`, `columns`, `include_evidence`, `destination`.

Output: `export_id`, `file_name`, `row_count`, `warnings`, and checksum.

## 6. Research run sequence

1. Parse the user request into a `ResearchSpec`.
2. Show or store the interpreted scope: topic, geography, date window, fields, max records, sources, and contact policy.
3. Plan a small set of search queries and source types.
4. Search and rank candidate URLs.
5. Fetch candidates within per-domain and global budgets.
6. Extract records with a schema and field-level evidence.
7. Verify required fields and targeted high-risk fields.
8. Deduplicate and preserve all supporting evidence.
9. Export the requested format with provenance and warnings.
10. Return a concise summary: counts, coverage, limitations, and output link.

## 7. Limited-compute defaults

```text
max_run_seconds       = 300
max_search_queries    = 12
max_pages_fetched     = 40
max_browser_pages     = 8
max_response_bytes    = 2_000_000
max_records           = 250
max_parallel_fetches  = 4
max_browser_workers   = 1
max_llm_tool_steps    = 30
```

Start with one worker and a SQLite connection in WAL mode. Use a small cache keyed by normalized URL and content hash. Move to a queue and multiple workers only after metrics show that the single-worker design is the bottleneck.

## 8. Deployment layout

```text
research-agent/
  app/
    api.py
    orchestrator.py
    policy.py
    llm/gemini_client.py
    tools/{search,fetch,browser,extract,verify,dedupe,export}.py
    models/{task,run,record}.py
    db/{connection,migrations,repositories}.py
  tests/
  migrations/
  exports/
  .env.example
```

Run the API and worker in the same process for the MVP. Store exports outside the database with unguessable names and a short retention period. If a UI is added, serve downloads through an authenticated endpoint rather than exposing the export directory.

## 9. Definition of done for the architecture

- A task can be created, resumed, cancelled, and exported.
- Every record has source provenance and an explicit verification status.
- All network and model calls are bounded and observable.
- A prompt injection in a web page cannot grant a new tool or change policy.
- A failed source yields a warning or partial run, not a fabricated record.
- SQLite can be replaced by PostgreSQL through the repository interface and migrations described in `Database.md`.

## 10. External API notes

Review these links during implementation because model names, SDK behavior, and endpoint recommendations change:

- [Gemini API overview](https://ai.google.dev/gemini-api/docs)
- [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview)
- [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search)
- [Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Models](https://ai.google.dev/gemini-api/docs/models)

