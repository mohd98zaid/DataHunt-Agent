"""
Unit, integration, and negative test suite for DataHunt Shared Intelligence Layer.
Covers all 15 agents, EvidenceStore, TaskController, and negative verification invariants.
"""
import pytest
from datetime import datetime, timezone, timedelta
from datahunt.models.shared_intel import (
    SourceCategory, SourcePlan, EntityType, EntityIdentity, Fact,
    EvidenceRelationship, EvidenceConfidence, SharedEvidence,
    FreshnessRating, FreshnessAssessment, ContradictionStatus,
    ContradictionItem, CoverageGap, SharedCoverageState, RiskSeverity,
    RiskStatus, RiskItem, PersonRole, PersonProfile, ContactType,
    ContactInfo, CompanyIntelligenceProfile, NewsEvent, OpportunityMatch,
    MonitoringChangeType, MonitoringEvent
)
from datahunt.evidence.store import EvidenceStore
from datahunt.agents.source_intelligence import SourceIntelligenceAgent
from datahunt.agents.entity_resolution import EntityResolutionAgent
from datahunt.agents.fact_extraction import FactExtractionAgent
from datahunt.agents.evidence import EvidenceAgent
from datahunt.agents.freshness import FreshnessAgent
from datahunt.agents.contradiction import ContradictionAgent
from datahunt.agents.coverage import CoverageAgent
from datahunt.agents.risk import RiskAgent
from datahunt.agents.company_intelligence import CompanyIntelligenceAgent
from datahunt.agents.people_intelligence import PeopleIntelligenceAgent
from datahunt.agents.contact_discovery import ContactDiscoveryAgent
from datahunt.agents.opportunity_matching import OpportunityMatchingAgent
from datahunt.agents.news_intelligence import NewsIntelligenceAgent
from datahunt.agents.monitoring import MonitoringAgent
from datahunt.agents.feedback import FeedbackAgent
from datahunt.agent.task_controller import TaskController
from datahunt.tools.base import ToolResult


# ==============================================================================
# 1. Source Intelligence Agent
# ==============================================================================

def test_source_intelligence_agent_domain_planning():
    agent = SourceIntelligenceAgent()

    # Market domain planning
    market_plan = agent.plan_sources(domain="market", query="top momentum stocks NSE")
    assert SourceCategory.EXCHANGE in market_plan.source_types
    assert SourceCategory.FINANCIAL_DATA in market_plan.source_types
    assert any("nseindia.com" in s for s in market_plan.primary_sources)
    assert len(market_plan.search_queries) >= 2

    # Job domain planning
    job_plan = agent.plan_sources(domain="jobs", query="GenAI Engineer", location="Dubai, UAE")
    assert SourceCategory.ATS in job_plan.source_types
    assert any("greenhouse.io" in s for s in job_plan.primary_sources)
    assert any("bayt.com" in s for s in job_plan.secondary_sources)

    # Dynamic source discovery from search hits
    search_hits = [
        {"url": "https://careers.acme.com/jobs/123", "title": "Acme Jobs"},
        {"url": "https://www.moneycontrol.com/stocks/infy", "title": "Infosys"},
    ]
    discovered = agent.discover_sources_from_results(search_hits)
    assert "careers.acme.com" in discovered
    assert "moneycontrol.com" in discovered


# ==============================================================================
# 2. Entity Resolution Agent
# ==============================================================================

def test_entity_resolution_agent_stocks():
    agent = EntityResolutionAgent()

    # Authoritative symbols
    infy = agent.resolve_stock("INFY")
    assert infy is not None
    assert infy.canonical_name == "INFY"
    assert infy.identifiers.get("symbol") == "INFY"

    # Alias / company name mapping
    infy_alias = agent.resolve_stock("Infosys Ltd")
    assert infy_alias is not None
    assert infy_alias.canonical_name == "INFY"

    # Negative test: Banned news platforms must NEVER resolve as stocks
    assert agent.resolve_stock("TradingView") is None
    assert agent.resolve_stock("IndiaTimes") is None
    assert agent.resolve_stock("Stockezee") is None


