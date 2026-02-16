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
from crawler_api.translation.providers import get_provider, TranslationError

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
    # - SKIP English variants (en-GB, en-AU, etc.) - they don't need translation
    items_needing_translation = await sync_to_async(list)(
        TrendItem.objects.exclude(
            original_locale=canonical_locale
        ).exclude(
            original_locale__startswith='en-'  # Skip all English variants
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
            logger.debug(
                f"Created pending translation: item #{item.id} "
                f"{item.original_locale} → {canonical_locale} | "
                f"Provider: {settings.default_provider}"
            )
        except django.db.utils.IntegrityError:
            # Already exists (race condition)
            continue

    if created_count > 0:
        logger.info(
            f"Created {created_count} pending canonical translation(s) "
            f"with provider={settings.default_provider}"
        )

    return created_count


async def create_missing_additional_locale_translations():
    """
    Create pending translations for enabled_locales (e.g., zh-Hans).

    Feature A (from /tmp/t3):
    - Supports bidirectional translation (en-US ↔ zh-Hans)
    - Creates translations for all enabled_locales beyond canonical en-US
    - Skip if original_locale matches target locale (already native)
    """
    settings = await sync_to_async(TranslationSettings.get_settings)()

    if not settings.translation_enabled:
        return 0

    enabled_locales = settings.enabled_locales or []
    if not enabled_locales:
        logger.debug("No additional locales enabled, skipping")
        return 0

    total_created = 0

    for target_locale in enabled_locales:
        # Find items without this locale translation
        # - original_locale != target_locale (skip if already native)
        # - No existing translation for this locale
        items_needing_translation = await sync_to_async(list)(
            TrendItem.objects.exclude(
                original_locale=target_locale
            ).exclude(
                translations__locale=target_locale
            )[:100]  # Batch size per locale
        )

        created_count = 0
        for item in items_needing_translation:
            try:
                # Create pending translation
                await sync_to_async(TrendItemTranslation.objects.create)(
                    item=item,
                    locale=target_locale,
                    title='',  # Will be filled by translation
                    description='',
                    status='pending',
                    provider=settings.default_provider
                )
                created_count += 1
                logger.debug(
                    f"Created pending translation: item #{item.id} "
                    f"{item.original_locale} → {target_locale} | "
                    f"Provider: {settings.default_provider}"
                )
            except django.db.utils.IntegrityError:
                # Already exists (race condition)
                continue

        if created_count > 0:
            logger.info(
                f"Created {created_count} pending {target_locale} translation(s) "
                f"with provider={settings.default_provider}"
            )
            total_created += created_count

    return total_created


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
            logger.info(
                f"📝 Processing item #{translation.item.id}: "
                f"{translation.item.original_locale} → {translation.locale} | "
                f"Provider: {provider_name}"
            )
            provider = get_provider(provider_name)

            # Extract text to translate
            source_locale = translation.item.original_locale
            target_locale = translation.locale
            title = translation.item.title_original
            description = translation.item.description_original or ''

            # Skip translation for English variants (en-GB, en-AU, etc. → en-US)
            # Just copy the original text
            if source_locale.startswith('en-') and target_locale.startswith('en-'):
                logger.info(
                    f"Skipping translation for English variant: {source_locale} → {target_locale} "
                    f"(item #{translation.item.id}), using original text"
                )
                translation.title = title
                translation.description = description
                translation.status = 'complete'
                translation.translated_at = timezone.now()
                translation.provider = 'none'  # No translation needed
                translation.error_message = None
                await sync_to_async(translation.save)()
                processed_count += 1
                continue

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
            translation.provider = provider_name  # Record which provider was used
            translation.error_message = None
            await sync_to_async(translation.save)()

            logger.info(
                f"Translated item #{translation.item.id} "
                f"{source_locale} → {target_locale} "
                f"(provider: {provider_name})"
            )

            processed_count += 1

        except TranslationError as e:
            # Translation provider failed (DeepL quota, auth, API error, etc.)
            # Fall back to argostranslate offline translation
            logger.warning(
                f"⚠️  Translation provider failed! Falling back to argostranslate offline translation. "
                f"Provider: {provider_name}, Error: {e}"
            )

            try:
                # Retry with argostranslate offline provider
                logger.info(f"Retrying translation for item #{translation.item.id} with argostranslate provider...")
                local_provider = get_provider("argostranslate")

                # Extract text again (already have it from above)
                source_locale = translation.item.original_locale
                target_locale = translation.locale
                title = translation.item.title_original
                description = translation.item.description_original or ''

                # Translate with argostranslate provider
                translated_title = await local_provider.translate(
                    text=title,
                    source_locale=source_locale,
                    target_locale=target_locale
                )

                translated_description = ''
                if description:
                    translated_description = await local_provider.translate(
                        text=description,
                        source_locale=source_locale,
                        target_locale=target_locale
                    )

                # Success with argostranslate provider
                translation.title = translated_title
                translation.description = translated_description
                translation.status = 'complete'
                translation.translated_at = timezone.now()
                translation.provider = 'argostranslate'  # Record that we used argostranslate fallback
                translation.error_message = None
                await sync_to_async(translation.save)()

                logger.info(
                    f"✓ Translated item #{translation.item.id} with argostranslate provider "
                    f"{source_locale} → {target_locale} (fallback from {provider_name})"
                )

                processed_count += 1

            except Exception as local_error:
                # Argostranslate translation also failed
                logger.error(
                    f"Argostranslate translation fallback failed for item #{translation.item.id}: {local_error}"
                )
                # Mark as failed since both primary provider and argostranslate failed
                translation.status = 'failed'
                translation.error_message = f"{provider_name} failed ({e}), argostranslate fallback also failed: {local_error}"
                await sync_to_async(translation.save)(
                    update_fields=['status', 'error_message']
                )

        except Exception as e:
            # Translation failed for other reasons
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


async def test_deepl_connectivity():
    """
    Test DeepL API connectivity on startup.

    Attempts a simple translation to verify:
    - API key is valid
    - Endpoint is reachable
    - Account has quota available
    """
    try:
        logger.info("🔍 Testing DeepL API connectivity...")

        settings = await sync_to_async(TranslationSettings.get_settings)()
        if settings.default_provider != 'deepl':
            logger.info(f"Default provider is '{settings.default_provider}', skipping DeepL test")
            return

        provider = get_provider('deepl')

        # Simple test translation: "Hello" from English to Spanish
        result = await provider.translate(
            text="Hello",
            source_locale="en-US",
            target_locale="es-ES"
        )

        logger.info(f"✅ DeepL API test successful! Test translation result: '{result}'")

        # Try to get usage stats if available
        if hasattr(provider, 'get_usage'):
            usage = await provider.get_usage()
            if usage:
                logger.info(
                    f"📊 DeepL usage stats: {usage.character.count}/{usage.character.limit} "
                    f"characters used ({usage.character.count / usage.character.limit * 100:.1f}%)"
                )

    except Exception as e:
        logger.error(
            f"❌ DeepL API connectivity test FAILED: {e}\n"
            f"   Translation worker will continue, but DeepL translations will fail!\n"
            f"   Check your DEEPL_API_KEY environment variable and account status.",
            exc_info=True
        )


async def run_worker_loop():
    """
    Main worker loop.

    From REQUIREMENTS-MASTER.md + Feature A (from /tmp/t3):
    1. Create missing canonical translations (canonical-first priority)
    2. Create missing additional locale translations (zh-Hans, etc.)
    3. Process pending translations
    4. Sleep TRANSLATION_WORKER_POLL_INTERVAL
    5. Repeat forever (worker never crashes)
    """
    logger.info("Translation worker started")
    logger.info(f"TRANSLATION_WORKER_POLL_INTERVAL: {TRANSLATION_WORKER_POLL_INTERVAL}")

    # Test DeepL connectivity on startup
    await test_deepl_connectivity()

    while True:
        try:
            # 1. Create missing canonical translations (en-US) - canonical-first priority
            created_canonical = await create_missing_canonical_translations()

            # 2. Create missing additional locale translations (zh-Hans, etc.)
            created_additional = await create_missing_additional_locale_translations()

            # 3. Process pending translations
            processed = await process_pending_translations(batch_size=10)

            if created_canonical > 0 or created_additional > 0 or processed > 0:
                logger.info(
                    f"Created canonical: {created_canonical}, "
                    f"Created additional: {created_additional}, "
                    f"Processed: {processed}"
                )

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
