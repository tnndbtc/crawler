# Crawler Observability & Safety Layer

**Version**: 1.1 (Updated from /tmp/t8)
**Date**: 2024-01-15
**Purpose**: Self-verifiable crawler with minimal invasive changes

---

## Goal

Answer these questions at any time:
- ✅ Is each surface running?
- ✅ Did it succeed?
- ✅ How many items were fetched vs stored?
- ✅ Are translations happening?
- ✅ Can I safely test new collectors?

**Constraints**:
- ✅ SQLite compatible
- ✅ Minimal code changes
- ✅ No new infrastructure (no Celery, Redis, etc.)
- ✅ Worker never crashes main loop

---

## 1. CrawlRun Audit Log (Core Observability)

### Model

```python
class CrawlRun(models.Model):
    """
    Audit log for every surface execution.
    Provides complete observability into what happened.
    """
    surface = models.ForeignKey(TrendSurface, on_delete=models.CASCADE)

    # Timestamps
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    # Status
    status = models.CharField(
        max_length=20,
        choices=[
            ('success', 'Success'),
            ('failed', 'Failed'),
        ]
    )

    # Counts (VERIFIABILITY)
    fetched_count = models.IntegerField(default=0)      # From API
    stored_new_count = models.IntegerField(default=0)   # Inserted to DB
    deduped_count = models.IntegerField(default=0)      # Skipped (duplicates)

    # Error tracking
    error_message = models.TextField(null=True, blank=True)

    # Performance
    duration_ms = models.IntegerField(null=True, blank=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['surface', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]
```

### Worker Behavior

```python
# When surface starts
run = CrawlRun.objects.create(
    surface=surface,
    started_at=now()
)

# During execution
fetched_count = len(items)  # From collector
stored_new_count = 0        # Count insertions
deduped_count = 0           # Count duplicates

# On success
run.finished_at = now()
run.duration_ms = (run.finished_at - run.started_at).total_seconds() * 1000
run.status = 'success'
run.fetched_count = fetched_count
run.stored_new_count = stored_new_count
run.deduped_count = deduped_count
run.save()

# On failure
run.finished_at = now()
run.status = 'failed'
run.error_message = str(exception)
run.save()

# Worker MUST continue to next surface
```

---

## 2. Surface Health Fields (Quick Diagnosis)

### Extended TrendSurface Model

Add these fields to **existing** TrendSurface:

```python
class TrendSurface(models.Model):
    # ... existing fields ...

    # NEW: Health tracking
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
```

### Worker Updates

```python
# At start of execution
surface.last_run_at = now()
surface.save()

# On success
surface.last_success_at = now()
surface.last_error = None
surface.save()

# On failure
surface.last_error = str(exception)
surface.save()
```

**Benefit**: Quick glance at surface health without querying CrawlRun table.

---

## 3. DRY_RUN Mode (Safe Collector Testing)

### Environment Variable

```bash
# .env
DRY_RUN=false  # Default: false (normal operation)
DRY_RUN=true   # Enable for testing
```

### Behavior

```python
import os

DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'

# In worker loop
for item_dict in items:
    canonical_hash = compute_hash(...)

    if TrendItem.objects.filter(canonical_hash=canonical_hash).exists():
        deduped_count += 1
        continue

    # Only insert if NOT dry run
    if not DRY_RUN:
        TrendItem.objects.create(...)

    stored_new_count += 1

# CrawlRun is ALWAYS created (even in dry run)
run.stored_new_count = stored_new_count  # Tracks what WOULD be stored
run.save()

# Log with marker
if DRY_RUN:
    logger.warning(f"[DRY_RUN] Would store {stored_new_count} items")
```

**When to Use**:
- Testing new collectors before production
- Verifying deduplication logic
- Checking API response formats
- Debugging without polluting database

---

## 4. Safety Guardrails (Timeout Protection)

### Environment Variable

```bash
# .env
MAX_RUN_SECONDS=60  # Default: 60 seconds per surface
```

### Implementation

```python
import asyncio
import os

MAX_RUN_SECONDS = int(os.getenv('MAX_RUN_SECONDS', '60'))

async def run_surface_with_timeout(surface):
    """Execute surface with timeout protection."""
    run = CrawlRun.objects.create(
        surface=surface,
        started_at=now()
    )

    try:
        # Wrap execution in timeout
        result = await asyncio.wait_for(
            execute_surface(surface, run),
            timeout=MAX_RUN_SECONDS
        )
        return result

    except asyncio.TimeoutError:
        # Timeout - mark failed and continue
        run.status = 'failed'
        run.error_message = f'Timeout after {MAX_RUN_SECONDS}s'
        run.finished_at = now()
        run.save()

        surface.last_error = f'Timeout after {MAX_RUN_SECONDS}s'
        surface.save()

        logger.error(f'Surface {surface.key} timed out after {MAX_RUN_SECONDS}s')

    except Exception as e:
        # Other errors
        run.status = 'failed'
        run.error_message = str(e)
        run.finished_at = now()
        run.save()

        surface.last_error = str(e)
        surface.save()

        logger.error(f'Surface {surface.key} failed: {e}')
```

