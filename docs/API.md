# API Reference

FastAPI read-only endpoints for consuming trend data.

**Base URL**: `http://localhost:8000/api/v1`

---

## Authentication

Currently, all endpoints are **public** and **read-only**. No authentication required.

Future versions may add API key authentication for rate limiting.

---

## Endpoints

### 1. List Regions

Get all enabled regions with their metadata.

```http
GET /api/v1/regions
```

#### Query Parameters

None

#### Response

```json
{
  "regions": [
    {
      "key": "us",
      "name": "United States",
      "default_locale": "en-US",
      "surfaces_count": 5,
      "latest_item_at": "2024-01-15T12:30:00Z"
    },
    {
      "key": "jp",
      "name": "Japan",
      "default_locale": "ja-JP",
      "surfaces_count": 3,
      "latest_item_at": "2024-01-15T12:25:00Z"
    }
  ],
  "count": 2
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | Unique region identifier |
| `name` | string | Human-readable region name |
| `default_locale` | string | Default locale for this region |
| `surfaces_count` | integer | Number of enabled surfaces |
| `latest_item_at` | string (ISO 8601) | Timestamp of most recent trend item |

#### Example

```bash
curl http://localhost:8000/api/v1/regions
```

---

### 2. List Surfaces

Get all enabled trend surfaces, optionally filtered by region.

```http
GET /api/v1/surfaces
```

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `region` | string | No | Filter by region key (e.g., `us`, `jp`) |

#### Response

```json
{
  "surfaces": [
    {
      "key": "reddit_hot",
      "region": "us",
      "surface_type": "ranking",
      "platform": "reddit",
      "enabled": true,
      "poll_interval_seconds": 3600,
      "last_run_at": "2024-01-15T11:00:00Z",
      "next_run_at": "2024-01-15T12:00:00Z",
      "items_count": 150,
      "last_run_error": null
    },
    {
      "key": "youtube_trending",
      "region": "us",
      "surface_type": "ranking",
      "platform": "youtube",
      "enabled": true,
      "poll_interval_seconds": 7200,
      "last_run_at": "2024-01-15T10:30:00Z",
      "next_run_at": "2024-01-15T12:30:00Z",
      "items_count": 200,
      "last_run_error": null
    }
  ],
  "count": 2
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | Unique surface identifier within region |
| `region` | string | Region key this surface belongs to |
| `surface_type` | string | One of: `ranking`, `sampler`, `search`, `news` |
| `platform` | string | Platform identifier (e.g., `reddit`, `youtube`) |
| `enabled` | boolean | Whether surface is active |
| `poll_interval_seconds` | integer | Collection frequency in seconds |
| `last_run_at` | string (ISO 8601) | Last successful collection time |
| `next_run_at` | string (ISO 8601) | Next scheduled collection time |
| `items_count` | integer | Total items collected from this surface |
| `last_run_error` | string or null | Error message from last run, if any |

#### Examples

**Get all surfaces:**
```bash
curl http://localhost:8000/api/v1/surfaces
```

**Get surfaces for a specific region:**
```bash
curl http://localhost:8000/api/v1/surfaces?region=jp
```

---

### 3. Get Trends

Get trending items with translations.

```http
GET /api/v1/trends
```

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `region` | string | **Yes** | - | Region key (e.g., `us`, `jp`) |
| `since` | string (ISO 8601) | No | 24 hours ago | Start timestamp for filtering |
| `until` | string (ISO 8601) | No | Now | End timestamp for filtering |
| `locales` | string (comma-separated) | No | Region's default_locale | Locales to include in response |
| `surface` | string | No | All surfaces | Filter by specific surface key |
| `limit` | integer | No | 100 | Maximum items to return (max: 1000) |
| `offset` | integer | No | 0 | Pagination offset |

#### Response

```json
{
  "trends": [
    {
      "id": 12345,
      "region": "us",
      "surface": "reddit_hot",
      "surface_type": "ranking",
      "platform": "reddit",
      "url": "https://reddit.com/r/technology/comments/abc123",
      "original_locale": "en-US",
      "published_at": "2024-01-15T10:30:00Z",
      "collected_at": "2024-01-15T11:00:00Z",
      "translations": {
        "en-US": {
          "title": "New AI breakthrough announced",
          "description": "Researchers unveil groundbreaking AI model"
        },
        "ja-JP": {
          "title": "新しいAIのブレークスルーが発表される",
          "description": "研究者が画期的なAIモデルを発表"
        },
        "ko-KR": {
          "title": "새로운 AI 돌파구 발표",
          "description": "연구자들이 획기적인 AI 모델 공개"
        }
      },
      "raw_payload": {
        "upvotes": 5432,
        "comments": 234,
        "subreddit": "technology",
        "author": "user123"
      }
    },
    {
      "id": 12346,
      "region": "us",
      "surface": "youtube_trending",
      "surface_type": "ranking",
      "platform": "youtube",
      "url": "https://youtube.com/watch?v=xyz789",
      "original_locale": "en-US",
      "published_at": "2024-01-15T09:15:00Z",
      "collected_at": "2024-01-15T10:30:00Z",
      "translations": {
        "en-US": {
          "title": "Viral video takes internet by storm",
          "description": "Amazing cat compilation"
        },
        "ja-JP": {
          "title": "バイラル動画がインターネットを席巻",
          "description": "素晴らしい猫の編集"
        }
      },
      "raw_payload": {
        "views": 1234567,
        "likes": 98765,
        "channel": "CatLovers"
      }
    }
  ],
  "meta": {
    "count": 2,
    "total": 150,
    "offset": 0,
    "limit": 100,
    "next_offset": 100,
    "has_more": true
  },
  "filters": {
    "region": "us",
    "since": "2024-01-14T12:00:00Z",
    "until": "2024-01-15T12:00:00Z",
    "locales": ["en-US", "ja-JP"],
    "surface": null
  }
}
```

#### Response Fields

**Trend Object:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique trend item ID |
| `region` | string | Region key |
| `surface` | string | Surface key that collected this item |
| `surface_type` | string | Type of surface (ranking/sampler/search/news) |
| `platform` | string | Platform identifier |
| `url` | string | Link to original content |
| `original_locale` | string | Locale of original content |
| `published_at` | string (ISO 8601) or null | When content was published |
| `collected_at` | string (ISO 8601) | When we collected this item |
| `translations` | object | Map of locale → {title, description} |
| `raw_payload` | object | Platform-specific metadata |

**Meta Object:**

| Field | Type | Description |
|-------|------|-------------|
| `count` | integer | Number of items in this response |
| `total` | integer | Total items matching filters |
| `offset` | integer | Current pagination offset |
| `limit` | integer | Items per page |
| `next_offset` | integer or null | Offset for next page |
| `has_more` | boolean | Whether more pages exist |

**Filters Object:**

Echo of applied filters for debugging.

#### Examples

**Basic usage (last 24 hours in US):**
```bash
curl "http://localhost:8000/api/v1/trends?region=us"
```

**Specific time range:**
```bash
curl "http://localhost:8000/api/v1/trends?region=jp&since=2024-01-15T00:00:00Z&until=2024-01-15T12:00:00Z"
```

**Multiple locales:**
```bash
curl "http://localhost:8000/api/v1/trends?region=us&locales=en-US,ja-JP,ko-KR"
```

**Filter by surface:**
```bash
curl "http://localhost:8000/api/v1/trends?region=us&surface=reddit_hot"
```

**Pagination:**
```bash
curl "http://localhost:8000/api/v1/trends?region=us&limit=50&offset=0"
curl "http://localhost:8000/api/v1/trends?region=us&limit=50&offset=50"
```

**Complex query:**
```bash
curl "http://localhost:8000/api/v1/trends?region=jp&since=2024-01-14T00:00:00Z&locales=ja-JP,en-US&surface=yahoo_jp_ranking&limit=20"
```

---

## Error Responses

All endpoints return consistent error responses:

### 400 Bad Request

Invalid query parameters.

```json
{
  "error": "bad_request",
  "message": "Invalid region key: 'invalid'",
  "details": {
    "param": "region",
    "value": "invalid",
    "allowed": ["us", "jp", "kr", "cn"]
  }
}
```

### 404 Not Found

Resource not found.

```json
{
  "error": "not_found",
  "message": "Region 'xyz' not found"
}
```

### 422 Unprocessable Entity

Invalid data format.

```json
{
  "error": "validation_error",
  "message": "Invalid ISO 8601 timestamp",
  "details": {
    "param": "since",
    "value": "not-a-date",
    "expected": "ISO 8601 format (e.g., 2024-01-15T12:00:00Z)"
  }
}
```

### 500 Internal Server Error

Server error (should be rare).

```json
{
  "error": "internal_server_error",
  "message": "An unexpected error occurred",
  "request_id": "abc-123-def-456"
}
```

---

## Rate Limiting

**Current**: No rate limiting (public beta)

**Future**:
- Free tier: 100 requests/minute
- Authenticated: 1000 requests/minute

Rate limit headers will be added:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1642234567
```

---

## Data Freshness

**Collection Intervals:**
- Most surfaces: 1-2 hours
- High-frequency surfaces: 30 minutes
- Low-frequency surfaces: 6-12 hours

Check `next_run_at` in `/api/v1/surfaces` to see when data will be updated.

**Translation Lag:**
- Translations typically complete within 5-10 minutes of collection
- Check `translations` object - missing locales are still processing

---

## Pagination Best Practices

### Simple Pagination

For small datasets or UI display:

```python
# Page 1
GET /api/v1/trends?region=us&limit=50&offset=0

# Page 2
GET /api/v1/trends?region=us&limit=50&offset=50

# Page 3
GET /api/v1/trends?region=us&limit=50&offset=100
```

### Cursor-Based Pagination (Future)

For stable iteration over large datasets:

```python
# First page
GET /api/v1/trends?region=us&limit=50

# Use cursor from response
GET /api/v1/trends?region=us&limit=50&cursor=eyJpZCI6MTIzNDV9
```

---

## Filtering Best Practices

### Time-Based Filtering

**Get latest trends (last hour):**
```python
since = (datetime.utcnow() - timedelta(hours=1)).isoformat() + 'Z'
GET /api/v1/trends?region=us&since={since}
```

**Get trends for specific day:**
```python
since = "2024-01-15T00:00:00Z"
until = "2024-01-15T23:59:59Z"
GET /api/v1/trends?region=us&since={since}&until={until}
```

### Multi-Locale Responses

**Request multiple locales efficiently:**
```python
# Good: Request all needed locales in one call
GET /api/v1/trends?region=us&locales=en-US,ja-JP,ko-KR

# Bad: Multiple calls for same data
GET /api/v1/trends?region=us&locales=en-US
GET /api/v1/trends?region=us&locales=ja-JP
GET /api/v1/trends?region=us&locales=ko-KR
```

### Surface-Specific Queries

**Compare platforms:**
```python
# Reddit trends
reddit = GET /api/v1/trends?region=us&surface=reddit_hot

# YouTube trends
youtube = GET /api/v1/trends?region=us&surface=youtube_trending

# Analyze overlap
```

---

## WebSocket API (Future)

Real-time trend updates via WebSocket:

```javascript
const ws = new WebSocket('ws://localhost:8000/api/v1/trends/stream?region=us');

ws.onmessage = (event) => {
  const trend = JSON.parse(event.data);
  console.log('New trend:', trend);
};
```

---

## GraphQL API (Future)

Flexible querying with GraphQL:

```graphql
query {
  region(key: "us") {
    name
    surfaces {
      key
      platform
      trends(limit: 10) {
        id
        url
        translations(locales: ["en-US", "ja-JP"]) {
          locale
          title
        }
      }
    }
  }
}
```

---

## SDKs

### Python

```python
from crawler_client import CrawlerClient

client = CrawlerClient(base_url="http://localhost:8000")

# Get regions
regions = client.get_regions()

# Get trends
trends = client.get_trends(
    region="us",
    since=datetime.now() - timedelta(hours=24),
    locales=["en-US", "ja-JP"],
    limit=50
)

for trend in trends:
    print(f"{trend.translations['en-US'].title} - {trend.url}")
```

### JavaScript

```javascript
import { CrawlerClient } from '@crawler/client';

const client = new CrawlerClient({ baseURL: 'http://localhost:8000' });

// Get regions
const regions = await client.getRegions();

// Get trends
const trends = await client.getTrends({
  region: 'us',
  since: new Date(Date.now() - 24 * 60 * 60 * 1000),
  locales: ['en-US', 'ja-JP'],
  limit: 50
});

trends.forEach(trend => {
  console.log(trend.translations['en-US'].title, trend.url);
});
```

---

## OpenAPI Specification

Full OpenAPI 3.0 spec available at:

```
GET /api/v1/openapi.json
```

Interactive documentation (Swagger UI):

```
GET /docs
```

Alternative documentation (ReDoc):

```
GET /redoc
```

---

## Changelog

### v1.0.0 (2024-01-15)
- Initial release
- `/regions`, `/surfaces`, `/trends` endpoints
- Multi-locale support
- Basic pagination

### Future Versions

**v1.1.0** (Planned)
- Rate limiting
- API key authentication
- Cursor-based pagination

**v1.2.0** (Planned)
- WebSocket streaming
- Trend analytics endpoints
- Aggregation endpoints

**v2.0.0** (Planned)
- GraphQL API
- Advanced filtering (sentiment, topics)
- Machine learning insights

---

## Support

- **Documentation**: https://docs.crawler.example.com
- **Issues**: https://github.com/org/crawler/issues
- **Email**: api-support@example.com

---

**Happy Building! 🚀**
