# DataHunt — Evidence-Backed Autonomous Multi-Agent Research & Discovery Workstation

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-210%20Passed%20%E2%80%A2%200%20Failures-brightgreen.svg)]()
[![Storage](https://img.shields.io/badge/Storage-SQLite%20WAL%20Mode-003B57.svg?style=flat&logo=sqlite&logoColor=white)]()
[![AI Engine](https://img.shields.io/badge/LLM%20Pool-Google%20Gemini%20Multi--Model-4285F4.svg?style=flat&logo=google&logoColor=white)]()
[![Interface](https://img.shields.io/badge/Frontend-3D%20WebGL%20Three.js%20HUD-000000.svg?style=flat&logo=three.js&logoColor=white)]()
[![FastAPI](https://img.shields.io/badge/API-FastAPI%20REST%20%2B%20WebSocket-009688.svg?style=flat&logo=fastapi&logoColor=white)](http://127.0.0.1:8000/docs)

**DataHunt** is an evidence-first, multi-agent autonomous research and intelligence engine engineered for deterministic execution, strict defensive security, structured data extraction, and deep market/career analysis.

Whether discovering *"GenAI Engineer jobs in Saudi Arabia or UAE with 0–6 years experience"*, comparing *"LangGraph vs. CrewAI for production agent orchestration"*, or uncovering *"Top 10 NSE momentum stocks with favorable technical setups"*, DataHunt autonomously discovers the relevant source universe, executes multi-wave adaptive investigations, enforces field-level verification against raw source documents, eliminates hallucinations, and streams live telemetry to an interactive 3D WebGL tactical cockpit and Kanban board.

---

## Table of Contents

- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [System Architecture](#system-architecture)
  - [High-Level Architectural Workflow](#high-level-architectural-workflow)
  - [Directory Structure](#directory-structure)
  - [Request Lifecycle & Execution Flow](#request-lifecycle--execution-flow)
  - [Database Schema (SQLite WAL)](#database-schema-sqlite-wal)
- [The Three Autonomous Engines](#the-three-autonomous-engines)
  - [1. Job Hunt AI Agent (P0 — Highest Priority)](#1-job-hunt-ai-agent-p0--highest-priority)
  - [2. Deep Technical Research Agent (P1)](#2-deep-technical-research-agent-p1)
  - [3. Market Intelligence Agent (P2)](#3-market-intelligence-agent-p2)
- [Canonical Intent Routing & State Isolation](#canonical-intent-routing--state-isolation)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [⚡ One-Click Windows Launch](#-one-click-windows-launch)
  - [Manual Setup (Step-by-Step)](#manual-setup-step-by-step)
  - [🐳 Docker & Container Deployment](#-docker--container-deployment)
- [Environment Variables Reference](#environment-variables-reference)
- [CLI Command Suite](#cli-command-suite)
- [Cybernetic Web Workstation & Interfaces](#cybernetic-web-workstation--interfaces)
  - [Central Agent Hub (`/index.html`)](#central-agent-hub-indexhtml)
  - [3D Tactical Radar Cockpit (`/cockpit.html`)](#3d-tactical-radar-cockpit-cockpithtml)
  - [Job Application & Interview Kanban (`/jobs.html`)](#job-application--interview-kanban-jobshtml)
- [Defensive Security & Integrity Protection](#defensive-security--integrity-protection)
- [REST & WebSocket API Reference](#rest--websocket-api-reference)
- [Testing & Quality Assurance](#testing--quality-assurance)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Contributing](#contributing)
- [License](#license)

---

## Key Features

- **Multi-Wave Adaptive Discovery Loops:** Replaces naive single-shot search routines with iterative search-fetch-discover loops that adaptively formulate follow-up waves based on intermediate yields.
- **Canonical Intent Routing (Zero False Positives):** Dual-layer intent routing (deterministic fast-path + LLM refinement) prevents profession terms like *"engineer"* or company names like *"SpaceX"* from triggering job searches when the user asked for conceptual explanations or company profiles.
- **Strict Qualification & Transparent Scoring (0–100):** Multi-factor deterministic scoring breakdown for job discovery (Title 40%, Location 30%, Experience 15%, Skills 15%) supporting multi-location OR semantics (`Saudi Arabia or UAE`). Inferred skills never act as hard disqualification gates.
- **Multi-Tier Source Quality Classification:** Evaluates documentation authenticity across 7 distinct source tiers (`OFFICIAL_DOCUMENTATION`, `PRIMARY_SOURCE`, `ACADEMIC_SOURCE`, `ENGINEERING_BLOG`, `TECH_COMMUNITY`, `GENERAL_WEB`, `LOW_QUALITY`).
- **Counter-Evidence & Trade-Off Analysis:** Dedicated negative discovery wave hunts for failure modes, architectural trade-offs, performance bottlenecks, and bearish risks (`"What could make this candidate fail?"`).
- **Unified Canonical Models (`Evidence` & `JobRecord`):** Strongly typed domain schemas linking every claim and extracted field back to verifiable primary document URLs with character quotes and confidence scores.
- **Multi-Model Google Gemini Load Balancer:** Free-tier multi-model pool with round-robin rotation, micro-pacing, and zero-latency automatic failover on Google `503 Service Unavailable` or `429 Resource Exhausted` quotas.
- **Enterprise-Grade Security:** Strict SSRF blocking, DNS pinning with custom socket transports (preventing TOCTOU and DNS rebinding), CSV/Excel formula injection escaping (`=`, `+`, `-`, `@`, `\t`, `\r`), and path traversal sanitization.
- **Interactive Cybernetic Cockpit:** Three.js WebGL 3D radar interface streaming real-time phase updates, WebSocket telemetry, live terminal logs, and live Kanban auto-synchronization on application click.

---

## Tech Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Runtime Language** | Python 3.10+ (Tested on 3.10, 3.11, 3.12, 3.14) | Core backend, agent loops, CLI, and tool execution |
| **Web Framework** | FastAPI 0.110+, Uvicorn 0.28+ | High-throughput async REST API & bidirectional WebSocket telemetry |
| **Data Validation** | Pydantic v2 (2.6+) | Strongly typed schemas, serialization, and strict runtime contracts |
| **LLM Orchestration** | Google GenAI SDK (`google-genai` 0.1+) | Structured JSON schema extraction and dossier synthesis |
| **Persistence** | SQLite 3 (WAL Mode, `PRAGMA synchronous = NORMAL`) | ACID-compliant relational storage for tasks, runs, records, and evidence |
| **HTTP & Scraping** | HTTPX 0.27+, BeautifulSoup4 4.12+ | Async HTTP client, connection pooling, and resilient DOM parsing |
| **Search Providers** | DuckDuckGo Lite & Multi-Engine Crawlers | Zero-cost multi-query public web search with exponential backoff |
| **Document Export** | OpenPyXL 3.1+, Python-Docx 1.1+, Jinja2 | Cryptographic SHA-256 exports in Markdown, Excel, Word, CSV, JSON |
| **Frontend UI** | HTML5, CSS3, ES6+, Three.js (WebGL), DOMPurify | Cyberpunk tactical HUD, 3D radar scene, and Kanban job tracker |
| **Testing** | Pytest 9.1+, Pytest-Asyncio, AnyIO | 210 automated unit, integration, and security regression tests |

---

## System Architecture

### High-Level Architectural Workflow

```text
                                  ┌──────────────────────────────────────────────┐
                                  │           User Request / API / UI            │
                                  └──────────────────────┬───────────────────────┘
                                                         │
                                                         ▼
                                          ┌──────────────────────────────┐
                                          │   Canonical IntentRouter     │
                                          │  Deterministic & LLM Fallback│
                                          └──────────────┬───────────────┘
                                                         │
                          ┌──────────────────────────────┼──────────────────────────────┐
                          │ (mode="jobs" / JOB_SEARCH)   │ (mode="research" / HOW_TO /  │ (mode="market" /
                          │                              │  EXPLANATION / COMPARISON)   │  MARKET_RESEARCH)
                          ▼                              ▼                              ▼
           ┌──────────────────────────────┐┌──────────────────────────────┐┌──────────────────────────────┐
           │       Job Agent (P0)         ││     Research Agent (P1)      ││      Market Agent (P2)       │
           │   Autonomous Discovery Loop  ││   ResearchDiscoveryEngine    ││    MarketDiscoveryEngine     │
           │  • Canonical JobSearchSpec   ││  • Wave 1: Primary / Docs    ││  • Wave 1: Regime & Context  │
           │  • Adaptive ATS Harvesting   ││  • Wave 2: Tech Architecture ││  • Wave 2: Sector Priority   │
           │  • Multi-Location OR Matching││  • Wave 3: Trade-Offs / Risks││  • Wave 3: 6 Discovery Tracks│
           │  • Deterministic Scoring     ││  • Source Tiering (1.0-0.25) ││  • Wave 4: Deep Technicals   │
           │  • Canonical JobRecord Output││  • Markdown Dossier Matrix   ││  • Wave 5: Counter-Evidence  │
           └──────────────┬───────────────┘└──────────────┬───────────────┘└──────────────┬───────────────┘
                          │                              │                              │
                          └──────────────────────────────┼──────────────────────────────┘
                                                         ▼
                                  ┌──────────────────────────────────────────────┐
                                  │     Shared Infrastructure & Data Layer       │
                                  │  • Canonical Evidence Model                  │
                                  │  • SSRF & Host-Pinned FetchTool              │
                                  │  • Multi-Provider Search with Rate-Limits    │
                                  │  • SQLite Transactions & Event Emission      │
                                  └──────────────────────────────────────────────┘
```

---

### Directory Structure

```text
DataHunt-Agent/
├── agent/                         # 🤖 Top-Level Agent Wrappers & Orchestrator
│   ├── __init__.py                # Package exports (JobHuntAgent, DeepResearchAgent, etc.)
│   ├── base.py                    # BaseAgent abstract class
│   ├── job_agent.py               # 🎯 0-Sec Live ATS Job Radar Agent Wrapper
│   ├── research_agent.py          # 🔬 Deep Technical Research Agent Wrapper
│   ├── market_agent.py            # 📊 Market & Competitive Intelligence Agent Wrapper
│   └── orchestrator.py            # 🎛️ ResearchOrchestrator routing runs to engines
│
├── datahunt/                      # 🧠 Core System Engine Package
│   ├── api.py                     # FastAPI REST server & WebSocket streaming router
│   ├── cli.py                     # Rich CLI command suite
│   ├── config.py                  # Typed configuration, environment loading, and budgets
│   ├── errors.py                  # Error taxonomy (ErrorCode, envelope & jitter backoff)
│   ├── logger.py                  # Structured logging & credential masking
│   ├── policy.py                  # SSRF defenses, DNS resolution, and formula escaping
│   │
│   ├── agent/                     # ⚙️ Autonomous Discovery Engines & Runtime
│   │   ├── runtime.py             # AgentRuntime decision loop (Observe → Decide → Act)
│   │   ├── research_discovery.py  # 🔬 Multi-Wave ResearchDiscoveryEngine
│   │   ├── market_discovery.py    # 📊 Multi-Wave MarketDiscoveryEngine
│   │   ├── discovery_engine.py    # ATS & Job Discovery Engine
│   │   ├── discovery_models.py    # SearchTask, DiscoveryBudget, SourceCoverageMatrix
│   │   ├── policies.py            # qualify_job, match_location, match_experience, scoring
│   │   ├── decision.py            # DecisionEngine next-action selector
│   │   ├── state.py               # Canonical AgentState representation
│   │   └── coverage.py            # CoverageEvaluator & honest discovery reporting
│   │
│   ├── agents/                    # 🧩 Specialized Agent Modules
│   │   ├── intent_router.py       # Canonical IntentRouter (fast-path + LLM fallback)
│   │   ├── query_understanding.py # QueryUnderstandingAgent & JobSearchRequest
│   │   ├── query_expansion.py     # QueryExpansionAgent (synonyms, skills, keywords)
│   │   ├── search_planner.py      # SearchPlannerAgent (ATS & generic search tasks)
│   │   ├── hard_filter.py         # HardFilter deterministic exclusion rules
│   │   ├── normalizer.py          # DataNormalizer (raw fields to NormalizedJob)
│   │   ├── job_analysis.py        # JobAnalysisAgent (multidimensional scoring)
│   │   ├── market_intent.py       # MarketIntentParser (equities, indices, horizons)
│   │   └── market_validator.py    # MarketValidator (ticker symbols & exchange verification)
│   │
│   ├── db/                        # 🗄️ Relational Persistence Layer
│   │   ├── connection.py          # SQLite connection factory (WAL mode enabled)
│   │   ├── migrations.py          # Idempotent database schema migrator
│   │   └── repositories.py        # Repositories (Task, Run, Document, Record, Export, Job)
│   │
│   ├── llm/                       # 🤖 LLM Client & Prompting
│   │   ├── gemini_client.py       # Gemini API client, model rotation pool, and fallback
│   │   └── prompts.py             # Few-shot prompts, schemas, and dossier templates
│   │
│   ├── models/                    # 📐 Canonical Pydantic Schemas
│   │   ├── evidence.py            # Canonical Evidence model
│   │   ├── job_record.py          # Canonical JobRecord model (Section 12)
│   │   ├── intent.py              # ResearchIntent, ResearchOutputType, ResearchIntentSpec
│   │   ├── job_spec.py            # JobSearchSpec specification
│   │   ├── market.py              # MarketRecord, MarketEvidence, MarketCandidate
│   │   ├── record.py              # ExtractedRecord, SourceDocument, RecordEvidence
│   │   ├── run.py                 # ResearchRun, RunBudget, RunCounters, RunStatus
│   │   └── task.py                # ResearchTask, ResearchSpec, Geography, SourcePolicy
│   │
│   ├── sources/                   # 🌐 Source Discovery & ATS Registry
│   │   ├── registry.py            # JobSourceRegistry (Greenhouse, Lever, Ashby, etc.)
│   │   ├── models.py              # DiscoveredSource, SourceHealth, SourceTier
│   │   └── seed_sources.py        # Seed job boards, ATS signatures, and regional portals
│   │
│   └── tools/                     # 🛠️ Execution Tool Substrate
│       ├── base.py                # BaseTool and ToolResult contracts
│       ├── search.py              # SearchTool with DuckDuckGo Lite & fallback providers
│       ├── fetch.py               # FetchTool (SSRF-protected, DNS-pinned HTTP client)
│       ├── extract.py             # ExtractTool (Schema-constrained entity extraction)
│       ├── verify.py              # VerifyTool (Fact/quote locator & phone/email policy)
│       ├── dedupe.py              # DedupeTool (Canonical URL hashing & content merging)
│       ├── export.py              # ExportTool (JSON, CSV, XLSX, MD, DOCX with SHA-256)
│       └── market_analysis.py     # Deterministic indicators (RSI, MACD, SMA, EMA, ATR)
│
├── frontend/                      # 💻 Cybernetic User Interfaces
│   ├── index.html                 # Central AI Agent Hub Homepage
│   ├── cockpit.html               # 3D Interactive Tactical Cockpit (Three.js HUD)
│   ├── jobs.html                  # Persistent Job Application & Interview Kanban
│   ├── css/                       # Cyberpunk stylesheets (styles.css, hub.css, jobs.css)
│   └── js/                        # Frontend controllers (app.js, hud.js, three_scene.js, jobs.js)
│
├── migrations/                    # 📜 SQL Migration Scripts
│   ├── 001_initial_schema.sql     # Core tables (tasks, runs, records, documents, exports)
│   └── 002_job_applications.sql  # Job application tracker & interview status schema
│
├── tests/                         # 🧪 210 Automated Unit, Integration & Regression Tests
│   ├── test_70_step_job_pipeline.py           # 70-step discovery pipeline integration
│   ├── test_intent_routing.py                 # IntentRouter classification & negative tests
│   ├── test_job_search_request_canonical.py   # Canonical request immutability
│   ├── test_golden_genai_search.py            # Golden GenAI discovery in Saudi/UAE
│   ├── test_job_agent_e2e.py                  # End-to-end Job Agent loop
│   ├── test_location_matching.py              # Multi-location OR and regional clustering
│   ├── test_research_discovery_engine.py      # Golden LangGraph vs CrewAI & source tiers
│   ├── test_golden_answer_research.py         # Space travel & factual explanation tests
│   ├── test_market_agent.py                   # Multi-wave market discovery & math tests
│   └── ...                                    # SSRF, Dedupe, Export, Yield & DB tests
│
├── data/                          # 💾 SQLite database files (created at runtime)
├── exports/                       # 📥 Generated export files (created at runtime)
├── Dockerfile                     # Container build file
├── docker-compose.yml             # Docker Compose orchestration
├── run_app.bat                    # One-click Windows application launcher
├── pyproject.toml                 # Package metadata and test configuration
├── requirements.txt               # Pinned Python package dependencies
└── README.md                      # Project documentation
```

---

### Request Lifecycle & Execution Flow

```text
1. Intake:
   User submits natural language query via Web HUD, REST API, or CLI.
   Request headers and payload validated against schema boundaries and quota limits.

2. Intent Classification:
   IntentRouter evaluates query against deterministic fast-path regex rules.
   If ambiguous, queries Gemini with structured schema fallback.
   Query routed to:
   - JOB_SEARCH           --> Job Hunt Engine (AgentRuntime)
   - HOW_TO / EXPLANATION --> Deep Research Engine (ResearchDiscoveryEngine)
   - COMPARISON           --> Deep Research Engine (Comparison Mode)
   - MARKET_RESEARCH      --> Market Intelligence Engine (MarketDiscoveryEngine)

3. Adaptive Discovery Waves:
   - Primary Wave: Authoritative and primary sources searched and fetched.
   - Deep Dive Wave: Technical specs, benchmarks, and multi-channel candidate discovery.
   - Verification Wave: Negative discovery, trade-off detection, and counter-evidence.

4. Extraction & Normalization:
   Raw HTML stripped of navigation boilerplate; text chunks extracted via LLM or regex parsers.
   Extracted entities normalized into typed structures (NormalizedJob or MarketCandidate).

5. Deterministic Verification & Qualification:
   Every field verified against source documents.
   Explicit constraints evaluated with strict Boolean gates (MISMATCH rejects).
   Additive signals (skills, market sentiment) scored on a 0–100 scale.

6. Deduplication & Cross-Referencing:
   Canonical URLs generated (stripping UTM tracking, preserving ATS identifiers).
   Duplicate entities collapsed while merging evidence points.

7. Synthesis & Report Generation:
   Executive markdown dossier generated with comparison tables, key findings, and primary citations.

8. Persistence & Live Streaming:
   Records, documents, and evidence inserted into SQLite in atomic transactions.
   WebSocket streams real-time telemetry to the 3D WebGL Cockpit.
```

---

### Database Schema (SQLite WAL)

DataHunt uses an ACID-compliant SQLite datastore configured with **Write-Ahead Logging (WAL)** and **Normal Synchronous** mode for ultra-fast concurrent reads and writes without database lock contention.

```
research_tasks
├── id (TEXT, PK)
├── request_text (TEXT, NOT NULL)
├── agent_mode (TEXT, DEFAULT 'auto')
├── normalized_spec_json (TEXT)
├── status (TEXT)
├── created_at (TEXT)
└── updated_at (TEXT)

research_runs
├── id (TEXT, PK)
├── task_id (TEXT, FK → research_tasks.id)
├── status (TEXT)
├── target_records (INTEGER)
├── budget_json (TEXT)
├── counters_json (TEXT)
├── warnings_json (TEXT)
├── created_at (TEXT)
└── finished_at (TEXT)

source_documents
├── id (TEXT, PK)
├── run_id (TEXT, FK → research_runs.id)
├── requested_url (TEXT)
├── canonical_url (TEXT)
├── domain (TEXT)
├── http_status (INTEGER)
├── title (TEXT)
├── extracted_text (TEXT)
└── created_at (TEXT)

extracted_records
├── id (TEXT, PK)
├── run_id (TEXT, FK → research_runs.id)
├── fields_json (TEXT, NOT NULL)
├── confidence (REAL)
├── verification_status (TEXT)
├── dedupe_hash (TEXT)
└── created_at (TEXT)

record_evidence
├── id (TEXT, PK)
├── record_id (TEXT, FK → extracted_records.id)
├── field_name (TEXT, NOT NULL)
├── source_document_id (TEXT, FK → source_documents.id)
├── source_url (TEXT)
├── quote (TEXT)
├── context (TEXT)
└── extracted_at (TEXT)

job_applications (Kanban Tracker)
├── id (TEXT, PK)
├── record_id (TEXT, FK → extracted_records.id)
├── title (TEXT, NOT NULL)
├── company (TEXT, NOT NULL)
├── location (TEXT)
├── application_url (TEXT)
├── status (TEXT: 'not_applied' | 'applied' | 'interviewing' | 'offered' | 'rejected')
├── notes (TEXT)
├── applied_at (TEXT)
└── updated_at (TEXT)

export_artifacts
├── id (TEXT, PK)
├── run_id (TEXT, FK → research_runs.id)
├── format (TEXT)
├── file_path (TEXT)
├── sha256_hash (TEXT)
├── record_count (INTEGER)
└── created_at (TEXT)
```

---

## The Three Autonomous Engines

### 1. Job Hunt AI Agent (P0 — Highest Priority)

The Job Agent is a specialized discovery engine for career intelligence that bypasses traditional scraper limitations:

- **Canonical Request Immutability:** Uses [`JobSearchSpec`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/models/job_spec.py) / [`JobSearchRequest`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/agents/query_understanding.py) parsed at entry. Downstream phases never re-parse or create competing fallback requests.
- **Direct ATS Platform Harvesting:** Employs targeted signatures for Greenhouse (`boards.greenhouse.io`), Lever (`jobs.lever.co`), Ashby (`jobs.ashbyhq.com`), and Workable (`apply.workable.com`), extracting verified application endpoints.
- **Multi-Location OR Matching:** Formulates location queries using semantic regional clusters:
  - Query: `"GenAI Engineer jobs in Saudi Arabia or UAE with 0–6 years experience"`
  - Clusters: `saudi_arabia` (`riyadh`, `jeddah`, `ksa`), `uae` (`dubai`, `abu dhabi`, `sharjah`), and `gulf`.
  - Semantics: Any job matching either region is accepted (`MatchStatus.MATCH`).
- **Separation of Hard Gates vs. Soft Signals:**
  - *Hard Gates (Must not be MISMATCH):* Job Title category, Location cluster, Experience upper limit.
  - *Soft Signals (Additive Scoring):* Explicit and inferred skills (Python, LangChain, PyTorch, Docker) boost relevance scores (0–15%) but **never** disqualify candidates.
- **Output:** Emits strongly typed [`JobRecord`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/models/job_record.py) models with attached [`Evidence`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/models/evidence.py) objects and transparent score breakdowns.

### 2. Deep Technical Research Agent (P1)

Built on [`ResearchDiscoveryEngine`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/agent/research_discovery.py), this engine conducts multi-wave investigations for technical comparisons, architectural teardowns, and factual inquiries:

- **Wave 1 (Primary & Official Documentation):** Targets official project docs, GitHub release notes, and specification RFCs (weight: 1.0).
- **Wave 2 (Deep Technical & Comparative Analysis):** Examines state persistence, concurrency models, benchmarks, and production patterns (weight: 0.85).
- **Wave 3 (Contradiction & Nuance Verification):** Systematically hunts for counter-evidence, known limitations, open GitHub issues, and failure modes.
- **7-Tier Source Quality Hierarchy:**
  - `OFFICIAL_DOCUMENTATION` / `PRIMARY_SOURCE` (1.0)
  - `ACADEMIC_SOURCE` / `STANDARDS_BODY` (0.95)
  - `ENGINEERING_BLOG` (0.85)
  - `TECH_COMMUNITY` (0.65)
  - `GENERAL_WEB` (0.50)
  - `LOW_QUALITY` (0.25)
- **Comparative Dossier Output:** Synthesizes structured comparison matrices across 7 evaluation dimensions (e.g., LangGraph vs. CrewAI: Orchestration Paradigm, State Management, Control Flow, Human-in-the-Loop, Error Recovery, Learning Curve, Production Maturity) with primary citations.

### 3. Market Intelligence Agent (P2)

Built on [`MarketDiscoveryEngine`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/agent/market_discovery.py), this engine delivers evidence-based equity and financial market intelligence:

- **Wave 1 (Market Context & Regime):** Analyzes benchmark indices (Nifty 50, Sensex, S&P 500) to classify market regime (`BULLISH`, `BEARISH`, `SIDEWAYS`, `VOLATILE`).
- **Wave 2 (Sector Relative Strength):** Identifies sector tailwinds (e.g., IT, Banking, Auto, Energy) to prioritize candidate selection.
- **Wave 3 (Multi-Channel Candidate Discovery):** Scans 6 independent channels: Momentum, Volume Surges, Technical Breakouts, Fundamentals, Corporate Events, and News Catalysts.
- **Wave 4 (Deep Technicals & Deterministic Indicators):** Computes RSI, MACD, 20/50/200 SMA/EMA, and ATR deterministically in Python (zero LLM hallucinations of prices or technical levels).
- **Wave 5 (Counter-Evidence & Bearish Risk Discovery):** Specifically searches for negative news, promoter pledges, earnings misses, and regulatory scrutiny (`"What could make this candidate fail?"`).
- **Wave 6 (Transparent Scoring & Stop Control):** Ranks candidates (0–100) based on trend strength, sector alignment, volume confirmation, and risk penalties. Never promises financial returns.

---

## Canonical Intent Routing & State Isolation

The [`IntentRouter`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/agents/intent_router.py) eliminates keyword-only misrouting:

| User Query | Classified Intent | Output Type | Expected Behavior |
| :--- | :--- | :--- | :--- |
| `"Explain how software engineers build spacecraft."` | `EXPLANATION` | `ANSWER` | Explains engineering methods. **Must NOT** trigger Job Search despite the word *"engineers"*. |
| `"Research SpaceX."` | `COMPANY_RESEARCH` | `COMPANY_PROFILE` | Researches company valuation, launch vehicles, and profile. **Must NOT** trigger Job Search. |
| `"What is LangGraph?"` | `EXPLANATION` | `ANSWER` | Explains graph state machine architecture. |
| `"Find LangGraph engineer jobs."` | `JOB_SEARCH` | `JOB_RESULTS` | Discovers open engineering roles requiring LangGraph. |
| `"Find AI Engineer jobs in UAE."` | `JOB_SEARCH` | `JOB_RESULTS` | Discovers AI Engineer postings in Dubai/Abu Dhabi/UAE. |
| `"Give NSE stocks with strong momentum this week."` | `MARKET_RESEARCH` | `MARKET_INTEL` | Routes to Market Engine. **Must NOT** trigger generic List Research. |

---

## Prerequisites

Before setting up DataHunt, verify that your environment meets the following requirements:

- **Operating System:** Windows 10/11, macOS 12+, or Linux (Ubuntu 20.04+, Debian 11+, RHEL 8+)
- **Python:** Python 3.10, 3.11, 3.12, or 3.14 (64-bit recommended)
- **Git:** Version 2.30 or higher
- **Google Gemini API Key:** (Optional for live LLM extraction; free-tier key works with automatic load balancing)
- **Modern Web Browser:** Chrome, Edge, Firefox, or Safari with WebGL enabled (for 3D Radar Cockpit)

---

## Getting Started

### ⚡ One-Click Windows Launch

For Windows workstations, a turnkey launcher script is included in the project root:

```cmd
run_app.bat
```

**What this script does:**
1. Checks for an installed Python 3.10+ interpreter.
2. Automatically creates and activates a local `.venv` virtual environment if not already present.
3. Installs or upgrades dependencies from `requirements.txt`.
4. Runs database schema migrations (`python -m datahunt.cli migrate`).
5. Opens your default web browser to `http://127.0.0.1:8000`.
6. Launches the FastAPI server with live reload enabled.

---

### Manual Setup (Step-by-Step)

#### 1. Clone the Repository
```bash
git clone https://github.com/mohd98zaid/DataHunt-Agent.git
cd DataHunt-Agent
```

#### 2. Create and Activate Virtual Environment
```bash
# On Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate

# On Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1

# On Windows (Command Prompt):
python -m venv .venv
.venv\Scripts\activate.bat
```

#### 3. Install Package Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

*For development and test dependencies:*
```bash
pip install -e ".[dev]"
```

#### 4. Configure Environment Variables
Copy the example environment file:
```bash
cp .env.example .env
```

Edit `.env` using your preferred editor:
```env
# Google Gemini API Key (Required for live LLM synthesis)
GEMINI_API_KEY=AIzaSyYourGeminiApiKeyHere
GEMINI_MODEL=auto

# Storage & Export Paths
DATABASE_PATH=./data/datahunt.sqlite3
EXPORT_DIR=./exports

# Performance & Quota Budgets
MAX_RUN_SECONDS=600
MAX_SEARCH_QUERIES=30
MAX_PAGES_FETCHED=120
MAX_RECORDS=500

# Logging Level
LOG_LEVEL=INFO
```

#### 5. Run Database Migrations
Initialize the SQLite WAL database:
```bash
python -m datahunt.cli migrate
```

*Expected output:*
```text
DataHunt Running SQLite migrations...
[OK] Applied migrations: 001_initial_schema.sql, 002_job_applications.sql
```

#### 6. Start the Application Server
```bash
uvicorn datahunt.api:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser to:
- **Central Agent Hub:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **3D Tactical Radar Cockpit:** [http://127.0.0.1:8000/cockpit.html](http://127.0.0.1:8000/cockpit.html)
- **Job Tracker Kanban Board:** [http://127.0.0.1:8000/jobs.html](http://127.0.0.1:8000/jobs.html)
- **Interactive OpenAPI Documentation:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

### 🐳 Docker & Container Deployment

To run DataHunt inside a containerized environment:

#### 1. Build and Run via Docker Compose
```bash
docker compose up --build -d
```

#### 2. Inspect Running Containers
```bash
docker compose ps
```

#### 3. View Real-Time Container Logs
```bash
docker compose logs -f datahunt
```

#### 4. Stop Container
```bash
docker compose down
```

---

## Environment Variables Reference

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `GEMINI_API_KEY` | String | `""` | Google Gemini API key. Free-tier keys are fully supported. |
| `GEMINI_MODEL` | String | `"auto"` | Model routing mode: `"auto"`, `"gemini-flash-latest"`, `"gemini-3.8-flash"`. |
| `GEMINI_LITE_MODEL` | String | `"gemini-flash-lite-latest"` | Lightweight fallback model for high-frequency extraction. |
| `GEMINI_MAX_OUTPUT_TOKENS` | Integer | `4096` | Max tokens generated per LLM call. |
| `GEMINI_TEMPERATURE` | Float | `0.1` | Temperature for deterministic entity extraction. |
| `DATABASE_PATH` | Path | `./data/datahunt.sqlite3` | SQLite database file location. |
| `EXPORT_DIR` | Path | `./exports` | Directory for exported files (JSON, CSV, XLSX, MD, DOCX). |
| `MAX_RUN_SECONDS` | Integer | `600` | Hard deadline budget per research run in seconds. |
| `MAX_SEARCH_QUERIES` | Integer | `30` | Maximum number of search queries permitted per run. |
| `MAX_PAGES_FETCHED` | Integer | `120` | Maximum number of web pages fetched per run. |
| `MAX_RESPONSE_BYTES` | Integer | `2000000` | Max bytes fetched per HTTP request (2MB limit). |
| `MAX_RECORDS` | Integer | `500` | Maximum final qualified records returned. |
| `CONNECT_TIMEOUT_SECONDS` | Float | `5.0` | Socket connection timeout for HTTP requests. |
| `TOTAL_FETCH_TIMEOUT_SECONDS` | Float | `30.0` | Total timeout per document fetch. |
| `LOG_LEVEL` | String | `"INFO"` | Logging verbosity: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`. |
| `LANGSMITH_API_KEY` | String | `""` | Optional LangSmith API key for distributed tracing. |
| `LANGSMITH_PROJECT` | String | `"DataHunt"` | LangSmith project name for traces. |
| `LANGSMITH_TRACING` | Boolean | `false` | Enable or disable LangSmith telemetry. |
| `DATAHUNT_API_KEY` | String | `""` | Optional API key to secure REST endpoints. |

---

## CLI Command Suite

DataHunt includes a full command-line suite styled with Rich tables and progress bars.

```bash
# List available agents and mission profiles
python -m datahunt.cli agents

# Run Job Radar Agent with JSON output
python -m datahunt.cli run "Find GenAI Engineer jobs in Saudi Arabia or UAE with 0-6 years experience" --agent jobs --format json

# Run Deep Technical Research Agent with Markdown export
python -m datahunt.cli run "Compare LangGraph and CrewAI for production agent orchestration" --agent research --format md

# Run Market Intelligence Agent with Excel spreadsheet export
python -m datahunt.cli run "Give top 10 NSE stocks with potentially favorable setups for this week" --agent market --format xlsx

# List historical research runs
python -m datahunt.cli list --limit 10

# Inspect run details and metrics
python -m datahunt.cli status <run_id>

# Run SQLite schema migrations
python -m datahunt.cli migrate
```

---

## Cybernetic Web Workstation & Interfaces

### Central Agent Hub (`/index.html`)
The main entry point provides mission cards for each of the three agents, mode selection toggles, quick query chips, and active run status.

### 3D Tactical Radar Cockpit (`/cockpit.html`)
A Three.js WebGL interface styled as an intelligence radar cockpit:
- **WebGL 3D Radar Sphere:** Interactive wireframe sphere with pulsing target blips representing discovered sources and candidate records.
- **Live WebSocket Telemetry:** Real-time updates for Search Iterations, Pages Fetched, Records Verified, Records Rejected, and Model Failovers.
- **Terminal Event Feed:** Monospace log displaying sub-second phase transitions and decision rationales.
- **Dynamic Dossier Viewer:** Renders synthesized Markdown dossiers with embedded tables, criteria matrices, and source citations.

### Job Application & Interview Kanban (`/jobs.html`)
A persistent application tracker directly synced with SQLite:
- **Columns:** *⚪ Not Applied*, *🔵 Applied*, *🟡 Interviewing*, *🟢 Offered*, *🔴 Rejected*.
- **One-Click Apply Auto-Sync:** Clicking `APPLY ➔` opens the direct ATS job posting in a new browser tab while automatically moving the card to *Applied*, setting the timestamp, and updating application statistics in SQLite.
- **Salary & Location Filtering:** Real-time search by job title, company name, location, or salary range.

---

## Defensive Security & Integrity Protection

DataHunt enforces strict defensive engineering:

1. **Deep SSRF Defense & DNS Pinning:**
   [`datahunt/policy.py`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/policy.py) pre-resolves hostnames before network calls, blocking loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`), private RFC 1918 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), and carrier-grade NAT. In [`FetchTool`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/tools/fetch.py), HTTP connections are pinned to the verified IP address with a custom socket transport, eliminating DNS rebinding (TOCTOU) attacks.
2. **Formula Injection Sanitization:**
   [`ExportTool`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/tools/export.py) automatically sanitizes cell values starting with `= `, `+ `, `- `, `@ `, `\t`, or `\r` before writing CSV or Excel (`.xlsx`) files. Excel cells explicitly enforce string data types (`cell.data_type = 's'`).
3. **Filesystem Path Traversal Sandboxing:**
   Export download endpoints in [`datahunt/api.py`](file:///c:/Users/mohd9/OneDrive/Desktop/My_project/DataHunt_agent/datahunt/api.py) resolve canonical paths against `settings.EXPORT_DIR`, blocking directory escape attempts (`../`).
4. **PII Redaction & Contact Hygiene:**
   Collects only corporate addresses (`careers@`, `info@`, `contact@`) and automatically redacts personal Gmail/Yahoo addresses and private phone numbers.

---

## REST & WebSocket API Reference

The server exposes OpenAPI 3.0 documentation at `http://127.0.0.1:8000/docs`.

### Primary Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/info` | Service status, application version, and uptime metadata. |
| `GET` | `/api/health` | Health check endpoint returning database connectivity status. |
| `POST` | `/api/tasks` | Create and execute an autonomous research run. |
| `GET` | `/api/tasks/{task_id}` | Fetch task metadata, status, and associated run ID. |
| `GET` | `/api/runs/{run_id}` | Fetch run details, telemetry counters, warnings, and summary. |
| `GET` | `/api/runs/{run_id}/records` | Retrieve extracted and verified records for a specific run. |
| `GET` | `/api/runs/{run_id}/export` | Trigger on-demand export to JSON, CSV, XLSX, MD, or DOCX. |
| `GET` | `/api/exports/{export_id}/download` | Securely download generated export artifact with SHA-256 header. |
| `GET` | `/api/jobs` | Retrieve all tracked job applications for the Kanban board. |
| `PATCH` | `/api/jobs/{job_id}/status` | Update job application status (`applied`, `interviewing`, etc.). |
| `WS` | `/ws/runs/{run_id}` | Bidirectional WebSocket stream for real-time telemetry events. |

#### Create Task Request Payload (`POST /api/tasks`)
```json
{
  "query": "Find GenAI Engineer jobs in Saudi Arabia or UAE with 0-6 years experience",
  "agent_mode": "jobs",
  "max_records": 50,
  "freshness_days": 14,
  "output_format": "json",
  "run_in_background": false
}
```

---

## Testing & Quality Assurance

DataHunt includes an automated test suite containing **210 tests** covering unit, integration, golden discovery scenarios, and security boundaries.

### Running Tests

```bash
# Run the complete test suite
python -m pytest

# Run tests with verbose output and test execution timings
python -m pytest -v --durations=10

# Run specific subsystem test suites:
python -m pytest tests/test_intent_routing.py               # Intent routing & negative tests
python -m pytest tests/test_job_search_request_canonical.py # Canonical job request validation
python -m pytest tests/test_location_matching.py            # Multi-location OR & cluster tests
python -m pytest tests/test_golden_genai_search.py          # Golden GenAI discovery in Saudi/UAE
python -m pytest tests/test_research_discovery_engine.py    # Golden LangGraph vs CrewAI comparison
python -m pytest tests/test_market_agent.py                 # Multi-wave market discovery & math
python -m pytest tests/test_policy_ssrf.py                  # SSRF & DNS rebinding defenses
```

### Test Suite Results

```text
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\mohd9\OneDrive\Desktop\My_project\DataHunt_agent
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.0, langsmith-0.12.2, asyncio-1.4.0
collected 210 items

tests\test_70_step_job_pipeline.py ............                          [  5%]
tests\test_agent_directory.py ..                                         [  6%]
tests\test_agent_evaluation.py ........                                  [ 10%]
tests\test_agent_state.py ................                               [ 18%]
tests\test_api.py .......                                                [ 21%]
tests\test_audit_fixes.py .......                                        [ 24%]
tests\test_config.py ....                                                [ 26%]
tests\test_db.py ..                                                      [ 27%]
tests\test_dedupe.py ..                                                  [ 28%]
tests\test_dynamic_discovery_engine.py .......                           [ 31%]
tests\test_export.py .....                                               [ 34%]
tests\test_fetch.py ...                                                  [ 35%]
tests\test_freshness_and_ats.py ...........                              [ 40%]
tests\test_global_job_discovery.py .......                               [ 44%]
tests\test_golden_answer_research.py .....                               [ 46%]
tests\test_golden_genai_search.py ...                                    [ 48%]
tests\test_intent_routing.py .....................                       [ 58%]
tests\test_job_agent_e2e.py .........                                    [ 62%]
tests\test_job_search_request_canonical.py ........                      [ 66%]
tests\test_location_matching.py ..............                           [ 72%]
tests\test_market_agent.py ...........                                   [ 78%]
tests\test_model_pool.py .....                                           [ 80%]
tests\test_orchestrator.py .                                             [ 80%]
tests\test_policy_ssrf.py .....                                          [ 83%]
tests\test_regional_job_search.py ....                                   [ 85%]
tests\test_research_discovery_engine.py ...                              [ 86%]
tests\test_search_yield.py ..............                                [ 93%]
tests\test_search_yield_and_geo.py ......                                [ 96%]
tests\test_stop_conditions.py ........                                   [100%]

====================== 210 passed, 2 warnings in 49.38s =======================
```

---

## Troubleshooting & FAQ

### 1. `pytest: The term 'pytest' is not recognized`
**Cause:** The virtual environment is not activated in your shell.  
**Solution:**
```bash
# Windows PowerShell:
.venv\Scripts\Activate.ps1

# Windows CMD:
.venv\Scripts\activate.bat

# Linux/macOS:
source .venv/bin/activate
```
Alternatively, invoke pytest through the Python interpreter:
```bash
python -m pytest
```

### 2. `database is locked` Error in SQLite
**Cause:** Concurrent writes attempted without WAL mode enabled.  
**Solution:** Verify that the database connection has enabled Write-Ahead Logging:
```bash
python -m datahunt.cli migrate
```
DataHunt automatically executes `PRAGMA journal_mode = WAL;` and `PRAGMA busy_timeout = 5000;` on connection initialization.

### 3. Google Gemini `429 Resource Exhausted` or `503 Service Unavailable`
**Cause:** Free-tier per-minute quota limits reached.  
**Solution:** DataHunt's multi-model pool catches HTTP 429 and 503 errors and places the affected model on a temporary cooldown window (30s/60s), instantly failing over to alternative models (`gemini-flash-latest`, `gemini-3.8-flash`, `gemini-flash-lite-latest`) without crashing the active run.

### 4. Port 8000 Already in Use
**Cause:** Another instance of Uvicorn or another service is listening on port 8000.  
**Solution:** Specify a different port when launching:
```bash
uvicorn datahunt.api:app --host 127.0.0.1 --port 8080 --reload
```

---

## Contributing

Contributions are welcome. Please adhere to the following development practices:

1. **Fork the Repository:** Create a feature branch (`git checkout -b feature/my-feature`).
2. **Deterministic Logic:** Avoid LLMs for operations that can be computed deterministically (RSI math, location matching, URL normalization).
3. **Preserve Security Invariants:** Never bypass SSRF protections, host validation, or spreadsheet formula escaping.
4. **Run the Full Test Suite:** Verify that all 210 tests pass prior to submitting a PR:
   ```bash
   python -m pytest
   ```

---

## License

This project is licensed under the **Apache 2.0 License**. See the [LICENSE](LICENSE) file for details.
