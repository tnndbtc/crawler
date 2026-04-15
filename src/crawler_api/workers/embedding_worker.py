"""
Embedding worker — semantic vector generation for TrendItems.

Computes a dense vector for every crawled item that has been translated to
canonical English (canonical_title IS NOT NULL).  Vectors are stored in the
ItemDerivation table (derivation_type='embedding') and consumed by the
story_engine's search functions.

Input text (per item):
    canonical_title + ". " + canonical_description
    + optional " [comments: <c1> | <c2> | ...]" from raw_payload.top_comments

Model:     BAAI/bge-small-en-v1.5  (384-dim, ~250 MB RAM, ~15 ms/item CPU)
Storage:
    ItemDerivation.content_title  — input text (for debugging / re-embed)
    ItemDerivation.content_body   — JSON float array  e.g. "[0.12, -0.34, ...]"
    ItemDerivation.target_locale  — "en"
    ItemDerivation.engine         — "BAAI/bge-small-en-v1.5"

Two phases per poll cycle:
    1. BACKFILL  — processes every existing item with no embedding yet, in
                   ascending id order, BACKFILL_BATCH items at a time until
                   the queue is empty.
    2. ONGOING   — polls for items collected since the last run.

Configuration (env vars):
    EMBEDDING_WORKER_POLL_INTERVAL   seconds between ongoing polls  (default 120)
    EMBEDDING_WORKER_BACKFILL_BATCH  items per backfill batch       (default 200)
    EMBEDDING_WORKER_ENCODE_BATCH    items fed to model at once     (default 64)
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import List, Tuple

import django
from asgiref.sync import sync_to_async

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendItem, ItemDerivation  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_INTERVAL    = int(os.getenv('EMBEDDING_WORKER_POLL_INTERVAL',   '120'))
BACKFILL_BATCH   = int(os.getenv('EMBEDDING_WORKER_BACKFILL_BATCH',  '200'))
ENCODE_BATCH     = int(os.getenv('EMBEDDING_WORKER_ENCODE_BATCH',     '64'))

MODEL_NAME       = 'BAAI/bge-small-en-v1.5'
DERIVATION_TYPE  = 'embedding'
TARGET_LOCALE    = 'en'

# Max chars contributed by each section of the input text
_TITLE_MAX       = 200
_DESC_MAX        = 500
_COMMENT_MAX     = 150   # per comment
_COMMENTS_TOTAL  = 5     # max comments to include

# ---------------------------------------------------------------------------
# Model (loaded once at startup)
# ---------------------------------------------------------------------------

_model = None


def _get_model():
    """Load and cache the SentenceTransformer model (called once)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {MODEL_NAME}")
        t0 = time.time()
        _model = SentenceTransformer(MODEL_NAME)
        logger.info(f"Model loaded in {time.time() - t0:.1f}s")
    return _model


# ---------------------------------------------------------------------------
# Text construction
# ---------------------------------------------------------------------------

def _build_embed_text(item: TrendItem) -> str:
    """
    Construct the input string to embed for a single TrendItem.

    Uses canonical (English) fields so vectors are comparable across
    platforms and languages.  Appends top comments when present — they
    carry community reaction signal that pure title+description misses.
    """
    parts: List[str] = []

    title = (item.canonical_title or '').strip()[:_TITLE_MAX]
    if title:
        parts.append(title)

    desc = (item.canonical_description or '').strip()[:_DESC_MAX]
    if desc:
        parts.append(desc)

    # Include top comments from raw_payload when available
    comments: List[str] = (item.raw_payload or {}).get('top_comments', [])
    if comments:
        snippets = [c.strip()[:_COMMENT_MAX] for c in comments if c and c.strip()]
        if snippets:
            parts.append('[comments: ' + ' | '.join(snippets[:_COMMENTS_TOTAL]) + ']')

    return ' '.join(parts)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _items_needing_embedding(last_id: int, limit: int) -> List[TrendItem]:
    """
    Return up to `limit` items with canonical_title that have no embedding yet,
    with id > last_id (for stable cursor-based backfill).
    """
    return list(
        TrendItem.objects
        .filter(canonical_title__isnull=False)
        .exclude(canonical_title='')
        .filter(id__gt=last_id)
        .exclude(derivations__derivation_type=DERIVATION_TYPE)
        .order_by('id')
        .select_related()
        [:limit]
    )


def _count_needing_embedding() -> int:
    return (
        TrendItem.objects
        .filter(canonical_title__isnull=False)
        .exclude(canonical_title='')
        .exclude(derivations__derivation_type=DERIVATION_TYPE)
        .count()
    )


