"""
run_all.py — Run all three ingestion scripts in sequence.
Run this once to populate Pinecone, and re-run whenever content updates.

Usage:
  python ingestion/run_all.py
  python ingestion/run_all.py --source blog   # re-ingest blog only
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion import ingest_linkedin, ingest_book, ingest_blog


def main():
    parser = argparse.ArgumentParser(description="Run Amit Digital Twin ingestion")
    parser.add_argument(
        "--source",
        choices=["all", "linkedin", "book", "blog"],
        default="all",
        help="Which source to ingest (default: all)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Amit Digital Twin — Ingestion Pipeline")
    print("=" * 60)

    if args.source in ("all", "linkedin"):
        print("\n[1/3] LinkedIn")
        print("-" * 40)
        ingest_linkedin.main()

    if args.source in ("all", "book"):
        print("\n[2/3] Nonlinear (book)")
        print("-" * 40)
        ingest_book.main()

    if args.source in ("all", "blog"):
        print("\n[3/3] Blog (random-walk.blog)")
        print("-" * 40)
        ingest_blog.main()

    print("\n" + "=" * 60)
    print("Ingestion complete. All sources are in Pinecone.")
    print("=" * 60)


if __name__ == "__main__":
    main()
