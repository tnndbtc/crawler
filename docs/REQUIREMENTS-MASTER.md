# Master Requirements - Culture-Flexible Crawler

**Version**: FINAL
**Date**: 2024-01-15
**Consolidates**: /tmp/t3 + /tmp/t4 + /tmp/t7 + /tmp/t8

---

## Executive Summary

Build a **self-verifiable, culture-flexible trend crawler** with:

✅ **Region-first design** - Not platform-centric
✅ **Django Admin** - Configuration without code changes
✅ **Simple polling workers** - No Celery/RabbitMQ
✅ **Canonical language (en-US)** - For cross-regional analysis
✅ **Complete observability** - Every execution tracked
✅ **DRY_RUN mode** - Safe testing
✅ **Health monitoring** - Know what's working/failing

---

## Complete Data Model

### 1. Region
```python
key = CharField(unique=True)           # "us", "jp", "kr"
name = CharField()                      # "United States"
default_locale = CharField()            # "en-US"
enabled = BooleanField(default=True)
```

### 2. TrendSurface
```python
region = ForeignKey(Region)
key = CharField()                       # Unique per region
platform = CharField()                  # "reddit", "youtube"
surface_type = CharField(choices=[     # ranking|sampler|search|news
    ('ranking', 'Ranking'),
    ('sampler', 'Feed Sampler'),
    ('search', 'Search Trends'),
    ('news', 'News'),
])
bucket = CharField(choices=[           # PRODUCT CONSTRAINT: Diversity buckets
    ('hot_now', 'Hot Now'),            # Major trending content
    ('rising', 'Rising'),              # New gaining traction
    ('category_tech', 'Tech'),
    ('category_sports', 'Sports'),
    ('category_entertainment', 'Entertainment'),
    ('category_finance', 'Finance'),
    ('category_gaming', 'Gaming'),
    ('category_lifestyle', 'Lifestyle'),
    ('category_science', 'Science'),
    ('category_politics', 'Politics'),
    ('region_local', 'Region Local'),  # Local mainstream portals
    ('evergreen', 'Evergreen'),        # Slower high-quality sources
])
bucket_weight = FloatField(default=1.0)  # For bucket balancing (NEW from /tmp/t9)
entrypoint = CharField()                # "crawler_surfaces.reddit_hot:collect"
enabled = BooleanField(default=True)
poll_interval_seconds = IntegerField(default=3600)
max_items_per_run = IntegerField(default=200)
config_json = JSONField(default=dict)
last_cursor = TextField(null=True)

# Health fields (from /tmp/t8)
last_run_at = DateTimeField(null=True)
last_success_at = DateTimeField(null=True)
last_error = TextField(null=True)

# Legacy scheduling fields
next_run_at = DateTimeField(null=True)
last_run_error = TextField(null=True)   # Deprecated, use last_error

unique_together = [['region', 'key']]
```

### 3. TrendItem
```python
region = ForeignKey(Region)
surface = ForeignKey(TrendSurface)
external_id = CharField(null=True)
canonical_hash = CharField(db_index=True)

# Content
title_original = TextField()
description_original = TextField(null=True)
original_locale = CharField()
url = TextField()

# Ranking & Engagement
rank_position = IntegerField(null=True)
engagement_signals = JSONField(default=dict)
bucket = CharField()

# Timestamps
published_at = DateTimeField(null=True)
collected_at = DateTimeField(auto_now_add=True)

# Raw data
raw_payload = JSONField(default=dict)
```

### 4. TrendItemTranslation
```python
item = ForeignKey(TrendItem, related_name='translations')
locale = CharField()
title = TextField()
description = TextField(null=True)
status = CharField(choices=['pending', 'running', 'complete', 'failed'])
provider = CharField()                  # "deepl" | "openai"
error_message = TextField(null=True)
translated_at = DateTimeField(null=True)

unique_together = [['item', 'locale']]
```

