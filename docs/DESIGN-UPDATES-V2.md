# Design Updates v2 - Additional Requirements

**Date**: 2024-01-15
**Version**: 2.0
**Status**: Incorporates requirements from /tmp/t4

---

## 🆕 New Concepts

### 1. Bucket System

**What is a Bucket?**

A "bucket" categorizes the **type of trending signal** a surface provides. This helps organize and filter trends by their nature.

**Bucket Types:**

| Bucket | Description | Example |
|--------|-------------|---------|
| `hot_now` | Currently trending/viral | Reddit Hot, Twitter Trending |
| `rising` | Gaining momentum | Reddit Rising, YouTube Rising |
| `category_*` | Category-specific trends | `category_tech`, `category_sports` |
| `evergreen` | Consistently popular | Wikipedia most viewed |
| `local` | Geographically localized | Google Trends by city |

**Why Buckets?**

- Different buckets have different signal strengths
- `hot_now` = high urgency, short-lived
- `evergreen` = persistent topics
- Allows downstream analysis to weight signals differently

### 2. Canonical Analysis Language (en-US)

**Core Principle**: English is the **base language for all trend analysis**.

**Why?**
- Cross-regional comparison requires common language
- Machine learning models work best with consistent language
- Clustering/ranking algorithms need normalized text

**Strategy**:
1. Collect item in original language (preserve authenticity)
2. **ALWAYS** translate to en-US (for analysis)
3. Optionally translate to other locales (for localization)

**Implementation**:
```
Japanese item collected
  ↓
Original: 日本語のタイトル (ja-JP)
  ↓
Canonical translation: "Japanese Title" (en-US) ← REQUIRED for analysis
  ↓
Additional translations:
  - "Título japonés" (es-ES)
  - "Titre japonais" (fr-FR)
```

---

## 📊 Updated Data Models

### 1. TrendSurface (ENHANCED)

**NEW FIELD: bucket**

```python
class TrendSurface(models.Model):
    region = models.ForeignKey(Region)
    key = models.CharField(max_length=100)
    platform = models.CharField(max_length=50)
    surface_type = models.CharField(choices=[
        ('ranking', 'Ranking Surface'),
        ('sampler', 'Feed Sampler'),
        ('search', 'Search Trends'),
        ('news', 'News Top Stories'),
    ])

    # NEW: Bucket categorization
    bucket = models.CharField(
        max_length=50,
        choices=[
            ('hot_now', 'Hot Now'),
            ('rising', 'Rising'),
            ('category_tech', 'Tech Category'),
            ('category_sports', 'Sports Category'),
            ('category_entertainment', 'Entertainment Category'),
            ('evergreen', 'Evergreen'),
            ('local', 'Local'),
        ],
        default='hot_now',
        help_text="Categorizes the type of trending signal"
    )

    entrypoint = models.CharField(max_length=200)
    enabled = models.BooleanField(default=True)
    poll_interval_seconds = models.IntegerField(default=3600)
    max_items_per_run = models.IntegerField(default=200)
    config_json = models.JSONField(default=dict)
    last_cursor = models.TextField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_error = models.TextField(null=True, blank=True)  # Renamed from last_error

    class Meta:
        unique_together = [['region', 'key']]
```

**Example Configuration:**

```python
# Reddit Hot = currently trending
TrendSurface.objects.create(
    region=us,
    key='reddit_hot',
    platform='reddit',
    surface_type='ranking',
    bucket='hot_now',  # ← NEW
    entrypoint='crawler_surfaces.reddit_hot:collect',
    poll_interval_seconds=1800  # 30 min
)

# Reddit Rising = gaining momentum
TrendSurface.objects.create(
    region=us,
    key='reddit_rising',
    platform='reddit',
    surface_type='ranking',
    bucket='rising',  # ← NEW
    entrypoint='crawler_surfaces.reddit_rising:collect',
    poll_interval_seconds=3600  # 1 hour
)
```

### 2. TrendItem (ENHANCED)

**NEW FIELDS: rank_position, engagement_signals, bucket**

