import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import (
    FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect,
    Query, Request
)
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from datahunt import APP_NAME, __version__
from datahunt.config import settings
from datahunt.db import (
    get_connection, TaskRepository, RunRepository,
    RecordRepository, ExportRepository, JobTrackingRepository
)
from datahunt.models import RunStatus, VerificationStatus, ExportRecord
from datahunt.orchestrator import ResearchOrchestrator
from datahunt.tracing import query_project_runs

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(
    title=f"{APP_NAME} API",
    description="Autonomous Research AI Agent API",
    version=__version__
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    # Prevent browser caching of frontend HTML, CSS, JS and API run status
    if (
        path.endswith((".html", ".css", ".js", ".json"))
        or path == "/"
        or path.startswith("/runs")
        or path.startswith("/api")
    ):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.middleware("http")
async def verify_api_key_middleware(request: Request, call_next):
    if settings.API_KEY and settings.API_KEY.strip():
        path = request.url.path
        if not (
            path.startswith("/frontend") or
            path.startswith("/css") or
            path.startswith("/js") or
            path.startswith("/docs") or
            path.startswith("/openapi.json") or
            path in ("/", "/favicon.ico", "/api/info", "/api/health")
        ):
            key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                key = auth_header[7:]
            if key != settings.API_KEY:
                return Response(
                    content=json.dumps({"detail": "Invalid or missing API key"}),
                    status_code=401,
                    media_type="application/json"
                )
    return await call_next(request)

task_repo = TaskRepository()
run_repo = RunRepository()
rec_repo = RecordRepository()
exp_repo = ExportRepository()
job_repo = JobTrackingRepository()

class CreateTaskRequest(BaseModel):
    query: str = Field(..., description="Natural language research query")
    max_records: int = Field(150, ge=1, le=500)
    freshness_days: Optional[int] = Field(7, ge=1)
    output_format: str = Field("json", pattern="^(json|csv|xlsx|md|docx)$")
    contact_policy: str = Field("business_public_only")
    allowed_domains: List[str] = Field(default_factory=list)
    blocked_domains: List[str] = Field(default_factory=list)
    run_in_background: bool = Field(False, description="Whether to execute asynchronously")
    model: Optional[str] = Field("auto", description="AI model engine: auto, gemini-flash-lite-latest, or gemini-3.8-flash")
    agent_mode: Optional[str] = Field("auto", description="Agent mode: auto, jobs, research, or market")

def run_task_background(run_id: str, model: Optional[str] = None):
    orch = ResearchOrchestrator(model=model)
    orch.execute_run(run_id)

@app.get("/api/info")
def api_info():
    return {
        "app": APP_NAME,
        "version": __version__,
        "status": "online",
        "docs_url": "/docs"
    }

@app.get("/")
def api_root():
    # Return index.html if exists, otherwise simple banner
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="""
        <html>
            <head><title>DataHunt Agent Core</title></head>
            <body style="font-family: sans-serif; background: #0b0f19; color: #00f0ff; padding: 2rem;">
                <h1>DataHunt AI Agent Core</h1>
                <p>Status: Synchronized & Operational</p>
                <p><a href="/docs" style="color: #00ffa3;">Swagger OpenAPI Specification &rarr;</a></p>
            </body>
        </html>
        """
    )

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">⚡</text></svg>'
    return Response(content=svg_icon, media_type="image/svg+xml")

@app.get("/health")
def api_health():
    try:
        conn = get_connection()
        conn.execute("SELECT 1;").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
        
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "gemini_configured": settings.is_gemini_configured,
        "model": settings.GEMINI_MODEL,
        "models_available": ["auto", "gemini-flash-lite-latest", "gemini-3.8-flash"]
    }

