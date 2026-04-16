# Content Generation API — Frontend Integration Reference

Base URL: `http://localhost:8000/api/v1`

---

## Workflow Overview

```
[1] Ingest sources  →  [2] Generate ideas by industry  →  [3] Select idea → Generate content  →  [4] Review draft  →  [5] Approve & publish
POST /sources           POST /ideas/generate              POST /content/generate                 GET /drafts/{id}        POST /drafts/{id}/approve
                        (industry → PDF chunks                                                   
                         + deep research → 5 ideas                                               
                         → stored in DB)                                                         
```

### Data Storage at Each Step

| Step | What is stored | DB Table | How to retrieve |
|------|---------------|----------|-----------------|
| 1. Ingest source | Source metadata + raw text + vector chunks | `sources` + Qdrant | `GET /sources`, `GET /sources/{id}` |
| 2. Generate ideas | Industry, PDF chunks used, research JSON, LLM prompt/response, 5 ideas | `idea_bundles` | `GET /ideas`, `GET /ideas/{bundle_id}` |
| 3. Generate content | Draft with linkedin_content, x_content, linked to idea_bundle_id | `drafts` | `GET /drafts`, `GET /drafts/{id}` |
| 4. Approve/Publish | Status, scheduled_at, postiz_targets, platform post IDs | `drafts` (updated) | `GET /drafts?status=published` |

---

## 1. Sources

### `POST /sources` — Ingest a source document

Upload a file **or** submit raw text. Creates a source and embeds it into the vector store.

**Request** — `multipart/form-data`

| Field          | Type   | Required | Description                                  |
|----------------|--------|----------|----------------------------------------------|
| `title`        | string | ✅       | Human-readable title                         |
| `source_type`  | string | —        | `"article"` (default), `"blog"`, `"report"` |
| `category`     | string | —        | `"general"` (default)                        |
| `text_content` | string | either   | Raw text content                             |
| `file`         | File   | either   | PDF, DOCX, TXT, MD, HTML                     |

**Response** `201`

```json
{
  "id": "uuid",
  "title": "Why Embedded Finance Is Eating Traditional Banking",
  "source_type": "article",
  "category": "fintech",
  "filename": null,
  "chunk_count": 12,
  "created_at": "2026-04-16T10:00:00Z"
}
```

---

### `GET /sources` — List all sources

**Response** `200` — array of `SourceResponse`

---

### `GET /sources/{source_id}` — Get source with chunks

**Response** `200` — adds `raw_text` and `chunks[]` to the base response.

---

### `DELETE /sources/{source_id}` — Delete a source

**Response** `204 No Content`

---

## 2. Search

### `POST /search` — Semantic search over knowledge base

**Request**

```json
{
  "query": "embedded finance APIs",
  "top_k": 5,
  "category_filter": "fintech"
}
```

**Response** `200`

```json
{
  "query": "embedded finance APIs",
  "results": [
    {
      "chunk_id": "uuid",
      "source_id": "abc-123",
      "source_title": "Embedded Finance Report 2025",
      "text": "Banking-as-a-Service APIs let any company embed...",
      "score": 0.921,
      "metadata": { "word_count": 87 }
    }
  ]
}
```

---

## 3. Idea Generation (Step 1) — Industry-Driven

### `POST /ideas/generate` — Generate 5 content ideas for an industry

The user provides an **industry** (and optionally a **service_description**) — the backend automatically:
1. Searches PDF chunks in the knowledge base relevant to that industry
2. Runs **multi-angle deep research** via Parallel Search API:
   - ★ **Technology Updates** (1–2 sources)
   - ★★★ **Research & Benchmarks** (3+ sources — papers, datasets, benchmarks)
   - ★★ **Real-World Deployments** (2 sources — case studies, pilots)
   - ★★★ **Challenges & Gaps** (3+ sources — limitations, failure modes, unmet needs)
3. Feeds all raw search results into the LLM to produce a **structured intelligence report**
4. Combines PDF chunks + intelligence report and generates 5 high-impact content ideas
5. Stores everything (chunks, intelligence report, LLM prompt/response, ideas) in the `idea_bundles` table

**Request**

