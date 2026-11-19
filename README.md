# DataHunt

DataHunt is a Python-based, multi-agent web research and job-intelligence workstation. It accepts a natural-language directive, classifies the request, plans bounded web searches, fetches public sources, extracts structured records, verifies evidence, deduplicates results, persists the run to SQLite, and exports the result as JSON, CSV, XLSX, Markdown, or DOCX.

It can be used through:

- A FastAPI service with Swagger/OpenAPI documentation.
- A cyberpunk-style browser hub and real-time cockpit.
- A Rich command-line interface.
- Python agent wrappers such as `get_agent("jobs")` and `get_agent("people")`.

> **Status:** Active development. The latest recorded full-suite run had 734 passing tests and one crawler log-capture failure; subsequent focused checks passed. This is a local workstation with in-process execution, not a hosted multi-tenant service. See [Testing and Quality Checks](#testing-and-quality-checks) and [Known Gaps and Caveats](#known-gaps-and-caveats).

## Table of Contents

- [Quick Start](#quick-start)
- [What DataHunt Does](#what-datahunt-does)
- [Core Capabilities](#core-capabilities)
- [Agent Fleet](#agent-fleet)
- [Architecture](#architecture)
- [Repository Layout](#repository-layout)
- [Requirements](#requirements)
- [Local Setup](#local-setup)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Using the CLI](#using-the-cli)
- [Using the API](#using-the-api)
- [Using the Web Interfaces](#using-the-web-interfaces)
- [Request Lifecycle](#request-lifecycle)
- [Job Discovery Pipeline](#job-discovery-pipeline)
- [FreeHire Integration Reference](#freehire-integration-reference)
- [Research and Market Pipelines](#research-and-market-pipelines)
- [People, Company, and Interview Intelligence](#people-company-and-interview-intelligence)
- [Persistence and Data Model](#persistence-and-data-model)
- [Exports](#exports)
- [Security and Trust Boundaries](#security-and-trust-boundaries)
- [Docker](#docker)
- [Operations and Recovery](#operations-and-recovery)
- [Testing and Quality Checks](#testing-and-quality-checks)
- [Troubleshooting](#troubleshooting)
- [Known Gaps and Caveats](#known-gaps-and-caveats)
- [Contributing](#contributing)
- [Further Documentation](#further-documentation)
- [License](#license)

## Quick Start

Run these commands from the repository root. Python 3.10+ is required; the container uses Python 3.12. The frontend is static HTML/CSS/JavaScript served by FastAPI, so no Node.js build is required.

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

**macOS/Linux**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

Edit `.env` before starting. The example selects Gemini: set `GEMINI_API_KEY`, or select `LLM_PROVIDER=nvidia` and set `NVIDIA_API_KEY`. It also enables FreeHire; set `FREEHIRE_ENABLED=false` if you do not want that external catalogue queried.

Then use the virtual environment's Python:

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m datahunt.cli migrate
.\.venv\Scripts\python.exe -m uvicorn datahunt.api:app --host 127.0.0.1 --port 8000
```

```bash
# macOS/Linux
.venv/bin/python -m datahunt.cli migrate
.venv/bin/python -m uvicorn datahunt.api:app --host 127.0.0.1 --port 8000
```

Open the [hub](http://127.0.0.1:8000/) or [API docs](http://127.0.0.1:8000/docs). For a first run, select Job Radar and enter a role and geography, such as `Find AI Engineer jobs in Dubai, UAE`. Inspect the run's warnings, verified records, and export links when it finishes. A target record count is a ceiling/goal, not a promise that enough matching vacancies exist.

| Your goal | Start here |
| --- | --- |
| Search and track jobs | Hub → Job Radar; `/jobs.html` for tracking |
| Compare technologies or collect research | Hub → Deep Research |
| Analyze market signals | Hub → Market Intel |
| Find public hiring contacts | CLI/API/Python with `people` mode |
| Use FreeHire | [Configuration and adapter behavior](#freehire-integration-reference) |
| Integrate another client | [REST and WebSocket API](#using-the-api) |
| Run without a browser | [CLI examples](#using-the-cli) |

## What DataHunt Does

DataHunt is designed for research tasks where a useful answer needs more than one search result. A run is treated as a bounded evidence pipeline rather than a single LLM completion:

```text
Natural-language request
        │
        ▼
Intent routing and request normalization
        │
        ▼
Agent-specific planning
        │
        ▼
Search → fetch → extract → verify → deduplicate
        │
        ▼
Evidence-backed records and warnings
        │
        ├── SQLite persistence
        ├── JSON / CSV / XLSX / MD / DOCX export
        └── API, WebSocket, CLI, and cockpit telemetry
```

The system is deliberately conservative at trust boundaries:

- A field can remain unavailable instead of being invented.
- A source can be blocked by SSRF or contact policy checks.
- A record can be marked `needs_review`, `rejected`, or `duplicate` instead of being presented as verified.
- A run can finish as `partial` when budgets or source failures prevent complete coverage.

## Core Capabilities

### Multi-agent routing

The system exposes four top-level agent profiles:

| Mode | Agent | Best for |
| --- | --- | --- |
| `jobs` | Job Hunt AI Agent | Live job discovery, ATS harvesting, qualification, and application tracking |
| `research` | Deep Research AI Agent | Technical research, explanations, comparisons, and evidence-backed dossiers |
| `market` | Market Intel AI Agent | Equity/market intelligence and competitive or SaaS research workflows |
| `people` | People Scout AI Agent | Public recruiters, hiring managers, engineering leaders, and business contact paths |

`auto` mode uses deterministic intent routing and optional LLM refinement to select the appropriate path.

### Evidence-first extraction

Records retain source-document relationships and field-level evidence. The canonical models live under `datahunt/models/`, especially:

- `ResearchTask` and `ResearchSpec` for normalized requests.
- `ResearchRun` and `RunBudget` for execution state and limits.
- `SourceDocument` for fetched source metadata and retrieval status.
- `ExtractedRecord` for normalized output records.
- `RecordEvidence` for field-level provenance.

### Bounded execution

Every run has limits for time, search calls, fetched pages, response size, iterations, and output records. These limits are persisted with the run so later code does not silently replace them with process-wide defaults.

### Optional FreeHire catalogue source

DataHunt can use [FreeHire](https://github.com/strelov1/freehire), an MIT-licensed open-source job catalogue, as an additional structured source for job searches. The integration calls FreeHire's public read-only API and then sends the returned links through DataHunt's existing geography, freshness, title, deduplication, verification, and qualification pipeline.

Enable it explicitly in `.env`:

```dotenv
FREEHIRE_ENABLED=true
FREEHIRE_API_URL=https://freehire.me/api/v1
FREEHIRE_API_KEY=
FREEHIRE_MAX_REQUESTS_PER_RUN=20
```

The adapter uses `GET /jobs/search` with full-text query fields and preserves the employer application URL when FreeHire provides one. If a posting is direct-apply, it uses the public FreeHire job page as the source URL. A FreeHire API key is optional for the public API, but commercial/product use must follow FreeHire's current data-licence terms and rate limits. The integration is disabled by the Python runtime default to avoid unexpected external calls; the checked-in `.env.example` opts it in for a fresh local setup.

The settings API accepts the enable flag and request cap. The browser settings form renders the flag as a checkbox. Safe settings output includes the endpoint URL and a key-configured boolean, but not the FreeHire API key. See the [integration reference](#freehire-integration-reference) for request mapping, fallback order, and limitations.

### Job-specific precision

The job pipeline supports:

- Direct ATS discovery for Greenhouse, Lever, Ashby, Workable, and other supported patterns.
- Multi-location `OR` matching such as Saudi Arabia or UAE.
- Explicit skills as hard requirements and inferred skills as soft ranking signals.
- Freshness-aware ranking, including “just now” and same-day postings.
- Direct application URL preference.
- Hard rejection for invalid, expired, login, board-root, or unrelated pages.
- Saved-job, application, interview, and monitoring workflows.

### Public-contact safety

People and contact discovery is restricted to public professional profiles, official pages, and business-public contact policy. The system does not infer private emails or fabricate phone numbers.

## Agent Fleet

### 1. Job Hunt AI Agent

Python wrapper: `agent.job_agent.JobHuntAgent`
Mode: `jobs`

The job agent delegates execution to the job discovery runtime and its supporting agents. Important components include:

- `datahunt/agent/discovery_engine.py`
- `datahunt/agent/runtime.py`
- `datahunt/agent/decision.py`
- `datahunt/agent/policies.py`
- `datahunt/agents/query_understanding.py`
- `datahunt/agents/query_expansion.py`
- `datahunt/agents/job_analysis.py`
- `datahunt/sources/crawlers.py`

Typical directive:

```text
Find current GenAI Engineer jobs in Saudi Arabia or UAE for candidates with 0–6 years of experience.
```

The pipeline should preserve the requested role, geography, experience bounds, explicit skills, freshness, and direct-application constraints from intake through qualification.

### 2. Deep Research AI Agent

Python wrapper: `agent.research_agent.DeepResearchAgent`
Mode: `research`

The research agent uses `ResearchDiscoveryEngine` for multi-wave evidence collection. It supports technical explanations, architecture investigations, comparisons, factual research, and dossier-style output.

Key modules:

- `datahunt/agent/research_discovery.py` — planning, source waves, retrieval, synthesis, and record mapping.
- `datahunt/agent/research_report.py` — report rendering extracted from the discovery engine.
- `datahunt/agents/intent_router.py` — deterministic and optional LLM intent classification.
- `datahunt/tools/search.py` — search providers and query generation.
- `datahunt/tools/fetch.py` — SSRF-protected retrieval.
- `datahunt/tools/extract.py` — structured extraction.
- `datahunt/tools/verify.py` — evidence and field verification.

Typical directive:

```text
Compare HNSW and IVF indexing for a production vector-search system.
```

The research report renderer keeps facts, evidence, caveats, and source URLs visible instead of presenting unsupported model prose as verified fact.

### 3. Market Intel AI Agent

Python wrapper: `agent.market_agent.MarketIntelAgent`
Mode: `market`

The market path is equity-first when the request contains stock, exchange, momentum, technical setup, or market-intelligence signals. It also contains competitive and SaaS extraction helpers used by the broader market/research tooling.

Key modules:

- `datahunt/agent/market_discovery.py` — market discovery workflow.
- `datahunt/agent/market_report.py` — market report rendering.
- `datahunt/agents/market_intent.py` — market intent parsing.
- `datahunt/agents/market_validator.py` — market candidate validation.
- `datahunt/tools/market_analysis.py` — deterministic technical indicators.

Market outputs are informational intelligence, not financial advice or a promise of returns. The implementation is designed to keep technical calculations deterministic and to expose counter-evidence and risk factors.

### 4. People Scout AI Agent

Python wrapper: `agent.people_agent.PeopleScoutAgent`
Mode: `people`

The People Scout agent uses the existing people/contact workflow in the orchestrator:

- `datahunt/agents/people_intelligence.py` finds public professional profiles.
- `datahunt/agents/contact_discovery.py` finds official contact pages and safe public business emails.
- `datahunt/models/shared_intel.py` defines people and contact models.

Typical directive:

```text
Find public AI recruiting contacts and engineering hiring managers at a target company.
```

Use `people` when the goal is contact discovery. Use `research` or `company` endpoints when the goal is a general company profile rather than a contact path.

### Specialized pipeline agents

The repository also contains focused modules used by the main agents and API workflows:

- Company research: `datahunt/agents/company_research.py`
- Interview preparation and answer evaluation: `datahunt/agents/interview_prep.py`
- Job analysis and ranking: `datahunt/agents/job_analysis.py`
- Preference learning: `datahunt/agents/learning_engine.py`
- Saved-job monitoring: `datahunt/agents/job_monitor.py`
- Source intelligence, evidence, freshness, risk, contradiction, and coverage helpers under `datahunt/agents/`

## Architecture

### Layered design

```text
┌──────────────────────────────────────────────────────────────┐
│ Interfaces                                                   │
│ FastAPI / WebSocket / CLI / HTML cockpit / Python wrappers   │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ Orchestration                                                │
│ ResearchOrchestrator, intent routing, budgets, run lifecycle │
└──────────────┬───────────────────────┬───────────────────────┘
               │                       │
┌──────────────▼──────────────┐  ┌─────▼───────────────────────┐
│ Discovery engines           │  │ Specialized agents          │
│ jobs / research / market     │  │ parsing, people, company,   │
│ / people workflow            │  │ interview, ranking, risk    │
└──────────────┬──────────────┘  └─────┬────────────────────────┘
               │                       │
┌──────────────▼───────────────────────▼────────────────────────┐
│ Tool substrate                                                │
│ Search → Fetch → Extract → Verify → Dedupe → Export            │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│ Persistence and observability                                 │
│ SQLite/WAL, evidence, checkpoints, tool events, LangSmith     │
└────────────────────────────────────────────────────────────────┘
```

### Orchestrator responsibilities

`agent/orchestrator.py` owns the run-level state machine. It:

1. Normalizes the incoming request through the active model client.
2. Creates a `ResearchTask` and bounded `ResearchRun`.
3. Selects the route based on explicit mode and canonical intent.
4. Initializes tools with the active model client.
5. Emits run, phase, model, decision, record, and completion events.
6. Persists documents, records, evidence, exports, and warnings.
7. Returns a stable result dictionary for the API and CLI.

`datahunt/orchestrator.py` remains a backward-compatibility import surface for the newer `agent/` package.

### Model-provider routing

The active provider is selected by configuration and optional per-run model selection:

- NVIDIA NIM via `datahunt/llm/nvidia_client.py`.
- Google Gemini via `datahunt/llm/gemini_client.py`.

The orchestrator selects the provider from the model name when a run-specific model is supplied. If no model is supplied, it uses the configured default provider/client.

## Repository Layout

```text
DataHunt_agent/
├── agent/                         # Public agent wrappers and orchestrator
│   ├── base.py                    # BaseAgent contract
│   ├── job_agent.py               # Job Hunt wrapper
│   ├── research_agent.py          # Deep Research wrapper
│   ├── market_agent.py            # Market Intel wrapper
│   ├── people_agent.py            # People Scout wrapper
│   └── orchestrator.py            # Run lifecycle and routing
├── datahunt/
│   ├── api.py                     # FastAPI routes and WebSocket stream
│   ├── cli.py                     # Rich CLI
│   ├── config.py                  # Environment and persistent settings
│   ├── orchestrator.py            # Backward-compatible re-export module
│   ├── agent/                     # Runtime discovery engines and state machine
│   ├── agents/                    # Focused domain agents
│   ├── db/                        # SQLite connection, migrations, repositories
│   ├── evidence/                  # Evidence storage helpers
│   ├── llm/                       # NVIDIA/Gemini clients and prompts
│   ├── models/                    # Pydantic domain schemas
│   ├── sources/                   # Source registry and crawlers
│   └── tools/                     # Search, fetch, extract, verify, dedupe, export
├── frontend/
│   ├── index.html                 # Central agent hub
│   ├── cockpit.html               # Real-time cockpit
│   ├── jobs.html                  # Application tracker
│   ├── bot_preview.html            # 3D bot showcase
│   ├── css/                       # UI styles
│   └── js/                        # Browser controllers
├── migrations/                    # Ordered SQLite schema migrations
├── tests/                         # Pytest suite
├── docs/                          # Architecture, database, security, and design notes
├── data/                          # Default SQLite data directory
├── exports/                       # Generated artifacts
├── Dockerfile                     # Single-container image
├── docker-compose.yml             # Local container orchestration
├── pyproject.toml                 # Package metadata and pytest config
├── requirements.txt               # Runtime and test dependencies
├── .env.example                  # Example environment configuration
├── run_app.bat                   # Windows setup and launcher
└── start.bat                     # Windows launcher alias
```

## Requirements

### Runtime

- Python 3.10 or newer. Python 3.12 is used by the Dockerfile.
- A writable working directory for SQLite, exports, and optional `.env` synchronization.
- Network access to the configured search providers and public source pages.
- At least one configured LLM provider for model-backed planning, synthesis, or extraction. Deterministic fallbacks exist for several paths, but a fully useful deployment should configure a provider.

### Optional services

- LangSmith for tracing and run telemetry.
- Docker or Docker Compose for containerized execution.
- A browser for the HTML hub and cockpit.

## Local Setup

### 1. Create a virtual environment

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The dependency set includes FastAPI/Uvicorn, Pydantic, HTTP clients, search providers, BeautifulSoup, LLM SDKs, LangSmith, Rich, SQLite-adjacent utilities, and export libraries for XLSX and DOCX.

### 3. Create the environment file

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then set at least one provider key. See [Configuration](#configuration).

### 4. Apply SQLite migrations

```bash
python -m datahunt.cli migrate
```

The application also creates the configured database and export directories on startup. Running migrations explicitly is still recommended before the first run and after pulling schema changes.

### 5. Start the API server

```bash
python -m uvicorn datahunt.api:app --host 127.0.0.1 --port 8000 --reload
```

Open:

- Hub: <http://127.0.0.1:8000/>
- API docs: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- Health: <http://127.0.0.1:8000/health>
- Cockpit: <http://127.0.0.1:8000/cockpit.html>
- Job tracker: <http://127.0.0.1:8000/jobs.html>

### Windows one-click launcher

From a Command Prompt:

```bat
run_app.bat
```

The launcher attempts to activate `.venv` or `venv`, creates `.env` from `.env.example` when missing, runs migrations, starts Uvicorn on port `8000`, waits for `/health`, and opens the hub in the default browser. Install dependencies first: the launcher does not create a virtual environment or install requirements. It currently continues even if migration returns an error; resolve any migration error before running tasks.

## Configuration

Configuration is loaded from `.env` by `datahunt/config.py`. Settings can also be updated through the `/api/settings` endpoints and persisted to SQLite plus `.env` for the editable settings supported by the application.

### Configuration precedence

1. Existing process environment variables take precedence over `.env` during `load_dotenv()`.
2. Missing variables use defaults in `Settings`.
3. Stored `system_settings` values override editable settings when the settings singleton loads.
4. Request-level values such as mode, model, record target, and freshness are passed to task creation and normalized into the run specification.

Relative storage paths resolve from the process working directory. Run commands from the repository root so `.env`, SQLite, migrations, and exports use the expected locations. Changing `.env` does not refresh an existing provider instance; restart the service and check `/api/settings` for database overrides that may still win.

### FreeHire settings

| Variable | Runtime default | Purpose |
| --- | --- | --- |
| `FREEHIRE_ENABLED` | `false` | Enable the optional catalogue; `.env.example` sets `true` |
| `FREEHIRE_API_URL` | `https://freehire.me/api/v1` | API base URL including `/api/v1` |
| `FREEHIRE_API_KEY` | empty | Optional Bearer token for a FreeHire API plan |
| `FREEHIRE_MAX_REQUESTS_PER_RUN` | `20` | Request cap, enforced per provider instance |

All four values are editable through the settings API. Only the flag and cap have browser form fields. The configured endpoint is visible in safe settings output; credentials belong in the dedicated key field, not in the URL.

### LLM providers

#### NVIDIA NIM

```dotenv
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=your-key-here
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_PRIMARY_MODEL=openai/gpt-oss-20b
NVIDIA_FALLBACK_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_MAX_OUTPUT_TOKENS=2048
NVIDIA_TEMPERATURE=0.1
NVIDIA_RPM=40
```

#### Google Gemini

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=auto
GEMINI_LITE_MODEL=gemini-flash-lite-latest
GEMINI_FLASH_MODEL=gemini-flash-latest
GEMINI_MAX_OUTPUT_TOKENS=4096
GEMINI_TEMPERATURE=0.1
```

The checked-in `.env.example` currently selects Gemini, while `datahunt/config.py` defaults to NVIDIA when no `LLM_PROVIDER` is supplied. Set the provider explicitly instead of relying on the default.

### Storage and limits

| Variable | Purpose | Runtime default |
| --- | --- | --- |
| `DATABASE_PATH` | SQLite database path | `./data/datahunt.sqlite3` |
| `EXPORT_DIR` | Export artifact directory | `./exports` |
| `MAX_RUN_SECONDS` | Search-phase deadline | `1800` |
| `DRAIN_TIMEOUT_SECONDS` | Grace period to drain discovered candidates | `600` |
| `MAX_SEARCH_QUERIES` | Search-call ceiling | `150` |
| `MAX_PAGES_FETCHED` | Fetch-call/page ceiling | `300` |
| `MAX_BROWSER_PAGES` | Browser/page budget | `12` |
| `MAX_RESPONSE_BYTES` | Maximum response size | `5000000` |
| `MAX_RECORDS` | Maximum output records | `500` |
| `MAX_TOOL_STEPS` | Tool-step ceiling | `50` |
| `MAX_ITERATIONS` | Runtime decision-loop ceiling | `200` |
| `MAX_CONCURRENT_RUNS` | In-process execution slots | `2` |
| `SEARCH_BATCH_SIZE` | Parallel search dispatch size | `20` |
| `DEFAULT_FRESHNESS` | UI/job freshness default | `0sec` |
| `DEFAULT_COLLECTION_MODE` | Collection strategy | `thorough` |

### HTTP fetch limits

| Variable | Purpose | Runtime default |
| --- | --- | --- |
| `CONNECT_TIMEOUT_SECONDS` | Connection timeout | `5.0` |
| `READ_TIMEOUT_SECONDS` | Read timeout | `20.0` |
| `TOTAL_FETCH_TIMEOUT_SECONDS` | Overall fetch timeout | `30.0` |
| `MAX_REDIRECTS` | Redirect ceiling | `5` |
| `MAX_EXTRACTED_CHARS` | Text extraction cap | `30000` |

### API security

```dotenv
DATAHUNT_API_KEY=replace-with-a-long-random-value
```

When configured, protected API routes require either:

```http
X-API-Key: replace-with-a-long-random-value
```

or:

```http
Authorization: Bearer replace-with-a-long-random-value
```

The current middleware exempts `/`, `/health`, `/api/info`, `/favicon.ico`, `/redoc`, and prefixes `/frontend`, `/css`, `/js`, `/docs`, and `/openapi.json`. Settings writes/resets, task/run/job/export routes, telemetry, and other API routes require the key. Top-level HTML pages such as `/cockpit.html` also require the key when configured, so use the hub without `DATAHUNT_API_KEY` or place authentication at a reverse proxy that supports the UI. The WebSocket checks `X-API-Key` or Bearer authentication before accepting run commands. These are current access-control boundaries, not a complete identity or tenant system.

### LangSmith observability

```dotenv
LANGSMITH_API_KEY=your-key-here
LANGSMITH_PROJECT=DataHunt
LANGSMITH_TRACING=true
```

`LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, and `LANGCHAIN_TRACING_V2` are also recognized as compatibility aliases. Never commit provider keys or tracing keys.

### Settings API

The settings UI and API expose a safe, masked view of configured keys:

```bash
curl http://127.0.0.1:8000/api/settings
```

Update supported settings:

```bash
curl -X POST http://127.0.0.1:8000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"max_run_seconds": 600, "default_collection_mode": "fast"}'
```

The API accepts either a flat object or `{"settings": {...}}`. Unknown keys and some invalid values are ignored; read back the effective value rather than treating HTTP success as proof that every field changed. Provider API keys are intentionally not editable through this endpoint; keep them in environment/secret-manager configuration. Non-secret editable values are persisted to SQLite and synchronized to `.env`.

Reset stored overrides and selected runtime defaults:

```bash
curl -X POST http://127.0.0.1:8000/api/settings/reset
```

Reset clears database overrides and removes editable non-secret keys from `.env`, then resets the supported in-memory defaults. Provider keys and FreeHire values supplied only through environment variables are not deleted. The concurrent-run semaphore is initialized at process startup, so restart after changing `max_concurrent_runs`.

## Running the Application

### Start the API directly

```bash
python -m uvicorn datahunt.api:app --host 127.0.0.1 --port 8000
```

For a container or LAN-facing process:

```bash
python -m uvicorn datahunt.api:app --host 0.0.0.0 --port 8000
```

Only use `0.0.0.0` behind an intentional network and authentication boundary.

### List installed agents

```bash
python -m datahunt.cli agents
```

The current fleet reports:

- `JOBS` — Job Hunt AI Agent
- `RESEARCH` — Universal Research AI Agent
- `MARKET` — Market Intel AI Agent
- `PEOPLE` — People Scout AI Agent

### Programmatic agent access

```python
from agent import get_agent, list_agents

for profile in list_agents():
    print(profile["mode"], profile["name"])

people_agent = get_agent("people")
result = people_agent.run(
    "Find public AI recruiters and engineering hiring managers at Example Corp"
)
print(result)
```

`BaseAgent.run()` applies the agent defaults, creates a task/run through `ResearchOrchestrator`, and executes the persisted run. The wrapper does not bypass the normal budget and persistence path.

## Using the CLI

The CLI entry point is `python -m datahunt.cli`.

### Help

```bash
python -m datahunt.cli --help
python -m datahunt.cli run --help
```

### Start a job search

```bash
python -m datahunt.cli run \
  "Find current GenAI Engineer jobs in Dubai and Abu Dhabi with 0-6 years experience" \
  --agent jobs \
  --max-records 25 \
  --freshness 1 \
  --format json
```

### Start deep research

```bash
python -m datahunt.cli run \
  "Compare HNSW and IVF indexing for production vector search" \
  --agent research \
  --max-records 20 \
  --format md
```

### Start market intelligence

```bash
python -m datahunt.cli run \
  "Analyze current NSE momentum candidates and their bearish risks" \
  --agent market \
  --max-records 20 \
  --format json
```

### Start people/contact discovery

```bash
python -m datahunt.cli run \
  "Find public AI recruiters and hiring managers at Example Corp" \
  --agent people \
  --max-records 10 \
  --format json
```

### Inspect runs

```bash
python -m datahunt.cli list --limit 20
python -m datahunt.cli status RUN_ID_HERE
```

### CLI options

| Option | Values | Meaning |
| --- | --- | --- |
| `--agent` | `auto`, `jobs`, `research`, `market`, `people` | Select the specialized profile |
| `--model` | `auto` or provider model name | Select the model/client for the run |
| `--max-records` | integer | Target output size |
| `--freshness` | integer days | Requested freshness window |
| `--format` | `json`, `csv`, `xlsx`, `md`, `docx` | Export format |
| `--allowed-domains` | zero or more domains | Restrict sources to an allowlist |
| `--blocked-domains` | zero or more domains | Exclude sources |

## Using the API

The API is defined in `datahunt/api.py`. FastAPI generates the complete OpenAPI schema automatically.

Examples below assume local execution with `DATAHUNT_API_KEY` unset. When it is set, add `-H "X-API-Key: $DATAHUNT_API_KEY"` to protected requests in Bash, or supply `@{"X-API-Key" = $env:DATAHUNT_API_KEY}` as PowerShell headers. Multi-line `curl` examples use Bash syntax; use `curl.exe` with PowerShell-compatible quoting or `Invoke-RestMethod` on Windows.

### Endpoint index

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/health` | Database connectivity and model configuration |
| `GET` | `/api/info` | Application name, runtime version, docs link |
| `GET` | `/api/settings` | Effective safe settings and form schema |
| `POST` | `/api/settings` | Persist editable settings |
| `POST` | `/api/settings/reset` | Clear overrides and reset selected values |
| `POST` | `/tasks` | Create and execute or queue a run |
| `GET` | `/tasks` | Recent tasks; `limit` defaults to 20, maximum 100 |
| `GET` | `/tasks/{task_id}` | Persisted task specification |
| `GET` | `/runs/latest` | Most recent run and available Markdown summary |
| `GET` | `/runs/{run_id}` | Run status, counters, warnings, exports, errors |
| `GET` | `/runs/{run_id}/records` | Records, optionally filtered by verification `status` |
| `GET` | `/runs/{run_id}/export` | Download or generate an export; default `format=md` |
| `GET` | `/exports/{export_id}/download` | Download an existing export artifact |
| `GET` | `/api/jobs` | Paginated tracked jobs; optional `run_id`, `limit`, `offset` |
| `GET` | `/api/jobs/saved` | Saved jobs |
| `GET` | `/api/jobs/applications` | Recorded applications |
| `GET`, `PATCH`, `DELETE` | `/api/jobs/{record_id}` | Read, update tracking state, or delete a job |
| `POST` | `/api/jobs/bulk-delete` | Delete supplied `record_ids` |
| `POST` | `/api/jobs/{record_id}/save` | Save a job and optional notes |
| `POST` | `/api/jobs/{record_id}/ignore` | Record an ignore reason |
| `POST` | `/api/jobs/{record_id}/apply` | Record an application and optional cover letter |
| `POST` | `/api/company/research` | Research a company |
| `POST` | `/api/interview/prep` | Generate an interview preparation pack |
| `POST` | `/api/interview/evaluate` | Evaluate a practice answer |
| `GET` | `/api/monitor/events` | Unnotified saved-job changes |
| `POST` | `/api/monitor/check` | Check saved postings now |
| `GET` | `/telemetry/runs` | LangSmith project run data |
| WebSocket | `/ws/agent-stream` | Start runs and receive broadcast events |

`/docs`, `/redoc`, and `/openapi.json` provide generated schema documentation. There is currently no public REST cancellation, resume, or scheduling endpoint.

### Health and metadata

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/info
```

`/health` reports database connectivity, provider configuration, and the configured primary/fallback model names. It does not prove that an external provider key is valid or that a live source is reachable.

### Create a run

Synchronous request:

```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Find current AI engineer jobs in UAE",
    "agent_mode": "jobs",
    "max_records": 20,
    "freshness_days": 1,
    "output_format": "json",
    "run_in_background": false
  }'
```

Background request:

```bash
curl -X POST http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Compare vector databases for production RAG",
    "agent_mode": "research",
    "max_records": 20,
    "output_format": "md",
    "run_in_background": true
  }'
```

The response contains `task_id`, `run_id`, and a status. Use the run endpoints to inspect progress and results.

For a background request, the HTTP status is `201` and the response has this shape (IDs below are illustrative):

```json
{
  "task_id": "task_example",
  "run_id": "run_example",
  "status": "queued",
  "message": "Task accepted and running in background."
}
```

`queued` is an acknowledgment value, not a `RunStatus` enum member. The background work runs inside the API process, not in a durable external queue. Synchronous responses additionally include verified/rejected counts, confidence, model metadata, export filename, and summary.

PowerShell example:

```powershell
$body = @{
    query = "Find AI Engineer jobs in Dubai, UAE"
    agent_mode = "jobs"
    max_records = 20
    freshness_days = 7
    output_format = "json"
    run_in_background = $true
} | ConvertTo-Json
$headers = @{}
if ($env:DATAHUNT_API_KEY) { $headers["X-API-Key"] = $env:DATAHUNT_API_KEY }
$task = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/tasks" -Headers $headers -ContentType "application/json" -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8000/runs/$($task.run_id)" -Headers $headers
```

### `POST /tasks` request fields

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `query` | string | required | Natural-language directive |
| `max_records` | integer | `150` at the API boundary | Valid range `1–500`; also subject to run limits |
| `freshness_days` | integer or null | `7` | Integer values must be at least `1`; used by normalization and planning |
| `output_format` | string | `json` | `json`, `csv`, `xlsx`, `md`, or `docx` |
| `contact_policy` | string | `business_public_only` | Contact handling policy |
| `allowed_domains` | string[] | `[]` | Optional source allowlist |
| `blocked_domains` | string[] | `[]` | Optional source denylist |
| `run_in_background` | boolean | `false` | Queue execution through FastAPI background tasks |
| `model` | string or null | `auto` | Provider/model selection |
| `agent_mode` | string or null | `auto` | `auto`, `jobs`, `research`, `market`, or `people` |

### Task and run inspection

```bash
curl http://127.0.0.1:8000/tasks
curl http://127.0.0.1:8000/tasks/TASK_ID
curl http://127.0.0.1:8000/runs/RUN_ID
curl http://127.0.0.1:8000/runs/RUN_ID/records
curl http://127.0.0.1:8000/runs/latest
```

The run response includes status, counters, warnings, timestamps, exports, and any error code/message. Records include fields, canonical URL, verification status, confidence, evidence, and warnings.

Filter records by verification state:

```bash
curl 'http://127.0.0.1:8000/runs/RUN_ID/records?status=verified'
curl 'http://127.0.0.1:8000/runs/RUN_ID/records?status=needs_review'
```

| Verification state | Meaning |
| --- | --- |
| `unverified` | Stored record awaiting verification |
| `verified` | Passed the applicable pipeline checks |
| `needs_review` | Insufficient or ambiguous evidence needs inspection |
| `rejected` | Failed applicable validation or qualification |
| `duplicate` | Collapsed into another record's identity |

Verification describes the checks implemented by the selected pipeline; it is not a guarantee that an employer is still accepting applications or that every report claim is correct.

Common HTTP errors are `400` for task creation or invalid verification status, `401` for a missing/incorrect API key on protected routes, `404` for missing tasks/runs/jobs/exports, `422` for Pydantic request validation, and `500` for execution/export failures. A valid task can also finish with `status=failed`; inspect the run resource rather than relying only on the creation response.

### On-demand exports

```bash
curl -L -o result.md \
  'http://127.0.0.1:8000/runs/RUN_ID/export?format=md'

curl -L -o result.docx \
  'http://127.0.0.1:8000/runs/RUN_ID/export?format=docx'
```

Supported formats are `json`, `csv`, `xlsx`, `md`, and `docx`. Generated export metadata is stored in SQLite with a SHA-256 value when available.

### Job tracker endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/jobs` | List tracked jobs with pagination and `X-Total-Count` |
| `GET` | `/api/jobs/{record_id}` | Get full job details |
| `PATCH` | `/api/jobs/{record_id}` | Update application/interview status and notes |
| `DELETE` | `/api/jobs/{record_id}` | Delete one tracked job |
| `POST` | `/api/jobs/bulk-delete` | Delete multiple tracked jobs |
| `GET` | `/api/jobs/saved` | List saved jobs |
| `GET` | `/api/jobs/applications` | List application tracking data |
| `POST` | `/api/jobs/{record_id}/save` | Save a job and record a positive preference signal |
| `POST` | `/api/jobs/{record_id}/ignore` | Ignore a job and record a negative preference signal |
| `POST` | `/api/jobs/{record_id}/apply` | Record an application event |

Example status update:

```bash
curl -X PATCH http://127.0.0.1:8000/api/jobs/RECORD_ID \
  -H 'Content-Type: application/json' \
  -d '{
    "applied_status": "interviewing",
    "interview_status": "technical",
    "notes": "Technical screen scheduled"
  }'
```

Application statuses accepted by `PATCH` are `not_applied`, `applied`, `interviewing`, `offered`, and `rejected`. Interview statuses are `no_call`, `screening`, `technical`, `final_round`, `offered`, and `rejected`.

`GET /api/jobs` defaults to `limit=200`, accepts up to `5000`, and uses a zero-based `offset`. Read `X-Total-Count` for pagination. The `/apply` action only records your application in DataHunt; it does not send anything to an employer.

### Company research and interview endpoints

Company profile:

```bash
curl -X POST http://127.0.0.1:8000/api/company/research \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"Example Corp","force_refresh":false}'
```

Interview preparation:

```bash
curl -X POST http://127.0.0.1:8000/api/interview/prep \
  -H 'Content-Type: application/json' \
  -d '{
    "role_title":"Senior Machine Learning Engineer",
    "company_name":"Example Corp",
    "skills":["Python","PyTorch","distributed systems"],
    "job_description":"Build and operate machine-learning services."
  }'
```

Mock-answer evaluation:

```bash
curl -X POST http://127.0.0.1:8000/api/interview/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "role_title":"Senior Machine Learning Engineer",
    "question":"Tell me about a production incident you handled.",
    "answer":"I reduced latency by 40 percent after profiling the service..."
  }'
```

### Monitoring and telemetry

```bash
curl http://127.0.0.1:8000/api/monitor/events
curl -X POST http://127.0.0.1:8000/api/monitor/check
curl http://127.0.0.1:8000/telemetry/runs
```

The monitor revisits saved job URLs and records closed, removed, or otherwise changed postings. LangSmith telemetry is only useful when the corresponding environment variables are configured.

### WebSocket stream

Connect to:

```text
ws://127.0.0.1:8000/ws/agent-stream
```

Start a run by sending a JSON message:

```json
{
  "action": "start_run",
  "query": "Find current AI engineer jobs in Dubai",
  "agent_mode": "jobs",
  "max_records": 20,
  "freshness_days": 1,
  "output_format": "json",
  "model": "auto"
}
```

The server emits lifecycle events such as `run.started`, `phase.change`, `model.active`, `agent.decision`, `record.extracted`, `record.verified`, `run.completed`, and `run.failed`. A `ping` action returns a `pong` event.

Each message is an envelope of the form `{"event": "event.name", "data": {...}}`. Connections share an in-memory broadcast set: events are not isolated by user, and there is no replay cursor. Browser disconnection does not cancel an already detached task, but stopping/reloading the server stops in-process execution. Use the REST run/record endpoints to recover persisted state after reconnecting.

Unlike `POST /tasks`, the current WebSocket start handler does not forward `allowed_domains`, `blocked_domains`, or `contact_policy` fields. Use the REST API or CLI when an explicit source policy is required.

## Using the Web Interfaces

### Central hub: `/`

The hub provides navigation to the supported UI surfaces and a quick directive launcher. It currently exposes direct navigation for:

- Job Radar.
- Deep Research.
- Market Intel.
- 3D bot showcase.
- Job tracker.
- API documentation.

The People agent is currently available through the CLI, API, and Python factory; it is not yet a dedicated hub navigation card.

### Cockpit: `/cockpit.html`

The cockpit is a real-time visual interface for run telemetry. It can be opened with a mode query parameter:

```text
/cockpit.html?mode=jobs
/cockpit.html?mode=research
/cockpit.html?mode=market
```

The cockpit displays phase status, counters, model state, records, reports, and export links. It uses the WebSocket stream when available and falls back to HTTP polling.

### Job tracker: `/jobs.html`

The tracker reads persisted job/application data and supports:

- Search and filtering.
- Saved jobs.
- Applied/interview status.
- Bulk deletion.
- Live hunting from the tracker.
- Links back to the radar cockpit and settings.

### Bot showcase: `/bot_preview.html`

This is a visual demonstration surface for the 3D assistant UI. It is not a separate backend execution path.

## Request Lifecycle

### 1. Intake

Requests can arrive from the API, CLI, WebSocket, or `BaseAgent.run()`. The boundary validates basic fields and creates a task/run record.

### 2. Normalization

The active LLM client normalizes the request into a `ResearchSpec`. This captures topic, geography, requested fields, freshness, contact policy, source policy, output mode, and agent mode.

### 3. Canonical intent routing

`IntentRouter` first applies deterministic patterns. If a live model is available, it may refine the result, but deterministic guards prevent obvious misclassification such as treating “how do I become an engineer?” as an active job search.

Explicit operator modes take precedence:

- `jobs` forces job-search intent.
- `market` forces market-research intent.
- `research` prevents job-mode execution when the query contains profession words.
- `people` enters the people/contact workflow through the orchestrator.

### 4. Agent-specific planning

The orchestrator builds the query/task plan for the selected route. Job mode uses the discovery engine and runtime state machine. Research and market modes use their multi-wave engines. People mode uses people and contact discovery.

### 5. Retrieval and extraction

The common substrate is:

```text
SearchTool → FetchTool → ExtractTool → VerifyTool → DedupeTool → ExportTool
```

Each tool returns structured success/failure information and contributes to counters, warnings, and tool-event telemetry.

### 6. Persistence

Source documents, extracted records, field evidence, run counters, warnings, checkpoints, and exports are persisted to SQLite. The frontend can refresh and recover results from the database after the active page changes.

### 7. Completion

The run finishes as `completed`, `partial`, `failed`, or `cancelled`. A partial run is a valid outcome when the system found some evidence but hit source, time, or budget limits.

## Job Discovery Pipeline

The job route is the most stateful execution path in the project.

### Request understanding

`QueryUnderstandingAgent` extracts and preserves:

- Requested title and role-family alternatives.
- Locations and location operator, including `OR` semantics.
- Remote preference.
- Experience bounds.
- Explicit versus inferred skills.
- Salary floor where provided.
- Excluded titles and companies where supported.

`QueryExpansionAgent` adds related titles and search keywords without replacing the canonical request.

### Planning and source coverage

`DiscoveryEngine` creates search tasks for:

- Direct ATS platforms.
- Search-engine discovery.
- Company career pages.
- Regional job boards and source adapters.
- Follow-up queries based on discovered evidence.

The system prefers search-before-crawl when direct URLs are blocked or unproductive, and it persists the run budget rather than silently expanding it.

### Filtering order

The intended order is:

```text
Candidate URL
  → URL/login/board-root validation
  → fetch and job-page validation
  → normalize title/company/location/skills
  → hard qualification filters
  → deduplication
  → relevance scoring and ranking
  → verification and export
```

Hard filters cannot be rescued by a high ranking score. Missing inferred skills can reduce ranking, but missing explicit skills can disqualify a candidate.

### Freshness

Freshness is extracted from structured metadata and page text where possible. Values such as “0 sec ago,” “just now,” “today,” and relative timestamps are preserved as structured fields and used for ranking/filtering.

### Direct application links

When both an aggregator URL and a direct ATS/application URL are available, the direct application URL is preferred while source evidence remains preserved.

## FreeHire Integration Reference

FreeHire is an external job-search service and a separately maintained MIT-licensed Go application. DataHunt integrates its read-only HTTP API; running DataHunt does not install FreeHire's Go backend, PostgreSQL database, Meilisearch index, or Svelte UI.

Implementation: [`FreeHireSearchProvider` in `datahunt/tools/search.py`](datahunt/tools/search.py). Public exports are available from `datahunt.tools`, and `SearchProviderRegistry` recognizes both `freehire` and `fh`.

### Enable or disable

Set the four [FreeHire environment variables](#freehire-settings), then restart the application. To update a running service's settings for new provider instances:

```bash
curl -X POST http://127.0.0.1:8000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"settings":{"freehire_enabled":true,"freehire_max_requests_per_run":20}}'
```

Use `false` to disable. Existing orchestrators retain providers initialized with their previous configuration. `freehire_configured` in the settings response means a key is present; it does not mean the integration is enabled or the service is reachable. Keyless requests are possible when allowed by FreeHire's service terms.

### Search order and fallback

```text
HybridSearchProvider.search(query)
  ├─ Job-keyword match + FreeHire enabled → FreeHire catalogue
  │    └─ Enough hits? Return them
  ├─ DuckDuckGo → fill remaining slots
  │    └─ Any catalogue/web hits? Return them
  ├─ Job query → direct public job-board providers
  ├─ LLM-assisted candidate discovery, when configured
  └─ Empty result; mock fallback only when explicitly enabled for testing
```

The job-keyword heuristic checks query text rather than the orchestrator's mode. A research query containing words such as `engineer`, `developer`, or `architect` can therefore reach FreeHire when enabled. Disable FreeHire for a session that needs exclusively general research sources.

### Request mapping

| DataHunt input | FreeHire request behavior |
| --- | --- |
| `query` | Remove `site:` operators, supported experience ranges, and selected search noise words; send remaining text as `q` |
| Searchable fields | `q_fields=title,company,description,location` |
| Country aliases | Repeated `countries` parameters using lower-case ISO codes |
| `0-6 years`, `0–6 years`, `0 to 6 years` | `experience_years_min=0`, `experience_years_max=6` |
| Positive `freshness_days` | `posted_within_days` |
| `limit` | Clamped to `1–100` per API request |
| `page` | `offset=(page-1)*clamped_limit`, minimum zero |
| Sorting | `sort=posted_at`, `order=desc` |
| Optional API key | `Authorization: Bearer ...` |

Country aliases currently cover Saudi Arabia/KSA, UAE, Qatar, Kuwait, Bahrain, Oman, India, UK, Singapore, Canada, and USA. Cities remain free-text terms; the adapter does not resolve city facets. Country words also remain in `q`, so complex multi-country sentences may be more restrictive than the country facet alone. This lightweight translation is not a complete natural-language role/location parser.

Each call performs at most one HTTP request, with an HTTPX timeout derived from `READ_TIMEOUT_SECONDS` and clamped to 3–10 seconds. A thread lock protects request reservation. Errors, HTTP `429`, and other HTTP error statuses return an empty provider result so hybrid search can continue. There is no adapter-level retry or `Retry-After` scheduler.

Despite the variable name `FREEHIRE_MAX_REQUESTS_PER_RUN`, the counter belongs to the provider instance. Fresh CLI/API orchestrators normally create fresh counters; reusing a Python agent or orchestrator shares the cap across its runs. The cap is not persisted across process restarts and is not a global rate limiter.

### Returned data and qualification

- The employer URL becomes the candidate URL when supplied; otherwise a public slug produces `https://freehire.me/jobs/{slug}`.
- Titles include the company when present. The snippet contains company/location context plus up to 1,200 characters of HTML-stripped description.
- `source_domain` identifies `freehire.me`, and `provider_citation_id` preserves `freehire:{public_slug}` when available.
- Allowed/blocked domains are checked against returned URLs. Explicitly blocking `freehire.me` also skips this provider.
- Results are discovery candidates. The adapter does not mark them verified or bypass DataHunt's downstream fetch, qualification, and deduplication steps.
- Salary, complete description, publication timestamps, and enrichment fields are not mapped into structured job records directly by this adapter. A candidate link still needs downstream evidence collection.

### Programmatic catalogue-only lookup

The following makes one external search request. It returns `SearchHit` objects, not persisted or verified records:

```python
from datahunt.tools import FreeHireSearchProvider

provider = FreeHireSearchProvider(enabled=True, max_requests=1)
hits = provider.search(
    "AI Engineer jobs in UAE",
    limit=5,
    page=1,
    freshness_days=7,
)
for hit in hits:
    print(hit.to_dict())
print("Requests used:", provider.requests_used)
```

For the normal persisted workflow, use `--agent jobs` or `agent_mode="jobs"` with FreeHire enabled instead of calling the provider directly.

### Upstream references and usage terms

- [FreeHire source repository](https://github.com/strelov1/freehire)
- [Public API schema](https://github.com/strelov1/freehire/blob/main/web/static/openapi.yaml)
- [Hosted-service data licence](https://freehire.me/terms#data-licence)

The upstream code's MIT licence and the hosted catalogue's data-use terms are separate. FreeHire's published API contract restricts keyless access to personal/non-commercial use and requires an appropriate API plan for displaying postings in a product offered to others. No FreeHire application-submission, CV-upload, or account-management endpoints are called by this integration.

## Research and Market Pipelines

### Research waves

The research engine is designed around multiple source waves:

1. Official documentation, standards, RFCs, and primary sources.
2. Deep architecture, implementation, benchmarks, and comparative material.
3. Contradiction, limitations, known failures, and counter-evidence.

Research records are synthesized into Markdown or other export formats with visible source URLs and evidence relationships.

### Market behavior

The market engine supports evidence collection around:

- Market regime and benchmark context.
- Sector/industry signals.
- Candidate discovery.
- Deterministic technical indicators such as SMA, EMA, RSI, MACD, and ATR.
- Bearish-risk and counter-evidence searches.
- Source corroboration and watchlist separation.

Do not treat generated market output as investment advice. The code can calculate indicators deterministically, but the quality of any market conclusion still depends on current public source availability and the configured data path.

## People, Company, and Interview Intelligence

### People discovery

The people workflow searches public professional profiles and classifies evidence-backed roles such as recruiter, talent acquisition, hiring manager, AI leader, engineering leader, executive, or researcher.

### Contact discovery

The contact workflow accepts:

- Official contact/careers/about links.
- Public business emails found in fetched public pages and passing `business_public_only` policy checks.

It does not infer personal addresses from naming conventions.

### Company profiles

`POST /api/company/research` searches public company references, uses an LLM when configured, and falls back to an evidence-backed profile with `Unknown`/`unavailable` values where appropriate. Verified profiles are cached in `company_research_profiles`.

### Interview preparation

`POST /api/interview/prep` returns structured technical, behavioral, role-specific, company-specific, and interviewer questions. `POST /api/interview/evaluate` scores and critiques practice answers, using deterministic fallback feedback when no live model is available.

The company and interview API handlers currently construct `NvidiaClient` directly. Changing `LLM_PROVIDER` to Gemini does not switch those specific endpoints; general task orchestration has separate provider routing.

## Persistence and Data Model

DataHunt uses SQLite with migrations under `migrations/`. The default path is `./data/datahunt.sqlite3`.

### Core tables

| Table | Purpose |
| --- | --- |
| `research_tasks` | Original request and normalized specification |
| `research_runs` | Run status, budgets, counters, warnings, and errors |
| `source_documents` | Requested/canonical URLs, retrieval status, text, hashes, and policy flags |
| `extracted_records` | Structured records and verification status |
| `record_evidence` | Field-level evidence linked to source documents |
| `exports` | Generated artifact metadata and SHA-256 values |
| `tool_events` | Tool execution audit events |
| `job_application_status` | Applied/interview/offered/rejected state |
| `saved_jobs` | User-saved jobs and notes |
| `ignored_jobs` | User negative signals and reasons |
| `user_preferences` | Learned title, company, location, skill, and salary preferences |
| `interview_prep_sessions` | Interview preparation and feedback state |
| `company_research_profiles` | Cached company intelligence profiles |
| `job_change_events` | Saved-job closure/removal/update events |
| `discovery_schedules` | Persisted recurring-discovery metadata |
| `run_checkpoints` | Durable runtime checkpoints for resumable execution |
| `schema_migrations` | Applied SQL migration filenames and timestamps |
| `system_settings` | Editable configuration overrides; created by `SettingsRepository` |

### Migrations

The current migration sequence includes:

1. Initial tasks, runs, documents, records, evidence, exports, and tool events.
2. Job application tracking.
3. Markdown/DOCX export format support.
4. Saved jobs and user preferences.
5. Extended application tracking and interview preparation.
6. Continuous monitoring and company profiles.
7. Durable run checkpoints.

Run migrations with:

```bash
python -m datahunt.cli migrate
```

## Exports

The `ExportTool` and `/runs/{run_id}/export` endpoint support:

- JSON for machine-readable records and evidence.
- CSV for flat tables.
- XLSX for spreadsheet workflows.
- Markdown for dossiers and reports.
- DOCX for editable document delivery.

Generated files are written under `EXPORT_DIR`. The database stores the export path/key, format, row count, run relationship, and hash metadata.

Use `/exports/{export_id}/download` for a known stored artifact or `/runs/{run_id}/export?format=xlsx` to request a format. The latter reuses an existing file of the same format when available. Export downloads depend on both the database row and the local file; keeping only one is insufficient for recovery.

## Security and Trust Boundaries

DataHunt handles untrusted URLs, public web content, model output, and user-provided search directives. The important controls are:

### SSRF and fetch controls

`datahunt/policy.py` and `datahunt/tools/fetch.py` enforce URL and network policy before retrieval. Fetches are bounded by:

- Allowed and blocked domains.
- Redirect limits.
- Connect/read/total timeouts.
- Response-size ceilings.
- Extracted-text ceilings.
- DNS and private-network protections where implemented by the fetch policy.

### Evidence and model boundaries

- Model output is parsed into Pydantic/domain structures instead of being treated as arbitrary trusted state.
- Unsupported fields should remain empty, `Unknown`, or `unavailable`.
- Source URLs and snippets are retained for later inspection.
- Verification status is distinct from confidence; a high confidence value must not bypass hard policy failures.

### Contact and privacy controls

- The default contact policy is `business_public_only`.
- Private or inferred contact details must not be generated.
- Suppression entries and contact safety checks are part of the persistence/policy layer.

### API deployment

The service has no per-user ownership model. WebSocket events are broadcast to all connected clients without per-user isolation, and a single API key grants access to all protected resources. Use a network/authentication boundary around the entire application when sharing it beyond localhost.

### What DataHunt does not do

- It does not solve CAPTCHAs.
- It does not bypass authentication or access private accounts.
- It has no configured proxy-rotation service. The current crawler includes a `DDGS.extract` fallback after selected HTTP failures, described in its code/logging as TLS browser impersonation; this is separate from the read-only FreeHire adapter.
- It does not automatically submit job applications.
- It does not turn inferred personal contact data into “verified” data.

## Docker

### Build the image

The Dockerfile installs `requirements.txt` and copies the checkout into `/app`. The checkout currently has no `.dockerignore`; exclude private `.env`, database, and export files from the build context before distributing an image.

```bash
docker build -t datahunt-agent .
```

### Initialize the mounted database

The container entrypoint starts Uvicorn; it does not apply migrations. Initialize the persistent data directory before starting the server:

```bash
docker run --rm \
  -e DATABASE_PATH=/app/data/datahunt.sqlite3 \
  -v "$(pwd)/data:/app/data" \
  datahunt-agent python -m datahunt.cli migrate
```

Container commands in this section use Bash path syntax. On PowerShell use an absolute host path or `${PWD}` in bind-mount arguments.

### Run the image

```bash
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -e LLM_PROVIDER=nvidia \
  -e NVIDIA_API_KEY="$NVIDIA_API_KEY" \
  -e DATABASE_PATH=/app/data/datahunt.sqlite3 \
  -e EXPORT_DIR=/app/exports \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/exports:/app/exports" \
  datahunt-agent
```

The image runs:

```text
uvicorn datahunt.api:app --host 0.0.0.0 --port 8000
```

### Docker Compose

Review and correct the persistence mapping described below, then initialize and launch:

```bash
docker compose build
docker compose run --rm datahunt-agent python -m datahunt.cli migrate
docker compose up --build
```

The checked-in Compose file sets `DATABASE_PATH=/app/data/datahunt.sqlite3`, `EXPORT_DIR=/app/exports`, uses named persistent volumes, and includes a healthcheck. Initialize its database before the first launch:

```bash
docker compose build
docker compose run --rm datahunt-agent python -m datahunt.cli migrate
docker compose up -d
```

Do not commit API keys into Compose files. Use an `.env` file excluded by Git or your deployment secret store.

## Operations and Recovery

### Process model

Use one Uvicorn worker for the current local workstation architecture. Background tasks, WebSocket clients, provider counters, and caches are process-local. Adding workers does not create a shared task queue or shared event stream.

- `--reload` is useful during development but reloads interrupt active in-process work.
- Closing a browser tab does not stop a detached WebSocket run.
- A persisted checkpoint can support runtime recovery paths, but the API does not automatically reschedule every interrupted run after restart.
- `discovery_schedules` stores scheduling metadata; there is no standalone scheduler command in the public CLI.
- Run `POST /api/monitor/check` to explicitly recheck saved postings.

### Inspect an incomplete run

1. Read `/runs/{run_id}` for status, counters, warnings, and `error_code`/`error_message`.
2. Read `/runs/{run_id}/records` to see records persisted before interruption, including review/rejected states.
3. Check service logs and the configured source/model availability.
4. Start a new task when a fresh run is needed. Reusing an internal runtime checkpoint is a Python-level workflow, not a documented REST resume operation.

Persisted run states include `planned`, `searching`, `collecting`, `extracting`, `verifying`, `deduplicating`, `exporting`, `completed`, `partial`, `failed`, and `cancelled`. The exact phases visited depend on the selected engine.

### Logs and tracing

`datahunt/logger.py` emits JSON logs to stdout with UTC timestamps, levels, messages, and optional run/tool context. Its formatter redacts recognized token, email, and phone patterns. Redaction is pattern-based; inspect a diagnostic log before sharing it.

`LOG_LEVEL` is a configuration field, but `get_logger()` currently initializes the application logger at `INFO` rather than reading that field. In a Python diagnostic session, set `logger.setLevel(logging.DEBUG)` explicitly if needed. FreeHire HTTP failures other than `429` are logged at debug level.

LangSmith is optional and sends trace data to an external service when configured. `/telemetry/runs` reads the configured project; SQLite remains the source of local task/run/record state.

### Backup and restore

Back up the configured SQLite database and `EXPORT_DIR` together. Settings updates can store provider keys in `system_settings`, so treat the database and `.env` as private configuration as well as application data.

For a straightforward cold backup:

1. Wait for active runs to finish and stop every DataHunt process using the database.
2. Copy the database and any remaining adjacent `-wal` and `-shm` files, plus the export directory, into a private backup directory.
3. Store `.env` separately with equivalent access restrictions.
4. Restart the application.

For backups while the database is live, use SQLite's backup API rather than copying only the main database file. WAL mode can leave committed data in the write-ahead log.

To restore, stop the service, restore the matching data/export snapshot at the configured paths, and restart. Export rows store file paths: moving a backup to a different filesystem layout can leave download references pointing to old locations. `/health` checks connectivity, not backup completeness or migration correctness.

### Updating a checkout

1. Preserve local work and take a data/configuration backup.
2. Review dependency and migration changes.
3. Install the updated `requirements.txt` in the same virtual environment.
4. Run `python -m datahunt.cli migrate` before starting the updated application.
5. Check `/health`, list recent runs, and open an existing export.

Migrations are recorded by filename and are designed to run once. Do not edit already-applied migrations as an upgrade mechanism; add a subsequent migration instead. There is no public down-migration command.

## Testing and Quality Checks

### Run the full test suite

```bash
python -m pytest -q
```

### Run focused suites

```bash
python -m pytest -q tests/test_agent_directory.py
python -m pytest -q tests/test_job_agent_e2e.py
python -m pytest -q tests/test_research_discovery_engine.py
python -m pytest -q tests/test_market_agent.py
python -m pytest -q tests/test_api.py
python -m pytest -q tests/test_freehire_search.py
```

### Compile-check Python files

```bash
python -m compileall -q agent datahunt tests
```

### Check patch whitespace

```bash
git diff --check
```

### Lint

If Ruff is installed:

```bash
ruff check agent datahunt tests
```

The repository currently contains pre-existing Ruff/style debt in legacy files. Treat a full-repository lint failure separately from failures in the files changed by a focused task.

### Latest local verification snapshot

Recorded on **2026-10-26** after production-readiness hardening:

| Check | Recorded result |
| --- | --- |
| Full `python -m pytest -q` | 740 passed |
| Production-readiness, API, configuration, and registry focused checks | 21 passed |
| Python compilation and patch whitespace | Passed |
| README structure/link/example validation | Passed |
| Compose structure validation | Passed |

These are local results, not a CI badge or a current source-availability guarantee. FreeHire's dedicated tests mock HTTP; the production suite does not prove that external providers, model keys, or public job sources are reachable in deployment.

## Troubleshooting

### The service starts but `/health` reports a degraded database

Check the configured path and directory permissions:

```bash
python - <<'PY'
from datahunt.config import settings
print(settings.DATABASE_PATH)
print(settings.EXPORT_DIR)
PY
```

Then run:

```bash
python -m datahunt.cli migrate
```

### The model is not configured

Check the effective provider without printing secrets:

```bash
curl http://127.0.0.1:8000/health
```

Set exactly one provider explicitly:

```dotenv
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=...
```

or:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

Restart the process after changing provider variables.

### FreeHire is enabled but no jobs appear

Check `/api/settings` for `freehire_enabled`, `freehire_api_url`, and the request cap. A false `freehire_configured` only means no API key is present; it does not disable keyless searches.

Other causes include exhausted instance budgets, upstream throttling, domain filtering, restrictive query text, or downstream job rejection. The provider returns an empty list on HTTP/parse/connection failures, allowing the other providers to continue. A successful run therefore does not prove that FreeHire contributed results. Inspect candidate provenance and debug logs when investigating coverage.

### Editing `.env` does not change behavior

Stored `system_settings` overrides are loaded after environment settings. Read the effective settings response and update the relevant editable field through `/api/settings`, or remove its database override deliberately. Restart to recreate provider instances. The global reset removes editable non-secret configuration lines; provider keys remain environment/secret-manager values.

### PowerShell activation is blocked

Activation is optional. Invoke `.\.venv\Scripts\python.exe` directly as shown in [Quick Start](#quick-start), using the same interpreter for installation, migrations, tests, and Uvicorn.

### The port is already in use

Start Uvicorn on another local port, for example `--port 8001`, and open `http://127.0.0.1:8001/`. The Windows launcher is fixed to port 8000; use the direct command for another port.

### Search returns zero or partial results

This can be correct behavior. Public sources may:

- Return HTTP `403` or `429`.
- Require JavaScript rendering.
- Redirect to login pages.
- Expose stale or closed postings.
- Have no matching records in the requested geography.

Inspect the run warnings, counters, source documents, and planned queries instead of assuming the search loop failed.

### A job is rejected even though it looks related

Check the hard qualification policy. Explicit skills, geography, title exclusions, source validity, expired-page detection, and direct-posting checks occur before ranking. A high relevance score cannot rescue a hard rejection by design.

### API requests return `401`

If `DATAHUNT_API_KEY` is set, send either `X-API-Key` or a Bearer token. Do not paste keys into shell history when avoidable; use environment variables or a secret manager.

### The UI shows stale assets

The API sets no-cache headers for frontend assets and run endpoints, but browser tabs can still hold old JavaScript. Hard-refresh the page or open a new tab after changing `frontend/` files.

### Docker writes data to the wrong location

Use `DATABASE_PATH=/app/data/datahunt.sqlite3` and persist `/app/data`, as shown in [Docker](#docker). `DATAHUNT_DB_PATH` is not a supported setting.

## Known Gaps and Caveats

These are implementation facts worth knowing before deploying:

1. **Version source duplication:** `datahunt/__init__.py` and `pyproject.toml` currently both report `0.2.0`, but releases must keep these two declarations synchronized.
2. **Environment-example drift:** `.env.example` still uses some older Gemini/default limit values. `datahunt/config.py` is the runtime source of truth.
3. **Compose storage model:** Compose uses named volumes for database and exports. Back them up with Docker volume tooling or replace them with deliberate host mounts and matching permissions.
4. **People UI exposure:** The People agent is wired into the Python factory, CLI, API mode, and orchestrator, but the central HTML hub does not yet have a dedicated People navigation card.
5. **Market routing is equity-first:** Explicit `market` mode routes through the market discovery engine. SaaS/competitive helper code exists, but market-intent routing continues to evolve.
6. **External-source volatility:** Search engines and public career pages can throttle, block, or change structure without notice.
7. **Full lint debt:** The full repository has unrelated existing Ruff findings; passing tests do not imply a clean lint baseline.
8. **No automatic applications:** Job records and application tracking are supported, but submitting an application remains a user action.
9. **FreeHire integration scope:** Query translation uses a small alias/regex mapping, retains country words in full text, and uses a query-keyword heuristic rather than an explicit agent-mode argument. The request cap is per provider instance, not a durable per-run/global quota.
10. **Settings/runtime boundaries:** Provider keys are environment/secret-manager configuration and cannot be changed through the settings API. FreeHire provider instances and the concurrent-run semaphore are initialized at process startup; restart after configuration changes.
11. **Local-service authentication limits:** A configured API key is shared by all callers; there is no per-user ownership or tenant isolation, and WebSocket events broadcast across connected clients.
12. **Background execution:** Work runs inside the server process; checkpoints do not provide a durable queue or automatic restart scheduling. Use one worker for consistent local telemetry.
13. **Provider selection in auxiliary endpoints:** Company research and interview endpoints instantiate NVIDIA directly, independently of the general task provider choice.
14. **Deployment verification:** The test suite does not validate live provider credentials, external source availability, container image builds, or volume restore procedures.

## Contributing

Before changing code:

1. Read `AGENTS.md` and preserve unrelated worktree changes.
2. Inspect callers, tests, configuration, exports, and persistence implications.
3. Prefer existing tools, models, policies, and agent patterns.
4. Add a focused regression test for non-trivial behavior.
5. Run the smallest meaningful check, then the full suite when the change affects shared orchestration.
6. Keep credentials, tokens, private payloads, local databases, and generated exports out of commits.

Typical workflow:

```bash
git status --short
python -m pytest -q tests/test_relevant_area.py
python -m compileall -q agent datahunt tests
git diff --check
git diff -- path/to/changed/file.py
```

Keep commits focused and use conventional prefixes such as `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, or `chore:` when committing is requested.

### Extending the application

| Change | Main integration points | Useful checks |
| --- | --- | --- |
| Add a search source | `SearchProvider`/`SearchHit`, hybrid routing, provider registry, config, exports | Provider mapping/error tests, domain-policy and fallback tests |
| Add an agent mode | `agent/` wrapper and factory, orchestrator routing, CLI/API input, optional frontend entry | `tests/test_agent_directory.py` and route-specific tests |
| Change job qualification | Canonical query/spec models, `datahunt/agent/policies.py`, runtime and extraction callers | Related/unrelated role, geography, experience, and explicit-skill regressions |
| Change stored records | Domain models, repositories, a new ordered SQL migration | Migration/idempotence, persistence, API, and export tests |
| Change report presentation | `research_report.py`, `market_report.py`, compatible engine wrappers and export formatting | Report evidence/format assertions |

Inject mock search/model dependencies for deterministic tests. Keep credentials in private local configuration, and use fixture job/company data rather than copying production exports into tests.

## Further Documentation

- [Architecture notes](docs/Architecture.md)
- [Database design](docs/Database.md)
- [Security notes](docs/Security.md)
- [Error handling](docs/Error-handling.md)
- [Prompts](docs/Prompts.md)
- [Project phases](docs/Phases.md)
- [Job discovery audit](docs/JOB_DISCOVERY_AUDIT.md)
- [Repository contribution instructions](AGENTS.md)

Design notes may describe intended behavior beyond the current implementation. For exact runtime contracts, consult `datahunt/config.py`, `datahunt/api.py`, `datahunt/models/`, and the generated `/openapi.json`.

## License

DataHunt is distributed under the [Apache License 2.0](LICENSE).

FreeHire is a separate [MIT-licensed project](https://github.com/strelov1/freehire). Its hosted API/data terms and third-party source terms apply independently of DataHunt's code licence.
