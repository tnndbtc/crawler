# Product Philosophy - Addictive Feed Candidate Generator

**Version**: 1.0
**Date**: 2024-01-15
**Based on**: /tmp/t9 product constraints

---

## Core Philosophy

**This crawler is NOT a generic scraper.**

**This crawler IS a candidate generator for an addictive feed.**

---

## The Problem

Generic scrapers optimize for **volume**:
- ❌ Collect everything from one platform
- ❌ Let popular sources dominate
- ❌ No diversity guarantees

**Result**: Homogeneous, boring feed

---

## Our Approach

We optimize for **diversity**:
- ✅ Balanced candidate pool across buckets
- ✅ Prevent single-bucket dominance
- ✅ High-quality, varied content
- ✅ Feed ranking happens LATER

**Result**: Engaging, diverse feed that keeps users coming back

---

## Bucket System (Diversity Enforcement)

Every surface must belong to **exactly one bucket**:

### 1. `hot_now` - Major Trending Content
**Purpose**: Viral, time-sensitive content that everyone's talking about

**Examples**:
- Reddit Hot
- Twitter Trending
- YouTube Trending
- TikTok Trending

**Characteristics**:
- High urgency
- Short shelf life (hours)
- Mass appeal
- High engagement

---

### 2. `rising` - New Gaining Traction
**Purpose**: Catch trends early before they explode

**Examples**:
- Reddit Rising
- YouTube Rising
- Hacker News New (with upvote threshold)
- Twitter Rising Topics

**Characteristics**:
- Early signals
- Growing momentum
- Not yet mainstream
- Higher discovery value

---

### 3. `category_*` - Topic Anchors
**Purpose**: Ensure every major interest area is represented

**Buckets**:
- `category_tech` - Technology, gadgets, software
- `category_sports` - Sports, games, athletes
- `category_entertainment` - Movies, TV, music, celebrities
- `category_finance` - Business, stocks, crypto
- `category_gaming` - Video games, esports
- `category_lifestyle` - Health, food, fashion, travel
- `category_science` - Research, discoveries
- `category_politics` - Government, policy, elections

**Examples**:
- TechCrunch (category_tech)
- ESPN (category_sports)
- Variety (category_entertainment)
- Bloomberg (category_finance)

**Characteristics**:
- Topic-focused
- Consistent quality
- Serves niche interests
- Prevents feed from being all viral content

---

### 4. `region_local` - Local Mainstream Portals
**Purpose**: Regional relevance and cultural context

**Examples**:
- Yahoo Japan (Japan)
- Naver (South Korea)
- Sina News (China)
- BBC News (UK)
- The Guardian (UK)

**Characteristics**:
- Culturally relevant
- Regional perspective
- Mainstream credibility
- Localized content

---

### 5. `evergreen` - Slower High-Quality Sources
**Purpose**: Depth over speed, quality over virality

**Examples**:
- New Yorker
- Atlantic
- Nature Journal
- MIT Technology Review
- Wikipedia trending

**Characteristics**:
- Long-form content
- In-depth analysis
- Slow update cadence
- High editorial standards

---

## Design Rule: Prevent Bucket Dominance

**Rule**: No single bucket can represent > 40% of items collected in one run.

### Why 40%?

- **Too high (>50%)**: One bucket dominates, feed becomes monotonous
- **Too low (<30%)**: Artificial constraints, reduces quality
- **40% sweet spot**: Dominant bucket can shine, but others guaranteed presence

### Example Distribution

**Good** (balanced):
```
Total items collected in one run: 500

hot_now:           180 items (36%)  ✅
rising:             80 items (16%)  ✅
category_tech:      70 items (14%)  ✅
category_sports:    60 items (12%)  ✅
region_local:       60 items (12%)  ✅
evergreen:          50 items (10%)  ✅
```

**Bad** (hot_now dominance):
```
Total items collected in one run: 500

hot_now:           300 items (60%)  ❌ DOMINANCE!
rising:             50 items (10%)
category_tech:      40 items (8%)
category_sports:    30 items (6%)
region_local:       40 items (8%)
evergreen:          40 items (8%)
```

---

## Bucket Weight System

### TrendSurface.bucket_weight

```python
class TrendSurface(models.Model):
    # ... existing fields ...
    bucket_weight = models.FloatField(
        default=1.0,
        help_text="Weight for bucket balancing (higher = more priority)"
    )
```

**Purpose**: Fine-tune bucket balance without changing bucket caps.

**Examples**:
```python
# High-quality source gets higher weight
TrendSurface(
    key='reddit_hot',
    bucket='hot_now',
    bucket_weight=1.5  # Prefer this over other hot_now sources
)

# Lower-quality source gets lower weight
TrendSurface(
    key='low_quality_blog',
    bucket='evergreen',
    bucket_weight=0.5  # De-prioritize
)
```

**How it works**:
When multiple surfaces compete within same bucket, higher weight surfaces get sampled first.

---

## Worker Logic: Bucket Balancing

### Per-Run Bucket Cap Enforcement

