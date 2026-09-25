import pytest
from unittest.mock import MagicMock
from agent import JobHuntAgent, DeepResearchAgent, MarketIntelAgent
from agent.orchestrator import ResearchOrchestrator
from datahunt.models import ResearchSpec, SourceDocument
from datahunt.tools.extract import (
    ExtractTool,
    extract_job_records_from_document,
    extract_technical_knowledge_records,
    extract_market_competitor_records,
)
from datahunt.tools.search import (
    generate_ats_queries,
    generate_technical_research_queries,
    generate_market_research_queries,
)
from datahunt.tools.verify import VerifyTool
from datahunt.llm.gemini_client import (
    synthesize_local_research_dossier,
    synthesize_local_market_dossier,
    GeminiClient,
)


# =====================================================================
# EVALUATION SUITE: 1. JOB HUNT AGENT (0-Sec Career Radar)
# =====================================================================

def test_job_agent_spec_and_ats_harvesting():
    """Verify JobHuntAgent intake, query planning, and extraction."""
    agent = JobHuntAgent()
    assert agent.mode == "jobs"
    assert agent.default_output_format == "json"

    # Test query planning
    queries = generate_ats_queries("GenAI Engineer in Dubai 0-sec")
    assert any("site:greenhouse.io" in q for q in queries)
    assert any("dubai" in q.lower() for q in queries)

    # Test extraction
    meta = {
        "url": "https://jobs.lever.co/careem/98765432-1234-5678",
        "title": "Careem - Senior GenAI Platform Engineer",
        "domain": "jobs.lever.co"
    }
    doc_text = """
    Careem - Senior GenAI Platform Engineer
    Location: Dubai, UAE
    Salary: AED 45,000 - 55,000 per month
    0 sec ago
    Join our Dubai AI engineering team.
    """
    records = extract_job_records_from_document(doc_text, meta, "GenAI Engineer")
    assert len(records) == 1
    rec = records[0]["fields"]
    assert "Senior GenAI Platform Engineer" in rec["title"]
    assert rec["company"] == "Careem"
    assert "Dubai" in rec["location"]
    assert rec["posted_age_seconds"] == 0
    assert "0-SEC" in rec["freshness_badge"] or "JUST NOW" in rec["freshness_badge"]


def test_job_agent_summary_formatting():
    """Verify Job agent generates Career Radar formatted report."""
    client = GeminiClient()
    run_meta = {
        "request_text": "Find AI Engineer roles in Dubai",
        "records_verified": 3,
        "pages_fetched": 5,
        "records_rejected": 1
    }
    verified_records = [
        {"title": "Staff AI Engineer", "company": "TechCorp", "salary": "$180,000", "application_url": "https://example.com/job/1"}
    ]
    summary = client.summarize_run(run_meta, verified_records, query_text="Find AI Engineer roles in Dubai", agent_mode="jobs")
    assert "Career Radar Intelligence" in summary
    assert "3 active roles" in summary or "1 active roles" in summary


# =====================================================================
# EVALUATION SUITE: 2. DEEP RESEARCH AGENT (Technical Dossiers)
# =====================================================================

def test_research_agent_spec_and_query_planning():
    """Verify DeepResearchAgent intake and technical query synthesis."""
    agent = DeepResearchAgent()
    assert agent.mode == "research"
    assert agent.default_output_format == "md"

    tech_queries = generate_technical_research_queries("Vector Search HNSW Indexing")
    assert len(tech_queries) >= 4
    assert any("architecture" in q or "internals" in q for q in tech_queries)


def test_research_agent_extraction_no_hardcoded_leak():
    """Verify DeepResearchAgent fallback extracts dynamic concepts with zero hardcoded LangChain leakage."""
    meta = {
        "url": "https://docs.docker.com/engine/architecture/",
        "title": "Docker Architecture and Container Engine Internals",
        "domain": "docs.docker.com"
    }
    doc_text = """
    # Docker Daemon Engine
    The Docker daemon (dockerd) listens for Docker API requests and manages Docker objects such as images, containers, networks, and volumes.
    A daemon can also communicate with other daemons to manage Docker services.
    - Manages container lifecycle and namespaces
    - Exposes REST API endpoint over UNIX socket
    - Handles storage driver abstraction

    ```python
    import docker
    client = docker.from_env()
    client.containers.run("alpine", ["echo", "hello world"])
    ```

    # Containerd Runtime
    containerd is an industry-standard container runtime with an emphasis on simplicity, robustness and portability.
    It is available as a daemon for Linux and Windows.
    - Manages complete container lifecycle
    - Handles image transfer and storage
    - Supervises low-level runc execution
    """
    records = extract_technical_knowledge_records(doc_text, meta, "Docker Architecture")
    assert len(records) >= 2
    names = [r["fields"]["name"] for r in records]
    assert any("Docker Daemon Engine" in n or "Docker" in n for n in names)
    assert any("Containerd Runtime" in n or "Containerd" in n for n in names)
    # Ensure no hardcoded LangChain concepts leaked
    assert not any("Runnable" in n or "LCEL" in n for n in names)


