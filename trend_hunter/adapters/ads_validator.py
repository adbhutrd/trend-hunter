"""Meta Ad Library + TikTok Spark-Ads validator.

Gold rule from the original brief:
  An ad that has been running for >30 days is overwhelmingly likely to be
  profitable — advertisers stop bad creatives inside a week. So we treat
  "days_running >= 30" as a validated-profit signal.

Phase 1 doesn't scrape Meta/TikTok (rate-limits + ToS risk). Instead we
read a local JSON of known-validated ads you curate yourself, and we extend
the framework for Phase 2 to swap in a real scraper behind the same API.

The local catalog lives at `data/ads.json`:
[
  {
    "creative_url": "https://example.com/ad/1.jpg",
    "advertiser": "Lumify",
    "niche": "desk lamp",
    "days_running": 45,
    "source": "manual",
    "captured_at": "2026-06-01T12:00:00Z"
  }
]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VALIDATED_DAYS_THRESHOLD = 30  # ≥ 30 days running ⇒ validated profit
_REQUIRED_KEYS = ("creative_url", "advertiser", "niche", "days_running")


@dataclass(frozen=True, slots=True)
class AdEvidence:
    creative_url: str
    advertiser: str
    niche: str
    days_running: int
    source: str
    captured_at: datetime | None


class AdsValidator:
    def __init__(self, json_path: Path = Path("./data/ads.json")) -> None:
        self.json_path = json_path
        self._items: list[AdEvidence] = []
        self._load()

    def _load(self) -> None:
        self._items = []
        if not self.json_path.exists():
            return
        try:
            raw = self.json_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, list):
            return
        for row in data:
            if not isinstance(row, dict):
                continue
            # Strict shape: all 4 required keys must be present.
            if not all(k in row for k in _REQUIRED_KEYS):
                continue
            try:
                self._items.append(
                    AdEvidence(
                        creative_url=str(row["creative_url"]).strip(),
                        advertiser=str(row["advertiser"]).strip(),
                        niche=str(row["niche"]).strip(),
                        days_running=int(row["days_running"]),
                        source=str(row.get("source", "manual")).strip(),
                        captured_at=_parse_ts(row.get("captured_at")),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue

    def reload(self) -> None:
        self._load()

    def all(self) -> list[AdEvidence]:
        return list(self._items)

    def validated(self) -> list[AdEvidence]:
        """Ads running ≥ 30 days — our 'validated profit' filter."""
        return [a for a in self._items if a.days_running >= VALIDATED_DAYS_THRESHOLD]

    def matches_niche(self, niche: str) -> list[AdEvidence]:
        q = (niche or "").lower()
        return [a for a in self.validated() if q in a.niche.lower()]


def _parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        # tolerate trailing Z
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
