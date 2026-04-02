"""
Translation worker - Async translation with canonical-first priority.

Refactored to use the new pluggable translation engine architecture:
- Uses TranslationManager for engine orchestration
- Updates TrendItem fields directly (not TrendItemTranslation)
- Supports canonical (en-US) and display (zh-Hans) translations
- Fallback handling via TranslationConfig

Provider Failover & STOPPED State:
- Checks provider health before processing
- When all providers unavailable, enters STOPPED state
- Health probes attempt recovery every 5 minutes
- Items stay 'pending' when providers unavailable (not marked 'failed')

From REQUIREMENTS-MASTER.md and /tmp/t9:
- Translation NEVER blocks collection (async)
- Canonical locale (en-US) always attempted for non-English items
- Processes pending translations using configured engines
- Updates TrendItem canonical/display fields
"""

import os
import sys
import asyncio
import logging
import time
from typing import List

import django
from django.utils import timezone
from django.db.models import Q
from asgiref.sync import sync_to_async

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendItem, ItemDerivation
from translation.manager import TranslationManager
from translation.models import TranslationConfig
from translation.health import ProviderHealthManager
from shared.translation_selection import select_items_for_translation

# Legacy imports for backward compatibility
from crawler_admin.models import TrendItemTranslation

logger = logging.getLogger(__name__)

# Configuration from environment
TRANSLATION_WORKER_POLL_INTERVAL = int(os.getenv('TRANSLATION_WORKER_POLL_INTERVAL', '30'))

# Health probe configuration
HEALTH_PROBE_INTERVAL = 300  # 5 minutes between health probes when STOPPED
_last_health_probe_time: float = 0



async def process_canonical_translations(manager: TranslationManager, batch_size: int = 10) -> int:
    """
    Process pending canonical (en-US) translations.

    Updates TrendItem.canonical_* fields directly.

    Args:
        manager: TranslationManager instance
        batch_size: Number of items to process per batch

    Returns:
        Number of items processed
    """
    config = await manager.get_config()

    if not config.enable_translation:
        logger.info("Translation disabled, skipping canonical processing")
        return 0

    # Find items needing canonical translation:
    # - canonical_status = 'pending'
    # - Not already English (would be 'skipped')
    items_need_canonical = await sync_to_async(list)(
        TrendItem.objects.filter(
            canonical_status='pending'
        ).exclude(
            original_locale__startswith='en-'
        ).select_related('region', 'surface')[:batch_size]
    )

    if not items_need_canonical:
        logger.debug("No items need canonical translation")
        return 0

    logger.info(f"Processing {len(items_need_canonical)} canonical translation(s)")

    processed_count = 0
    for item in items_need_canonical:
        try:
            logger.debug(
                f"📝 Canonical: item #{item.id} | "
                f"{item.original_locale} → en-US | "
                f"Title: {item.title_original[:50]}..."
            )

            result = await manager.translate_item_canonical(item)

            if result.success:
                item.canonical_title = result.title
                item.canonical_description = result.description
                item.canonical_status = 'complete'
                item.canonical_engine = result.engine
                item.canonical_error = None

                logger.info(
                    f"✅ Canonical translated: item #{item.id} | "
                    f"engine: {result.engine}"
                )
            elif result.engine in ('system_stopped', 'none_available'):
                # Provider unavailable - keep item as pending
                # Don't update status or save, just skip this item
                logger.info(
                    f"⏸️ Canonical skipped (providers unavailable): item #{item.id}"
                )
                continue
            else:
                item.canonical_status = 'failed'
                item.canonical_error = result.error
                item.canonical_engine = result.engine

                logger.warning(
                    f"❌ Canonical failed: item #{item.id} | "
                    f"error: {result.error}"
                )

            await sync_to_async(item.save)(
                update_fields=[
                    'canonical_title', 'canonical_description',
                    'canonical_status', 'canonical_engine', 'canonical_error'
                ]
            )
            processed_count += 1

        except Exception as e:
            logger.error(f"Error processing canonical for item #{item.id}: {e}", exc_info=True)
            # Mark as failed
            item.canonical_status = 'failed'
            item.canonical_error = str(e)
            await sync_to_async(item.save)(
                update_fields=['canonical_status', 'canonical_error']
            )

    return processed_count