**Why This Matters**:
- Prevents one slow collector from blocking all others
- Worker continues even if collector hangs
- Clear timeout messages in CrawlRun

---

## 5. Translation Health Endpoint

### Endpoint

```http
GET /api/v1/health/translation
```

### Response Format

```json
{
  "missing_canonical_en_count": 23,
  "pending_count": 15,
  "failed_count": 5
}
```

### Implementation

```python
@app.get("/api/v1/health/translation")
async def translation_health():
    """
    Translation queue health.

    Rules:
    - No external API calls
    - SQLite compatible
    - Fast query
    """
    # Items missing canonical (en-US) translation
    missing_canonical = TrendItem.objects.exclude(
        original_locale='en-US'
    ).exclude(
        translations__locale='en-US',
        translations__status='complete'
    ).count()

    # Pending translations (any locale)
    pending = TrendItemTranslation.objects.filter(
        status='pending'
    ).count()

    # Failed translations (any locale)
    failed = TrendItemTranslation.objects.filter(
        status='failed'
    ).count()

    return {
        "missing_canonical_en_count": missing_canonical,
        "pending_count": pending,
        "failed_count": failed,
    }
```

**Quick Diagnosis**:
- High `missing_canonical_en_count` → Translation worker not running or falling behind
- High `pending_count` → Translation worker slow
- High `failed_count` → API key issues or provider problems

---

## 6. Crawl Health Endpoint

### Endpoint

```http
GET /api/v1/health/crawl
```

### Response Format

```json
[
  {
    "surface_key": "reddit_hot",
    "last_status": "success",
    "last_finished_at": "2024-01-15T10:00:03Z",
    "duration_ms": 3247
  },
  {
    "surface_key": "yahoo_jp_ranking",
    "last_status": "failed",
    "last_finished_at": "2024-01-15T09:45:12Z",
    "duration_ms": 15234
  }
]
```

### Implementation

```python
@app.get("/api/v1/health/crawl")
async def crawl_health():
    """
    Per-surface crawl health.

    Returns last CrawlRun for each surface.
    """
    surfaces = TrendSurface.objects.filter(enabled=True)

    health_data = []
    for surface in surfaces:
        # Get most recent CrawlRun
        last_run = CrawlRun.objects.filter(
            surface=surface
        ).order_by('-created_at').first()

        if last_run:
            health_data.append({
                "surface_key": surface.key,
                "last_status": last_run.status,
                "last_finished_at": last_run.finished_at,
                "duration_ms": last_run.duration_ms,
            })

    return health_data
```

**Quick Diagnosis**:
- See which surfaces are failing
- Check execution times (slow collectors)
- Verify surfaces are running

---

## 7. Recent Items Sanity Endpoint

### Endpoint

```http
GET /api/v1/surfaces/{surface_id}/recent?limit=20
```

### Response Format

```json
{
  "surface_key": "reddit_hot",
  "items": [
    {
      "id": 123,
      "title_original": "Breaking news story",
      "canonical_title": "Breaking news story",  // en-US translation if exists
      "bucket": "hot_now",
      "collected_at": "2024-01-15T10:00:00Z"
    },
    {
      "id": 124,
      "title_original": "日本のニュース",
      "canonical_title": "Japanese news",  // Translated
      "bucket": "hot_now",
      "collected_at": "2024-01-15T09:58:00Z"
    }
  ]
}
```

### Implementation

```python
@app.get("/api/v1/surfaces/{surface_id}/recent")
async def surface_recent_items(surface_id: int, limit: int = 20):
    """
    Recent items from a specific surface.

    Useful for sanity checking collector output.
    """
    surface = TrendSurface.objects.get(id=surface_id)

    items = TrendItem.objects.filter(
        surface=surface
    ).order_by('-collected_at')[:limit]

    items_data = []
    for item in items:
        # Get canonical (en-US) translation if exists
        canonical_title = item.title_original

        if item.original_locale != 'en-US':
            en_translation = item.translations.filter(
                locale='en-US',
                status='complete'
            ).first()

            if en_translation:
                canonical_title = en_translation.title

        items_data.append({
            "id": item.id,
            "title_original": item.title_original,
            "canonical_title": canonical_title,
            "bucket": item.bucket,
            "collected_at": item.collected_at,
        })

    return {
        "surface_key": surface.key,
        "items": items_data,
    }
```

