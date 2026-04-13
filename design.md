# Classification System

> **Status:** Goals locked 2026-04-12. Design reviewed and ready for implementation.

---

## Part 1 — Goals

### Purpose

Define the minimum classification guarantees the crawler must provide so the story engine can select from trustworthy, stable metadata — without rebuilding semantics at selection time.

---

### Goal 1 — Single canonical taxonomy shared by crawler and story engine

The crawler must emit `story_category` using the same vocabulary the story engine uses for allocation, balancing, and format eligibility. The story engine must not depend on a remapping layer as the normal path.

**Current problem:** The crawler emits 12 raw topic tags; the story engine remaps them to 8 story categories. Two vocabularies that can drift independently. `"ai"` and `"tech"` collapse into one. `"world"` is not expressible as a crawler tag at all.

**Required:** The crawler produces `story_category` directly using this canonical 9-value vocabulary:

```
world | politics | business | technology | ai | science | society | sports | entertainment
```

`"ai"` is a first-class category, separate from `"technology"`:
- `"ai"` — machine learning, LLMs, AI models, AI policy, AI companies
- `"technology"` — chips, software, devices, platforms

Items carrying the crawler tag `ai` map to category `ai`, not `technology`. Items carrying only `tech` map to `technology`.

---

### Goal 2 — Classification must be complete before story selection

Items reaching the story engine selector must already carry a resolved `story_category`. Selection-time semantic inference should be an exception handler only, not the normal path.

**Current problem:** A 10-minute polling gap means items can be selected while still unclassified. Sources 2 and 3 in `story_mix.json` exist precisely to cover this gap — they are workarounds, not design.

**Required:** The story engine only selects items where:
```sql
classification_state NOT IN ('pending', 'failed')
```

Both states are hard-excluded from the candidate pool. Failed items must not enter selection — they carry no useful category and would inflate an "unknown" bucket, triggering dominance warnings that mask real supply gaps.

---

### Goal 3 — Explicit classification state

Every item must carry a machine-readable state that tells consumers exactly where it stands in the classification pipeline.

**Required states:**

| State | Meaning |
|---|---|
| `pending` | Not yet attempted |
| `heuristic_complete` | Heuristic ran and produced a result |
| `llm_complete` | LLM pass ran and produced a result |
| `failed` | All passes attempted; no result produced |

**Current problem:** The system uses `topic_classified_at IS NULL` as a proxy for "pending" and `topic_tags = []` as a proxy for "failed." These are ambiguous and force consumers to infer state rather than read it.

---

### Goal 4 — Explicit classification provenance

Every classified item must record how its classification was produced.

**Required fields:**

| Field | Description |
|---|---|
| `classified_by` | Which signal layer produced the result: `bucket / platform / keyword / locale / surface / llm` |
| `classification_state` | See Goal 3 |
| `topic_classified_at` | When classification was last attempted |

`classified_by` and `topic_classified_at` already exist. `classification_state` must be added.

---

### Goal 5 — Tiered classification: cheap on the full corpus, expensive only where it adds value

Heuristic classification must run on all items. LLM classification must be reserved for items where the heuristic is insufficient:
- Empty result (no category produced)
- Low-confidence result (surface or locale fallback only)
- High-hotness items where a weak classification is likely wrong

**Current problem:** LLM rescue only targets items with empty `topic_tags`. Items that received a weak fallback classification (e.g. locale fallback) are not eligible for LLM rescue even when high-hotness.

**Required:** LLM eligibility is based on confidence tier, not just empty tags.

Confidence tier is derived from `classified_by`:

| `classified_by` | Confidence tier | LLM eligible? |
|---|---|---|
| `bucket` | high | No |
| `keyword` | medium | No |
| `platform` | medium | No |
| `surface` | low | Yes — if high-hotness |
| `locale` | low | Yes — if high-hotness |
| `llm` | high | No (already rescued) |
| _(empty / failed)_ | none | Yes — unconditionally |

