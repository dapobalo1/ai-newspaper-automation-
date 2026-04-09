"""
publish_to_wordpress.py
Takes approved drafts from the DB and creates posts in WordPress via REST API.

Post status mapping:
  - pending_editor    → WordPress 'draft'    (awaiting editor review)
  - pending_publisher → WordPress 'pending'  (awaiting publisher approval)

WordPress category IDs are fetched live from the API so they stay in sync
with whatever categories exist on the site.

Usage: python3 tools/publish_to_wordpress.py
Requirements: WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD in .env
"""

import os
import sqlite3
import json

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: pip3 install requests")

from dotenv import load_dotenv
load_dotenv()

DB_PATH     = os.path.join(os.path.dirname(__file__), "..", "articles.db")
WP_SITE_URL = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASS = os.getenv("WP_APP_PASSWORD", "")

WP_POSTS_URL      = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
WP_CATEGORIES_URL = f"{WP_SITE_URL}/wp-json/wp/v2/categories"

AUTH = (WP_USERNAME, WP_APP_PASS)


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

_category_cache: dict[str, int] = {}


def get_wp_category_id(name: str) -> int | None:
    """Return WordPress category ID for the given name (case-insensitive)."""
    global _category_cache

    if not _category_cache:
        try:
            r = requests.get(WP_CATEGORIES_URL, params={"per_page": 100}, auth=AUTH, timeout=15)
            r.raise_for_status()
            for cat in r.json():
                _category_cache[cat["name"].lower()] = cat["id"]
        except Exception as e:
            print(f"    WARN: Could not fetch WP categories: {e}")
            return None

    return _category_cache.get(name.lower())


# ---------------------------------------------------------------------------
# Post creation
# ---------------------------------------------------------------------------

def create_wp_post(draft: dict) -> int | None:
    """Create a WordPress post as 'draft'. Returns wp_post_id or None."""
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        print("    WARN: WordPress credentials not configured in .env — skipping.")
        return None

    category_id = get_wp_category_id(draft.get("category", ""))

    body_with_attribution = (
        (draft.get("body") or "")
        + f'\n\n<p><em>Source(s): {draft.get("seo_description", "")}</em></p>'
    )

    payload = {
        "title":   draft["headline"],
        "content": body_with_attribution,
        "status":  "draft",
        "excerpt": {"raw": draft.get("seo_description", "")},
    }

    if category_id:
        payload["categories"] = [category_id]

    if draft.get("wp_image_id"):
        payload["featured_media"] = draft["wp_image_id"]

    try:
        r = requests.post(WP_POSTS_URL, auth=AUTH, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        print(f"    WordPress post error: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"    Response: {e.response.text[:300]}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def publish_drafts():
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        print("WordPress credentials not set in .env — cannot publish.")
        print("Set WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD and re-run.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Only push drafts that haven't been sent to WP yet
    c.execute("""
        SELECT id, headline, body, category, seo_description, wp_image_id
        FROM drafts
        WHERE status = 'pending_editor'
          AND (wp_post_id IS NULL OR wp_post_id = 0)
        ORDER BY id
    """)
    drafts = [dict(r) for r in c.fetchall()]

    if not drafts:
        print("No new drafts to publish to WordPress.")
        conn.close()
        return

    print(f"Publishing {len(drafts)} draft(s) to WordPress ...")
    published = 0

    for draft in drafts:
        print(f"\n  Draft {draft['id']}: \"{draft['headline'][:60]}\"")
        wp_id = create_wp_post(draft)

        if wp_id:
            c.execute(
                "UPDATE drafts SET wp_post_id = ? WHERE id = ?",
                (wp_id, draft["id"])
            )
            conn.commit()
            print(f"  ✓ WordPress post created (id={wp_id}) → {WP_SITE_URL}/?p={wp_id}")
            published += 1
        else:
            print(f"  ✗ Failed to create WP post for draft {draft['id']}")

    conn.close()
    print(f"\nDone. {published}/{len(drafts)} posts created in WordPress.")


if __name__ == "__main__":
    publish_drafts()