def test_entity_resolution_agent_companies_and_negative():
    agent = EntityResolutionAgent()

    # Canonical legal suffix collapsing
    c1 = agent.resolve_company("Acme Corporation Inc.", domain="https://www.acme.com")
    c2 = agent.resolve_company("Acme Corp", domain="https://acme.com")
    assert c1.entity_id == c2.entity_id
    assert c1.canonical_name == "Acme Corporation Inc."

    # Negative test: Must NOT merge unrelated companies with different domains
    c3 = agent.resolve_company("Acme Logistics Ltd", domain="https://acmelogistics.com")
    assert c1.entity_id != c3.entity_id


def test_entity_resolution_agent_people_and_negative():
    agent = EntityResolutionAgent()

    p1 = agent.resolve_person("John Doe", company="Acme Corp", role="Recruiter")
    p2 = agent.resolve_person("John Doe", company="Acme Corp", role="Talent Lead")
    assert p1.entity_id == p2.entity_id

    # Negative test: Must NOT merge people with same name at DIFFERENT companies
    p3 = agent.resolve_person("John Doe", company="Beta Global", role="Recruiter")
    assert p1.entity_id != p3.entity_id


# ==============================================================================
# 3. Fact Extraction Agent
# ==============================================================================

def test_fact_extraction_agent_unit_preservation():
    agent = FactExtractionAgent()
    text = """
    Acme AI reported revenue of ₹ 4,500 crore in Q3, representing a strong 15.5% operating margin.
    Net profit reached USD 120 million.
    The company has a total workforce of 25,000+ employees and is headquartered in Riyadh, Saudi Arabia.
    Founded in 2018, it offers compensation of ₹ 35 LPA with required 5+ years of experience.
    """
    facts = agent.extract_facts(text=text, entity_id="ent_acme", domain="all")

    field_map = {f.field: f for f in facts}

    assert "revenue" in field_map
    assert field_map["revenue"].unit == "₹ crore"
    assert "4,500" in str(field_map["revenue"].value)

    assert "operating_margin" in field_map
    assert field_map["operating_margin"].unit == "%"

    assert "net_profit" in field_map
    assert field_map["net_profit"].unit == "USD million"

    assert "employee_count" in field_map
    assert "25,000" in str(field_map["employee_count"].value)

    assert "headquarters" in field_map
    assert "Riyadh" in str(field_map["headquarters"].value)

    assert "salary" in field_map
    assert field_map["salary"].unit == "LPA"

    assert "experience_years" in field_map
    assert "5" in str(field_map["experience_years"].value)


# ==============================================================================
# 4. Evidence Agent & EvidenceStore
# ==============================================================================

def test_evidence_agent_and_store():
    store = EvidenceStore()
    agent = EvidenceAgent()

    # Supporting evidence
    text_support = "Acme Corp announced an expansion of its generative AI engineering center in Dubai."
    claim = "Acme Corp expanding AI engineering in Dubai"
    ev1 = agent.evaluate_claim_in_text(
        claim=claim,
        text=text_support,
        entity_id="ent_acme",
        source_url="https://news.acme.com/announcement",
    )
    assert ev1 is not None
    assert ev1.relationship == EvidenceRelationship.SUPPORTS
    store.add_evidence(ev1)

    # Contradicting evidence
    text_contradict = "Acme Corp denied and rejected reports that it is expanding its AI engineering center in Dubai."
    ev2 = agent.evaluate_claim_in_text(
        claim=claim,
        text=text_contradict,
        entity_id="ent_acme",
        source_url="https://news.acme.com/correction",
    )
    assert ev2 is not None
    assert ev2.relationship == EvidenceRelationship.CONTRADICTS
    store.add_evidence(ev2)

    # Query store
    entity_ev = store.evidence_for_entity("ent_acme")
    assert len(entity_ev) == 2

    claim_ev = store.evidence_for_claim("expanding AI engineering")
    assert len(claim_ev) == 2

    sources = store.sources_for_entity("ent_acme")
    assert "https://news.acme.com/announcement" in sources
    assert "https://news.acme.com/correction" in sources


# ==============================================================================
# 5. Freshness Agent
# ==============================================================================

