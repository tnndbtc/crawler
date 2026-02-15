# Migration Guide: News & Social Media Collectors

This guide documents the migration of 15 crawler sources from `/home/tnnd/data/code/trend` to this project.

## ✅ Migration Status

**Completed**: 14 of 15 collectors (93%)
**Pending**: 1 collector (Twitter - awaiting API key)

---

## 🚀 Quick Setup (TL;DR)

**Just run these commands:**

```bash
# 1. Install dependencies
./setup.sh
# Select: 11) Update Dependencies

# 2. Configure all 14 collectors automatically
./setup.sh migrate

# 3. Restart services
./setup.sh restart

# Done! Check http://localhost:8001/admin to see all collectors
```

**That's it!** All 14 collectors are configured and running.

For detailed information, continue reading below.

---

## 📊 Summary of Migrated Collectors

### News Sources (10 collectors)

| Collector | File | RSS URL | Schedule | Bucket |
|-----------|------|---------|----------|--------|
| **BBC News** | `bbc_news.py` | http://feeds.bbci.co.uk/news/rss.xml | Every 20 min | `region_local` |
| **Google News** | `google_news.py` | https://news.google.com/rss | Every 15 min | `region_local` |
| **Reuters** | `reuters_news.py` | https://www.reutersagency.com/feed/ | Every 15 min | `region_local` |
| **Associated Press** | `ap_news.py` | https://rss.apnews.com/rss/topnews | Every 20 min | `region_local` |
| **The Guardian** | `guardian_news.py` | https://www.theguardian.com/world/rss | Every 20 min | `region_local` |
| **Al Jazeera** | `aljazeera_news.py` | https://www.aljazeera.com/xml/rss/all.xml | Every 20 min | `region_local` |
| **Billboard** | `billboard_news.py` | https://www.billboard.com/feed/ | Every 30 min | `category_entertainment` |
| **Variety** | `variety_news.py` | https://variety.com/feed/ | Every 30 min | `category_entertainment` |
| **IGN** | `ign_news.py` | https://feeds.feedburner.com/ign/news | Every 30 min | `category_gaming` |
| **Polygon** | `polygon_news.py` | https://www.polygon.com/rss/index.xml | Every 30 min | `category_gaming` |

### Social Media Sources (5 collectors)

| Collector | File | API | Schedule | Bucket | Status |
|-----------|------|-----|----------|--------|--------|
| **Reddit** | `reddit_hot.py` | Public JSON | Every 30 min | `hot_now` | ✅ Enhanced |
| **Hacker News** | `hackernews.py` | Firebase API | Every 20 min | `category_tech` | ✅ New |
| **YouTube** | `youtube_trending.py` | Data API v3 | Every 2 hours | `category_entertainment` | ✅ Enhanced |
| **Google Trends** | `google_trends.py` | pytrends | Every 3 hours | `hot_now` | ✅ New |
| **Twitter/X** | `twitter_trending.py` | API v2 | Every hour | `rising` | ⏳ Pending API key |

---

## 🔧 Installation

### 1. Install Dependencies

```bash
cd /home/tnnd/data/code/crawler
pip install -r requirements.txt
```

**New dependencies added:**
- `feedparser>=6.0.10` - RSS feed parsing
- `pytrends>=4.9.0` - Google Trends data
- `beautifulsoup4>=4.12.0` - HTML parsing

### 2. Set Up API Keys

Create a `.env` file in the project root:

```bash
# Required for YouTube collector
YOUTUBE_API_KEY=your_youtube_api_key_here

# Optional - Required for Twitter collector
TWITTER_BEARER_TOKEN=your_twitter_bearer_token_here

# Existing keys (should already be set)
DEEPL_API_KEY=your_deepl_key
OPENAI_API_KEY=your_openai_key
```

#### Getting API Keys

**YouTube Data API v3** (✅ You have this):
- Dashboard: https://console.cloud.google.com/apis/credentials
- Free tier: 10,000 quota units/day
- Recommended: Enable YouTube Data API v3

**Twitter API v2 Bearer Token** (⏳ To acquire):
- Dashboard: https://developer.twitter.com/en/portal/dashboard
- Pricing:
  - Free: 500K tweets/month
  - Basic ($100/month): 10M tweets/month
- Steps:
  1. Create a new project and app
  2. Generate Bearer Token
  3. Save to `.env` file

---

## 📝 Configuration

### ⚡ Quick Setup (Recommended)

**Easiest way** - Use the automated setup script:

