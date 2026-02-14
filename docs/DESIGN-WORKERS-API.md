# Trend Crawler Architecture - Workers & API

[← Surfaces](./DESIGN-SURFACES.md) | **Part 4 of 4** | [Back to Overview](./DESIGN-OVERVIEW.md)

---

## Worker Architecture

The system uses two async workers with simple polling (no queue infrastructure needed):

1. **Surface Runner Worker**: Collects trend items from configured surfaces
2. **Translation Worker**: Enriches items with translations

Both workers are:
- **Stateless**: Can be stopped/started anytime
- **Fault-tolerant**: Errors don't crash entire worker
- **Self-scheduling**: Each surface manages its own schedule

---

## 1. Surface Runner Worker

**Purpose**: Poll enabled surfaces and collect trend items.

**Location**: `scripts/run_surface_worker.sh`

### Algorithm

```python
import asyncio
from datetime import datetime, timedelta
from crawler_admin.models import TrendSurface, TrendItem
from crawler_api.surfaces.registry import get_collector

async def run_surface_worker():
    """Main worker loop."""
    while True:
        now = datetime.utcnow()

        # Find surfaces due for collection
        due_surfaces = TrendSurface.objects.filter(
            enabled=True,
            next_run_at__lte=now
        ).select_related('region')

        for surface in due_surfaces:
            try:
                # Load collector dynamically
                collector = get_collector(surface.entrypoint)

                # Collect items
                items, next_cursor = await collector(
                    config=surface.config_json,
                    cursor=surface.last_cursor,
                    limit=surface.max_items_per_run
                )

                # Store items with deduplication
                for item_dict in items:
                    canonical_hash = compute_hash(
                        item_dict['title'],
                        item_dict['url']
                    )

                    # Skip if already exists
                    if TrendItem.objects.filter(
                        canonical_hash=canonical_hash
                    ).exists():
                        continue

                    TrendItem.objects.create(
                        region=surface.region,
                        surface=surface,
                        external_id=item_dict.get('external_id'),
                        title_original=item_dict['title'],
                        description_original=item_dict.get('description'),
                        original_locale=item_dict['locale'],
                        url=item_dict['url'],
                        published_at=item_dict.get('published_at'),
                        raw_payload=item_dict['raw_payload'],
                        canonical_hash=canonical_hash,
                    )

                # Update surface state
                surface.last_cursor = next_cursor
                surface.last_run_at = now
                surface.next_run_at = now + timedelta(
                    seconds=surface.poll_interval_seconds
                )
                surface.last_run_error = None
                surface.save()

                logger.info(
                    f"Collected {len(items)} items from {surface.key}"
                )

            except Exception as e:
                # Log error but continue with other surfaces
                surface.last_run_error = str(e)
                surface.next_run_at = now + timedelta(seconds=300)  # Retry in 5 min
                surface.save()

                logger.error(
                    f"Error collecting from {surface.key}: {e}",
                    exc_info=True
                )

        # Sleep before next poll
        await asyncio.sleep(60)  # Check every minute

if __name__ == '__main__':
    asyncio.run(run_surface_worker())
```

### Key Features

✅ **Idempotent**: Deduplicates by canonical_hash
✅ **Fault-tolerant**: Errors in one surface don't crash entire worker
✅ **Self-scheduling**: Each surface tracks its own next_run_at
✅ **Respects limits**: max_items_per_run prevents runaway collection
✅ **Cursor support**: Handles pagination via cursor

### Error Handling

When a collector fails:
1. Error message stored in `surface.last_run_error`
2. Retry scheduled in 5 minutes
3. Worker continues with other surfaces
4. Admin can see errors in Django Admin

---

## 2. Translation Worker

**Purpose**: Translate collected items into enabled locales.

**Location**: `scripts/run_translation_worker.sh`

### Algorithm