async def process_display_translations(
    manager: TranslationManager,
    target_locale: str,
    batch_size: int = 10
) -> int:
    """
    Process pending display translations for a specific locale.

    Updates TrendItem.display_*_{locale} fields directly.

    Args:
        manager: TranslationManager instance
        target_locale: Target locale (e.g., 'zh-Hans')
        batch_size: Number of items to process per batch

    Returns:
        Number of items processed
    """
    config = await manager.get_config()

    if not config.enable_translation:
        return 0

    # Map locale to field name suffix (zh-Hans → zh_hans)
    locale_suffix = target_locale.replace('-', '_').lower()
    status_field = f'display_status_{locale_suffix}'
    title_field = f'display_title_{locale_suffix}'
    description_field = f'display_description_{locale_suffix}'
    engine_field = f'display_engine_{locale_suffix}'
    error_field = f'display_error_{locale_suffix}'

    # Check if fields exist
    if not hasattr(TrendItem, status_field):
        logger.warning(f"TrendItem has no field '{status_field}', skipping {target_locale}")
        return 0

    # Find items needing display translation
    filter_kwargs = {status_field: 'pending'}
    items_need_display = await sync_to_async(list)(
        TrendItem.objects.filter(**filter_kwargs)
        .exclude(original_locale=target_locale)  # Skip if already native
        .select_related('region', 'surface')[:batch_size]
    )

    if not items_need_display:
        logger.debug(f"No items need {target_locale} display translation")
        return 0

    logger.info(f"Processing {len(items_need_display)} {target_locale} display translation(s)")

    processed_count = 0
    for item in items_need_display:
        try:
            logger.debug(
                f"📝 Display ({target_locale}): item #{item.id} | "
                f"{item.original_locale} → {target_locale}"
            )

            result = await manager.translate_item_display(item, target_locale)

            if result.success:
                setattr(item, title_field, result.title)
                setattr(item, description_field, result.description)
                setattr(item, status_field, 'complete')
                setattr(item, engine_field, result.engine)
                setattr(item, error_field, None)

                logger.info(
                    f"✅ Display ({target_locale}) translated: item #{item.id} | "
                    f"engine: {result.engine}"
                )
            elif result.engine in ('system_stopped', 'none_available'):
                # Provider unavailable - keep item as pending
                # Don't update status or save, just skip this item
                logger.info(
                    f"⏸️ Display ({target_locale}) skipped (providers unavailable): item #{item.id}"
                )
                continue
            else:
                setattr(item, status_field, 'failed')
                setattr(item, engine_field, result.engine)
                setattr(item, error_field, result.error)

                logger.warning(
                    f"❌ Display ({target_locale}) failed: item #{item.id} | "
                    f"error: {result.error}"
                )

            await sync_to_async(item.save)(
                update_fields=[title_field, description_field, status_field, engine_field, error_field]
            )
            processed_count += 1

        except Exception as e:
            logger.error(
                f"Error processing display ({target_locale}) for item #{item.id}: {e}",
                exc_info=True
            )
            setattr(item, status_field, 'failed')
            setattr(item, error_field, str(e))
            await sync_to_async(item.save)(update_fields=[status_field, error_field])

    return processed_count


