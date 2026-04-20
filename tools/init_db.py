"""
init_db.py
Run once to create the SQLite database and schema.
Usage: python tools/init_db.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "articles.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS raw_articles (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            url              TEXT UNIQUE,
            url_hash         TEXT UNIQUE,
            source           TEXT,
            title            TEXT,
            summary          TEXT,
            published_at     TEXT,
            fetched_at       TEXT,
            category         TEXT,
            raw_content      TEXT,
            story_group_id   TEXT,
            is_duplicate     INTEGER DEFAULT 0,
            priority_score   REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_article_ids  TEXT,
            headline         TEXT,
            body             TEXT,
            category         TEXT,
            seo_description  TEXT,
            image_keywords   TEXT,
            wp_post_id       INTEGER,
            wp_image_id      INTEGER,
            status           TEXT DEFAULT 'pending_editor',
            created_at       TEXT,
            editor_notes     TEXT,
            rejection_reason TEXT
        );
    """)

    conn.commit()
    conn.close()
    print(f"Database initialised at: {os.path.abspath(DB_PATH)}")


if __name__ == "__main__":
    init_db()
