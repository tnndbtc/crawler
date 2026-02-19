# Culture-Flexible Trend Crawler

## Project Purpose

The Culture-Flexible Trend Crawler is a multi-region content aggregation system designed to collect trending content from diverse platforms (social media, news, video, search) across different cultural regions and languages. The system serves as a candidate generator for ranking and recommendation systems by collecting, deduplicating, translating, and organizing trend signals from 19+ data sources including Reddit, YouTube, Hacker News, Google Trends, and major news outlets. All content is crawled asynchronously with built-in politeness controls (rate limiting, circuit breakers, caching) to ensure reliable and respectful data collection. Translation happens selectively and asynchronously based on engagement scores, ensuring fast collection while enabling cross-cultural analysis.

---

## Current Features (LOCKED FOR v1)

This section documents all features currently implemented and supported in v1. These behaviors form the contract for this release and will be maintained for backward compatibility.

### 1. Data Collection

**Supported Platforms:**
- **Social & Forums**: Reddit (hot posts, configurable subreddits), Twitter/X (trending topics), Hacker News (top stories)
- **Video**: YouTube (trending videos)
- **News Sources**: Associated Press, Reuters, Guardian, BBC News, Al Jazeera, IGN, Polygon, Variety, Billboard, Wenxuecity (Chinese news)
- **Search & Discovery**: Google Trends (search spike tracking), Google News, Yahoo Japan Ranking
- **Generic RSS**: Configurable RSS/Atom feed collector for any feed

**Surface Types:**
- Five surface types supported: ranking (curated lists), sampler (algorithmic feeds), search (search trends), news (news portals), and rss_feed (generic RSS)

**Diversity Buckets:**
- Content is categorized into 13 diversity buckets: hot_now, rising, news, 8 category buckets (tech, sports, entertainment, finance, gaming, lifestyle, science, politics), region_local, and evergreen
- System enforces a 40% cap per bucket to prevent any single content type from dominating feeds

**Content Capture:**
- System captures complete data for each item: original title and description, source URL, original language locale, platform-specific ID, ranking position, engagement metrics (upvotes, comments, views, shares, score), publication and collection timestamps
- All items preserve the complete raw platform response for future analysis (never throw away data)

**Scheduled Execution:**
- Each data source has a configurable poll interval (how often to collect)
- Maximum items per collection run is configurable per source
- All executions are logged in audit records with metrics (fetched, stored, deduplicated counts)

### 2. Storage & Deduplication

**Hash-Based Deduplication:**
- Items are deduplicated using SHA256 hash of normalized title + URL
- Normalization includes lowercase conversion and whitespace trimming
- Duplicate items are detected before insertion and skipped

**Metrics Tracking:**
- Every collection run tracks how many items were fetched from the platform, how many were new and stored, and how many were duplicates and skipped
- Deduplication rate is calculated and logged for observability

### 3. Canonicalization

