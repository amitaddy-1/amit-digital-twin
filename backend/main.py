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
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

from backend import rag  # noqa: E402 — import after env loaded

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Amit Digital Twin", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to your WordPress domain after testing
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────
class Message(BaseModel):
    role: str   # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
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
def chat(req: ChatRequest):
    """Main RAG chat endpoint."""
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
