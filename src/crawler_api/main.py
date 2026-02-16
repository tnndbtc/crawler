"""
FastAPI application for trend crawler.

From REQUIREMENTS-MASTER.md:
- Read-only APIs for data consumption
- Health check endpoints
- Observability endpoints (from /tmp/t8)
- canonical_title in responses (from /tmp/t4)

All endpoints are read-only (no writes via API).
Configuration is done via Django Admin.
"""

import os
import sys
import base64
import json
import logging
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import django
from asgiref.sync import sync_to_async
from django.db.models import Q

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import (
    Region,
    TrendSurface,
    TrendItem,
    TrendItemTranslation,
    CrawlRun
)

# Setup logging
logger = logging.getLogger(__name__)


# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(
    title="Culture-Flexible Trend Crawler API",
    description="Read-only API for trend data consumption",
    version="1.0.0"
)

# ============================================================================
# CORS Middleware
# ============================================================================

# Allow cross-origin requests from the UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://192.168.86.41:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Pydantic Models
# ============================================================================

class RegionResponse(BaseModel):
    """Region response schema."""
    key: str
    name: str
    default_locale: str
    enabled: bool


class SurfaceResponse(BaseModel):
    """Surface response schema."""
    id: int
    region_key: str
    key: str
    platform: str
    surface_type: str
    bucket: str
    bucket_weight: float
    enabled: bool
    poll_interval_seconds: int
    last_run_at: Optional[datetime]
    last_success_at: Optional[datetime]
    last_error: Optional[str]


class TrendItemResponse(BaseModel):
    """
    Trend item response schema.

    From /tmp/t4 and /tmp/t9:
    - canonical_title: Translated English title (or original if en-US)
    - canonical_description: Translated English description
    - rank_position: Position in ranking (important!)
    - engagement_signals: Platform engagement metrics

    Feature A (from /tmp/t3):
    - display_title: Title in requested language (lang parameter)
    - display_description: Description in requested language
    - Falls back to canonical if translation unavailable
    """
    id: int
    region_key: str
    platform: str
    bucket: str

    # Original content
    title_original: str
    description_original: Optional[str]
    original_locale: str
    url: str

    # Canonical (English) content
    canonical_title: str
    canonical_description: Optional[str]

    # Display content (localized based on lang parameter)
    display_title: str
    display_description: Optional[str]

    # Ranking & engagement
    rank_position: Optional[int]
    engagement_signals: dict

    # Timestamps
    published_at: Optional[datetime]
    collected_at: datetime


class CursorTrendResponse(BaseModel):
    """
    Cursor-based paginated response for trend items.

    Uses cursor pagination for infinite scroll support.
    """
    items: List[TrendItemResponse]
    next_cursor: Optional[str] = None
    has_more: bool


