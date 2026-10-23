# DataHunt

DataHunt is a Python-based, multi-agent web research and job-intelligence workstation. It accepts a natural-language directive, classifies the request, plans bounded web searches, fetches public sources, extracts structured records, verifies evidence, deduplicates results, persists the run to SQLite, and exports the result as JSON, CSV, XLSX, Markdown, or DOCX.

It can be used through:

- A FastAPI service with Swagger/OpenAPI documentation.
- A cyberpunk-style browser hub and real-time cockpit.
- A Rich command-line interface.
- Python agent wrappers such as `get_agent("jobs")` and `get_agent("people")`.

> **Status:** The repository is an active engineering workspace. The core test suite is passing, but some documentation, Docker defaults, and UI surfaces are still being aligned with the evolving implementation. See [Known Gaps and Caveats](#known-gaps-and-caveats).

## Table of Contents

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
- [Research and Market Pipelines](#research-and-market-pipelines)
- [People, Company, and Interview Intelligence](#people-company-and-interview-intelligence)
- [Persistence and Data Model](#persistence-and-data-model)
- [Exports](#exports)
- [Security and Trust Boundaries](#security-and-trust-boundaries)
- [Docker](#docker)
- [Testing and Quality Checks](#testing-and-quality-checks)
- [Troubleshooting](#troubleshooting)
- [Known Gaps and Caveats](#known-gaps-and-caveats)
- [Contributing](#contributing)
- [License](#license)

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
│ Discovery engines             │  │ Specialized agents          │
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

The launcher attempts to activate `.venv` or `venv`, creates `.env` from `.env.example` when missing, runs migrations, starts Uvicorn on port `8000`, waits for `/health`, and opens the hub in the default browser.

## Configuration

Configuration is loaded from `.env` by `datahunt/config.py`. Settings can also be updated through the `/api/settings` endpoints and persisted to SQLite plus `.env` for the editable settings supported by the application.

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

Health, API metadata, documentation, frontend assets, and settings routes are intentionally treated differently by the current middleware. Do not expose an unauthenticated deployment to the public internet without reviewing `datahunt/api.py` and adding a reverse proxy/authentication boundary appropriate for the deployment.

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

Reset editable settings:

```bash
curl -X POST http://127.0.0.1:8000/api/settings/reset
```

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

### `POST /tasks` request fields

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `query` | string | required | Natural-language directive |
| `max_records` | integer | `150` at the API boundary | Must be at least `1`; bounded by configured/runtime limits |
| `freshness_days` | integer or null | `7` | Used by normalization and source planning |
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

The built-in API key middleware is useful for local or simple deployments, but it is not a complete production identity system. For production exposure, place the service behind an authenticated reverse proxy or an identity-aware gateway, restrict network access, and rotate credentials through a secret manager.

### What DataHunt does not do

- It does not solve CAPTCHAs.
- It does not bypass authentication or access private accounts.
- It does not use stealth/proxy rotation as a scraping strategy.
- It does not automatically submit job applications.
- It does not turn inferred personal contact data into “verified” data.

## Docker

### Build the image

```bash
docker build -t datahunt-agent .
```

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

```bash
docker compose up --build
```

The checked-in Compose file is useful as a starting point, but review its environment and volume mappings before relying on it for persistence. The current Python settings read `DATABASE_PATH`, while the Compose file contains a legacy `DATAHUNT_DB_PATH` variable and mounts a root-level `datahunt.db`. To make persistence explicit, prefer a Compose override or environment values equivalent to:

```yaml
environment:
  DATABASE_PATH: /app/data/datahunt.sqlite3
  EXPORT_DIR: /app/exports
volumes:
  - ./data:/app/data
  - ./exports:/app/exports
```

Do not commit API keys into Compose files. Use an `.env` file excluded by Git or your deployment secret store.

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

At the time this README was updated, the full local test command completed with **730 passed**. Re-run the command in your checkout; test counts and external-source behavior can change as the repository evolves.

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

Use `DATABASE_PATH=/app/data/datahunt.sqlite3` and mount `/app/data`, as shown in [Docker](#docker). `DATAHUNT_DB_PATH` is not the setting read by the current Python configuration.

## Known Gaps and Caveats

These are implementation facts worth knowing before deploying:

1. **Runtime/package version mismatch:** `datahunt/__init__.py` reports `0.1.0`, while `pyproject.toml` declares `0.2.0`.
2. **Environment-example drift:** `.env.example` still uses some older Gemini/default limit values. `datahunt/config.py` is the runtime source of truth.
3. **Compose persistence drift:** The checked-in Compose file uses a legacy database variable/mount arrangement. Set `DATABASE_PATH` explicitly for reliable persistence.
4. **People UI exposure:** The People agent is wired into the Python factory, CLI, API mode, and orchestrator, but the central HTML hub does not yet have a dedicated People navigation card.
5. **Market routing is equity-first:** Explicit `market` mode routes through the market discovery engine. SaaS/competitive helper code exists, but market-intent routing continues to evolve.
6. **External-source volatility:** Search engines and public career pages can throttle, block, or change structure without notice.
7. **Full lint debt:** The full repository has unrelated existing Ruff findings; passing tests do not imply a clean lint baseline.
8. **No automatic applications:** Job records and application tracking are supported, but submitting an application remains a user action.

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

## License

DataHunt is distributed under the [Apache License 2.0](LICENSE).
