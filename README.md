# Culture-Flexible Trend Crawler

**Self-verifiable, region-first trend crawler with async translation.**

## 🎯 Overview

This crawler is **NOT a generic scraper** - it's a **candidate generator for addictive feeds** that prioritizes diversity over volume.

**Key Features:**
- ✅ **Region-first design** - Not platform-centric
- ✅ **Django Admin** - Configuration without code changes
- ✅ **Simple polling workers** - No Celery/RabbitMQ
- ✅ **Canonical language (en-US)** - For cross-regional analysis
- ✅ **Complete observability** - Every execution tracked
- ✅ **DRY_RUN mode** - Safe testing
- ✅ **Bucket diversity** - No bucket > 40% of items

**Product Philosophy:** See [`docs/DESIGN-PRODUCT-PHILOSOPHY.md`](docs/DESIGN-PRODUCT-PHILOSOPHY.md)

---

## 🚀 Quick Start

```bash
# 1. Clone and setup
cd /home/tnnd/data/code/crawler
cp .env.example .env
# Edit .env with your API keys

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run migrations
python manage.py migrate

# 4. Load initial data
python manage.py loaddata initial_data

# 5. Create admin user
python manage.py createsuperuser

# 6. Start Django Admin
python manage.py runserver 8001

# 7. Start workers (in separate terminals)
./scripts/run_surface_worker.sh
./scripts/run_translation_worker.sh

# 8. Start API server
./scripts/run_api_server.sh

# 9. Access:
# - Django Admin: http://localhost:8001/admin
# - API Docs: http://localhost:8000/docs
# - Health: http://localhost:8000/api/v1/health/crawl
```

---

## 📋 Setup Instructions

### 1. Environment Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Django
DJANGO_SECRET_KEY=your-secret-key-here
DJANGO_DEBUG=True

# Translation APIs
DEEPL_API_KEY=your-deepl-key  # Recommended
OPENAI_API_KEY=your-openai-key  # Fallback

# Worker Settings
DRY_RUN=false                    # Set to 'true' for testing
MAX_RUN_SECONDS=60               # Timeout per surface
SURFACE_WORKER_POLL_INTERVAL=60  # Seconds between polls
TRANSLATION_WORKER_POLL_INTERVAL=30

# Logging
LOG_LEVEL=INFO
```

### 2. Database Setup

```bash
# Run migrations
python manage.py migrate

# Load initial data (4 regions + 3 stub surfaces)
python manage.py loaddata initial_data

# Create admin user
python manage.py createsuperuser
```

### 3. Verify Setup

```bash
# Check Django configuration
python manage.py check

# List surfaces
python manage.py shell
>>> from crawler_admin.models import TrendSurface
>>> TrendSurface.objects.all()
```

---

## 🏃 Running the System

### Components

The system has 4 components:

1. **Django Admin** - Configuration UI
2. **Surface Worker** - Collects trending items
3. **Translation Worker** - Translates to English
4. **FastAPI Server** - Read-only data API

### Start All Components

```bash
# Terminal 1: Django Admin
python manage.py runserver 8001

# Terminal 2: Surface Worker
./scripts/run_surface_worker.sh

# Terminal 3: Translation Worker
./scripts/run_translation_worker.sh

# Terminal 4: API Server
./scripts/run_api_server.sh
```

### Testing with DRY_RUN Mode

**Important:** Always test new collectors with `DRY_RUN=true` first!

```bash
# In .env:
DRY_RUN=true

# Start surface worker
./scripts/run_surface_worker.sh

# Check logs - CrawlRun created but no TrendItems stored
# Verify: python manage.py shell
>>> from crawler_admin.models import CrawlRun, TrendItem
>>> CrawlRun.objects.count()  # Should increase
>>> TrendItem.objects.count()  # Should NOT increase (DRY_RUN)
```

**After testing succeeds:**

```bash
# In .env:
DRY_RUN=false