"High-hotness" threshold uses the existing `translation_hot_percent_<lang>` SystemSetting, same as the current LLM pass filter.

---

### Goal 6 — Safe reclassification

The system must support reclassification when keyword maps change or taxonomy changes, without silently mixing old and new labels.

**Required:** Items carry a `classification_version` that identifies which keyword map and taxonomy version produced the result. When either changes, affected items can be identified and re-queued without ambiguity.

Version format:

| Component | Source |
|---|---|
| `keyword_map_version` | SHA-256 of `auto_keywords.json` at load time; recomputed each worker cycle |
| `taxonomy_version` | Integer constant in code; bumped manually on vocab change (current: `1`) |

Stored as a composite string: `"<taxonomy_version>:<keyword_map_sha[:8]>"` — e.g. `"1:a3f2c8d1"`.

**Re-queue logic:** On worker startup, compute the current version. Find all items where `classification_version != current_version AND classified_by != 'llm'`. Reset their `classification_state` to `pending` for re-processing.

LLM-classified items are exempt — their results are authoritative and reclassification must be triggered explicitly.

**Current problem:** Keyword hot-reload resets "tried, no tags" items but records no version. Items classified under an old keyword map look identical to items classified under the new one.

---

### What is already correct — do not change

- Heuristic on full corpus, LLM only on selected subset: correct cost architecture
- Hotness-prioritised processing within each cycle: correct for story use
- Dynamic keyword updates via `auto_keywords.json` hot-reload: keep as-is
- Signal priority order (`bucket → platform → keyword → locale → surface`): keep as-is

---

### Goals reviewed and dropped

**Confidence score as a numeric field** — subsumed by `classification_state` + `classified_by`. Consumers can derive confidence tier from these two fields without a separate score column.

**Deterministic classification as a separate goal** — this is an implementation constraint on the LLM pass, not a standalone system goal. Heuristic is already deterministic. LLM results are stored and not recomputed unless reclassification is explicitly triggered (covered by Goal 6).

**Full auditability / reclassification history** — out of scope. Goal 4 (provenance) plus Goal 6 (version) provide enough to explain any current classification. Historical audit trail belongs in a logging system, not the classification DB.

---

### Architectural conclusion

The crawler must own semantic classification end-to-end. The story engine must consume canonical, story-ready metadata. Selection-time derivation (Sources 2 and 3 in `story_mix.json`) must be reduced to a true emergency fallback — not the steady-state design.

"Emergency fallback" is defined and measurable:
- Sources 2 and 3 invocation rate must be **< 5%** of selected candidates in a healthy batch.
- If this threshold is exceeded, it indicates crawler pipeline degradation: worker down, timing gap, or a new surface not yet configured.
- This rate must be logged per batch and surfaced in observability output.

The current 3-layer model is a symptom of incomplete crawler ownership. The fix is not to improve the remapping layer — it is to eliminate the need for it.

---

## Part 2 — Design

### Overview

Four coordinated changes deliver all six goals:

| Section | Change | Delivers |
|---|---|---|
| A | Schema extension — 3 new fields on `TrendItem`, 1 altered field | Goals 3, 4, 6 |
| B | Classifier redesign — `topic_classifier.py` produces `story_category` directly | Goal 1, 5 |
| C | Worker redesign — state machine, version management, expanded LLM eligibility | Goals 2, 5, 6 |
| D | Story engine contract — taxonomy, state, and selection eligibility (self-contained) | Goals 1, 2, 3 |
| E | Keyword harvest — group by `story_category`; vocabulary key migration | Goal 1 |

> `topic_tags` is retained on the model for backward compatibility during transition but is no longer the primary output. `story_category` is the canonical contract field.

---

### A. Schema Changes

**Migration 0026** — add three fields and alter one:

#### `story_category`
```
CharField(16), null=True, blank=True, db_index=True
Choices: world | politics | business | technology |
         ai | science | society | sports | entertainment
```
`NULL` means not yet classified (`classification_state = 'pending'`). Set by `topic_classifier_worker`.