```python
class TrendItem(models.Model):
    region = models.ForeignKey(Region)
    surface = models.ForeignKey(TrendSurface)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    canonical_hash = models.CharField(max_length=64, db_index=True)

    # Content fields
    title_original = models.TextField()
    description_original = models.TextField(null=True, blank=True)
    original_locale = models.CharField(max_length=10)
    url = models.TextField()

    # NEW: Ranking/position data
    rank_position = models.IntegerField(
        null=True,
        blank=True,
        help_text="Position in ranking (1=top, null=not ranked)"
    )

    # NEW: Engagement metrics
    engagement_signals = models.JSONField(
        default=dict,
        help_text="Platform-specific engagement data (upvotes, views, likes, etc.)"
    )

    # NEW: Bucket (copied from surface at collection time)
    bucket = models.CharField(
        max_length=50,
        help_text="Trend bucket type from source surface"
    )

    # Timestamps
    published_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(auto_now_add=True)

    # Original payload
    raw_payload = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(fields=['region', 'collected_at']),
            models.Index(fields=['canonical_hash']),
            models.Index(fields=['bucket', 'collected_at']),  # NEW
            models.Index(fields=['rank_position']),  # NEW
        ]
```

**Example Data:**

```python
TrendItem.objects.create(
    region=us,
    surface=reddit_hot_surface,
    external_id='abc123',
    canonical_hash='sha256...',
    title_original='New AI breakthrough announced',
    original_locale='en-US',
    url='https://reddit.com/...',

    # NEW: Ranking data
    rank_position=3,  # 3rd on the list

    # NEW: Engagement signals
    engagement_signals={
        'upvotes': 5432,
        'comments': 234,
        'upvote_ratio': 0.95,
        'awards': 12
    },

    # NEW: Bucket
    bucket='hot_now',  # Copied from surface

    published_at=datetime.now(),
    collected_at=datetime.now(),
    raw_payload={...}
)
```

### 3. TranslationSettings (ENHANCED)

**NEW FIELDS: canonical_locale_for_analysis, force_canonical_translation**

```python
class TranslationSettings(models.Model):
    # Basic settings
    translation_enabled = models.BooleanField(
        default=True,
        help_text="Master switch for translation worker"
    )

    default_provider = models.CharField(
        max_length=20,
        choices=[('deepl', 'DeepL'), ('openai', 'OpenAI')],
        default='deepl',
        help_text="Primary translation provider"
    )

    # NEW: Canonical language strategy
    canonical_locale_for_analysis = models.CharField(
        max_length=10,
        default='en-US',
        help_text="Base language for trend analysis (MUST be en-US)"
    )

    force_canonical_translation = models.BooleanField(
        default=True,
        help_text="Always create en-US translation when original_locale != en-US"
    )

    # Locale configuration
    enabled_locales = models.JSONField(
        default=list,
        help_text="Additional locales to translate to (beyond canonical)"
    )

    # Rate limiting (optional)
    max_chars_per_request = models.IntegerField(
        default=5000,
        help_text="Maximum characters per translation request"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Translation Settings"
        verbose_name_plural = "Translation Settings"

    def clean(self):
        """Validate canonical_locale_for_analysis is always en-US."""
        if self.canonical_locale_for_analysis != 'en-US':
            raise ValidationError(
                "canonical_locale_for_analysis must be 'en-US' for proper trend analysis"
            )
```

**Example Configuration:**

```python
TranslationSettings.objects.create(
    translation_enabled=True,
    default_provider='deepl',

    # NEW: Canonical strategy
    canonical_locale_for_analysis='en-US',  # REQUIRED
    force_canonical_translation=True,       # ALWAYS translate to English

    # Additional locales (optional)
    enabled_locales=['es-ES', 'fr-FR', 'de-DE'],

    max_chars_per_request=5000
)
```

---

## 🔄 Updated Translation Worker Logic

### Priority-Based Translation

