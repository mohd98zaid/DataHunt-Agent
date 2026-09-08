# DataHunt — Evidence-Backed Autonomous Research AI Agent

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-20%20Passed-brightgreen.svg)]()
[![Database](https://img.shields.io/badge/Storage-SQLite%20WAL-orange.svg)]()
[![AI](https://img.shields.io/badge/Powered%20By-Google%20Gemini-4285F4.svg)]()

**DataHunt** is an evidence-first autonomous research AI agent built for high reliability, strict defensive security, and structured data extraction.

Given a natural-language research request (e.g., *"Find 20 AI/ML engineer openings in Dubai posted in the last 7 days"*), DataHunt plans search strategies, traverses public web pages, extracts structured records, enforces field-level evidence verification with quotes and locators, safely deduplicates records, and exports clean JSON, CSV, and Excel datasets.

---

## Architecture & Pipeline

```text
User Request (CLI / API)
          │
          ▼
┌──────────────────────────────────────┐
│  Intake & Security Gate              │  ◄── Enforces SSRF, domain & budget policies
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│  Research Orchestrator               │  ◄── Bounded execution loop & state transitions
└──────┬───────────────────────┬───────┘
       │                       │
       ▼                       ▼
┌──────────────┐       ┌──────────────┐
│ Gemini Model │       │ SQLite (WAL) │  ◄── Durable audit logs, runs & task storage
│ (Plan/Extract│       └──────────────┘
└──────┬───────┘
       │
 ┌─────┴──────────────────┐
 │ Tools & Adapters       │
 │ • Google Search API    │  ◄── Multi-query search grounding
 │ • Safe HTTP Fetcher    │  ◄── DNS pre-resolution, SSRF block, timeout & byte caps
 │ • Document Parser      │  ◄── Clean text extraction, script/style sanitization
 └─────┬──────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  Field-Level Verification Engine     │  ◄── Validates quotes & locators against source docs
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│  Deterministic Deduplication Engine  │  ◄── Canonical URL & field-level evidence merging
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│  Safe Exporter (JSON / CSV / XLSX)   │  ◄── Formula injection escaping & SHA-256 integrity
└──────────────────────────────────────┘
```

---

## Key Features & Security Defenses

### 1. Evidence-First Integrity
- Every non-null field in an extracted record is mapped directly to a verified source document.
- Retains exact quotations, character/DOM locators, and retrieval timestamps.
- Unsupported or hallucinated claims fail verification and are rejected or quarantined.

### 2. Deep SSRF & Network Protections
- **Pre-Resolution DNS Checks**: Inspects resolved IP addresses before establishing TCP connections.
- **Strict IP Blocking**: Denies loopback (`127.0.0.0/8`), private RFC 1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`), Carrier-Grade NAT (`100.64.0.0/10`), IPv6 equivalents, and cloud metadata services (`169.254.169.254`).
- **Redirect Guards**: Validates target IPs across HTTP redirects, defeating DNS rebinding attacks.

### 3. Prompt-Injection Containment
- Treats external web pages strictly as untrusted data inside delimited payloads.
- Model cannot register, alter, or invoke arbitrary tools; tool execution is strictly regulated by application-level policy code.

### 4. Business Contact Policy (`business_public_only`)
- Strictly collects public, official business inboxes (`info@`, `careers@`, `sales@`, etc.) displayed on corporate domains.
- Automatically redacts personal email addresses and telephone numbers.

### 5. CSV / Spreadsheet Formula Injection Shield
- Sanitizes CSV/Excel cells starting with `=`, `+`, `-`, `@`, `\t`, or `\r` by escaping them with single-quotes, preventing malicious formula execution when opened in Excel or Google Sheets.

### 6. Durable WAL Storage & Idempotent Migrations
- SQLite database configured with Write-Ahead Logging (`PRAGMA journal_mode = WAL;`) and `PRAGMA busy_timeout = 5000;`.
- Idempotent schema migrations track applied changes and guarantee safe restarts.

### 7. Unified Error Taxonomy & Exponential Jitter Backoff
- Typed exception hierarchy (`DataHuntError`, `PolicyViolationError`, `FetchError`, `BudgetExceededError`, etc.).
- Exponential backoff with decorrelated jitter avoids thundering herds when querying external endpoints.

---

## Repository Structure

```text
DataHunt-Agent/
├── datahunt/
│   ├── __init__.py          # Package metadata & version
│   ├── cli.py               # Rich CLI command suite
│   ├── config.py            # Typed settings & environment loading
│   ├── errors.py            # Error taxonomy, envelope & backoff
│   ├── logger.py            # Structured logging & secret masking
│   ├── orchestrator.py      # Core research workflow orchestrator
│   ├── policy.py            # Security policies & SSRF guards
│   ├── db/                  # SQLite schema, WAL connection & repositories
│   ├── llm/                 # Gemini API interaction layer
│   ├── models/              # Pydantic schemas (Task, Run, Record, Evidence)
│   └── tools/               # Search, safe fetch, and export tools
├── migrations/
│   └── 001_initial_schema.sql  # Idempotent SQLite schema
├── tests/                   # 20+ unit and integration tests
├── .env.example             # Template environment variables
├── .gitignore               # Configured to ignore caches, secrets, & internal docs
├── LICENSE                  # Apache 2.0 License
├── requirements.txt         # Core dependencies
└── README.md                # Project documentation
```

---

## Quick Start

### 1. Prerequisites

- Python 3.10, 3.11, 3.12, 3.13, or 3.14
- Git

### 2. Clone the Repository

```bash
git clone https://github.com/mohd98zaid/DataHunt-Agent.git
cd DataHunt-Agent
```

### 3. Set Up Virtual Environment

```bash
# On Linux/macOS:
python -m venv .venv
source .venv/bin/activate

# On Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure Environment Variables

Copy `.env.example` to `.env` and configure your settings:

```bash
cp .env.example .env
```

Edit `.env`:
```env
# Google Gemini API Key
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# Database & Export paths
DATABASE_PATH=./data/datahunt.sqlite3
EXPORT_DIR=./exports

# Resource & Budget Limits
MAX_RUN_SECONDS=300
MAX_SEARCH_QUERIES=12
MAX_PAGES_FETCHED=40
MAX_RESPONSE_BYTES=2000000
MAX_RECORDS=250

LOG_LEVEL=INFO
```

### 6. Initialize the Database

Run idempotent SQLite schema migrations:

```bash
python -m datahunt.cli migrate
```

---

## CLI Usage

DataHunt provides a command-line interface with formatted Rich tables and status bars.

### Run a Research Task

```bash
# Basic query (JSON export)
python -m datahunt.cli run "Find 10 AI startups founded in 2024"

# Custom limits and export format (CSV or Excel)
python -m datahunt.cli run "Find 15 remote Python developer roles" --max-records 15 --freshness 7 --format xlsx

# Constrain to specific domains
python -m datahunt.cli run "Find latest research papers on LLM agents" --allowed-domains arxiv.org nature.com --format json
```

### List Past Runs

```bash
python -m datahunt.cli list --limit 10
```

### Inspect Run Details & Artifacts

```bash
python -m datahunt.cli status <run_id>
```

---

## Output Formats & Verification Citations

Exported files are written directly to `./exports/` with SHA-256 integrity digests recorded in the database.

### Sample Record (JSON)

```json
{
  "record_id": "rec_8df103ab",
  "data": {
    "title": "Senior AI Engineer",
    "company": "DeepTech Labs",
    "location": "Dubai, UAE",
    "posted_at": "2026-09-05",
    "application_url": "https://example.com/careers/ai-eng"
  },
  "evidence": [
    {
      "field_name": "title",
      "quote": "Senior AI Engineer - Dubai Office",
      "source_url": "https://example.com/careers/ai-eng",
      "retrieved_at": "2026-09-08T12:00:00Z"
    }
  ],
  "verification_status": "verified",
  "confidence_score": 0.95
}
```

---

## Running Tests

DataHunt includes test suites covering SSRF filtering, deduplication, CSV sanitization, SQLite WAL migrations, and pipeline orchestration:

```bash
python -m pytest tests -v
```

Expected output:
```text
tests/test_config.py::test_settings_defaults PASSED
tests/test_config.py::test_settings_masking PASSED
tests/test_config.py::test_error_envelope PASSED
tests/test_config.py::test_compute_backoff PASSED
tests/test_db.py::test_migration_idempotency_and_wal PASSED
tests/test_db.py::test_repositories_crud PASSED
tests/test_dedupe.py::test_dedupe_by_canonical_url_and_evidence_merge PASSED
tests/test_dedupe.py::test_dedupe_distinct_records PASSED
tests/test_export.py::test_export_json_and_sha256 PASSED
tests/test_export.py::test_export_csv_formula_injection PASSED
tests/test_export.py::test_export_xlsx PASSED
tests/test_fetch.py::test_clean_html PASSED
tests/test_fetch.py::test_fetch_mock_and_limits PASSED
tests/test_fetch.py::test_fetch_ssrf_blocking PASSED
tests/test_orchestrator.py::test_orchestrator_end_to_end PASSED
tests/test_policy_ssrf.py::test_scheme_validation PASSED
tests/test_policy_ssrf.py::test_ssrf_ip_blocking PASSED
tests/test_policy_ssrf.py::test_domain_policy PASSED
tests/test_policy_ssrf.py::test_public_business_email PASSED
tests/test_policy_ssrf.py::test_csv_formula_escaping PASSED

======================== 20 passed in ~1s ========================
```

---

## Configuration Reference

| Environment Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *None* | Google Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model identifier |
| `GEMINI_TEMPERATURE` | `0.1` | Temperature for extraction & planning |
| `DATABASE_PATH` | `./data/datahunt.sqlite3` | SQLite database file location |
| `EXPORT_DIR` | `./exports` | Target folder for exported results |
| `MAX_RUN_SECONDS` | `300` | Max wall-clock execution time per run |
| `MAX_SEARCH_QUERIES` | `12` | Max search tool invocations allowed |
| `MAX_PAGES_FETCHED` | `40` | Max HTTP web pages downloaded per run |
| `MAX_RESPONSE_BYTES` | `2000000` | Max bytes accepted per HTTP response (2 MB) |
| `MAX_RECORDS` | `250` | Maximum structured records returned |
| `LOG_LEVEL` | `INFO` | Console & audit log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## License

This project is licensed under the [Apache 2.0 License](LICENSE).