```json
{
  "industry": "NLP Data Annotation",
  "service_description": "AI training data annotation services for NLP, including named entity recognition, sentiment analysis, and text classification labelling",
  "top_k": 8,
  "category_filter": "nlp"
}
```

| Field                 | Type   | Required | Description                                |
|-----------------------|--------|----------|--------------------------------------------|
| `industry`            | string | ✅       | Industry/vertical — also used as the `SERVICE_LABEL` for research |
| `service_description` | string | —        | Detailed description of the service domain (improves research quality) |
| `top_k`               | int    | —        | Number of KB chunks to retrieve (default 8) |
| `category_filter`     | string | —        | Optional category filter for chunks          |

**Response** `200`

```json
{
  "bundle_id": "uuid-of-stored-bundle",
  "industry": "NLP Data Annotation",
  "generated_at": "2026-04-16T10:00:00Z",
  "context_summary": "Knowledge base contains sources on NER, text classification, and annotation tooling.",
  "ideas": [
    {
      "id": "idea_1",
      "title": "Why LLM-Assisted Annotation Still Needs Human-in-the-Loop",
      "angle": "myth-busting",
      "core_hook": "GPT-4o can label 10x faster, but error cascading in edge cases means you still can't ship without human QA.",
      "knowledge_source": "NER Benchmark Report 2026",
      "trend_source": "Annotation quality gaps in LLM era",
      "target_audience": "ML engineers, data ops leads, annotation team managers",
      "engagement_potential": "High",
      "engagement_reasoning": "Challenges the 'just use LLMs' narrative with hard data",
      "suggested_formats": ["linkedin_post", "x_thread"],
      "research_data": { "...structured intelligence report..." }
    }
  ],
  "research_sources": [
    {"index": "R1", "title": "...", "url": "...", "published_date": "...", "publisher": "...", "summary": "...", "key_fact": "..."}
  ],
  "research_insights": "3–4 sentence intelligence summary from the domain research"
}
```

The `research_data` attached to each idea contains the **full structured intelligence report** with:
- `intelligence_summary` — top-level synthesis
- `top_research_finding` — single most compelling paper result
- `top_open_problem` — single most urgent gap
- `angles.technology_updates` — latest tech developments
- `angles.research_and_benchmarks` — papers, datasets, benchmarks (3+ sources)
- `angles.real_world_deployments` — production case studies (2 sources)
- `angles.challenges_and_gaps` — limitations, gaps, gap clusters (3+ sources)
- `content_opportunities[]` — suggested post topics with source references

> **`bundle_id`** is the key — pass it to `/content/generate` and use it to retrieve ideas later.

---

### `GET /ideas` — List all stored idea bundles

**Query params:** `?industry=fintech`

**Response** `200` — array of `IdeaBundleResponse`

```json
[
  {
    "id": "bundle-uuid",
    "industry": "NLP Data Annotation",
    "context_summary": "Knowledge base contains sources on NER...",
    "ideas": [...],
    "research_data": { "...full intelligence report..." },
    "research_sources": [...],
    "research_insights": "...",
    "idea_count": 5,
    "status": "generated",
    "created_at": "2026-04-16T10:00:00Z"
  }
]
```

---

### `GET /ideas/{bundle_id}` — Get a single idea bundle with all data

Returns the full bundle including all 5 ideas, research data, and chunks used.

---

### `GET /ideas/{bundle_id}/{idea_id}` — Get a single idea

Returns one specific `ContentIdea` from a bundle.

**Response** `200`

```json
{
  "id": "idea_1",
  "title": "Why Embedded Finance Is Eating Traditional Banking Products",
  "angle": "data-story",
  "core_hook": "...",
  "knowledge_source": "...",
  "trend_source": "...",
  "target_audience": "...",
  "engagement_potential": "High",
  "engagement_reasoning": "...",
  "suggested_formats": ["linkedin:article", "x:thread"],
  "research_data": {...}
}
```

---

## 4. Content Generation (Step 2) — From Selected Idea

### `POST /content/generate` — Generate posts from a selected idea

User picks an idea from Step 1. The backend:
1. Loads the stored idea + research context from the `idea_bundles` table
2. Retrieves supporting PDF chunks
3. Generates LinkedIn + X content via LLM
4. Saves draft in `drafts` table linked to the idea bundle

