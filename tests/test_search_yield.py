"""Tests for search yield tracking and source tier classification."""
import pytest
from datahunt.tools.search import calculate_search_yield, assign_source_tier


class TestCalculateSearchYield:
    def test_perfect_yield(self):
        assert calculate_search_yield(10, 10) == 1.0

    def test_zero_yield(self):
        assert calculate_search_yield(10, 0) == 0.0

    def test_half_yield(self):
        assert calculate_search_yield(20, 10) == 0.5

    def test_zero_total_hits(self):
        assert calculate_search_yield(0, 0) == 0.0

    def test_rounds_to_3_decimal(self):
        result = calculate_search_yield(3, 1)
        assert result == round(1/3, 3)


class TestAssignSourceTier:
    def test_greenhouse_is_tier_1(self):
        assert assign_source_tier("https://boards.greenhouse.io/company/jobs/12345") == 1

    def test_lever_is_tier_1(self):
        assert assign_source_tier("https://jobs.lever.co/startup/position-id") == 1

    def test_ashby_is_tier_1(self):
        assert assign_source_tier("https://jobs.ashbyhq.com/company/role") == 1

    def test_bayt_is_tier_2(self):
        assert assign_source_tier("https://www.bayt.com/en/uae/jobs/ai-engineer-4567890/") == 2

    def test_naukrigulf_is_tier_2(self):
        assert assign_source_tier("https://www.naukrigulf.com/ai-ml-jobs-in-uae") == 2

    def test_linkedin_is_tier_3(self):
        assert assign_source_tier("https://www.linkedin.com/jobs/view/1234567") == 3

    def test_indeed_is_tier_3(self):
        assert assign_source_tier("https://ae.indeed.com/viewjob?jk=abc123") == 3

    def test_unknown_domain_is_tier_4(self):
        assert assign_source_tier("https://somerandomblog.com/jobs/ai-engineer") == 4

    def test_case_insensitive(self):
        assert assign_source_tier("https://BOARDS.GREENHOUSE.IO/company/job/123") == 1