def test_freshness_agent_domain_horizons():
    agent = FreshnessAgent()
    now = datetime.now(timezone.utc)

    # 1. Market price: 10 mins ago -> FRESH; 2 hours ago -> AGING; 3 days ago -> EXPIRED
    ts_10m = (now - timedelta(minutes=10)).isoformat()
    eval_price_fresh = agent.evaluate_freshness("item_1", "price", ts_10m, domain="market_price")
    assert eval_price_fresh.rating == FreshnessRating.FRESH

    ts_2h = (now - timedelta(hours=2)).isoformat()
    eval_price_aging = agent.evaluate_freshness("item_2", "price", ts_2h, domain="market_price")
    assert eval_price_aging.rating == FreshnessRating.AGING

    ts_3d = (now - timedelta(days=3)).isoformat()
    eval_price_expired = agent.evaluate_freshness("item_3", "price", ts_3d, domain="market_price")
    assert eval_price_expired.rating == FreshnessRating.EXPIRED

    # 2. Job posting: 5 days ago -> FRESH; 25 days ago -> AGING; 65 days ago -> EXPIRED
    ts_5d = (now - timedelta(days=5)).isoformat()
    eval_job_fresh = agent.evaluate_freshness("job_1", "posting", ts_5d, domain="job_posting")
    assert eval_job_fresh.rating == FreshnessRating.FRESH

    ts_25d = (now - timedelta(days=25)).isoformat()
    eval_job_aging = agent.evaluate_freshness("job_2", "posting", ts_25d, domain="job_posting")
    assert eval_job_aging.rating == FreshnessRating.AGING

    # 3. Missing timestamp -> UNKNOWN
    eval_unknown = agent.evaluate_freshness("job_3", "posting", None, domain="job_posting")
    assert eval_unknown.rating == FreshnessRating.UNKNOWN


# ==============================================================================
# 6. Contradiction Agent
# ==============================================================================

def test_contradiction_agent_detection():
    agent = ContradictionAgent()

    # Conflicting revenue growth numbers from different sources
    f1 = Fact(
        entity_id="ent_1",
        field="revenue_growth",
        value="15%",
        unit="%",
        source="https://source-a.com/report",
        published_at="2026-01-15",
    )
    f2 = Fact(
        entity_id="ent_1",
        field="revenue_growth",
        value="11%",
        unit="%",
        source="https://source-b.com/report",
        published_at="2026-02-01",
    )
    # Concordant fact
    f3 = Fact(
        entity_id="ent_1",
        field="headquarters",
        value="Dubai",
        source="https://source-a.com/about",
    )
    f4 = Fact(
        entity_id="ent_1",
        field="headquarters",
        value="Dubai",
        source="https://source-b.com/about",
    )

    conflicts = agent.detect_conflicts([f1, f2, f3, f4])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.entity_id == "ent_1"
    assert c.resolution_status == ContradictionStatus.UNRESOLVED
    assert "15%" in c.possible_reason and "11%" in c.possible_reason
    assert "Timestamps differ" in c.possible_reason


# ==============================================================================
# 7. Coverage Agent
# ==============================================================================

def test_coverage_agent_gap_detection():
    agent = CoverageAgent()

    # 3 candidates: Candidate 1 has full data, Candidates 2 & 3 lack counter-evidence and fundamentals
    c1 = EntityIdentity(entity_type=EntityType.STOCK, canonical_name="TCS", confidence=1.0)
    c2 = EntityIdentity(entity_type=EntityType.STOCK, canonical_name="INFY", confidence=1.0)
    c3 = EntityIdentity(entity_type=EntityType.STOCK, canonical_name="RELIANCE", confidence=1.0)

    facts = {
        c1.entity_id: [
            Fact(entity_id=c1.entity_id, field="current_price", value=3800),
            Fact(entity_id=c1.entity_id, field="revenue", value="60000 cr"),
        ],
        c2.entity_id: [
            Fact(entity_id=c2.entity_id, field="current_price", value=1500),
        ],
        c3.entity_id: [],
    }
    evidence = {
        c1.entity_id: [
            SharedEvidence(claim="Growth strong", entity_id=c1.entity_id, source_url="https://s1.com", relationship=EvidenceRelationship.SUPPORTS),
            SharedEvidence(claim="Margin risks", entity_id=c1.entity_id, source_url="https://s2.com", relationship=EvidenceRelationship.CONTRADICTS),
        ],
        c2.entity_id: [],
        c3.entity_id: [],
    }

    cov = agent.evaluate_coverage(
        task_id="task_test",
        domain="market",
        candidates=[c1, c2, c3],
        facts_by_entity=facts,
        evidence_by_entity=evidence,
        target_count=3,
    )

    assert cov.candidates_discovered == 3
    assert cov.candidates_validated == 3
    assert cov.is_sufficient is False  # Missing fundamentals and counter-evidence for 2 candidates
    assert len(cov.missing_gaps) > 0
    # Check that suggested queries were formulated for gaps
    suggested = [q for g in cov.missing_gaps for q in g.suggested_queries]
    assert any("INFY" in q for q in suggested)
    assert any("RELIANCE" in q for q in suggested)


