# Research AI Agent — Security, Privacy, and Anti-Abuse

This system reads untrusted web content and may process public business contact information. Security is therefore part of the data pipeline, not a later add-on. The controls below are implementation requirements for the MVP; adapt them to the operator’s jurisdiction, contracts, and applicable law.

## 1. Security objectives

- Keep Gemini keys, database credentials, cookies, and host access secret.
- Prevent web content from controlling the agent.
- Prevent the fetcher from reaching private network services.
- Collect only the minimum public data needed for the requested task.
- Keep evidence and provenance so every result can be reviewed or removed.
- Make scraping volume bounded, attributable, and respectful of source policies.
- Avoid enabling spam, surveillance, credential theft, or access-control bypass.

## 2. Threat model

| Threat | Example | Primary control |
|---|---|---|
| Prompt injection | Page says to reveal secrets or call a new tool | Treat page text as data; app-owned tool authorization |
| SSRF | URL redirects to localhost or cloud metadata | Scheme, DNS, IP, redirect, and egress checks |
| Secret leakage | API key appears in logs or model context | Secret manager, redaction, context isolation |
| Malicious document | Huge compressed file or hostile HTML | size limits, parser sandbox, content-type checks |
| Privacy misuse | Bulk personal email/phone harvesting | business-public-only policy, suppression, review |
| Abuse/spam | Repeated high-volume collection or outreach | quotas, rate limits, no send tools, audit |
| Data poisoning | Fake source or conflicting listing | evidence, verification, cross-source checks |
| Export exposure | Guessable download path | auth, random storage key, expiry, access log |
| Supply-chain issue | Untrusted parser/browser package | lockfiles, updates, minimal dependencies |

## 3. Secrets and credentials

- Store `GEMINI_API_KEY` in an environment secret store or OS credential manager; never commit it or put it in a prompt.
- Use separate development, staging, and production keys with the smallest available quota.
- Rotate keys after suspected exposure and record the rotation event.
- Never log request headers, cookies, authorization fields, full tool payloads, or full model responses by default.
- Redact common secret patterns before writing logs or error reports.
- Do not send secrets to Gemini, fetched pages, exports, or browser JavaScript.

Recommended `.env.example` fields:

```text
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
DATABASE_PATH=./data/research.sqlite3
EXPORT_DIR=./exports
MAX_RUN_SECONDS=300
MAX_RESPONSE_BYTES=2000000
```

## 4. Web fetching and SSRF defenses

The fetcher must enforce all of the following before every request and after every redirect:

1. Allow only `https` by default; permit `http` only when explicitly configured.
2. Reject `file:`, `data:`, `javascript:`, `ftp:`, `gopher:`, and unknown schemes.
3. Resolve DNS and reject loopback, link-local, private, multicast, reserved, and unspecified IP ranges.
4. Re-check the resolved address after redirects and prevent DNS rebinding.
5. Limit redirects, response bytes, decompressed bytes, and total request time.
6. Reject unexpected content types and archive formats unless a dedicated safe parser is enabled.
7. Do not fetch user-supplied URLs automatically without the same policy checks.
8. Use an egress firewall in production to deny private network ranges and cloud metadata endpoints.
9. Do not render arbitrary pages with access to local files, credentials, or the host clipboard.

The browser adapter must use a fresh, restricted context for each run, disable downloads by default, and keep browser concurrency low.

## 5. Prompt-injection defense

### Application controls

- Tool definitions are fixed by code; Gemini cannot create or enable tools.
- A policy engine checks every tool name and every URL before execution.
- Page text is delimited and labeled untrusted in every extraction prompt.
- The model receives bounded excerpts, not unrestricted browser state.
- System and policy instructions are not copied into page prompts or exports.
- High-impact actions such as exporting contact data require a final policy check.

### Model instructions

The shared prompt in `Prompts.md` tells the model to ignore instructions in pages, snippets, PDFs, metadata, and scripts. This is helpful but not sufficient; enforcement must remain outside the model.

