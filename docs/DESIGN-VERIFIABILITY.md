# Crawler Verifiability & Observability

**Version**: 1.0
**Date**: 2024-01-15
**Purpose**: Prove the crawler follows instructions with trackable metrics

---

## Overview

The crawler must be **verifiable** - we need to prove it's working correctly by tracking every execution with detailed metrics.

## Core Concept: CrawlRun

Every time a surface worker executes, it creates a **CrawlRun** record that tracks:

- ✅ What was fetched (count)
- ✅ What was stored (new items)
- ✅ What was deduplicated (already exists)
- ✅ Success or failure status
- ✅ Execution duration
- ✅ Error messages if failed

This provides **audit trail** and **proof of operation**.

---

## Data Model

### CrawlRun

Tracks each surface worker execution.

```python
class CrawlRun(models.Model):
    """
    Audit record for every surface collection execution.

    Provides verifiability: we can prove what was collected,
    what was stored, and what was deduplicated.
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

    # Metrics
    fetched_count = models.IntegerField(default=0)        # Items fetched from API
    stored_new_count = models.IntegerField(default=0)     # New items stored
    deduped_count = models.IntegerField(default=0)        # Duplicates skipped

    # State tracking
    next_cursor = models.TextField(null=True, blank=True) # Pagination cursor
    error_message = models.TextField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)  # Execution time

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['surface', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]
```

**Example CrawlRun record:**
```python
CrawlRun(
    surface=reddit_hot_surface,
    started_at='2024-01-15T10:00:00Z',
    finished_at='2024-01-15T10:00:03Z',
    status='success',
    fetched_count=50,          # Fetched 50 items from Reddit API
    stored_new_count=12,       # 12 were new, stored in DB
    deduped_count=38,          # 38 were duplicates, skipped
    next_cursor='t3_abc123',
    duration_ms=3247,          # Took 3.2 seconds
)
```

---

## Surface Worker Changes

### Before (No Tracking)

```python
# Old: No audit trail
for surface in due_surfaces:
    items, cursor = await collector.collect(...)
    for item in items:
        TrendItem.objects.create(...)  # No tracking
```

### After (With CrawlRun)

```python
# New: Full audit trail
for surface in due_surfaces:
    # Create CrawlRun to track execution
    run = CrawlRun.objects.create(
        surface=surface,
        started_at=now(),
        status='running',  # Will update later
    )

    try:
        start_time = time.time()

        # Collect items
        items, next_cursor = await collector.collect(...)
        fetched_count = len(items)

        # Process items
        stored_new_count = 0
        deduped_count = 0

        for item_dict in items:
            canonical_hash = compute_hash(...)

            # Check if duplicate
            if TrendItem.objects.filter(canonical_hash=canonical_hash).exists():
                deduped_count += 1
                continue

            # Store new item (unless DRY_RUN)
            if not DRY_RUN:
                TrendItem.objects.create(...)

            stored_new_count += 1

        # Update CrawlRun with success
        duration_ms = int((time.time() - start_time) * 1000)
        run.status = 'success'
        run.fetched_count = fetched_count
        run.stored_new_count = stored_new_count
        run.deduped_count = deduped_count
        run.next_cursor = next_cursor
        run.duration_ms = duration_ms
        run.finished_at = now()
        run.save()

    except Exception as e:
        # Update CrawlRun with failure
        run.status = 'failed'
        run.error_message = str(e)
        run.finished_at = now()
        run.save()
```

---

## DRY_RUN Mode

### Purpose

Test the crawler **without** storing data:
- Verify collectors work
- Check deduplication logic
- Validate counts
- Test error handling

**WITHOUT** polluting the database with test data.

### Configuration

```bash
# .env
DRY_RUN=true  # Enable dry run mode
```

### Behavior

