# Complete Requirements - Culture-Flexible Crawler with Verifiability

**Version**: 3.0 Final
**Date**: 2024-01-15
**Integrates**: /tmp/t3 + /tmp/t4 + /tmp/t7

---

## Quick Summary

Build a **culture-flexible trend crawler** that:
1. ✅ Collects trends from multiple regions/platforms
2. ✅ Translates to English (canonical) for analysis
3. ✅ Provides Django Admin for configuration
4. ✅ Uses simple polling workers (no Celery)
5. ✅ **Tracks every execution for verifiability**
6. ✅ **Provides health monitoring**
7. ✅ **Supports DRY_RUN mode for testing**

---

## Data Models (Complete)

### 1. Region
```python
class Region(models.Model):
    key = CharField(max_length=10, unique=True)  # "us", "jp", "kr"
    name = CharField(max_length=100)
    default_locale = CharField(max_length=10)    # "en-US", "ja-JP"
    enabled = BooleanField(default=True)
```

### 2. TrendSurface
```python
class TrendSurface(models.Model):
    region = ForeignKey(Region)
    key = CharField(max_length=100)              # Unique per region
    platform = CharField(max_length=50)
    surface_type = CharField(choices=[
        ('ranking', 'Ranking'),
        ('sampler', 'Feed Sampler'),
        ('search', 'Search Trends'),
        ('news', 'News'),
    ])
    bucket = CharField(max_length=50, choices=[
        ('hot_now', 'Hot Now'),
        ('rising', 'Rising'),
        ('category_tech', 'Tech'),
        ('evergreen', 'Evergreen'),
        ('local', 'Local'),
    ])
    entrypoint = CharField(max_length=200)       # Python import path
    enabled = BooleanField(default=True)
    poll_interval_seconds = IntegerField(default=3600)
    max_items_per_run = IntegerField(default=200)
    config_json = JSONField(default=dict)
    last_cursor = TextField(null=True, blank=True)
    last_run_at = DateTimeField(null=True, blank=True)
    next_run_at = DateTimeField(null=True, blank=True)
    last_run_error = TextField(null=True, blank=True)

    class Meta:
        unique_together = [['region', 'key']]
```

### 3. TrendItem
```python
class TrendItem(models.Model):
    region = ForeignKey(Region)
    surface = ForeignKey(TrendSurface)
    external_id = CharField(max_length=255, null=True, blank=True)
    canonical_hash = CharField(max_length=64, db_index=True)

    # Content
    title_original = TextField()
    description_original = TextField(null=True, blank=True)
    original_locale = CharField(max_length=10)
    url = TextField()

    # Ranking & Engagement
    rank_position = IntegerField(null=True, blank=True)
    engagement_signals = JSONField(default=dict)
    bucket = CharField(max_length=50)

    # Timestamps
    published_at = DateTimeField(null=True, blank=True)
    collected_at = DateTimeField(auto_now_add=True)

    # Raw data
    raw_payload = JSONField(default=dict)

    class Meta:
        indexes = [
            Index(fields=['region', 'collected_at']),
            Index(fields=['canonical_hash']),
            Index(fields=['bucket', 'collected_at']),
            Index(fields=['rank_position']),
        ]
```

### 4. TrendItemTranslation
```python
class TrendItemTranslation(models.Model):
    item = ForeignKey(TrendItem, related_name='translations')
    locale = CharField(max_length=10)
    title = TextField()
    description = TextField(null=True, blank=True)
    status = CharField(choices=[
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ])
    provider = CharField(max_length=20)  # "deepl" | "openai"
    error_message = TextField(null=True, blank=True)
    translated_at = DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [['item', 'locale']]
```

### 5. TranslationSettings (Singleton)
```python
class TranslationSettings(models.Model):
    translation_enabled = BooleanField(default=True)
    default_provider = CharField(
        max_length=20,
        choices=[('deepl', 'DeepL'), ('openai', 'OpenAI')],
        default='deepl'
    )
    canonical_locale_for_analysis = CharField(
        max_length=10,
        default='en-US'  # LOCKED to en-US
    )
    force_canonical_translation = BooleanField(
        default=True  # Always create en-US translation
    )
    enabled_locales = JSONField(default=list)
    max_chars_per_request = IntegerField(default=5000)
```

### 6. CrawlRun (NEW - Verifiability)
```python
class CrawlRun(models.Model):
    """Audit record for every surface collection execution."""
    surface = ForeignKey(TrendSurface, on_delete=CASCADE)

    # Timestamps
    started_at = DateTimeField()
    finished_at = DateTimeField(null=True, blank=True)

    # Status
    status = CharField(max_length=20, choices=[
        ('success', 'Success'),
        ('failed', 'Failed'),
    ])

    # Metrics (VERIFIABILITY)
    fetched_count = IntegerField(default=0)        # Items fetched from API
    stored_new_count = IntegerField(default=0)     # New items stored
    deduped_count = IntegerField(default=0)        # Duplicates skipped

    # State tracking
    next_cursor = TextField(null=True, blank=True)
    error_message = TextField(null=True, blank=True)
    duration_ms = IntegerField(null=True, blank=True)

    # Audit
    created_at = DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            Index(fields=['surface', '-created_at']),
            Index(fields=['status', '-created_at']),
        ]
```

