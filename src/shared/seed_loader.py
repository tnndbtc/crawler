"""
Seed loader — reads config/seeds.json and ensures all regions, surfaces,
and translation settings exist in the database.

Uses get_or_create so it's safe to run repeatedly (idempotent).
New entries in seeds.json are added; existing entries are not modified.

Usage:
    from shared.seed_loader import load_seeds
    load_seeds()  # Called automatically on service startup
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

SEEDS_FILE = os.path.join(
    os.path.dirname(__file__), '..', '..', 'config', 'seeds.json'
)


def load_seeds():
    """
    Load all seeds from config/seeds.json into the database.

    Safe to call multiple times — uses get_or_create for all entries.
    """
    if not os.path.exists(SEEDS_FILE):
        logger.warning(f"Seeds file not found: {SEEDS_FILE}")
        return

    with open(SEEDS_FILE, 'r') as f:
        seeds = json.load(f)

    _seed_regions(seeds.get('regions', []))
    _seed_surfaces(seeds.get('surfaces', []))
    _seed_translation(seeds.get('translation', {}))
    _seed_data_retention(seeds.get('data_retention', {}))

    logger.info("Seed loader complete")


def _seed_regions(regions):
    """Seed regions from config."""
    from crawler_admin.models import Region

    for r in regions:
        _, created = Region.objects.get_or_create(
            key=r['key'],
            defaults={
                'name': r['name'],
                'default_locale': r['default_locale'],
                'enabled': r.get('enabled', True),
            }
        )
        if created:
            logger.info(f"  Created region: {r['key']}")


def _seed_surfaces(surfaces):
    """Seed surfaces from config."""
    from crawler_admin.models import Region, TrendSurface

    for s in surfaces:
        try:
            region = Region.objects.get(key=s['region'])
        except Region.DoesNotExist:
            logger.warning(f"  Skipping surface {s['key']}: region '{s['region']}' not found")
            continue

        _, created = TrendSurface.objects.get_or_create(
            region=region,
            key=s['key'],
            defaults={
                'platform': s['platform'],
                'surface_type': s.get('surface_type', 'ranking'),
                'bucket': s.get('bucket', 'hot_now'),
                'bucket_weight': s.get('bucket_weight', 1.0),
                'entrypoint': s['entrypoint'],
                'enabled': s.get('enabled', True),
                'poll_interval_seconds': s.get('poll_interval_seconds', 1800),
                'max_items_per_run': s.get('max_items_per_run', 50),
                'config_json': s.get('config_json', {}),
            }
        )
        if created:
            logger.info(f"  Created surface: {s['key']} ({s['platform']})")


def _seed_translation(translation):
    """Seed translation config and system settings."""
    if not translation:
        return

    # Seed TranslationConfig
    from translation.models import TranslationConfig

    config = TranslationConfig.get_config()
    changed = False

    if config.canonical_engine != translation.get('engine', 'claude'):
        config.canonical_engine = translation['engine']
        changed = True
    if config.display_engine != translation.get('engine', 'claude'):
        config.display_engine = translation['engine']
        changed = True
    if config.fallback_order != translation.get('fallback_order', ['claude']):
        config.fallback_order = translation['fallback_order']
        changed = True

    target_locales = translation.get('target_locales', ['zh-Hans'])
    if config.enabled_locales != target_locales:
        config.enabled_locales = target_locales
        changed = True

    batch_size = translation.get('batch_size', 10)
    if config.batch_size != batch_size:
        config.batch_size = batch_size
        changed = True

    if changed:
        config.save()
        logger.info("  Updated TranslationConfig")

    # Seed SystemSettings
    from crawler_admin.models import SystemSettings

    global_hot_percent = translation.get('hot_percent', 2)

    settings_map = {
        'translation_hot_percent': (global_hot_percent, 'integer', 'Top X% hottest items to translate per language group (global fallback)'),
        'translation_target_locales': (target_locales, 'json', 'Target locales for display translation'),
        'translation_source_langs': (translation.get('source_langs', []), 'json', 'Source languages (empty=ALL)'),
    }

    # Per-lang-group overrides: translation_hot_percent_<lang>
    hot_percent_by_lang = translation.get('hot_percent_by_lang', {})
    for lang, pct in hot_percent_by_lang.items():
        settings_map[f'translation_hot_percent_{lang}'] = (
            pct,
            'integer',
            f'Top X% hottest items to translate for lang_group={lang} (overrides global)',
        )

    for key, (value, vtype, desc) in settings_map.items():
        _, created = SystemSettings.objects.get_or_create(
            key=key,
            defaults={
                'value_json': value,
                'value_type': vtype,
                'description': desc,
                'updated_by': 'seed_loader',
            }
        )
        if created:
            logger.info(f"  Created setting: {key} = {value}")


def _seed_data_retention(retention):
    """Seed data retention settings."""
    if not retention:
        return

    from crawler_admin.models import SystemSettings

    days = retention.get('retention_days', 30)
    _, created = SystemSettings.objects.get_or_create(
        key='data_retention_days',
        defaults={
            'value_json': days,
            'value_type': 'integer',
            'description': 'Days to keep crawled data before cleanup (-1=keep forever)',
            'updated_by': 'seed_loader',
        }
    )
    if created:
        logger.info(f"  Created setting: data_retention_days = {days}")