# ==============================================================================
# 8. Risk Agent
# ==============================================================================

def test_risk_agent_evidence_backed_and_negative():
    agent = RiskAgent()

    # Case A: Concrete risk disclosures
    text_with_risk = """
    SEBI issued a show cause notice regarding audit qualification in the company's financial records.
    The promoter pledge remains high at 48% of total holding.
    """
    risks = agent.analyze_risks("ent_risky", text_with_risk, source_urls=["https://filing.com"])
    known_risks = [r for r in risks if r.status == RiskStatus.KNOWN_RISK]
    assert len(known_risks) >= 2
    types = [r.risk_type for r in known_risks]
    assert "regulatory_probe" in types
    assert "promoter_pledge" in types

    # Case B: Negative test — Zero synthetic hallucinations when disclosures are clean
    clean_text = "Acme Corp announced normal quarterly hiring and steady customer satisfaction."
    clean_risks = agent.analyze_risks("ent_clean", clean_text)
    assert not any(r.status == RiskStatus.KNOWN_RISK for r in clean_risks)
    assert any(r.status == RiskStatus.UNKNOWN for r in clean_risks)


# ==============================================================================
# 9. Company Intelligence Agent (Zero-Fabrication)
# ==============================================================================

def test_company_intelligence_zero_fabrication_on_offline():
    agent = CompanyIntelligenceAgent()

    # When network is disallowed / scraping fails
    profile = agent.get_company_profile("Unknown Steath AI Inc.", allow_network=False)
    assert profile.is_available is False
    assert profile.leadership == []
    assert profile.products == []
    assert profile.technology == []
    assert profile.financial_information == {}


# ==============================================================================
# 10. People Intelligence Agent
# ==============================================================================

class MockSearchTool:
    def __init__(self, hits):
        self.hits = hits
    def execute(self, **kwargs):
        return ToolResult(success=True, data={"results": self.hits})


def test_people_intelligence_role_validation():
    mock_hits = [
        {
            "title": "Jane Doe - Head of AI & Machine Learning - TechNova | LinkedIn",
            "snippet": "Jane Doe leads the artificial intelligence and LLM infrastructure team at TechNova.",
            "url": "https://linkedin.com/in/janedoe",
        },
        {
            "title": "Mark Davis - Senior Technical Recruiter - TechNova | LinkedIn",
            "snippet": "Mark Davis recruits senior AI engineers and backend infrastructure engineers for TechNova.",
            "url": "https://linkedin.com/in/markdavis",
        },
    ]
    agent = PeopleIntelligenceAgent(search_tool=MockSearchTool(mock_hits))
    people = agent.find_people("TechNova", max_results=2)

    assert len(people) == 2
    roles = {p.name: p.role for p in people}
    assert roles.get("Jane Doe") == PersonRole.AI_LEADER
    assert roles.get("Mark Davis") == PersonRole.RECRUITER


# ==============================================================================
# 11. Contact Discovery Agent (Zero-Fabrication)
# ==============================================================================

def test_contact_discovery_zero_fabrication():
    agent = ContactDiscoveryAgent(search_tool=MockSearchTool([]))
    contacts = agent.discover_contacts(
        entity_name="Acme Corp",
        entity_id="ent_acme",
        official_domain="acme.com",
    )
    # Generates official contact page URL, but does NOT invent fake emails
    assert len(contacts) == 1
    assert contacts[0].contact_type == ContactType.COMPANY_PAGE
    assert "https://acme.com/contact" in contacts[0].value
    # Negative test: No invented emails
    assert not any(c.contact_type == ContactType.EMAIL for c in contacts)


# ==============================================================================
# 12. Opportunity Matching Agent
# ==============================================================================