```python
async def translate_item(item: TrendItem, settings: TranslationSettings):
    """
    Translate item with canonical-first strategy.

    Priority:
    1. Canonical (en-US) - ALWAYS (if original != en-US)
    2. Additional locales - Optional
    """

    # STEP 1: CANONICAL TRANSLATION (HIGHEST PRIORITY)
    if settings.force_canonical_translation:
        if item.original_locale != settings.canonical_locale_for_analysis:
            # Create en-US translation (REQUIRED for analysis)
            translation, created = TrendItemTranslation.objects.get_or_create(
                item=item,
                locale=settings.canonical_locale_for_analysis,
                defaults={
                    'status': 'pending',
                    'provider': settings.default_provider
                }
            )

            if translation.status == 'pending':
                await process_translation(translation, settings, priority='high')

    # STEP 2: ADDITIONAL LOCALES (LOWER PRIORITY)
    for locale in settings.enabled_locales:
        # Skip if same as original or canonical
        if locale in [item.original_locale, settings.canonical_locale_for_analysis]:
            continue

        translation, created = TrendItemTranslation.objects.get_or_create(
            item=item,
            locale=locale,
            defaults={
                'status': 'pending',
                'provider': settings.default_provider
            }
        )

        if translation.status == 'pending':
            await process_translation(translation, settings, priority='normal')


async def process_translation(
    translation: TrendItemTranslation,
    settings: TranslationSettings,
    priority: str
):
    """Process a single translation."""
    try:
        # Mark as running
        translation.status = 'running'
        translation.save()

        # Get provider
        provider = get_provider(settings.default_provider)

        # Translate title
        translated_title = await provider.translate(
            text=translation.item.title_original,
            source_locale=translation.item.original_locale,
            target_locale=translation.locale
        )

        # Translate description (if exists)
        translated_desc = None
        if translation.item.description_original:
            translated_desc = await provider.translate(
                text=translation.item.description_original,
                source_locale=translation.item.original_locale,
                target_locale=translation.locale
            )

        # Save translation
        translation.title = translated_title
        translation.description = translated_desc
        translation.status = 'complete'
        translation.translated_at = datetime.utcnow()
        translation.save()

        logger.info(
            f"Translated item {translation.item.id} to {translation.locale} "
            f"(priority: {priority})"
        )

    except Exception as e:
        # Mark as failed
        translation.status = 'failed'
        translation.error_message = str(e)
        translation.save()

        logger.error(
            f"Translation failed for item {translation.item.id} "
            f"to {translation.locale}: {e}"
        )
```

---

## 🔌 Updated API Endpoints

### 1. Health Check (NEW)

```python
@app.get("/health")
async def health_check():
    """Basic health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }
```

### 2. System Status (NEW)

```python
@app.get("/api/v1/status")
async def system_status():
    """Detailed system status."""
    return {
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat(),
        "stats": {
            "regions": Region.objects.filter(enabled=True).count(),
            "surfaces": TrendSurface.objects.filter(enabled=True).count(),
            "total_items": TrendItem.objects.count(),
            "items_last_24h": TrendItem.objects.filter(
                collected_at__gte=datetime.utcnow() - timedelta(days=1)
            ).count(),
        },
        "translation": {
            "enabled": TranslationSettings.objects.first().translation_enabled,
            "provider": TranslationSettings.objects.first().default_provider,
            "canonical_locale": TranslationSettings.objects.first().canonical_locale_for_analysis,
            "pending_translations": TrendItemTranslation.objects.filter(
                status='pending'
            ).count(),
        }
    }
```

### 3. Get Trends (ENHANCED)

**NEW: bucket filter, canonical_text fields**