async def process_display_translations_selective(
    manager: TranslationManager,
    target_locale: str,
    batch_size: int = 10
) -> int:
    """
    Process display translations using selective hotness-based selection.

    This is the NEW approach that:
    1. Uses select_items_for_translation() to get top X% hottest items
    2. Stores translations in ItemDerivation table (not inline fields)
    3. Checks for existing derivations (idempotency)
    4. Supports admin-configurable translation percentage

    Args:
        manager: TranslationManager instance
        target_locale: Target locale (e.g., 'zh-Hans')
        batch_size: Number of items to process per batch

    Returns:
        Number of items processed
    """
    config = await manager.get_config()

    if not config.enable_translation:
        return 0

    # Get selected items using hotness algorithm
    items = await sync_to_async(select_items_for_translation)(
        target_locale, batch_size
    )

    if not items:
        logger.debug(f"No items selected for translation to {target_locale}")
        return 0

    logger.info(
        f"Processing {len(items)} selected item(s) for translation to {target_locale} "
        f"(selective mode)"
    )

    processed_count = 0
    for item in items:
        try:
            # Extract all item data in sync context to avoid async database access
            def get_item_data():
                return {
                    'item_id': item.id,
                    'base_lang': item.base_lang,
                    'hotness': item.hotness,
                    'title': item.title_original,
                    'description': item.description_original,
                    'source_locale': item.original_locale,
                    'source_platform': item.surface.platform if item.surface else '',
                    'region': item.region.key if item.region else '',
                }

            item_data = await sync_to_async(get_item_data)()

            # Skip if derivation already exists (race condition protection)
            derivation_exists = await sync_to_async(
                ItemDerivation.objects.filter(
                    item=item,
                    derivation_type='translation',
                    target_locale=target_locale
                ).exists
            )()

            if derivation_exists:
                logger.debug(
                    f"⏭️  Skipping item #{item_data['item_id']}: derivation already exists"
                )
                continue

            logger.debug(
                f"📝 Selective translation: item #{item_data['item_id']} | "
                f"{item_data['base_lang']} → {target_locale} | "
                f"hotness={item_data['hotness']:.2f}"
            )

            # Translate using direct method (all data now in simple dict)
            result = await manager.translate_display(
                title=item_data['title'],
                description=item_data['description'],
                source_locale=item_data['source_locale'],
                target_locale=target_locale,
                source_platform=item_data['source_platform'],
                region=item_data['region']
            )

            if result.success:
                # Store in ItemDerivation (use get_or_create to handle race conditions)
                derivation, created = await sync_to_async(ItemDerivation.objects.get_or_create)(
                    item=item,
                    derivation_type='translation',
                    target_locale=target_locale,
                    defaults={
                        'content_title': result.title,
                        'content_body': result.description,
                        'engine': result.engine,
                        'status': 'complete'
                    }
                )

                if created:
                    logger.info(
                        f"✅ Selective translation complete: item #{item_data['item_id']} → {target_locale} | "
                        f"engine: {result.engine}, hotness: {item_data['hotness']:.2f}"
                    )

                    # DUAL-WRITE: Also update inline fields for backward compatibility
                    # This can be removed in Phase C (cleanup) after validation
                    locale_suffix = target_locale.replace('-', '_').lower()
                    title_field = f'display_title_{locale_suffix}'
                    description_field = f'display_description_{locale_suffix}'
                    status_field = f'display_status_{locale_suffix}'
                    engine_field = f'display_engine_{locale_suffix}'
                    error_field = f'display_error_{locale_suffix}'

                    if hasattr(TrendItem, title_field):
                        setattr(item, title_field, result.title)
                        setattr(item, description_field, result.description)
                        setattr(item, status_field, 'complete')
                        setattr(item, engine_field, result.engine)
                        setattr(item, error_field, None)
                        await sync_to_async(item.save)(
                            update_fields=[
                                title_field, description_field,
                                status_field, engine_field, error_field
                            ]
                        )
                        logger.debug(f"  (also updated inline fields for compatibility)")
                else:
                    logger.debug(
                        f"⏭️  Translation already exists: item #{item_data['item_id']} → {target_locale} (race condition)"
                    )

            elif result.engine in ('system_stopped', 'none_available'):
                # Provider unavailable - skip this item
                logger.info(
                    f"⏸️ Selective translation skipped (providers unavailable): item #{item_data['item_id']}"
                )
                continue

            else:
                # Translation failed (use get_or_create to handle race conditions)
                derivation, created = await sync_to_async(ItemDerivation.objects.get_or_create)(
                    item=item,
                    derivation_type='translation',
                    target_locale=target_locale,
                    defaults={
                        'content_title': '',
                        'content_body': '',
                        'engine': result.engine or 'unknown',
                        'status': 'failed',
                        'error_message': result.error
                    }
                )

                if created:
                    logger.warning(
                        f"❌ Selective translation failed: item #{item_data['item_id']} | "
                        f"error: {result.error}"
                    )
                else:
                    logger.debug(
                        f"⏭️  Failed translation already recorded: item #{item_data['item_id']} → {target_locale}"
                    )

            processed_count += 1

        except Exception as e:
            # Get item ID safely (item_data might not exist if error was early)
            try:
                item_id = item_data['item_id']
            except (NameError, KeyError):
                item_id = await sync_to_async(lambda: item.id)()

            logger.error(
                f"Error processing selective translation for item #{item_id}: {e}",
                exc_info=True
            )
            # Try to create failed derivation
            try:
                await sync_to_async(ItemDerivation.objects.get_or_create)(
                    item=item,
                    derivation_type='translation',
                    target_locale=target_locale,
                    defaults={
                        'content_title': '',
                        'content_body': '',
                        'engine': 'error',
                        'status': 'failed',
                        'error_message': str(e)
                    }
                )
            except:
                pass  # Best effort

    return processed_count


