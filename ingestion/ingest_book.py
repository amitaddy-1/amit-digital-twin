"""
ingest_book.py — Ingest Amit's book "Nonlinear" into Pinecone

Strategy: Semantic paragraph grouping (NOT fixed token windows)
  - ~350 words (~450 tokens) per chunk, 1-paragraph overlap
  - Named frameworks (DeSIRe, E³) → single dedicated chunk, type=framework
  - Chapter-opening quotes → micro-chunk, type=quote
  - Metadata: source, chapter, type, chunk_index
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone
from pypdf import PdfReader

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
PDF_PATH = Path(__file__).parent.parent / "data" / "Nonlinear.pdf"
NAMESPACE = "book"
EMBED_MODEL = "text-embedding-3-small"
TARGET_WORDS = 350       # target words per chunk
OVERLAP_WORDS = 120      # overlap (≈ 1 paragraph)
BATCH_SIZE = 50          # embed in batches to avoid rate limits

# ── Chapter map (PDF page index → chapter name) ──────────────────────────────
# Derived from actual PDF analysis (0-indexed)
CHAPTER_MAP = [
    (0,   2,   "Front Matter"),
    (2,   4,   "Acknowledgements"),
    (4,   8,   "Foreword"),
    (8,   16,  "Introduction"),
    (16,  28,  "Ch1: The Nonlinear World"),
    (28,  46,  "Ch2: Impact of Technology on our Minds"),
    (46,  60,  "Ch3: The Rise of Artificial Intelligence"),
    (60,  76,  "Ch4: Ushering Digital Renaissance"),
    (76,  88,  "Ch5: Delink"),
    (88,  100, "Ch6: Simplify"),
    (100, 108, "Ch7: Invest"),
    (108, 122, "Ch8: Reskill"),
    (122, 126, "Ch9: Conclusion"),
    (126, 130, "Ch10: An Afterthought"),
]

# Named frameworks to keep as single chunks
FRAMEWORK_PATTERNS = [
    r"DeSIRe",
    r'E[³3]\s+(?:framework|tenet|model)',
    r"Dual Processing Model",
    r"Stimulus.Response Theory",
    r"Law of Effect",
    r"Law of Readiness",
    r"Shu Ha Ri",
]


def get_chapter(page_idx: int) -> str:
    for start, end, name in CHAPTER_MAP:
        if start <= page_idx < end:
            return name
    return "Unknown"


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Returns list of (page_idx, text)."""
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        # Clean up hyphenation artefacts from PDF extraction
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        pages.append((i, text))
    return pages