```python
@app.get("/api/v1/trends")
async def get_trends(
    region: str,
    since: Optional[datetime] = None,
    bucket: Optional[str] = None,  # NEW
    locales: Optional[str] = None,
    surface: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """Get trending items with translations."""
    if since is None:
        since = datetime.utcnow() - timedelta(days=1)

    # Build query
    query = TrendItem.objects.filter(
        region__key=region,
        collected_at__gte=since
    )

    if surface:
        query = query.filter(surface__key=surface)

    # NEW: Filter by bucket
    if bucket:
        query = query.filter(bucket=bucket)

    total = query.count()

    # Get items with translations
    items = query.select_related('region', 'surface').prefetch_related(
        'translations'
    ).order_by('-collected_at')[offset:offset+limit]

    # Parse requested locales (default to canonical + region default)
    if locales:
        requested_locales = locales.split(',')
    else:
        requested_locales = ['en-US']  # Always include canonical

    # Format response
    trends = []
    for item in items:
        # Get completed translations
        translations_dict = {}
        for trans in item.translations.filter(
            locale__in=requested_locales,
            status='complete'
        ):
            translations_dict[trans.locale] = {
                "title": trans.title,
                "description": trans.description
            }

        # NEW: Compute canonical_text fields
        canonical_title = item.title_original
        canonical_description = item.description_original

        # Prefer en-US translation if available
        if 'en-US' in translations_dict:
            canonical_title = translations_dict['en-US']['title']
            canonical_description = translations_dict['en-US']['description']

        trends.append({
            "id": item.id,
            "region": item.region.key,
            "surface": item.surface.key,
            "bucket": item.bucket,  # NEW
            "platform": item.surface.platform,
            "url": item.url,
            "original_locale": item.original_locale,

            # NEW: Canonical text for analysis
            "canonical_title": canonical_title,
            "canonical_description": canonical_description,

            # Ranking data
            "rank_position": item.rank_position,  # NEW
            "engagement_signals": item.engagement_signals,  # NEW

            # Timestamps
            "published_at": item.published_at,
            "collected_at": item.collected_at,

            # Translations
            "translations": translations_dict,

            # Raw data
            "raw_payload": item.raw_payload
        })

    return {
        "trends": trends,
        "meta": {
            "count": len(trends),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
            "filters": {
                "region": region,
                "bucket": bucket,
                "since": since.isoformat()
            }
        }
    }
```

---

## 🎨 Enhanced Django Admin

### 1. TranslationSettings Admin

```python
@admin.register(TranslationSettings)
class TranslationSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Master Controls', {
            'fields': ('translation_enabled',)
        }),
        ('Provider Configuration', {
            'fields': ('default_provider',)
        }),
        ('Canonical Language Strategy', {
            'fields': (
                'canonical_locale_for_analysis',
                'force_canonical_translation',
            ),
            'description': (
                'Canonical locale (en-US) is used for cross-regional trend analysis. '
                'When force_canonical_translation is enabled, all non-English items '
                'will be automatically translated to English.'
            )
        }),
        ('Additional Locales', {
            'fields': ('enabled_locales',),
            'description': 'Optional additional locales for localization (beyond canonical en-US)'
        }),
        ('Rate Limiting', {
            'fields': ('max_chars_per_request',),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = ['created_at', 'updated_at']

    def has_add_permission(self, request):
        # Only allow one TranslationSettings instance
        return not TranslationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Don't allow deleting the settings
        return False
```

### 2. TrendItem Admin with Filters

