# Culture-Flexible Trend Crawler

A region-first trend aggregation framework that collects trending content from multiple platforms across different cultures.

## 🌍 Core Concept: Culture-Flexible Design

### What Makes This Different?

**Traditional platform-centric crawlers:**
```
❌ Hardcoded: "Scrape Reddit, YouTube, Twitter"
❌ US-centric assumptions baked into code
❌ Adding new regions requires significant rewrites
```

**Our culture-flexible approach:**
```
✅ Configuration-driven: "Define trend surfaces per region"
✅ Region-aware: Each culture has its own important platforms
✅ Extensible: Adding new regions is mostly configuration
```

### Example: How Different Regions Work

**United States** (en-US):
- Reddit Hot Posts
- YouTube Trending
- Google Trends
- Twitter Trending

**Japan** (ja-JP):
- Yahoo Japan Ranking
- Niconico Trending
- 2channel Topics
- LINE News

**South Korea** (ko-KR):
- Naver Real-time Search
- Daum Popular Topics
- DCInside Hot Posts
- Kakao Trending

**China** (zh-Hans):
- Weibo Hot Search
- Zhihu Trending
- Douyin (TikTok) Hot
- Baidu Trending

Each region defines which "surfaces" matter for that culture!

---

## 🏗️ Architecture Overview

```
┌─────────────┐
│ Django Admin│ ← Configure regions & surfaces
└──────┬──────┘
       │
       ▼
┌─────────────┐      ┌──────────────┐
│   SQLite    │◄────►│  FastAPI     │ ← Public read API
│  Database   │      │  Read Server │
└──────┬──────┘      └──────────────┘
       │
       ▼
┌──────────────┐     ┌───────────────┐
│Surface Worker│────►│Plugin Surfaces│ ← Collect trends
└──────────────┘     └───────────────┘

┌──────────────┐
│Translation   │ ← Enrich with translations
│Worker        │
└──────────────┘
```

---

## 🎯 What Are "Trend Surfaces"?

A **trend surface** is any source that reveals trending topics in a region. We categorize them into 4 types:

### 1. RankingSurface
Curated/ranked lists maintained by platforms.

**Examples:**
- Reddit "Hot" page
- YouTube Trending
- Weibo Hot Search
- Yahoo Japan Ranking

### 2. FeedSampler
Sample algorithmic feeds to infer trends.

**Examples:**
- TikTok "For You" page samples
- Instagram Explore samples
- Twitter timeline samples

### 3. SearchTrends
Search spike/rank pages.

**Examples:**
- Google Trends
- Naver Real-time Search
- Baidu Hot Search
- Twitter Trending Searches

### 4. NewsTopStories
Headline/portal ranking pages.

**Examples:**
- Yahoo News Top Stories
- Google News Top Headlines
- Naver News Ranking
- Reddit /r/news

Each surface is **configured per region** with:
- Platform identifier
- Collection interval
- Python collector module
- Platform-specific config (API keys, filters, etc.)

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- SQLite 3
- DeepL API key (for translations)

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create admin user
python manage.py createsuperuser

# Start Django admin
python manage.py runserver

# Start FastAPI server
uvicorn crawler_api.main:app --reload

