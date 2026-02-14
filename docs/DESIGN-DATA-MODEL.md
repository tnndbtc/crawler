# Trend Crawler Architecture - Data Model

[← Overview](./DESIGN-OVERVIEW.md) | **Part 2 of 4** | [Surfaces](./DESIGN-SURFACES.md) | [Workers & API](./DESIGN-WORKERS-API.md)

---

## Data Model

### 1. Region

Represents a geographical/cultural region with its own trend ecosystem.

```python
class Region(models.Model):
    key = models.CharField(max_length=10, unique=True)  # "us", "jp", "kr", "cn"
    name = models.CharField(max_length=100)             # "United States", "Japan"
    default_locale = models.CharField(max_length=10)    # "en-US", "ja-JP"
    enabled = models.BooleanField(default=True)
```

**Examples:**
- `us`: United States (en-US)
- `jp`: Japan (ja-JP)
- `kr`: South Korea (ko-KR)
- `cn`: China (zh-Hans)
- `eu`: Europe (en-US or de-DE)
- `sa`: Saudi Arabia (ar-SA)

**Usage:**
```python
# Create a region
Region.objects.create(
    key='jp',
    name='Japan',
    default_locale='ja-JP',
    enabled=True
)
```

---

### 2. TrendSurface

Represents a specific trend indicator source within a region.

```python
class TrendSurface(models.Model):
    region = models.ForeignKey(Region)
    key = models.CharField(max_length=100)  # Unique per region
    surface_type = models.CharField(choices=[
        ('ranking', 'Ranking Surface'),      # Curated lists
        ('sampler', 'Feed Sampler'),         # Algorithmic feed samples
        ('search', 'Search Trends'),         # Search spike indicators
        ('news', 'News Top Stories'),        # News portal rankings
    ])
    platform = models.CharField(max_length=50)  # "reddit", "youtube", "weibo"
    entrypoint = models.CharField(max_length=200)  # "crawler_surfaces.reddit_hot:collect"
    enabled = models.BooleanField(default=True)
    poll_interval_seconds = models.IntegerField(default=3600)
    max_items_per_run = models.IntegerField(default=200)
    config_json = models.JSONField(default=dict)  # Collector-specific config
    last_cursor = models.TextField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_error = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = [['region', 'key']]
```

**Examples:**

| Region | Key | Type | Platform | Entrypoint |
|--------|-----|------|----------|------------|
| us | reddit_hot | ranking | reddit | crawler_surfaces.reddit_hot:collect |
| us | youtube_trending | ranking | youtube | crawler_surfaces.youtube_trending:collect |
| jp | yahoo_jp_ranking | ranking | yahoo_jp | crawler_surfaces.yahoo_jp_ranking:collect |
| kr | naver_realtime | search | naver | crawler_surfaces.naver_realtime:collect |

**Field Descriptions:**

- **region**: Which region this surface belongs to
- **key**: Unique identifier within the region (e.g., "reddit_hot")
- **surface_type**: One of 4 types (ranking, sampler, search, news)
- **platform**: Platform name for display/filtering
- **entrypoint**: Python import path to collector function
- **enabled**: Whether this surface is active
- **poll_interval_seconds**: How often to collect (e.g., 3600 = 1 hour)
- **max_items_per_run**: Limit to prevent runaway collection
- **config_json**: Surface-specific configuration passed to collector
- **last_cursor**: Pagination state from last run
- **last_run_at**: When last collection completed
- **next_run_at**: When next collection should happen
- **last_run_error**: Error message if last run failed

**Usage:**
```python
# Create a surface
TrendSurface.objects.create(
    region=region_us,
    key='reddit_hot',
    surface_type='ranking',
    platform='reddit',
    entrypoint='crawler_surfaces.reddit_hot:collect',
    poll_interval_seconds=3600,
    max_items_per_run=200,
    config_json={'subreddit': 'all', 'locale': 'en-US'}
)
```

---

### 3. TrendItem

