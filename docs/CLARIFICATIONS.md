# Design Clarifications & Enhancements

**Date**: 2024-01-15
**Purpose**: Address key questions and enhance design based on requirements

---

## ✅ Yes: Django Admin for Managing Sources

### What You Get

**Django Admin provides a complete UI for configuring crawl sources WITHOUT writing code.**

### How It Works

1. **Login to Django Admin** (http://localhost:8000/admin)

2. **Add/Edit Regions** via UI:
   ```
   Region Management
   ├── Add Region (button)
   ├── List Regions (table with filters)
   └── Edit Region (click any row)
   ```

3. **Add/Edit Trend Surfaces** via UI:
   ```
   Trend Surface Management
   ├── Add Surface (button)
   │   ├── Select Region (dropdown)
   │   ├── Set Key (text input)
   │   ├── Choose Surface Type (ranking/sampler/search/news)
   │   ├── Set Platform (text input)
   │   ├── Set Entrypoint (dropdown or text)
   │   ├── Configure Poll Interval (number input)
   │   ├── Set Max Items (number input)
   │   ├── Edit Config JSON (JSON editor)
   │   └── Enable/Disable (checkbox)
   ├── List Surfaces (table with filters by region/platform/type)
   ├── Edit Surface (click any row)
   └── Delete Surface (with confirmation)
   ```

4. **View Collected Items** (Read-only):
   ```
   Trend Items Management
   ├── List Items (filtered by region/surface/date)
   ├── View Item Details (click any row)
   └── See Translation Status
   ```

### Example Workflow: Adding Reddit Source

**Via Django Admin UI (no code needed):**

1. Navigate to "Trend Surfaces" section
2. Click "Add Trend Surface"
3. Fill in form:
   - Region: `us` (select from dropdown)
   - Key: `reddit_technology`
   - Surface Type: `ranking`
   - Platform: `reddit`
   - Entrypoint: `crawler_surfaces.reddit_hot:collect` (select from dropdown)
   - Poll Interval: `3600` (seconds)
   - Max Items: `200`
   - Config JSON:
     ```json
     {
       "subreddit": "technology",
       "locale": "en-US"
     }
     ```
   - Enabled: ✓

4. Click "Save"
5. Worker automatically picks it up and starts crawling within 1 minute

**No restart needed. No code deployment needed.**

### Django Admin Features

✅ **User-Friendly Forms**: Dropdowns, date pickers, JSON editor
✅ **Validation**: Prevents invalid configurations
✅ **Search & Filters**: Find surfaces by region, platform, status
✅ **Bulk Actions**: Enable/disable multiple surfaces at once
✅ **Audit Trail**: See who changed what and when
✅ **Permissions**: Control who can add/edit/delete sources
✅ **Real-time Status**: See last run time, errors, next scheduled run

### Admin Customization Example

```python
# src/crawler_admin/admin.py

from django.contrib import admin
from .models import Region, TrendSurface, TrendItem

@admin.register(TrendSurface)
class TrendSurfaceAdmin(admin.ModelAdmin):
    list_display = [
        'key',
        'region',
        'surface_type',
        'platform',
        'enabled',
        'last_run_at',
        'next_run_at',
        'status_indicator'
    ]
    list_filter = ['region', 'surface_type', 'platform', 'enabled']
    search_fields = ['key', 'platform']
    readonly_fields = ['last_run_at', 'next_run_at', 'last_run_error']

    fieldsets = (
        ('Basic Info', {
            'fields': ('region', 'key', 'surface_type', 'platform')
        }),
        ('Collector Config', {
            'fields': ('entrypoint', 'config_json')
        }),
        ('Schedule', {
            'fields': ('enabled', 'poll_interval_seconds', 'max_items_per_run')
        }),
        ('Status (Read-only)', {
            'fields': ('last_run_at', 'next_run_at', 'last_cursor', 'last_run_error'),
            'classes': ('collapse',)
        }),
    )

    def status_indicator(self, obj):
        """Show visual status indicator."""
        if not obj.enabled:
            return '🔴 Disabled'
        elif obj.last_run_error:
            return '⚠️ Error'
        elif obj.last_run_at:
            return '✅ Running'
        else:
            return '⏳ Pending'
    status_indicator.short_description = 'Status'

    actions = ['enable_surfaces', 'disable_surfaces', 'reset_errors']

    def enable_surfaces(self, request, queryset):
        queryset.update(enabled=True)
    enable_surfaces.short_description = "Enable selected surfaces"

    def disable_surfaces(self, request, queryset):
        queryset.update(enabled=False)
    disable_surfaces.short_description = "Disable selected surfaces"
```

---

## ✅ Yes: Translation Layer (DeepL → OpenAI Fallback)

### Architecture

**Default**: DeepL (higher quality, faster)
**Fallback**: OpenAI (when DeepL doesn't support locale or fails)

### Configuration via Django Admin

**Global Settings:**
```
Translation Settings
├── Default Provider: [DeepL ▼] (dropdown: DeepL, OpenAI)
├── Enabled Locales: [en-US, ja-JP, ko-KR, zh-Hans] (multi-select)
├── Enabled: ✓
└── API Keys (configured via environment variables)
```

**Region-Specific Override:**
```
Translation Settings (for China region)
├── Region: [China ▼]
├── Default Provider: [OpenAI ▼] (DeepL blocked in China)
├── Enabled Locales: [en-US, zh-Hant]
├── Enabled: ✓
```

### Environment Variables

```bash
# .env file
DEEPL_API_KEY=your_deepl_key_here
OPENAI_API_KEY=your_openai_key_here  # ← Your existing env var
```

### How Translation Selection Works

```python
# Automatic provider selection logic

def get_translation_provider(
    source_locale: str,
    target_locale: str,
    settings: TranslationSettings
) -> TranslationProvider:
    """
    Select best provider based on locale support and settings.
    """
    provider_name = settings.default_provider

    # Check if DeepL supports this locale pair
    if provider_name == 'deepl':
        if not deepl_supports_locale(source_locale, target_locale):
            logger.warning(
                f"DeepL doesn't support {source_locale}→{target_locale}, "
                f"falling back to OpenAI"
            )
            provider_name = 'openai'

    return get_provider(provider_name)
```

### DeepL Locale Support

DeepL officially supports:
- ✅ EN, DE, FR, ES, IT, NL, PL, PT, RU
- ✅ JA, ZH (Chinese)
- ❌ KO (Korean) - **Falls back to OpenAI**
- ❌ AR (Arabic) - **Falls back to OpenAI**

### Implementation

```python
# src/crawler_api/translation/providers.py

import os
import httpx
import openai

class DeepLProvider:
    def __init__(self):
        self.api_key = os.getenv('DEEPL_API_KEY')
        self.base_url = "https://api-free.deepl.com/v2/translate"

    async def translate(self, text, source_locale, target_locale):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.base_url,
                data={
                    "auth_key": self.api_key,
                    "text": text,
                    "source_lang": self._map_locale(source_locale),
                    "target_lang": self._map_locale(target_locale),
                }
            )
            response.raise_for_status()
            return response.json()["translations"][0]["text"]

class OpenAIProvider:
    def __init__(self):
        self.api_key = os.getenv('OPENAI_API_KEY')  # ← Uses your env var
        self.client = openai.AsyncOpenAI(api_key=self.api_key)

    async def translate(self, text, source_locale, target_locale):
        response = await self.client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a professional translator. "
                        f"Translate from {source_locale} to {target_locale}. "
                        f"Preserve meaning and tone. Return only the translation."
                    )
                },
                {"role": "user", "content": text}
            ],
            temperature=0.3  # Lower temperature for consistent translations
        )
        return response.choices[0].message.content.strip()

def get_provider(name: str):
    """Factory function for providers."""
    if name == 'deepl':
        return DeepLProvider()
    elif name == 'openai':
        return OpenAIProvider()
    else:
        raise ValueError(f"Unknown provider: {name}")
```

---

## 🆕 ENHANCED: English as Translation Base for Trend Analysis

### The Problem

You're collecting trends from:
- Japan (ja-JP)
- Korea (ko-KR)
- China (zh-Hans)
- Saudi Arabia (ar-SA)
- etc.

**You want to analyze trends ACROSS regions in a common language (English).**

### The Solution

**Two-tier translation strategy:**

1. **Always translate to English (en-US)** for cross-regional analysis
2. **Optionally translate to other locales** for localization

### Updated Data Model

Add `TranslationPriority` to settings:

```python
class TranslationSettings(models.Model):
    region = models.ForeignKey(Region, null=True, blank=True)
    default_provider = models.CharField(...)

    # NEW: Base locale for analysis
    base_locale = models.CharField(
        max_length=10,
        default='en-US',
        help_text="Primary locale for cross-regional trend analysis"
    )

    # NEW: Additional locales for localization
    additional_locales = models.JSONField(
        default=list,
        help_text="Other locales to translate to (optional)"
    )

    enabled = models.BooleanField(default=True)
```

### Translation Priority Logic

```python
# Translation worker logic (UPDATED)

async def translate_item(item: TrendItem, settings: TranslationSettings):
    """
    Translate item with priority:
    1. Base locale (en-US) - ALWAYS translate first
    2. Additional locales - Translate after base
    """

    # Step 1: ALWAYS translate to base locale (English) first
    if item.original_locale != settings.base_locale:
        await create_translation(
            item=item,
            target_locale=settings.base_locale,
            priority='high'  # Process immediately
        )

    # Step 2: Translate to additional locales (lower priority)
    for locale in settings.additional_locales:
        if locale != item.original_locale and locale != settings.base_locale:
            await create_translation(
                item=item,
                target_locale=locale,
                priority='normal'  # Process after base translations
            )
```

### Example Configuration

**Scenario**: You're crawling Japanese, Korean, and Chinese sources. You want everything in English for analysis, plus some items in Spanish for your Spanish-speaking users.

**Configuration via Django Admin:**
```
Translation Settings
├── Base Locale: en-US (for trend analysis)
├── Additional Locales: [es-ES, fr-FR] (for localization)
├── Default Provider: DeepL
└── Enabled: ✓
```

**What happens:**

1. Japanese item collected → Translate to en-US **immediately** (high priority)
2. Korean item collected → Translate to en-US **immediately** (high priority)
3. Chinese item collected → Translate to en-US **immediately** (high priority)
4. After all en-US translations → Translate to es-ES, fr-FR (normal priority)

### Analysis Endpoint

**New endpoint for cross-regional trend analysis:**

```python
@app.get("/api/v1/trends/analyze")
async def analyze_trends(
    since: datetime,
    regions: Optional[str] = None,  # Comma-separated: "us,jp,kr"
    limit: int = 100
):
    """
    Get trends across regions in English for analysis.

    Returns all items with English translations, regardless of original locale.
    """

    query = TrendItem.objects.filter(
        collected_at__gte=since,
        translations__locale='en-US',  # ONLY items with English translation
        translations__status='complete'
    )

    if regions:
        region_keys = regions.split(',')
        query = query.filter(region__key__in=region_keys)

    items = query.select_related('region', 'surface').prefetch_related(
        Prefetch(
            'translations',
            queryset=TrendItemTranslation.objects.filter(
                locale='en-US',
                status='complete'
            )
        )
    )[:limit]

    return {
        "trends": [
            {
                "id": item.id,
                "region": item.region.key,
                "surface": item.surface.key,
                "platform": item.surface.platform,
                "url": item.url,
                "original_locale": item.original_locale,
                "original_title": item.title_original,
                "english_title": item.translations.first().title,  # English version
                "english_description": item.translations.first().description,
                "collected_at": item.collected_at,
                "raw_payload": item.raw_payload
            }
            for item in items
        ],
        "meta": {
            "count": len(items),
            "base_locale": "en-US",
            "regions": regions.split(',') if regions else "all"
        }
    }
```

### Usage Example

**Analyze what's trending in Asia (all in English):**

```bash
curl "http://localhost:8000/api/v1/trends/analyze?regions=jp,kr,cn&since=2024-01-15T00:00:00Z"
```

**Response:**
```json
{
  "trends": [
    {
      "region": "jp",
      "original_locale": "ja-JP",
      "original_title": "新しいAI技術が発表される",
      "english_title": "New AI Technology Announced",
      "platform": "yahoo_jp"
    },
    {
      "region": "kr",
      "original_locale": "ko-KR",
      "original_title": "새로운 스마트폰 출시",
      "english_title": "New Smartphone Released",
      "platform": "naver"
    },
    {
      "region": "cn",
      "original_locale": "zh-Hans",
      "original_title": "科技公司推出新产品",
      "english_title": "Tech Company Launches New Product",
      "platform": "weibo"
    }
  ],
  "meta": {
    "base_locale": "en-US",
    "regions": ["jp", "kr", "cn"]
  }
}
```

Now you can analyze trends across regions in a common language!

---

## Summary of Design Enhancements

### ✅ Already in Design

1. **Django Admin for source management** - Fully documented
2. **Translation layer with DeepL/OpenAI** - Fully documented
3. **Configurable translation settings** - Fully documented

### 🆕 Added in This Document

1. **English as base locale strategy** - NEW
2. **Priority-based translation** - NEW
3. **Cross-regional analysis endpoint** - NEW
4. **Visual admin UI examples** - ENHANCED
5. **Provider fallback logic** - CLARIFIED

---

## Updated Configuration Example

```python
# Complete translation configuration in Django Admin

TranslationSettings.objects.create(
    region=None,  # Global settings
    base_locale='en-US',  # PRIMARY: For trend analysis
    additional_locales=['es-ES', 'fr-FR', 'de-DE'],  # OPTIONAL: For localization
    default_provider='deepl',  # Use DeepL by default
    enabled=True
)

# Region-specific override for Korea (DeepL doesn't support Korean)
TranslationSettings.objects.create(
    region=Region.objects.get(key='kr'),
    base_locale='en-US',
    additional_locales=[],
    default_provider='openai',  # Use OpenAI because DeepL lacks Korean
    enabled=True
)
```

---

**Next Steps**: Should I update the main design documents to incorporate these clarifications?
