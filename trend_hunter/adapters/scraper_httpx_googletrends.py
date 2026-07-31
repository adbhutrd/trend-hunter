"""Google Trends adapter — search interest over time via pytrends.

Uses the pytrends library (unofficial Google Trends API client).
No API key required — pytrends works by emulating browser requests
to Google Trends' public endpoints.

Each search term becomes a product signal with interest-over-time
as the "price" metric. Rising interest = rising trend.

Install: pip install pytrends
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

DEFAULT_TREND_TERMS = [
    "ai tools",
    "chatgpt",
    "sustainable fashion",
    "smart home",
    "nootropics",
    "electric vehicle",
    "crypto wallet",
    "home workout",
    "meal prep",
    "plant based",
]


def _retry_with_backoff(fn, max_retries=3, base_delay=5):
    """Retry a callable with exponential backoff, handling 429 errors."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            err_msg = str(e).lower()
            if "429" in err_msg or "too many" in err_msg:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.info(f"googletrends: 429 backoff {delay}s (attempt {attempt + 1})")
                    time.sleep(delay)
                    continue
            raise


class GoogleTrendsScraper:
    name = "googletrends"

    def __init__(
        self,
        keywords: list[str] | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        self.keywords = keywords or DEFAULT_TREND_TERMS
        self.timeout_s = timeout_s

    async def fetch(self) -> list[RawSignal]:
        """Fetch Google Trends data for each keyword.

        Runs synchronously (pytrends doesn't support async) via
        asyncio.to_thread to avoid blocking the event loop.
        """
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[RawSignal]:
        signals: list[RawSignal] = []
        now = datetime.now(UTC)

        try:
            from pytrends.request import TrendReq
        except ImportError:
            logger.warning("googletrends: pytrends not installed (pip install pytrends)")
            return []

        def _init_pytrends():
            return TrendReq(timeout=(10, int(self.timeout_s)))

        try:
            pytrends = _retry_with_backoff(_init_pytrends)
        except Exception as e:
            logger.warning(f"googletrends: failed to init: {e}")
            return []

        # Process keywords in batches of 5 (Google Trends limit),
        # with delays between batches to avoid 429 rate limiting.
        batch_size = 5
        for i in range(0, len(self.keywords), batch_size):
            batch = self.keywords[i : i + batch_size]

            def _build_and_fetch(b=batch):
                pytrends.build_payload(b, timeframe="today 3-m", geo="")
                return pytrends.interest_over_time()

            try:
                interest_df = _retry_with_backoff(_build_and_fetch)
            except Exception as e:
                logger.warning(f"googletrends: batch {batch} failed after retries: {e}")
                continue

            # Polite delay between batches to avoid rate limiting.
            if i + batch_size < len(self.keywords):
                time.sleep(3)

            if interest_df.empty:
                continue

            for kw in batch:
                if kw not in interest_df.columns:
                    continue
                series = interest_df[kw].dropna()
                if series.empty:
                    continue

                avg_interest = float(series.mean())
                current_interest = float(series.iloc[-1])
                trend_slope = (current_interest - float(series.iloc[0])) / max(1, len(series))

                signals.append(
                    RawSignal(
                        source=self.name,
                        external_id=f"gtrends-{kw.replace(' ', '-')}",
                        captured_at=now,
                        payload={
                            "title": kw.title(),
                            "keyword": kw,
                            "price": current_interest,
                            "avg_interest": avg_interest,
                            "trend_slope": trend_slope,
                            "data_points": len(series),
                            "currency": "INTEREST",
                        },
                    )
                )

        logger.info(f"googletrends: ingested {len(signals)} trend terms")
        return signals

    async def health(self) -> Health:
        try:
            from pytrends.request import TrendReq  # noqa: F401
        except ImportError:
            return Health(
                source=self.name,
                state=HealthState.FAILING,
                last_run=None,
                last_ok=None,
                rows_in=0,
                error_rate=1.0,
                detail="pytrends not installed",
            )

        return Health(
            source=self.name,
            state=HealthState.OK,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"{len(self.keywords)} keywords configured",
        )
