# Trend Crawler Architecture - Overview

**Part 1 of 4** | [Data Model](./DESIGN-DATA-MODEL.md) | [Surfaces](./DESIGN-SURFACES.md) | [Workers & API](./DESIGN-WORKERS-API.md)

---

## Executive Summary

This document describes a **culture-flexible trend crawler framework** designed to collect and aggregate trending content from multiple platforms across different regions and cultures. The system is architected around the concept of "trend surfaces" rather than platform-specific scrapers, making it easy to add new regions through configuration rather than code rewrites.

---

## Core Philosophy

### Culture-Flexible, Not Platform-Aware

**Traditional Approach (AVOID):**
```
❌ "Crawl posts from Reddit"
❌ "Scrape YouTube trending"
❌ Platform-centric design
```

**Our Approach (CORRECT):**
```
✅ "Collect trend signals from surfaces, per region/culture"
✅ Region-first design
✅ Surfaces are configurable trend indicators
```

### Key Principles

1. **Regions Define What Matters**: Different cultures have different platforms that matter
   - US: Reddit, YouTube, Twitter
   - Japan: Yahoo Japan, Niconico, 2channel
   - Korea: Naver, Daum, DCInside
   - China: Weibo, Zhihu, Douyin

2. **Surfaces Are Trend Indicators**: A surface is any source that reveals trending topics
   - Ranking pages (hot/trending/top lists)
   - Algorithmic feed samples
   - Search trend pages
   - News portal headlines

3. **Configuration Over Code**: Adding a new region should be mostly admin configuration
   - Add Region in Django Admin
   - Configure TrendSurfaces with collectors
   - Optionally implement new collector if platform not yet supported

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Django Admin                          │
│  (Configure Regions, Surfaces, Translation Settings)         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       SQLite Database                        │
│  Regions │ TrendSurfaces │ TrendItems │ Translations        │
└─────────────────────────────────────────────────────────────┘
        ▲                           │                    ▲
        │                           │                    │
┌───────┴────────┐         ┌────────▼────────┐   ┌──────┴────────┐
│  Surface       │         │   FastAPI       │   │  Translation  │
│  Worker        │         │   Read APIs     │   │  Worker       │
│  (Async Poll)  │         │   (Public)      │   │  (Async Poll) │
└────────────────┘         └─────────────────┘   └───────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│              Surface Collectors (Plugin System)              │
│  reddit_hot │ youtube_trending │ yahoo_jp │ naver_realtime  │
└─────────────────────────────────────────────────────────────┘
```

### Components

1. **Django Admin**: Configuration UI for non-technical users
2. **SQLite Database**: Shared storage between FastAPI and Django
3. **Surface Worker**: Polls configured surfaces and collects trend items
4. **Translation Worker**: Asynchronously enriches items with translations
5. **FastAPI Server**: Public read-only APIs for consuming trend data
6. **Surface Collectors**: Pluggable modules implementing the collector interface

---

## Supported Locales

All collectors must output one of these locales:

- `zh-Hans` - Chinese Simplified
- `zh-Hant` - Chinese Traditional
- `en-US` - English (US)
- `es-ES` - Spanish (Spain)
- `fr-FR` - French (France)
- `de-DE` - German (Germany)
- `ja-JP` - Japanese (Japan)
- `ko-KR` - Korean (South Korea)
- `ru-RU` - Russian (Russia)
- `ar-SA` - Arabic (Saudi Arabia)

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Admin UI** | Django Admin | Quick setup, powerful out-of-box features |
| **Read API** | FastAPI | High performance, async, auto-generated docs |
| **Database** | SQLite | Simple, shared between Django/FastAPI |
| **Workers** | Async Python | No heavy queue infrastructure needed |
| **Translation** | DeepL + OpenAI | DeepL for quality, OpenAI as fallback |
| **Scheduler** | Database-driven polling | No cron/Celery needed for simplicity |

### File Structure

```
crawler/
├── src/
│   ├── crawler_admin/          # Django app
│   │   ├── models.py           # Region, TrendSurface, TrendItem, etc.
│   │   ├── admin.py            # Admin UI configuration
│   │   └── migrations/
│   ├── crawler_api/            # FastAPI app
│   │   ├── main.py             # FastAPI app + routes
│   │   ├── surfaces/
│   │   │   ├── interfaces.py   # TrendSurfaceCollector protocol
│   │   │   ├── reddit_hot.py
│   │   │   ├── youtube_trending.py
│   │   │   └── yahoo_jp_ranking.py
│   │   └── translation/
│   │       ├── providers.py    # DeepL, OpenAI providers
│   │       └── worker.py       # Translation worker logic
│   └── shared/
│       └── db.py               # SQLite connection
├── scripts/
│   ├── run_surface_worker.sh
│   └── run_translation_worker.sh
├── docs/
│   ├── DESIGN-OVERVIEW.md      # This file
│   ├── DESIGN-DATA-MODEL.md    # Data model details
│   ├── DESIGN-SURFACES.md      # Surface plugin system
│   ├── DESIGN-WORKERS-API.md   # Workers and API design
│   ├── README.md               # Quick start guide
│   └── API.md                  # API reference
└── README.md
```

---

## Success Criteria

The system is considered successful if:

✅ **Culture-Flexible**: Adding Japan took < 1 hour (mostly config)
✅ **Extensible**: New surfaces can be added without core code changes
✅ **Fault-Tolerant**: One surface failure doesn't crash the system
✅ **Scalable**: Can handle 10+ regions with 5+ surfaces each
✅ **Multilingual**: Automatic translation to all supported locales
✅ **Maintainable**: Non-technical users can configure via Django Admin

---

## Design Requirements

### ✅ DO:
- Model as "trend surfaces per region"
- Make regions first-class entities
- Use configuration for new surfaces
- Keep collectors stateless and simple
- Deduplicate items by canonical hash
- Handle errors gracefully (don't crash workers)

### ❌ DON'T:
- Hardcode platform assumptions in core code
- Assume all regions use the same platforms
- Skip deduplication (causes duplicates across surfaces)
- Block ingestion on translation (it's async!)
- Put business logic in admin UI
- Create monolithic collectors (keep them focused)

---

## Next Steps

After implementing this skeleton:

1. **Add Real Collectors**: Replace stub collectors with actual API integrations
2. **Monitoring**: Add metrics and alerts for worker health
3. **Rate Limiting**: Implement per-surface rate limiting to respect API quotas
4. **Caching**: Add Redis for API response caching
5. **Webhooks**: Optional webhook notifications for new trends
6. **Search**: Add full-text search across trend items
7. **Analytics**: Aggregate trend patterns over time

---

## Documentation Structure

- **[DESIGN-OVERVIEW.md](./DESIGN-OVERVIEW.md)** (this file): Core philosophy and architecture
- **[DESIGN-DATA-MODEL.md](./DESIGN-DATA-MODEL.md)**: Database models and schema
- **[DESIGN-SURFACES.md](./DESIGN-SURFACES.md)**: Surface plugin system and collectors
- **[DESIGN-WORKERS-API.md](./DESIGN-WORKERS-API.md)**: Worker architecture and API design
- **[README.md](./README.md)**: Quick start and user guide
- **[API.md](./API.md)**: Complete API reference

---

**Document Version**: 1.0
**Last Updated**: 2024-01-15
**Next**: [Data Model Design →](./DESIGN-DATA-MODEL.md)