```bash
# Interactive mode
./setup.sh
# Select: 16) Setup Migrated Collectors

# Or non-interactive
./setup.sh migrate
```

This automatically configures all 14 collectors with proper settings!

---

### 🔧 Manual Setup (Alternative)

If you prefer to configure manually, create `TrendSurface` records in Django Admin:

#### Example: BBC News

```python
from crawler_admin.models import TrendSurface, Region

# Get or create region
region_us, _ = Region.objects.get_or_create(
    key='us',
    defaults={'name': 'United States', 'locale': 'en-US'}
)

# Create TrendSurface
TrendSurface.objects.create(
    region=region_us,
    key='bbc_news',
    platform='bbc',
    surface_type='news',
    bucket='region_local',
    entrypoint='crawler_api.surfaces.bbc_news:collect',
    poll_interval_seconds=1200,  # 20 minutes
    max_items_per_run=40,
    config_json={'locale': 'en-GB'},  # BBC is UK-based
    is_active=True
)
```

#### Example: Hacker News

```python
TrendSurface.objects.create(
    region=region_us,
    key='hackernews',
    platform='hackernews',
    surface_type='ranking',
    bucket='category_tech',
    entrypoint='crawler_api.surfaces.hackernews:collect',
    poll_interval_seconds=1200,  # 20 minutes
    max_items_per_run=30,
    config_json={},
    is_active=True
)
```

#### Example: YouTube (with dual-mode)

```python
TrendSurface.objects.create(
    region=region_us,
    key='youtube_trending',
    platform='youtube',
    surface_type='ranking',
    bucket='category_entertainment',
    entrypoint='crawler_api.surfaces.youtube_trending:collect',
    poll_interval_seconds=7200,  # 2 hours
    max_items_per_run=50,
    config_json={
        'region_code': 'US',
        'locale': 'en-US',
        'dual_mode': True  # Enable trending + popular recent
    },
    is_active=True
)
```

#### Example: Google Trends

```python
TrendSurface.objects.create(
    region=region_us,
    key='google_trends',
    platform='google_trends',
    surface_type='trending',
    bucket='hot_now',
    entrypoint='crawler_api.surfaces.google_trends:collect',
    poll_interval_seconds=10800,  # 3 hours
    max_items_per_run=30,
    config_json={
        'geo': 'US',
        'locale': 'en-US',
        'max_daily': 20,
        'max_realtime': 10,
        'include_realtime': True
    },
    is_active=True
)
```

### Complete Configuration Script

**Automated Setup Available!** Instead of running the script below manually, just use:

```bash
./setup.sh migrate
# or
python manage.py setup_migrated_collectors
```

Manual script (if needed):