def test_research_agent_dossier_synthesis():
    """Verify DeepResearchAgent synthesizes publication-grade research dossier."""
    run_meta = {"pages_fetched": 4, "records_verified": 2}
    verified_records = [
        {
            "name": "Docker Daemon Engine",
            "category": "Architecture & Primitives",
            "description": "The Docker daemon listens for Docker API requests and manages Docker objects.",
            "core_capabilities": ["Lifecycle management", "REST API endpoint"],
            "documentation_url": "https://docs.docker.com/engine/architecture/"
        }
    ]
    dossier = synthesize_local_research_dossier(
        query="Docker Architecture",
        run_metadata=run_meta,
        verified_records=verified_records,
        source_texts="Source URL: https://docs.docker.com/engine/architecture/\nDocker daemon core concepts."
    )
    assert "# Executive Research Dossier: Docker Architecture" in dossier
    assert "## 1. Executive Summary & Core Value Proposition" in dossier
    assert "## 2. Architectural Blueprint & Core Mechanics" in dossier
    assert "Docker Daemon Engine" in dossier
    assert "https://docs.docker.com/engine/architecture/" in dossier


# =====================================================================
# EVALUATION SUITE: 3. MARKET INTEL AGENT (SaaS Pricing & Competitors)
# =====================================================================

def test_market_agent_spec_and_query_planning():
    """Verify MarketIntelAgent intake and market query synthesis."""
    agent = MarketIntelAgent()
    assert agent.mode == "market"
    assert agent.default_output_format == "json"

    market_queries = generate_market_research_queries("Supabase vs Firebase")
    assert len(market_queries) >= 4
    assert any("pricing" in q.lower() or "cost" in q.lower() or "alternatives" in q.lower() for q in market_queries)


def test_market_agent_extraction():
    """Verify MarketIntelAgent extracts competitor profiles, pricing tiers, and capabilities."""
    meta = {
        "url": "https://supabase.com/pricing",
        "title": "Supabase Pricing - Open Source Firebase Alternative",
        "domain": "supabase.com"
    }
    doc_text = """
    # Supabase
    The open source Firebase alternative with Postgres database, authentication, instant APIs, and edge functions.
    Pricing starts with a generous free tier for prototyping.
    - Free tier: $0/month includes 500MB database and 50,000 MAUs
    - Pro tier: $25/month includes 8GB database, 100,000 MAUs, and daily backups
    - Enterprise: custom volume pricing, SOC2 compliance, and dedicated SLA
    Starting at $0 per month.
    Target audience: Developers, startups, and modern engineering teams.
    Strengths: Full Postgres access, SQL-first design, open source ecosystem.

    # Neon Serverless Postgres
    Serverless Postgres built for the cloud with bottomless storage and instant branching.
    - Free tier: $0 with 0.5 GB storage
    - Launch plan: $19/month usage-based
    - Scale plan: $69/month
    Starting price: $0 free tier available.
    Target audience: Cloud developers and scale-ups.
    Strengths: Instant branch creation, separation of storage and compute.
    """
    records = extract_market_competitor_records(doc_text, meta, "Postgres Backend")
    assert len(records) >= 2
    comp_names = [r["fields"]["company_name"] for r in records]
    assert any("Supabase" in n for n in comp_names)
    assert any("Neon" in n for n in comp_names)

    supa = next(r["fields"] for r in records if "Supabase" in r["fields"]["company_name"])
    assert "pricing_model" in supa
    assert supa["website_url"] == meta["url"]
    assert len(supa["key_features"]) > 0


def test_market_agent_dossier_synthesis():
    """Verify MarketIntelAgent synthesizes executive market intelligence report."""
    run_meta = {"pages_fetched": 6, "records_verified": 2}
    verified_records = [
        {
            "company_name": "Supabase",
            "product_name": "Supabase Backend",
            "pricing_model": "Freemium / Tiered",
            "starting_price": "$0 / Free Tier",
            "target_audience": "Developers & Startups",
            "key_features": ["Postgres DB", "Auth & Row-Level Security", "Edge Functions"],
            "strengths": "Full PostgreSQL power with instant real-time APIs.",
            "website_url": "https://supabase.com"
        },
        {
            "company_name": "Firebase",
            "product_name": "Google Firebase",
            "pricing_model": "Usage-Based / Pay-As-You-Go",
            "starting_price": "Free Spark Plan",
            "target_audience": "Mobile & Web Developers",
            "key_features": ["Firestore NoSQL", "Firebase Auth", "Cloud Functions"],
            "strengths": "Tightly integrated Google Cloud ecosystem and turnkey mobile SDKs.",
            "website_url": "https://firebase.google.com"
        }
    ]
    dossier = synthesize_local_market_dossier(
        query="Supabase vs Firebase",
        run_metadata=run_meta,
        verified_records=verified_records,
        source_texts="Source URL: https://supabase.com/pricing\nSupabase pricing and plans."
    )
    assert "# 📊 Market & Competitive Intelligence Report: Supabase vs Firebase" in dossier
    assert "## 1. Executive Landscape & Market Dynamics" in dossier
    assert "## 2. Competitor Feature & Architecture Matrix" in dossier
    assert "## 3. SaaS & API Pricing Tier Comparison" in dossier
    assert "Supabase" in dossier
    assert "Firebase" in dossier
    assert "https://supabase.com" in dossier
