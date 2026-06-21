"""
ingest_blog.py — Ingest Amit's blog posts into Pinecone

Two modes (select via --mode flag):

  folder   (default) — reads .md / .txt files from data/blog_posts/
                        Use this for every new post going forward.
                        Each file must start with YAML frontmatter:
                          ---
                          title: Post title
                          url: https://www.linkedin.com/pulse/...
                          ---
                          Full article text...

  linkedin-export     — reads HTML files from a LinkedIn data export zip.
                        Use this once to ingest the full historical archive.
                        Pass the zip path with --zip path/to/export.zip

Usage:
  python ingestion/ingest_blog.py                          # folder mode (default)
  python ingestion/ingest_blog.py --mode folder            # explicit
  python ingestion/ingest_blog.py --mode linkedin-export --zip ~/Downloads/Basic_LinkedInDataExport_06-21-2026.zip
"""

import argparse
import os
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
BLOG_POSTS_DIR = Path(__file__).parent.parent / "data" / "blog_posts"
NAMESPACE = "blog"
EMBED_MODEL = "text-embedding-3-small"
MAX_WORDS_PER_CHUNK = 800
OVERLAP_WORDS = 100
BATCH_SIZE = 50
LINKEDIN_PULSE_BASE = "https://www.linkedin.com/pulse"


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_md_file(path: Path) -> dict | None:
    """
    Parse a .md or .txt file with YAML frontmatter.
    Returns {title, url, paragraphs, word_count} or None on error.
    """
    raw = path.read_text(encoding="utf-8").strip()

    title, url = "", ""
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            frontmatter = raw[3:end].strip()
            body = raw[end + 3:].strip()
            for line in frontmatter.splitlines():
                if line.lower().startswith("title:"):
                    title = line.split(":", 1)[1].strip()
                elif line.lower().startswith("url:"):
                    url = line.split(":", 1)[1].strip()
        else:
            body = raw
    else:
        body = raw

    if not title:
        title = path.stem.replace("-", " ").replace("_", " ").title()

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip() and len(p.strip()) > 30]
    if not paragraphs:
        print(f"  Skipping {path.name}: no usable content")
        return None

    return {
        "title": title,
        "url": url,
        "paragraphs": paragraphs,
        "word_count": sum(len(p.split()) for p in paragraphs),
    }


def parse_linkedin_html(html: str, slug: str) -> dict | None:
    """
    Parse a single LinkedIn export HTML file.
    Title comes from <h1>; body from <p> and <blockquote> inside <div>.
    URL is reconstructed from the filename slug.
    """
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else slug.replace("-", " ").title()

    url = f"{LINKEDIN_PULSE_BASE}/{slug}"

    div = soup.find("div")
    if not div:
        return None

    paragraphs = []
    for el in div.find_all(["p", "blockquote", "h2", "h3"]):
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


def parse_linkedin_zip(zip_path: str) -> list[dict]:
    """Read all HTML article files from a LinkedIn data export zip."""
    posts = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        html_files = [n for n in zf.namelist()
                      if n.startswith("Articles/Articles/") and n.endswith(".html")]
        print(f"  Found {len(html_files)} HTML files in zip")
        for name in sorted(html_files):
            slug = Path(name).stem          # e.g. memory-palace-donald-hebb-amit-adarkar-vwfgf
            html = zf.read(name).decode("utf-8", errors="replace")
            post = parse_linkedin_html(html, slug)
            if post:
                posts.append(post)
            else:
                print(f"  Skipping {Path(name).name}: no usable content")
    return posts


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_post(post: dict) -> list[dict]:
    """1 chunk if ≤800 words; split at paragraph boundaries if longer."""
    title       = post["title"]
    url         = post["url"]
    paragraphs  = post["paragraphs"]
    total_words = post["word_count"]

    if total_words <= MAX_WORDS_PER_CHUNK:
        text = f"{title}\n\n" + "\n\n".join(paragraphs)
        return [_make_chunk(text, title, url, 0)]

    chunks = []
    current_paras = [title]
    current_words = len(title.split())
    chunk_idx = 0
    overlap_para = None

    for para in paragraphs:
        para_words = len(para.split())
        if current_words + para_words > MAX_WORDS_PER_CHUNK and current_words > OVERLAP_WORDS:
            chunks.append(_make_chunk("\n\n".join(current_paras), title, url, chunk_idx))
            chunk_idx += 1
            current_paras = [f"{title} (continued)"]
            if overlap_para:
                current_paras.append(overlap_para)
            current_words = sum(len(p.split()) for p in current_paras)

        current_paras.append(para)
        current_words += para_words
        overlap_para = para

    if len(current_paras) > 1:
        chunks.append(_make_chunk("\n\n".join(current_paras), title, url, chunk_idx))

    return chunks


