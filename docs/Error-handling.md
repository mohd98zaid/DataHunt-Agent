# Research AI Agent — Error Handling, Retries, and Observability

The system should fail visibly and recover conservatively. A run is successful only when its final state, records, evidence, and export are durable. A `partial` run is a valid outcome when limits or source failures prevent complete coverage.

## 1. Error envelope

Use one internal error shape across HTTP, Gemini, search, fetch, extraction, database, and export layers:

```json
{
  "code": "FETCH_TIMEOUT",
  "category": "transient",
  "retryable": true,
  "user_message": "A source timed out. The run can continue with other sources.",
  "operator_message": "GET https://example.com exceeded read timeout",
  "run_id": "run_123",
  "tool": "fetch_page",
  "attempt": 1,
  "retry_after_seconds": 4,
  "cause_id": "evt_123"
}
```

Keep `operator_message` out of end-user responses when it contains URLs, headers, stack traces, or provider details that are not needed.

## 2. Error taxonomy

| Code | Category | Retry? | Default action |
|---|---|---:|---|
| `INVALID_REQUEST` | user | no | ask for a narrower or complete request |
| `POLICY_REFUSED` | policy | no | explain allowed public-business alternative |
| `BUDGET_EXCEEDED` | budget | no | finish partial with limits |
| `MODEL_RATE_LIMITED` | transient | yes | backoff, then fallback or partial |
| `MODEL_UNAVAILABLE` | transient | yes | retry once, then configured fallback |
| `MODEL_AUTH_FAILED` | configuration | no | fail run and alert operator |
| `MODEL_SCHEMA_INVALID` | model | once | repair/reprompt once, then reject step |
| `TOOL_NOT_ALLOWED` | policy | no | block and audit |
| `SEARCH_RATE_LIMITED` | transient | yes | domain/provider backoff |
| `SEARCH_UNAVAILABLE` | transient | yes | alternate provider or partial |
| `FETCH_TIMEOUT` | transient | yes | retry once with same URL |
| `FETCH_DNS_FAILED` | transient/policy | maybe | retry only if not private/unsafe |
| `FETCH_BLOCKED` | policy/source | no | skip source and warn |
| `FETCH_TOO_LARGE` | source | no | truncate safely or skip |
| `FETCH_UNSUPPORTED_TYPE` | source | no | skip or route to approved parser |
| `PARSE_FAILED` | source | once | use alternate parser, then skip |
| `EXTRACTION_EMPTY` | quality | no | keep source warning; try another page |
| `VERIFICATION_CONFLICT` | quality | no | mark `needs_review` |
| `DUPLICATE_CONFLICT` | quality | no | preserve both and flag |
| `DB_BUSY` | transient | yes | short backoff within deadline |
| `DB_CONSTRAINT` | data | no | record event, do not corrupt run |
| `EXPORT_FAILED` | transient/system | yes | write temp file, retry, clean up |
| `CANCELLED` | user | no | stop safely and mark cancelled |
| `INTERNAL_ERROR` | system | maybe | capture incident ID and fail safely |

## 3. Retry policy

Retry only idempotent operations. Never retry a policy refusal, invalid request, unsupported source, or ambiguous verification as if it were transient.

### Backoff

```text
delay = min(max_delay, base_delay * 2^attempt) + random(0, jitter)
```

Suggested values:

| Operation | Attempts | Base | Max |
|---|---:|---:|---:|
| Gemini request | 3 total | 1 s | 20 s |
| Search call | 3 total | 1 s | 30 s |
| HTTP fetch | 2 total | 1 s | 8 s |
| SQLite busy | 4 total | 0.1 s | 2 s |
| Export write | 2 total | 0.5 s | 4 s |

Honor provider `Retry-After` when it is reasonable and within the run deadline. Retry inside the remaining run budget; a retry that would cross the deadline should become a partial result.

### Circuit breakers

Maintain a short-lived per-provider and per-domain breaker:

- Open after 5 transient failures in 60 seconds.
- Stay open for 30 seconds, then allow one probe.
- Close after a successful probe.
- Do not count policy blocks or user errors as provider health failures.

## 4. Gemini-specific handling

### Rate limits and availability

1. Record provider status, model, attempt, and request ID when available.
2. Back off with jitter.
3. If configured, switch from the primary model to a lower-cost or fallback model only for the same schema.
4. If no fallback is safe, finish the run as `partial` if verified records already exist.
5. Never silently change the model for a high-risk or sensitive extraction; record the change in run warnings.

### Invalid structured output

1. Validate the response against the schema.
2. Strip transport wrappers only when the SDK indicates the response is valid text.
3. Reprompt once with a compact validation error and the same input.
4. If it still fails, record `MODEL_SCHEMA_INVALID`, keep the source available for retry/manual review, and continue with other pages.
5. Do not use an unconstrained “fix JSON” model call repeatedly.

