"""
ingest_linkedin.py — Ingest Amit's LinkedIn PDF into Pinecone

Strategy: 3 section-based chunks (no splitting needed — it's 3 pages)
  Chunk 1: Professional summary + top skills + certifications
  Chunk 2: Employment history (all roles)
  Chunk 3: Education
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
PDF_PATH = Path(__file__).parent.parent / "data" / "LinkedIn.pdf"
NAMESPACE = "linkedin"
EMBED_MODEL = "text-embedding-3-small"


def extract_text_by_page(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def build_chunks(pages: list[str]) -> list[dict]:
    """
    LinkedIn PDF has 3 pages:
      Page 1: Contact, Top Skills, Certifications, Summary
      Page 2: Employment history
      Page 3: More experience + Education
    """
    full_text = "\n".join(pages)

    # Split on known section anchors
    summary_match = re.search(r"Summary\n(.+?)(?=\n[A-Z][a-z]+ [A-Z]|\nExperience|\nEducation|\Z)",
                               full_text, re.DOTALL)
    summary = summary_match.group(1).strip() if summary_match else ""

    # Skills + certs are on page 1
    page1 = pages[0]
    skills_match = re.search(r"Top Skills\n(.+?)(?=\nCertifications|\nSummary|\Z)", page1, re.DOTALL)
    skills = skills_match.group(1).strip() if skills_match else ""

    certs_match = re.search(r"Certifications\n(.+?)(?=\nSummary|\Z)", page1, re.DOTALL)
    certs = certs_match.group(1).strip() if certs_match else ""

    # Education is on page 3
    page3 = pages[2] if len(pages) >= 3 else ""
    edu_match = re.search(r"Education\n(.+)", page3, re.DOTALL)
    education = edu_match.group(1).strip() if edu_match else ""

    # Employment: pages 2 + start of page 3 (before Education)
    employment_raw = pages[1] if len(pages) >= 2 else ""
    if edu_match and len(pages) >= 3:
        employment_raw += "\n" + page3[:edu_match.start()]

    chunks = [
        {
            "id": "linkedin_summary",
            "text": f"Amit Adarkar — Professional Summary\n\n{summary}\n\nTop Skills: {skills}\n\nCertifications: {certs}",
            "metadata": {
                "source": "linkedin",
                "title": "Professional Summary",
                "type": "profile",
                "chunk_index": 0,
            },
        },
        {
            "id": "linkedin_experience",
            "text": f"Amit Adarkar — Career & Work Experience\n\n{employment_raw.strip()}",
            "metadata": {
                "source": "linkedin",
                "title": "Work Experience",
                "type": "experience",
                "chunk_index": 1,
            },
        },
        {
            "id": "linkedin_education",
            "text": f"Amit Adarkar — Education\n\n{education}",
            "metadata": {
                "source": "linkedin",
                "title": "Education",
                "type": "education",
                "chunk_index": 2,
            },
        },
    ]
    return chunks


def embed_texts(texts: list[str], client: OpenAI) -> list[list[float]]:
    response = client.embeddings.create(
        model=EMBED_MODEL,
        input=[t.replace("\n", " ") for t in texts],
    )
    return [item.embedding for item in response.data]


def upsert_to_pinecone(chunks: list[dict], embeddings: list[list[float]]):
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(os.environ["PINECONE_INDEX"])

    vectors = []
    for chunk, embedding in zip(chunks, embeddings):
        metadata = chunk["metadata"].copy()
        metadata["text"] = chunk["text"]   # store text in metadata for retrieval
        vectors.append({
            "id": chunk["id"],
            "values": embedding,
            "metadata": metadata,
        })

    index.upsert(vectors=vectors, namespace=NAMESPACE)
    print(f"Upserted {len(vectors)} LinkedIn chunks to namespace '{NAMESPACE}'")


def main():
    print("Ingesting LinkedIn PDF...")
    pages = extract_text_by_page(PDF_PATH)
    print(f"  Extracted {len(pages)} pages")

    chunks = build_chunks(pages)
    print(f"  Built {len(chunks)} chunks")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embeddings = embed_texts([c["text"] for c in chunks], client)
    print(f"  Generated {len(embeddings)} embeddings")

    upsert_to_pinecone(chunks, embeddings)
    print("LinkedIn ingestion complete.")


if __name__ == "__main__":
    main()
