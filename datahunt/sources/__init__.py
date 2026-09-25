"""
datahunt.sources — Source registry, adapters, and multi-source discovery subsystem.
"""
from datahunt.sources.models import JobSource, JobSourceType, SourceStatus, SourceRunResult, RawJob
from datahunt.sources.registry import JobSourceRegistry
from datahunt.sources.adapters import (
    BaseSourceAdapter, ATSAdapter, RegionalBoardAdapter, MajorBoardAdapter,
    TechBoardAdapter, RemoteBoardAdapter, CompanyCareerAdapter, SearchIndexAdapter,
    get_adapter_for_source
)

__all__ = [
    "JobSource",
    "JobSourceType",
    "SourceStatus",
    "SourceRunResult",
    "RawJob",
    "JobSourceRegistry",
    "BaseSourceAdapter",
    "ATSAdapter",
    "RegionalBoardAdapter",
    "MajorBoardAdapter",
    "TechBoardAdapter",
    "RemoteBoardAdapter",
    "CompanyCareerAdapter",
    "SearchIndexAdapter",
    "get_adapter_for_source",
]