Raw trend signals collected from surfaces.

```python
class TrendItem(models.Model):
    region = models.ForeignKey(Region)
    surface = models.ForeignKey(TrendSurface)
    external_id = models.CharField(max_length=255, null=True)  # Platform's ID
    title_original = models.TextField()
    description_original = models.TextField(null=True, blank=True)
    original_locale = models.CharField(max_length=10)  # One of supported locales
    url = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(auto_now_add=True)
    raw_payload = models.JSONField(default=dict)  # Original API response
    canonical_hash = models.CharField(max_length=64, db_index=True)  # For dedupe

    class Meta:
        indexes = [
            models.Index(fields=['region', 'collected_at']),
            models.Index(fields=['canonical_hash']),
        ]
```

**Field Descriptions:**

- **region**: Which region this item belongs to
- **surface**: Which surface collected this item
- **external_id**: Platform's unique ID (optional, for reference)
- **title_original**: Original title in source language
- **description_original**: Original description/snippet (optional)
- **original_locale**: Locale of original content (e.g., "ja-JP")
- **url**: Link to original content
- **published_at**: When content was published (if available)
- **collected_at**: When we collected this item
- **raw_payload**: Full platform response for debugging/enrichment
- **canonical_hash**: SHA256 hash for deduplication

**Canonical Hash Computation:**

```python
import hashlib

def compute_canonical_hash(title: str, url: str) -> str:
    """
    Compute canonical hash for deduplication.

    Normalizes title and URL to catch duplicates across surfaces.
    """
    normalized = f"{title.lower().strip()}|{url.lower().strip()}"
    return hashlib.sha256(normalized.encode()).hexdigest()
```

**Why Canonical Hash?**

Same content may appear on multiple surfaces:
- Reddit post → also trending on Twitter
- YouTube video → also on Yahoo News
- Weibo post → also on Zhihu

Canonical hash prevents duplicates while preserving surface attribution.

**Usage:**
```python
# Create trend item
canonical_hash = compute_canonical_hash(
    "Trending Topic",
    "https://example.com/post"
)

TrendItem.objects.create(
    region=region_us,
    surface=surface_reddit,
    external_id='abc123',
    title_original='Trending Topic',
    description_original='More details...',
    original_locale='en-US',
    url='https://example.com/post',
    published_at=datetime.now(),
    raw_payload={'upvotes': 1234, 'comments': 56},
    canonical_hash=canonical_hash
)
```

---

### 4. TrendItemTranslation