# ============================================================================
# Basic Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """
    Basic health check.

    From REQUIREMENTS-MASTER.md: Basic health check endpoint.
    """
    return {
        "status": "healthy",
        "service": "trend-crawler",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/v1/regions", response_model=List[RegionResponse])
async def list_regions(
    enabled_only: bool = Query(True, description="Only show enabled regions")
):
    """
    List all regions.

    From REQUIREMENTS-MASTER.md: GET /api/v1/regions
    """
    queryset = Region.objects.all()

    if enabled_only:
        queryset = queryset.filter(enabled=True)

    regions = await sync_to_async(list)(queryset)

    return [
        RegionResponse(
            key=r.key,
            name=r.name,
            default_locale=r.default_locale,
            enabled=r.enabled
        )
        for r in regions
    ]


@app.get("/api/v1/surfaces", response_model=List[SurfaceResponse])
async def list_surfaces(
    region: Optional[str] = Query(None, description="Filter by region key"),
    enabled_only: bool = Query(True, description="Only show enabled surfaces")
):
    """
    List all surfaces.

    From REQUIREMENTS-MASTER.md: GET /api/v1/surfaces
    """
    queryset = TrendSurface.objects.select_related('region')

    if region:
        queryset = queryset.filter(region__key=region)

    if enabled_only:
        queryset = queryset.filter(enabled=True)

    surfaces = await sync_to_async(list)(queryset)

    return [
        SurfaceResponse(
            id=s.id,
            region_key=s.region.key,
            key=s.key,
            platform=s.platform,
            surface_type=s.surface_type,
            bucket=s.bucket,
            bucket_weight=s.bucket_weight,
            enabled=s.enabled,
            poll_interval_seconds=s.poll_interval_seconds,
            last_run_at=s.last_run_at,
            last_success_at=s.last_success_at,
            last_error=s.last_error
        )
        for s in surfaces
    ]


def _has_complete_translation(item, locale: str) -> bool:
    """
    Check if a TrendItem has a complete translation for the given locale.

    Helper function for filtering items by translation status.
    Used by chunk-based scanning for zh-Hans requests (from /tmp/t4).

    Checks both new TrendItem fields and legacy TrendItemTranslation table.
    """
    # Check new TrendItem display fields first
    if locale == 'zh-Hans':
        if item.display_status_zh_hans in ('complete', 'skipped') and item.display_title_zh_hans:
            return True

    # Fallback to legacy translations table
    for trans in item.translations.all():
        if trans.locale == locale and trans.status == 'complete':
            return True

    return False


async def _fetch_zh_hans_items(
    queryset,
    limit: int
) -> Tuple[List[Any], bool, Dict[str, Any], Optional[int]]:
    """
    Fetch items with complete zh-Hans translation using chunk-based scanning.

    From /tmp/t4:
    - Scan candidates in chunks until we have enough translated items
    - Only return items with complete zh-Hans translation (no fallback)
    - Stop when: collected >= limit OR scanned >= max_scan_limit

    Returns:
        (items, has_more, scan_metrics, last_scanned_id)
        - items: List of TrendItem objects with zh-Hans translation
        - has_more: Boolean indicating if more items are available
        - scan_metrics: Dict with scan statistics
        - last_scanned_id: ID of last scanned item (for cursor advancement)
    """
    CHUNK_SIZE = 250  # Scan in chunks of 250 (5x typical limit)
    MAX_SCAN_LIMIT = 2000  # Stop after scanning 2000 candidates

    collected_items = []
    scanned_count = 0
    max_scan_reached = False
    last_scanned_id = None

    while len(collected_items) < limit and scanned_count < MAX_SCAN_LIMIT:
        # Fetch next chunk
        chunk = await sync_to_async(list)(
            queryset[scanned_count:scanned_count + CHUNK_SIZE]
        )

        if not chunk:
            # No more items available
            break

        # Filter items with complete zh-Hans translation
        for item in chunk:
            scanned_count += 1
            last_scanned_id = item.id  # Track last scanned item for cursor

            # Check if item has complete zh-Hans translation OR is native Chinese
            has_zh_hans = (
                item.original_locale.startswith('zh') or
                _has_complete_translation(item, 'zh-Hans')
            )

            if has_zh_hans:
                collected_items.append(item)

                # Stop if we have enough items
                if len(collected_items) >= limit:
                    break

        # Stop if chunk was smaller than expected (end of data)
        if len(chunk) < CHUNK_SIZE:
            break

    # Check if there are more items by looking ahead
    has_more = False
    if scanned_count < MAX_SCAN_LIMIT:
        # Try to fetch one more item
        peek = await sync_to_async(list)(queryset[scanned_count:scanned_count + 1])
        has_more = len(peek) > 0
    else:
        max_scan_reached = True
        # Assume more items exist if we hit scan limit
        has_more = True

    # Calculate metrics
    ready_rate = (len(collected_items) / scanned_count * 100) if scanned_count > 0 else 0.0

    scan_metrics = {
        'scanned': scanned_count,
        'returned': len(collected_items),
        'ready_rate': ready_rate,
        'max_scan_reached': max_scan_reached
    }

    return collected_items, has_more, scan_metrics, last_scanned_id


@app.get("/api/v1/trends", response_model=CursorTrendResponse)
async def list_trends(
    region: Optional[str] = Query(None, description="Filter by region key"),
    bucket: Optional[str] = Query(None, description="Filter by bucket"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(50, ge=1, le=200, description="Number of items to return (hint)"),
    lang: str = Query("en-US", description="Language for display content (en-US|zh-Hans)")
):
    """
    Get trend items with translations (cursor-based pagination).

    From REQUIREMENTS-MASTER.md:
    - GET /api/v1/trends?region=xx&bucket=yy&cursor=xxx&limit=50
    - Returns items with canonical_title (from /tmp/t4)
    - Uses cursor-based pagination for infinite scroll

    Feature A (from /tmp/t3):
    - lang parameter: en-US (default) or zh-Hans
    - display_title/display_description in requested language
    - Falls back to canonical (en-US) if translation unavailable for en-US

    Feature t4 (from /tmp/t4):
    - lang=zh-Hans: ONLY returns items with complete zh-Hans translation
    - Uses chunk-based overfetching to ensure enough results
    - No fallback to English for zh-Hans (strict filtering)

    Product constraint (from /tmp/t9):
    - rank_position is important metadata
    - engagement_signals show platform metrics

    Cursor Pagination:
    - cursor: Opaque cursor string (base64-encoded)
    - For en-US: Simple ID encoding
    - For zh-Hans: JSON encoding with scan state
    - limit: Number of items to return (max 200, hint only - backend may return fewer)
    - Returns next_cursor for fetching more items
    """
    # Build base queryset
    queryset = TrendItem.objects.select_related('region', 'surface').prefetch_related(
        'translations'
    ).order_by('-collected_at', '-id')  # Secondary sort by ID for stability

    if region:
        queryset = queryset.filter(region__key=region)

    if bucket:
        queryset = queryset.filter(bucket=bucket)

    # Apply cursor filter
    cursor_id = None
    if cursor:
        try:
            # Try to decode as JSON first (zh-Hans format)
            cursor_data = json.loads(base64.b64decode(cursor).decode('utf-8'))
            cursor_id = cursor_data.get('id')
        except (ValueError, json.JSONDecodeError):
            # Fall back to simple ID encoding (en-US format)
            try:
                cursor_id = int(base64.b64decode(cursor).decode('utf-8'))
            except (ValueError, Exception):
                # Invalid cursor, ignore it
                cursor_id = None

    if cursor_id:
        queryset = queryset.filter(id__lt=cursor_id)

    # FEATURE t4: Different behavior for zh-Hans vs en-US
    last_scanned_id = None
    if lang == 'zh-Hans':
        # Chunk-based scanning for zh-Hans: ONLY return items with complete translation
        # No fallback to English - strict filtering
        items, has_more, scan_metrics, last_scanned_id = await _fetch_zh_hans_items(queryset, limit)

        # Log scan metrics
        logger.info(
            f"[zh-Hans] scanned={scan_metrics['scanned']} "
            f"returned={scan_metrics['returned']} "
            f"ready_rate={scan_metrics['ready_rate']:.1f}% "
            f"max_scan_reached={scan_metrics['max_scan_reached']}"
        )
    else:
        # Standard behavior for en-US: include all items, use fallback
        # Fetch limit + 1 to check if there are more items
        items = await sync_to_async(list)(queryset[:limit + 1])

        # Check if there are more items
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]  # Trim to limit

        scan_metrics = None

    results = []
    for item in items:
        # Get canonical (en-US) translation from new TrendItem fields
        # Falls back to legacy TrendItemTranslation if new fields not populated
        if item.canonical_status in ('complete', 'skipped') and item.canonical_title:
            canonical_title = item.canonical_title
            canonical_description = item.canonical_description
        else:
            # Fallback to legacy translations table
            canonical_translation = None
            for trans in item.translations.all():
                if trans.locale == 'en-US' and trans.status == 'complete':
                    canonical_translation = trans
                    break

            if canonical_translation:
                canonical_title = canonical_translation.title
                canonical_description = canonical_translation.description
            else:
                # Original is already English or no translation available
                canonical_title = item.title_original
                canonical_description = item.description_original

        # Feature A: Populate display fields based on lang parameter
        # First check new TrendItem display fields, then fall back to legacy
        display_title = None
        display_description = None

        if lang == 'zh-Hans':
            # Check new zh-Hans fields
            if item.display_status_zh_hans in ('complete', 'skipped') and item.display_title_zh_hans:
                display_title = item.display_title_zh_hans
                display_description = item.display_description_zh_hans

        if not display_title:
            # Fallback to legacy translations table
            requested_lang_translation = None
            for trans in item.translations.all():
                if trans.locale == lang and trans.status == 'complete':
                    requested_lang_translation = trans
                    break

            if requested_lang_translation:
                display_title = requested_lang_translation.title
                display_description = requested_lang_translation.description
            elif item.original_locale == lang or item.original_locale.startswith(lang[:2]):
                # Original content is in requested language
                display_title = item.title_original
                display_description = item.description_original
            else:
                # Fallback to canonical (en-US)
                display_title = canonical_title
                display_description = canonical_description

        results.append(
            TrendItemResponse(
                id=item.id,
                region_key=item.region.key,
                platform=item.surface.platform,
                bucket=item.bucket,
                title_original=item.title_original,
                description_original=item.description_original,
                original_locale=item.original_locale,
                url=item.url,
                canonical_title=canonical_title,
                canonical_description=canonical_description,
                display_title=display_title,
                display_description=display_description,
                rank_position=item.rank_position,
                engagement_signals=item.engagement_signals,
                published_at=item.published_at,
                collected_at=item.collected_at
            )
        )

    # Generate next cursor
    next_cursor = None
    if has_more and items:
        if lang == 'zh-Hans' and last_scanned_id:
            # For zh-Hans: encode last scanned ID (not last returned ID)
            # This ensures cursor advances past all scanned items, not just returned ones
            cursor_data = {
                'id': last_scanned_id,
                'lang': 'zh-Hans'
            }
            next_cursor = base64.b64encode(
                json.dumps(cursor_data).encode('utf-8')
            ).decode('utf-8')
        else:
            # For en-US: simple ID encoding (last returned item)
            last_item_id = items[-1].id
            next_cursor = base64.b64encode(
                str(last_item_id).encode('utf-8')
            ).decode('utf-8')

    return CursorTrendResponse(
        items=results,
        next_cursor=next_cursor,
        has_more=has_more
    )


# ============================================================================
# Observability Endpoints (from /tmp/t8)
# ============================================================================

@app.get("/api/v1/health/crawl")
async def crawl_health():
    """
    Per-surface crawl status.

    From REQUIREMENTS-MASTER.md (from /tmp/t8):
    GET /api/v1/health/crawl - Shows last run status for each surface
    """
    surfaces = await sync_to_async(list)(
        TrendSurface.objects.select_related('region').filter(enabled=True)
    )

    results = []
    for surface in surfaces:
        # Get last run
        last_run = await sync_to_async(
            lambda: CrawlRun.objects.filter(surface=surface).order_by('-created_at').first()
        )()

        if last_run:
            results.append({
                "surface_key": f"{surface.region.key}/{surface.key}",
                "last_status": last_run.status,
                "last_finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                "duration_ms": last_run.duration_ms,
                "fetched_count": last_run.fetched_count,
                "stored_new_count": last_run.stored_new_count,
                "deduped_count": last_run.deduped_count
            })
        else:
            results.append({
                "surface_key": f"{surface.region.key}/{surface.key}",
                "last_status": "never_run",
                "last_finished_at": None,
                "duration_ms": None,
                "fetched_count": 0,
                "stored_new_count": 0,
                "deduped_count": 0
            })

    return results


@app.get("/api/v1/health/translation")
async def translation_health():
    """
    Translation queue status with provider health.

    From REQUIREMENTS-MASTER.md (from /tmp/t8):
    GET /api/v1/health/translation - Shows translation queue metrics

    Enhanced to include:
    - Provider health status (available, unavailable_*, etc.)
    - System STOPPED state indicator
    - Available providers list

    Reports on both new (TrendItem fields) and legacy (TrendItemTranslation) systems.
    """
    # === Provider Health Status ===
    from translation.health import ProviderHealthManager
    provider_health = await ProviderHealthManager.get_health_status()

    # === New System Stats (TrendItem fields) ===

    # Canonical translation stats
    canonical_pending = await sync_to_async(
        TrendItem.objects.filter(canonical_status='pending').count
    )()
    canonical_complete = await sync_to_async(
        TrendItem.objects.filter(canonical_status__in=['complete', 'skipped']).count
    )()
    canonical_failed = await sync_to_async(
        TrendItem.objects.filter(canonical_status='failed').count
    )()

    # zh-Hans display translation stats
    zh_hans_pending = await sync_to_async(
        TrendItem.objects.filter(display_status_zh_hans='pending').count
    )()
    zh_hans_complete = await sync_to_async(
        TrendItem.objects.filter(display_status_zh_hans__in=['complete', 'skipped']).count
    )()
    zh_hans_failed = await sync_to_async(
        TrendItem.objects.filter(display_status_zh_hans='failed').count
    )()

    # === Legacy System Stats (TrendItemTranslation) ===

    # Count pending translations (legacy)
    legacy_pending_count = await sync_to_async(
        TrendItemTranslation.objects.filter(status='pending').count
    )()

    # Count failed translations (legacy)
    legacy_failed_count = await sync_to_async(
        TrendItemTranslation.objects.filter(status='failed').count
    )()

    # Get all unique locales in TrendItemTranslation (legacy)
    legacy_locales = await sync_to_async(list)(
        TrendItemTranslation.objects.values_list('locale', flat=True).distinct()
    )

    legacy_per_locale = {}
    for locale in legacy_locales:
        pending = await sync_to_async(
            TrendItemTranslation.objects.filter(locale=locale, status='pending').count
        )()
        complete = await sync_to_async(
            TrendItemTranslation.objects.filter(locale=locale, status='complete').count
        )()
        failed = await sync_to_async(
            TrendItemTranslation.objects.filter(locale=locale, status='failed').count
        )()

        legacy_per_locale[locale] = {
            "pending": pending,
            "complete": complete,
            "failed": failed
        }

    # Get last processed timestamp (most recent completed translation - legacy)
    last_translation = await sync_to_async(
        lambda: TrendItemTranslation.objects.filter(
            status='complete'
        ).order_by('-translated_at').first()
    )()

    last_processed_at = None
    if last_translation and last_translation.translated_at:
        last_processed_at = last_translation.translated_at.isoformat()

    return {
        # Provider health status (NEW)
        "status": provider_health['status'],  # "ok", "degraded", or "stopped"
        "is_stopped": provider_health['is_stopped'],
        "available_providers": provider_health['available_providers'],
        "providers": provider_health['providers'],

        # New system (TrendItem fields)
        "new_system": {
            "canonical": {
                "pending": canonical_pending,
                "complete": canonical_complete,
                "failed": canonical_failed,
            },
            "display": {
                "zh-Hans": {
                    "pending": zh_hans_pending,
                    "complete": zh_hans_complete,
                    "failed": zh_hans_failed,
                }
            }
        },
        # Legacy system (TrendItemTranslation) - for backward compatibility
        "legacy_system": {
            "pending_count": legacy_pending_count,
            "failed_count": legacy_failed_count,
            "per_locale": legacy_per_locale,
            "last_processed_at": last_processed_at
        },
        # Summary
        "total_pending": canonical_pending + zh_hans_pending + legacy_pending_count,
        "total_failed": canonical_failed + zh_hans_failed + legacy_failed_count,
    }


@app.get("/api/v1/surfaces/{surface_id}/recent")
async def surface_recent_items(
    surface_id: int,
    limit: int = Query(20, ge=1, le=100, description="Max items to return")
):
    """
    Recent items from a specific surface (sanity check).

    From REQUIREMENTS-MASTER.md (from /tmp/t8):
    GET /api/v1/surfaces/{surface_id}/recent?limit=20

    Useful for debugging collectors - shows what was actually collected.
    """
    # Get surface
    surface = await sync_to_async(
        lambda: TrendSurface.objects.select_related('region').filter(id=surface_id).first()
    )()

    if not surface:
        raise HTTPException(status_code=404, detail="Surface not found")

    # Get recent items
    items = await sync_to_async(list)(
        TrendItem.objects.filter(surface=surface).prefetch_related('translations')
        .order_by('-collected_at')[:limit]
    )

    results = []
    for item in items:
        # Get canonical translation
        canonical_translation = None
        for trans in item.translations.all():
            if trans.locale == 'en-US' and trans.status == 'complete':
                canonical_translation = trans
                break

        canonical_title = canonical_translation.title if canonical_translation else item.title_original

        results.append({
            "id": item.id,
            "title_original": item.title_original,
            "canonical_title": canonical_title,
            "bucket": item.bucket,
            "rank_position": item.rank_position,
            "engagement_signals": item.engagement_signals,
            "collected_at": item.collected_at.isoformat()
        })

    return {
        "surface_key": f"{surface.region.key}/{surface.key}",
        "platform": surface.platform,
        "bucket": surface.bucket,
        "items": results
    }


# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"detail": "Not found"}
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
