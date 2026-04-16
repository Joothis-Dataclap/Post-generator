# Social Content Engine

AI-powered social media content generation engine with RAG (Retrieval-Augmented Generation).

Upload your knowledge base, let AI generate platform-specific posts for LinkedIn and Twitter/X, review and approve, then publish directly.

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
│  + Image Gen         │──── Gemini 2.0 Flash
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
| `QDRANT_HOST` | No | `localhost` | Qdrant server host |
| `QDRANT_PORT` | No | `6333` | Qdrant server port |
| `QDRANT_COLLECTION` | No | `content_chunks` | Qdrant collection name |
| `EMBEDDING_PROVIDER` | No | `local` | `local` (sentence-transformers) or `openai` |
| `OPENAI_API_KEY` | If using OpenAI embeddings | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | **Yes** | — | Anthropic API key for Claude |
| `CLAUDE_MODEL` | No | `claude-opus-4-6` | Claude model to use |
| `GEMINI_API_KEY` | **Yes** | — | Google Gemini API key for image generation |
| `LINKEDIN_ACCESS_TOKEN` | For publishing | — | LinkedIn OAuth2 access token |
| `LINKEDIN_PERSON_URN` | For publishing | — | LinkedIn person URN (`urn:li:person:ID`) |
| `X_API_KEY` | For publishing | — | Twitter/X API key |
| `X_API_SECRET` | For publishing | — | Twitter/X API secret |
| `X_ACCESS_TOKEN` | For publishing | — | Twitter/X access token |
| `X_ACCESS_TOKEN_SECRET` | For publishing | — | Twitter/X access token secret |
| `X_BEARER_TOKEN` | For publishing | — | Twitter/X bearer token |

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
    "target_audience": "startup founders and CTOs",
    "image_style": "modern tech illustration"
  }'
```

**LinkedIn types:** `single`, `carousel`, `article`
**X types:** `tweet`, `thread`, `carousel`

---

### Module 4 — Review & Approve Drafts

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
      "hashtags": ["updated", "post"],
      "image_description": "new image description"
    }
  }'
```

**Approve and publish:**
```bash
curl -X POST http://localhost:8000/api/v1/drafts/{draft_id}/approve \
  -H "Content-Type: application/json" \
  -d '{
    "publish_linkedin": true,
    "publish_x": true
  }'
```

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
      "hashtags": ["final"],
      "image_description": "cover image"
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
│   │   ├── generation.py            # Claude prompt builder + API call
│   │   ├── image_gen.py             # Gemini 2.0 Flash image generation
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