@app.post("/tasks", status_code=201)
def create_task(req: CreateTaskRequest, background_tasks: BackgroundTasks):
    orch = ResearchOrchestrator(model=req.model)
    try:
        task, run = orch.create_task_and_run(
            request_text=req.query,
            max_records=req.max_records,
            freshness_days=req.freshness_days,
            output_format=req.output_format,
            contact_policy=req.contact_policy,
            allowed_domains=req.allowed_domains,
            blocked_domains=req.blocked_domains,
            model=req.model,
            agent_mode=req.agent_mode,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.run_in_background:
        background_tasks.add_task(run_task_background, run.id, req.model)
        return {
            "task_id": task.id,
            "run_id": run.id,
            "status": "queued",
            "message": "Task accepted and running in background."
        }
    else:
        try:
            result = orch.execute_run(run.id)
            return {
                "task_id": task.id,
                "run_id": run.id,
                "status": result["status"],
                "records_verified": result["records_verified"],
                "records_rejected": result["records_rejected"],
                "confidence": result.get("confidence", 0.95 if result.get("records_verified", 0) > 0 else 0.0),
                "model": result.get("model", "gemini-3.8-flash"),
                "model_mode": result.get("model_mode", "auto"),
                "export_file": result.get("export_file"),
                "summary": result.get("summary")
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Execution error: {e}")

@app.get("/tasks")
def list_tasks(limit: int = Query(20, ge=1, le=100)):
    tasks = task_repo.list_tasks(limit=limit)
    return [
        {
            "id": t.id,
            "query": t.request_text,
            "status": t.status.value,
            "max_records": t.max_records,
            "created_at": t.created_at
        }
        for t in tasks
    ]

@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = task_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.model_dump()

@app.get("/runs/latest")
def get_latest_run():
    run = run_repo.get_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No runs found")
    task = task_repo.get_task(run.task_id)
    exports = exp_repo.list_exports_for_run(run.id)
    summary_md = ""
    for exp in exports:
        if exp.export_format == "md" and exp.file_path:
            try:
                p = Path(exp.file_path)
                if p.exists():
                    summary_md = p.read_text(encoding="utf-8")
            except Exception:
                pass
            if summary_md:
                break
    return {
        "id": run.id,
        "task_id": run.task_id,
        "query": task.request_text if task else "",
        "agent_mode": getattr(task, "agent_mode", "auto") if task else "auto",
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "counters": run.counters.model_dump(),
        "warnings": run.warnings,
        "summary": summary_md,
        "exports": [e.model_dump() for e in exports],
        "error_code": run.error_code,
        "error_message": run.error_message
    }

@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = run_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    exports = exp_repo.list_exports_for_run(run.id)
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "counters": run.counters.model_dump(),
        "warnings": run.warnings,
        "exports": [e.model_dump() for e in exports],
        "error_code": run.error_code,
        "error_message": run.error_message
    }

@app.get("/runs/{run_id}/records")
def get_run_records(run_id: str, status: Optional[str] = None):
    v_status = None
    if status:
        try:
            v_status = VerificationStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid verification status: '{status}'")
    records = rec_repo.list_records_for_run(run_id, status=v_status)
    return [
        {
            "id": r.id,
            "record_type": r.record_type,
            "fields": r.fields,
            "canonical_url": r.canonical_url,
            "verification_status": r.verification_status.value,
            "confidence": r.confidence,
            "evidence": [e.model_dump() for e in r.evidence],
            "warnings": r.warnings,
        }
        for r in records
    ]

class UpdateJobStatusRequest(BaseModel):
    applied_status: Optional[str] = Field(None, pattern="^(not_applied|applied|interviewing|offered|rejected)$")
    applied_at: Optional[str] = None
    interview_status: Optional[str] = Field(None, pattern="^(no_call|screening|technical|final_round|offered|rejected)$")
    notes: Optional[str] = None

class BulkDeleteJobsRequest(BaseModel):
    record_ids: List[str] = Field(..., min_length=1)

@app.get("/api/jobs")
def list_jobs(run_id: Optional[str] = None, limit: int = Query(200, ge=1, le=500)):
    """List tracked jobs with their application status, interview status, and scraped metadata."""
    return job_repo.list_jobs(run_id=run_id, limit=limit)

@app.get("/api/jobs/saved")
def get_saved_jobs_endpoint():
    """Retrieve all user saved jobs."""
    from datahunt.db.user_repositories import UserPreferencesRepository
    return {"status": "success", "saved_jobs": UserPreferencesRepository().get_saved_jobs()}

@app.get("/api/jobs/applications")
def get_applications_endpoint():
    """Retrieve all tracked job applications."""
    from datahunt.db.user_repositories import ApplicationTrackingRepository
    return {"status": "success", "applications": ApplicationTrackingRepository().get_applications()}

@app.get("/api/jobs/{record_id}")
def get_job_detail(record_id: str):
    """Retrieve full job record including complete JD, timestamps, and evidence."""
    job = job_repo.get_job_detail(record_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job record not found")
    return job

@app.patch("/api/jobs/{record_id}")
def update_job_status(record_id: str, req: UpdateJobStatusRequest):
    """Update applied status, interview status, applied timestamp, or notes."""
    updated = job_repo.update_status(
        record_id=record_id,
        applied_status=req.applied_status,
        applied_at=req.applied_at,
        interview_status=req.interview_status,
        notes=req.notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Job record not found")
    return updated

@app.delete("/api/jobs/{record_id}")
def delete_job(record_id: str):
    """Delete a single job record and its application tracking status."""
    deleted = job_repo.delete_job(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job record not found")
    return {"status": "deleted", "id": record_id}

@app.post("/api/jobs/bulk-delete")
def bulk_delete_jobs(req: BulkDeleteJobsRequest):
    """Delete multiple job records in bulk."""
    count = job_repo.delete_jobs(req.record_ids)
    return {"status": "deleted", "count": count, "record_ids": req.record_ids}

@app.get("/exports/{export_id}/download")
def download_export(export_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM exports WHERE id = ?;", (export_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Export not found")

    file_path = Path(row["storage_key"]).resolve()
    # Path traversal guard: ensure file is within EXPORT_DIR
    export_dir = settings.EXPORT_DIR.resolve()
    if not str(file_path).startswith(str(export_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        file_path.relative_to(export_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: invalid storage path")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Export file missing on storage")

    fmt = row["format"].lower()
    media_type = (
        "application/json" if fmt == "json" else
        "text/csv" if fmt == "csv" else
        "text/markdown; charset=utf-8" if fmt == "md" else
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if fmt == "docx" else
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=row["file_name"],
        headers={"Content-Disposition": f'attachment; filename="{row["file_name"]}"'}
    )

@app.get("/runs/{run_id}/export")
def export_run_on_demand(run_id: str, format: str = Query("md", pattern="^(json|csv|xlsx|md|docx)$")):
    """Get or generate an export file in any format (.md, .docx, .json, .csv) for a run on-the-fly."""
    run = run_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Check if an export with this format already exists
    exports = exp_repo.list_exports_for_run(run_id)
    target_format = format.lower()
    for exp in exports:
        if exp.format.lower() == target_format:
            file_path = Path(exp.storage_key)
            if file_path.exists():
                return download_export(exp.id)

    # Generate on-the-fly if not already generated
    from datetime import datetime, timezone
    from datahunt.tools.export import ExportTool
    records = rec_repo.list_records_for_run(run_id)
    task = task_repo.get_task(run.task_id)

    # Extract source documents for summary context if needed
    conn = get_connection()
    doc_rows = conn.execute("SELECT extracted_text, requested_url FROM source_documents WHERE run_id = ? LIMIT 8;", (run_id,)).fetchall()
    conn.close()

    source_excerpts = []
    for d in doc_rows:
        txt = (d["extracted_text"] or "").strip()
        if txt:
            source_excerpts.append(f"Source URL: {d['requested_url']}\n{txt[:2000]}")
    combined_texts = "\n\n---\n\n".join(source_excerpts)

    meta = {
        "task_id": run.task_id,
        "request_text": task.request_text if task else "Research Run",
        "topic": getattr(task.normalized_spec, "topic", "") if task else "",
        "pages_fetched": run.counters.pages_fetched,
        "records_verified": len(records),
        "summary": combined_texts[:3000] if combined_texts else f"Executive Research Dossier for {task.request_text if task else run_id}",
    }

    tool = ExportTool()
    res = tool.execute(run_id=run.id, records=records, format=target_format, run_metadata=meta)
    if not res.success:
        raise HTTPException(status_code=500, detail=f"Export generation failed: {res.error_message}")

    exp_data = res.data
    exp_record = ExportRecord(
        id=exp_data['export_id'],
        run_id=run.id,
        format=exp_data['format'],
        file_name=exp_data['file_name'],
        storage_key=exp_data['file_path'],
        sha256=exp_data['sha256'],
        row_count=exp_data['row_count'],
        include_evidence=1,
        expires_at=None,
        created_at=datetime.now(timezone.utc).isoformat()
    )
    exp_repo.insert_export(exp_record)
    return download_export(exp_data['export_id'])

stream_clients: Set[WebSocket] = set()

def broadcast_stream_event(event_type: str, event_data: Dict[str, Any], loop: asyncio.AbstractEventLoop):
    msg = {"event": event_type, "data": event_data}
    dead = []
    for ws in list(stream_clients):
        try:
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(ws.send_json(msg), loop)
                try:
                    future.result(timeout=2.0)
                except Exception:
                    dead.append(ws)
        except Exception:
            dead.append(ws)
    for ws in dead:
        stream_clients.discard(ws)

@app.websocket("/ws/agent-stream")
async def websocket_agent_stream(websocket: WebSocket):
    await websocket.accept()
    stream_clients.add(websocket)
    loop = asyncio.get_running_loop()

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except Exception:
                await websocket.send_json({"event": "error", "data": {"message": "Invalid JSON payload"}})
                continue

            action = payload.get("action")
            if action == "ping":
                await websocket.send_json({"event": "pong", "data": {}})
                continue

            if action == "start_run":
                query_text = payload.get("query", "").strip()
                if not query_text:
                    await websocket.send_json({"event": "error", "data": {"message": "Query cannot be empty"}})
                    continue

                max_records = int(payload.get("max_records", 150))
                freshness_days = payload.get("freshness_days")
                if freshness_days is not None:
                    freshness_days = int(freshness_days)
                output_format = payload.get("output_format", "json")
                model_choice = payload.get("model", "auto")
                agent_mode = payload.get("agent_mode", "auto")

                orch = ResearchOrchestrator(model=model_choice)
                try:
                    task, run = orch.create_task_and_run(
                        request_text=query_text,
                        max_records=max_records,
                        freshness_days=freshness_days,
                        output_format=output_format,
                        model=model_choice,
                        agent_mode=agent_mode,
                    )
                except Exception as e:
                    await websocket.send_json({"event": "error", "data": {"message": f"Task creation failed: {e}"}})
                    continue

                def stream_event(event_type: str, event_data: Dict[str, Any]):
                    broadcast_stream_event(event_type, event_data, loop)

                async def _background_execute(run_id: str):
                    try:
                        await asyncio.to_thread(orch.execute_run, run_id, stream_event)
                    except Exception as e:
                        logger.error(f"Background run {run_id} execution error: {e}", exc_info=True)
                        broadcast_stream_event("run.failed", {"error": str(e), "run_id": run_id}, loop)

                # Detach run execution so page refreshes never abort or kill the background process
                asyncio.create_task(_background_execute(run.id))

    except WebSocketDisconnect:
        pass
    finally:
        stream_clients.discard(websocket)

@app.get("/telemetry/runs")
async def get_langsmith_runs(limit: int = 20):
    """Retrieve the latest telemetry runs from LangSmith using modern SmithDB client.runs.query()."""
    runs = await query_project_runs(limit=limit)
    return {
        "status": "success",
        "project": settings.LANGSMITH_PROJECT,
        "count": len(runs),
        "runs": runs
    }

# ─────────────────────────────────────────────
# 70-Step Lifecycle: Job Actions & Sub-Agents
# ─────────────────────────────────────────────

class JobActionRequest(BaseModel):
    notes: Optional[str] = ""
    reason: Optional[str] = "other"
    cover_letter: Optional[str] = ""

class CompanyResearchRequest(BaseModel):
    company_name: str
    force_refresh: bool = False

class InterviewPrepRequest(BaseModel):
    role_title: str
    company_name: str
    skills: List[str] = Field(default_factory=list)
    job_description: Optional[str] = ""

class InterviewEvalRequest(BaseModel):
    role_title: str
    question: str
    answer: str

@app.post("/api/jobs/{record_id}/save")
def save_job_endpoint(record_id: str, req: JobActionRequest):
    """Step 44: Save job to user's list and update learning engine."""
    from datahunt.db.user_repositories import UserPreferencesRepository
    from datahunt.agents.learning_engine import LearningEngine
    
    user_repo = UserPreferencesRepository()
    user_repo.save_job(record_id, notes=req.notes or "")
    
    # Positive reinforcement signal
    rec = rec_repo.get_record(record_id)
    if rec and rec.fields:
        LearningEngine(user_repo).record_save(rec.fields)
        
    return {"status": "success", "message": f"Job {record_id} saved.", "record_id": record_id}

@app.post("/api/jobs/{record_id}/ignore")
def ignore_job_endpoint(record_id: str, req: JobActionRequest):
    """Step 45: Ignore job from recommendations and learn negative preference."""
    from datahunt.db.user_repositories import UserPreferencesRepository
    from datahunt.agents.learning_engine import LearningEngine

    user_repo = UserPreferencesRepository()
    user_repo.ignore_job(record_id, reason=req.reason or "other")

    rec = rec_repo.get_record(record_id)
    if rec and rec.fields:
        LearningEngine(user_repo).record_ignore(rec.fields, reason=req.reason or "other")

    return {"status": "success", "message": f"Job {record_id} ignored.", "record_id": record_id}

@app.post("/api/jobs/{record_id}/apply")
def apply_job_endpoint(record_id: str, req: JobActionRequest):
    """Steps 48-56: Mark job application stage and store application notes."""
    from datahunt.db.user_repositories import ApplicationTrackingRepository
    from datahunt.agents.learning_engine import LearningEngine

    app_repo = ApplicationTrackingRepository()
    app_repo.update_stage(record_id, status="applied", notes=req.notes or "", cover_letter=req.cover_letter or "")

    rec = rec_repo.get_record(record_id)
    if rec and rec.fields:
        LearningEngine().record_application_outcome(rec.fields, outcome="applied")

    return {"status": "success", "message": f"Application recorded for {record_id}.", "record_id": record_id}

@app.post("/api/company/research")
def research_company_endpoint(req: CompanyResearchRequest):
    """Steps 40, 47: Run Company Research Agent on target organization."""
    from datahunt.agents.company_research import CompanyResearchAgent
    from datahunt.llm import GeminiClient
    agent = CompanyResearchAgent(gemini_client=GeminiClient())
    profile = agent.research(company_name=req.company_name, force_refresh=req.force_refresh)
    return {"status": "success", "company_profile": profile.model_dump()}

@app.post("/api/interview/prep")
def interview_prep_endpoint(req: InterviewPrepRequest):
    """Steps 57-60: Run Interview Preparation Agent to generate role Q&A pack."""
    from datahunt.agents.interview_prep import InterviewPrepAgent
    from datahunt.llm import GeminiClient
    agent = InterviewPrepAgent(gemini_client=GeminiClient())
    prep_pack = agent.prepare(
        role_title=req.role_title,
        company_name=req.company_name,
        skills=req.skills,
        job_description=req.job_description or ""
    )
    return {"status": "success", "interview_prep": prep_pack.model_dump()}

@app.post("/api/interview/evaluate")
def interview_eval_endpoint(req: InterviewEvalRequest):
    """Step 61: Evaluate mock interview practice response."""
    from datahunt.agents.interview_prep import InterviewPrepAgent
    from datahunt.llm import GeminiClient
    agent = InterviewPrepAgent(gemini_client=GeminiClient())
    feedback = agent.evaluate_answer(role_title=req.role_title, question=req.question, answer=req.answer)
    return {"status": "success", "evaluation": feedback}

@app.get("/api/monitor/events")
def monitor_events_endpoint():
    """Steps 67-69: Get detected job change events and status alerts."""
    from datahunt.agents.job_monitor import JobMonitor
    return {"status": "success", "events": JobMonitor().get_unnotified_events()}

@app.post("/api/monitor/check")
def monitor_check_endpoint():
    """Step 66: Trigger active health re-check across all saved jobs."""
    from datahunt.agents.job_monitor import JobMonitor
    events = JobMonitor().check_saved_jobs()
    return {"status": "success", "detected_events_count": len(events), "events": events}

# Mount frontend static directory if exists
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

