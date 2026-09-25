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

async def query_project_runs(project_name: Optional[str] = None, limit: int = 20):
    """
    Query project traces using LangSmith's SmithDB-backed client.runs.query() API.
    Replaces deprecated client.list_runs() in compliance with LangSmith SmithDB migration.
    """
    target_project = project_name or settings.LANGSMITH_PROJECT
    if not (LANGSMITH_AVAILABLE and settings.is_langsmith_configured):
        return []

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
                "start_time": r.start_time.isoformat() if r.start_time else None
            })
            if len(runs) >= limit:
                break
        return runs
    except Exception as e:
        logger.warning(f"Failed to query LangSmith runs via SmithDB: {e}")
        return []