#### `classification_state`
```
CharField(20), default='pending', db_index=True
Choices: pending | heuristic_complete | llm_complete | failed
```
Replaces the `topic_classified_at IS NULL` proxy. Set by `topic_classifier_worker`.

#### `classification_version`
```
CharField(32), null=True, blank=True
Format: "<taxonomy_version>:<keyword_map_sha[:8]>"
Example: "1:a3f2c8d1"
```
`NULL` on legacy items not yet re-processed. Set by `topic_classifier_worker` after each successful classification.

#### `classified_by` (altered)
```
ALTER max_length 16 → 32
```
Vocabulary expands from `('heuristic', 'llm')` to: `bucket | platform | keyword | locale | surface | llm`. `NULL` means pending (not yet classified).

---

**Migration 0027** — data backfill (runs once, no revert needed):

**Step 1: Set `classification_state` from existing field values.**

Rules are applied as a `CASE WHEN` expression (first match wins):

| Condition | → `classification_state` |
|---|---|
| `classified_by = 'llm'` | `'llm_complete'` |
| `topic_classified_at IS NULL` | `'pending'` |
| `classified_by = 'heuristic' AND topic_tags != []` | `'heuristic_complete'` |
| `classified_by = 'heuristic' AND topic_tags = []` | `'failed'` |
| `topic_classified_at IS NOT NULL AND classified_by IS NULL` | `'heuristic_complete'` |
| ELSE | `'pending'` |

> `classified_by = 'llm'` must be the first rule. Any LLM item with `topic_classified_at IS NULL` (unusual but possible due to historic bugs) would otherwise be incorrectly assigned `'pending'`. The `ELSE` clause catches all remaining combinations and routes them safely to `'pending'` for re-classification.

**Step 2: Derive `story_category` for LLM items — do not reset these.**

LLM items are exempt from the worker re-queue and must carry a valid `story_category` after migration. Derive it from `topic_tags` using the mapping below, selecting the highest-priority tag.

Raw-tag → `story_category` mapping:

| Raw tag | `story_category` |
|---|---|
| `ai` | `ai` |
| `tech` | `technology` |
| `politics` | `politics` |
| `finance` | `business` |
| `business` | `business` |
| `science` | `science` |
| `society` | `society` |
| `health` | `society` |
| `crime` | `society` |
| `environment` | `science` |
| `sports` | `sports` |
| `entertainment` | `entertainment` |

Within-category priority (highest wins when multiple tags are present):
```
ai > politics > business > technology > science > world > society > sports > entertainment
```

Example: `topic_tags = ['tech', 'ai']` → `story_category = 'ai'`

Edge case: `topic_tags = []` on an LLM item → `story_category = NULL`, `classification_state = 'failed'`.

**Step 3: Reset non-LLM items for re-queue.**

For all items where `classified_by != 'llm'`:
- Set `classification_state = 'pending'`
- Set `story_category = NULL` (repopulated on re-queue)
- Set `classification_version = NULL` (triggers version re-queue on startup)

LLM items: do **not** reset `classification_state`, `story_category`, or `classification_version`.

---

### B. Classifier Redesign

**File:** `shared/topic_classifier.py`

The new primary function replaces `classify_topic_tags()`:

```python
classify_item(bucket, platform, surface_key, lang_group, title,
              description, auto_keywords)
    → (story_category: str | None, classified_by: str | None)
```

Signals run in priority order; the function returns on the first hit:

| Priority | Signal | Returns |
|---|---|---|
| 1 | Bucket match | `(category, 'bucket')` |
| 2 | Platform match | `(category, 'platform')` |
| 3 | Keyword match | `(best_category, 'keyword')` — runs on title + `description[:300]` using static keyword lists + `auto_keywords` per category |
| 4 | Locale match | `(category, 'locale')` |
| 5 | Surface match | `(category, 'surface')` |
| 6 | No match | `(None, None)` |

