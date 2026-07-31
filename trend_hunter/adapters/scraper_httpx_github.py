"""GitHub Trending scraper — trending open-source repositories.

Scrapes GitHub's trending page (public HTML, no auth required).
Trending repos signal emerging technologies, tools, and frameworks —
leading indicators for developer-focused products and SaaS ideas.

No API key, no OAuth, no rate limits beyond standard web scraping courtesy.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from loguru import logger

from trend_hunter.core.types import Health, HealthState, RawSignal

GITHUB_TRENDING_URL = "https://github.com/trending"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class GitHubTrendingScraper:
    name = "github"

    def __init__(
        self,
        *,
        max_repos: int = 25,
        timeout_s: float = 20.0,
    ) -> None:
        self.max_repos = max_repos
        self.timeout_s = timeout_s

    async def fetch(self) -> list[RawSignal]:
        now = datetime.now(UTC)
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=True,
        ) as client:
            try:
                r = await client.get(GITHUB_TRENDING_URL)
                r.raise_for_status()
            except Exception as e:
                logger.warning(f"github: trending page failed: {e}")
                return []

            signals = _parse_trending_html(r.text, now, self.max_repos)
            if not signals and len(r.text) > 1000:
                logger.warning("github: parsed 0 repos — HTML may have changed or CAPTCHA")

        logger.info(f"github: ingested {len(signals)} trending repos")
        return signals

    async def health(self) -> Health:
        return Health(
            source=self.name,
            state=HealthState.OK,
            last_run=None,
            last_ok=None,
            rows_in=0,
            error_rate=0.0,
            detail=f"max_repos={self.max_repos}",
        )


def _parse_trending_html(html: str, run_ts: datetime, max_repos: int) -> list[RawSignal]:
    """Extract trending repos from GitHub's trending page HTML.

    Parses repo name, description, stars, and language from the page.
    Uses regex rather than BeautifulSoup to keep dependencies minimal.
    """
    signals: list[RawSignal] = []

    # Each repo is in an <article> with class "Box-row"
    articles = re.split(r'<article[^>]*class="[^"]*Box-row[^"]*"[^>]*>', html)[1:]

    for i, article in enumerate(articles[:max_repos]):
        try:
            # Extract repo name from href (e.g., "/owner/repo")
            name_match = re.search(r'href="/([^/]+/[^/"]+)"', article)
            repo_name = name_match.group(1) if name_match else f"unknown-{i}"

            # Extract description
            desc_match = re.search(
                r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', article, re.S
            )
            desc = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip() if desc_match else ""

            # Extract stars
            stars_match = re.search(r"(\d[\d,]*)\s*stars", article)
            stars = int(stars_match.group(1).replace(",", "")) if stars_match else 0

            # Extract language
            lang_match = re.search(r'itemprop="programmingLanguage"[^>]*>([^<]+)<', article)
            language = lang_match.group(1).strip() if lang_match else "Unknown"

            # Extract forks
            forks_match = re.search(r"(\d[\d,]*)\s*forks", article)
            forks = int(forks_match.group(1).replace(",", "")) if forks_match else 0

            signals.append(
                RawSignal(
                    source="github",
                    external_id=repo_name,
                    captured_at=run_ts,
                    payload={
                        "title": repo_name,
                        "description": desc[:300],
                        "url": f"https://github.com/{repo_name}",
                        "language": language,
                        "stars": stars,
                        "forks": forks,
                        "price": float(stars),  # proxy: stars as "price" for trend scoring
                        "currency": "STARS",
                    },
                )
            )
        except Exception:
            continue

    return signals
