# Trend Crawler Architecture - Surface Plugin System

[← Data Model](./DESIGN-DATA-MODEL.md) | **Part 3 of 4** | [Workers & API](./DESIGN-WORKERS-API.md)

---

## Surface Plugin System

### What is a Surface Collector?

A **surface collector** is a Python module that knows how to:
1. Fetch trend data from a specific source
2. Normalize it into our standard format
3. Handle pagination via cursor

All collectors implement a simple interface, making them pluggable and testable.

---

## Interface Definition

```python
# src/crawler_api/surfaces/interfaces.py

from typing import Protocol, Optional
from datetime import datetime

class TrendSurfaceCollector(Protocol):
    """
    Interface for trend surface collectors.

    Each collector is responsible for:
    1. Fetching trend data from a specific source
    2. Normalizing it into the standard format
    3. Handling pagination via cursor
    """

    async def collect(
        self,
        config: dict,
        cursor: Optional[str],
        limit: int
    ) -> tuple[list[dict], Optional[str]]:
        """
        Collect trend items from this surface.

        Args:
            config: Surface-specific configuration from TrendSurface.config_json
            cursor: Pagination cursor from last run (or None for first run)
            limit: Maximum items to collect (from TrendSurface.max_items_per_run)

        Returns:
            (items, next_cursor) tuple where:
            - items: List of normalized item dicts
            - next_cursor: Pagination cursor for next run (or None if exhausted)
        """
        ...
```

---

## Normalized Item Format

Each collector must return items in this format:

```python
{
    "external_id": "abc123",           # Optional: Platform's unique ID
    "title": "Trending topic here",    # Required: Original title
    "description": "More details...",  # Optional: Description/snippet
    "url": "https://...",              # Required: Link to content
    "published_at": "2024-01-15T10:30:00Z",  # Optional: ISO 8601 timestamp
    "locale": "en-US",                 # Required: One of supported locales
    "raw_payload": {                   # Required: Original API response
        "upvotes": 1234,
        "comments": 56,
        # ... any platform-specific data
    }
}
```

### Field Requirements

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `external_id` | No | string | Platform's ID, used for debugging |
| `title` | **Yes** | string | Original title in source language |
| `description` | No | string | Additional context/snippet |
| `url` | **Yes** | string | Link to original content |
| `published_at` | No | ISO 8601 | When content was published |
| `locale` | **Yes** | string | Must be one of supported locales |
| `raw_payload` | **Yes** | object | Original platform data |

---

## Example Collectors

### 1. Reddit Hot Posts

```python
# src/crawler_api/surfaces/reddit_hot.py

import asyncio
from typing import Optional

async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> tuple[list[dict], Optional[str]]:
    """
    Collect hot posts from Reddit.

    Config keys:
    - subreddit: Subreddit name (e.g. "all", "popular")
    - locale: Target locale for this surface (default: "en-US")
    """
    subreddit = config.get("subreddit", "all")
    locale = config.get("locale", "en-US")

    # In real implementation, call Reddit API here
    # Example: https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}&after={cursor}

    # For stub, return deterministic sample data
    items = [
        {
            "external_id": f"reddit_post_{i}",
            "title": f"Sample Reddit hot post #{i} from r/{subreddit}",
            "description": "This is a sample description for testing",
            "url": f"https://reddit.com/r/{subreddit}/comments/abc{i}",
            "published_at": "2024-01-15T10:30:00Z",
            "locale": locale,
            "raw_payload": {
                "upvotes": 1000 + i * 100,
                "comments": 50 + i * 5,
                "subreddit": subreddit,
                "author": f"user_{i}"
            }
        }
        for i in range(min(limit, 10))
    ]

    # Cursor pagination: in real impl, get from API response
    next_cursor = f"after_{len(items)}" if len(items) >= limit else None

    return items, next_cursor
```

**Configuration Example:**
```json
{
  "subreddit": "popular",
  "locale": "en-US"
}
```

---

### 2. YouTube Trending

