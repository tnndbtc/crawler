# Final Requirements Summary - Ready for Implementation

**Version**: 2.0 Final
**Date**: 2024-01-15
**Status**: Complete requirements from /tmp/t3 + /tmp/t4

---

## 🎯 Core Requirements

### 1. Culture-Flexible Crawler Framework
- ✅ Region-first design (not platform-first)
- ✅ Configuration-driven (Django Admin)
- ✅ Plugin architecture for collectors
- ✅ 4 surface types: ranking, sampler, search, news
- ✅ Bucket system for signal categorization

### 2. Django Admin Service
- ✅ Manage Regions, TrendSurfaces, TranslationSettings
- ✅ Add new sources without code changes
- ✅ Enable/disable surfaces with one click
- ✅ View collected items and translation status
- ✅ Requeue failed translations

### 3. Translation Enrichment Layer
- ✅ DeepL by default (DEEPL_API_KEY)
- ✅ OpenAI fallback (OPENAI_API_KEY)
- ✅ Switchable via Django Admin
- ✅ **Canonical language: en-US** for trend analysis
- ✅ Force canonical translation for all non-English items

### 4. Simple Workers (No Celery/RabbitMQ)
- ✅ Surface worker: Poll and collect
- ✅ Translation worker: Enrich with translations
- ✅ Database-driven scheduling
- ✅ Fault-tolerant (errors don't crash worker)

### 5. FastAPI Read APIs
- ✅ /health, /api/v1/status
- ✅ /api/v1/regions, /api/v1/surfaces
- ✅ /api/v1/trends with canonical_text fields

---

## 📊 Complete Data Model

### 1. Region
```python
class Region(models.Model):
    key = models.CharField(max_length=10, unique=True)  # "us", "jp"
    name = models.CharField(max_length=100)
    default_locale = models.CharField(max_length=10)  # "en-US", "ja-JP"
    enabled = models.BooleanField(default=True)
```

### 2. TrendSurface
```python
class TrendSurface(models.Model):
    region = models.ForeignKey(Region)
    key = models.CharField(max_length=100)
    platform = models.CharField(max_length=50)
    surface_type = models.CharField(choices=[
        ('ranking', 'Ranking'),
        ('sampler', 'Feed Sampler'),
        ('search', 'Search Trends'),
        ('news', 'News'),
    ])
    bucket = models.CharField(max_length=50, choices=[
        ('hot_now', 'Hot Now'),
        ('rising', 'Rising'),
        ('category_tech', 'Tech'),
        ('category_sports', 'Sports'),
        ('category_entertainment', 'Entertainment'),
        ('evergreen', 'Evergreen'),
        ('local', 'Local'),
    ])
    entrypoint = models.CharField(max_length=200)
    enabled = models.BooleanField(default=True)
    poll_interval_seconds = models.IntegerField(default=3600)
    max_items_per_run = models.IntegerField(default=200)
    config_json = models.JSONField(default=dict)
    last_cursor = models.TextField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_error = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = [['region', 'key']]
```

### 3. TrendItem
```python
class TrendItem(models.Model):
    region = models.ForeignKey(Region)
    surface = models.ForeignKey(TrendSurface)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    canonical_hash = models.CharField(max_length=64, db_index=True)

    # Content
    title_original = models.TextField()
    description_original = models.TextField(null=True, blank=True)
    original_locale = models.CharField(max_length=10)
    url = models.TextField()

    # Ranking & Engagement
    rank_position = models.IntegerField(null=True, blank=True)
    engagement_signals = models.JSONField(default=dict)
    bucket = models.CharField(max_length=50)

    # Timestamps
    published_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(auto_now_add=True)

    # Raw data
    raw_payload = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=['region', 'collected_at']),
            models.Index(fields=['canonical_hash']),
            models.Index(fields=['bucket', 'collected_at']),
            models.Index(fields=['rank_position']),
        ]
```

### 4. TrendItemTranslation
```python
class TrendItemTranslation(models.Model):
    item = models.ForeignKey(TrendItem, related_name='translations')
    locale = models.CharField(max_length=10)
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    status = models.CharField(choices=[
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ])
    provider = models.CharField(max_length=20)  # "deepl" | "openai"
    error_message = models.TextField(null=True, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [['item', 'locale']]
```

### 5. TranslationSettings (Singleton)
```python
class TranslationSettings(models.Model):
    translation_enabled = models.BooleanField(default=True)
    default_provider = models.CharField(
        max_length=20,
        choices=[('deepl', 'DeepL'), ('openai', 'OpenAI')],
        default='deepl'
    )
    canonical_locale_for_analysis = models.CharField(
        max_length=10,
        default='en-US'  # LOCKED to en-US
    )
    force_canonical_translation = models.BooleanField(
        default=True  # Always create en-US translation
    )
    enabled_locales = models.JSONField(default=list)
    max_chars_per_request = models.IntegerField(default=5000)
```

---

## 🔌 Surface Collector Interface

```python
# All collectors must implement this

async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> tuple[list[dict], Optional[str]]:
    """
    Returns:
        (items, next_cursor)

    Each item dict must have:
    {
        "external_id": "...",  # Optional
        "title": "...",        # Required
        "description": "...",  # Optional
        "url": "...",          # Required
        "published_at": "...", # Optional ISO8601
        "locale": "...",       # Required (one of supported)
        "rank_position": 1,    # Optional (position in ranking)
        "engagement_signals": { # Optional
            "upvotes": 123,
            "views": 456,
        },
        "raw_payload": {...}   # Required (original data)
    }
    """
```

---

## ⚙️ Worker Logic

### Surface Worker

```python
while True:
    due_surfaces = TrendSurface.objects.filter(
        enabled=True,
        next_run_at__lte=now()
    )

    for surface in due_surfaces:
        try:
            # Load collector
            collector = get_collector(surface.entrypoint)

            # Collect items
            items, next_cursor = await collector(
                config=surface.config_json,
                cursor=surface.last_cursor,
                limit=surface.max_items_per_run
            )

            # Store items
            for item_dict in items:
                canonical_hash = compute_hash(
                    item_dict['title'],
                    item_dict['url']
                )

                # Dedupe
                if TrendItem.objects.filter(
                    canonical_hash=canonical_hash
                ).exists():
                    continue

                # Create item
                TrendItem.objects.create(
                    region=surface.region,
                    surface=surface,
                    external_id=item_dict.get('external_id'),
                    canonical_hash=canonical_hash,
                    title_original=item_dict['title'],
                    description_original=item_dict.get('description'),
                    original_locale=item_dict['locale'],
                    url=item_dict['url'],
                    published_at=item_dict.get('published_at'),
                    rank_position=item_dict.get('rank_position'),
                    engagement_signals=item_dict.get('engagement_signals', {}),
                    bucket=surface.bucket,  # Copy from surface
                    raw_payload=item_dict['raw_payload'],
                )

            # Update surface
            surface.last_cursor = next_cursor
            surface.last_run_at = now()
            surface.next_run_at = now() + timedelta(
                seconds=surface.poll_interval_seconds
            )
            surface.last_run_error = None
            surface.save()

        except Exception as e:
            surface.last_run_error = str(e)
            surface.next_run_at = now() + timedelta(seconds=300)
            surface.save()

    await asyncio.sleep(60)
```

### Translation Worker

```python
while True:
    settings = TranslationSettings.objects.first()

    if not settings or not settings.translation_enabled:
        await asyncio.sleep(60)
        continue

    # Find items needing canonical translation
    items = TrendItem.objects.exclude(
        original_locale=settings.canonical_locale_for_analysis
    ).filter(
        translations__locale=settings.canonical_locale_for_analysis,
        translations__status__in=['pending', 'failed']
    )[:100]

    for item in items:
        # PRIORITY 1: Canonical (en-US) translation
        if settings.force_canonical_translation:
            translation = item.translations.get(
                locale=settings.canonical_locale_for_analysis
            )

            if translation.status in ['pending', 'failed']:
                await process_translation(translation, settings)

    # PRIORITY 2: Additional locales
    for locale in settings.enabled_locales:
        # Similar logic for other locales
        ...

    await asyncio.sleep(30)


async def process_translation(translation, settings):
    try:
        translation.status = 'running'
        translation.save()

        provider = get_provider(settings.default_provider)

        translated_title = await provider.translate(
            text=translation.item.title_original,
            source_locale=translation.item.original_locale,
            target_locale=translation.locale
        )

        translation.title = translated_title
        translation.status = 'complete'
        translation.translated_at = now()
        translation.save()

    except Exception as e:
        translation.status = 'failed'
        translation.error_message = str(e)
        translation.save()
```

---

## 🌐 API Endpoints

### 1. Health Check
```
GET /health

Response:
{
  "status": "healthy",
  "timestamp": "2024-01-15T12:00:00Z"
}
```

### 2. System Status
```
GET /api/v1/status

Response:
{
  "status": "operational",
  "stats": {
    "regions": 5,
    "surfaces": 15,
    "total_items": 10000,
    "items_last_24h": 500
  },
  "translation": {
    "enabled": true,
    "provider": "deepl",
    "canonical_locale": "en-US",
    "pending_translations": 23
  }
}
```

### 3. List Regions
```
GET /api/v1/regions

Response:
{
  "regions": [
    {
      "key": "us",
      "name": "United States",
      "default_locale": "en-US",
      "surfaces_count": 5
    }
  ]
}
```

### 4. List Surfaces
```
GET /api/v1/surfaces?region=us

Response:
{
  "surfaces": [
    {
      "key": "reddit_hot",
      "region": "us",
      "surface_type": "ranking",
      "platform": "reddit",
      "bucket": "hot_now",
      "enabled": true,
      "last_run_at": "2024-01-15T11:00:00Z"
    }
  ]
}
```

### 5. Get Trends
```
GET /api/v1/trends?region=us&bucket=hot_now&locales=en-US

Response:
{
  "trends": [
    {
      "id": 123,
      "region": "us",
      "surface": "reddit_hot",
      "bucket": "hot_now",
      "platform": "reddit",
      "url": "https://...",
      "original_locale": "en-US",

      // CANONICAL TEXT (for analysis)
      "canonical_title": "Trending Topic",
      "canonical_description": "Description",

      // Ranking data
      "rank_position": 3,
      "engagement_signals": {
        "upvotes": 5432,
        "comments": 234
      },

      // Translations
      "translations": {
        "en-US": {
          "title": "Trending Topic",
          "description": "Description"
        }
      },

      "collected_at": "2024-01-15T11:00:00Z"
    }
  ]
}
```

---

## 🎨 Django Admin Configuration

### TranslationSettings Admin
- Master switch: translation_enabled
- Provider dropdown: deepl | openai
- Canonical locale: en-US (locked)
- Force canonical: ✓ (always translate to English)
- Additional locales: JSON list editor

### TrendSurface Admin
- Add/Edit surface form
- Filters: region, platform, surface_type, bucket, enabled
- Actions: Enable/Disable selected surfaces
- Status indicators: ✅ Running, ⚠️ Error, 🔴 Disabled

### TrendItem Admin (Read-only)
- List filters:
  - Region, platform, bucket
  - Original locale
  - Canonical translation status (✅ Translated, ❌ Missing, ⏳ Pending)
- Actions:
  - "Requeue canonical translation" (sets en-US status to pending)

---

## 🔧 Environment Variables

```bash
# .env
DEEPL_API_KEY=your_deepl_key_here
OPENAI_API_KEY=your_openai_key_here

# Django
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=False

# Database
SQLITE_PATH=/path/to/db.sqlite3

# FastAPI
API_HOST=0.0.0.0
API_PORT=8000
```

---

## 📝 Stub Collectors (for MVP)

Implement these 3 stub collectors with fake data:

1. **crawler_surfaces.reddit_hot:collect**
   - Returns 10 sample items
   - Locale: en-US
   - Bucket: hot_now
   - rank_position: 1-10
   - engagement_signals: {upvotes, comments}

2. **crawler_surfaces.youtube_trending:collect**
   - Returns 10 sample items
   - Locale: en-US
   - Bucket: hot_now
   - rank_position: 1-10
   - engagement_signals: {views, likes}

3. **crawler_surfaces.yahoo_jp_ranking:collect**
   - Returns 10 sample items in Japanese
   - Locale: ja-JP
   - Bucket: hot_now
   - rank_position: 1-10
   - engagement_signals: {access_count}

---

## ✅ Success Criteria

The implementation is complete when:

1. ✅ Django Admin can add/edit regions and surfaces
2. ✅ Surface worker polls and collects items
3. ✅ Translation worker creates en-US translations
4. ✅ FastAPI returns trends with canonical_title
5. ✅ All migrations run successfully
6. ✅ Worker scripts start without errors
7. ✅ Can add new surface via Admin UI without code changes
8. ✅ Can switch translation provider via Admin UI
9. ✅ Japanese items automatically translated to English

---

## 📂 File Structure

```
crawler/
├── src/
│   ├── crawler_admin/              # Django app
│   │   ├── __init__.py
│   │   ├── models.py               # ALL data models
│   │   ├── admin.py                # Django Admin configuration
│   │   ├── apps.py
│   │   ├── migrations/
│   │   │   └── 0001_initial.py
│   │   └── settings.py
│   │
│   ├── crawler_api/                # FastAPI app
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI routes
│   │   ├── surfaces/
│   │   │   ├── __init__.py
│   │   │   ├── interfaces.py       # TrendSurfaceCollector protocol
│   │   │   ├── registry.py         # get_collector()
│   │   │   ├── reddit_hot.py       # Stub collector
│   │   │   ├── youtube_trending.py # Stub collector
│   │   │   └── yahoo_jp_ranking.py # Stub collector
│   │   │
│   │   ├── translation/
│   │   │   ├── __init__.py
│   │   │   ├── providers.py        # DeepL, OpenAI providers
│   │   │   └── worker.py           # Translation worker
│   │   │
│   │   └── workers/
│   │       ├── __init__.py
│   │       └── surface_worker.py   # Surface worker
│   │
│   └── shared/
│       ├── __init__.py
│       └── utils.py                # compute_hash(), etc.
│
├── scripts/
│   ├── run_surface_worker.sh
│   └── run_translation_worker.sh
│
├── docs/
│   ├── INDEX.md
│   ├── README.md
│   ├── REQUIREMENTS-FINAL.md       # This file
│   ├── DESIGN-*.md
│   └── API.md
│
├── requirements.txt
├── .env.example
├── manage.py
└── README.md
```

---

**READY FOR IMPLEMENTATION!** 🚀

All requirements are documented. Proceed to code implementation.
