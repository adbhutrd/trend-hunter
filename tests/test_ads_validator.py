"""Unit tests for AdsValidator (the ≥30-day-running filter)."""
from __future__ import annotations

import json

from trend_hunter.adapters.ads_validator import (
    VALIDATED_DAYS_THRESHOLD,
    AdsValidator,
)


def test_validator_empty_when_no_file(tmp_path):
    v = AdsValidator(tmp_path / "no.json")
    assert v.all() == []
    assert v.validated() == []


def test_validator_filters_to_validated_only(tmp_path):
    p = tmp_path / "ads.json"
    p.write_text(
        json.dumps([
            {"creative_url": "u1", "advertiser": "A", "niche": "lamp",
             "days_running": 60, "source": "manual"},
            {"creative_url": "u2", "advertiser": "B", "niche": "lamp",
             "days_running": 14, "source": "manual"},
            {"creative_url": "u3", "advertiser": "C", "niche": "desk",
             "days_running": 31, "source": "manual"},
        ])
    )
    v = AdsValidator(p)
    assert len(v.all()) == 3
    assert len(v.validated()) == 2
    assert {a.advertiser for a in v.validated()} == {"A", "C"}


def test_validator_matches_niche(tmp_path):
    p = tmp_path / "ads.json"
    p.write_text(
        json.dumps([
            {"creative_url": "u1", "advertiser": "A", "niche": "desk lamp",
             "days_running": 45, "source": "manual"},
            {"creative_url": "u2", "advertiser": "B", "niche": "yoga mat",
             "days_running": 60, "source": "manual"},
        ])
    )
    v = AdsValidator(p)
    assert {a.advertiser for a in v.matches_niche("lamp")} == {"A"}
    assert {a.advertiser for a in v.matches_niche("yoga")} == {"B"}


def test_validator_skips_malformed_entries(tmp_path):
    p = tmp_path / "ads.json"
    p.write_text(
        json.dumps([
            {"creative_url": "u1", "advertiser": "A", "niche": "lamp",
             "days_running": 60, "source": "manual"},
            {"creative_url": "u2"},                  # missing keys
            {"creative_url": "u3", "advertiser": "C", "niche": "lamp",
             "days_running": "not-a-number", "source": "manual"},
            {"creative_url": "u4", "advertiser": "D", "niche": "lamp",
             "days_running": 40, "source": "manual"},
        ])
    )
    v = AdsValidator(p)
    # Skips 2 malformed, keeps 2 valid
    assert len(v.all()) == 2
    assert len(v.validated()) == 2


def test_threshold_constant_is_30():
    assert VALIDATED_DAYS_THRESHOLD == 30
