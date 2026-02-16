# zh-Hans Translation Filtering (Feature t4)

## Overview

The `/api/v1/trends` endpoint now supports strict filtering for Simplified Chinese (zh-Hans) requests,  ensuring only items with complete translations are returned.

**Implementation Date**: February 2026
**From**: `/tmp/t4` requirements

---

## How It Works

### For `lang=en-US` (default)

- Returns **all items** regardless of translation status
- Falls back to canonical (en-US) title/description when translation unavailable
- **No filtering** - shows all trending content

### For `lang=zh-Hans`

- Returns **only items** with complete zh-Hans translation
- **NO fallback** to English - strict filtering only
- Uses **chunk-based overfetching** to ensure enough results returned
- Cursor advances based on scan position, not returned items

---

## API Usage

### Basic Request

```bash
# English (all items, with fallback)
GET /api/v1/trends?lang=en-US&limit=50

# Simplified Chinese (only translated items, no fallback)
GET /api/v1/trends?lang=zh-Hans&limit=50
```

### Query Parameters

| Parameter | Type    | Default  | Description |
|-----------|---------|----------|-------------|
| `lang`    | string  | `en-US`  | Language for display content (`en-US` or `zh-Hans`) |
| `limit`   | integer | `50`     | Number of items to return (max: 200) |
| `cursor`  | string  | `null`   | Opaque cursor for pagination |
| `region`  | string  | `null`   | Optional region filter |
| `bucket`  | string  | `null`   | Optional bucket filter |

---

## Response Format

### Successful Response

```json
{
  "items": [
    {
      "id": 5963,
      "region_key": "us",
      "platform": "ign",
      "bucket": "category_gaming",
      "title_original": "Seven Knights Idle Adventure Episode 5 Review - IGN",
      "description_original": "Seven Knights Idle Adventure: Episode 5...",
      "original_locale": "en-US",
      "url": "https://www.ign.com/articles/...",
      "canonical_title": "Seven Knights Idle Adventure Episode 5 Review - IGN",
      "canonical_description": "Seven Knights Idle Adventure: Episode 5...",
      "display_title": "七国骑士 第5集 评论 - IGN",
      "display_description": "七国骑士：第5集...",
      "rank_position": null,
      "engagement_signals": {},
      "published_at": "2024-02-15T10:30:00Z",
      "collected_at": "2024-02-15T11:00:00Z"
    }
  ],
  "next_cursor": "eyJpZCI6IDU5NjMsICJsYW5nIjogInpoLUhhbnMifQ==",
  "has_more": true
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `display_title` | string | Title in requested language (zh-Hans or fallback) |
| `display_description` | string | Description in requested language |
| `canonical_title` | string | English (en-US) title (always present) |
| `canonical_description` | string | English (en-US) description |
| `title_original` | string | Original untranslated title |
| `original_locale` | string | Locale of original content |

---

## Chunk-Based Scanning (zh-Hans Only)

### How It Works

1. **Scan in chunks**: Fetches candidates in batches of 250 items
2. **Filter by translation status**: Only items with `status='complete'` for zh-Hans
3. **Continue scanning** until:
   - Collected `>= limit` items, OR
   - Scanned `>= 2000` candidates (max scan limit)
4. **Cursor advancement**: Based on last **scanned** item, not last **returned** item

### Why This Matters

**Problem without overfetching**:
- If only 10% of items have zh-Hans translations
- Request `limit=50` would return only ~5 items
- Poor user experience

**Solution with chunk-based scanning**:
- Scans up to 2000 items to find 50 translated ones
- Returns requested `limit` even when translation rate is low
- Cursor progresses correctly to avoid infinite loops

---

## Cursor Format

### en-US Cursor (Simple)

```
Base64 encoding of: "5963"
Decoded: 5963
```

### zh-Hans Cursor (JSON)

```json
Base64 encoding of: {"id": 5963, "lang": "zh-Hans"}
Decoded: {
  "id": 5963,
  "lang": "zh-Hans"
}
```

**Why different formats?**
- zh-Hans needs to track scan position
- Prevents re-scanning same untranslated items
- Ensures cursor stability during filtering

---

## Pagination Example

### First Page

```bash
curl "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=10"
```

**Response**:
```json
{
  "items": [...],  // 10 items with zh-Hans translation
  "next_cursor": "eyJpZCI6IDU5NTAsICJsYW5nIjogInpoLUhhbnMifQ==",
  "has_more": true
}
```

### Second Page

```bash
curl "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=10&cursor=eyJpZCI6IDU5NTAsICJsYW5nIjogInpoLUhhbnMifQ=="
```

**Response**:
```json
{
  "items": [...],  // Next 10 translated items
  "next_cursor": "eyJpZCI6IDU5MzAsICJsYW5nIjogInpoLUhhbnMifQ==",
  "has_more": true
}
```

---

## Logging & Metrics

### Server-Side Logs

Each zh-Hans request logs scan metrics:

```
INFO [zh-Hans] scanned=250 returned=45 ready_rate=18.0% max_scan_reached=False
```

**Metrics**:
- `scanned`: Total items examined
- `returned`: Items with complete zh-Hans translation
- `ready_rate`: Percentage of items with translation ready
- `max_scan_reached`: Whether hit 2000 scan limit

### Monitoring Translation Health

Check `/api/v1/health/translation` for system-wide stats:

```bash
curl http://localhost:8002/api/v1/health/translation
```

**Response**:
```json
{
  "translation_stats": {
    "pending": 1234,
    "complete": 5678,
    "failed": 12
  },
  "last_processed_at": "2024-02-15T11:30:00Z"
}
```

---

## Edge Cases

### 1. No Translations Available

**Scenario**: All items in database lack zh-Hans translations

**Request**:
```bash
GET /api/v1/trends?lang=zh-Hans&limit=50
```

**Response**:
```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