```python
BUCKET_CAP_PERCENT = 40  # No bucket > 40% of total items

async def collect_with_bucket_balancing():
    """Collect items while enforcing bucket diversity."""

    # Target total items per run
    target_total = 500

    # Calculate max per bucket
    max_per_bucket = int(target_total * (BUCKET_CAP_PERCENT / 100))  # 200 items

    # Track collected per bucket
    bucket_counts = defaultdict(int)
    collected_items = []

    # Get all due surfaces, grouped by bucket
    surfaces_by_bucket = defaultdict(list)
    for surface in due_surfaces:
        surfaces_by_bucket[surface.bucket].append(surface)

    # Collect round-robin across buckets
    while len(collected_items) < target_total:
        for bucket, surfaces in surfaces_by_bucket.items():
            # Check bucket cap
            if bucket_counts[bucket] >= max_per_bucket:
                continue  # Skip this bucket, it's full

            # Collect from this bucket
            surface = select_surface_with_weight(surfaces)
            items = await collect_from_surface(surface, limit=50)

            for item in items:
                if bucket_counts[bucket] >= max_per_bucket:
                    break  # Bucket full

                collected_items.append(item)
                bucket_counts[bucket] += 1

    # Result: Balanced distribution
    logger.info(f"Bucket distribution: {dict(bucket_counts)}")
```

---

## Translation Rule: NEVER Wait

**Critical**: Ingestion must NEVER wait for translation.

### Wrong Approach ❌

```python
# BAD: Blocks ingestion
item = TrendItem.objects.create(...)
translation = await translate_to_english(item.title)  # BLOCKS!
item.canonical_title = translation
item.save()
```

### Correct Approach ✅

```python
# GOOD: Async, non-blocking
item = TrendItem.objects.create(...)  # Store immediately

# Translation happens separately in translation worker
# TrendItemTranslation created asynchronously
# Feed can use original title until translation completes
```

**Why**:
- Translation APIs can be slow (500ms - 2s per item)
- Translation failures shouldn't block collection
- Feed can show original content, upgrade to translated later
- Candidate generation must be fast

---

## Storage Rule: rank_position > timestamps

**Priority**: rank_position is MORE important than timestamps.

### Why?

**Ranking position tells us**:
- ✅ Editorial signal (human curators chose this)
- ✅ Relative importance (was #1 vs #50)
- ✅ Competitive context (beat other items)

**Timestamps tell us**:
- ⚠️ When published (can be misleading)
- ⚠️ Not always accurate
- ⚠️ Doesn't indicate quality

### Implementation

```python
class TrendItem(models.Model):
    # ... other fields ...

    # CRITICAL: Always capture rank if available
    rank_position = models.IntegerField(
        null=True,
        blank=True,
        help_text="Position in ranking (1=top). IMPORTANT for feed ranking."
    )

    # SECONDARY: Timestamp if available
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When published (if available). rank_position is more important."
    )

    # ALWAYS: Full platform data
    raw_payload = models.JSONField(
        default=dict,
        help_text="Complete platform response. Never throw away data."
    )
```

---

## What We Are NOT Building (Yet)

❌ **Feed Ranking** - Comes later
❌ **Personalization** - Comes later
❌ **User Preferences** - Comes later
❌ **A/B Testing** - Comes later
❌ **Click Prediction** - Comes later

## What We ARE Building

✅ **Balanced Candidate Pool** - Diverse, high-quality content
✅ **Bucket Diversity** - Prevent dominance
✅ **Fast Ingestion** - Don't wait for translation
✅ **Rich Metadata** - rank_position, engagement_signals, raw_payload
✅ **Regional Coverage** - Content from multiple cultures

---

## Success Metrics for Candidate Generation

**Good candidate pool has**:
1. ✅ No bucket > 40% of total items
2. ✅ All major topics represented (tech, sports, entertainment, etc.)
3. ✅ Multiple regions represented
4. ✅ Mix of viral (hot_now) and quality (evergreen)
5. ✅ Fresh content (collected recently)
6. ✅ Rich metadata (rank_position, engagement_signals)

**Bad candidate pool has**:
1. ❌ 90% from one bucket (e.g., all hot_now)
2. ❌ Only tech content (missing other categories)
3. ❌ Only US sources (missing regional diversity)
4. ❌ All viral content (no depth)
5. ❌ Stale content (old collection)
6. ❌ Sparse metadata (missing rank, engagement)

---

## Feed Pipeline (Future)

```
┌─────────────────────────┐
│ 1. Candidate Generation │  ← WE ARE HERE
│    (This Crawler)        │
│    - Diverse sources     │
│    - Bucket balancing    │
│    - Fast ingestion      │
└────────────┬─────────────┘
             │
             ▼
┌─────────────────────────┐
│ 2. Candidate Enrichment │  ← FUTURE
│    - Translation         │
│    - Summarization       │
│    - Entity extraction   │
└────────────┬─────────────┘
             │
             ▼
┌─────────────────────────┐
│ 3. Feed Ranking         │  ← FUTURE
│    - User preferences    │
│    - Click prediction    │
│    - Diversity injection │
└────────────┬─────────────┘
             │
             ▼
┌─────────────────────────┐
│ 4. Personalized Feed    │  ← FUTURE
│    - User-specific       │
│    - A/B tested          │
│    - Optimized for CTR   │
└─────────────────────────┘
```

**We are building Step 1**: Candidate Generation with diversity guarantees.

---

## Key Takeaways

1. **Diversity > Volume** - We want variety, not quantity
2. **Buckets enforce balance** - No single type of content dominates
3. **Translation is async** - Never blocks ingestion
4. **rank_position matters** - Editorial signal > timestamps
5. **This is just candidates** - Feed ranking comes later
6. **Quality candidate pool** - Enables better feed later

---

**Version**: 1.0
**Last Updated**: 2024-01-15
**Philosophy**: From /tmp/t9