```python
@admin.register(TrendItem)
class TrendItemAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'title_snippet',
        'region',
        'platform',
        'bucket',  # NEW
        'rank_position',  # NEW
        'original_locale',
        'canonical_status',  # NEW
        'collected_at'
    ]

    list_filter = [
        'region',
        'surface__platform',
        'bucket',  # NEW
        'original_locale',
        ('collected_at', admin.DateFieldListFilter),
        'canonical_translation_status',  # NEW custom filter
    ]

    search_fields = ['title_original', 'url']
    readonly_fields = [
        'region',
        'surface',
        'canonical_hash',
        'title_original',
        'description_original',
        'original_locale',
        'url',
        'rank_position',
        'engagement_signals',
        'bucket',
        'published_at',
        'collected_at',
        'raw_payload'
    ]

    actions = ['requeue_canonical_translation']  # NEW

    def title_snippet(self, obj):
        """Show truncated title."""
        return obj.title_original[:50] + '...' if len(obj.title_original) > 50 else obj.title_original
    title_snippet.short_description = 'Title'

    def platform(self, obj):
        """Show platform name."""
        return obj.surface.platform

    def canonical_status(self, obj):
        """Show if en-US translation exists."""
        en_translation = obj.translations.filter(
            locale='en-US',
            status='complete'
        ).first()

        if obj.original_locale == 'en-US':
            return '✅ Original English'
        elif en_translation:
            return '✅ Translated'
        else:
            pending = obj.translations.filter(locale='en-US', status='pending').exists()
            if pending:
                return '⏳ Pending'
            else:
                return '❌ Missing'
    canonical_status.short_description = 'English (Canonical)'

    def requeue_canonical_translation(self, request, queryset):
        """Requeue items for en-US translation."""
        count = 0
        for item in queryset:
            if item.original_locale != 'en-US':
                translation, created = TrendItemTranslation.objects.get_or_create(
                    item=item,
                    locale='en-US',
                    defaults={'status': 'pending'}
                )
                if not created and translation.status == 'failed':
                    translation.status = 'pending'
                    translation.error_message = None
                    translation.save()
                count += 1

        self.message_user(
            request,
            f'Requeued {count} items for English (canonical) translation.'
        )
    requeue_canonical_translation.short_description = "Requeue canonical (en-US) translation"
```

---

## 📝 Updated README Section

Add this section to README.md:

```markdown
## Canonical Analysis Language

### Why English (en-US)?

The system treats **English as the canonical language** for trend analysis:

✅ **Cross-regional comparison**: Compare trends from Japan, Korea, China in one language
✅ **ML/clustering**: Machine learning models work best with consistent language
✅ **Ranking algorithms**: Normalize text for proper comparison

### How It Works

1. **Collect** item in original language (preserved for authenticity)
2. **Translate** to English (en-US) automatically - REQUIRED for analysis
3. **Optionally** translate to other locales for localization

**Example:**
```
Weibo post (zh-Hans): "科技公司推出新产品"
  ↓ Automatic translation
Canonical (en-US): "Tech Company Launches New Product"  ← Used for analysis
  ↓ Optional additional translations
Spanish (es-ES): "Empresa tecnológica lanza nuevo producto"
French (fr-FR): "Entreprise technologique lance nouveau produit"
```

### Configuration

**Django Admin → Translation Settings:**
- Canonical Locale: `en-US` (locked, cannot change)
- Force Canonical Translation: ✓ (always translate to English)
- Additional Locales: `es-ES`, `fr-FR` (optional)

### API Usage

**Get trends with canonical English titles:**
```bash
curl "http://localhost:8000/api/v1/trends?region=jp"
```

**Response includes canonical_title:**
```json
{
  "trends": [
    {
      "original_locale": "ja-JP",
      "canonical_title": "New Technology Announced",  ← English for analysis
      "translations": {
        "en-US": {
          "title": "New Technology Announced"
        }
      }
    }
  ]
}
```
```

---

## Summary of Changes

### Data Model
- ✅ Added `bucket` to TrendSurface and TrendItem
- ✅ Added `rank_position` to TrendItem
- ✅ Added `engagement_signals` to TrendItem
- ✅ Added `canonical_locale_for_analysis` to TranslationSettings
- ✅ Added `force_canonical_translation` to TranslationSettings

### Translation Logic
- ✅ Priority-based translation (canonical first)
- ✅ Always translate to en-US for analysis
- ✅ Graceful degradation if translation fails

### API
- ✅ Added `/health` endpoint
- ✅ Added `/api/v1/status` endpoint
- ✅ Added `bucket` filter to `/api/v1/trends`
- ✅ Added `canonical_title` and `canonical_description` fields

### Django Admin
- ✅ Enhanced TranslationSettings admin
- ✅ Added canonical translation status to TrendItem list
- ✅ Added "Requeue canonical translation" action
- ✅ Added bucket filters

**All requirements from /tmp/t4 are now incorporated!**
