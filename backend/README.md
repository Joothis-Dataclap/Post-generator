# Social Content Engine

AI-powered social media content generation engine with RAG (Retrieval-Augmented Generation).

Upload your knowledge base, let AI generate platform-specific posts for LinkedIn and Twitter/X, review and approve, then publish directly.

The current generation flow is text-only. Frontend or editorial tooling can attach media later before publish-time where needed.

## Architecture

```
Knowledge Base (PDF/DOCX/TXT/MD/HTML)
        │
        ▼
┌─────────────────────┐
│  Ingestion Pipeline  │──── chunk + embed ────▶ Qdrant Vector DB
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  Semantic Search     │◀──── top-K retrieval
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  AI Generation       │──── Claude API + RAG context
│  Text-Only Drafts    │──── media added later by frontend/editorial flow
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  Human Review        │──── approve / edit / reject
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  Publisher            │──── LinkedIn API + Twitter/X API
└─────────────────────┘
```

## Setup

### 1. Clone and install

```bash
cd social-content-engine
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. Start Qdrant

```bash
docker compose up -d qdrant
```

### 3. Configure environment

```bash
copy .env.example .env
# Edit .env with your API keys
```

If you are starting from a fresh Directus database, run the bootstrap script once after Directus is up:

```bash
python ../directus/bootstrap_directus.py
```

### 4. Run the app

```bash
python main.py
# or
uvicorn main:app --reload --port 8000
```

API docs at: http://localhost:8000/docs

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./storage/social_engine.db` | Async SQLAlchemy DB URL |
| `DIRECTUS_URL` | No | `http://localhost:8055` | Directus REST API base URL |
| `DIRECTUS_ACCESS_TOKEN` | No | — | Bearer token for Directus API access |
| `DIRECTUS_EMAIL` / `DIRECTUS_PASSWORD` | No | — | Fallback login credentials for schema bootstrap and sync |
| `QDRANT_HOST` | No | `localhost` | Qdrant server host |
| `QDRANT_PORT` | No | `6333` | Qdrant server port |
| `QDRANT_COLLECTION` | No | `content_chunks` | Qdrant collection name |
| `EMBEDDING_PROVIDER` | No | `local` | `local` (sentence-transformers) or `openai` |
| `OPENAI_API_KEY` | If using OpenAI embeddings | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | **Yes** | — | Anthropic API key for Claude |
| `CLAUDE_MODEL` | No | `claude-opus-4-6` | Claude model to use |
| `GEMINI_API_KEY` | No | — | Reserved for optional image-generation workflows |
| `LINKEDIN_ACCESS_TOKEN` | For publishing | — | LinkedIn OAuth2 access token |
| `LINKEDIN_PERSON_URN` | For publishing | — | LinkedIn person URN (`urn:li:person:ID`) |
| `X_API_KEY` | For publishing | — | Twitter/X API key |
| `X_API_SECRET` | For publishing | — | Twitter/X API secret |
| `X_ACCESS_TOKEN` | For publishing | — | Twitter/X access token |
| `X_ACCESS_TOKEN_SECRET` | For publishing | — | Twitter/X access token secret |
| `X_BEARER_TOKEN` | For publishing | — | Twitter/X bearer token |
| `POSTIZ_API_URL` | No | `https://api.postiz.com/public/v1` | Postiz public API base URL |
| `POSTIZ_API_KEY` | For scheduling | — | Postiz public API key |
| `POSTIZ_LINKEDIN_INTEGRATION_ID` | For scheduling | — | Postiz LinkedIn integration id |
| `POSTIZ_X_INTEGRATION_ID` | For scheduling | — | Postiz X integration id |
| `POSTIZ_DEFAULT_DELAY_MINUTES` | No | `60` | Default delay when no scheduled time is supplied |

---

## API Endpoints & curl Examples

### Health Check

```bash
curl http://localhost:8000/health
```

---

### Module 1 — Ingest Sources

**Upload a file:**
```bash
curl -X POST http://localhost:8000/api/v1/sources \
  -F "title=My Blog Post" \
  -F "source_type=blog" \
  -F "category=marketing" \
  -F "file=@./my-article.pdf"
```

**Ingest text directly:**
```bash
curl -X POST http://localhost:8000/api/v1/sources \
  -F "title=Product Launch Notes" \
  -F "source_type=product" \
  -F "category=launches" \
  -F "text_content=Our new product XYZ revolutionizes the way teams collaborate..."
```

---

### Module 2 — Browse & Search

**List all sources:**
```bash
curl http://localhost:8000/api/v1/sources
```

**Get source with chunks:**
```bash
curl http://localhost:8000/api/v1/sources/{source_id}
```

**Semantic search:**
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "product collaboration features",
    "top_k": 5,
    "category_filter": "launches"
  }'