---

## Worker Implementation

### Surface Worker (with CrawlRun tracking)

```python
import os
import time
from datetime import datetime, timedelta

DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'

async def run_surface_worker():
    while True:
        now = datetime.utcnow()

        due_surfaces = TrendSurface.objects.filter(
            enabled=True,
            next_run_at__lte=now
        )

        for surface in due_surfaces:
            # Create CrawlRun to track this execution
            run = CrawlRun.objects.create(
                surface=surface,
                started_at=now,
                status='running',
            )

            try:
                start_time = time.time()

                # Load collector
                collector = get_collector(surface.entrypoint)

                # Collect items
                items, next_cursor = await collector(
                    config=surface.config_json,
                    cursor=surface.last_cursor,
                    limit=surface.max_items_per_run
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

                    # Check if duplicate
                    if TrendItem.objects.filter(
                        canonical_hash=canonical_hash
                    ).exists():
                        deduped_count += 1
                        continue

                    # Store new item (unless DRY_RUN)
                    if not DRY_RUN:
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
                            bucket=surface.bucket,
                            raw_payload=item_dict['raw_payload'],
                        )

                    stored_new_count += 1

                # Update CrawlRun with success
                duration_ms = int((time.time() - start_time) * 1000)
                run.status = 'success'
                run.fetched_count = fetched_count
                run.stored_new_count = stored_new_count
                run.deduped_count = deduped_count
                run.next_cursor = next_cursor
                run.duration_ms = duration_ms
                run.finished_at = datetime.utcnow()
                run.save()

                # Update surface
                surface.last_cursor = next_cursor
                surface.last_run_at = now
                surface.next_run_at = now + timedelta(
                    seconds=surface.poll_interval_seconds
                )
                surface.last_run_error = None
                surface.save()

                # Log
                dry_run_marker = "[DRY_RUN] " if DRY_RUN else ""
                logger.info(
                    f"{dry_run_marker}CrawlRun #{run.id}: {surface.key} - "
                    f"fetched={fetched_count}, new={stored_new_count}, "
                    f"deduped={deduped_count}, duration={duration_ms}ms"
                )

                if DRY_RUN and stored_new_count > 0:
                    logger.warning(
                        f"[DRY_RUN] Skipped storing {stored_new_count} items"
                    )

            except Exception as e:
                # Update CrawlRun with failure
                run.status = 'failed'
                run.error_message = str(e)
                run.finished_at = datetime.utcnow()
                run.save()

                # Update surface
                surface.last_run_error = str(e)
                surface.next_run_at = now + timedelta(seconds=300)
                surface.save()

                logger.error(f"CrawlRun #{run.id} failed: {e}")

        await asyncio.sleep(60)
```

---

## API Endpoints (Complete)

### Basic Endpoints

1. `GET /health` - Basic health check
2. `GET /api/v1/status` - System status
3. `GET /api/v1/regions` - List regions
4. `GET /api/v1/surfaces` - List surfaces
5. `GET /api/v1/trends?region=xx&bucket=yy` - Get trends

### Health Endpoints (NEW)

6. **`GET /api/v1/health/crawl`** - Per-surface crawl health

```python
@app.get("/api/v1/health/crawl")
async def crawl_health():
    """
    Monitor surface worker health.

    Returns per-surface metrics WITHOUT calling external APIs.
    """
    surfaces = TrendSurface.objects.filter(enabled=True).select_related('region')

    surface_health = []
    for surface in surfaces:
        # Get last CrawlRun
        last_run = CrawlRun.objects.filter(
            surface=surface
        ).order_by('-created_at').first()

        if not last_run:
            health_status = "warning"
            lag_seconds = None
        else:
            # Calculate lag
            now = datetime.utcnow()
            lag_seconds = int((now - last_run.started_at).total_seconds())

            # Determine health
            max_lag = surface.poll_interval_seconds * 2
            critical_lag = surface.poll_interval_seconds * 4

            if last_run.status == 'failed' or lag_seconds > critical_lag:
                health_status = "critical"
            elif lag_seconds > max_lag:
                health_status = "warning"
            else:
                health_status = "healthy"

        surface_health.append({
            "surface_key": surface.key,
            "region": surface.region.key,
            "platform": surface.platform,
            "last_run_at": last_run.started_at if last_run else None,
            "last_status": last_run.status if last_run else None,
            "last_counts": {
                "fetched": last_run.fetched_count,
                "stored_new": last_run.stored_new_count,
                "deduped": last_run.deduped_count,
            } if last_run else None,
            "lag_seconds": lag_seconds,
            "health": health_status,
        })

    # Summary
    summary = {
        "total_surfaces": len(surface_health),
        "healthy": sum(1 for s in surface_health if s["health"] == "healthy"),
        "warning": sum(1 for s in surface_health if s["health"] == "warning"),
        "critical": sum(1 for s in surface_health if s["health"] == "critical"),
    }

    return {
        "surfaces": surface_health,
        "summary": summary,
    }
```

