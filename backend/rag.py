"""
rag.py — Core RAG pipeline

Flow:
  1. Embed user query (text-embedding-3-small)
  2. Query Pinecone across all 3 namespaces (book, linkedin, blog)
  3. Re-rank by score, keep top 6
  4. If best score < threshold → return polite fallback
  5. Build prompt → call GPT-4o → return reply + sources
"""

import os
from openai import OpenAI
from pinecone import Pinecone
from backend.prompts import SYSTEM_PROMPT

# ── Constants ────────────────────────────────────────────────────────────────
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o"
NAMESPACES = ["book", "linkedin", "blog"]
TOP_K_PER_NS = 5          # fetch 5 from each namespace
FINAL_TOP_K = 6           # keep top 6 after re-ranking
SCORE_THRESHOLD = 0.35    # below this → fallback (no GPT-4o call)
MAX_HISTORY_TURNS = 6     # last N conversation turns sent to GPT-4o

FALLBACK_REPLY = (
    "That's a bit outside what I've written about — my world is technology, "
    "nonlinearity, marketing research, AI, and career growth. "
    "Happy to explore any of those with you!"
)


# ── Clients (lazy-initialised) ───────────────────────────────────────────────
_openai_client: OpenAI | None = None
_pinecone_index = None


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


def _get_index():
    global _pinecone_index
    if _pinecone_index is None:
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        _pinecone_index = pc.Index(os.environ["PINECONE_INDEX"])
    return _pinecone_index


# ── Core functions ────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """Return embedding vector for a text string."""
    response = _get_openai().embeddings.create(
        model=EMBED_MODEL,
        input=text.replace("\n", " "),
    )
    return response.data[0].embedding


def retrieve(query: str) -> list[dict]:
    """
    Query all 3 Pinecone namespaces, merge results, re-rank by score.
    Returns list of dicts: {text, source, title, score, ...}
    """
    query_vector = embed(query)
    index = _get_index()

    all_matches = []
    for ns in NAMESPACES:
        result = index.query(
            vector=query_vector,
            top_k=TOP_K_PER_NS,
            namespace=ns,
            include_metadata=True,
        )
        for match in result.matches:
            all_matches.append({
                "score": match.score,
                "text": match.metadata.get("text", ""),
                "source": match.metadata.get("source", ns),
                "title": match.metadata.get("title", ""),
                "chapter": match.metadata.get("chapter", ""),
                "url": match.metadata.get("url", ""),
                "type": match.metadata.get("type", "prose"),
            })

    # Re-rank by score descending, keep top FINAL_TOP_K
    all_matches.sort(key=lambda x: x["score"], reverse=True)
    return all_matches[:FINAL_TOP_K]


def build_context_block(chunks: list[dict]) -> str:
    """Format retrieved chunks into a readable context block for the prompt."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        label = _chunk_label(chunk)
        parts.append(f"[{i}] {label}\n{chunk['text'].strip()}")
    return "\n\n".join(parts)


def _chunk_label(chunk: dict) -> str:
    src = chunk["source"]
    if src == "book":
        return f"From book 'Nonlinear' — {chunk.get('chapter', '')}".strip(" —")
    elif src == "linkedin":
        return f"From LinkedIn profile — {chunk.get('title', '')}"
    elif src == "blog":
        title = chunk.get("title", "")
        url = chunk.get("url", "")
        return f"From blog post: '{title}' ({url})" if url else f"From blog: '{title}'"
    return f"From {src}"


def source_labels(chunks: list[dict]) -> list[str]:
    """Return human-readable source labels for the response."""
    seen = set()
    labels = []
    for chunk in chunks:
        label = _chunk_label(chunk)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


# ── Main entry point ──────────────────────────────────────────────────────────

def chat(message: str, history: list[dict]) -> dict:
    """
    Full RAG pipeline. Returns {"reply": str, "sources": list[str]}.

    history: list of {"role": "user"|"assistant", "content": str}
              (last MAX_HISTORY_TURNS turns, client-managed)
    """
    # 1. Retrieve
    chunks = retrieve(message)

    # 2. Fallback if no relevant context
    if not chunks or chunks[0]["score"] < SCORE_THRESHOLD:
        return {"reply": FALLBACK_REPLY, "sources": []}

    # 3. Build messages for GPT-4o
    context_block = build_context_block(chunks)

    system_with_context = (
        SYSTEM_PROMPT
        + "\n\n---\nCONTEXT (use only this to answer):\n"
        + context_block
        + "\n---"
    )

    messages = [{"role": "system", "content": system_with_context}]

    # Add conversation history (trim to last MAX_HISTORY_TURNS)
    trimmed_history = history[-(MAX_HISTORY_TURNS * 2):]
    messages.extend(trimmed_history)

    # Add current user message
    messages.append({"role": "user", "content": message})

    # 4. Call GPT-4o
    response = _get_openai().chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=400,
    )

    reply = response.choices[0].message.content.strip()
    sources = source_labels(chunks)

    return {"reply": reply, "sources": sources}