def test_opportunity_matching_hard_constraints():
    agent = OpportunityMatchingAgent()

    user_prefs = {
        "locations": ["Dubai, UAE"],
        "explicit_skills": ["langgraph", "pytorch"],
        "inferred_skills": ["docker"],
        "explicit_experience_max": 5.0,
    }

    # Case A: Fully matching job
    opp_qualified = {
        "title": "Senior AI Engineer",
        "company": "Acme AI",
        "location": "Dubai, UAE",
        "skills": ["langgraph", "pytorch", "docker"],
        "experience_years": 4.0,
    }
    m1 = agent.match_opportunity("c_1", opp_qualified, user_prefs)
    assert m1.is_qualified is True
    assert m1.match_score >= 0.70

    # Case B: Missing mandatory explicit skill -> Disqualified regardless of location/experience
    opp_missing_skill = {
        "title": "Senior AI Engineer",
        "company": "Acme AI",
        "location": "Dubai, UAE",
        "skills": ["pytorch", "docker"],  # Missing langgraph
        "experience_years": 4.0,
    }
    m2 = agent.match_opportunity("c_2", opp_missing_skill, user_prefs)
    assert m2.is_qualified is False
    assert any("Mandatory explicit skill missing" in req for req in m2.missing_requirements)
    assert m2.match_score <= 0.40

    # Case C: Location mismatch -> Disqualified
    opp_wrong_loc = {
        "title": "Senior AI Engineer",
        "company": "Acme AI",
        "location": "Singapore",
        "skills": ["langgraph", "pytorch", "docker"],
        "experience_years": 4.0,
    }
    m3 = agent.match_opportunity("c_3", opp_wrong_loc, user_prefs)
    assert m3.is_qualified is False
    assert any("Location mismatch" in req for req in m3.missing_requirements)


# ==============================================================================
# 13. News Intelligence Agent
# ==============================================================================

def test_news_intelligence_event_clustering():
    agent = NewsIntelligenceAgent()

    # 3 articles covering the same acquisition event
    articles = [
        {
            "title": "Acme Corp acquires CloudGen AI for $200M in strategic expansion",
            "summary": "Acme Corp announced the strategic acquisition of CloudGen AI to accelerate enterprise LLM development.",
            "url": "https://reuters.com/article1",
            "date": "2026-03-10",
        },
        {
            "title": "CloudGen AI acquired by Acme Corp in $200M enterprise expansion",
            "summary": "CloudGen AI has entered into a definitive merger agreement with Acme Corp.",
            "url": "https://bloomberg.com/article2",
            "date": "2026-03-10",
        },
        {
            "title": "Unrelated: Tech sector sees record venture investments in Q1",
            "summary": "Global tech venture capital flows reached record highs in the first quarter.",
            "url": "https://techcrunch.com/article3",
            "date": "2026-03-09",
        },
    ]

    events = agent.cluster_articles(articles)
    assert len(events) == 2  # The 2 Acme articles clustered into 1 event, plus the 1 unrelated article

    acme_event = [e for e in events if "CloudGen" in e.title or "Acme" in e.title][0]
    assert len(acme_event.sources) == 2
    assert acme_event.impact == "high"  # Contains "acquisition"


# ==============================================================================
# 14. Monitoring Agent
# ==============================================================================

def test_monitoring_agent_change_detection():
    agent = MonitoringAgent()

    # 1. Job status change
    job_events = agent.compare_snapshots(
        target_id="job_99",
        target_type="job",
        previous_snapshot={"status": "active"},
        current_snapshot={"status": "closed"},
    )
    assert len(job_events) == 1
    assert job_events[0].change_type == MonitoringChangeType.STATUS_CHANGED

    # 2. Stock price move
    stock_events = agent.compare_snapshots(
        target_id="NSE:INFY",
        target_type="stock",
        previous_snapshot={"price": 1500.0},
        current_snapshot={"price": 1550.0},
    )
    assert len(stock_events) == 1
    assert stock_events[0].change_type == MonitoringChangeType.PRICE_CHANGED
    assert "+3.33%" in stock_events[0].details


# ==============================================================================
# 15. Feedback Agent (Explicit Intent Override Invariant)
# ==============================================================================

def test_feedback_agent_bounded_weights():
    agent = FeedbackAgent()

    agent.record_feedback(
        user_id="test_user",
        item_id="rec_1",
        action="accepted",
        item_type="job",
        attributes={"company": "Acme AI", "skills": ["python", "langgraph"]},
    )

    boost = agent.get_soft_preference_boost(
        user_id="test_user",
        item_attributes={"company": "Acme AI", "skills": ["python"]},
    )
    # Modifier is bounded within [-0.05, 0.05]
    assert 0.0 < boost <= 0.05