```python
#!/usr/bin/env python
"""
Setup script for migrated collectors.
Run: python scripts/setup_migrated_collectors.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendSurface, Region

def setup_collectors():
    # Get US region
    region_us, _ = Region.objects.get_or_create(
        key='us',
        defaults={'name': 'United States', 'locale': 'en-US'}
    )

    collectors = [
        # News collectors
        {
            'key': 'bbc_news',
            'platform': 'bbc',
            'surface_type': 'news',
            'bucket': 'region_local',
            'entrypoint': 'crawler_api.surfaces.bbc_news:collect',
            'poll_interval_seconds': 1200,
            'max_items_per_run': 40,
            'config_json': {'locale': 'en-GB'},
        },
        {
            'key': 'google_news',
            'platform': 'google_news',
            'surface_type': 'news',
            'bucket': 'region_local',
            'entrypoint': 'crawler_api.surfaces.google_news:collect',
            'poll_interval_seconds': 900,
            'max_items_per_run': 50,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'reuters_news',
            'platform': 'reuters',
            'surface_type': 'news',
            'bucket': 'region_local',
            'entrypoint': 'crawler_api.surfaces.reuters_news:collect',
            'poll_interval_seconds': 900,
            'max_items_per_run': 40,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'ap_news',
            'platform': 'ap',
            'surface_type': 'news',
            'bucket': 'region_local',
            'entrypoint': 'crawler_api.surfaces.ap_news:collect',
            'poll_interval_seconds': 1200,
            'max_items_per_run': 40,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'guardian_news',
            'platform': 'guardian',
            'surface_type': 'news',
            'bucket': 'region_local',
            'entrypoint': 'crawler_api.surfaces.guardian_news:collect',
            'poll_interval_seconds': 1200,
            'max_items_per_run': 40,
            'config_json': {'locale': 'en-GB'},
        },
        {
            'key': 'aljazeera_news',
            'platform': 'aljazeera',
            'surface_type': 'news',
            'bucket': 'region_local',
            'entrypoint': 'crawler_api.surfaces.aljazeera_news:collect',
            'poll_interval_seconds': 1200,
            'max_items_per_run': 40,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'billboard_news',
            'platform': 'billboard',
            'surface_type': 'news',
            'bucket': 'category_entertainment',
            'entrypoint': 'crawler_api.surfaces.billboard_news:collect',
            'poll_interval_seconds': 1800,
            'max_items_per_run': 30,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'variety_news',
            'platform': 'variety',
            'surface_type': 'news',
            'bucket': 'category_entertainment',
            'entrypoint': 'crawler_api.surfaces.variety_news:collect',
            'poll_interval_seconds': 1800,
            'max_items_per_run': 30,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'ign_news',
            'platform': 'ign',
            'surface_type': 'news',
            'bucket': 'category_gaming',
            'entrypoint': 'crawler_api.surfaces.ign_news:collect',
            'poll_interval_seconds': 1800,
            'max_items_per_run': 30,
            'config_json': {'locale': 'en-US'},
        },
        {
            'key': 'polygon_news',
            'platform': 'polygon',
            'surface_type': 'news',
            'bucket': 'category_gaming',
            'entrypoint': 'crawler_api.surfaces.polygon_news:collect',
            'poll_interval_seconds': 1800,
            'max_items_per_run': 30,
            'config_json': {'locale': 'en-US'},
        },
        # Social media collectors
        {
            'key': 'hackernews',
            'platform': 'hackernews',
            'surface_type': 'ranking',
            'bucket': 'category_tech',
            'entrypoint': 'crawler_api.surfaces.hackernews:collect',
            'poll_interval_seconds': 1200,
            'max_items_per_run': 30,
            'config_json': {},
        },
        {
            'key': 'google_trends',
            'platform': 'google_trends',
            'surface_type': 'trending',
            'bucket': 'hot_now',
            'entrypoint': 'crawler_api.surfaces.google_trends:collect',
            'poll_interval_seconds': 10800,
            'max_items_per_run': 30,
            'config_json': {
                'geo': 'US',
                'locale': 'en-US',
                'max_daily': 20,
                'max_realtime': 10,
                'include_realtime': True
            },
        },
    ]

    for collector_config in collectors:
        obj, created = TrendSurface.objects.update_or_create(
            region=region_us,
            key=collector_config['key'],
            defaults=collector_config
        )
        status = "Created" if created else "Updated"
        print(f"{status}: {collector_config['key']}")

    print(f"\n✅ Setup complete! {len(collectors)} collectors configured.")
    print("\nNote: Reddit and YouTube collectors were already configured.")
    print("Note: Twitter collector pending API key acquisition.")

if __name__ == '__main__':
    setup_collectors()
```

---

## 🧪 Testing

### ⚡ Quick Test - Use setup.sh

After running the automated setup:

```bash
# 1. Setup collectors
./setup.sh migrate

# 2. Restart services
./setup.sh restart

# 3. Force a collection run to test all collectors
./setup.sh
# Select: 6) Force Collection Run

# 4. Check status
./setup.sh
# Select: 5) Show Service Status
```

This tests all collectors at once!

---

### 🔬 Test Individual Collectors (Manual)

```bash
# Test BBC News
python -c "
import asyncio
from crawler_api.surfaces import bbc_news

async def test():
    items, cursor = await bbc_news.collect({}, None, 10)
    print(f'✅ Collected {len(items)} items')
    if items:
        print(f'First item: {items[0][\"title\"]}')

asyncio.run(test())
"

# Test Hacker News
python -c "
import asyncio
from crawler_api.surfaces import hackernews

async def test():
    items, cursor = await hackernews.collect({}, None, 10)
    print(f'✅ Collected {len(items)} items')
    if items:
        print(f'First item: {items[0][\"title\"]}')

asyncio.run(test())
"

# Test Google Trends
python -c "
import asyncio
from crawler_api.surfaces import google_trends

async def test():
    items, cursor = await google_trends.collect({'geo': 'US'}, None, 10)
    print(f'✅ Collected {len(items)} items')
    if items:
        print(f'First trend: {items[0][\"title\"]}')

asyncio.run(test())
"
```

### Run Surface Worker

