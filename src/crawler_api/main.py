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
from typing import List, Optional
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


@app.get("/api/v1/trends", response_model=CursorTrendResponse)
async def list_trends(
    region: Optional[str] = Query(None, description="Filter by region key"),
    bucket: Optional[str] = Query(None, description="Filter by bucket"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(50, ge=1, le=200, description="Number of items to return (hint)")
):
    """
    Get trend items with canonical (English) translations (cursor-based pagination).

    From REQUIREMENTS-MASTER.md:
    - GET /api/v1/trends?region=xx&bucket=yy&cursor=xxx&limit=50
    - Returns items with canonical_title (from /tmp/t4)
    - Uses cursor-based pagination for infinite scroll

    Product constraint (from /tmp/t9):
    - rank_position is important metadata
    - engagement_signals show platform metrics

    Cursor Pagination:
    - cursor: Opaque cursor string (base64-encoded last item ID)
    - limit: Number of items to return (max 200, hint only - backend may return fewer)
    - Returns next_cursor for fetching more items
    """
    queryset = TrendItem.objects.select_related('region', 'surface').prefetch_related(
        'translations'
    ).order_by('-collected_at', '-id')  # Secondary sort by ID for stability

    if region:
        queryset = queryset.filter(region__key=region)

    if bucket:
        queryset = queryset.filter(bucket=bucket)

    # Filter: Only include items that are either originally English
    # or have a completed en-US translation
    queryset = queryset.filter(
        Q(original_locale='en-US') |
        Q(translations__locale='en-US', translations__status='complete')
    ).distinct()

    # Apply cursor filter
    if cursor:
        try:
            cursor_id = int(base64.b64decode(cursor).decode('utf-8'))
            queryset = queryset.filter(id__lt=cursor_id)
        except (ValueError, Exception):
            # Invalid cursor, ignore it
            pass

    # Fetch limit + 1 to check if there are more items
    items = await sync_to_async(list)(queryset[:limit + 1])

    # Check if there are more items
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]  # Trim to limit

    results = []
    for item in items:
        # Get canonical (en-US) translation
        canonical_translation = None
        for trans in item.translations.all():
            if trans.locale == 'en-US' and trans.status == 'complete':
                canonical_translation = trans
                break

        # Use canonical if available, otherwise original
        # Note: All items reaching this point are guaranteed to be either
        # originally English or have a completed en-US translation (filtered at DB level)
        if canonical_translation:
            canonical_title = canonical_translation.title
            canonical_description = canonical_translation.description
        else:
            # Original is already English (no else case needed - filtered at DB)
            canonical_title = item.title_original
            canonical_description = item.description_original

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
                rank_position=item.rank_position,
                engagement_signals=item.engagement_signals,
                published_at=item.published_at,
                collected_at=item.collected_at
            )
        )

    # Generate next cursor
    next_cursor = None
    if has_more and items:
        last_item_id = items[-1].id
        next_cursor = base64.b64encode(str(last_item_id).encode('utf-8')).decode('utf-8')

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
    Translation queue status.

    From REQUIREMENTS-MASTER.md (from /tmp/t8):
    GET /api/v1/health/translation - Shows translation queue metrics
    """
    # Count items missing canonical en-US translation
    missing_canonical_count = await sync_to_async(
        TrendItem.objects.filter(
            ~django.db.models.Q(original_locale='en-US')
        ).exclude(
            translations__locale='en-US',
            translations__status='complete'
        ).count
    )()

    # Count pending translations
    pending_count = await sync_to_async(
        TrendItemTranslation.objects.filter(status='pending').count
    )()

    # Count failed translations
    failed_count = await sync_to_async(
        TrendItemTranslation.objects.filter(status='failed').count
    )()

    return {
        "missing_canonical_en_count": missing_canonical_count,
        "pending_count": pending_count,
        "failed_count": failed_count
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
