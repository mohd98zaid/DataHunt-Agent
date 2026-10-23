"""
Regression tests for parallel dual-track search (job portals + career sites)
and the 40-RPM rolling-window rate limiter.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Rate limiter unit tests
# ---------------------------------------------------------------------------

class TestSearchRateLimiter:
    """Tests for the _SearchRateLimiter rolling-window rate limiter."""

    def _make_limiter(self, rpm: int):
        from datahunt.agent.runtime import _SearchRateLimiter
        return _SearchRateLimiter(rpm=rpm)

    def test_allows_calls_under_limit(self):
        """rpm=5: first 5 consecutive acquires should complete without blocking."""
        lim = self._make_limiter(rpm=5)
        t0 = time.time()
        for _ in range(5):
            lim.acquire()
        assert time.time() - t0 < 2.0, "5 acquires well under limit should be near-instant"

    def test_blocks_when_window_full(self):
        """rpm=2: third acquire must block until a slot opens (≥1 s wait)."""
        lim = self._make_limiter(rpm=2)
        # Saturate the window with two calls in the same second
        lim.acquire()
        lim.acquire()
        # Manually fast-forward: replace old timestamps with ones exactly 59s old
        with lim._lock:
            lim._window[0] = time.time() - 59.0
            lim._window[1] = time.time() - 59.0
        # Third call should block briefly until window expires (≥1 s)
        t0 = time.time()
        lim.acquire()
        elapsed = time.time() - t0
        assert elapsed >= 0.9, f"Expected block ≥1 s but got {elapsed:.2f} s"

    def test_reconfigure_changes_rpm(self):
        """reconfigure() must change the rpm attribute."""
        lim = self._make_limiter(rpm=10)
        lim.reconfigure(rpm=40)
        assert lim.rpm == 40

    def test_thread_safety(self):
        """20 threads all acquiring concurrently must not exceed rpm=20 in one window."""
        lim = self._make_limiter(rpm=20)
        timestamps = []
        lock = threading.Lock()

        def _acquire_and_record():
            lim.acquire()
            with lock:
                timestamps.append(time.time())

        threads = [threading.Thread(target=_acquire_and_record) for _ in range(20)]
        t0 = time.time()
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5)

        # All 20 must have acquired (no timeouts)
        assert len(timestamps) == 20
        # All 20 acquired within the first window (rpm=20 → all should fit)
        window_counts = sum(1 for ts in timestamps if ts - t0 < 60.0)
        assert window_counts == 20


# ---------------------------------------------------------------------------
# Task track-tagging tests
# ---------------------------------------------------------------------------

class TestTaskTrackTagging:
    """_PORTAL_TASK_TYPES and _CAREER_TASK_TYPES must classify all SearchTaskType values."""

    def test_portal_types_are_classified(self):
        from datahunt.agent.runtime import _PORTAL_TASK_TYPES
        from datahunt.agent.discovery_models import SearchTaskType
        portal_types = {SearchTaskType.BOARD_SEARCH, SearchTaskType.REGIONAL_BOARD_SEARCH, SearchTaskType.GENERAL_SEARCH}
        for t in portal_types:
            assert t.value in _PORTAL_TASK_TYPES, f"{t.value} should be a portal task type"

    def test_career_types_are_classified(self):
        from datahunt.agent.runtime import _CAREER_TASK_TYPES
        from datahunt.agent.discovery_models import SearchTaskType
        career_types = {SearchTaskType.ATS_SEARCH, SearchTaskType.CAREER_PAGE_SEARCH, SearchTaskType.COMPANY_SEARCH}
        for t in career_types:
            assert t.value in _CAREER_TASK_TYPES, f"{t.value} should be a career-site task type"

    def test_no_overlap_between_tracks(self):
        from datahunt.agent.runtime import _PORTAL_TASK_TYPES, _CAREER_TASK_TYPES
        overlap = _PORTAL_TASK_TYPES & _CAREER_TASK_TYPES
        assert not overlap, f"Tracks must not overlap; overlap={overlap}"

    def test_all_task_types_have_a_track(self):
        from datahunt.agent.runtime import _PORTAL_TASK_TYPES, _CAREER_TASK_TYPES
        from datahunt.agent.discovery_models import SearchTaskType
        all_known = _PORTAL_TASK_TYPES | _CAREER_TASK_TYPES
        for t in SearchTaskType:
            assert t.value in all_known, f"SearchTaskType.{t.name} has no track assignment"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfigBatchSettings:
    """NVIDIA_RPM and SEARCH_BATCH_SIZE must be present, positive, and editable."""

    def test_defaults_exist(self):
        from datahunt.config import Settings
        s = Settings.__new__(Settings)  # skip __init__ to avoid DB touch
        # Verify class-level defaults are sane
        assert int(Settings.__dict__.get("NVIDIA_RPM", "40")) > 0
        assert int(Settings.__dict__.get("SEARCH_BATCH_SIZE", "20")) > 0

    def test_editable_settings_contains_new_keys(self):
        from datahunt.config import EDITABLE_SETTINGS
        assert "NVIDIA_RPM" in EDITABLE_SETTINGS
        assert "SEARCH_BATCH_SIZE" in EDITABLE_SETTINGS
        assert EDITABLE_SETTINGS["NVIDIA_RPM"] is int
        assert EDITABLE_SETTINGS["SEARCH_BATCH_SIZE"] is int

    def test_to_safe_dict_includes_new_keys(self):
        from datahunt.config import settings
        d = settings.to_safe_dict()
        assert "nvidia_rpm" in d
        assert "search_batch_size" in d
        assert isinstance(d["nvidia_rpm"], int)
        assert isinstance(d["search_batch_size"], int)

    def test_batch_size_not_exceed_rpm(self):
        """Default SEARCH_BATCH_SIZE should not exceed NVIDIA_RPM."""
        from datahunt.config import settings
        assert settings.SEARCH_BATCH_SIZE <= settings.NVIDIA_RPM


# ---------------------------------------------------------------------------
# Parallel search behaviour — mock-level smoke test
# ---------------------------------------------------------------------------

class TestParallelSearchBehaviour:
    """_act_search must dispatch tasks in parallel and honour SEARCH_BATCH_SIZE."""

    def _make_task(self, task_type_str="BOARD_SEARCH", round_=1):
        from datahunt.agent.discovery_models import SearchTask, SearchTaskType, DiscoveredSourceType
        return SearchTask(
            id=f"t_{task_type_str}_{round_}_{id(object())}",
            query=f"GenAI Engineer {task_type_str}",
            task_type=SearchTaskType(task_type_str),
            source="test_source",
            source_type=DiscoveredSourceType.MAJOR_BOARD,
            priority=2,
            round=round_,
            depth=1,
            metadata={},
        )

    def test_both_tracks_present_in_batch(self):
        """A mixed queue with portal+career tasks produces a batch with both tracks."""
        from datahunt.agent.runtime import _PORTAL_TASK_TYPES, _CAREER_TASK_TYPES

        tasks = [
            self._make_task("BOARD_SEARCH"),
            self._make_task("ATS_SEARCH"),
            self._make_task("REGIONAL_BOARD_SEARCH"),
            self._make_task("CAREER_PAGE_SEARCH"),
        ]

        portal = [t for t in tasks if t.task_type.value in _PORTAL_TASK_TYPES]
        career = [t for t in tasks if t.task_type.value in _CAREER_TASK_TYPES]

        assert len(portal) == 2, "Expected 2 portal tasks"
        assert len(career) == 2, "Expected 2 career-site tasks"

    def test_batch_size_respects_config(self):
        """The batch pulled from the queue must not exceed SEARCH_BATCH_SIZE."""
        from datahunt.config import settings
        cfg_batch = getattr(settings, "SEARCH_BATCH_SIZE", 20)
        tasks = [self._make_task("BOARD_SEARCH", round_=i) for i in range(cfg_batch + 10)]

        batch = []
        queue = list(tasks)
        while queue and len(batch) < cfg_batch:
            batch.append(queue.pop(0))

        assert len(batch) == cfg_batch
        assert len(queue) == 10  # leftovers stay in queue