# Start workers (in separate terminals)
./scripts/run_surface_worker.sh
./scripts/run_translation_worker.sh
```

### Configure Your First Region

1. **Login to Django Admin**: http://localhost:8000/admin

2. **Add a Region**:
   - Key: `us`
   - Name: `United States`
   - Default Locale: `en-US`
   - Enabled: ✓

3. **Add a Surface**:
   - Region: `us`
   - Key: `reddit_hot`
   - Surface Type: `ranking`
   - Platform: `reddit`
   - Entrypoint: `crawler_surfaces.reddit_hot:collect`
   - Poll Interval: `3600` (1 hour)
   - Max Items: `200`
   - Config JSON: `{"subreddit": "all", "locale": "en-US"}`

4. **Configure Translation**:
   - Default Provider: `deepl`
   - Enabled Locales: `["en-US", "ja-JP", "ko-KR", "zh-Hans"]`
   - Enabled: ✓

5. **Watch the magic happen!**
   - Surface worker starts collecting every hour
   - Translation worker enriches items in all enabled locales
   - FastAPI serves trends via `/api/v1/trends?region=us`

---

## 📖 How to Add a New Region

### Example: Adding France

**Step 1: Create Region** (Django Admin)
```
Key: fr
Name: France
Default Locale: fr-FR
Enabled: ✓
```

**Step 2: Add Surfaces** (Django Admin)

For France, you might want:
- Google News France (`news` surface)
- Twitter France Trending (`search` surface)
- Reddit /r/france (`ranking` surface)

Example surface config:
```json
{
  "region": "fr",
  "key": "google_news_fr",
  "surface_type": "news",
  "platform": "google_news",
  "entrypoint": "crawler_surfaces.google_news:collect",
  "config_json": {
    "country": "FR",
    "locale": "fr-FR",
    "topics": ["business", "technology", "entertainment"]
  }
}
```

**Step 3: Implement Collector** (if needed)

If `crawler_surfaces.google_news:collect` doesn't exist yet:

```python
# src/crawler_api/surfaces/google_news.py

async def collect(config, cursor, limit):
    """Collect top stories from Google News."""
    country = config.get("country", "US")
    locale = config.get("locale", "en-US")

    # Call Google News API/RSS
    items = []
    # ... fetch and normalize ...

    return items, next_cursor
```

**Step 4: Done!**

The system automatically:
- Polls new surfaces based on their interval
- Stores items in the database
- Translates to enabled locales
- Serves via API

**Total time: ~30 minutes** (mostly waiting for API credentials)

---

## 🔌 Plugin System

### Surface Collector Interface

All collectors implement this simple interface:

```python
async def collect(
    config: dict,        # From TrendSurface.config_json
    cursor: str | None,  # Pagination cursor
    limit: int           # Max items to fetch
) -> tuple[list[dict], str | None]:
    """
    Returns:
        (items, next_cursor) where items are normalized dicts
    """
    ...
```

### Normalized Item Format

```python
{
    "external_id": "platform_id_123",      # Optional
    "title": "Trending topic here",        # Required
    "description": "More details...",      # Optional
    "url": "https://...",                  # Required
    "published_at": "2024-01-15T10:30Z",  # Optional (ISO 8601)
    "locale": "en-US",                     # Required (one of supported)
    "raw_payload": {                       # Required (original data)
        "upvotes": 1234,
        "comments": 56,
        # ... any platform-specific fields
    }
}
```

### Supported Locales

- `zh-Hans` - Chinese Simplified
- `zh-Hant` - Chinese Traditional
- `en-US` - English (US)
- `es-ES` - Spanish (Spain)
- `fr-FR` - French (France)
- `de-DE` - German (Germany)
- `ja-JP` - Japanese (Japan)
- `ko-KR` - Korean (South Korea)
- `ru-RU` - Russian (Russia)
- `ar-SA` - Arabic (Saudi Arabia)

---

## 🌐 Translation System

### How It Works

1. **Ingestion First**: Surface worker collects items in their original locale
2. **Async Translation**: Translation worker picks up new items
3. **Multi-Locale**: Translates to all enabled locales (except original)
4. **Provider Selection**: Uses DeepL by default, OpenAI as fallback

### Example Flow

```
1. Collect Japanese item from Yahoo Japan
   → original_locale: ja-JP
   → title_original: "新しいトレンド"

2. Translation worker sees enabled_locales: [en-US, ko-KR, zh-Hans]

3. Creates translations:
   → en-US: "New Trend"
   → ko-KR: "새로운 트렌드"
   → zh-Hans: "新趋势"

4. API returns all versions:
   GET /api/v1/trends?region=jp&locales=en-US,ja-JP