### 2. Hit Max Scan Limit

**Scenario**: Scanned 2000 items, found only 20 with zh-Hans

**Request**:
```bash
GET /api/v1/trends?lang=zh-Hans&limit=50
```

**Response**:
```json
{
  "items": [...],  // 20 items (less than requested limit)
  "next_cursor": "eyJpZCI6IDM1MDAsICJsYW5nIjogInpoLUhhbnMifQ==",
  "has_more": true  // Assume more exist beyond scan limit
}
```

**Log**:
```
INFO [zh-Hans] scanned=2000 returned=20 ready_rate=1.0% max_scan_reached=True
```

### 3. Native Chinese Content

**Scenario**: Original content is already in Simplified Chinese

**Behavior**: Item is included even without explicit translation
```python
# Included if:
original_locale.startswith('zh') OR has_complete_zh_hans_translation
```

---

## Performance Considerations

### Database Indexes

Migration `0003_add_translation_query_index` adds:
```sql
CREATE INDEX crawler_admin_translation_query_idx
ON crawler_admin_trenditemtranslation (locale, status, item_id);
```

**Purpose**: Optimize chunk-based scanning queries

### Query Efficiency

- **Prefetch translations**: Avoids N+1 queries
- **Chunk size**: 250 items per batch (tunable)
- **Max scan limit**: 2000 items (prevents runaway queries)

### Expected Performance

| Translation Rate | Items Scanned | Query Time |
|------------------|---------------|------------|
| 100% (all translated) | 50 | ~50ms |
| 50% (half translated) | 100 | ~80ms |
| 10% (few translated) | 500 | ~200ms |
| 1% (very few) | 2000 (max) | ~500ms |

---

## Testing

### Verify zh-Hans Filtering

```bash
# 1. Request zh-Hans items
curl "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=5" | jq '.items[0].display_title'

# Expected: Chinese characters (e.g., "七国骑士 第5集 评论 - IGN")
# Not: English fallback

# 2. Check original locale
curl "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=5" | jq '.items[0].original_locale'

# Expected: Could be "en-US" (translated) or "zh-*" (native)
```