# Restart surface worker to actually store data
```

---

## 🔍 Django Admin Usage

### Accessing Admin

1. Start Django server: `python manage.py runserver 8001`
2. Go to: http://localhost:8001/admin
3. Login with your superuser credentials

### Configuring Surfaces

1. Navigate to **Trend Surfaces**
2. Click **Add Trend Surface**
3. Fill in fields:
   - **Region**: Select region (e.g., "United States")
   - **Key**: Unique identifier (e.g., "reddit_hot")
   - **Platform**: Platform name (e.g., "reddit")
   - **Surface type**: Choose type (ranking/sampler/search/news)
   - **Bucket**: Choose bucket (hot_now/rising/category_*/region_local/evergreen)
   - **Bucket weight**: 1.0 (higher = more priority)
   - **Entrypoint**: Python path (e.g., "crawler_api.surfaces.reddit_hot:collect")
   - **Enabled**: ✅ Check to enable
   - **Poll interval**: 3600 (seconds)
   - **Max items per run**: 100
   - **Config JSON**: Surface-specific config

4. Click **Save**

### Monitoring Health

**View CrawlRuns:**
- Navigate to **Crawl Runs**
- Filter by status (✅ Success / ❌ Failed)
- Check metrics: fetched_count, stored_new_count, deduped_count

**View Surface Health:**
- Navigate to **Trend Surfaces**
- Check **Health** column (✅ Healthy / ❌ Error / ⚪ Never run)
- Click surface to see last_error if failing

**View Missing Translations:**
- Navigate to **Trend Items**
- Filter by **Canonical Translation** → "Missing en-US translation"
- Shows items needing translation

---

## 📡 API Endpoints

### Basic Endpoints

```bash
# Health check
curl http://localhost:8000/health

# List regions
curl http://localhost:8000/api/v1/regions

# List surfaces
curl http://localhost:8000/api/v1/surfaces?region=us

# Get trends (with canonical English titles)
curl http://localhost:8000/api/v1/trends?region=us&limit=20
```

### Observability Endpoints

From `/tmp/t8` - Self-verifiable monitoring:

```bash
# Per-surface crawl status
curl http://localhost:8000/api/v1/health/crawl

# Translation queue status
curl http://localhost:8000/api/v1/health/translation

# Recent items from specific surface
curl http://localhost:8000/api/v1/surfaces/1/recent?limit=10
```

### API Documentation

Interactive docs available at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## 🧪 Testing Checklist

### Technical Tests

- [x] ✅ Create region via Django Admin
- [x] ✅ Create surface via Django Admin with bucket assignment
- [ ] Run surface worker → CrawlRun created
- [ ] Check CrawlRun metrics (fetched/stored/deduped)
- [ ] Enable DRY_RUN → no TrendItems created
- [ ] Check timeout protection (slow collector)
- [ ] Call /api/v1/health/crawl → see status
- [ ] Call /api/v1/health/translation → see queue
- [ ] Call /api/v1/surfaces/{id}/recent → see items
- [ ] Filter TrendItems by missing canonical
- [ ] Run translation worker → en-US created
- [ ] Call /api/v1/trends → canonical_title present

### Product Tests (from /tmp/t9)

- [ ] Create surfaces in multiple buckets (hot_now, rising, category_tech, etc.)
- [ ] Collect 500 items → verify no bucket > 40% (200 items)
- [ ] Check TrendItems have rank_position when available
- [ ] Check TrendItems have engagement_signals populated
- [ ] Check raw_payload is complete (not truncated)
- [ ] Verify translation doesn't block collection (check timing logs)
- [ ] Verify bucket distribution in CrawlRun logs

---

## 📊 Data Model

### Core Models

1. **Region** - Geographical/cultural regions (US, JP, KR, CN, etc.)
2. **TrendSurface** - Configurable data sources with bucket assignment
3. **TrendItem** - Collected trend items (feed candidates)
4. **TrendItemTranslation** - Async translations
5. **TranslationSettings** - Global translation config (singleton)
6. **CrawlRun** - Execution audit log (observability)

### Bucket System

Every surface belongs to exactly one bucket:

- **hot_now** - Major trending content (capped at 40%)
- **rising** - New gaining traction
- **category_tech** - Technology, gadgets
- **category_sports** - Sports, games
- **category_entertainment** - Movies, TV, music
- **category_finance** - Business, stocks
- **category_gaming** - Video games, esports
- **category_lifestyle** - Health, food, fashion
- **category_science** - Research, discoveries
- **category_politics** - Government, policy
- **region_local** - Local mainstream portals
- **evergreen** - Slower high-quality sources

**Product rule:** No bucket can represent > 40% of collected items per run.

---

## 🏗️ Architecture

```
┌─────────────────────────┐
│   Django Admin (8001)   │  Configuration UI
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   SQLite Database       │  Data storage
└───┬─────────────────┬───┘
    │                 │
    ▼                 ▼
