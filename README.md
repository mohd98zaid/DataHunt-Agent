# DataHunt — Evidence-Backed Autonomous Multi-Agent Research Workstation

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-93%20Passed-brightgreen.svg)]()
[![Storage](https://img.shields.io/badge/Storage-SQLite%20WAL-orange.svg)]()
[![AI Engine](https://img.shields.io/badge/LLM%20Pool-Google%20Gemini%20Multi--Model-4285F4.svg)]()
[![Interface](https://img.shields.io/badge/UI-3D%20Three.js%20HUD-purple.svg)]()

**DataHunt** is an evidence-first, multi-agent autonomous research and intelligence suite engineered for high reliability, strict defensive security, structured data extraction, and deep market/career analysis.

Whether querying *"Find GenAI Engineer jobs in Saudi or UAE with 0-5 years experience"* or *"Analyze LangChain Expression Language LCEL architecture and runnables"*, DataHunt autonomously plans search strategies, bypasses anti-bot defenses using TLS browser impersonation, harvests authoritative ATS portals, enforces field-level quotation verification against raw documents, and persists results to an interactive 3D WebGL cockpit and Kanban job tracker.

---

## System Architecture

```text
User Request (Web HUD / REST API / CLI)
          │
          ▼
┌────────────────────────────────────────┐
│  Intake & Security Boundary            │  ◄── SSRF verification, domain policy & quota limits
└──────────────────┬─────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────┐
│  Research Orchestrator (State Machine) │  ◄── Dynamic phase loop & task lifecycle
└──────┬───────────────────────┬─────────┘
       │                       │
       ▼                       ▼
┌──────────────────────┐  ┌───────────────────────────────────┐
│ Gemini Model Pool    │  │ SQLite WAL Datastore              │
│ • Round-Robin        │  │ • Tasks, Runs, Evidence, Documents│
│ • 503/429 Cooldown   │  │ • Job Application Tracker         │
│ • Zero-Latency Shift │  └───────────────────────────────────┘
└──────┬───────────────┘
       │
 ┌─────┴───────────────────────────────────────────────────────┐
 │ Acquisition & Extraction Engine                             │
 │ • Hybrid Search (DuckDuckGo + Regional ATS + LLM Grounding) │
 │ • Safe Fetcher (DNS pin, SSRF shield, TLS browser profile)  │
 │ • Deterministic ATS Parsers (Greenhouse, Lever, Bayt, etc.) │
 │ • LLM Structured Extraction (JSON Schema constrained)       │
 └─────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────────┐
│  Field-Level Verification Engine       │  ◄── Verifies quotes & character locators in raw text
└──────────────────┬─────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────┐
│  Deterministic Deduplication Engine    │  ◄── Canonical URL normalization & evidence merge
└──────────────────┬─────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────┐
│  Cryptographic Exporter                │  ◄── Formula escaping (CSV/XLSX), JSON, Markdown, Word
└────────────────────────────────────────┘
```

---

## Key Features & Capabilities

### 1. Free-Tier Multi-Model Pool Load Balancer
- **Multi-Model Routing**: Distributes requests across Gemini models (`gemini-flash-latest`, `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-flash-lite-latest`, etc.) via thread-safe round-robin rotation.
- **Intelligent Cooldown & Zero-Latency Failover**: Catches Google `503 UNAVAILABLE` (high server demand) and `429 RESOURCE_EXHAUSTED` (rate limits) and automatically places cooling windows (30s/60s), instantly failing over to healthy alternative models without interrupting workflows.
- **Burst Pacing**: Enforces micro-pacing intervals between requests to eliminate instantaneous rate limit triggers.

### 2. Autonomous Specialized Agents
- **Job Hunt AI Agent (`mode: jobs`)**:
  - Sub-second (0-sec) career harvesting from modern ATS platforms (Greenhouse, Lever, Ashby, Workable) and regional portals (Bayt, GulfTalent, Indeed).
  - Extracts direct application URLs, parses freshness badges (`0s ago`, `just now`, `today`), and filters by location compliance.
- **Deep Research AI Agent (`mode: research`)**:
  - De-noises documentation and extracts verbatim code patterns, specifications, and components.
  - Synthesizes 7-section publication-grade Executive Research Dossiers with ASCII architecture diagrams and verified citations.
- **Market Intel AI Agent (`mode: market`)**:
  - Researches competitor landscapes, SaaS pricing tiers, feature matrices, and target audience differentiators.

### 3. Anti-Bot Bypass & TLS Browser Impersonation
- Overcomes Cloudflare and anti-bot HTTP 403 blocks on regional job boards using browser TLS fingerprint impersonation.
- Pre-fetches and in-memory caches full job descriptions to maximize scraping yield without re-fetching blocked endpoints.

### 4. Interactive Cybernetic Web Workstation
- **Radar Cockpit (`/cockpit.html`)**: 3D tactical radar interface powered by Three.js WebGL, real-time WebSocket telemetry, live terminal logs, and interactive dossier tabs.
- **Job Tracker Kanban (`/jobs.html`)**: Persistent job application dashboard with salary filters, live search, and interview status tracking.
- **One-Click Apply Auto-Sync**: Clicking `APPLY ➔` opens the ATS portal in a new tab while automatically updating the status from **⚪ Not Applied** to **🔵 Applied**, refreshing counter pills, and persisting the timestamp into SQLite.
- **Interactive REST API (`/docs`)**: OpenAPI/Swagger documentation for automation and external integrations.

### 5. Enterprise-Grade Security
- **Deep SSRF & DNS Pinning**: Validates resolved IP addresses before establishing TCP connections and pins sockets against DNS rebinding (TOCTOU) attacks.
- **Formula Injection Shield**: Sanitizes spreadsheet cells starting with `=`, `+`, `-`, `@`, `\t`, or `\r` for CSV and Excel (.xlsx) downloads.
- **Path Traversal Protection**: Enforces strict filesystem sandboxing for export downloads.
- **Public Business Contact Policy**: Collects only official corporate contact inboxes (`careers@`, `info@`), automatically redacting personal contact information.

---

## Project Structure

```text
DataHunt-Agent/
├── agent/                   # 🤖 Specialized Autonomous Agents Package
│   ├── base.py              # BaseAgent abstract class
│   ├── orchestrator.py      # Core research workflow orchestrator
│   ├── job_agent.py         # 🎯 0-Sec Live ATS Job Radar Agent
│   ├── research_agent.py    # 🔬 Deep Technical Research & Dossier Agent
│   └── market_agent.py      # 📊 Market & Competitive Intelligence Agent
├── datahunt/                # 🧠 Core System Engine
│   ├── api.py               # FastAPI REST & WebSocket streaming server
│   ├── cli.py               # Rich CLI command suite
│   ├── config.py            # Typed settings & environment loading
│   ├── errors.py            # Error taxonomy, envelope & jitter backoff
│   ├── logger.py            # Structured logging & secret masking
│   ├── policy.py            # SSRF protection, DNS validation & formula escaping
│   ├── db/                  # SQLite schema, WAL connection & repositories
│   ├── llm/                 # Gemini API client, multi-model pool & prompts
│   ├── models/              # Pydantic schemas (Task, Run, Record, Evidence)
│   └── tools/               # Search, safe fetch, extract, and export tools
├── docs/                    # 📚 Technical Documentation & System Specifications
│   ├── Architecture.md      # Distributed architecture specifications
│   ├── Database.md          # Relational SQLite schema & WAL configuration
│   ├── Error-handling.md    # Error taxonomy and backoff algorithms
│   ├── Phases.md            # State machine lifecycle transitions
│   ├── Prompts.md           # Few-shot prompts & system instructions
│   └── Security.md          # SSRF defenses & formula injection shielding
├── frontend/                # 💻 High-Tech Cybernetic Interfaces
│   ├── index.html           # Central AI Agent Hub Homepage
│   ├── cockpit.html         # 3D Interactive Agent Workstation (Three.js HUD)
│   ├── jobs.html            # Persistent Job Application & Interview Kanban
│   ├── css/                 # Cyberpunk themes (hub.css, styles.css, jobs.css)
│   └── js/                  # Real-time WebSocket clients, jobs controller & 3D HUD
├── migrations/              # 🗄️ Idempotent SQLite schema migrations
├── tests/                   # 🧪 93 Automated unit, integration, and regression tests
├── data/                    # 💾 SQLite database storage (WAL mode)
├── exports/                 # 📥 Cryptographic SHA-256 exports (JSON, CSV, XLSX, MD, DOCX)
├── Dockerfile               # Container build configuration
├── docker-compose.yml       # Multi-service container orchestration
├── run_app.bat              # One-click Windows application launcher
├── start.bat                # Quick launch convenience script
├── requirements.txt         # Core dependencies
└── README.md                # Project documentation
```

---

## Getting Started

### ⚡ One-Click Launch (Windows)

Double-click or run from Command Prompt:
```cmd
run_app.bat
```
*This launcher verifies Python, activates your virtual environment, executes SQLite migrations, opens your default browser to `http://127.0.0.1:8000`, and starts the FastAPI server.*

---

### Manual Setup

#### 1. Prerequisites
- Python 3.10+
- Git

#### 2. Clone the Repository
```bash
git clone https://github.com/mohd98zaid/DataHunt-Agent.git
cd DataHunt-Agent
```

#### 3. Set Up Virtual Environment
```bash
# Linux/macOS:
python -m venv .venv
source .venv/bin/activate

# Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1
```

#### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 5. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Configure your `.env`:
```env
# Google Gemini API Key
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=auto

# Storage & Export Paths
DATABASE_PATH=./data/datahunt.sqlite3
EXPORT_DIR=./exports

# Resource Limits
MAX_RUN_SECONDS=600
MAX_SEARCH_QUERIES=30
MAX_PAGES_FETCHED=120
MAX_RECORDS=500

LOG_LEVEL=INFO
```

#### 6. Initialize Database
Apply idempotent SQLite migrations:
```bash
python -m datahunt.cli migrate
```

#### 7. Start Server
```bash
uvicorn datahunt.api:app --host 127.0.0.1 --port 8000 --reload
```

---

### 🐳 Docker Deployment

Run via Docker Compose:
```bash
docker compose up --build
```
Access the workstation at `http://localhost:8000`.

---

## CLI Usage

DataHunt includes a feature-rich CLI with styled Rich tables and live progress reporting.

```bash
# List available agents
python -m datahunt.cli agents

# Run Job Radar Agent targeting ATS portals
python -m datahunt.cli run "Find GenAI Engineer jobs in Saudi or UAE with 0-5 years experience" --agent jobs --format json

# Run Deep Research Agent with Markdown export
python -m datahunt.cli run "LangChain Expression Language LCEL architecture" --agent research --format md

# Run Market Intel Agent with Excel spreadsheet export
python -m datahunt.cli run "Compare vector databases: Pinecone vs Qdrant vs Weaviate" --agent market --format xlsx

# List historical runs
python -m datahunt.cli list --limit 10

# Inspect run details
python -m datahunt.cli status <run_id>
```

---

## Output Formats & Verification Citations

Export files are saved in `./exports/` with cryptographic SHA-256 digests tracked in SQLite:
- **Markdown (`.md`)**: Formatted technical dossiers with ASCII diagrams and verification tables.
- **Word Document (`.docx`)**: Formatted corporate reports with executive summaries and citations.
- **JSON (`.json`)**: Machine-readable records with full field evidence, raw quotes, and character locators.
- **CSV & Excel (`.csv`, `.xlsx`)**: Tabular data protected against spreadsheet formula injection.

---

## Testing & Quality Assurance

Run the automated test suite across all 93 unit, integration, and security tests:

```bash
python -m pytest tests/ -v
```

Expected output:
```text
================== 93 passed, 2 warnings in 64.14s (0:01:04) ==================
```

---

## License

This project is licensed under the [Apache 2.0 License](LICENSE).
