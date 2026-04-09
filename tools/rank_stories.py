"""
rank_stories.py
Scores non-duplicate articles and returns the top N for draft generation.

Scoring signals:
  - Recency:         1.0 if published < 6h ago, 0.5 if 6–24h ago
  - Source authority: weight per source (0.2 – 1.0)
  - Cross-source:    +0.5 bonus if same story appeared in 2+ sources
  - Category boost:  +0.2 for Nigeria/Diaspora focus (core editorial priority)

Usage: python3 tools/rank_stories.py
Output: prints ranked list + writes .tmp/ranked_YYYY-MM-DD.json
"""

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH  = os.path.join(os.path.dirname(__file__), "..", "articles.db")
TMP_DIR  = os.path.join(os.path.dirname(__file__), "..", ".tmp")
LIMIT    = int(os.getenv("DAILY_ARTICLE_LIMIT", "8"))
LOOKBACK = 26  # hours


SOURCE_AUTHORITY = {
    "Premium Times":    1.0,
    "The Cable":        1.0,
    "Vanguard Nigeria": 0.9,
    "Daily Trust":      0.9,
    "ThisDay Live":     0.8,
    "BBC Africa":       0.9,
    "Al Jazeera":       0.85,
    "Kyiv Independent": 0.75,
    "TechCabal":        0.8,
}

CATEGORY_BOOST = {"Nigeria", "Diaspora", "Politics", "Business"}


def score_article(row: dict, group_sizes: dict[str, int]) -> float:
    score = 0.0

    # Recency
    if row["published_at"]:
        try:
            pub = datetime.fromisoformat(row["published_at"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(tz=timezone.utc) - pub).total_seconds() / 3600
            score += 1.0 if age_h < 6 else 0.5
        except ValueError:
            score += 0.3
    else:
        score += 0.3

    # Source authority
    score += SOURCE_AUTHORITY.get(row["source"], 0.5)

    # Cross-source bonus
    gid = row.get("story_group_id")
    if gid and group_sizes.get(gid, 1) > 1:
        score += 0.5

    # Category boost
    if row.get("category") in CATEGORY_BOOST:
        score += 0.2

    return round(score, 3)


def rank() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=LOOKBACK)).isoformat()

    # Count group sizes for cross-source bonus
    c.execute("""
        SELECT story_group_id, COUNT(*) as cnt
        FROM raw_articles
        WHERE fetched_at >= ?
          AND story_group_id IS NOT NULL AND story_group_id != ''
        GROUP BY story_group_id
    """, (cutoff,))
    group_sizes = {r["story_group_id"]: r["cnt"] for r in c.fetchall()}

    # Fetch non-duplicate anchors
    c.execute("""
        SELECT id, url, source, title, summary, published_at,
               category, story_group_id, raw_content
        FROM raw_articles
        WHERE fetched_at >= ?
          AND is_duplicate = 0
        ORDER BY id
    """, (cutoff,))
    articles = [dict(r) for r in c.fetchall()]
    conn.close()

    # Score + tag multi-source
    for a in articles:
        a["priority_score"] = score_article(a, group_sizes)
        gid = a.get("story_group_id")
        a["is_multi_source"] = bool(gid and group_sizes.get(gid, 1) > 1)

    # Sort and cap
    ranked = sorted(articles, key=lambda x: x["priority_score"], reverse=True)[:LIMIT]

    # Persist scores back to DB
    conn = sqlite3.connect(DB_PATH)
    for a in articles:
        conn.execute(
            "UPDATE raw_articles SET priority_score = ? WHERE id = ?",
            (a["priority_score"], a["id"])
        )
    conn.commit()
    conn.close()

    return ranked


def save_ranked(ranked: list[dict]) -> str:
    os.makedirs(TMP_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(TMP_DIR, f"ranked_{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)
    return path


if __name__ == "__main__":
    ranked = rank()
    path = save_ranked(ranked)

    print(f"Top {len(ranked)} stories selected (limit: {LIMIT}):\n")
    for i, a in enumerate(ranked, 1):
        multi = " [MULTI-SOURCE]" if a.get("is_multi_source") else ""
        print(f"  {i}. [{a['priority_score']:.2f}] [{a['category']}]{multi}")
        print(f"     {a['title'][:80]}")
        print(f"     Source: {a['source']}")
        print()

    print(f"Saved to: {path}")
