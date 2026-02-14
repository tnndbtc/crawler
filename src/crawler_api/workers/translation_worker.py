"""
Translation worker - Async translation with canonical-first priority.

From REQUIREMENTS-MASTER.md and /tmp/t9:
- Translation NEVER blocks collection (async)
- Canonical locale (en-US) always attempted for non-English items
- force_canonical_translation ensures all items get English version
- Processes pending translations using DeepL or OpenAI
- Updates TrendItemTranslation status

Product constraint (from /tmp/t9):
- Translation is ASYNC, never blocks ingestion
- Feed can show original content, upgrade to translated later
- Canonical en-US enables cross-regional analysis
"""

import os
import sys
import asyncio
import logging
from typing import List, Optional

import django
from django.utils import timezone
from asgiref.sync import sync_to_async

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import (
    TrendItem,
    TrendItemTranslation,
    TranslationSettings
)
from crawler_api.translation.providers import get_provider

logger = logging.getLogger(__name__)


# Configuration from environment
TRANSLATION_WORKER_POLL_INTERVAL = int(os.getenv('TRANSLATION_WORKER_POLL_INTERVAL', '30'))


async def create_missing_canonical_translations():
    """
    Create pending en-US translations for items that need them.

    From /tmp/t4 and /tmp/t9:
    - If force_canonical_translation=True, all non-English items get en-US translation
    - Creates TrendItemTranslation records in 'pending' status
    - Canonical-first priority (en-US before other locales)
    """
    settings = await sync_to_async(TranslationSettings.get_settings)()

    if not settings.translation_enabled:
        logger.debug("Translation disabled, skipping")
        return 0

    if not settings.force_canonical_translation:
        logger.debug("force_canonical_translation disabled, skipping")
        return 0

    canonical_locale = settings.canonical_locale_for_analysis

    # Find items without canonical translation
    # - original_locale != en-US (non-English items)
    # - No existing en-US translation
    items_needing_translation = await sync_to_async(list)(
        TrendItem.objects.filter(
            original_locale__ne=canonical_locale
        ).exclude(
            translations__locale=canonical_locale
        )[:100]  # Batch size
    )

    created_count = 0
    for item in items_needing_translation:
        try:
            # Create pending translation
            await sync_to_async(TrendItemTranslation.objects.create)(
                item=item,
                locale=canonical_locale,
                title='',  # Will be filled by translation
                description='',
                status='pending',
                provider=settings.default_provider
            )
            created_count += 1
        except django.db.utils.IntegrityError:
            # Already exists (race condition)
            continue

    if created_count > 0:
        logger.info(f"Created {created_count} pending canonical translation(s)")

    return created_count


async def process_pending_translations(batch_size: int = 10):
    """
    Process pending translations.

    From REQUIREMENTS-MASTER.md:
    1. Get pending translations (limit batch_size)
    2. Update status to 'running'
    3. Call translation API
    4. Update status to 'complete' or 'failed'
    5. Store translated text and error message

    Provider selection (from /tmp/t4):
    - Uses TrendItemTranslation.provider
    - Falls back to default_provider
    """
    settings = await sync_to_async(TranslationSettings.get_settings)()

    if not settings.translation_enabled:
        return 0

    # Get pending translations
    pending_translations = await sync_to_async(list)(
        TrendItemTranslation.objects.filter(
            status='pending'
        ).select_related('item')[:batch_size]
    )

    if not pending_translations:
        logger.debug("No pending translations")
        return 0

    logger.info(f"Processing {len(pending_translations)} pending translation(s)")

    processed_count = 0
    for translation in pending_translations:
        try:
            # Update status to running
            translation.status = 'running'
            await sync_to_async(translation.save)(update_fields=['status'])

            # Get provider
            provider_name = translation.provider or settings.default_provider
            provider = get_provider(provider_name)

            # Extract text to translate
            source_locale = translation.item.original_locale
            target_locale = translation.locale
            title = translation.item.title_original
            description = translation.item.description_original or ''

            # Translate title (always)
            translated_title = await provider.translate(
                text=title,
                source_locale=source_locale,
                target_locale=target_locale
            )

            # Translate description (if exists)
            translated_description = ''
            if description:
                translated_description = await provider.translate(
                    text=description,
                    source_locale=source_locale,
                    target_locale=target_locale
                )

            # Success
            translation.title = translated_title
            translation.description = translated_description
            translation.status = 'complete'
            translation.translated_at = timezone.now()
            translation.error_message = None
            await sync_to_async(translation.save)()

            logger.info(
                f"Translated item #{translation.item.id} "
                f"{source_locale} → {target_locale} "
                f"(provider: {provider_name})"
            )

            processed_count += 1

        except Exception as e:
            # Translation failed
            error_msg = str(e)
            translation.status = 'failed'
            translation.error_message = error_msg
            await sync_to_async(translation.save)(
                update_fields=['status', 'error_message']
            )

            logger.error(
                f"Translation failed for item #{translation.item.id}: {error_msg}",
                exc_info=True
            )

    return processed_count


async def run_worker_loop():
    """
    Main worker loop.

    From REQUIREMENTS-MASTER.md:
    1. Create missing canonical translations (canonical-first priority)
    2. Process pending translations
    3. Sleep TRANSLATION_WORKER_POLL_INTERVAL
    4. Repeat forever (worker never crashes)
    """
    logger.info("Translation worker started")
    logger.info(f"TRANSLATION_WORKER_POLL_INTERVAL: {TRANSLATION_WORKER_POLL_INTERVAL}")

    while True:
        try:
            # 1. Create missing canonical translations
            created = await create_missing_canonical_translations()

            # 2. Process pending translations
            processed = await process_pending_translations(batch_size=10)

            if created > 0 or processed > 0:
                logger.info(f"Created: {created}, Processed: {processed}")

        except Exception as e:
            # Never crash the worker loop
            logger.error(f"Worker loop error: {e}", exc_info=True)

        # Sleep before next poll
        await asyncio.sleep(TRANSLATION_WORKER_POLL_INTERVAL)


def main():
    """Entry point for translation worker."""
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Translation worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