**URL Normalization:**
- URLs are canonicalized by lowercasing scheme and domain, removing default ports, sorting query parameters alphabetically, and stripping tracking parameters (utm_*, fbclid, gclid, twclid, ref, referrer, etc.)
- Optionally strips URL fragments (#section) for consistent deduplication

**Language Detection:**
- System automatically detects the base language of collected content using Google's language detection library
- Detected language is stored at three levels: base language code (ISO 639-1 like 'en', 'zh'), full locale/region tag (BCP47 like 'en-US', 'zh-Hans'), and language grouping key for feeds (handles variants like zh-Hans/zh-Hant both mapping to 'zh')
- Detection combines title and description for better accuracy and falls back to region's default language on failure

### 4. Translation (Current Behavior)

**Translation Engines:**
- Two professional translation engines supported: DeepL (primary) and OpenAI GPT-based translation with customizable prompts (fallback)
- Admin can configure separate engines for canonical translation (to English) vs display translation (to other languages like Chinese)

**Translation Types:**
- **Canonical Translation**: Translates all non-English content to en-US for cross-regional analysis and ranking (locked to en-US target)
- **Display Translation**: Translates content to user-facing locales (currently zh-Hans Chinese Simplified) for readable UI display

**Selective Translation (Hotness-Based):**
- Display translation is selective, not universal - only the top trending items are translated
- System partitions items by language group (e.g., 'en', 'zh', 'ja'), sorts by engagement-based hotness score, and translates the top X% (default: 10%, admin-configurable)
- Small language groups (fewer than 20 items) use fixed min/max thresholds instead of percentages
- Items with existing translations are skipped (idempotent), and same-language translations are never attempted (e.g., English to English)

**Provider Health & Failover:**
- System tracks translation provider health (available, rate limited, quota exceeded, authentication failed, transient errors)
- When a provider becomes unavailable, system automatically tries the next provider in the configured fallback order
- If all providers are unavailable, system enters a STOPPED state and runs health probes every 5 minutes until recovery
- Pending translations are preserved during outages (not marked as failed) and resume when providers recover

**Asynchronous Processing:**
- Translation never blocks content collection - items are collected first, then translated asynchronously in the background
- Translation worker processes items in batches with configurable batch size and polling interval

### 5. Media Extraction (Images/Videos)

**Current Behavior:**
- **Media URLs are REFERENCED, not stored locally** - the system captures and preserves media URLs from source platforms but does NOT download, cache, or re-host media files
- Image and video URLs are stored in the raw platform response payload and the item URL field
- For Reddit content specifically, system detects i.redd.it and v.redd.it hosted media URLs and uses the permalink for access

**UI Behavior:**
- Frontend/UI applications are expected to embed original media sources directly (e.g., display images from i.redd.it URLs, embed YouTube videos)
- Media availability depends entirely on the source platform - if the source deletes or moves media, it will no longer be accessible

**Attribution & Copyright:**
- All media remains hosted by and attributed to the original source platform
- System makes no copyright guarantees - users consuming crawler data must verify licensing and attribution requirements for any media they display
- Media URLs may become invalid if source content is deleted, moved, or access-restricted by the original platform

### 6. API Behavior

**REST API Endpoints:**
- Read-only REST API provided via FastAPI framework
- Health check endpoint for monitoring system status
- Regions API: list all cultural/geographic regions (with optional filter for enabled regions only)
- Surfaces API: list all data sources (with optional filters for region and enabled status)
- Trends API: retrieve trend items with cursor-based pagination (filters: region, bucket, lang_group; pagination: cursor, limit up to 200; language selection: en-US or zh-Hans)

**Trends API Response:**
- Returns original content (title, description, locale, URL), canonical English translation, and localized display translation (based on lang parameter)
- Includes engagement data (rank position, upvotes/views/shares/comments), timestamps (published_at, collected_at), and metadata (region, platform, bucket)

**Language-Specific Behavior:**
- **English (en-US)**: Standard pagination with limit + 1 for has_more detection; falls back to canonical English translation if display translation unavailable
- **Chinese (zh-Hans)**: Chunk-based scanning (scans 250 items at a time, max 2000 total) and ONLY returns items with complete zh-Hans translation (no fallback to English)

**Observability Endpoints:**
- Crawl health endpoint showing per-surface execution status, last run timestamp, and collection metrics
- Translation health endpoint showing translation queue status, provider health (status, available providers, recent errors), and pending/complete/failed counts per locale

**CORS Configuration:**
- CORS enabled for localhost development (ports 3000) and specific local network IPs for testing

### 7. Ranking & Freshness Behavior

**Hotness Score Algorithm:**
- Each item receives a hotness score computed from recency (exponential time decay with half-life of ~14 hours) multiplied by log-scaled engagement (upvotes, comments, views, shares, ranking position) multiplied by 100
- Time decay ensures old content naturally loses visibility (24-hour-old content retains ~30%, 48-hour-old retains ~9%)
- Logarithmic engagement scaling prevents viral outliers from completely dominating feeds
- Typical score ranges: new items with 100 upvotes score 200-300; 24-hour-old items with 1000 upvotes score 100-150

**Feed Ordering:**
- API returns items sorted primarily by collection time (newest first), with hotness as secondary tie-breaker for items collected at the same time, and item ID as tertiary for stability
- Note: This represents the crawler's default ordering - actual feed rendering order is determined by downstream intelligence/ranking layers

**Recomputation (Disabled by Default in v1):**
- Hotness scores are computed immediately when items are collected
- Hotness worker exists but is disabled by default (via `crawler_hotness_worker_enabled` setting set to false) - newly crawled items appear immediately without waiting for score computation
- When enabled, worker can recompute scores periodically for recent items (less than 48 hours old)

### 8. Reliability & Politeness

**Rate Limiting (Per-Domain):**
- Configurable requests-per-minute (RPM) limit per domain (default: 30 RPM)
- Configurable maximum concurrent requests per domain (default: 5)
- Sliding window rate limiting with 60-second windows
- Adaptive backoff: multiplier increases automatically on 429 (rate limit) or 403 (forbidden) errors and gradually recovers on successful requests

**Circuit Breaker (Per-Domain):**
- Three states: closed (normal operation), open (blocking all requests after repeated failures), half_open (testing recovery with single request)
- Automatically opens circuit after configurable consecutive failures (default: 5)
- Cooldown period when circuit is open (default: 5 minutes) before attempting half_open recovery
- Automatically transitions from half_open to closed on successful request, or back to open on failure

**HTTP Caching:**
- Stores ETag and Last-Modified headers from HTTP responses
- Sends If-None-Match and If-Modified-Since on subsequent requests to same URLs
- Handles 304 Not Modified responses efficiently (skips re-processing unchanged content)
- Parses Cache-Control headers for cache expiration timing

**Retry Logic:**
- Maximum 3 retry attempts per request with exponential backoff and jitter
- 429 Rate Limit errors: respects Retry-After header if present, records failure for circuit breaker
- 5xx Server errors: exponential backoff between retries
- 403 Forbidden and 404 Not Found: no retries (potential blocking or missing content)
- Network errors (timeout, connection failures): exponential backoff up to 3 attempts

**Human Browsing Simulation (Optional):**
- When enabled, adds human-like behaviors: homepage visit before content fetch (20% probability), random pre-article delay of 2-15 seconds (no request, just wait), favicon prefetch (15% probability)
- Budget enforcement limits simulated requests to 30% of actual content requests plus a base allowance per domain per hour (default: 1)
- Adaptive backoff triggers on 429/403 errors: 15-minute penalty window with reduced simulation probability (50% reduction) and increased delays (2.5x multiplier)
- All simulated and actual requests are tracked separately in metrics for observability

### 9. Configuration & Admin Controls

**Django Admin Interface:**
- Web-based admin UI at `/admin/` for configuration and monitoring
- Admins can configure regions (geographic/cultural areas with default locales and enabled status)
- Admins can configure data sources (surfaces) with settings: target region, platform, surface type, diversity bucket, collector entrypoint, poll interval, max items per run, surface-specific config JSON, enabled/disabled status
- Admins can configure system-wide settings via key-value pairs (see below)
- Admins can configure per-domain policies for rate limiting, circuit breakers, and human browsing simulation
- Admins can view and monitor crawl execution audit logs, translation queue status, provider health, and domain metrics

**System Settings (Admin-Configurable Key-Value Store):**
- **Translation**: hot percentage for selective translation (default: 10%), small bucket min/max thresholds, target locales, source languages
- **HTTP Client**: enable/disable rate limiting, enable/disable circuit breaker, enable/disable HTTP caching, default RPM and concurrency limits
- **Human Browsing**: enable/disable simulation, homepage visit probability, favicon prefetch probability, base allowance per domain per hour, backoff penalty duration
- **Workers**: hotness worker enabled/disabled (default: disabled in v1, newly crawled items appear immediately)

**Translation Configuration (Global Settings):**
- Engine selection for canonical and display translation (DeepL, OpenAI, or test engines)
- LLM model selection when using OpenAI (GPT-4o-mini, Claude, etc.)
- Fallback order when primary engine fails
- Master enable/disable switch for all translation
- Production mode flag to disable test engines
- Character limit per translation request and batch size for worker processing

**Environment Variables:**
- Django settings: secret key, debug mode, allowed hosts, database URL, log level
- Translation API keys: DeepL API key, OpenAI API key
- Worker configuration: dry-run mode (no DB writes except audit logs), collector timeout, worker poll intervals, translation backlog threshold, auto-exit behavior

**Observability:**
- Every collection run logged with execution timing (started/finished timestamps), success or failure status, metrics (fetched/stored/deduplicated counts), error messages if failed, execution duration in milliseconds
- Hourly metrics aggregated per domain: request counts by status code (2xx, 4xx, 5xx, 429, 403), latency percentiles (p50, p95), retry counts, circuit breaker open duration, human browsing metrics (direct vs simulated requests, fallbacks, budget blocks)
- Translation provider health tracked: provider status, pending/complete/failed counts per locale, last success/error timestamps, consecutive failure counts
- Surface health tracked: last run timestamp, last successful run timestamp, last error message

---

## Media Safety & Attribution

### How Media is Handled

**The crawler does NOT store, cache, or re-host any media files.** Media URLs (images, videos, thumbnails) are captured from source platforms and preserved in the item data, but the actual media content remains hosted exclusively by the original platform (e.g., Reddit's i.redd.it, YouTube, news site CDNs).

**Frontend/UI Responsibility:**
- Any application consuming this crawler's data must embed or link to media using the original source URLs
- The consuming application bears responsibility for verifying copyright, licensing, and attribution requirements before displaying media
- Attribution to the original platform and content creator should be clearly visible (e.g., "Source: Reddit u/username" or "Source: YouTube - Channel Name")

**Availability & Permanence:**
- Media availability depends entirely on the source platform's hosting
- If the original content is deleted, made private, or geo-restricted by the source, the media will no longer be accessible via the crawler's stored URLs
- The crawler makes no guarantees about media permanence or availability

**Copyright & Legal Compliance:**
- This system provides URLs to publicly accessible content; it does not grant any copyright licenses
- Organizations using this crawler data must independently verify their right to display or redistribute media in their jurisdiction
- The crawler is designed for trend discovery and analysis, not for media archival or redistribution
- Consuming applications should implement DMCA/copyright takedown procedures to handle requests from content owners

---

## Out of Scope (NOT in v1)

The following features are intentionally NOT part of the v1 crawler contract and are handled by separate systems or future development:

**Intelligence & Recommendation:**
- AI-powered content summarization or headline generation
- Hook generation or engagement optimization
- Topic clustering or semantic grouping
- Personalized recommendation ranking
- User preference learning or collaborative filtering

**Content Enhancement:**
- Sentiment analysis or toxicity detection
- Entity extraction or knowledge graph building
- Automated tagging or categorization beyond source-provided data
- Content quality scoring beyond engagement signals
- Duplicate detection across linguistic/semantic variations (only exact hash-based dedup in v1)

**Media Processing:**
- Image recognition, OCR, or visual analysis
- Video transcription or scene detection
- Thumbnail generation or image resizing
- Media format conversion or optimization
- Video clip extraction or highlight generation

**User Features:**
- User accounts, authentication, or authorization
- Saved items, bookmarks, or reading lists
- User feedback (likes, dislikes, reports)
- Personalized feeds or filtering
- UI/frontend applications (crawler is backend-only)

**Advanced Analytics:**
- Trend prediction or forecasting
- Anomaly detection for viral content
- Cross-platform trend correlation
- Historical trend analysis or time-series modeling
- Geographic spread tracking

---

## Stability Guarantee

**This README documents the v1 contract for the Culture-Flexible Trend Crawler.** All behaviors, APIs, and features described in the "Current Features (LOCKED FOR v1)" section represent the stable, supported interface for this release.

**Backward Compatibility Commitment:**
- The API response format, field names, and data types documented above will remain stable
- Existing API endpoints will continue to function as described
- Database schema changes will be additive (new fields/tables only, no breaking removals)
- Configuration options will remain available (new settings may be added)

**Future Development:**
- New features and capabilities may be added in future versions
- Changes will be strictly additive - existing functionality will not be removed or altered in breaking ways
- Major version increments (v2, v3, etc.) will be explicitly announced if breaking changes become necessary
- Deprecation warnings will be provided at least one major version in advance of any breaking changes

**Admin Visibility:**
- Any changes to system behavior (new features, optimizations) will be documented in release notes
- Admin configuration changes will be backward compatible (defaults will preserve v1 behavior)
- Database migrations will be automatic and non-destructive

This stability guarantee ensures that applications built on the v1 crawler contract can safely upgrade to future versions without breaking changes.