When multiple categories match at keyword level, the within-category priority order is applied (see above). If multiple categories match at the same priority level, the higher-priority category wins.

#### Updated maps

**`BUCKET_STORY_CATEGORY_MAP`**
```python
{
    'category_ai':            'ai',
    'category_tech':          'technology',
    'category_finance':       'business',
    'category_politics':      'politics',
    'category_entertainment': 'entertainment',
    'category_gaming':        'entertainment',
    'category_science':       'science',
    'category_sports':        'sports',
    'category_lifestyle':     'society',
    'category_health':        'society',
    'category_crime':         'society',
    'category_environment':   'science',
    'international_news':     'world',   # new
    'world_news':             'world',   # new
    'global_news':            'world',   # new
}
```

**`PLATFORM_STORY_CATEGORY_MAP`** (excerpt)
```python
{
    'paperswithcode': 'ai',
    'github':         'technology',
    'hackernews':     'technology',
    'devto':          'technology',
    # ... remaining platforms, tags converted to story_category
}
```

**`STATIC_KEYWORDS`**
```python
{
    'ai':            ['LLM', 'GPT', 'Claude', 'Gemini', 'Llama', 'AI ', ...],
    'technology':    ['Apple', 'Google', 'Microsoft', 'chip', 'software', ...],
    'business':      ['earnings', 'revenue', 'merger', 'acquisition', 'IPO', ...],
    'politics':      ['election', 'president', 'congress', 'senate', ...],
    'science':       ['NASA', 'climate', 'research', 'study', 'genome', ...],
    'society':       ['health', 'crime', 'education', 'culture', ...],
    'sports':        ['championship', 'tournament', 'league', 'match', ...],
    'entertainment': ['movie', 'album', 'celebrity', 'award', ...],
    # 'world' is not produced by keyword match — too ambiguous without
    # explicit editorial configuration. Produced only by bucket or surface.
}
```

> `auto_keywords.json` vocabulary keys migrate from raw tags to `story_category` strings on the first post-deploy harvest run. See Section E.
>
> Authoritative vocabulary definition: see Section D.1.

---

### C. Worker Redesign

**File:** `crawler_api/workers/topic_classifier_worker.py`

#### State machine

```
pending
  └─ heuristic pass runs
       │
       ├─ no category found  (classified_by = None, tier = none)
       │    └─ LLM eligible unconditionally  [if ENABLE_LLM]
       │         ├─ LLM finds category  →  llm_complete
       │         │                          story_category       = <llm_cat>
       │         │                          classified_by        = 'llm'
       │         │                          classification_version = <current_version>
       │         └─ LLM finds nothing   →  failed
       │
       └─ category found  (classified_by ∈ {bucket, platform, keyword, locale, surface})
            └─ evaluate confidence tier
                 │
                 ├─ tier = high or medium  (bucket, platform, keyword)
                 │    └─ heuristic_complete  (no LLM)
                 │         story_category       = <heuristic_cat>
                 │         classified_by        = <signal>
                 │         classification_version = <current_version>
                 │
                 ├─ tier = low  AND  NOT high-hotness  (locale or surface)
                 │    └─ heuristic_complete  (no LLM)
                 │         [same fields as above]
                 │
                 └─ tier = low  AND  high-hotness  (locale or surface)
                      └─ LLM pass runs  (attempt to improve weak heuristic result)
                           ├─ LLM finds category  →  llm_complete
                           │                          story_category       = <llm_cat>
                           │                          classified_by        = 'llm'
                           │                          classification_version = <current_version>
                           └─ LLM fails / no result  →  heuristic_complete
                                                          story_category       = <heuristic_cat>  [preserved]
                                                          classified_by        = <heuristic_signal>  [preserved]
                                                          classification_version = <current_version>
```

