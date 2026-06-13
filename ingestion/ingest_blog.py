"""
ingest_blog.py — Crawl and ingest Amit's LinkedIn newsletter articles into Pinecone

Strategy:
  - Scrape LinkedIn Pulse article URLs from random-walk.blog homepage
  - Extract clean post text from each LinkedIn Pulse article
  - 1 chunk per post (≤800 words); split at paragraph boundary if longer
  - Each chunk tagged with post title + URL
"""

import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
BLOG_URL = "https://random-walk.blog"
NAMESPACE = "blog"
EMBED_MODEL = "text-embedding-3-small"
MAX_WORDS_PER_CHUNK = 800
OVERLAP_WORDS = 100
BATCH_SIZE = 50
CRAWL_DELAY = 1.0   # seconds between requests (be polite to LinkedIn)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


# ── Crawling ──────────────────────────────────────────────────────────────────

def get_all_post_urls(base_url: str) -> list[str]:
    """
    Scrape random-walk.blog for LinkedIn Pulse article links.
    The blog embeds newsletter links as href="https://www.linkedin.com/pulse/..."
    """
    urls = set()

    try:
        r = requests.get(base_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            # Strip tracking params
            href = href.split("?")[0].rstrip("/")
            if is_post_url(href):
                urls.add(href)
        print(f"  Found {len(urls)} LinkedIn Pulse URLs on blog homepage")
    except Exception as e:
        print(f"  Failed to fetch blog homepage: {e}")

    return sorted(urls)


def is_post_url(url: str) -> bool:
    """Is this a LinkedIn Pulse article URL?"""
    return "linkedin.com/pulse/" in url


def extract_post(url: str) -> dict | None:
    """Fetch and extract clean text from a LinkedIn Pulse article."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  Skipping {url}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # Extract title from h1
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)

    # LinkedIn Pulse article body lives in div.article-main__content
    content_el = soup.select_one("div.article-main__content")
    if not content_el:
        # Fallback: article tag
        content_el = soup.find("article")
    if not content_el:
        print(f"  No content found at {url}")
        return None

    # Get clean paragraphs from p, h2, h3, blockquote
    paragraphs = []
    for el in content_el.find_all(["p", "h2", "h3", "blockquote"]):
        text = el.get_text(separator=" ", strip=True)
        if text and len(text) > 30:
            paragraphs.append(text)

    if not paragraphs:
        return None

    return {
        "title": title,
        "url": url,
        "paragraphs": paragraphs,
        "word_count": sum(len(p.split()) for p in paragraphs),
    }


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_post(post: dict) -> list[dict]:
    """Split a post into chunks. 1 chunk if ≤800 words, else split at paragraphs."""
    title = post["title"]
    url = post["url"]
    paragraphs = post["paragraphs"]
    total_words = post["word_count"]

    if total_words <= MAX_WORDS_PER_CHUNK:
        # Single chunk for the whole post
        text = f"{title}\n\n" + "\n\n".join(paragraphs)
        return [_make_chunk(text, title, url, 0)]

    # Multi-chunk: split at paragraph boundaries
    chunks = []
    current_paras = [title]  # prepend title to first chunk
    current_words = len(title.split())
    chunk_idx = 0
    overlap_para = None

    for para in paragraphs:
        para_words = len(para.split())

        if current_words + para_words > MAX_WORDS_PER_CHUNK and current_words > OVERLAP_WORDS:
            text = "\n\n".join(current_paras)
            chunks.append(_make_chunk(text, title, url, chunk_idx))
            chunk_idx += 1

            # Start next chunk with overlap
            current_paras = [f"{title} (continued)"]
            if overlap_para:
                current_paras.append(overlap_para)
            current_words = sum(len(p.split()) for p in current_paras)

        current_paras.append(para)
        current_words += para_words
        overlap_para = para

    # Flush remaining
    if len(current_paras) > 1:
        text = "\n\n".join(current_paras)
        chunks.append(_make_chunk(text, title, url, chunk_idx))

    return chunks


def _make_chunk(text: str, title: str, url: str, index: int) -> dict:
    safe_slug = re.sub(r"[^a-z0-9_]", "_", url.rstrip("/").split("/")[-1].lower())[:50]
    chunk_id = f"blog_{safe_slug}_{index}"
    return {
        "id": chunk_id,
        "text": text.strip(),
        "metadata": {
            "source": "blog",
            "title": title,
            "url": url,
            "type": "prose",
            "chunk_index": index,
        },
    }


# ── Embedding + Pinecone ───────────────────────────────────────────────────────

def embed_in_batches(texts: list[str], client: OpenAI) -> list[list[float]]:
    embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        response = client.embeddings.create(
            model=EMBED_MODEL,
            input=[t.replace("\n", " ") for t in batch],
        )
        embeddings.extend([item.embedding for item in response.data])
        print(f"  Embedded batch {i // BATCH_SIZE + 1}/{-(-len(texts) // BATCH_SIZE)}")
    return embeddings


def upsert_to_pinecone(chunks: list[dict], embeddings: list[list[float]]):
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(os.environ["PINECONE_INDEX"])

    vectors = []
    for chunk, embedding in zip(chunks, embeddings):
        metadata = chunk["metadata"].copy()
        metadata["text"] = chunk["text"][:2000]
        vectors.append({
            "id": chunk["id"],
            "values": embedding,
            "metadata": metadata,
        })

    for i in range(0, len(vectors), 100):
        batch = vectors[i:i + 100]
        index.upsert(vectors=batch, namespace=NAMESPACE)
        print(f"  Upserted batch {i // 100 + 1}/{-(-len(vectors) // 100)}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("Ingesting blog: random-walk.blog...")

    print("  Discovering LinkedIn Pulse URLs from blog homepage...")
    post_urls = get_all_post_urls(BLOG_URL)
    print(f"  Total: {len(post_urls)} articles to ingest")

    all_chunks = []
    skipped = 0

    for i, url in enumerate(post_urls):
        print(f"  [{i+1}/{len(post_urls)}] {url}")
        post = extract_post(url)
        if not post:
            skipped += 1
            continue
        chunks = chunk_post(post)
        all_chunks.extend(chunks)
        time.sleep(CRAWL_DELAY)

    print(f"\n  Total chunks: {len(all_chunks)} (skipped {skipped} posts)")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print("  Generating embeddings...")
    embeddings = embed_in_batches([c["text"] for c in all_chunks], client)

    print("  Upserting to Pinecone...")
    upsert_to_pinecone(all_chunks, embeddings)
    print("Blog ingestion complete.")


if __name__ == "__main__":
    main()