Translated versions of trend items for different locales.

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
    provider = models.CharField(max_length=20)  # "deepl", "openai"
    error_message = models.TextField(null=True, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [['item', 'locale']]
```

**Field Descriptions:**

- **item**: Which TrendItem this translates
- **locale**: Target locale (e.g., "ja-JP")
- **title**: Translated title
- **description**: Translated description (if original had one)
- **status**: Translation workflow state
- **provider**: Which translation API was used
- **error_message**: Error details if failed
- **translated_at**: When translation completed

**Translation Workflow:**

```
pending → running → complete
                 ↘ failed
```

**Usage:**
```python
# Create translation
TrendItemTranslation.objects.create(
    item=trend_item,
    locale='ja-JP',
    title='トレンドトピック',
    description='詳細...',
    status='complete',
    provider='deepl',
    translated_at=datetime.now()
)
```

---

### 5. TranslationSettings

Global or per-region translation configuration.

```python
class TranslationSettings(models.Model):
    region = models.ForeignKey(Region, null=True, blank=True)  # Null = global
    default_provider = models.CharField(
        max_length=20,
        choices=[('deepl', 'DeepL'), ('openai', 'OpenAI')],
        default='deepl'
    )
    enabled_locales = models.JSONField(default=list)  # List of locale codes
    enabled = models.BooleanField(default=True)
```

**Field Descriptions:**

- **region**: Specific region (null = global default)
- **default_provider**: Which translation API to use
- **enabled_locales**: Which locales to translate to
- **enabled**: Master switch for translations

**Configuration Patterns:**

**Global Settings:**
```python
# Apply to all regions unless overridden
TranslationSettings.objects.create(
    region=None,
    default_provider='deepl',
    enabled_locales=['en-US', 'ja-JP', 'ko-KR', 'zh-Hans'],
    enabled=True
)
```

**Region-Specific Override:**
```python
# Use OpenAI for China (DeepL may be blocked)
TranslationSettings.objects.create(
    region=region_cn,
    default_provider='openai',
    enabled_locales=['en-US', 'zh-Hant'],
    enabled=True
)
```

**How Settings Are Resolved:**

```python
def get_translation_settings(region: Region) -> TranslationSettings:
    """Get translation settings for region, falling back to global."""
    # Try region-specific first
    settings = TranslationSettings.objects.filter(
        region=region,
        enabled=True
    ).first()

    # Fall back to global
    if not settings:
        settings = TranslationSettings.objects.filter(
            region=None,
            enabled=True
        ).first()

    return settings
```

---

## Database Schema

### Entity Relationship Diagram

```
┌─────────────┐
│   Region    │
│─────────────│
│ key (PK)    │
│ name        │
│ locale      │
└──────┬──────┘
       │ 1
       │
       │ N
┌──────▼──────────┐         ┌─────────────────┐
│  TrendSurface   │         │  Translation    │
│─────────────────│         │  Settings       │
│ id (PK)         │         │─────────────────│
│ region_id (FK)  │         │ region_id (FK)  │
│ key             │         │ provider        │
│ surface_type    │         │ locales         │
│ entrypoint      │         └─────────────────┘
│ config_json     │
└────────┬────────┘
         │ 1
         │
         │ N
    ┌────▼──────────┐
    │   TrendItem   │
    │───────────────│
    │ id (PK)       │
    │ region_id(FK) │
    │ surface_id(FK)│
    │ title_orig    │
    │ locale_orig   │
    │ url           │
    │ canonical_hash│
    └────────┬──────┘
             │ 1
             │
             │ N
     ┌───────▼──────────────┐
     │ TrendItemTranslation │
     │──────────────────────│
     │ id (PK)              │
     │ item_id (FK)         │
     │ locale               │
     │ title                │
     │ status               │
     └──────────────────────┘
```

---

## Indexes and Performance

### Required Indexes

```python
# TrendItem indexes
models.Index(fields=['region', 'collected_at'])  # For time-range queries
models.Index(fields=['canonical_hash'])          # For deduplication

# TrendSurface indexes
models.Index(fields=['enabled', 'next_run_at'])  # For worker queries

# TrendItemTranslation indexes
models.Index(fields=['status'])                  # For translation worker
models.Index(fields=['item', 'locale'])          # Unique constraint
```

### Query Patterns

**Find surfaces due for collection:**
```python
due_surfaces = TrendSurface.objects.filter(
    enabled=True,
    next_run_at__lte=now()
).select_related('region')
```

**Get recent trends with translations:**
```python
trends = TrendItem.objects.filter(
    region__key='us',
    collected_at__gte=since
).prefetch_related(
    'translations'
).select_related(
    'region',
    'surface'
)
```

**Find items needing translation:**
```python
items = TrendItem.objects.filter(
    translations__locale='ja-JP',
    translations__status='pending'
).select_related('region')
```

---

## Data Retention

**TrendItems:**
- Keep: 30 days by default
- Configurable per region
- Archive to cold storage after 30 days

**Translations:**
- Keep: Same as TrendItem
- Cascade delete when item is deleted

**Surfaces:**
- Keep: Forever (configuration)
- Soft delete (enabled=False) instead of hard delete

---

**Document Version**: 1.0
**Last Updated**: 2024-01-15
**Previous**: [← Overview](./DESIGN-OVERVIEW.md) | **Next**: [Surfaces →](./DESIGN-SURFACES.md)