> **Key behaviour:** When LLM is attempted on a low-confidence heuristic result but produces nothing, the heuristic result is preserved — not discarded. The item is written as `heuristic_complete`, not `failed`.

#### `ENABLE_LLM`

Read from `SystemSetting 'enable_llm_classification'` (boolean, default `True`). When `False`, all LLM branches collapse:
- No-category items → `failed`
- Low-confidence items → `heuristic_complete` (heuristic result preserved)

This allows LLM to be disabled in staging or during cost-control windows without a code deploy. Uses the same `SystemSetting` pattern as `translation_worker`.

#### LLM eligibility

```python
def is_llm_eligible(classified_by, item):
    tier = confidence_tier(classified_by)
    if tier == 'none':  return True
    if tier == 'low':   return is_high_hotness(item)
    return False

def is_high_hotness(item):
    # Same SystemSetting used by translation_worker.
    # Key: 'translation_hot_percent_<lang_group>'
    threshold = SystemSetting.get('translation_hot_percent_' + item.lang_group)
    if threshold is None:
        return False  # fail safe: absent key → not high-hotness
    return item.hotness >= threshold
```

These functions are called on **both** branches of the state machine — not only when heuristic finds no category. This is what enables LLM rescue for low-confidence `surface` / `locale` items.

#### Version management

```python
TAXONOMY_VERSION = 1  # bump manually when canonical vocabulary changes

# On startup and after each auto_keywords.json hot-reload:
keyword_map_sha = sha256(open(AUTO_KEYWORDS_PATH).read())[:8]
current_version = f"{TAXONOMY_VERSION}:{keyword_map_sha}"
# AUTO_KEYWORDS_PATH: use the same absolute path as the existing
# hot-reload mechanism in keyword_harvest_worker. Do not hardcode.

# Re-queue stale items:
stale = TrendItem.objects \
    .exclude(classification_version=current_version) \
    .exclude(classified_by='llm')
stale.update(
    classification_state='pending',
    story_category=None,
    classified_by=None,
    classification_version=None,
)
```

LLM items are exempt: their classifications are authoritative and must be re-triggered explicitly. This replaces the existing `reset_tried_no_tags()` mechanism entirely.

#### Candidate query

```python
# Old:
TrendItem.objects.filter(topic_tags=[], topic_classified_at__isnull=True)

# New:
TrendItem.objects.filter(classification_state='pending').order_by('-hotness')
```

Ordering by `hotness` ensures the most valuable items are classified first within each 10-minute cycle.

#### Deploy order (first deploy only)

Migration 0027 Step 3 resets all non-LLM items to `pending`. If the story engine filter goes live before the worker re-processes the corpus, only LLM items would be selectable.

**Required order:**

1. Run migration 0026 — schema only; no data change; no downtime risk.
2. Run migration 0027 — backfill: LLM items get `story_category`; non-LLM items reset to `pending`.
3. Run `keyword_harvest_worker` once manually (one-shot, not continuous) — generates new `"technology"` and `"business"` keys in `auto_keywords.json` before the classifier goes live. Without this step, those two categories carry no auto-keywords during the first cycle. See Section E.
4. Deploy updated crawler worker code and story engine filter change together.

**Why no waiting period is needed:** The story engine runs once every 12 hours. By the time it runs, the crawler worker will have re-classified enough pending items across multiple 10-minute cycles to provide adequate supply. Hotness ordering ensures the most valuable items are classified first. There is no supply cliff given this cadence.

For subsequent deploys (keyword map or taxonomy bumps), the re-queue is incremental — only items under the old version are reset, not the full corpus.

---

### D. Story Engine Contract

> This section is self-contained. Downstream consumers (story engine, analytics, any reader of `TrendItem`) only need to read this section — not A, B, or C.

Three contracts govern the interface between the crawler and its consumers.

---

#### D.1 Taxonomy Contract

**Purpose:** Establish a single shared vocabulary. Both crawler and story engine use these values directly. No remapping layer.

**Canonical vocabulary** (9 values, all lowercase):