| Operation | Normal Mode | DRY_RUN Mode |
|-----------|-------------|--------------|
| **Fetch from API** | ✅ Yes | ✅ Yes |
| **Compute hashes** | ✅ Yes | ✅ Yes |
| **Check duplicates** | ✅ Yes | ✅ Yes |
| **Count metrics** | ✅ Yes | ✅ Yes |
| **Create TrendItems** | ✅ Yes | ❌ No (skip) |
| **Create CrawlRun** | ✅ Yes | ✅ Yes (track metrics) |
| **Log results** | ✅ Yes | ✅ Yes (with DRY_RUN marker) |

### Example Logs

**Normal Mode:**
```
[INFO] CrawlRun #123: reddit_hot - fetched=50, new=12, deduped=38, duration=3247ms
[INFO] Stored 12 new items
```

**DRY_RUN Mode:**
```
[INFO] [DRY_RUN] CrawlRun #124: reddit_hot - fetched=50, new=12, deduped=38, duration=3201ms
[WARN] [DRY_RUN] Skipped storing 12 items (dry run mode enabled)
```

---

## Health Endpoints

### 1. Crawl Health

**Endpoint:** `GET /api/v1/health/crawl`

**Purpose:** Monitor surface worker health per-surface.

**Response:**
```json
{
  "surfaces": [
    {
      "surface_key": "reddit_hot",
      "region": "us",
      "platform": "reddit",
      "last_run_at": "2024-01-15T10:00:00Z",
      "last_status": "success",
      "last_counts": {
        "fetched": 50,
        "stored_new": 12,
        "deduped": 38
      },
      "lag_seconds": 120,  // How long since last successful run
      "health": "healthy"  // healthy | warning | critical
    },
    {
      "surface_key": "yahoo_jp_ranking",
      "region": "jp",
      "platform": "yahoo_jp",
      "last_run_at": "2024-01-15T09:45:00Z",
      "last_status": "failed",
      "last_error": "Connection timeout",
      "lag_seconds": 1020,
      "health": "critical"
    }
  ],
  "summary": {
    "total_surfaces": 10,
    "healthy": 7,
    "warning": 2,
    "critical": 1
  }
}
```

**Health Rules:**
- `healthy`: Last run succeeded AND lag < 2× poll_interval
- `warning`: Last run succeeded BUT lag > 2× poll_interval
- `critical`: Last run failed OR lag > 4× poll_interval

### 2. Translation Health

**Endpoint:** `GET /api/v1/health/translation`

**Purpose:** Monitor translation worker health.

**Response:**
```json
{
  "canonical_translation": {
    "pending_count": 23,
    "failed_count": 5,
    "total_items": 1000,
    "coverage_percent": 97.2
  },
  "by_locale": {
    "en-US": {
      "pending": 0,
      "failed": 0,
      "complete": 1000,
      "coverage_percent": 100.0
    },
    "ja-JP": {
      "pending": 15,
      "failed": 3,
      "complete": 982,
      "coverage_percent": 98.2
    },
    "es-ES": {
      "pending": 8,
      "failed": 2,
      "complete": 990,
      "coverage_percent": 99.0
    }
  },
  "by_provider": {
    "deepl": {
      "total": 850,
      "failed": 2,
      "success_rate": 99.8
    },
    "openai": {
      "total": 150,
      "failed": 3,
      "success_rate": 98.0
    }
  },
  "health": "healthy"
}
```

**Rules:**
- ✅ **No API calls** - just query database
- ✅ **Fast response** - pre-computed aggregations
- ✅ **SQLite compatible** - uses COUNT and GROUP BY

---

## Django Admin Changes

### 1. CrawlRun Admin

```python
@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'surface',
        'region_display',
        'status_indicator',
        'fetched_count',
        'stored_new_count',
        'deduped_count',
        'duration_display',
        'started_at',
    ]

    list_filter = [
        'status',
        'surface__region',
        'surface__platform',
        ('started_at', admin.DateFieldListFilter),
    ]

    search_fields = [
        'surface__key',
        'surface__platform',
        'error_message',
    ]

    readonly_fields = [
        'surface',
        'started_at',
        'finished_at',
        'status',
        'fetched_count',
        'stored_new_count',
        'deduped_count',
        'next_cursor',
        'error_message',
        'duration_ms',
        'created_at',
    ]

    def status_indicator(self, obj):
        if obj.status == 'success':
            return '✅ Success'
        else:
            return '❌ Failed'
    status_indicator.short_description = 'Status'

    def region_display(self, obj):
        return obj.surface.region.key
    region_display.short_description = 'Region'

    def duration_display(self, obj):
        if obj.duration_ms:
            return f"{obj.duration_ms}ms"
        return "-"
    duration_display.short_description = 'Duration'
```

