import tempfile
from pathlib import Path
import pytest
from datahunt.db import run_migrations, get_connection
from datahunt.orchestrator import ResearchOrchestrator
from datahunt.tools import SearchTool, FetchTool, MockSearchProvider, SearchHit, ExportTool

@pytest.fixture
def temp_env():
    temp_dir = Path(tempfile.mkdtemp())
    db_path = temp_dir / "test_orch.sqlite3"
    export_dir = temp_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    run_migrations(db_path=db_path)
    yield db_path, export_dir

def test_orchestrator_end_to_end(temp_env):
    db_path, export_dir = temp_env
    
    mock_hits = [
        SearchHit(
            url="https://company.example.com/job/lead-ai",
            title="Lead AI Engineer - Dubai",
            snippet="Join our Dubai team as Lead AI Engineer.",
            source_domain="company.example.com"
        )
    ]
    mock_html = """
    <html>
      <head><title>Lead AI Engineer - Dubai</title></head>
      <body>
        <h1>Lead AI Engineer</h1>
        <div class="company">Dubai AI Holdings</div>
        <div class="location">Dubai, UAE</div>
        <p>Seeking Lead AI Engineer for autonomous agents research.</p>
        <span class="date">Posted: 2026-09-06</span>
      </body>
    </html>
    """
    
    search_tool = SearchTool(provider=MockSearchProvider(mock_hits))
    fetch_tool = FetchTool(mock_responses={"https://company.example.com/job/lead-ai": mock_html})
    export_tool = ExportTool(export_dir=export_dir)

    from datahunt.llm.gemini_client import GeminiClient

    orch = ResearchOrchestrator(
        gemini_client=GeminiClient(api_key=""),
        search_tool=search_tool,
        fetch_tool=fetch_tool,
        export_tool=export_tool,
    )
    # Point repos to temp DB
    orch.task_repo.conn_factory = lambda: get_connection(db_path)
    orch.run_repo.conn_factory = lambda: get_connection(db_path)
    orch.doc_repo.conn_factory = lambda: get_connection(db_path)
    orch.record_repo.conn_factory = lambda: get_connection(db_path)
    orch.export_repo.conn_factory = lambda: get_connection(db_path)
    orch.tool_repo.conn_factory = lambda: get_connection(db_path)

    task, run = orch.create_task_and_run(
        request_text="Find 10 AI Jobs in Dubai",
        max_records=10,
        output_format="json"
    )
    assert task.status.value == "accepted"
    assert run.status.value == "planned"

    result = orch.execute_run(run.id)
    assert result["status"] in ["completed", "partial"]
    assert result["export_file"] is not None
    assert "confidence" in result
    assert result["confidence"] >= 0.0

    export_file_path = export_dir / result["export_file"]
    assert export_file_path.exists()