### 5. TranslationSettings (Singleton)
```python
translation_enabled = BooleanField(default=True)
default_provider = CharField(default='deepl')
canonical_locale_for_analysis = CharField(default='en-US')  # LOCKED
force_canonical_translation = BooleanField(default=True)
enabled_locales = JSONField(default=list)
max_chars_per_request = IntegerField(default=5000)
```

### 6. CrawlRun (Observability)
```python
surface = ForeignKey(TrendSurface)
started_at = DateTimeField()
finished_at = DateTimeField(null=True)
status = CharField(choices=['success', 'failed'])

# Metrics
fetched_count = IntegerField(default=0)
stored_new_count = IntegerField(default=0)
deduped_count = IntegerField(default=0)

# Diagnostics
error_message = TextField(null=True)
duration_ms = IntegerField(null=True)
created_at = DateTimeField(auto_now_add=True)
```

---

## Product Constraints (from /tmp/t9)

**This crawler is a candidate generator for an addictive feed, NOT a generic scraper.**

### Key Principles
1. **Diversity > Volume** - Balanced candidate pool, not maximum items
2. **Bucket enforcement** - No bucket > 40% of items per run
3. **Translation is async** - NEVER wait for translation during ingestion
4. **rank_position priority** - More important than timestamps
5. **Candidate generation only** - Feed ranking comes later

See [DESIGN-PRODUCT-PHILOSOPHY.md](./DESIGN-PRODUCT-PHILOSOPHY.md) for details.

---

## Surface Worker Logic

```python
import os
import time
import asyncio
from collections import defaultdict

DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'
MAX_RUN_SECONDS = int(os.getenv('MAX_RUN_SECONDS', '60'))
BUCKET_CAP_PERCENT = 40  # No bucket > 40% of total items (from /tmp/t9)

async def execute_surface(surface):
    """Execute single surface with full observability."""
    # Create CrawlRun
    run = CrawlRun.objects.create(
        surface=surface,
        started_at=now()
    )

    # Update surface health
    surface.last_run_at = now()
    surface.save()

    try:
        start_time = time.time()

        # Load collector
        collector = get_collector(surface.entrypoint)

        # Collect with timeout
        items, next_cursor = await asyncio.wait_for(
            collector(
                config=surface.config_json,
                cursor=surface.last_cursor,
                limit=surface.max_items_per_run
            ),
            timeout=MAX_RUN_SECONDS
        )

        fetched_count = len(items)
        stored_new_count = 0
        deduped_count = 0

        # Process items
        for item_dict in items:
            canonical_hash = compute_hash(
                item_dict['title'],
                item_dict['url']
            )

            # Check duplicate
            if TrendItem.objects.filter(canonical_hash=canonical_hash).exists():
                deduped_count += 1
                continue

            # Store (unless DRY_RUN)
            if not DRY_RUN:
                TrendItem.objects.create(
                    region=surface.region,
                    surface=surface,
                    canonical_hash=canonical_hash,
                    title_original=item_dict['title'],
                    description_original=item_dict.get('description'),
                    original_locale=item_dict['locale'],
                    url=item_dict['url'],
                    published_at=item_dict.get('published_at'),
                    rank_position=item_dict.get('rank_position'),
                    engagement_signals=item_dict.get('engagement_signals', {}),
                    bucket=surface.bucket,
                    raw_payload=item_dict['raw_payload'],
                )

            stored_new_count += 1

        # Success
        duration_ms = int((time.time() - start_time) * 1000)
        run.status = 'success'
        run.fetched_count = fetched_count
        run.stored_new_count = stored_new_count
        run.deduped_count = deduped_count
        run.duration_ms = duration_ms
        run.finished_at = now()
        run.save()

        # Update surface health
        surface.last_success_at = now()
        surface.last_error = None
        surface.last_cursor = next_cursor
        surface.next_run_at = now() + timedelta(seconds=surface.poll_interval_seconds)
        surface.save()

        # Log
        dry_run_prefix = "[DRY_RUN] " if DRY_RUN else ""
        logger.info(
            f"{dry_run_prefix}CrawlRun #{run.id}: {surface.key} - "
            f"fetched={fetched_count}, new={stored_new_count}, "
            f"deduped={deduped_count}, duration={duration_ms}ms"
        )

    except asyncio.TimeoutError:
        # Timeout
        run.status = 'failed'
        run.error_message = f'Timeout after {MAX_RUN_SECONDS}s'
        run.finished_at = now()
        run.save()

        surface.last_error = f'Timeout after {MAX_RUN_SECONDS}s'
        surface.save()

    except Exception as e:
        # Other failure
        run.status = 'failed'
        run.error_message = str(e)
        run.finished_at = now()
        run.save()

        surface.last_error = str(e)
        surface.next_run_at = now() + timedelta(seconds=300)
        surface.save()

        logger.error(f"CrawlRun #{run.id} failed: {e}")


# NOTE: Bucket balancing logic (from /tmp/t9)
# In production, worker should enforce: no bucket > 40% of collected items
# Implementation:
# 1. Track bucket_counts = defaultdict(int)
# 2. Calculate max_per_bucket = total_items * 0.40
# 3. Skip surfaces when bucket_counts[bucket] >= max_per_bucket
# 4. Log bucket distribution at end of run
#
# This ensures diverse candidate pool for feed ranking
```