```
world | politics | business | technology | ai | science | society | sports | entertainment
```

**Rules:**
- `story_category` on any `TrendItem` is always one of these 9 values, or `NULL`.
- `NULL` means the item is either not yet classified (`pending`) or classification was attempted but produced no result (`failed`). In both cases the item is excluded from story selection (see D.3).
- The story engine must **not** remap, alias, or infer categories from this field. What the crawler writes is what the story engine uses.
- `"ai"` and `"technology"` are distinct:
  - `"ai"` — ML, LLMs, AI models, AI policy, AI companies
  - `"technology"` — chips, software, devices, platforms
- `"world"` has no keyword signal path. It is produced only by explicit bucket or surface configuration and will not appear unless a bucket or surface is mapped to it.

**Stability guarantee:**
- The vocabulary is versioned via `taxonomy_version` (currently: `1`).
- Any vocabulary change bumps `taxonomy_version`. Consumers can detect a vocab change by comparing `classification_version` prefixes across items.
- No value will be silently retired or renamed without a version bump.

---

#### D.2 Classification State Contract

**Purpose:** Tell consumers exactly where each item stands in the classification pipeline, so no implicit inference is needed.

| State | Meaning | `story_category` | Action for consumers |
|---|---|---|---|
| `pending` | Not yet classified | `NULL` | Do not select |
| `heuristic_complete` | Heuristic ran and produced a result | Set (trustworthy) | Safe to select |
| `llm_complete` | LLM pass ran and produced a result | Set (authoritative) | Safe to select |
| `failed` | All passes attempted; no result | `NULL` | Do not select; do not treat as "unknown" |

**Provenance fields** — always present when state is `heuristic_complete` or `llm_complete`:

| Field | Value |
|---|---|
| `story_category` | One of the 9 canonical values; never `NULL` |
| `classified_by` | Signal that produced the result: `bucket \| platform \| keyword \| locale \| surface \| llm` |
| `topic_classified_at` | Timestamp of last classification attempt |
| `classification_version` | `"<taxonomy_version>:<keyword_map_sha[:8]>"` — e.g. `"1:a3f2c8d1"`. Changes when keyword map or taxonomy vocab changes. |

LLM results (`classified_by = 'llm'`) are not automatically re-classified on taxonomy or keyword map changes. Re-classification must be triggered explicitly.

---

#### D.3 Selection Eligibility Contract

**Purpose:** Define the gate rule for story engine candidate selection.

**Rule:**
```sql
-- Preferred form (positive):
classification_state IN ('heuristic_complete', 'llm_complete')

-- Equivalent form (negative):
classification_state NOT IN ('pending', 'failed')
```

Both forms are safe to use. The negative form is more defensive against future state additions.

**Rationale:**
- `pending` items have no `story_category` — selecting them produces `NULL` categories.
- `failed` items have no `story_category` — selecting them would inflate an "unknown" bucket and mask real supply gaps.
- Both must be hard-excluded, not merely deprioritised.

**Emergency fallback** (Sources 2 and 3 in `story_mix.json`):
- Sources 2 and 3 exist as a fallback **only**. They must not be the steady-state path.
- A healthy batch has Sources 2 and 3 invocation rate **< 5%** of selected candidates.
- Exceeding 5% signals crawler pipeline degradation: worker down, classification timing gap, or a new surface not yet configured.
- This rate is logged per batch in `generate.log` via `logger.info` in `stage1_normalize.py` when the selection-time category derivation path fires.

**Read/write boundary:**

| | Fields |
|---|---|
| Story engine **may read** | `story_category`, `classification_state`, `classified_by`, `topic_classified_at`, `classification_version` |
| Story engine **must not write** | Any classification field — the crawler owns classification end-to-end |

---

### E. Keyword Harvest Changes

**File:** `crawler_api/workers/keyword_harvest_worker.py`

Two changes:

**1. Group extracted keywords by `story_category` instead of `topic_tags`.**

