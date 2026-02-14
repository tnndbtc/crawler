# Documentation Index

Complete documentation for the Culture-Flexible Trend Crawler system.

---

## 📖 Quick Start

**New to the project?** Start here:

1. **[README.md](./README.md)** - Overview, quick start guide, and FAQ
2. **[DESIGN-OVERVIEW.md](./DESIGN-OVERVIEW.md)** - Core philosophy and architecture

---

## 🏗️ Architecture Design (4 Parts)

Comprehensive design documentation split into focused sections:

### Part 1: [Overview](./DESIGN-OVERVIEW.md)
- Core philosophy: Culture-flexible design
- Architecture diagram
- Technology stack
- Success criteria
- **Size**: 9.0 KB

### Part 2: [Data Model](./DESIGN-DATA-MODEL.md)
- Database schema
- Region, TrendSurface, TrendItem models
- Translation models
- Indexes and performance
- **Size**: 13.3 KB

### Part 3: [Surface Plugin System](./DESIGN-SURFACES.md)
- Collector interface
- Normalized item format
- Example collectors (Reddit, YouTube, Yahoo Japan)
- Testing and best practices
- **Size**: 13.1 KB

### Part 4: [Workers & API](./DESIGN-WORKERS-API.md)
- Surface runner worker
- Translation worker
- FastAPI endpoints
- Deployment guide
- **Size**: 15.7 KB

---

## 📡 API Reference

**[API.md](./API.md)** - Complete API documentation
- All endpoints with examples
- Request/response formats
- Error handling
- Pagination
- SDKs and usage examples
- **Size**: 13.6 KB

---

## 📚 Reading Guide

### For Product Managers
1. [README.md](./README.md) - Understand what the system does
2. [DESIGN-OVERVIEW.md](./DESIGN-OVERVIEW.md) - Learn the core concepts
3. [API.md](./API.md) - See what data is available

### For Backend Engineers
1. [DESIGN-OVERVIEW.md](./DESIGN-OVERVIEW.md) - Architecture overview
2. [DESIGN-DATA-MODEL.md](./DESIGN-DATA-MODEL.md) - Database design
3. [DESIGN-SURFACES.md](./DESIGN-SURFACES.md) - How to add collectors
4. [DESIGN-WORKERS-API.md](./DESIGN-WORKERS-API.md) - Worker implementation

### For Frontend Engineers
1. [README.md](./README.md) - System overview
2. [API.md](./API.md) - API endpoints and examples
3. [DESIGN-DATA-MODEL.md](./DESIGN-DATA-MODEL.md) - Data structure

### For DevOps Engineers
1. [DESIGN-OVERVIEW.md](./DESIGN-OVERVIEW.md) - Component overview
2. [DESIGN-WORKERS-API.md](./DESIGN-WORKERS-API.md) - Deployment section
3. [README.md](./README.md) - Setup instructions

---

## 🗂️ Document Sizes

All documents are kept under 25KB for easy reading:

| Document | Size | Purpose |
|----------|------|---------|
| README.md | 11.9 KB | Quick start guide |
| DESIGN-OVERVIEW.md | 9.0 KB | Architecture overview |
| DESIGN-DATA-MODEL.md | 13.3 KB | Database schema |
| DESIGN-SURFACES.md | 13.1 KB | Plugin system |
| DESIGN-WORKERS-API.md | 15.7 KB | Workers & API |
| API.md | 13.6 KB | API reference |
| **Total** | **76.6 KB** | **6 documents** |

---

## 🔍 Common Tasks

### I want to add a new region
→ Read: [README.md § How to Add a New Region](./README.md#how-to-add-a-new-region)

### I want to implement a new collector
→ Read: [DESIGN-SURFACES.md § Adding a New Collector](./DESIGN-SURFACES.md#adding-a-new-collector)

### I want to understand the data model
→ Read: [DESIGN-DATA-MODEL.md](./DESIGN-DATA-MODEL.md)

### I want to use the API
→ Read: [API.md](./API.md)

### I want to deploy the system
→ Read: [DESIGN-WORKERS-API.md § Deployment](./DESIGN-WORKERS-API.md#deployment)

### I want to understand translations
→ Read: [DESIGN-WORKERS-API.md § Translation Providers](./DESIGN-WORKERS-API.md#translation-providers)

---

## 📝 Document Hierarchy

```
docs/
├── INDEX.md                    ← You are here
├── README.md                   ← Start here (user guide)
│
├── Design Documentation (4 parts):
│   ├── DESIGN-OVERVIEW.md      ← Part 1: Philosophy & architecture
│   ├── DESIGN-DATA-MODEL.md    ← Part 2: Database schema
│   ├── DESIGN-SURFACES.md      ← Part 3: Plugin system
│   └── DESIGN-WORKERS-API.md   ← Part 4: Workers & API
│
└── API.md                      ← API reference
```

---

## ✨ Key Concepts

### Culture-Flexible Design
Different regions have different platforms that matter. The system is designed **region-first**, not platform-first.

### Trend Surfaces
Any source that reveals trending topics:
- **Ranking**: Curated lists (Reddit Hot, YouTube Trending)
- **Sampler**: Algorithmic feed samples
- **Search**: Search trend pages (Google Trends)
- **News**: News portal rankings

### Plugin Architecture
Add new platforms by implementing a simple `collect()` function. No core code changes needed.

### Async Translation
Translation happens asynchronously and never blocks data collection. Supports DeepL and OpenAI.

---

## 🔗 External Links

- **GitHub Repository**: (TODO)
- **API Playground**: (TODO)
- **Issue Tracker**: (TODO)
- **Deployment Docs**: (TODO)

---

**Last Updated**: 2024-01-15
**Documentation Version**: 1.0
