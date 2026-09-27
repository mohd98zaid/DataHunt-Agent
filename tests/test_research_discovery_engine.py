import pytest
from unittest.mock import MagicMock
from datahunt.agent.research_discovery import (
    ResearchDiscoveryEngine,
    ResearchSourceTier,
    classify_research_source,
)
from datahunt.models.evidence import Evidence
from datahunt.models.intent import ResearchIntent, ResearchIntentSpec, ResearchOutputType
from datahunt.models import SourceDocument
from datahunt.tools.base import ToolResult


def test_classify_research_source():
    """Verify source tier classification and weights across different domain types."""
    tier, weight = classify_research_source("https://langchain-ai.github.io/langgraph/concepts/")
    assert tier == ResearchSourceTier.OFFICIAL_DOCS
    assert weight == 1.0

    tier, weight = classify_research_source("https://docs.crewai.com/core-concepts/Agents/")
    assert tier == ResearchSourceTier.OFFICIAL_DOCS
    assert weight == 1.0

    tier, weight = classify_research_source("https://arxiv.org/abs/2401.00001")
    assert tier == ResearchSourceTier.ACADEMIC
    assert weight >= 0.9

    tier, weight = classify_research_source("https://martinfowler.com/articles/enterprise-patterns.html")
    assert tier == ResearchSourceTier.ENGINEERING_BLOG
    assert weight >= 0.8

    tier, weight = classify_research_source("https://stackoverflow.com/questions/123456/langgraph-state")
    assert tier == ResearchSourceTier.TECH_COMMUNITY
    assert weight >= 0.6

    tier, weight = classify_research_source("https://example.com/some-random-post")
    assert tier == ResearchSourceTier.GENERAL_WEB
    assert weight == 0.5