**Use Case**:
- Verify new collector is working
- Check if translations are happening
- Debug collector output format
- Sanity check bucket assignment

---

## 8. Django Admin Configuration

### CrawlRun Admin

```python
@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'surface',
        'status_indicator',
        'fetched_count',
        'stored_new_count',
        'deduped_count',
        'duration_ms',
        'created_at',
    ]

    list_filter = [
        'status',
        'surface__region',
        'surface',
        ('created_at', admin.DateFieldListFilter),
    ]

    readonly_fields = [
        'surface',
        'started_at',
        'finished_at',
        'status',
        'fetched_count',
        'stored_new_count',
        'deduped_count',
        'error_message',
        'duration_ms',
        'created_at',
    ]

    def status_indicator(self, obj):
        if obj.status == 'success':
            return '✅ Success'
        else:
            return f'❌ Failed: {obj.error_message[:50]}'
    status_indicator.short_description = 'Status'
```

### TrendItem Admin - Additional Filters

```python
class MissingCanonicalFilter(admin.SimpleListFilter):
    """Filter for items missing canonical en-US translation."""
    title = 'canonical translation'
    parameter_name = 'canonical_missing'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Missing en-US'),
            ('no', 'Has en-US'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(
                original_locale='en-US'
            ).exclude(
                translations__locale='en-US',
                translations__status='complete'
            )
        elif self.value() == 'no':
            return queryset.filter(
                translations__locale='en-US',
                translations__status='complete'
            ) | queryset.filter(
                original_locale='en-US'
            )

@admin.register(TrendItem)
class TrendItemAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'title_snippet',
        'region',
        'platform',
        'bucket',
        'canonical_status',
        'collected_at',
    ]

    list_filter = [
        'region',
        'surface__platform',
        'bucket',
        MissingCanonicalFilter,  # NEW
        ('collected_at', admin.DateFieldListFilter),
    ]

    def platform(self, obj):
        return obj.surface.platform

    def canonical_status(self, obj):
        if obj.original_locale == 'en-US':
            return '✅ Original English'

        has_translation = obj.translations.filter(
            locale='en-US',
            status='complete'
        ).exists()

        return '✅ Translated' if has_translation else '❌ Missing'
    canonical_status.short_description = 'Canonical en-US'
```

---

## Summary of Changes

### Models
- ✅ **Add**: CrawlRun (new model)
- ✅ **Extend**: TrendSurface (add last_run_at, last_success_at, last_error)

### Worker
- ✅ **Add**: CrawlRun creation for every execution
- ✅ **Add**: DRY_RUN mode support
- ✅ **Add**: MAX_RUN_SECONDS timeout protection
- ✅ **Add**: Count tracking (fetched, stored, deduped)

### API Endpoints
- ✅ **Add**: GET /api/v1/health/translation
- ✅ **Add**: GET /api/v1/health/crawl
- ✅ **Add**: GET /api/v1/surfaces/{surface_id}/recent

### Django Admin
- ✅ **Register**: CrawlRun admin
- ✅ **Extend**: TrendItem admin with missing canonical filter

### Environment Variables
- ✅ **Add**: DRY_RUN (default: false)
- ✅ **Add**: MAX_RUN_SECONDS (default: 60)

---

## Verification Examples

### Example 1: Test New Collector Safely

```bash
# Enable dry run
export DRY_RUN=true

# Run worker
python -m crawler_api.workers.surface_worker

# Check CrawlRun in admin
# See: fetched=50, stored_new=12, deduped=38
# Database unchanged ✅

# Check recent items
curl http://localhost:8000/api/v1/surfaces/1/recent
# Verify output format ✅
```

### Example 2: Monitor Production Health

```bash
# Check crawl health
curl http://localhost:8000/api/v1/health/crawl
[
  {"surface_key": "reddit_hot", "last_status": "success", ...},
  {"surface_key": "yahoo_jp", "last_status": "failed", ...}
]

# Check translation health
curl http://localhost:8000/api/v1/health/translation
{
  "missing_canonical_en_count": 5,
  "pending_count": 12,
  "failed_count": 0
}
```

### Example 3: Debug Slow Collector

```bash
# Check duration in admin or API
curl http://localhost:8000/api/v1/health/crawl
[
  {"surface_key": "slow_collector", "duration_ms": 58000}  // Nearly timing out!
]

# Adjust timeout if needed
export MAX_RUN_SECONDS=120
```

---

**Rules Followed**:
- ✅ Minimal invasive changes
- ✅ SQLite compatible
- ✅ No new infrastructure
- ✅ Worker never crashes
- ✅ Only extensions, no redesigns

---

**Version**: 1.1
**Last Updated**: 2024-01-15
**Based on**: /tmp/t8 requirements