```

---

### Module 3 — Generate Posts

```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "YOUR_SOURCE_UUID",
    "query_context": "Focus on the collaboration features",
    "linkedin_type": "single",
    "x_type": "thread",
    "brand_voice": "professional yet approachable",
    "target_audience": "startup founders and CTOs"
  }'
```

**LinkedIn types:** `single`, `carousel`, `article`
**X types:** `tweet`, `thread`, `carousel`

Both generated payloads are persisted as text-only drafts in the database.

**Generate from an approved idea with the same type selectors:**
```bash
curl -X POST http://localhost:8000/api/v1/content/generate \
  -H "Content-Type: application/json" \
  -d '{
    "idea": {
      "id": "idea-001",
      "title": "Why data engineering matters for AI agents",
      "angle": "how-to",
      "core_hook": "AI agents fail when the data foundation is weak",
      "knowledge_source": "Internal architecture notes",
      "trend_source": "Growing enterprise agent adoption",
      "target_audience": "CTOs and data platform leads",
      "engagement_potential": "High",
      "engagement_reasoning": "Clear tie between AI delivery and data-platform maturity",
      "suggested_formats": ["linkedin-carousel", "x-thread"]
    },
    "query": "data engineering for AI agents",
    "source_id": "YOUR_SOURCE_UUID",
    "linkedin_type": "carousel",
    "x_type": "thread",
    "brand_voice": "professional yet approachable",
    "target_audience": "CTOs and data platform leads"
  }'
```

---

### Module 4 — Review & Schedule Drafts

**List drafts (filter by status):**
```bash
curl "http://localhost:8000/api/v1/drafts?status=pending"
```

**Get a draft:**
```bash
curl http://localhost:8000/api/v1/drafts/{draft_id}
```

**Edit a draft:**
```bash
curl -X PUT http://localhost:8000/api/v1/drafts/{draft_id} \
  -H "Content-Type: application/json" \
  -d '{
    "linkedin_content": {
      "hook": "Updated hook line",
      "body": "Updated body text...",
      "hashtags": ["updated", "post"]
    }
  }'
```

**Approve and schedule via Postiz:**
```bash
curl -X POST http://localhost:8000/api/v1/drafts/{draft_id}/approve \
  -H "Content-Type: application/json" \
  -d '{
    "publish_linkedin": true,
    "publish_x": true,
    "scheduled_at": "2026-04-16T16:00:00Z"
  }'
```

If Postiz is not configured yet, the endpoint keeps the legacy direct-publish fallback so you can still test the workflow locally.

Carousel drafts remain text-only in this version. LinkedIn and X carousel publishing stays blocked until slide media is attached through the later frontend/editorial handoff.

**Approve with content override:**
```bash
curl -X POST http://localhost:8000/api/v1/drafts/{draft_id}/approve \
  -H "Content-Type: application/json" \
  -d '{
    "publish_linkedin": true,
    "publish_x": false,
    "linkedin_content_override": {
      "hook": "Final hook",
      "body": "Final edited body...",
      "hashtags": ["final"]
    }
  }'
```

**Reject a draft:**
```bash
curl -X POST http://localhost:8000/api/v1/drafts/{draft_id}/reject \
  -H "Content-Type: application/json" \
  -d '{"reason": "Tone does not match brand guidelines"}'
```

---

## Project Structure

```
social-content-engine/
├── main.py                          # FastAPI app entry point
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment template
├── docker-compose.yml               # Qdrant + app services
├── Dockerfile                       # Container build
├── app/
│   ├── core/
│   │   ├── config.py                # Settings via pydantic-settings
│   │   ├── database.py              # SQLAlchemy async engine + session
│   │   └── dependencies.py          # FastAPI dependency injection
│   ├── models/
│   │   ├── source.py                # Source ORM model
│   │   └── draft.py                 # Draft ORM model
│   ├── schemas/
│   │   ├── source.py                # Source request/response schemas
│   │   ├── draft.py                 # Draft schemas
│   │   └── generation.py            # Generate request/response + content models
│   ├── services/
│   │   ├── ingestion.py             # RAG: chunking + embedding + Qdrant upsert
│   │   ├── retrieval.py             # Semantic search wrapper
│   │   ├── generation.py            # Shared text-only generation pipeline
│   │   ├── image_gen.py             # Optional image workflow helpers
│   │   ├── publisher_linkedin.py    # LinkedIn OAuth2 publishing
│   │   └── publisher_x.py          # Twitter/X OAuth publishing
│   └── api/
│       ├── router.py                # Central API router
│       ├── sources.py               # /sources endpoints
│       ├── search.py                # /search endpoint
│       ├── generate.py              # /generate endpoint
│       └── drafts.py                # /drafts endpoints
└── storage/
    └── images/                      # Generated images directory
```

## Docker

Run everything with Docker Compose:

```bash
docker compose up --build
```

This starts:
- **Qdrant** on port 6333
- **App** on port 8000

## License

MIT
