"""
main.py — FastAPI application

Endpoints:
  GET  /health          → uptime + Pinecone connectivity check
  POST /chat            → main RAG chat endpoint
  POST /ingest          → trigger re-ingestion (protected by X-Ingest-Secret header)
  GET  /widget.js       → serve the embeddable chat widget
"""

import os
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

from backend import rag  # noqa: E402 — import after env loaded

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Amit Digital Twin", version="1.0.0")

# CORS — set ALLOWED_ORIGIN env var to your WordPress domain in production
# e.g. "https://random-walk.blog"  (leave unset or "*" to allow all)
_allowed_origin = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Rate limiter (in-memory, per IP) ──────────────────────────────────────────
RATE_LIMIT_REQUESTS = 10   # max requests per window
RATE_LIMIT_WINDOW   = 60   # seconds

_rate_buckets: dict[str, deque] = defaultdict(deque)

def _check_rate_limit(ip: str) -> None:
    """Raise 429 if IP has exceeded RATE_LIMIT_REQUESTS in the last window."""
    now = time.time()
    bucket = _rate_buckets[ip]
    # Drop timestamps outside the window
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please wait a moment before sending another message.",
        )
    bucket.append(now)


# ── Request / Response models ─────────────────────────────────────────────────
class Message(BaseModel):
    role: str   # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=600)
    history: list[Message] = []


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = []


class IngestRequest(BaseModel):
    source: str = "all"   # "all" | "book" | "linkedin" | "blog"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Uptime + Pinecone connectivity check."""
    try:
        index = rag._get_index()
        stats = index.describe_index_stats()
        total_vectors = stats.total_vector_count
        pinecone_status = "connected"
    except Exception as e:
        pinecone_status = f"error: {e}"
        total_vectors = None

    return {
        "status": "ok",
        "pinecone": pinecone_status,
        "total_vectors": total_vectors,
        "version": "1.0.0",
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    """Main RAG chat endpoint."""
    _check_rate_limit(request.client.host)

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # Convert Pydantic models to plain dicts for rag.chat()
    history = [{"role": m.role, "content": m.content} for m in req.history]

    result = rag.chat(message=req.message, history=history)
    return ChatResponse(reply=result["reply"], sources=result["sources"])


@app.post("/ingest")
def ingest(req: IngestRequest, request: Request):
    """
    Trigger re-ingestion. Protected by X-Ingest-Secret header.
    Runs the appropriate ingestion script as a subprocess.
    """
    secret = request.headers.get("X-Ingest-Secret", "")
    if secret != os.environ.get("INGEST_SECRET", ""):
        raise HTTPException(status_code=401, detail="Unauthorized.")

    source_map = {
        "all": "ingestion/run_all.py",
        "linkedin": "ingestion/ingest_linkedin.py",
        "book": "ingestion/ingest_book.py",
        "blog": "ingestion/ingest_blog.py",
    }

    script = source_map.get(req.source)
    if not script:
        raise HTTPException(status_code=400, detail=f"Unknown source: {req.source}")

    try:
        result = subprocess.run(
            ["python", script],
            capture_output=True, text=True, timeout=600
        )
        return {
            "status": "ok",
            "source": req.source,
            "stdout": result.stdout[-2000:],   # last 2K chars
            "stderr": result.stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Ingestion timed out.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/widget.js")
def serve_widget():
    """Serve the embeddable chat widget JS file."""
    widget_path = Path(__file__).parent.parent / "widget" / "widget.js"
    if not widget_path.exists():
        raise HTTPException(status_code=404, detail="Widget not built yet.")
    return FileResponse(widget_path, media_type="application/javascript")
