# Translation Selection Fix - Implementation Summary

## ✅ Implementation Complete

**Date:** 2026-02-18
**File Modified:** `src/shared/translation_selection.py`
**Function:** `select_items_for_translation()` (lines 215-290)

---

## 🎯 Problem Fixed

### Before (Broken Behavior)

The translation selection logic calculated the percentage from **untranslated items only**, causing it to continuously select new items even after the intended quota was filled.

**Example with 0.001% and 10,000 English posts:**

- **Cycle 1:** Filters 10,000 untranslated → Calculate 0.001% of 10,000 = 1 → Select 1 post
- **Cycle 2:** Filters 9,999 untranslated → Calculate 0.001% of 9,999 = 1 → Select 1 post
- **Result:** Keeps selecting forever until all 10,000 are translated ❌

### After (Correct Behavior)

Now calculates the percentage from **TOTAL items** (including already-translated), then determines how many MORE are needed.

**Example with 0.001% and 10,000 English posts:**

- **Cycle 1:** Total = 10,000 → Target = 1 → Already translated = 0 → Needed = 1 → Select 1 post
- **Cycle 2:** Total = 10,000 → Target = 1 → Already translated = 1 → Needed = 0 → Select 0 posts
- **Result:** Translates exactly 0.001% (1 out of 10,000), then stops ✅

---

## 🔧 Changes Made

### Code Changes

The main loop in `select_items_for_translation()` now:

1. **Builds TWO queries per lang_group:**
   - **all_items_query:** All items with hotness (including already-translated) → for counting TOTAL
   - **group_items_untranslated:** Only untranslated items → for selection

2. **Calculates quota correctly:**
   - Counts **total items** (including translated)
   - Counts **already-translated items**
   - Calculates **target count** from total (not from untranslated)
   - Calculates **needed count** = max(0, target - already_translated)

3. **Stops when quota filled:**
   - If needed count = 0, skips selection (logs "quota already filled")
   - If needed count > 0, selects that many from untranslated items

### Key Log Output (New)

The worker now logs detailed quota information:

```
INFO translation_selection lang_group=en: total=11902, already_translated=2117, target=1, needed=0
INFO translation_selection lang_group=en: quota already filled, skipping
```

This makes it easy to verify the fix is working correctly.

---

## ✅ Verification Results

**Test Environment:**
- Setting: `translation_hot_percent = 0.001%`
- Total items with hotness: 13,843

**Results:**

| Lang Group | Total Items | Already Translated | Target (0.001%) | Needed | Status |
|------------|-------------|-------------------|-----------------|---------|---------|
| en         | 11,902      | 2,117             | 1               | 0       | ✅ Quota FILLED |
| es         | 91          | 8                 | 1               | 0       | ✅ Quota FILLED |
| ja         | 24          | 24                | 1               | 0       | ✅ Quota FILLED |
| de         | 222         | 222               | 1               | 0       | ✅ Quota FILLED |
| fr         | 109         | 109               | 1               | 0       | ✅ Quota FILLED |

**Final selection:** 0 items (correct! ✅)

### Before vs After

**Before the fix:**
- Would have selected ~1 item per cycle per lang_group continuously
- Would eventually translate all 13,843 items (100%) instead of 0.001%

**After the fix:**
- Correctly identifies quota is filled (2,117 > target of 1 for English)
- Selects 0 items
- Stops translating ✅

---

## 📊 Edge Cases Handled

### Case 1: Manual Translations Exceed Quota ✅

If items were manually translated or quota was previously higher:

- Total: 10,000
- Already translated: 50 (manual or previous higher %)
- Target (0.001%): 1
- **Needed: max(0, 1 - 50) = 0** → Selects nothing ✅

### Case 2: Small Bucket Logic ✅

For lang_groups with < 20 items:

- Total: 15 items (triggers small bucket)
- Already translated: 0
- Target (small bucket): max(1, min(15, 5)) = 5
- **Needed: 5 - 0 = 5** → Selects 5 items ✅