**Request**

```json
{
  "bundle_id": "uuid-from-step-1",
  "idea_id": "idea_1",
  "source_id": null,
  "linkedin_type": "single",
  "x_type": "thread",
  "brand_voice": "authoritative but direct",
  "target_audience": "fintech founders and CTOs",
  "top_k": 8
}
```

| Field            | Type   | Required | Description                                  |
|------------------|--------|----------|----------------------------------------------|
| `bundle_id`      | string | ✅       | IdeaBundle ID from `/ideas/generate`         |
| `idea_id`        | string | ✅       | Idea ID within the bundle (e.g. `"idea_1"`)  |
| `source_id`      | string | —        | Restrict chunks to one source                |
| `linkedin_type`  | string | —        | `"single"` \| `"carousel"` \| `"article"` (default: `"single"`) |
| `x_type`         | string | —        | `"tweet"` \| `"thread"` \| `"carousel"` (default: `"thread"`) |
| `brand_voice`    | string | —        | Tone instruction                             |
| `target_audience`| string | —        | Who to write for                             |
| `top_k`          | int    | —        | Chunks to retrieve (default 8)               |

**Response** `200`

```json
{
  "bundle_id": "uuid-from-step-1",
  "idea_id": "idea_1",
  "idea_title": "Why Embedded Finance Is Eating Traditional Banking Products",
  "linkedin_type": "single",
  "x_type": "thread",
  "linkedin_content": {
    "hook": "Banks built moats over 150 years. Embedded finance erased them in 18 months.",
    "body": "The branch network, the regulated balance sheet...",
    "hashtags": ["EmbeddedFinance", "BaaS", "OpenBanking"],
    "image_description": ""
  },
  "x_content": {
    "hook_tweet": "Banks spent 150 years building distribution moats...",
    "tweets": ["tweet 2", "tweet 3", "tweet 4", "tweet 5", "tweet 6"],
    "cta_tweet": "Building in this space? Reply — I read every response.",
    "hashtags": ["EmbeddedFinance", "Fintech"]
  },
  "content_notes": "Provocative data-story angle to drive debate.",
  "draft_id": "draft-uuid",
  "cover_image_url": null
}
```

---

## 5. Direct Generation (Source-first, skip ideas)

### `POST /generate` — Generate posts directly from a source

**Request**

```json
{
  "source_id": "abc-123",
  "query_context": "focus on cost savings angle",
  "linkedin_type": "single",
  "x_type": "tweet",
  "brand_voice": "professional yet approachable",
  "target_audience": "CTOs and engineering leaders"
}
```

**Response** — same shape as `GenerateResponse` (draft_id, source_id, linkedin_content, x_content)

---

## 6. Drafts

### `GET /drafts` — List all drafts

**Query params:** `?status=pending` | `approved` | `scheduled` | `published` | `rejected`

**Response** `200` — array of `DraftResponse`

```json
[
  {
    "id": "draft-uuid",
    "source_id": "abc-123",
    "idea_bundle_id": "bundle-uuid",
    "idea_id": "idea_1",
    "linkedin_type": "single",
    "x_type": "thread",
    "linkedin_content": { "hook": "...", "body": "...", "hashtags": [...] },
    "x_content": { "hook_tweet": "...", "tweets": [...], "cta_tweet": "...", "hashtags": [...] },
    "cover_image_path": null,
    "status": "pending",
    "reject_reason": null,
    "created_at": "2026-04-16T10:00:00Z",
    "scheduled_at": null,
    "published_at": null,
    "linkedin_post_id": null,
    "x_post_id": null,
    "postiz_targets": []
  }
]
```

**Draft status lifecycle:**  
`pending` → `approved` → `scheduled` (if Postiz) → `published`  
`pending` / `approved` → `rejected`

---

### `GET /drafts/{draft_id}` — Get single draft

Same shape as above.

---

### `PUT /drafts/{draft_id}` — Edit a draft

Cannot edit published drafts.

**Request**

```json
{
  "linkedin_content": { "hook": "Updated hook", "body": "...", "hashtags": [] },
  "x_content": null,
  "status": "pending"
}
```

---