async def mark_english_items_as_skipped(batch_size: int = 100) -> int:
    """
    Mark English items as 'skipped' for canonical translation.

    These items don't need translation since they're already in English.

    Returns:
        Number of items marked
    """
    items = await sync_to_async(list)(
        TrendItem.objects.filter(
            canonical_status='pending',
            original_locale__startswith='en-'
        )[:batch_size]
    )

    if not items:
        return 0

    marked_count = 0
    for item in items:
        item.canonical_status = 'skipped'
        item.canonical_title = item.title_original
        item.canonical_description = item.description_original
        item.canonical_engine = 'none'
        await sync_to_async(item.save)(
            update_fields=[
                'canonical_status', 'canonical_title',
                'canonical_description', 'canonical_engine'
            ]
        )
        marked_count += 1

    if marked_count > 0:
        logger.info(f"Marked {marked_count} English item(s) as skipped for canonical translation")

    return marked_count


async def mark_native_items_as_skipped(target_locale: str, batch_size: int = 100) -> int:
    """
    Mark items already in target locale as 'skipped' for display translation.

    Returns:
        Number of items marked
    """
    locale_suffix = target_locale.replace('-', '_').lower()
    status_field = f'display_status_{locale_suffix}'
    title_field = f'display_title_{locale_suffix}'
    description_field = f'display_description_{locale_suffix}'
    engine_field = f'display_engine_{locale_suffix}'

    if not hasattr(TrendItem, status_field):
        return 0

    # Find items where original_locale matches or starts with target base
    # e.g., zh-Hans items for zh-Hans display
    filter_kwargs = {
        status_field: 'pending',
        'original_locale__startswith': target_locale[:2]
    }

    items = await sync_to_async(list)(
        TrendItem.objects.filter(**filter_kwargs)[:batch_size]
    )

    if not items:
        return 0

    marked_count = 0
    for item in items:
        setattr(item, status_field, 'skipped')
        setattr(item, title_field, item.title_original)
        setattr(item, description_field, item.description_original)
        setattr(item, engine_field, 'none')
        await sync_to_async(item.save)(
            update_fields=[status_field, title_field, description_field, engine_field]
        )
        marked_count += 1

    if marked_count > 0:
        logger.info(
            f"Marked {marked_count} native {target_locale} item(s) as skipped for display translation"
        )

    return marked_count


async def test_engine_connectivity(manager: TranslationManager):
    """
    Test translation engine connectivity on startup.

    Tests the primary canonical engine to verify it's working.
    """
    config = await manager.get_config()
    primary_engine = config.canonical_engine

    logger.info(f"🔍 Testing {primary_engine} engine connectivity...")

    try:
        success = await manager.test_engine(primary_engine)

        if success:
            logger.info(f"✅ {primary_engine} engine test successful!")
            # Mark provider as available on successful test
            await ProviderHealthManager.mark_success(primary_engine)
        else:
            logger.warning(
                f"⚠️ {primary_engine} engine test failed! "
                f"Translation worker will use fallback engines."
            )

    except Exception as e:
        logger.error(
            f"❌ {primary_engine} engine connectivity test FAILED: {e}\n"
            f"   Translation worker will continue with fallback engines.",
            exc_info=True
        )


def should_run_health_probe() -> bool:
    """
    Check if enough time has passed to run a health probe.

    Returns:
        True if health probe should run
    """
    global _last_health_probe_time
    current_time = time.time()

    if current_time - _last_health_probe_time >= HEALTH_PROBE_INTERVAL:
        _last_health_probe_time = current_time
        return True
    return False


async def run_health_probes(manager: TranslationManager) -> bool:
    """
    Run health probes for unavailable providers.

    Tests each unavailable provider with a lightweight translation.
    If any provider recovers, marks it as available.

    Args:
        manager: TranslationManager instance

    Returns:
        True if any provider recovered
    """
    logger.info("🏥 Running health probes for unavailable providers...")

    unavailable = await ProviderHealthManager.get_unavailable_providers()
    if not unavailable:
        logger.debug("No unavailable providers to probe")
        return False

    any_recovered = False

    for provider in unavailable:
        logger.debug(f"Probing provider: {provider}")

        # Test with a lightweight translation
        async def test_provider(p):
            await manager.test_engine(p)

        success, error = await ProviderHealthManager.run_health_probe(
            provider, test_provider
        )

        if success:
            logger.info(f"✅ Provider {provider} recovered!")
            any_recovered = True
        else:
            logger.debug(f"Provider {provider} still unavailable: {error}")

    if any_recovered:
        logger.info("🎉 At least one provider recovered, resuming normal operation")
    else:
        logger.info("All probed providers still unavailable")

    return any_recovered


