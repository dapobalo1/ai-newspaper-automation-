"""
store_articles.py
Reads the latest raw_feed JSON from .tmp/ and inserts new articles into
the raw_articles table. Already-seen URLs are skipped (UNIQUE constraint).

Usage: python3 tools/store_articles.py
       python3 tools/store_articles.py --file .tmp/raw_feed_2026-03-31.json
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from glob import glob

DB_PATH  = os.path.join(os.path.dirname(__file__), "..", "articles.db")
TMP_DIR  = os.path.join(os.path.dirname(__file__), "..", ".tmp")


def latest_feed_file() -> str:
    """Return the most recently modified raw_feed JSON in .tmp/."""
    files = sorted(glob(os.path.join(TMP_DIR, "raw_feed_*.json")), reverse=True)
    if not files:
        raise FileNotFoundError(f"No raw_feed_*.json found in {TMP_DIR}")
    return files[0]


def load_articles(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def store(articles: list[dict]) -> tuple[int, int]:
    """Insert articles, skip duplicates. Returns (inserted, skipped)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    inserted = skipped = 0

    for a in articles:
        try:
            c.execute("""
                INSERT INTO raw_articles
                    (url, url_hash, source, title, summary, published_at,
                     fetched_at, category, raw_content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                a["url"],
                a["url_hash"],
                a["source"],
                a["title"],
                a["summary"],
                a.get("published_at"),
                a.get("fetched_at", datetime.now(tz=timezone.utc).isoformat()),
                a.get("category", "General"),
                a.get("raw_content", ""),
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            # URL already in DB — skip silently
            skipped += 1

    conn.commit()
    conn.close()
    return inserted, skipped


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--file":
        feed_file = sys.argv[2]
    else:
        feed_file = latest_feed_file()

    print(f"Loading: {feed_file}")
    articles = load_articles(feed_file)
    print(f"Articles in file: {len(articles)}")

    inserted, skipped = store(articles)
    print(f"Inserted: {inserted}  |  Skipped (duplicates): {skipped}")