### `POST /drafts/{draft_id}/approve` — Approve and publish

**Request**

```json
{
  "publish_linkedin": true,
  "publish_x": true,
  "scheduled_at": "2026-04-17T09:00:00Z",
  "linkedin_content_override": null,
  "x_content_override": null
}
```

- Postiz configured → `"scheduled"` | No Postiz → direct publish → `"published"`
- Carousel types blocked until media handoff implemented

---

### `POST /drafts/{draft_id}/reject` — Reject a draft

**Request**

```json
{
  "reason": "Hook is too weak — needs a stronger opening stat"
}
```

---

## 7. Webhooks

### `POST /webhooks/postiz` — Postiz state callbacks

**Header:** `x-postiz-secret: <secret>`

---

## Post Type Reference

| Platform  | Type       | Key fields in content object                                                                     |
|-----------|------------|---------------------------------------------------------------------------------------------------|
| LinkedIn  | `single`   | `hook` (≤80 chars), `body`, `hashtags[]`                                                         |
| LinkedIn  | `carousel` | `intro_caption`, `slides[5]` (`headline`, `body`), `hashtags[]`                                  |
| LinkedIn  | `article`  | `title`, `subtitle`, `body` (600–800 words), `hashtags[]`                                        |
| X         | `tweet`    | `text` (≤260 chars), `hashtags[]`                                                                 |
| X         | `thread`   | `hook_tweet`, `tweets[5]` (strings), `cta_tweet`, `hashtags[]`                                   |
| X         | `carousel` | `caption` (≤240 chars), `slides[4]` (`headline`)                                                 |

---

## Frontend Integration Flow (Step by Step)

### Step 1: User enters an industry
```javascript
// Frontend sends just the industry
const ideasResponse = await api.post('/ideas/generate', {
  industry: 'fintech'
});

// Save the bundle_id and show ideas to user
const bundleId = ideasResponse.data.bundle_id;
const ideas = ideasResponse.data.ideas;  // 5 ideas to choose from
```

### Step 2: User selects an idea and picks post types
```javascript
const contentResponse = await api.post('/content/generate', {
  bundle_id: bundleId,
  idea_id: 'idea_1',           // user selected this idea
  linkedin_type: 'single',     // user picks the type
  x_type: 'thread',            // user picks the type
  brand_voice: 'authoritative',
  target_audience: 'fintech CTOs'
});

// Draft is auto-saved — show content for review
const draftId = contentResponse.data.draft_id;
const linkedinContent = contentResponse.data.linkedin_content;
const xContent = contentResponse.data.x_content;
```

### Step 3: User reviews and approves
```javascript
// Edit if needed
await api.put(`/drafts/${draftId}`, {
  linkedin_content: { ...editedLinkedInContent }
});

// Approve and publish
await api.post(`/drafts/${draftId}/approve`, {
  publish_linkedin: true,
  publish_x: true,
  scheduled_at: '2026-04-17T09:00:00Z'
});
```

### Retrieve past data anytime
```javascript
// List all idea bundles
const bundles = await api.get('/ideas');

// List bundles for a specific industry
const fintechBundles = await api.get('/ideas?industry=fintech');

// Get a specific bundle with all ideas + research
const bundle = await api.get(`/ideas/${bundleId}`);

// Get a specific idea
const idea = await api.get(`/ideas/${bundleId}/idea_1`);

// List all drafts
const drafts = await api.get('/drafts');

// Get drafts by status
const pending = await api.get('/drafts?status=pending');
```

---

## Database Tables

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `sources` | id, title, source_type, category, raw_text, chunk_count | Ingested PDFs/docs |
| `idea_bundles` | id, industry, retrieved_chunks, research_data, research_sources, llm_prompt, llm_raw_response, ideas, context_summary | Stored idea generations with full audit trail |
| `drafts` | id, source_id, idea_bundle_id, idea_id, linkedin_type, x_type, linkedin_content, x_content, status | Generated content drafts linked to ideas |

---

## Environment Variables

```env
GENERATION_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
PARALLEL_API_KEY=...             # For deep research (optional)
QDRANT_URL=http://localhost:6333
POSTIZ_API_KEY=...               # For scheduling (optional)
```