7. **`GET /api/v1/health/translation`** - Translation queue health

```python
@app.get("/api/v1/health/translation")
async def translation_health():
    """
    Monitor translation worker health.

    Returns translation queue stats WITHOUT calling external APIs.
    """
    settings = TranslationSettings.objects.first()

    # Canonical translation status
    canonical_pending = TrendItemTranslation.objects.filter(
        locale=settings.canonical_locale_for_analysis,
        status='pending'
    ).count()

    canonical_failed = TrendItemTranslation.objects.filter(
        locale=settings.canonical_locale_for_analysis,
        status='failed'
    ).count()

    total_items = TrendItem.objects.count()

    # By locale
    locale_stats = {}
    for locale in settings.enabled_locales:
        pending = TrendItemTranslation.objects.filter(
            locale=locale,
            status='pending'
        ).count()

        failed = TrendItemTranslation.objects.filter(
            locale=locale,
            status='failed'
        ).count()

        complete = TrendItemTranslation.objects.filter(
            locale=locale,
            status='complete'
        ).count()

        locale_stats[locale] = {
            "pending": pending,
            "failed": failed,
            "complete": complete,
            "coverage_percent": (complete / total_items * 100) if total_items > 0 else 0,
        }

    # By provider
    provider_stats = {}
    for provider in ['deepl', 'openai']:
        total = TrendItemTranslation.objects.filter(provider=provider).count()
        failed = TrendItemTranslation.objects.filter(
            provider=provider,
            status='failed'
        ).count()

        provider_stats[provider] = {
            "total": total,
            "failed": failed,
            "success_rate": ((total - failed) / total * 100) if total > 0 else 0,
        }

    return {
        "canonical_translation": {
            "pending_count": canonical_pending,
            "failed_count": canonical_failed,
            "total_items": total_items,
            "coverage_percent": ((total_items - canonical_pending - canonical_failed) / total_items * 100) if total_items > 0 else 0,
        },
        "by_locale": locale_stats,
        "by_provider": provider_stats,
        "health": "healthy" if canonical_pending < 100 and canonical_failed < 10 else "warning",
    }
```

---

## Environment Variables

```bash
# Django
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True

# Translation
DEEPL_API_KEY=your-deepl-key
OPENAI_API_KEY=your-openai-key

# Workers
DRY_RUN=false  # Set to 'true' for testing without storing data

# Logging
LOG_LEVEL=INFO
```

---

## Django Admin Requirements

### 1. CrawlRun Admin
- ✅ List display: surface, region, status, counts, duration, started_at
- ✅ Filters: status, surface__region, surface__platform, started_at
- ✅ Read-only fields (all)
- ✅ Status indicator with emoji (✅/❌)

### 2. TrendItem Admin
- ✅ Custom filter: "Missing canonical en-US translation"
- ✅ Filters: region, platform, bucket, original_locale
- ✅ Canonical status display

### 3. Other Models
- ✅ Region: Standard CRUD
- ✅ TrendSurface: Health indicators, bulk actions
- ✅ TrendItemTranslation: Status tracking
- ✅ TranslationSettings: Singleton with validation

---

## Success Criteria

The system is complete when:

1. ✅ Django Admin can manage all models
2. ✅ Surface worker creates CrawlRun for every execution
3. ✅ DRY_RUN mode works (collects but doesn't store)
4. ✅ Health endpoints return correct data
5. ✅ Canonical translations created automatically
6. ✅ All filters work in Django Admin
7. ✅ Can prove deduplication via CrawlRun metrics
8. ✅ Can monitor worker health via API

---

## Testing Scenarios

### 1. Verify Deduplication
```bash
# Run 1: All new
CrawlRun #1: fetched=50, new=50, deduped=0

# Run 2: All duplicates
CrawlRun #2: fetched=50, new=0, deduped=50 ✅
```

### 2. DRY_RUN Mode
```bash
export DRY_RUN=true
# Run worker - no TrendItems created but CrawlRun exists ✅
```

### 3. Health Monitoring
```bash
curl /api/v1/health/crawl
# Returns health status for all surfaces ✅

curl /api/v1/health/translation
# Returns translation queue stats ✅
```

---

**Version**: 3.0 Complete
**Integrates**: Culture-flexibility + Canonical language + Verifiability