The source query is unchanged (`classified_by = 'llm'`). The grouping field changes from the `topic_tags` list to `story_category` (single value) — simpler and directly aligned with the new vocabulary.

**2. Vocabulary key migration on first post-deploy harvest run.**

On deploy day, `auto_keywords.json` still contains old-vocabulary keys (`"tech"`, `"finance"`). The updated classifier looks for `"technology"` and `"business"` keys. Until the first harvest run completes, those two categories carry no auto-keywords, degrading heuristic accuracy and inflating LLM invocation.

Fix:
- The harvest worker is run manually once between migration 0027 and deploying the updated classifier code (step 3 of the deploy order in Section C). This generates the new `"technology"` and `"business"` keys.
- After this run, old and new keys coexist in `auto_keywords.json`. The classifier reads only new keys; old keys are silently ignored.
- Old keys expire via the existing TTL mechanism:
  - Weak keywords (score < 10): expire after 30 days without new occurrences
  - Strong keywords (score ≥ 10): expire after 90 days
- No new expiry mechanism is required.

Monitor LLM invocation rate in the first post-deploy cycle to confirm `"technology"` and `"business"` keyword coverage is adequate.

---

### Files Changed

#### Crawler

| File | Changes |
|---|---|
| `crawler_admin/models.py` | Add `story_category`, `classification_state`, `classification_version` fields. Alter `classified_by` max_length and update help_text. |
| `crawler_admin/migrations/0026_story_category_state_version.py` | Schema migration: add three fields, alter `classified_by`. |
| `crawler_admin/migrations/0027_backfill_classification_state.py` | Data migration: Step 1 — set `classification_state`. Step 2 — derive `story_category` for LLM items. Step 3 — reset non-LLM items for re-queue. |
| `shared/topic_classifier.py` | Replace `classify_topic_tags()` with `classify_item()`. Update all maps to use `story_category` vocabulary. Single-value return only. |
| `shared/topic_llm_classifier.py` | Update LLM prompt vocabulary from 12 raw tags to 9 `story_category` values. Return type: `story_category` string (not list of tags). |
| `crawler_api/workers/topic_classifier_worker.py` | Replace candidate query (pending state). Add version management. Replace `reset_tried_no_tags()`. Replace LLM gate with `is_llm_eligible()` called on both state machine branches. Write `classification_state` and `classification_version` on each update. Preserve heuristic result when LLM fails on low-confidence item. |
| `crawler_api/workers/keyword_harvest_worker.py` | Group by `story_category`. Write `auto_keywords.json` with new vocabulary keys. Run once manually as part of deploy order (Section C). |

#### Story engine

| File | Changes |
|---|---|
| `story_engine/src/db/crawler_reader.py` | Add `classification_state NOT IN ('pending', 'failed')` filter to `get_top_items()`. This is the gate that enforces Goal 2 on the story engine side. |
| `story_engine/src/engine/selector/stage1_normalize.py` | Lines 212–223: (a) Skip category derivation when `story_category` is already set — run only when `NULL` (emergency path). (b) Log the derivation invocation rate per batch to `generate.log` via `logger.info` so the 5% threshold is observable. |

---

### Not Changing

| Component | Note |
|---|---|
| Signal priority order | `bucket → platform → keyword → locale → surface` — unchanged |
| `auto_keywords.json` hot-reload mechanism | Unchanged; version update handles stale-item reset |
| Hotness-based LLM filtering threshold | Unchanged; same `SystemSetting` key |
| Region classification | `region_classifier_worker`, `region_classifier.py` — unchanged |
| `topic_tags` field | Retained on `TrendItem`; no longer written by the heuristic pass. LLM pass continues to write `topic_tags` during the transition period. Deprecation is a follow-on task. |
| LLM items exempt from automatic reclassification | Unchanged; required by Goal 6 |

---

> **Last updated:** 2026-04-12 (rev 2 — reviewer comments applied)
