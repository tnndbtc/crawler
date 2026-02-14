# Changes Applied from /tmp/t9

**Date**: 2024-01-15
**Purpose**: Product constraints for addictive feed candidate generation

---

## Summary

Updated design to reflect that **this is NOT a generic scraper** - it's a **candidate generator for an addictive feed** that optimizes for **diversity, not volume**.

---

## Changes Made

### 1. Updated Bucket Choices

**Before** (generic):
```python
bucket = CharField(choices=[
    ('hot_now', 'Hot Now'),
    ('rising', 'Rising'),
    ('category_*', 'Category'),
    ('evergreen', 'Evergreen'),
    ('local', 'Local'),
])
```

**After** (specific for feed diversity):
```python
bucket = CharField(choices=[
    ('hot_now', 'Hot Now'),                    # Major trending
    ('rising', 'Rising'),                      # New gaining traction
    ('category_tech', 'Tech'),                 # Topic anchors
    ('category_sports', 'Sports'),
    ('category_entertainment', 'Entertainment'),
    ('category_finance', 'Finance'),
    ('category_gaming', 'Gaming'),
    ('category_lifestyle', 'Lifestyle'),
    ('category_science', 'Science'),
    ('category_politics', 'Politics'),
    ('region_local', 'Region Local'),          # Local mainstream
    ('evergreen', 'Evergreen'),                # High-quality slow
])
```

### 2. Added bucket_weight to TrendSurface

```python
class TrendSurface(models.Model):
    # ... existing fields ...
    bucket_weight = models.FloatField(default=1.0)  # NEW
```

**Purpose**: Fine-tune bucket balance within same bucket type.

### 3. Added Bucket Cap Enforcement

**New constant**:
```python
BUCKET_CAP_PERCENT = 40  # No bucket > 40% of total items
```

**Worker logic**:
- Track items per bucket during collection
- Skip surfaces when `bucket_counts[bucket] >= max_per_bucket`
- Log bucket distribution at end of run
- **Goal**: Prevent single bucket from dominating feed candidates

### 4. Emphasized Product Philosophy

Created **DESIGN-PRODUCT-PHILOSOPHY.md** explaining:
- Why diversity > volume
- Bucket system purpose
- Translation must be async (never block)
- rank_position > timestamps
- This is candidate generation, not ranking

### 5. Updated Collector Interface Documentation

**Added emphasis on**:
- `rank_position` is MORE important than `published_at`
- `engagement_signals` should be captured (upvotes, views, likes)
- `raw_payload` must ALWAYS be stored (never throw away data)

### 6. Updated Success Criteria

**Added product criteria**:
- ✅ No bucket > 40% of collected items
- ✅ All buckets represented
- ✅ rank_position captured when available
- ✅ engagement_signals captured
- ✅ raw_payload always stored
- ✅ Translation async (NEVER blocks)
- ✅ Balanced candidate pool

### 7. Updated Testing Checklist

**Added product tests**:
- Verify bucket distribution (no bucket > 40%)
- Check rank_position populated
- Check engagement_signals populated
- Check raw_payload complete
- Verify translation doesn't block collection
- Verify bucket diversity in logs

---

## Key Insights from /tmp/t9

### 1. This is Candidate Generation, Not Scraping

**Old thinking**: Scraper collects everything
**New thinking**: Candidate generator provides balanced, diverse pool for later ranking

### 2. Diversity Beats Volume

**Old thinking**: Collect as many items as possible
**New thinking**: Collect diverse items across buckets, prevent dominance

### 3. Buckets Enforce Quality

- `hot_now` - Viral content (but capped at 40%)
- `rising` - Early trends
- `category_*` - Topic diversity (tech, sports, etc.)
- `region_local` - Cultural relevance
- `evergreen` - Quality depth

**Result**: Feed has variety, not just viral spam

### 4. Translation Never Blocks

**Critical rule**: Ingestion must NEVER wait for translation

**Why**:
- Translation APIs can be slow (500ms - 2s)
- Failures shouldn't block collection
- Feed can show original, upgrade later
- Candidate generation must be fast

### 5. rank_position > timestamps

**Why rank matters**:
- Editorial signal (humans chose this)
- Relative importance (#1 vs #50)
- Competitive context

**Why timestamps don't**:
- Can be misleading
- Not always accurate
- Doesn't indicate quality

---

## What We're NOT Building (Yet)

❌ Feed ranking algorithm
❌ Personalization engine
❌ User preferences
❌ Click prediction
❌ A/B testing

## What We ARE Building

✅ Balanced candidate pool
✅ Bucket diversity enforcement
✅ Fast, non-blocking ingestion
✅ Rich metadata capture
✅ Regional coverage

---

## Files Updated

1. **REQUIREMENTS-MASTER.md**
   - Added bucket choices
   - Added bucket_weight field
   - Added bucket cap logic
   - Added product constraints section
   - Updated success criteria
   - Updated testing checklist

2. **DESIGN-PRODUCT-PHILOSOPHY.md** (NEW)
   - Explains why diversity > volume
   - Bucket system rationale
   - Translation async requirement
   - rank_position importance
   - Feed pipeline (candidate gen → ranking → personalization)

3. **.env.example**
   - Already had DRY_RUN and MAX_RUN_SECONDS
   - No changes needed

---

## Implementation Impact

### Models
- ✅ Add `bucket_weight` to TrendSurface
- ✅ Update bucket choices to specific list

### Worker
- ✅ Track `bucket_counts` during collection
- ✅ Enforce `max_per_bucket = total * 0.40`
- ✅ Skip surfaces when bucket full
- ✅ Log bucket distribution

### Collectors
- ✅ Always capture `rank_position` when available
- ✅ Always capture `engagement_signals`
- ✅ Always store complete `raw_payload`

### Admin
- ✅ Show bucket distribution in CrawlRun admin
- ✅ Add bucket_weight field to TrendSurface admin

---

## Verification

### How to verify bucket balancing works:

1. **Create surfaces in multiple buckets**:
   - 3x hot_now surfaces
   - 2x category_tech surfaces
   - 2x evergreen surfaces

2. **Run collection**:
   - Collect 500 items total
   - Worker should enforce caps

3. **Check logs**:
   ```
   Bucket distribution: {
     'hot_now': 195,           # 39% ✅
     'category_tech': 180,     # 36% ✅
     'evergreen': 125          # 25% ✅
   }
   ```

4. **Verify no bucket > 200 items** (40% of 500)

---

## Summary

/tmp/t9 transformed this from a **generic scraper** into a **feed candidate generator** with:
- ✅ Diversity enforcement (bucket caps)
- ✅ Product thinking (addictive feeds)
- ✅ Rich metadata (rank, engagement, raw data)
- ✅ Non-blocking translation
- ✅ Quality over quantity

**Ready for implementation!**