```bash
# Start the surface worker (collects from all active surfaces)
python src/manage.py surface_worker
```

### Check Collection Status

```python
from crawler_admin.models import CrawlRun

# Get recent runs
recent_runs = CrawlRun.objects.order_by('-started_at')[:10]

for run in recent_runs:
    print(f"{run.surface.key}: {run.items_collected} items, {run.status}")
```

---

## 📋 Bucket Distribution

Ensure no bucket exceeds 40% of total items (product requirement):

| Bucket | Collectors | Expected % |
|--------|------------|------------|
| `region_local` | 6 news sources | ~25% |
| `category_entertainment` | 3 (Billboard, Variety, YouTube) | ~15% |
| `category_gaming` | 2 (IGN, Polygon) | ~10% |
| `category_tech` | 1 (Hacker News) | ~5% |
| `hot_now` | 2 (Reddit, Google Trends) | ~20% |
| `rising` | 1 (Twitter - pending) | ~5% |

---

## 🔍 Troubleshooting

### Common Issues

**Issue**: `ModuleNotFoundError: No module named 'feedparser'`
```bash
# Solution: Install dependencies
pip install -r requirements.txt
```

**Issue**: `pytrends library not installed`
```bash
# Solution: Install pytrends
pip install pytrends>=4.9.0
```

**Issue**: `YOUTUBE_API_KEY environment variable not set`
```bash
# Solution: Add to .env file
echo "YOUTUBE_API_KEY=your_key_here" >> .env
```

**Issue**: RSS feed parsing errors
```bash
# Check feed is accessible
curl -I http://feeds.bbci.co.uk/news/rss.xml

# Test with feedparser
python -c "import feedparser; print(feedparser.parse('http://feeds.bbci.co.uk/news/rss.xml').entries[0])"
```

**Issue**: Rate limiting from Google Trends
```bash
# Solution: Increase poll_interval_seconds
# Google Trends has aggressive rate limiting
# Recommended: 10800 seconds (3 hours) minimum
```

---

## 📊 Monitoring

### Key Metrics to Monitor

1. **Collection Success Rate**
   ```python
   from django.db.models import Count, Q
   from crawler_admin.models import CrawlRun

   total = CrawlRun.objects.count()
   success = CrawlRun.objects.filter(status='success').count()
   print(f"Success rate: {success/total*100:.1f}%")
   ```

2. **Items Per Collector**
   ```python
   from django.db.models import Count
   from crawler_admin.models import TrendSurface, TrendItem

   for surface in TrendSurface.objects.annotate(item_count=Count('items')):
       print(f"{surface.key}: {surface.item_count} items")
   ```

3. **Bucket Distribution**
   ```python
   from django.db.models import Count
   from crawler_admin.models import TrendItem

   buckets = TrendItem.objects.values('surface__bucket').annotate(count=Count('id'))
   total = TrendItem.objects.count()

   for bucket in buckets:
       pct = bucket['count'] / total * 100
       print(f"{bucket['surface__bucket']}: {pct:.1f}%")
   ```

---

## 🚀 Next Steps

1. **Acquire Twitter API Key** (if needed)
   - Go to https://developer.twitter.com/en/portal/dashboard
   - Create project and app
   - Generate Bearer Token
   - Add to `.env`: `TWITTER_BEARER_TOKEN=your_token`
   - Activate Twitter collector in Django Admin

2. **Monitor for 48 Hours**
   - Check `CrawlRun` logs for errors
   - Verify bucket distribution stays under 40%
   - Monitor API quota usage (YouTube, Twitter)

3. **Optimize Collection**
   - Adjust `poll_interval_seconds` based on content freshness
   - Tune `max_items_per_run` based on data quality
   - Enable/disable collectors based on relevance

4. **Scale Up** (if needed)
   - Migrate from SQLite to PostgreSQL for better performance
   - Add more regions (JP, EU, etc.)
   - Implement caching for expensive API calls

---

## 📚 Additional Resources

- **Source Project**: `/home/tnnd/data/code/trend`
- **RSS Feed Validator**: https://validator.w3.org/feed/
- **YouTube API Docs**: https://developers.google.com/youtube/v3
- **Twitter API Docs**: https://developer.twitter.com/en/docs/twitter-api
- **Google Trends API (pytrends)**: https://github.com/GeneralMills/pytrends

---

**Migration completed**: 2024-XX-XX
**Migrated by**: Claude Code Assistant
**Total collectors**: 15 (14 active, 1 pending API key)