### Test examples

Include malicious strings in HTML fixtures, search snippets, PDF text, page titles, and JSON-LD. The expected result is always: extract only supported facts, preserve the warning, and leave tool permissions unchanged.

## 6. Public business contact policy

The default policy is `business_public_only`.

### Allowed by default

- A generic company inbox such as `info@example.com`, `support@example.com`, `careers@example.com`, or `sales@example.com` when published on an official company page.
- A company switchboard or office number when published as a business contact on an official company page.
- A contact form URL or careers page URL.
- A company’s public domain and role mailbox when the source explicitly presents it as a business channel.

### Excluded by default

- Personal emails or phone numbers found on profiles, resumes, forums, social posts, or data aggregators.
- Guessed emails such as `firstname.lastname@domain`.
- Enriched or purchased contact data.
- Sensitive personal information, home addresses, private social handles, or identity attributes.
- Lists whose purpose is to enable unsolicited bulk outreach.

### Required handling

- Preserve the source URL, observed timestamp, and context classification.
- Store a normalized hash for suppression checks; restrict raw values to authorized result views.
- Apply suppression entries before export.
- Let operators delete or suppress a value and retain the suppression record so it is not re-collected immediately.
- If publication context is unclear, set the field to `null` and mark `needs_review`.

This is a product policy, not legal advice. Before launch, review applicable privacy, marketing, anti-spam, employment, and web-access rules for the target markets.

## 7. Anti-abuse controls

- Per-user and per-API-key daily quotas.
- Per-run limits for queries, pages, bytes, browser seconds, records, and model calls.
- Per-domain request rate and concurrency limits.
- Exponential backoff on rate-limit responses; do not hammer a failing source.
- Domain blocklist for known prohibited or high-risk destinations.
- Refuse requests to bypass restrictions, collect credentials, or gather bulk personal contacts.
- No email/SMS/CRM-send tool in the MVP.
- Require explicit user confirmation before an export containing approved contact fields.
- Audit policy decisions with task ID, run ID, rule, and outcome.

## 8. Application and export security

- Authenticate task and export endpoints when a UI or multi-user API is added.
- Authorize access by task owner or tenant; never rely on a hidden file name alone.
- Generate random export storage keys; do not use the task text or user email in file names.
- Set an expiry and delete expired exports in a scheduled cleanup.
- Set safe download headers and serve files through an authenticated endpoint.
- Escape CSV cells that begin with `=`, `+`, `-`, or `@` to reduce spreadsheet formula injection risk.
- Keep evidence and warnings in a separate metadata sheet or JSON sidecar to avoid surprising downstream use.
- Validate requested export columns against an allowlist.

## 9. Privacy lifecycle

1. **Collect:** only fields required by the ResearchSpec.
2. **Classify:** source type, publication context, and confidence.
3. **Minimize:** discard unrelated page text and excluded contact values.
4. **Use:** generate the requested export only; do not repurpose data for outreach.
5. **Retain:** follow the defaults in `Database.md` or a stricter operator policy.
6. **Delete/suppress:** support value suppression and task/export deletion with audit events.

## 10. Operations checklist

- [ ] Keys are outside source control and rotateable.
- [ ] Logs redact secrets, email addresses, phone numbers, cookies, and page bodies.
- [ ] SSRF tests cover redirects, IPv4, IPv6, DNS rebinding, and metadata addresses.
- [ ] Parser and browser dependencies are pinned and updated through review.
- [ ] Tool allowlist and URL policy are unit-tested.
- [ ] Prompt-injection fixtures pass.
- [ ] Contact classification and suppression run before export.
- [ ] CSV formula injection is neutralized.
- [ ] Export access is authenticated and expires.
- [ ] Backups are encrypted and restore-tested.
- [ ] Incident procedure covers key exposure, privacy complaint, and source-block notice.