def _save_embeddings(rows: List[Tuple[TrendItem, str, str]]) -> int:
    """
    Bulk-upsert ItemDerivation rows.

    Args:
        rows: list of (item, input_text, json_vector_string)

    Returns:
        number of rows written
    """
    to_create = []
    for item, input_text, vector_json in rows:
        to_create.append(ItemDerivation(
            item=item,
            derivation_type=DERIVATION_TYPE,
            target_locale=TARGET_LOCALE,
            engine=MODEL_NAME,
            status='complete',
            content_title=input_text[:500],   # store truncated input for inspection
            content_body=vector_json,
        ))

    # Use ignore_conflicts so re-runs are safe without unique violation errors
    ItemDerivation.objects.bulk_create(to_create, ignore_conflicts=True)
    return len(to_create)


# ---------------------------------------------------------------------------
# Core encode-and-save loop
# ---------------------------------------------------------------------------

async def _encode_and_save(items: List[TrendItem]) -> int:
    """
    Embed a batch of items and write results to ItemDerivation.

    CPU-bound model inference is offloaded to the default thread executor
    so the async event loop stays responsive.

    Returns number of embeddings saved.
    """
    if not items:
        return 0

    # Build input texts
    texts = [_build_embed_text(item) for item in items]

    # Filter out any items that produced empty text
    valid_pairs = [(item, text) for item, text in zip(items, texts) if text.strip()]
    if not valid_pairs:
        logger.debug("All items in batch had empty embed text — skipping")
        return 0

    valid_items, valid_texts = zip(*valid_pairs)

    # Encode on thread pool (sync, CPU-bound)
    loop = asyncio.get_running_loop()
    model = _get_model()

    def _encode():
        return model.encode(
            list(valid_texts),
            batch_size=ENCODE_BATCH,
            normalize_embeddings=True,   # required for bge cosine similarity
            show_progress_bar=False,
        )

    embeddings = await loop.run_in_executor(None, _encode)

    # Build DB rows
    rows = []
    for item, text, vec in zip(valid_items, valid_texts, embeddings):
        vector_json = json.dumps(vec.tolist())
        rows.append((item, text, vector_json))

    saved = await sync_to_async(_save_embeddings)(rows)
    return saved


# ---------------------------------------------------------------------------
# Backfill phase
# ---------------------------------------------------------------------------

async def run_backfill() -> int:
    """
    Process all existing items that have no embedding, from oldest to newest.

    Returns total number of embeddings created.
    """
    pending = await sync_to_async(_count_needing_embedding)()
    if pending == 0:
        logger.info("Backfill: nothing to do")
        return 0

    logger.info(f"Backfill started — {pending} items to embed")

    total_saved = 0
    last_id = 0

    while True:
        items = await sync_to_async(_items_needing_embedding)(last_id, BACKFILL_BATCH)
        if not items:
            break

        saved = await _encode_and_save(items)
        total_saved += saved
        last_id = items[-1].id

        logger.info(
            f"Backfill progress: +{saved} embedded "
            f"(total {total_saved}, last_id={last_id})"
        )

        # Yield to the event loop between batches so other coroutines can run
        await asyncio.sleep(0)

    logger.info(f"Backfill complete — {total_saved} embeddings created")
    return total_saved


# ---------------------------------------------------------------------------
# Ongoing phase
# ---------------------------------------------------------------------------

_last_ongoing_max_id: int = 0


async def run_ongoing() -> int:
    """
    Embed newly arrived items since the last ongoing run.

    Uses a simple high-water-mark on item id so each item is processed
    exactly once without a DB scan of the full table.

    Returns number of embeddings created this cycle.
    """
    global _last_ongoing_max_id

    # On the very first ongoing call, anchor to the current max id so we
    # only look at items that arrive after the worker starts.
    if _last_ongoing_max_id == 0:
        max_id_row = await sync_to_async(
            lambda: TrendItem.objects.order_by('-id').values_list('id', flat=True).first()
        )()
        _last_ongoing_max_id = max_id_row or 0
        logger.debug(f"Ongoing anchor set to id={_last_ongoing_max_id}")
        return 0

    items = await sync_to_async(_items_needing_embedding)(
        _last_ongoing_max_id, BACKFILL_BATCH
    )

    if not items:
        logger.debug("Ongoing: no new items to embed")
        return 0

    saved = await _encode_and_save(items)
    _last_ongoing_max_id = items[-1].id

    if saved:
        logger.info(f"Ongoing: embedded {saved} new item(s), last_id={_last_ongoing_max_id}")

    return saved


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

async def run_worker_loop():
    logger.info("Embedding worker started")
    logger.info(
        f"Model={MODEL_NAME}  POLL_INTERVAL={POLL_INTERVAL}s  "
        f"BACKFILL_BATCH={BACKFILL_BATCH}  ENCODE_BATCH={ENCODE_BATCH}"
    )

    # Pre-load model before entering the loop so the first batch doesn't stall
    await asyncio.get_running_loop().run_in_executor(None, _get_model)

    # Phase 1: backfill all existing items
    try:
        await run_backfill()
    except Exception as e:
        logger.error(f"Backfill error: {e}", exc_info=True)

    # Phase 2: ongoing poll for new items
    while True:
        try:
            await run_ongoing()
        except Exception as e:
            logger.error(f"Ongoing embedding error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Embedding worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