### Verify Cursor Progression

```bash
# 1. Get first page
cursor=$(curl -s "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=3" | jq -r '.next_cursor')

# 2. Decode cursor (for debugging)
echo $cursor | base64 -d

# Expected: {"id": 5961, "lang": "zh-Hans"}

# 3. Get next page
curl "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=3&cursor=$cursor" | jq '.items[0].id'

# Expected: ID < 5961 (cursor progressed correctly)
```

### Check Scan Metrics

```bash
# Make request and check server logs
tail -f /var/log/crawler_api.log | grep '\[zh-Hans\]'

# Expected log format:
# INFO [zh-Hans] scanned=5 returned=5 ready_rate=100.0% max_scan_reached=False
```

---

## Troubleshooting

### Issue: Empty Results for zh-Hans

**Symptoms**:
```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

**Possible Causes**:
1. No zh-Hans translations exist yet
2. Translation worker not running
3. All translations failed

**Solution**:
```bash
# Check translation status
curl http://localhost:8002/api/v1/health/translation

# Restart translation worker
./scripts/run_translation_worker.sh
```

### Issue: Slow Response Times

**Symptoms**: Requests taking >1 second

**Possible Causes**:
1. Low translation rate (< 5%)
2. Database index not created
3. Large dataset

**Solution**:
```bash
# 1. Check if migration ran
python manage.py showmigrations crawler_admin

# 2. Check translation rate
curl "http://localhost:8002/api/v1/trends?lang=zh-Hans&limit=10" 2>&1 | grep "ready_rate"

# 3. Consider reducing MAX_SCAN_LIMIT if needed (in main.py)
```

### Issue: Duplicate Items

**Symptoms**: Same item appears multiple times

**Possible Causes**:
1. Cursor encoding bug
2. Items updated during pagination

**Solution**:
- Cursors are stateless - this should not happen
- Report as bug if observed

---

## Migration Notes

### Upgrading from t3 to t4

**Before (t3)**:
- `lang=zh-Hans` returned all items with English fallback
- Poor user experience for Chinese users

**After (t4)**:
- `lang=zh-Hans` returns only translated items
- No English fallback for zh-Hans
- Better user experience, but requires good translation coverage

**Rollback Plan**:
If needed, revert to fallback behavior by:
1. Remove chunk-based scanning logic
2. Keep all items, use fallback for display_title

---

## Future Enhancements

### Potential Improvements

1. **Adaptive chunk size**: Increase chunk size if translation rate is low
2. **Translation priority queue**: Prioritize translating trending items
3. **Cache scan results**: Cache translation status for better performance
4. **More languages**: Support ja-JP, ko-KR with same filtering logic

### API Additions (Future)

```http
# Get translation status for specific item
GET /api/v1/trends/{id}/translations

# Request translation for specific item
POST /api/v1/trends/{id}/translate?locale=zh-Hans
```

---

## Summary

**Key Features**:
- ✅ Strict zh-Hans filtering (no English fallback)
- ✅ Chunk-based overfetching for better results
- ✅ Proper cursor advancement (avoids infinite loops)
- ✅ Scan metrics logging for monitoring
- ✅ Performance-optimized with database indexes

**Benefits**:
- Better UX for Chinese users (no English mixed in)
- Scalable approach (handles low translation rates)
- Observable (metrics and logs)
- Testable (clear behavior, documented edge cases)

**Trade-offs**:
- Requires good translation coverage (>10% ideal)
- Slower than en-US when translation rate is low
- More complex cursor logic

---

**Documentation**: `/home/tnnd/data/code/crawler/docs/ZH_HANS_FILTERING.md`
**Implementation**: `/home/tnnd/data/code/crawler/src/crawler_api/main.py`
**Migration**: `/home/tnnd/data/code/crawler/src/crawler_admin/migrations/0003_add_translation_query_index.py`