def pages_to_chapter_texts(pages: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """Group pages by chapter, return list of (chapter_name, full_text)."""
    chapters = {}
    for page_idx, text in pages:
        chapter = get_chapter(page_idx)
        chapters.setdefault(chapter, []).append(text)

    result = []
    for _, start, _, name in [(s, s, e, n) for s, e, n in [(s,e,n) for s,e,n in
                               [(s,e,n) for s,e,n in CHAPTER_MAP]]]:
        pass

    # Preserve order
    seen = []
    for _, end, name in CHAPTER_MAP:
        if name not in seen and name in chapters:
            seen.append(name)
            result.append((name, "\n\n".join(chapters[name])))
    return result


def split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (double-newline separated), filter blanks."""
    paragraphs = re.split(r"\n{2,}", text)
    return [p.strip() for p in paragraphs if p.strip() and len(p.strip()) > 40]


def is_framework_paragraph(text: str) -> bool:
    for pattern in FRAMEWORK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def is_quote_paragraph(text: str) -> bool:
    """Chapter-opening quotes: usually short, wrapped in quotes, followed by attribution."""
    stripped = text.strip()
    return (
        (stripped.startswith('"') or stripped.startswith('“'))
        and len(stripped.split()) < 60
        and ('–' in stripped or '—' in stripped or '-' in stripped)
    )


def chunk_chapter(chapter_name: str, text: str) -> list[dict]:
    paragraphs = split_into_paragraphs(text)
    chunks = []
    chunk_index = 0

    i = 0
    current_paragraphs = []
    current_words = 0

    # Buffer for overlap
    overlap_buffer = []

    while i < len(paragraphs):
        para = paragraphs[i]
        para_words = len(para.split())

        # ── Quote: extract as standalone micro-chunk ──────────────────────
        if is_quote_paragraph(para):
            # Flush current buffer first
            if current_paragraphs:
                text_block = "\n\n".join(current_paragraphs)
                chunks.append(_make_chunk(text_block, chapter_name, "prose", chunk_index))
                chunk_index += 1
                overlap_buffer = current_paragraphs[-1:]
                current_paragraphs = list(overlap_buffer)
                current_words = sum(len(p.split()) for p in current_paragraphs)

            chunks.append(_make_chunk(para, chapter_name, "quote", chunk_index))
            chunk_index += 1
            i += 1
            continue

        # ── Framework: collect paragraphs until framework ends ────────────
        if is_framework_paragraph(para):
            # Flush current buffer first
            if current_paragraphs:
                text_block = "\n\n".join(current_paragraphs)
                chunks.append(_make_chunk(text_block, chapter_name, "prose", chunk_index))
                chunk_index += 1
                current_paragraphs = []
                current_words = 0

            # Collect the framework block (current + next few paragraphs while related)
            fw_paragraphs = [para]
            j = i + 1
            while j < len(paragraphs) and j < i + 8:  # max 8 paragraphs for a framework
                if is_quote_paragraph(paragraphs[j]):
                    break
                fw_paragraphs.append(paragraphs[j])
                j += 1

            fw_text = "\n\n".join(fw_paragraphs)
            chunks.append(_make_chunk(fw_text, chapter_name, "framework", chunk_index))
            chunk_index += 1
            i = j
            continue

        # ── Normal prose: accumulate until TARGET_WORDS ───────────────────
        current_paragraphs.append(para)
        current_words += para_words

        if current_words >= TARGET_WORDS:
            text_block = "\n\n".join(current_paragraphs)
            chunks.append(_make_chunk(text_block, chapter_name, "prose", chunk_index))
            chunk_index += 1

            # Keep last paragraph as overlap for next chunk
            overlap_para = current_paragraphs[-1]
            current_paragraphs = [overlap_para]
            current_words = len(overlap_para.split())

        i += 1

    # Flush remaining
    if current_paragraphs and current_words > 50:
        text_block = "\n\n".join(current_paragraphs)
        chunks.append(_make_chunk(text_block, chapter_name, "prose", chunk_index))

    return chunks


def _make_chunk(text: str, chapter: str, chunk_type: str, index: int) -> dict:
    chunk_id = f"book_{chapter.replace(' ', '_').replace(':', '')}_{index}"
    return {
        "id": chunk_id,
        "text": text.strip(),
        "metadata": {
            "source": "book",
            "chapter": chapter,
            "title": f"Nonlinear — {chapter}",
            "type": chunk_type,
            "chunk_index": index,
        },
    }


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
        # Pinecone metadata values must be strings/numbers/lists
        metadata["text"] = chunk["text"][:2000]  # trim very long chunks for metadata
        vectors.append({
            "id": chunk["id"],
            "values": embedding,
            "metadata": metadata,
        })

    # Upsert in batches of 100
    for i in range(0, len(vectors), 100):
        batch = vectors[i:i + 100]
        index.upsert(vectors=batch, namespace=NAMESPACE)
        print(f"  Upserted batch {i // 100 + 1}/{-(-len(vectors) // 100)}")


def main():
    print("Ingesting Nonlinear book...")
    pages = extract_pages(PDF_PATH)
    print(f"  Extracted {len(pages)} pages")

    chapter_texts = pages_to_chapter_texts(pages)
    print(f"  Grouped into {len(chapter_texts)} chapters")

    all_chunks = []
    for chapter_name, text in chapter_texts:
        chapter_chunks = chunk_chapter(chapter_name, text)
        all_chunks.extend(chapter_chunks)
        print(f"  {chapter_name}: {len(chapter_chunks)} chunks")

    print(f"\n  Total chunks: {len(all_chunks)}")

    # Count by type
    from collections import Counter
    type_counts = Counter(c["metadata"]["type"] for c in all_chunks)
    for t, n in type_counts.items():
        print(f"    {t}: {n}")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print("\n  Generating embeddings...")
    embeddings = embed_in_batches([c["text"] for c in all_chunks], client)

    print("\n  Upserting to Pinecone...")
    upsert_to_pinecone(all_chunks, embeddings)
    print("Book ingestion complete.")


if __name__ == "__main__":
    main()
