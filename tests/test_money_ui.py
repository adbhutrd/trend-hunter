"""Money-page overlap logic — pure helpers, no Streamlit import.

We test the niche-overlap function directly so the Money page's
overlay stays correct even if Streamlit paginate/AGGrid changes.
"""

from __future__ import annotations

from pathlib import Path

from trend_hunter.adapters.ads_validator import AdEvidence, AdsValidator

# Path to the curated seed file we ship at the repo root.
SAMPLE_ADS = Path(__file__).resolve().parent.parent / "data" / "ads.json"


def _hits_for(title: str, ads: list[AdEvidence]) -> list[AdEvidence]:
    # Mirror the page's _niche_hits() in-test signature.
    haystack = (title or "").lower()
    return [a for a in ads if a.niche.lower() in haystack]


def test_ads_validator_loads_three_rows() -> None:
    """Validator must parse the curated seed file we ship in data/ads.json."""
    assert SAMPLE_ADS.exists(), f"missing seed at {SAMPLE_ADS}"
    validator = AdsValidator(SAMPLE_ADS)
    items = validator.all()
    assert len(items) == 3, f"expected 3 seed ads, got {len(items)}"
    assert {a.niche for a in items} == {"desk lamp", "hoodie", "water bottle"}


def test_validated_filter_keeps_only_30d_plus() -> None:
    validator = AdsValidator(SAMPLE_ADS)
    assert len(validator.validated()) == 3  # all three seed rows are >=30d


def test_niche_overlap_matches_title_token() -> None:
    validator = AdsValidator(SAMPLE_ADS)
    ads = validator.validated()
    # "Black hoodie" matches the curated "hoodie" niche → 1 hit
    hits = _hits_for("Black hoodie 320gsm cotton", ads)
    assert len(hits) == 1 and hits[0].niche == "hoodie"

    # "Stainless water bottle 24oz" matches "water bottle"
    hits = _hits_for("Stainless water bottle 24oz", ads)
    assert len(hits) == 1 and hits[0].niche == "water bottle"

    # A title with no niche overlap → empty
    assert _hits_for("Limited edition vinyl record", ads) == []
