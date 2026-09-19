"""
check_twin.py — Which blog posts are actually live in the twin?

Answers the question "did my last post make it into Pinecone?" by comparing
the local .md files in data/blog_posts/ against what's really in the index.

For each local post it derives the exact chunk IDs the way ingest_blog.py does,
then asks Pinecone whether those vectors exist. No guessing, no scraping.

Usage:
  python scripts/check_twin.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pinecone import Pinecone

# Reuse the real parsing + chunking logic so IDs always match ingestion
sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from ingest_blog import parse_md_file, chunk_post  # noqa: E402

load_dotenv()

BLOG_POSTS_DIR = Path(__file__).parent.parent / "data" / "blog_posts"
NAMESPACE = "blog"


def main():
    files = sorted(BLOG_POSTS_DIR.glob("*.md")) + sorted(BLOG_POSTS_DIR.glob("*.txt"))
    if not files:
        print(f"No posts found in {BLOG_POSTS_DIR}")
        return

    # Build {post_title: [expected chunk ids]}
    expected = {}
    for f in files:
        post = parse_md_file(f)
        if not post:
            continue
        ids = [c["id"] for c in chunk_post(post)]
        expected[post["title"]] = ids

    all_ids = [cid for ids in expected.values() for cid in ids]

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(os.environ["PINECONE_INDEX"])

    # Fetch all expected ids in one call; present ids come back in .vectors
    present = set(index.fetch(ids=all_ids, namespace=NAMESPACE).vectors.keys())

    stats = index.describe_index_stats()
    blog_count = stats.namespaces.get(NAMESPACE).vector_count if NAMESPACE in stats.namespaces else 0

    print(f"\nBlog vectors in Pinecone (namespace '{NAMESPACE}'): {blog_count}")
    print("=" * 60)

    last_live = None
    for title, ids in expected.items():
        in_twin = all(cid in present for cid in ids)
        mark = "✓ in twin " if in_twin else "✗ MISSING "
        chunk_note = "" if len(ids) == 1 else f"  ({sum(c in present for c in ids)}/{len(ids)} chunks)"
        print(f"  {mark} {title}{chunk_note}")
        if in_twin:
            last_live = title

    print("=" * 60)
    if last_live:
        # "last" = last in file order; note file order is alphabetical, not chronological
        print(f"Most recent post confirmed in the twin (by file order): {last_live}")
    else:
        print("No blog posts are in the twin yet — run: python ingestion/ingest_blog.py --mode folder")
    print()


if __name__ == "__main__":
    main()
