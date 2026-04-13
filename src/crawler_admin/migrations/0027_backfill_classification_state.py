"""
Data migration: backfill classification_state and story_category from existing fields.

Three steps:
  Step 1: Set classification_state from existing field values (CASE WHEN, first match wins).
          LLM rule is first to handle edge case of LLM items with NULL topic_classified_at.
  Step 2: Derive story_category for LLM items from topic_tags using raw-tag mapping.
          LLM items are exempt from the worker re-queue — they must carry a valid
          story_category after migration. Non-LLM items will get story_category on re-queue.
  Step 3: Reset non-LLM items for re-queue: set classification_state='pending',
          story_category=NULL, classification_version=NULL.
          LLM items are NOT reset.
"""

from django.db import migrations


# Raw-tag → story_category mapping (for LLM item backfill)
_TAG_TO_CATEGORY = {
    'ai':            'ai',
    'tech':          'technology',
    'politics':      'politics',
    'finance':       'business',
    'business':      'business',
    'science':       'science',
    'society':       'society',
    'health':        'society',
    'crime':         'society',
    'environment':   'science',
    'sports':        'sports',
    'entertainment': 'entertainment',
}

# Within-category priority (highest wins when multiple tags present)
# Note: 'world' is intentionally absent — no raw tag maps to 'world'.
# World is only produced via bucket or surface signals, not topic_tags.
_CATEGORY_PRIORITY = [
    'ai', 'politics', 'business', 'technology', 'science',
    'society', 'sports', 'entertainment',
]


def _pick_story_category(topic_tags: list) -> str | None:
    """Pick the highest-priority story_category from a list of raw topic_tags."""
    mapped = set()
    for tag in (topic_tags or []):
        cat = _TAG_TO_CATEGORY.get(tag)
        if cat:
            mapped.add(cat)
    for cat in _CATEGORY_PRIORITY:
        if cat in mapped:
            return cat
    return None


def backfill_forward(apps, schema_editor):
    import json as _json

    TrendItem = apps.get_model('crawler_admin', 'TrendItem')

    # Step 1: Set classification_state from existing field values.
    # Process all items. LLM rule first (catches LLM items even if topic_classified_at IS NULL).
    items_to_update_state = []
    for item in TrendItem.objects.only(
        'id', 'classified_by', 'topic_classified_at', 'topic_tags'
    ).iterator(chunk_size=1000):
        if item.classified_by == 'llm':
            item.classification_state = 'llm_complete'
        elif item.topic_classified_at is None:
            item.classification_state = 'pending'
        elif item.classified_by == 'heuristic':
            tags = item.topic_tags or []
            item.classification_state = 'heuristic_complete' if tags else 'failed'
        elif item.topic_classified_at is not None and item.classified_by is None:
            item.classification_state = 'heuristic_complete'
        else:
            item.classification_state = 'pending'  # ELSE: safe default
        items_to_update_state.append(item)

    # Bulk update in batches
    batch_size = 500
    for i in range(0, len(items_to_update_state), batch_size):
        TrendItem.objects.bulk_update(
            items_to_update_state[i:i + batch_size],
            ['classification_state'],
        )

    # Step 2: Derive story_category for LLM items (DO NOT reset these).
    llm_items = list(
        TrendItem.objects.filter(classified_by='llm').only('id', 'topic_tags')
    )
    llm_to_update = []
    for item in llm_items:
        tags = item.topic_tags or []
        cat = _pick_story_category(tags)
        if cat:
            item.story_category = cat
        else:
            # Edge case: LLM item with empty tags → failed
            item.story_category = None
            item.classification_state = 'failed'
        llm_to_update.append(item)

    for i in range(0, len(llm_to_update), batch_size):
        TrendItem.objects.bulk_update(
            llm_to_update[i:i + batch_size],
            ['story_category', 'classification_state'],
        )

    # Step 3: Reset non-LLM items for re-queue.
    # Sets classification_state='pending', story_category=NULL, classification_version=NULL.
    # LLM items excluded — their story_category and version are authoritative.
    TrendItem.objects.exclude(classified_by='llm').update(
        classification_state='pending',
        story_category=None,
        classification_version=None,
    )


def backfill_reverse(apps, schema_editor):
    # Reverse: clear all three new fields
    TrendItem = apps.get_model('crawler_admin', 'TrendItem')
    TrendItem.objects.all().update(
        classification_state='pending',
        story_category=None,
        classification_version=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0026_story_category_state_version'),
    ]

    operations = [
        migrations.RunPython(backfill_forward, backfill_reverse),
    ]