async def run_worker_loop():
    """
    Main worker loop.

    STOPPED State Handling:
    - When all providers unavailable, enters STOPPED state
    - In STOPPED state, runs health probes every 5 minutes
    - When a provider recovers, resumes normal operation

    Normal Operation:
    1. Check STOPPED state (all providers unavailable)
    2. Mark English items as skipped (no canonical translation needed)
    3. Process canonical (en-US) translations
    4. Mark native items as skipped for each display locale
    5. Process display translations for each enabled locale
    6. Sleep and repeat
    """
    logger.info("Translation worker started (new architecture)")
    logger.info(f"TRANSLATION_WORKER_POLL_INTERVAL: {TRANSLATION_WORKER_POLL_INTERVAL}s")
    logger.info(f"HEALTH_PROBE_INTERVAL: {HEALTH_PROBE_INTERVAL}s")

    # Initialize manager
    manager = TranslationManager()

    # Test engine connectivity on startup
    await test_engine_connectivity(manager)

    while True:
        try:
            # Refresh config each cycle to pick up admin changes
            config = await manager.reload_config()

            if not config.enable_translation:
                logger.info("Translation disabled, sleeping...")
                await asyncio.sleep(TRANSLATION_WORKER_POLL_INTERVAL)
                continue

            # Check STOPPED state (all providers unavailable)
            if await ProviderHealthManager.is_stopped():
                logger.warning("⛔ System STOPPED: all providers unavailable")

                # Run health probes periodically
                if should_run_health_probe():
                    recovered = await run_health_probes(manager)
                    if recovered:
                        logger.info("Provider recovered! Resuming normal operation...")
                        # Continue to normal processing
                    else:
                        # Still stopped, sleep and try again
                        await asyncio.sleep(TRANSLATION_WORKER_POLL_INTERVAL)
                        continue
                else:
                    # Not time for health probe yet, just sleep
                    remaining = HEALTH_PROBE_INTERVAL - (time.time() - _last_health_probe_time)
                    logger.debug(
                        f"STOPPED state: next health probe in {remaining:.0f}s"
                    )
                    await asyncio.sleep(TRANSLATION_WORKER_POLL_INTERVAL)
                    continue

            batch_size = config.batch_size

            # 1. Mark English items as skipped
            skipped_english = await mark_english_items_as_skipped(batch_size)

            # 2. Process canonical translations
            processed_canonical = await process_canonical_translations(manager, batch_size)

            # 3. Process display translations for each target locale
            # Read from SystemSettings (single source of truth)
            from shared.translation_selection import get_translation_settings
            settings = await sync_to_async(get_translation_settings)()
            target_locales = settings.get('target_locales', ['zh-Hans'])

            processed_display = {}
            for locale in target_locales:
                # Mark native items as skipped (for inline fields compatibility)
                skipped_native = await mark_native_items_as_skipped(locale, batch_size)

                # Process translations using SELECTIVE approach (hotness-based)
                count = await process_display_translations_selective(manager, locale, batch_size)
                processed_display[locale] = count

                # LEGACY: Also process old inline-field approach for items not selected
                # This ensures backward compatibility during transition
                # Can be removed in Phase C (cleanup) after full migration
                # count_legacy = await process_display_translations(manager, locale, batch_size)
                # processed_display[f'{locale}_legacy'] = count_legacy

            # Log summary
            total_processed = processed_canonical + sum(processed_display.values())
            if total_processed > 0 or skipped_english > 0:
                display_summary = ', '.join(
                    f"{locale}={count}"
                    for locale, count in processed_display.items()
                )
                logger.info(
                    f"Cycle complete: "
                    f"canonical={processed_canonical}, "
                    f"display=[{display_summary}], "
                    f"skipped_english={skipped_english}"
                )

        except Exception as e:
            # Never crash the worker loop
            logger.error(f"Worker loop error: {e}", exc_info=True)

        # Sleep before next poll
        await asyncio.sleep(TRANSLATION_WORKER_POLL_INTERVAL)


def _mask_key(key: str) -> str:
    """Mask API key for safe logging."""
    if not key:
        return "(not set)"
    if len(key) <= 11:
        return "***"
    return f"{key[:7]}...{key[-4:]}"


def main():
    """Entry point for translation worker."""
    # Log env vars at startup for debugging
    logger.info("🔑 Translation engine: Claude CLI (claude -p)")

    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Translation worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