# ==============================================================================
# 16. Task Controller End-to-End Coordination
# ==============================================================================

class MockFetchTool:
    def execute(self, url, **kwargs):
        class MockDoc:
            http_status = 200
            extracted_text = (
                "Infosys (NSE: INFY) reported revenue of ₹ 40,000 crore. "
                "Operating margin is 21.5%. SEBI cleared all past disclosures. "
                "Active in generative AI solutions."
            )
        return ToolResult(success=True, data=MockDoc())


def test_task_controller_coordination():
    mock_search = MockSearchTool([
        {"url": "https://nseindia.com/market-data/infy", "title": "Infosys Ltd - INFY", "snippet": "Infosys equity quotes and filings."},
    ])
    mock_fetch = MockFetchTool()

    controller = TaskController(search_tool=mock_search, fetch_tool=mock_fetch)

    events_received = []
    result = controller.run_intelligence_pipeline(
        query="Analyze top NSE stock Infosys",
        domain="market",
        target_count=1,
        max_duration_seconds=30,
        event_callback=lambda evt, data: events_received.append(evt),
    )

    assert "controller.started" in events_received
    assert "source_intel.planned" in events_received
    assert "controller.completed" in events_received

    assert result["domain"] == "market"
    assert len(result["entities"]) >= 1
    assert result["facts_count"] >= 1
    assert result["coverage"]["candidates_validated"] >= 1
    assert result["evidence_store_stats"]["total_facts"] >= 1


def test_orchestrator_people_workflow_and_properties(tmp_path):
    from datahunt.orchestrator import ResearchOrchestrator
    from datahunt.evidence.store import EvidenceStore
    from datahunt.agent.task_controller import TaskController
    from datahunt.db import run_migrations, get_connection
    from datahunt.tools import SearchTool, FetchTool, MockSearchProvider, SearchHit, ExportTool
    from datahunt.llm.gemini_client import GeminiClient

    db_path = tmp_path / "test_people.sqlite3"
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    run_migrations(db_path=db_path)

    mock_hits = [
        SearchHit(
            url="https://linkedin.com/in/sarah-smith",
            title="Sarah Smith - Senior Technical Recruiter - Acme AI | LinkedIn",
            snippet="Sarah Smith is a Senior Technical Recruiter specializing in GenAI engineering talent at Acme AI.",
            source_domain="linkedin.com"
        )
    ]
    search_tool = SearchTool(provider=MockSearchProvider(mock_hits))
    fetch_tool = FetchTool(mock_responses={})
    export_tool = ExportTool(export_dir=export_dir)

    orch = ResearchOrchestrator(
        gemini_client=GeminiClient(api_key=""),
        search_tool=search_tool,
        fetch_tool=fetch_tool,
        export_tool=export_tool,
    )

    # 1. Verify shared intelligence properties are exposed
    assert isinstance(orch.evidence_store, EvidenceStore)
    assert isinstance(orch.task_controller, TaskController)

    # 2. Point repositories to temp DB
    orch.task_repo.conn_factory = lambda: get_connection(db_path)
    orch.run_repo.conn_factory = lambda: get_connection(db_path)
    orch.doc_repo.conn_factory = lambda: get_connection(db_path)
    orch.record_repo.conn_factory = lambda: get_connection(db_path)
    orch.export_repo.conn_factory = lambda: get_connection(db_path)
    orch.tool_repo.conn_factory = lambda: get_connection(db_path)

    task, run = orch.create_task_and_run(
        request_text="Find recruiters for GenAI roles at Acme AI",
        agent_mode="people",
        max_records=5,
    )

    events = []
    res = orch.execute_run(run.id, event_callback=lambda evt, data: events.append((evt, data)))

    assert any(data.get("phase") == "PEOPLE_DISCOVERY" for evt, data in events if evt == "phase.change")
    assert res["status"].upper() in ("SUCCEEDED", "COMPLETED", "PARTIAL")
    records = orch.record_repo.list_records_for_run(run.id)
    assert len(records) >= 1
    rec = records[0]
    assert rec.record_type == "people_intelligence"
    assert rec.fields.get("person") == "Sarah Smith"
    assert str(rec.fields.get("role", "")).lower() in ("recruiter", "talent_acquisition")

