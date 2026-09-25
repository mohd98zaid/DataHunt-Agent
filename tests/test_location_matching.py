"""Tests for location matching with MATCH/MISMATCH/UNKNOWN outcomes."""
import pytest
from datahunt.agent.policies import match_location, MatchStatus


class TestMatchLocation:
    # MATCH cases
    def test_riyadh_matches_saudi(self):
        status, reason = match_location("Saudi Arabia", "Riyadh, Saudi Arabia")
        assert status == MatchStatus.MATCH

    def test_dubai_matches_uae(self):
        status, reason = match_location("UAE", "Dubai, UAE")
        assert status == MatchStatus.MATCH

    def test_ksa_alias_matches_saudi(self):
        status, reason = match_location("Saudi Arabia", "Riyadh, KSA")
        assert status == MatchStatus.MATCH

    def test_abu_dhabi_matches_uae(self):
        status, reason = match_location("UAE", "Abu Dhabi")
        assert status == MatchStatus.MATCH

    def test_no_location_constraint_always_matches(self):
        status, reason = match_location(None, "Moscow, Russia")
        assert status == MatchStatus.MATCH

    def test_globally_remote_matches_any_location(self):
        status, reason = match_location("Saudi Arabia", "worldwide", remote_ok=True)
        assert status in (MatchStatus.MATCH, MatchStatus.UNKNOWN)

    # UNKNOWN cases
    def test_empty_job_location_is_unknown(self):
        status, reason = match_location("UAE", "")
        assert status == MatchStatus.UNKNOWN

    def test_not_specified_is_unknown(self):
        status, reason = match_location("UAE", "not specified")
        assert status == MatchStatus.UNKNOWN

    def test_unrecognized_city_is_unknown(self):
        status, reason = match_location("UAE", "Timbuktu Province")
        assert status == MatchStatus.UNKNOWN

    def test_remote_without_region_is_unknown(self):
        status, reason = match_location("Saudi Arabia", "remote")
        assert status == MatchStatus.UNKNOWN

    # MISMATCH cases
    def test_us_city_mismatches_saudi(self):
        status, reason = match_location("Saudi Arabia", "San Francisco, CA, USA")
        assert status == MatchStatus.MISMATCH

    def test_uk_mismatches_uae(self):
        status, reason = match_location("UAE", "London, United Kingdom")
        assert status == MatchStatus.MISMATCH

    def test_india_mismatches_uae(self):
        status, reason = match_location("UAE", "Bangalore, India")
        assert status == MatchStatus.MISMATCH

    def test_us_remote_mismatches_uae(self):
        # "Denver, CO (Remote)" — US-based remote should mismatch UAE request
        status, reason = match_location("UAE", "Denver, CO (Remote)")
        assert status == MatchStatus.MISMATCH