```python
import asyncio
from datetime import datetime
from crawler_admin.models import (
    TrendItem,
    TrendItemTranslation,
    TranslationSettings
)
from crawler_api.translation.providers import get_provider

async def run_translation_worker():
    """Main translation worker loop."""
    while True:
        # Get translation settings
        settings = TranslationSettings.objects.filter(
            enabled=True,
            region=None  # Global settings
        ).first()

        if not settings:
            await asyncio.sleep(60)
            continue

        # Find items needing translation
        items = TrendItem.objects.exclude(
            original_locale__in=settings.enabled_locales
        ).filter(
            translations__isnull=True
        )[:100]  # Batch size

        # Get translation provider
        provider = get_provider(settings.default_provider)

        for item in items:
            for locale in settings.enabled_locales:
                # Skip if same as original
                if locale == item.original_locale:
                    continue

                # Create or get translation record
                translation, created = TrendItemTranslation.objects.get_or_create(
                    item=item,
                    locale=locale,
                    defaults={
                        'status': 'pending',
                        'provider': settings.default_provider
                    }
                )

                # Skip if already complete
                if translation.status == 'complete':
                    continue

                try:
                    # Mark as running
                    translation.status = 'running'
                    translation.save()

                    # Translate title
                    translated_title = await provider.translate(
                        text=item.title_original,
                        source_locale=item.original_locale,
                        target_locale=locale
                    )

                    # Translate description if exists
                    translated_desc = None
                    if item.description_original:
                        translated_desc = await provider.translate(
                            text=item.description_original,
                            source_locale=item.original_locale,
                            target_locale=locale
                        )

                    # Update translation
                    translation.title = translated_title
                    translation.description = translated_desc
                    translation.status = 'complete'
                    translation.translated_at = datetime.utcnow()
                    translation.save()

                    logger.info(
                        f"Translated item {item.id} to {locale}"
                    )

                except Exception as e:
                    # Mark as failed
                    translation.status = 'failed'
                    translation.error_message = str(e)
                    translation.save()

                    logger.error(
                        f"Translation failed for item {item.id} to {locale}: {e}",
                        exc_info=True
                    )

        # Sleep before next batch
        await asyncio.sleep(30)

if __name__ == '__main__':
    asyncio.run(run_translation_worker())
```

### Key Features

✅ **Non-blocking**: Never delays ingestion
✅ **Batch processing**: Translates in chunks
✅ **Provider-agnostic**: Supports DeepL and OpenAI
✅ **Error handling**: Failed translations marked but don't block others
✅ **Resumable**: Can restart without losing progress

---

## Translation Providers

### Provider Interface

```python
# src/crawler_api/translation/providers.py

from typing import Protocol

class TranslationProvider(Protocol):
    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str
    ) -> str:
        """Translate text from source to target locale."""
        ...
```

### DeepL Provider

```python
class DeepLProvider:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api-free.deepl.com/v2/translate"

    async def translate(self, text, source_locale, target_locale):
        # Map our locales to DeepL language codes
        source_lang = self._map_locale(source_locale)
        target_lang = self._map_locale(target_locale)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.base_url,
                data={
                    "auth_key": self.api_key,
                    "text": text,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                }
            )
            response.raise_for_status()
            result = response.json()
            return result["translations"][0]["text"]

    def _map_locale(self, locale: str) -> str:
        """Map our locale codes to DeepL language codes."""
        mapping = {
            "en-US": "EN",
            "ja-JP": "JA",
            "ko-KR": "KO",
            "zh-Hans": "ZH",
            # ... more mappings
        }
        return mapping.get(locale, locale[:2].upper())
```

### OpenAI Provider

```python
class OpenAIProvider:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = openai.AsyncOpenAI(api_key=api_key)

    async def translate(self, text, source_locale, target_locale):
        response = await self.client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": f"Translate from {source_locale} to {target_locale}."
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        )
        return response.choices[0].message.content
```

### Provider Registry

```python
def get_provider(name: str) -> TranslationProvider:
    """Get translation provider by name."""
    if name == "deepl":
        api_key = os.getenv("DEEPL_API_KEY")
        return DeepLProvider(api_key)
    elif name == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        return OpenAIProvider(api_key)
    else:
        raise ValueError(f"Unknown provider: {name}")
```