---

## API Endpoints (Complete List)

### Basic Endpoints
1. `GET /health` - Basic health check
2. `GET /api/v1/regions` - List regions
3. `GET /api/v1/surfaces` - List surfaces
4. `GET /api/v1/trends?region=xx&bucket=yy` - Get trends with canonical_title

### Observability Endpoints (from /tmp/t8)
5. **`GET /api/v1/health/crawl`** - Per-surface crawl status

```json
[
  {
    "surface_key": "reddit_hot",
    "last_status": "success",
    "last_finished_at": "2024-01-15T10:00:03Z",
    "duration_ms": 3247
  }
]
```

6. **`GET /api/v1/health/translation`** - Translation queue status

```json
{
  "missing_canonical_en_count": 23,
  "pending_count": 15,
  "failed_count": 5
}
```

7. **`GET /api/v1/surfaces/{surface_id}/recent?limit=20`** - Recent items sanity check

```json
{
  "surface_key": "reddit_hot",
  "items": [
    {
      "id": 123,
      "title_original": "Original title",
      "canonical_title": "English translation",
      "bucket": "hot_now",
      "collected_at": "2024-01-15T10:00:00Z"
    }
  ]
}
```

---

## Environment Variables

```bash
# Django
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True

# Database
# DATABASE_URL=sqlite:///db.sqlite3

# Translation
DEEPL_API_KEY=your-deepl-key
OPENAI_API_KEY=your-openai-key

# Observability & Safety (from /tmp/t8)
DRY_RUN=false                    # true = test mode, no DB writes
MAX_RUN_SECONDS=60               # Timeout per surface execution

# Workers
SURFACE_WORKER_POLL_INTERVAL=60
TRANSLATION_WORKER_POLL_INTERVAL=30

# Logging
LOG_LEVEL=INFO
```

---

## Django Admin Requirements

### CrawlRun Admin
- List: surface, status (✅/❌), fetched_count, stored_new_count, deduped_count, duration_ms, created_at
- Filters: status, surface, region, date
- Read-only (all fields)

### TrendItem Admin
- **Custom Filter**: "Missing canonical en-US translation"
- Filters: region, platform, bucket, canonical_missing, date
- Display: title, region, platform, bucket, canonical_status (✅/❌)

### TrendSurface Admin
- Display health indicators
- Bulk actions: enable/disable
- Show: last_run_at, last_success_at, last_error

---

## Collector Interface

