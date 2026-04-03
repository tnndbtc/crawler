"""
GitHub Trending surface collector.

Scrapes the GitHub Trending page for today's top repositories.
URL: https://github.com/trending  (or /trending/{language}?since=daily)

No authentication required. Uses BeautifulSoup for HTML parsing.
Locale: en-US
Surface type: ranking
Bucket: hot_now
"""

import logging
from typing import Optional, Tuple, List

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

GITHUB_TRENDING_BASE = "https://github.com/trending"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_star_count(text: str) -> int:
    """
    Parse star count string to integer.

    Examples:
        "1,234" → 1234
        "12.5k" → 12500
        "1,234 stars today" → 1234
    """
    if not text:
        return 0
    clean = text.strip().lower().replace(",", "").split()[0]
    try:
        if clean.endswith("k"):
            return int(float(clean[:-1]) * 1000)
        return int(clean)
    except (ValueError, TypeError):
        return 0


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending repositories from GitHub Trending page.

    Config params (from TrendSurface.config_json):
        - language: Filter by programming language (default: all languages)
        - since: Time range — daily | weekly | monthly (default: daily)

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused — GitHub trending has no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On HTTP request failures
    """
    language = config.get("language", "")
    since = config.get("since", "daily")

    # Build URL
    if language:
        url = f"{GITHUB_TRENDING_BASE}/{language}"
    else:
        url = GITHUB_TRENDING_BASE

    params = {"since": since}

    logger.info(
        f"Fetching GitHub Trending: language={language or 'all'}, since={since}"
    )

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []

    # Each trending repo is wrapped in <article class="Box-row">
    articles = soup.select("article.Box-row")

    if not articles:
        # Fallback: try generic article elements
        articles = soup.select("article")

    logger.info(f"GitHub Trending page returned {len(articles)} repo articles")

    for rank, article in enumerate(articles[:limit], start=1):
        # --- Repo name & owner ---
        h2 = article.select_one("h2")
        if not h2:
            continue
        link_el = h2.select_one("a[href]")
        if not link_el:
            continue

        href = link_el.get("href", "").strip().lstrip("/")
        parts = href.split("/")
        if len(parts) < 2:
            continue
        owner, repo_name = parts[0], parts[1]
        full_name = f"{owner}/{repo_name}"
        repo_url = f"https://github.com/{full_name}"
        title = full_name

        # --- Description ---
        desc_el = article.select_one("p")
        description = desc_el.get_text(strip=True) if desc_el else None

        # --- Programming language ---
        lang_el = article.select_one('[itemprop="programmingLanguage"]')
        repo_language = lang_el.get_text(strip=True) if lang_el else ""

        # --- Total stars ---
        stars_link = article.select_one(f'a[href="/{full_name}/stargazers"]')
        total_stars = _parse_star_count(stars_link.get_text(strip=True)) if stars_link else 0

        # --- Forks ---
        forks_link = article.select_one(f'a[href="/{full_name}/forks"]')
        forks = _parse_star_count(forks_link.get_text(strip=True)) if forks_link else 0

        # --- Stars today ---
        stars_today = 0
        for span in article.select("span"):
            text = span.get_text(strip=True).lower()
            if "stars today" in text or "star today" in text:
                stars_today = _parse_star_count(text)
                break

        item: CollectedItem = {
            "title": title,
            "url": repo_url,
            "locale": "en-US",
            "rank_position": rank,
            "description": description,
            "engagement_signals": {
                "stars": total_stars,
                "forks": forks,
                "stars_today": stars_today,
            },
            "raw_payload": {
                "owner": owner,
                "repo": repo_name,
                "full_name": full_name,
                "description": description,
                "language": repo_language,
                "stars": total_stars,
                "forks": forks,
                "stars_today": stars_today,
                "since": since,
                "source": "github_trending",
            },
        }
        items.append(item)

    logger.info(f"Collected {len(items)} repositories from GitHub Trending")
    return items, None
