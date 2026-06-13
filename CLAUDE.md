# CLAUDE.md — Digital Twin "Amit" Project Guide

> Read this at the start of every Claude Code session. It is the single source of truth for what this project is, how it works, and what to build next.

---

## What This Project Is

A RAG-powered chatbot ("Amit") that lives on a WordPress website. It answers questions grounded in exactly three sources:
1. Amit's LinkedIn profile PDF (`data/LinkedIn.pdf`)
2. Amit's book *Nonlinear* (`data/Nonlinear.pdf`)
3. Amit's blog at https://random-walk.blog

It speaks in Amit's voice: warm, data-driven, analogy-rich, conversational. It politely declines anything outside these three sources.

---

## Stack

| Layer | Tech |
|---|---|
| LLM + Embeddings | OpenAI GPT-4o + text-embedding-3-small |
| Vector DB | Pinecone (index: `amit-twin`, namespaces: `book`, `linkedin`, `blog`) |
| Backend API | FastAPI, deployed on Render |
| Frontend widget | Vanilla JS chat bubble, embedded via `<script>` tag in WordPress |
| Session memory | Client-side (widget holds last 6 turns, sends with each request) |

---

## Project Structure

```
amit-digital-twin/
├── backend/
│   ├── main.py          # FastAPI app — routes, CORS, startup
│   ├── rag.py           # Core RAG: embed query → Pinecone → GPT-4o
│   ├── prompts.py       # System prompt (Amit's personality)
│   └── requirements.txt
├── ingestion/
│   ├── ingest_linkedin.py   # Ingest LinkedIn.pdf (3 section chunks)
│   ├── ingest_book.py       # Ingest Nonlinear.pdf (semantic paragraph chunks)
│   ├── ingest_blog.py       # Crawl + ingest random-walk.blog
│   └── run_all.py           # Run all three ingestors in sequence
├── widget/
│   ├── widget.js        # Embeddable chat bubble (served by FastAPI)
│   └── widget.css       # Widget styles (inlined into widget.js)
├── data/
│   ├── LinkedIn.pdf     # Source PDF (do not modify)
│   └── Nonlinear.pdf    # Source PDF (do not modify)
├── .env                 # Real secrets (gitignored)
├── .env.example         # Template for secrets
├── render.yaml          # Render deployment config
└── CLAUDE.md            # This file
```

---

## Environment Variables

```
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=...
PINECONE_INDEX=amit-twin
PINECONE_REGION=us-east-1
INGEST_SECRET=...          # Used to protect the /ingest endpoint
```

---

## Chunking Strategy (DO NOT change without re-reading ARCHITECTURE.md)

### Book (Nonlinear.pdf)
- Semantic paragraph grouping: ~350 words / ~450 tokens per chunk
- Overlap: 1 full paragraph (~120 words)
- Special: named frameworks (DeSIRe, E³) → single chunk, tagged `type: framework`
- Chapter-opening quotes → micro-chunk, tagged `type: quote`
- Namespace: `book`

### LinkedIn (LinkedIn.pdf)
- Exactly 3 chunks: summary, experience, education/certs
- Namespace: `linkedin`

### Blog (random-walk.blog)
- 1 chunk per post (≤800 words); split at paragraph if longer
- Each chunk tagged with post title + URL
- Namespace: `blog`

---

## Metadata Schema (Pinecone vectors)

```json
{
  "source": "book | linkedin | blog",
  "chapter": "Ch5: Delink",
  "title": "Post or section title",
  "url": "https://...",
  "type": "prose | quote | framework | profile | experience | education",
  "chunk_index": 3,
  "text": "The actual chunk text (stored for retrieval)"
}
```

---

## RAG Pipeline (per /chat request)

1. Embed user message → `text-embedding-3-small`
2. Query Pinecone: `top_k=5` per namespace (15 total), filter by score ≥ 0.50
3. Re-rank: keep top 6 by score across all namespaces
4. If best score < 0.50 → return polite fallback (do not call GPT-4o)
5. Build prompt: system + context chunks + conversation history (last 6 turns) + user message
6. Call GPT-4o → return reply + source list

---

## API Contract

### POST /chat
```json
// Request
{
  "message": "What is the DeSIRe framework?",
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}

// Response
{
  "reply": "The DeSIRe framework stands for...",
  "sources": ["book: Ch5 Delink", "book: Ch6 Simplify"]
}
```

### GET /health
```json
{ "status": "ok", "pinecone": "connected", "version": "1.0.0" }
```

### POST /ingest  (header: X-Ingest-Secret: <INGEST_SECRET>)
```json
{ "source": "blog | book | linkedin | all" }
```

---

## Running Locally

```bash
cd amit-digital-twin
pip install -r backend/requirements.txt
cp .env.example .env   # fill in your keys
uvicorn backend.main:app --reload --port 8000
```

Run ingestion (one-time setup):
```bash
python ingestion/run_all.py
```

---

## Deploy to Render

1. Push to GitHub
2. Create new Web Service on Render → connect repo
3. Build command: `pip install -r backend/requirements.txt`
4. Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
5. Add env vars from `.env` in Render dashboard
6. Auto-deploy is on by default

---

## Loop Progress

| Loop | Status | Goal |
|---|---|---|
| 1 | ✅ Done | Scaffold + Pinecone connection + /health |
| 2 | ⬜ Next | `ingest_linkedin.py` — ingest LinkedIn PDF |
| 3 | ⬜ | `ingest_book.py` — ingest Nonlinear book |
| 4 | ⬜ | `ingest_blog.py` — crawl + ingest blog |
| 5 | ⬜ | `rag.py` — full query pipeline |
| 6 | ⬜ | Wire `/chat` endpoint, test end-to-end |
| 7 | ⬜ | `widget.js` — chat bubble UI |
| 8 | ⬜ | Deploy to Render, embed in WordPress |
| 9 | ⬜ | Tune system prompt |
| 10 | ⬜ | Polish: source attribution, rate limiting |

---

## Key Rules for Claude Code

1. **Never use LangChain** — write the RAG pipeline directly with the OpenAI and Pinecone SDKs. Fewer dependencies, easier to debug.
2. **Never hallucinate outside the 3 sources** — the fallback threshold is in `rag.py`, do not remove it.
3. **Chunk text is stored in Pinecone metadata** (`text` field) — we do not need a separate document store.
4. **Widget is vanilla JS only** — no React, no bundler. It must work as a single `<script>` include.
5. **All secrets via environment variables** — never hardcode keys.