```

### Provider Configuration

**DeepL** (default, high quality):
```python
TranslationSettings(
    default_provider='deepl',
    enabled_locales=['en-US', 'ja-JP', 'ko-KR']
)
```

**OpenAI** (fallback, more flexible):
```python
TranslationSettings(
    default_provider='openai',
    enabled_locales=['en-US', 'ar-SA']  # DeepL doesn't support Arabic well
)
```

---

## 📊 API Usage

### List Regions
```bash
curl http://localhost:8000/api/v1/regions
```

**Response:**
```json
{
  "regions": [
    {"key": "us", "name": "United States", "surfaces_count": 5},
    {"key": "jp", "name": "Japan", "surfaces_count": 3}
  ]
}
```

### Get Trends
```bash
curl "http://localhost:8000/api/v1/trends?region=us&since=2024-01-15T00:00:00Z&locales=en-US,ja-JP"
```

**Response:**
```json
{
  "trends": [
    {
      "id": 123,
      "region": "us",
      "surface": "reddit_hot",
      "url": "https://reddit.com/...",
      "translations": {
        "en-US": {
          "title": "Trending post",
          "description": "Description"
        },
        "ja-JP": {
          "title": "トレンド投稿",
          "description": "説明"
        }
      }
    }
  ]
}
```

### Query Parameters
- `region` *(required)*: Region key (e.g., `us`, `jp`)
- `since` *(optional)*: ISO 8601 timestamp (default: last 24h)
- `locales` *(optional)*: Comma-separated locale codes
- `surface` *(optional)*: Filter by surface key
- `limit` *(optional)*: Max items (default: 100)

---

## 🛠️ Implementation Checklist

- [ ] Django models (Region, TrendSurface, TrendItem, etc.)
- [ ] Django admin configuration
- [ ] Surface collector interface
- [ ] 3 stub collectors (reddit, youtube, yahoo_jp)
- [ ] Surface worker (async polling)
- [ ] Translation worker (async enrichment)
- [ ] FastAPI endpoints (regions, surfaces, trends)
- [ ] Database migrations
- [ ] Worker shell scripts
- [ ] Documentation (this file + DESIGN.md)

---

## 📚 Documentation

- **[DESIGN.md](./DESIGN.md)**: Detailed architecture and data model design
- **[API Reference](./API.md)**: Complete API documentation (TODO)
- **[Deployment Guide](./DEPLOYMENT.md)**: Production deployment guide (TODO)

---

## 🤝 Contributing

### Adding a New Platform

1. Create collector module: `src/crawler_api/surfaces/your_platform.py`
2. Implement `async def collect(config, cursor, limit)` function
3. Return normalized items with correct locale
4. Add to Django admin as a TrendSurface
5. Test with worker

### Adding a New Locale

1. Add locale code to `SUPPORTED_LOCALES` constant
2. Update TranslationSettings.enabled_locales
3. Ensure translation provider supports the locale
4. Test translation flow

---

## ⚠️ Design Principles (MUST FOLLOW)

### ✅ DO:
- Model as "trend surfaces per region"
- Make regions first-class entities
- Use configuration for new surfaces
- Keep collectors stateless and simple
- Deduplicate items by canonical hash
- Handle errors gracefully (don't crash workers)

### ❌ DON'T:
- Hardcode platform assumptions in core code
- Assume all regions use the same platforms
- Skip deduplication (causes duplicates across surfaces)
- Block ingestion on translation (it's async!)
- Put business logic in admin UI
- Create monolithic collectors (keep them focused)

---

## 📝 License

MIT

---

## 🙋 FAQ

**Q: Why SQLite instead of PostgreSQL?**
A: Simplicity for MVP. Easy to upgrade later if needed.

**Q: Why not use Celery for workers?**
A: Polling is simpler for this use case. No queue infrastructure needed.

**Q: How do I handle rate limits?**
A: Configure `poll_interval_seconds` and implement backoff in collectors.

**Q: Can I add custom fields to TrendItem?**
A: Use `raw_payload` JSON field for platform-specific data.

**Q: How do I prioritize certain surfaces?**
A: Adjust `poll_interval_seconds` - lower = more frequent.

**Q: What if DeepL doesn't support my locale?**
A: Switch to OpenAI provider in TranslationSettings.

---

**Happy Crawling! 🚀**