def test_golden_langgraph_vs_crewai_comparison():
    """
    Golden Comparison Test:
    'Compare LangGraph and CrewAI for production agent orchestration.'
    Verifies multi-wave queries, source tiering, canonical Evidence models,
    and structured comparison synthesis table.
    """
    mock_search = MagicMock()
    mock_fetch = MagicMock()

    # Configure mock search returns
    def mock_search_exec(query, limit=8, **kwargs):
        q_low = query.lower()
        if "langgraph" in q_low and "official" in q_low:
            hits = [
                {"title": "LangGraph Official Documentation", "url": "https://langchain-ai.github.io/langgraph/", "snippet": "Stateful cyclic graph orchestration framework."},
                {"title": "LangGraph GitHub", "url": "https://github.com/langchain-ai/langgraph", "snippet": "Build resilient language agents with cyclic graphs."},
            ]
        elif "crewai" in q_low and "official" in q_low:
            hits = [
                {"title": "CrewAI Official Documentation", "url": "https://docs.crewai.com/", "snippet": "Cutting-edge framework for orchestrating role-playing autonomous AI agents."},
                {"title": "CrewAI GitHub", "url": "https://github.com/joaomdmoura/crewAI", "snippet": "Framework for orchestrating role-playing autonomous AI agents."},
            ]
        elif "trade-off" in q_low or "limitation" in q_low or "drawback" in q_low:
            hits = [
                {"title": "Agent Production Trade-Offs", "url": "https://martinfowler.com/articles/agent-tradeoffs.html", "snippet": "Graph models add debugging overhead while crew models have context drift limitations."},
            ]
        else:
            hits = [
                {"title": "Multi-Agent Production Benchmark", "url": "https://engineering.fb.com/ai-agents-benchmark", "snippet": "Benchmarking latency, state management, and memory overhead in production."},
            ]
        return ToolResult(success=True, data=hits)

    mock_search.execute.side_effect = mock_search_exec

    # Configure mock fetch returns
    def mock_fetch_exec(url, run_id=None):
        if "langgraph" in url:
            text = """# LangGraph Architecture
LangGraph is a library for building stateful, multi-actor applications with LLMs.
It extends LangChain with the ability to coordinate multiple chains or actors across cyclic steps using a graph data model.
Key features include persistent state checkpoints, human-in-the-loop approvals, and streaming execution.
Limitation: Steep learning curve for complex graph state definitions.
"""
            title = "LangGraph Official Architecture"
        elif "crewai" in url:
            text = """# CrewAI Architecture
CrewAI provides high-level abstractions for multi-agent collaboration.
Agents assume distinct roles, goals, and backstories to complete tasks sequentially or hierarchically.
Limitation: Context window drift and non-deterministic task coordination under high concurrency.
"""
            title = "CrewAI Framework Overview"
        else:
            text = """# Production Benchmark
Graph state persistence allows state rollback and deterministic recovery at the cost of boilerplate.
Crew abstractions enable rapid development but lack fine-grained edge-level control.
"""
            title = "Benchmark Report"

        doc = SourceDocument(
            id=f"doc_{hash(url)}",
            run_id="run_comp_test",
            requested_url=url,
            canonical_url=url,
            title=title,
            domain="mock.com",
            extracted_text=text,
        )
        res = MagicMock()
        res.success = True
        res.data = doc
        return res

    mock_fetch.execute.side_effect = mock_fetch_exec

    engine = ResearchDiscoveryEngine(
        search_tool=mock_search,
        fetch_tool=mock_fetch,
        gemini_client=None,
    )

    query = "Compare LangGraph and CrewAI for production agent orchestration."
    result = engine.run_research(query=query, max_runtime_seconds=30)

    assert result["status"] == "completed"
    assert len(result["queries_executed"]) >= 3
    assert len(result["source_documents"]) >= 2

    # Check evidence collection
    evidence_list = result["evidence"]
    assert len(evidence_list) > 0
    for ev in evidence_list:
        assert isinstance(ev, Evidence)
        assert ev.claim
        assert ev.source_url
        assert ev.source_type in [t.value for t in ResearchSourceTier]
        assert 0.0 <= ev.confidence <= 1.0

    # Verify counter-evidence / trade-offs detected
    counter_ev = [e for e in evidence_list if e.is_counter_evidence]
    assert len(counter_ev) >= 1

    # Check Markdown dossier structure
    markdown = result["answer_markdown"]
    assert "# ⚖️ Technical Architecture Comparison: LangGraph vs CrewAI" in markdown
    assert "Head-to-Head Architectural Matrix" in markdown
    assert "State Management" in markdown
    assert "Human-in-the-Loop" in markdown
    assert "Production Recommendation" in markdown
    assert "Authoritative Primary Sources & Citations" in markdown


def test_what_is_langgraph_explanation():
    """Verify explanation queries synthesize a technical explanation report."""
    mock_search = MagicMock()
    mock_fetch = MagicMock()

    mock_search.execute.return_value = ToolResult(
        success=True,
        data=[
            {"title": "LangGraph Intro", "url": "https://langchain-ai.github.io/langgraph/", "snippet": "State machine library for agent workflows."}
        ]
    )

    doc = SourceDocument(
        id="doc_lg",
        run_id="run_exp",
        requested_url="https://langchain-ai.github.io/langgraph/",
        canonical_url="https://langchain-ai.github.io/langgraph/",
        title="LangGraph Overview",
        domain="langchain-ai.github.io",
        extracted_text="LangGraph enables building robust multi-agent state machines with cyclic flow control and durable checkpoints.",
    )
    mock_res = MagicMock()
    mock_res.success = True
    mock_res.data = doc
    mock_fetch.execute.return_value = mock_res

    engine = ResearchDiscoveryEngine(
        search_tool=mock_search,
        fetch_tool=mock_fetch,
        gemini_client=None,
    )

    result = engine.run_research(query="What is LangGraph?", max_runtime_seconds=15)
    assert result["status"] == "completed"
    assert "LangGraph" in result["answer_markdown"]
    assert len(result["evidence"]) >= 1
    assert result["evidence"][0].source_type == ResearchSourceTier.OFFICIAL_DOCS.value