```python
# src/crawler_api/surfaces/youtube_trending.py

from typing import Optional

async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> tuple[list[dict], Optional[str]]:
    """
    Collect trending videos from YouTube.

    Config keys:
    - region_code: YouTube region code (e.g. "US", "JP")
    - category_id: Video category (optional, default: all)
    - locale: Target locale (default: "en-US")
    """
    region_code = config.get("region_code", "US")
    category_id = config.get("category_id")
    locale = config.get("locale", "en-US")

    # In real implementation:
    # Use YouTube Data API v3
    # https://youtube.googleapis.com/youtube/v3/videos?part=snippet&chart=mostPopular&regionCode={region_code}

    # Stub data
    items = [
        {
            "external_id": f"youtube_video_{i}",
            "title": f"Trending YouTube video #{i} in {region_code}",
            "description": "Sample video description",
            "url": f"https://youtube.com/watch?v=abc{i}",
            "published_at": "2024-01-15T09:00:00Z",
            "locale": locale,
            "raw_payload": {
                "views": 1000000 + i * 50000,
                "likes": 50000 + i * 1000,
                "channel": f"Channel_{i}",
                "category_id": category_id or "0"
            }
        }
        for i in range(min(limit, 10))
    ]

    next_cursor = f"page_{len(items)}" if len(items) >= limit else None
    return items, next_cursor
```

**Configuration Example:**
```json
{
  "region_code": "JP",
  "category_id": "10",
  "locale": "ja-JP"
}
```

---

### 3. Yahoo Japan Ranking

```python
# src/crawler_api/surfaces/yahoo_jp_ranking.py

from typing import Optional

async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> tuple[list[dict], Optional[str]]:
    """
    Collect ranking from Yahoo Japan.

    Config keys:
    - category: Category name (e.g. "all", "news", "entertainment")
    - locale: Always "ja-JP" for Yahoo Japan
    """
    category = config.get("category", "all")
    locale = config.get("locale", "ja-JP")

    # In real implementation:
    # Scrape https://www.yahoo.co.jp/ranking/
    # Or use Yahoo Japan API if available

    # Stub data with Japanese content
    items = [
        {
            "external_id": f"yahoo_jp_item_{i}",
            "title": f"Yahoo Japan ランキング #{i}",
            "description": "サンプルの説明文",
            "url": f"https://news.yahoo.co.jp/articles/abc{i}",
            "published_at": "2024-01-15T08:00:00Z",
            "locale": locale,
            "raw_payload": {
                "rank": i + 1,
                "category": category,
                "access_count": 100000 - i * 1000
            }
        }
        for i in range(min(limit, 10))
    ]

    # Yahoo rankings typically don't paginate
    next_cursor = None
    return items, next_cursor
```

**Configuration Example:**
```json
{
  "category": "news",
  "locale": "ja-JP"
}
```

---

## Collector Registry

The system uses dynamic imports to load collectors:

```python
# src/crawler_api/surfaces/registry.py

import importlib
from typing import Callable

def get_collector(entrypoint: str) -> Callable:
    """
    Dynamically import and return collector function.

    Args:
        entrypoint: String like "crawler_surfaces.reddit_hot:collect"

    Returns:
        Collector function

    Raises:
        ImportError: If module or function not found
    """
    if ':' not in entrypoint:
        raise ValueError(f"Invalid entrypoint format: {entrypoint}")

    module_path, func_name = entrypoint.split(':', 1)

    try:
        module = importlib.import_module(module_path)
        collector = getattr(module, func_name)
        return collector
    except ImportError as e:
        raise ImportError(f"Could not import module {module_path}: {e}")
    except AttributeError as e:
        raise AttributeError(f"Module {module_path} has no function {func_name}: {e}")
```

**Usage:**
```python
# Load collector dynamically
collector = get_collector("crawler_surfaces.reddit_hot:collect")

# Call it
items, cursor = await collector(
    config={"subreddit": "popular"},
    cursor=None,
    limit=100
)
```

---

## Error Handling

Collectors should handle errors gracefully:

```python
async def collect(config, cursor, limit):
    try:
        # Fetch from API
        response = await fetch_api(...)

        # Normalize items
        items = normalize_items(response)

        return items, next_cursor

    except HTTPError as e:
        # Log and re-raise
        logger.error(f"API error: {e}")
        raise

    except ValidationError as e:
        # Data format issues
        logger.error(f"Invalid data format: {e}")
        raise

    except Exception as e:
        # Unexpected errors
        logger.error(f"Unexpected error: {e}")
        raise
```

**Worker will catch these errors and:**
- Store error message in `TrendSurface.last_run_error`
- Schedule retry with backoff
- Continue with other surfaces

---

## Rate Limiting

Collectors should implement rate limiting:

```python
from asyncio import Semaphore, sleep

# Global rate limiter (max 10 concurrent requests)
RATE_LIMITER = Semaphore(10)

async def collect(config, cursor, limit):
    async with RATE_LIMITER:
        # Rate limit per surface
        await sleep(config.get("rate_limit_delay", 1.0))

        # Fetch data
        items = await fetch_from_api(...)

        return items, cursor
```

---

## Testing Collectors

### Unit Test Example

```python
# tests/surfaces/test_reddit_hot.py

import pytest
from crawler_api.surfaces import reddit_hot

@pytest.mark.asyncio
async def test_collect_returns_valid_format():
    """Test that collector returns valid normalized format."""
    config = {"subreddit": "all", "locale": "en-US"}
    items, cursor = await reddit_hot.collect(config, None, 10)

    assert len(items) <= 10
    assert cursor is not None or len(items) < 10

    for item in items:
        # Check required fields
        assert "title" in item
        assert "url" in item
        assert "locale" in item
        assert "raw_payload" in item

        # Check locale is valid
        assert item["locale"] == "en-US"

@pytest.mark.asyncio
async def test_collect_respects_limit():
    """Test that collector respects limit parameter."""
    config = {"subreddit": "popular"}
    items, _ = await reddit_hot.collect(config, None, 5)

    assert len(items) <= 5
```

---

## Adding a New Collector

### Step-by-Step Guide

**1. Create collector module:**
```bash
touch src/crawler_api/surfaces/naver_realtime.py
```

**2. Implement `collect` function:**
```python
# src/crawler_api/surfaces/naver_realtime.py

from typing import Optional

async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> tuple[list[dict], Optional[str]]:
    """Collect from Naver real-time search."""

    # Your implementation here
    items = []
    # ... fetch and normalize ...

    return items, next_cursor
```

**3. Add to Django Admin:**
```python
TrendSurface.objects.create(
    region=region_kr,
    key='naver_realtime',
    surface_type='search',
    platform='naver',
    entrypoint='crawler_surfaces.naver_realtime:collect',
    poll_interval_seconds=1800,  # 30 min
    config_json={'locale': 'ko-KR'}
)
```

**4. Test:**
```python
# Test manually
from crawler_api.surfaces import naver_realtime
items, cursor = await naver_realtime.collect(
    config={'locale': 'ko-KR'},
    cursor=None,
    limit=10
)
print(items)
```

**5. Deploy:**
- Surface worker will automatically discover and poll it
- No restart needed (if using auto-reload)

---

## Best Practices

### DO ✅

- **Return consistent format**: Always use normalized item dict
- **Handle pagination**: Implement cursor-based pagination
- **Validate config**: Check required config keys
- **Log errors**: Use logging for debugging
- **Set timeouts**: Don't let API calls hang forever
- **Respect rate limits**: Implement delays/backoff

### DON'T ❌

- **Don't store state**: Collectors should be stateless
- **Don't modify database**: Only return data, worker handles storage
- **Don't crash on errors**: Raise exceptions, let worker handle
- **Don't hardcode limits**: Use the `limit` parameter
- **Don't skip normalization**: Always return standard format
- **Don't ignore locale**: Always set correct locale

---

**Document Version**: 1.0
**Last Updated**: 2024-01-15
**Previous**: [← Data Model](./DESIGN-DATA-MODEL.md) | **Next**: [Workers & API →](./DESIGN-WORKERS-API.md)
