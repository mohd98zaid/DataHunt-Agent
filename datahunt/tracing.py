import functools
from typing import Any, Dict, List, Optional
from datahunt.logger import logger
from datahunt.config import settings

try:
    from langsmith import traceable as ls_traceable
    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False


def traceable(*args, **kwargs):
    """
    Decorator for tracing functions with LangSmith.
    Gracefully falls back to a no-op passthrough if LangSmith is not installed
    or unconfigured.
    """
    if LANGSMITH_AVAILABLE and settings.is_langsmith_configured:
        return ls_traceable(*args, **kwargs)

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*f_args, **f_kwargs):
            return func(*f_args, **f_kwargs)
        return wrapper

    if len(args) == 1 and callable(args[0]) and not kwargs:
        return decorator(args[0])
    return decorator


async def query_project_runs(project_name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Query project traces using LangSmith's SmithDB-backed client.traces.query() API.
    Replaces deprecated client.list_runs() in compliance with LangSmith SmithDB migration
    (LangSmith Python SDK >= 0.10.15).
    """
    target_project = project_name or settings.LANGSMITH_PROJECT
    if not (LANGSMITH_AVAILABLE and settings.is_langsmith_configured):
        return []

    try:
        from langsmith import Client
        client = Client()
        project = await client.aread_project(project_name=target_project)
        proj_id = str(project.id)

        selects = [
            "ID", "NAME", "RUN_TYPE", "STATUS", "LATENCY_SECONDS",
            "START_TIME", "TOTAL_TOKENS", "TOTAL_COST", "ERROR"
        ]

        runs: List[Dict[str, Any]] = []
        async for t in client.traces.query(
            project_id=proj_id,
            selects=selects,
            page_size=min(limit, 100)
        ):
            root = t.root_run
            agg = t.trace_aggregates
            runs.append({
                "id": str(root.id) if root and root.id else "",
                "name": root.name if root else "Unknown",
                "run_type": root.run_type if root else "chain",
                "status": root.status if root else "unknown",
                "latency_seconds": root.latency_seconds if root else None,
                "start_time": root.start_time.isoformat() if root and root.start_time else None,
                "total_tokens": agg.total_tokens if agg and agg.total_tokens is not None else (root.total_tokens if root else None),
                "total_cost": agg.total_cost if agg and agg.total_cost is not None else (root.total_cost if root else None),
                "error": root.error if root else None
            })
            if len(runs) >= limit:
                break
        return runs
    except Exception as e:
        logger.warning(f"Failed to query LangSmith traces via SmithDB: {e}")
        # Resilient fallback to client.runs.query
        try:
            from langsmith import Client
            client = Client()
            project = await client.aread_project(project_name=target_project)
            runs = []
            async for r in client.runs.query(
                project_ids=[str(project.id)],
                selects=["ID", "NAME", "RUN_TYPE", "STATUS", "LATENCY_SECONDS", "START_TIME"],
                page_size=min(limit, 100)
            ):
                runs.append({
                    "id": str(r.id),
                    "name": r.name,
                    "run_type": r.run_type,
                    "status": r.status,
                    "latency_seconds": r.latency_seconds,
                    "start_time": r.start_time.isoformat() if r.start_time else None,
                    "total_tokens": getattr(r, "total_tokens", None),
                    "total_cost": getattr(r, "total_cost", None),
                    "error": getattr(r, "error", None)
                })
                if len(runs) >= limit:
                    break
            return runs
        except Exception as fallback_err:
            logger.warning(f"Fallback LangSmith runs query failed: {fallback_err}")
            return []


async def retrieve_run_details(run_id: str, project_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieve single run details using modern SmithDB-backed client.runs.retrieve().
    Replaces deprecated client.read_run().
    """
    target_project = project_name or settings.LANGSMITH_PROJECT
    if not (LANGSMITH_AVAILABLE and settings.is_langsmith_configured):
        return None

    try:
        from langsmith import Client
        client = Client()
        project = await client.aread_project(project_name=target_project)
        selects = [
            "ID", "NAME", "RUN_TYPE", "STATUS", "LATENCY_SECONDS",
            "START_TIME", "END_TIME", "INPUTS", "OUTPUTS", "ERROR",
            "TOTAL_TOKENS", "TOTAL_COST"
        ]
        run = await client.runs.retrieve(
            project_id=str(project.id),
            run_id=run_id,
            selects=selects
        )
        return {
            "id": str(run.id) if run.id else run_id,
            "name": run.name,
            "run_type": run.run_type,
            "status": run.status,
            "latency_seconds": run.latency_seconds,
            "start_time": run.start_time.isoformat() if run.start_time else None,
            "end_time": run.end_time.isoformat() if run.end_time else None,
            "inputs": run.inputs,
            "outputs": run.outputs,
            "error": run.error,
            "total_tokens": getattr(run, "total_tokens", None),
            "total_cost": getattr(run, "total_cost", None),
        }
    except Exception as e:
        logger.warning(f"Failed to retrieve run details for {run_id} via SmithDB: {e}")
        return None


async def query_project_threads(project_name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Query project threads using modern SmithDB-backed client.threads.query().
    Replaces deprecated client.list_threads().
    """
    target_project = project_name or settings.LANGSMITH_PROJECT
    if not (LANGSMITH_AVAILABLE and settings.is_langsmith_configured):
        return []

    try:
        from langsmith import Client
        client = Client()
        project = await client.aread_project(project_name=target_project)
        threads = []
        async for th in client.threads.query(
            project_id=str(project.id),
            page_size=min(limit, 100)
        ):
            threads.append({
                "id": str(getattr(th, "id", "")),
                "thread_id": getattr(th, "thread_id", None),
                "status": getattr(th, "status", None),
            })
            if len(threads) >= limit:
                break
        return threads
    except Exception as e:
        logger.warning(f"Failed to query LangSmith threads via SmithDB: {e}")
        return []
