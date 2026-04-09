"""
deduplicate.py
Groups articles from the DB that cover the same story across different sources.
Uses fuzzy title similarity (token-based overlap) — no ML dependencies needed.

Logic:
  - For today's unprocessed articles, compare every pair of titles.
  - If similarity >= THRESHOLD, assign them the same story_group_id.
  - The article with the most detail (longest summary) becomes the group anchor.
  - All others are flagged is_duplicate = 1.
  - Groups with 2+ sources are flagged for multi-source synthesis.

Usage: python3 tools/deduplicate.py
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

DB_PATH   = os.path.join(os.path.dirname(__file__), "..", "articles.db")
THRESHOLD = 0.35   # Jaccard similarity — tune up/down to be more/less aggressive
LOOKBACK_HOURS = 26  # process articles from the last N hours


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def tokenise(text: str) -> set[str]:
    """Lowercase words, 3+ chars, strip punctuation."""
    import re
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return {w for w in words if len(w) >= 3}


def jaccard(a: str, b: str) -> float:
    sa, sb = tokenise(a), tokenise(b)
    if not sa or not sb:
        return 0.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def deduplicate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat()

    # Fetch recent, unprocessed articles (no story_group_id yet)
    c.execute("""
        SELECT id, title, summary, source
        FROM raw_articles
        WHERE fetched_at >= ?
          AND (story_group_id IS NULL OR story_group_id = '')
        ORDER BY id
    """, (cutoff,))
    articles = [dict(r) for r in c.fetchall()]

    if not articles:
        print("No new articles to deduplicate.")
        conn.close()
        return

    print(f"Comparing {len(articles)} articles for duplicate stories ...")

    # Union-Find for grouping
    parent = {a["id"]: a["id"] for a in articles}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    # Compare all pairs
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            sim = jaccard(articles[i]["title"], articles[j]["title"])
            if sim >= THRESHOLD:
                union(articles[i]["id"], articles[j]["id"])

    # Build groups
    groups: dict[int, list[dict]] = {}
    for a in articles:
        root = find(a["id"])
        groups.setdefault(root, []).append(a)

    # Assign story_group_ids and mark duplicates
    updated = duplicate_count = group_count = 0

    for root, members in groups.items():
        group_id = str(uuid.uuid4())
        # Anchor = longest summary (most detail)
        anchor = max(members, key=lambda x: len(x.get("summary") or ""))
        multi_source = len({m["source"] for m in members}) > 1

        for m in members:
            is_dup = 0 if m["id"] == anchor["id"] else 1
            if is_dup:
                duplicate_count += 1
            c.execute("""
                UPDATE raw_articles
                SET story_group_id = ?,
                    is_duplicate   = ?
                WHERE id = ?
            """, (group_id, is_dup, m["id"]))
            updated += 1

        if len(members) > 1:
            group_count += 1
            sources = [m["source"] for m in members]
            synth_flag = " [MULTI-SOURCE SYNTHESIS]" if multi_source else ""
            print(f"  Group ({len(members)} articles){synth_flag}: \"{anchor['title'][:60]}\"")
            print(f"    Sources: {', '.join(set(sources))}")

    conn.commit()
    conn.close()

    print(f"\nDone. {updated} articles processed, {group_count} story groups, {duplicate_count} marked as duplicates.")


if __name__ == "__main__":
    deduplicate()