### Tool-call loops

The orchestrator should stop when any limit is hit:

```text
steps >= max_tool_steps
deadline <= now
searches >= max_search_queries
pages >= max_pages
records >= max_records
estimated_cost >= max_cost
```

If Gemini requests a tool that is not registered or that violates policy, return `TOOL_NOT_ALLOWED`, log the event, and end the current planning branch. Do not invent a new tool implementation.

## 5. Fetch and browser handling

- Connect timeout: 5 seconds.
- Read timeout: 20 seconds.
- Total per-page timeout: 30 seconds.
- Maximum redirects: 5.
- Maximum response bytes: 2 MB by default.
- Maximum extracted text passed to the model: a smaller bounded excerpt, for example 30,000 characters.
- Retry only network timeouts, connection resets, and selected 5xx responses.
- Do not retry 401, 403, CAPTCHA, robots/terms blocks, or policy blocks.
- If a page requires JavaScript and browser rendering is allowed, use one browser attempt; otherwise record a source limitation.
- If a parser fails, use one alternate parser and keep the raw error out of user output.

## 6. Database and crash recovery

- Use short transactions; never keep a transaction open during model or network calls.
- On startup, find runs in non-terminal states whose heartbeat is stale.
- Mark unfinished tool events as `timed_out`.
- Resume from the last durable phase, reusing cached sources and idempotency keys.
- If the same run cannot resume safely, mark it `partial` with a recovery warning rather than duplicating exports.
- Use a temporary export path and atomic rename so a crash cannot publish a half-written file.

## 7. Partial results and user-facing behavior

### Terminal states

- `completed`: requested work finished within limits; warnings may still exist.
- `partial`: useful verified results exist, but a limit, source failure, or verification gap remains.
- `failed`: no trustworthy result or a configuration/system failure prevented completion.
- `cancelled`: user or operator stopped the run.

### Response template

```text
Run status: {{completed|partial|failed|cancelled}}
Records: {{verified}} verified, {{review}} needs review, {{rejected}} rejected
Sources: {{fetched}} fetched, {{failed}} unavailable, {{blocked}} blocked
Warnings: {{top_warnings}}
Export: {{download_link_or_none}}
Coverage: {{coverage_disclaimer}}
```

Do not hide missing coverage behind a confident sentence. If a source was inaccessible, say so. If a field was not found, return null in the export.

## 8. Observability

### Structured logs

Every log event should include:

```json
{
  "ts": "2026-09-08T10:00:00Z",
  "level": "INFO",
  "event": "tool.completed",
  "task_id": "task_123",
  "run_id": "run_123",
  "tool": "fetch_page",
  "attempt": 1,
  "duration_ms": 820,
  "status": "succeeded",
  "source_domain": "example.com"
}
```

Never include API keys, cookies, full page text, raw personal contacts, or complete model prompts in normal logs.

### Metrics

Track counters and histograms for:

- runs by terminal status
- error codes by tool, provider, and domain
- retries and circuit-breaker opens
- latency p50/p95 for search, fetch, model, extraction, verification, and export
- pages, bytes, records, tool steps, and tokens per run
- cache hit rate
- required-field completeness, evidence coverage, duplicate rate, and conflict rate
- policy refusals, SSRF blocks, contact suppressions, and export downloads

### Tracing

Use a trace per run and spans for each model call, search, fetch, parse, extract, verify, dedupe, and export step. Propagate `task_id`, `run_id`, and `tool_event_id`; avoid putting raw data in span attributes.

## 9. Alerts and runbooks

Alert on:

- authentication failures or sudden model error spikes
- repeated SSRF or policy violations
- a domain’s failure rate or latency exceeding a threshold
- export failures or disk-space pressure
- database lock or migration errors
- unusual increases in records per run or contact-field extraction

First-response runbook:

1. Inspect the run and tool event IDs.
2. Determine whether the failure is provider, domain, model, policy, or local infrastructure.
3. Stop or narrow the affected provider/domain if abuse or privacy risk is possible.
4. Preserve evidence and logs needed for diagnosis, with secrets redacted.
5. Retry only after the cause and remaining budget are understood.
6. Record the incident and any configuration rollback.

## 10. Failure-injection tests

- Gemini 429, 500, timeout, auth failure, and invalid schema.
- Search provider timeout and empty result set.
- Redirect loop and redirect to a private IP.
- HTML with prompt injection, malformed encoding, huge body, and unsupported content type.
- Duplicate pages with conflicting fields.
- SQLite busy/locked database and process crash during export.
- Disk-full simulation for export temp files.
- Cancellation during fetch, extraction, and export.

Every test should assert both the terminal status and the absence of fabricated records or leaked secrets.