---

## FastAPI Read APIs

### Endpoints

#### 1. List Regions

```python
@app.get("/api/v1/regions")
async def list_regions():
    """Get all enabled regions."""
    regions = Region.objects.filter(enabled=True)

    return {
        "regions": [
            {
                "key": r.key,
                "name": r.name,
                "default_locale": r.default_locale,
                "surfaces_count": r.trendsurface_set.filter(enabled=True).count(),
            }
            for r in regions
        ]
    }
```

#### 2. List Surfaces

```python
@app.get("/api/v1/surfaces")
async def list_surfaces(region: Optional[str] = None):
    """Get enabled surfaces, optionally filtered by region."""
    query = TrendSurface.objects.filter(enabled=True)

    if region:
        query = query.filter(region__key=region)

    surfaces = query.select_related('region')

    return {
        "surfaces": [
            {
                "key": s.key,
                "region": s.region.key,
                "surface_type": s.surface_type,
                "platform": s.platform,
                "last_run_at": s.last_run_at,
                "next_run_at": s.next_run_at,
                "items_count": s.trenditem_set.count(),
            }
            for s in surfaces
        ]
    }
```

#### 3. Get Trends

```python
@app.get("/api/v1/trends")
async def get_trends(
    region: str,
    since: Optional[datetime] = None,
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

    # Get total count
    total = query.count()

    # Get items with translations
    items = query.select_related('region', 'surface').prefetch_related(
        'translations'
    )[offset:offset+limit]

    # Parse requested locales
    requested_locales = locales.split(',') if locales else [items[0].region.default_locale]

    # Format response
    trends = []
    for item in items:
        translations_dict = {}
        for trans in item.translations.filter(locale__in=requested_locales, status='complete'):
            translations_dict[trans.locale] = {
                "title": trans.title,
                "description": trans.description
            }

        trends.append({
            "id": item.id,
            "region": item.region.key,
            "surface": item.surface.key,
            "url": item.url,
            "original_locale": item.original_locale,
            "published_at": item.published_at,
            "collected_at": item.collected_at,
            "translations": translations_dict,
            "raw_payload": item.raw_payload
        })

    return {
        "trends": trends,
        "meta": {
            "count": len(trends),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total
        }
    }
```

---

## Deployment

### Running Workers

**Surface Worker:**
```bash
#!/bin/bash
# scripts/run_surface_worker.sh

cd "$(dirname "$0")/.."
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=crawler_admin.settings

python -m crawler_api.workers.surface_worker
```

**Translation Worker:**
```bash
#!/bin/bash
# scripts/run_translation_worker.sh

cd "$(dirname "$0")/.."
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=crawler_admin.settings
export DEEPL_API_KEY=your_key_here

python -m crawler_api.workers.translation_worker
```

### Process Management

**Using systemd:**
```ini
# /etc/systemd/system/crawler-surface-worker.service

[Unit]
Description=Crawler Surface Worker
After=network.target

[Service]
User=crawler
WorkingDirectory=/opt/crawler
ExecStart=/opt/crawler/scripts/run_surface_worker.sh
Restart=always

[Install]
WantedBy=multi-user.target
```

**Using Docker Compose:**
```yaml
version: '3.8'
services:
  surface-worker:
    build: .
    command: python -m crawler_api.workers.surface_worker
    environment:
      - DJANGO_SETTINGS_MODULE=crawler_admin.settings
    restart: always

  translation-worker:
    build: .
    command: python -m crawler_api.workers.translation_worker
    environment:
      - DEEPL_API_KEY=${DEEPL_API_KEY}
    restart: always

  api:
    build: .
    command: uvicorn crawler_api.main:app --host 0.0.0.0
    ports:
      - "8000:8000"
```

---

**Document Version**: 1.0
**Last Updated**: 2024-01-15
**Previous**: [← Surfaces](./DESIGN-SURFACES.md) | [Back to Overview →](./DESIGN-OVERVIEW.md)
