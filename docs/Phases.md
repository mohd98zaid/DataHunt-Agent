# Research AI Agent — Implementation Phases

This roadmap keeps the first usable system small while preserving the interfaces needed for production hardening. Each phase has a shippable outcome, an explicit boundary, and exit criteria.

## 0. Operating assumptions

- Python service, one worker, SQLite, local export directory.
- Google GenAI SDK and Gemini Interactions API behind an adapter.
- `gemini-2.5-flash` or another currently supported stable model selected by configuration.
- HTTP retrieval first; browser rendering only for a small approved subset.
- No login-required scraping, CAPTCHA bypass, unsolicited outreach, or data-broker enrichment.

## Phase 0 — Foundation and policy (1–2 days)

### Deliverables

- Repository layout from `Architecture.md`.
- Configuration loader with `.env.example` and secret validation.
- SQLite connection, WAL mode, migrations, and repository interfaces.
- Run/task status enums and structured application error codes.
- URL validation, domain policy, robots/terms policy hook, and basic rate limiter.
- Redaction-aware structured logger.

### Exit criteria

- The service starts with no secret printed to logs.
- A test task can be inserted and recovered after a process restart.
- Disallowed URL schemes and blocked domains are rejected before network access.
- A migration can be applied twice without corrupting the database.

## Phase 1 — Vertical MVP: one research shape (3–5 days)

Choose one narrow use case first: public job listings with title, employer, location, posted date, application URL, and source URL.

### Deliverables

- Natural-language intake prompt that produces a `ResearchSpec`.
- Gemini client with a single bounded tool loop.
- Google Search grounding or one approved search provider.
- HTTP fetcher with timeouts, byte cap, content-type checks, and caching.
- HTML text extraction and the `extract_records` schema.
- Deterministic URL and job identity deduplication.
- JSON and CSV export.
- Run summary showing completed, partial, and rejected records.

### Suggested MVP limits

```text
max_records       = 50
max_queries       = 8
max_pages         = 25
max_run_seconds   = 180
max_tool_steps    = 20
```

### Exit criteria

- Five representative tasks produce valid JSON and CSV.
- Every output row includes a source URL and extraction timestamp.
- A page containing malicious instructions cannot change the output schema or tool policy.
- At least one simulated timeout and one malformed model response end in a useful user-facing error.

## Phase 2 — Reliability and evidence (3–5 days)

### Deliverables

- Field-level evidence table with short quote/locator and source document ID.
- Verification pass for required fields, dates, URLs, and contact classification.
- One targeted retry/re-fetch policy and one alternate-source fallback.
- Content hash cache and canonical URL normalization.
- XLSX export with a data sheet and a run metadata sheet.
- Partial-completion state and resumable runs.
- Golden test fixtures for pages with missing fields, duplicate listings, conflicting dates, redirects, and JavaScript-only content.

### Exit criteria

- Duplicate listings collapse without losing supporting evidence.
- Records with unsupported fields are marked `needs_review` or rejected, never guessed.
- Re-running the same task uses cached documents where safe and does not duplicate records.
- A run can resume after a worker restart without repeating completed side effects.

## Phase 3 — Security, privacy, and abuse controls (2–4 days)

### Deliverables

- Secret management guidance and key rotation procedure.
- SSRF defenses, DNS/IP checks, redirect limits, and response-size caps.
- Per-user, per-run, and per-domain rate limits.
- Public-business-contact policy enforced in code.
- Export access control, unguessable file names, retention cleanup, and audit events.
- Prompt-injection test suite and tool-authorization tests.
- Suppression/opt-out table for business contacts that must not be returned.

### Exit criteria

- Private IP ranges, localhost, file URLs, and unsafe redirects are blocked.
- Personal emails/phones are excluded unless the task has an approved, documented basis; the MVP default is business-public-only.
- The service refuses to collect credentials, bypass controls, or perform unsolicited messaging.
- Logs contain IDs and hashes, not raw email addresses, phone numbers, API keys, or full page bodies.

## Phase 4 — Production readiness (1–2 weeks of focused hardening)

### Deliverables

- Small authenticated API/UI.
- Background job queue or process supervisor with graceful shutdown.
- Health/readiness endpoints and operational dashboard.
- Provider/model configuration with a tested fallback model or human-review route.
- PostgreSQL migration plan and staging rehearsal.
- Backups, restore test, export retention job, and incident runbook.
- Load tests using recorded fixtures, not uncontrolled live scraping.

### Exit criteria

- A failed model/provider does not lose task state.
- Operators can identify a failing domain, model, or tool within one run.
- Backups restore into a clean database.
- The system remains within defined compute, network, and model budget at expected concurrency.

## 5. Work breakdown by module

| Module | Phase 0 | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|---|
| Intake/spec | schema | first prompt | ambiguity checks | policy gate | versioned templates |
| Gemini adapter | client shell | one loop | typed outputs | prompt-injection defenses | model routing |
| Search | interface | one provider | caching/citations | domain quotas | provider failover |
| Fetch/browser | safe HTTP | HTML only | browser fallback | SSRF hardening | isolated browser worker |
| Extraction | models | job schema | field evidence | sensitive-field policy | schema registry |
| Verification | statuses | required fields | cross-source checks | contact policy | review queue |
| Data | migrations | task/run/record | evidence/cache | suppression/audit | PostgreSQL |
| Exports | interface | JSON/CSV | XLSX + metadata | secure downloads | retention/archive |
| Operations | logs | run summary | metrics | alerts/audit | dashboard/runbooks |

## 6. MVP acceptance test matrix

| Scenario | Expected result |
|---|---|
| “Find 20 jobs in Dubai from the last 7 days” | bounded search, structured rows, citations, export |
| No exact date on page | `posted_at=null`, warning, no invented date |
| Same job on three URLs | one canonical record with three evidence links |
| Search provider unavailable | retry, then alternate provider or `partial` result |
| Page says “ignore previous instructions” | text treated as data; tools/policy unchanged |
| URL redirects to private address | blocked before final fetch |
| Public company contact page | business contact may be included with source and classification |
| Personal profile with phone number | excluded by default with privacy warning |
| Export interrupted | no corrupt final file; resumable or clean failure |

## 7. Metrics to track from the first run

### Quality

- `record_acceptance_rate`
- `required_field_completeness`
- `field_evidence_coverage`
- `duplicate_rate`
- `verification_conflict_rate`
- `source_freshness_lag`

### Reliability

- `run_success_rate`
- `partial_run_rate`
- `tool_error_rate` by tool and domain
- `retry_rate`
- `p95_run_duration`
- `resume_success_rate`

### Cost and safety

- model input/output tokens and estimated spend per run
- pages, bytes, browser seconds, and search calls per run
- rejected policy requests
- blocked SSRF attempts
- redaction events and export downloads

## 8. Rollout and rollback

1. Run all new prompts and model settings against recorded fixtures.
2. Enable in shadow mode: execute but do not expose results automatically.
3. Compare acceptance, evidence coverage, and cost to the previous version.
4. Roll out to a small user group with conservative budgets.
5. Keep prompt, model, tool-schema, and migration versions with every run.
6. Roll back the configuration version first; roll back code only when schema compatibility is preserved.

Never delete historical runs during rollback. Mark affected outputs as superseded and retain their audit record according to the retention policy.