def _make_chunk(text: str, title: str, url: str, index: int) -> dict:
    slug = re.sub(r"[^a-z0-9_]", "_", url.rstrip("/").split("/")[-1].lower())[:50] if url \
        else re.sub(r"[^a-z0-9_]", "_", title.lower())[:50]
    return {
        "id": f"blog_{slug}_{index}",
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
        metadata["text"] = chunk["text"]   # full chunk text — chunking already bounds size
        vectors.append({"id": chunk["id"], "values": embedding, "metadata": metadata})

    for i in range(0, len(vectors), 100):
        batch = vectors[i:i + 100]
        index.upsert(vectors=batch, namespace=NAMESPACE)
        print(f"  Upserted batch {i // 100 + 1}/{-(-len(vectors) // 100)}")


# ── Main ───────────────────────────────────────────────────────────────────────

def _ingest(mode: str = "folder", zip_path: str | None = None):
    if mode == "folder":
        print(f"Ingesting blog posts from {BLOG_POSTS_DIR} ...")
        files = sorted(BLOG_POSTS_DIR.glob("*.md")) + sorted(BLOG_POSTS_DIR.glob("*.txt"))
        if not files:
            print(f"  No .md or .txt files found in {BLOG_POSTS_DIR}. Nothing to ingest.")
            return
        posts = []
        for f in files:
            print(f"  Parsing {f.name}")
            post = parse_md_file(f)
            if post:
                posts.append(post)
        print(f"  Parsed {len(posts)} posts")

    else:  # linkedin-export
        if not zip_path:
            print("Error: --zip is required for linkedin-export mode.")
            return
        print(f"Ingesting from LinkedIn export zip: {zip_path} ...")
        posts = parse_linkedin_zip(zip_path)
        print(f"  Parsed {len(posts)} articles")

    if not posts:
        print("Nothing to ingest.")
        return

    all_chunks = []
    for post in posts:
        chunks = chunk_post(post)
        all_chunks.extend(chunks)
        print(f"  '{post['title']}' → {len(chunks)} chunk(s), {post['word_count']} words")

    print(f"\nTotal chunks: {len(all_chunks)}")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print("Generating embeddings...")
    embeddings = embed_in_batches([c["text"] for c in all_chunks], client)

    print("Upserting to Pinecone...")
    upsert_to_pinecone(all_chunks, embeddings)
    print("Blog ingestion complete.")


def run(mode: str = "folder", zip_path: str | None = None):
    """Programmatic entry point — called by run_all.py."""
    _ingest(mode=mode, zip_path=zip_path)


def main():
    parser = argparse.ArgumentParser(description="Ingest blog posts into Pinecone")
    parser.add_argument(
        "--mode", choices=["folder", "linkedin-export"], default="folder",
        help="folder: read from data/blog_posts/ (default). linkedin-export: read LinkedIn zip."
    )
    parser.add_argument(
        "--zip", default=None,
        help="Path to LinkedIn data export zip (required for linkedin-export mode)"
    )
    args = parser.parse_args()
    _ingest(mode=args.mode, zip_path=args.zip)


if __name__ == "__main__":
    main()