```python
async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> tuple[list[dict], Optional[str]]:
    """
    Returns: (items, next_cursor)

    Each item must have:
    {
        "external_id": "...",         # Optional
        "title": "...",               # Required
        "description": "...",         # Optional
        "url": "...",                 # Required
        "published_at": "ISO8601",    # Optional (less important than rank)
        "locale": "en-US",            # Required

        # CRITICAL (from /tmp/t9): rank_position is MORE important than timestamps
        "rank_position": 1,           # IMPORTANT: Editorial signal (1=top)

        # CRITICAL: Rich metadata for feed ranking
        "engagement_signals": {       # IMPORTANT: Upvotes, views, likes, etc.
            "upvotes": 1234,
            "comments": 56,
            "views": 9999
        },

        # CRITICAL: Never throw away data
        "raw_payload": {...}          # Required: Complete platform response
    }

    Product rule (from /tmp/t9):
    - rank_position > published_at in importance
    - Always include raw_payload (full platform data)
    - Engagement signals help future ranking
    """
```

---

## Success Criteria

### Technical
✅ All 6 models implemented with migrations
✅ Django Admin configured with custom filters
✅ Surface worker creates CrawlRun for every execution
✅ DRY_RUN mode works (no DB writes but CrawlRun created)
✅ MAX_RUN_SECONDS timeout prevents hangs
✅ Health endpoints return accurate data
✅ Can filter TrendItems by missing canonical translation
✅ Canonical (en-US) translation created automatically
✅ Recent items endpoint shows collector output

### Product (from /tmp/t9)
✅ No bucket > 40% of collected items (diversity enforcement)
✅ All buckets represented (hot_now, rising, categories, region_local, evergreen)
✅ rank_position captured when available
✅ engagement_signals captured (upvotes, views, likes, etc.)
✅ raw_payload always stored (never throw away data)
✅ Translation async (NEVER blocks ingestion)
✅ Balanced candidate pool (quality > volume)

---

## Testing Checklist

### Technical Tests
- [ ] Create region via Django Admin
- [ ] Create surface via Django Admin with bucket assignment
- [ ] Run surface worker → CrawlRun created
- [ ] Check CrawlRun metrics (fetched/stored/deduped)
- [ ] Enable DRY_RUN → no TrendItems created
- [ ] Check timeout protection (slow collector)
- [ ] Call /api/v1/health/crawl → see status
- [ ] Call /api/v1/health/translation → see queue
- [ ] Call /api/v1/surfaces/{id}/recent → see items
- [ ] Filter TrendItems by missing canonical
- [ ] Run translation worker → en-US created
- [ ] Call /api/v1/trends → canonical_title present

### Product Tests (from /tmp/t9)
- [ ] Create surfaces in multiple buckets (hot_now, rising, category_tech, etc.)
- [ ] Collect 500 items → verify no bucket > 40% (200 items)
- [ ] Check TrendItems have rank_position when available
- [ ] Check TrendItems have engagement_signals populated
- [ ] Check raw_payload is complete (not truncated)
- [ ] Verify translation doesn't block collection (check timing logs)
- [ ] Verify bucket distribution in CrawlRun logs

---

## Key Design Principles

### Technical Principles
1. **Minimal invasive changes** - Extend, don't redesign
2. **SQLite compatible** - No PostgreSQL-specific features
3. **Worker never crashes** - Catch all exceptions, continue loop
4. **Every execution tracked** - CrawlRun for verifiability
5. **Safe testing** - DRY_RUN mode for new collectors
6. **Observable** - Health endpoints, no external API calls
7. **Canonical language** - Always en-US for analysis

### Product Principles (from /tmp/t9)
1. **Diversity > Volume** - Balanced candidates, not maximum items
2. **Bucket enforcement** - No bucket > 40% (prevents dominance)
3. **Translation async** - NEVER blocks ingestion
4. **rank_position priority** - Editorial signal > timestamps
5. **Rich metadata** - engagement_signals, raw_payload always captured
6. **Candidate generation only** - Feed ranking happens later
7. **This is NOT a scraper** - It's a candidate generator for addictive feeds

**See Also**: [DESIGN-PRODUCT-PHILOSOPHY.md](./DESIGN-PRODUCT-PHILOSOPHY.md)

---

**Version**: FINAL
**Date**: 2024-01-15
**Status**: Ready for implementation
**Requirements from**: /tmp/t3 + /tmp/t4 + /tmp/t7 + /tmp/t8 + /tmp/t9 ✅