### 2. TrendItem Admin - Enhanced Filters

```python
@admin.register(TrendItem)
class TrendItemAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'title_snippet',
        'region',
        'platform',
        'bucket',
        'original_locale',
        'canonical_status',
        'collected_at',
    ]

    list_filter = [
        'region',
        'surface__platform',
        'bucket',
        'original_locale',
        'canonical_translation_missing',  # Custom filter
        ('collected_at', admin.DateFieldListFilter),
    ]

    def canonical_translation_missing(self, obj):
        """Custom filter for items missing en-US translation."""
        if obj.original_locale == 'en-US':
            return False
        return not obj.translations.filter(
            locale='en-US',
            status='complete'
        ).exists()
```

**Custom Filter Implementation:**
```python
class MissingCanonicalTranslationFilter(admin.SimpleListFilter):
    title = 'canonical translation'
    parameter_name = 'canonical_missing'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Missing en-US'),
            ('no', 'Has en-US'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            # Items without en-US translation
            return queryset.exclude(
                original_locale='en-US'
            ).exclude(
                translations__locale='en-US',
                translations__status='complete'
            )
        elif self.value() == 'no':
            # Items with en-US translation
            return queryset.filter(
                translations__locale='en-US',
                translations__status='complete'
            ) | queryset.filter(
                original_locale='en-US'
            )
```

---

## Implementation Checklist

- [ ] Add CrawlRun model to models.py
- [ ] Update surface worker to create CrawlRun records
- [ ] Add DRY_RUN environment variable support
- [ ] Implement GET /api/v1/health/crawl endpoint
- [ ] Implement GET /api/v1/health/translation endpoint
- [ ] Register CrawlRun in Django Admin
- [ ] Add custom filters to TrendItem admin
- [ ] Update worker logs to show DRY_RUN markers
- [ ] Add DRY_RUN to .env.example

---

## Verification Examples

### Test Scenario 1: Verify Deduplication Works

```bash
# Run 1: Collect 50 items, all new
$ python manage.py runworker --surface reddit_hot
CrawlRun #1: fetched=50, new=50, deduped=0

# Run 2: Collect same 50 items, all duplicates
$ python manage.py runworker --surface reddit_hot
CrawlRun #2: fetched=50, new=0, deduped=50  ✅ Proof of deduplication!
```

### Test Scenario 2: DRY_RUN Mode

```bash
# Enable dry run
$ export DRY_RUN=true

# Run worker
$ python manage.py runworker
[DRY_RUN] CrawlRun #3: fetched=50, new=12, deduped=38
[DRY_RUN] Skipped storing 12 items

# Check database: TrendItem count unchanged ✅
```

### Test Scenario 3: Health Monitoring

```bash
# Check crawl health
$ curl http://localhost:8000/api/v1/health/crawl
{
  "surfaces": [...],
  "summary": {"healthy": 8, "warning": 1, "critical": 1}
}

# Investigate critical surface
$ curl http://localhost:8000/api/v1/health/crawl?status=critical
{
  "surfaces": [
    {
      "surface_key": "yahoo_jp_ranking",
      "last_error": "Connection timeout",
      "lag_seconds": 3600
    }
  ]
}
```

---

## Benefits

✅ **Verifiable**: Prove every execution with metrics
✅ **Debuggable**: See exactly what happened in each run
✅ **Testable**: DRY_RUN mode for safe testing
✅ **Monitorable**: Health endpoints for alerts
✅ **Auditable**: Complete execution history

---

**Document Version**: 1.0
**Last Updated**: 2024-01-15