### Case 3: Partial Quota Filled ✅

When only some of the quota is filled:

- Total: 10,000
- Already translated: 50
- Target (1%): 100
- **Needed: 100 - 50 = 50** → Selects 50 more items ✅

---

## 🧪 How to Verify

### Method 1: Check Worker Logs

Watch the translation worker logs:

```bash
tail -f logs/translation_worker.log | grep -E "(lang_group.*needed|quota already filled)"
```

**Expected output when quota is filled:**

```
lang_group=en: total=11902, already_translated=2117, target=1, needed=0
lang_group=en: quota already filled, skipping
Final selection: 0 items for translation to zh-Hans
```

**Expected output when items are needed:**

```
lang_group=ko: total=5000, already_translated=25, target=50, needed=25
Selected 25/5000 items from lang_group=ko for translation to zh-Hans
```

### Method 2: Run Django Shell Query

```bash
python manage.py shell --command "
from crawler_admin.models import TrendItem, ItemDerivation, SystemSettings
from shared.translation_selection import select_items_for_translation

hot_percent = SystemSettings.get_setting('translation_hot_percent')
print(f'Setting: {hot_percent}%')

# Test selection
selected = select_items_for_translation('zh-Hans', limit=1000)
print(f'Selected: {len(selected)} items')
"
```

### Method 3: Use Verification Script

```bash
python verify_translation_selection_fix.py
```

This will:
- Show current quota status for all lang_groups
- Run selection
- Compare expected vs actual selection counts
- Report PASS/FAIL for each lang_group

---

## 📈 Expected Behavior Changes

### Impact on Existing Deployments

| Setting | Before Fix | After Fix |
|---------|-----------|-----------|
| 0.001% (10K items) | Translates all 10K items over time | Translates exactly 1 item, then stops |
| 10% (10K items) | Translates all 10K items over time | Translates exactly 1,000 items, then stops |
| 50% (10K items) | Translates all 10K items over time | Translates exactly 5,000 items, then stops |

### For Users

**What this means:**
- `translation_hot_percent` is now a **quota**, not a **rate**
- Once the quota is filled, the worker stops selecting more items for that lang_group
- The worker will only resume when:
  - New items are collected (increases total count)
  - The percentage setting is increased
  - Existing translations are deleted (decreases already_translated count)

**This is the INTENDED behavior** - the setting should represent "translate the top X% hottest items," not "continuously translate X% of remaining items."

---

## 🔄 Rollback Plan (if needed)

If the new logic causes issues:

1. **Revert the code:**
   ```bash
   git diff src/shared/translation_selection.py > /tmp/translation_fix.patch
   git checkout src/shared/translation_selection.py
   ```

2. **Restart worker:**
   ```bash
   ./setup.sh restart
   ```

3. **Verify old behavior restored:**
   ```bash
   tail -f logs/translation_worker.log | grep "Selected.*from lang_group"
   # Should see selections on every cycle again (old buggy behavior)
   ```

---

## 📝 Performance Impact

**Before:** 1 query per lang_group (untranslated items only)
**After:** 2-3 queries per lang_group:
- 1 COUNT query for all items
- 1 COUNT query for already-translated items
- 1 SELECT query for untranslated items (if needed > 0)

**Impact:** Negligible
- All queries use indexed fields (`hotness`, `lang_group`, `base_lang`)
- COUNT queries are fast
- Selection query only runs if items are actually needed

---

## ✅ Conclusion

The fix is **working correctly** and resolves the core issue:

- ✅ Calculates percentage from TOTAL items (including already-translated)
- ✅ Tracks quota per lang_group
- ✅ Stops selecting when quota is filled
- ✅ Handles edge cases (manual translations, small buckets, partial quotas)
- ✅ Provides clear logging for debugging

**No further action needed** - the translation selection logic now behaves as intended!
