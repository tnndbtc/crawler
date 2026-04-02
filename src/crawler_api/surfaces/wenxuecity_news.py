"""
Wenxuecity (文学城) News Collector

Collects trending news from Wenxuecity.com, a popular Chinese news aggregator.

Features:
- Web scraping (no RSS feed available)
- Extracts Chinese-language news articles
- Parses publication dates from URL structure
- Automatic translation to English via translation worker

Source: https://www.wenxuecity.com/
Language: Simplified Chinese (zh-Hans)
Update Frequency: Every 2 hours
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .adapters import raw_item_to_collected_item
from shared.http_client import RateLimitedClient
from shared.human_behavior import HumanBrowsingSession
from shared.url_utils import extract_domain

logger = logging.getLogger(__name__)

# Base URL
BASE_URL = "https://www.wenxuecity.com/"

# How many articles to fetch body text for (top N by rank, to limit HTTP overhead)
ARTICLE_BODY_FETCH_LIMIT = 20

# Max chars of article body to store (summarization worker will condense further)
ARTICLE_BODY_MAX_CHARS = 3000

# CSS selectors tried in order to find article body on wenxuecity article pages
ARTICLE_BODY_SELECTORS = [
    'div.article-content',
    'div#article-content',
    'div.content',
    'div#content',
    'div.article',
    'article',
    'div.main-content',
    'div[class*="article"]',
    'div[class*="content"]',
]

# Date pattern in URL: /news/YYYY/MM/DD/######.html
URL_DATE_PATTERN = re.compile(r'/news/(\d{4})/(\d{2})/(\d{2})/')


async def fetch_article_body(
    client: RateLimitedClient,
    url: str,
    headers: dict,
) -> Optional[str]:
    """
    Fetch a Wenxuecity article page and extract the main body text.

    Tries multiple CSS selectors in order; falls back to joining all <p> tags
    inside the page body if none match.

    Returns up to ARTICLE_BODY_MAX_CHARS of plain text, or None on failure.
    """
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Try known selectors first
        body_elem = None
        for selector in ARTICLE_BODY_SELECTORS:
            body_elem = soup.select_one(selector)
            if body_elem:
                break

        if body_elem:
            text = body_elem.get_text(separator=' ', strip=True)
        else:
            # Fallback: collect all <p> text from the page
            paragraphs = soup.find_all('p')
            text = ' '.join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

        text = text.strip()
        if len(text) < 50:
            # Too short — probably failed to find real content
            return None

        return text[:ARTICLE_BODY_MAX_CHARS]

    except Exception as e:
        logger.debug(f"Could not fetch article body from {url}: {e}")
        return None


def extract_date_from_url(url: str) -> Optional[datetime]:
    """
    Extract publication date from wenxuecity URL structure.

    URL format: /news/2026/02/15/126534469.html
    Extracts: 2026-02-15

    Args:
        url: Article URL

    Returns:
        datetime object or None if date cannot be parsed
    """
    match = URL_DATE_PATTERN.search(url)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        except ValueError:
            logger.warning(f"Invalid date in URL: {url}")
            return None
    return None


async def collect(
    config: dict,
    cursor: Optional[str] = None,
    limit: int = 50
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Collect trending news from Wenxuecity.com.

    Args:
        config: Configuration dict with optional 'locale' key
        cursor: Pagination cursor (not used - wenxuecity doesn't paginate)
        limit: Maximum items to collect

    Returns:
        Tuple of (collected_items, next_cursor)
    """
    locale = config.get("locale", "zh-Hans")

    logger.info(f"Collecting Wenxuecity news (locale={locale}, limit={limit})")

    collected_items = []

    try:
        # Use RateLimitedClient for resilience
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        async with RateLimitedClient(
            timeout=30.0,
            follow_redirects=True
        ) as client:
            # Initialize human browsing session
            domain = extract_domain(BASE_URL)
            human_session = await HumanBrowsingSession.from_config(client, domain)

            # Optional: maybe visit homepage (though we're already fetching it)
            # This adds human-like behavior before the actual fetch
            await human_session.maybe_visit_homepage(BASE_URL)

            # Fetch homepage (request_role="direct" by default)
            response = await client.get(BASE_URL, headers=headers)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find all news article links
            # Structure: <li><a href="/news/YYYY/MM/DD/######.html">Title</a></li>
            news_links = soup.select('li > a[href*="/news/"]')

            logger.info(f"Found {len(news_links)} news articles on wenxuecity homepage")

            # Process articles (limited by 'limit' parameter)
            for idx, link_elem in enumerate(news_links[:limit]):
                try:
                    # Extract data
                    title = link_elem.get_text(strip=True)
                    relative_url = link_elem.get('href', '')

                    # Skip if missing essential data
                    if not title or not relative_url:
                        continue

                    # Make URL absolute
                    full_url = urljoin(BASE_URL, relative_url)

                    # Extract publication date from URL
                    published_at = extract_date_from_url(relative_url)
                    if not published_at:
                        # Fallback to current time if date cannot be extracted
                        published_at = datetime.now(timezone.utc)

                    # Fetch article body for top-ranked articles
                    # Gives the summarization worker real content to work with
                    article_body = None
                    if idx < ARTICLE_BODY_FETCH_LIMIT:
                        article_body = await fetch_article_body(client, full_url, headers)
                        if article_body:
                            logger.debug(
                                f"Fetched article body for rank {idx+1}: {len(article_body)} chars"
                            )
                        # Small delay to be polite between article fetches
                        await asyncio.sleep(0.5)
                    else:
                        # Still do human-like prefetch for lower-ranked items
                        await human_session.optional_prefetch(full_url)

                    # Create unique source ID from URL
                    source_id = f"wenxuecity_{relative_url.replace('/', '_')}"

                    # Build collected item
                    item = raw_item_to_collected_item(
                        source="wenxuecity",
                        source_id=source_id,
                        url=full_url,
                        title=title,
                        description=article_body,  # Raw body for top articles; None for the rest
                        author=None,
                        published_at=published_at,
                        rank_position=idx + 1,
                        engagement_signals={},  # No metrics in listing
                        locale=locale,
                        raw_payload={
                            "source_name": "文学城",
                            "language": "zh-Hans",
                            "content_type": "news",
                            "article_body": article_body,  # Preserved even after summary overwrites description_original
                        }
                    )

                    collected_items.append(item)

                except Exception as e:
                    logger.warning(f"Error parsing wenxuecity article at index {idx}: {e}")
                    continue

            logger.info(f"Successfully collected {len(collected_items)} items from wenxuecity")

            # No pagination on wenxuecity homepage
            return collected_items, None

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching wenxuecity: {e}")
        return [], None
    except Exception as e:
        logger.error(f"Unexpected error collecting wenxuecity news: {e}")
        return [], None