┌──────────────┐  ┌──────────────┐
│Surface Worker│  │Trans. Worker │  Background workers
└──────┬───────┘  └──────┬───────┘
       │                 │
       ▼                 ▼
┌─────────────────────────┐
│   FastAPI Server (8000) │  Read-only APIs
└─────────────────────────┘
```

### Workers

**Surface Worker** (`run_surface_worker.sh`):
- Polls for due surfaces every 60s
- Executes collectors with timeout (60s default)
- Creates CrawlRun audit logs
- Updates surface health fields
- Respects DRY_RUN mode

**Translation Worker** (`run_translation_worker.sh`):
- Creates pending en-US translations for non-English items
- Processes translations using DeepL or OpenAI
- Canonical-first priority (en-US before other locales)
- Never blocks collection

---

## 📖 Documentation

- **[REQUIREMENTS-MASTER.md](docs/REQUIREMENTS-MASTER.md)** - Complete requirements
- **[DESIGN-PRODUCT-PHILOSOPHY.md](docs/DESIGN-PRODUCT-PHILOSOPHY.md)** - Product thinking
- **[DESIGN-OVERVIEW.md](docs/DESIGN-OVERVIEW.md)** - Architecture overview
- **[DESIGN-DATA-MODEL.md](docs/DESIGN-DATA-MODEL.md)** - Database schema
- **[DESIGN-SURFACES.md](docs/DESIGN-SURFACES.md)** - Surface plugin system
- **[DESIGN-WORKERS-API.md](docs/DESIGN-WORKERS-API.md)** - Workers & API
- **[DESIGN-OBSERVABILITY.md](docs/DESIGN-OBSERVABILITY.md)** - Health monitoring
- **[CHANGES-FROM-T9.md](docs/CHANGES-FROM-T9.md)** - Product evolution

See [`docs/INDEX.md`](docs/INDEX.md) for full documentation index.

---

## 🔧 Creating Custom Collectors

See [`docs/DESIGN-SURFACES.md`](docs/DESIGN-SURFACES.md) for detailed guide.

**Quick example:**

```python
# src/crawler_api/surfaces/my_custom_surface.py

async def collect(config: dict, cursor: Optional[str], limit: int):
    """Collect trending items."""
    items = []

    # Your collection logic here
    for i, item_data in enumerate(fetch_items(), start=1):
        items.append({
            "title": item_data.title,
            "url": item_data.url,
            "locale": "en-US",

            # CRITICAL from /tmp/t9:
            "rank_position": i,  # Position in ranking
            "engagement_signals": {
                "upvotes": item_data.upvotes,
                "comments": item_data.comments,
            },
            "raw_payload": item_data.to_dict()  # Full data
        })

    return items, None  # (items, next_cursor)
```

Then add surface in Django Admin:
- **Entrypoint**: `crawler_api.surfaces.my_custom_surface:collect`

---

## 🐛 Troubleshooting

### Workers Not Running

```bash
# Check if workers are running
ps aux | grep worker

# Check logs for errors
tail -f logs/surface_worker.log
tail -f logs/translation_worker.log
```

### No Items Being Collected

1. **Check surface is enabled** (Django Admin → Trend Surfaces → Enabled = ✅)
2. **Check next_run_at** (should be in the past or None)
3. **Check CrawlRuns** (any errors?)
4. **Check DRY_RUN mode** (should be `false` for real collection)

### Translation Not Working

1. **Check API keys** (DEEPL_API_KEY or OPENAI_API_KEY in .env)
2. **Check TranslationSettings** (translation_enabled = True)
3. **Check translation worker is running**
4. **Check TrendItemTranslation** for error_message

### API Not Responding

```bash
# Check if API server is running
curl http://localhost:8000/health

# Check logs
tail -f logs/api.log
```

---

## 📝 License

[Your License Here]

---

## 🙏 Credits

Based on requirements from:
- `/tmp/t3` - Initial architecture
- `/tmp/t4` - Canonical language strategy
- `/tmp/t7` - Verifiability (CrawlRun)
- `/tmp/t8` - Observability (health monitoring)
- `/tmp/t9` - Product philosophy (diversity > volume)

Built with Django, FastAPI, DeepL, and OpenAI.
