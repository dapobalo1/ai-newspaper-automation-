"""
publish_to_wordpress.py
Takes approved drafts from the DB and creates posts in WordPress via REST API.

Post status mapping:
  - pending_editor    → WordPress 'draft'    (awaiting editor review)
  - pending_publisher → WordPress 'pending'  (awaiting publisher approval)

Category routing for AI-generated content:
  - ALL AI articles are always filed under "Breaking News" (feeds the site ticker + homepage section)
  - A topic sub-category (Politics, Business, etc.) is added as a second category
  - "The Pulse" and "Viewpoint" are human-written sections — AI articles NEVER go there

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

# Primary category for ALL AI-generated content — feeds the site ticker and Breaking News homepage section
AI_PRIMARY_CATEGORY = "breaking news"

# Also tag every AI-generated post with these so the newsletter RSS picks it up
# and the owner can see at a glance which articles the workflow generated
NEWSLETTER_CATEGORY_ID = 74   # "Daily Newsletter Update" — feeds the MailerLite RSS campaign
AI_WORKFLOW_TAG_ID     = 86   # "AI Workflow" tag — distinguishes AI articles from human-written ones

# Map Claude's category labels to WordPress category names
# Claude outputs: Politics|Business|Sports|Technology|Culture|Diaspora|International|Nigeria
CATEGORY_MAP = {
    "politics":      "politics",
    "business":      "business",
    "sports":        "sports",
    "technology":    "tech-tainment",
    "culture":       "culture",
    "diaspora":      "diaspora news",
    "international": "international",
    "nigeria":       "politics",   # Nigerian-specific stories fall under politics
}

# AI content is NEVER allowed in these sections (human-written only)
BLOCKED_AI_CATEGORIES = {"the pulse", "pulse", "viewpoint", "opinion"}


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

_category_cache: dict[str, int] = {}
_slug_cache: dict[str, int] = {}


def _load_categories():
    global _category_cache, _slug_cache
    if _category_cache:
        return
    try:
        r = requests.get(WP_CATEGORIES_URL, params={"per_page": 100}, auth=AUTH, timeout=15)
        r.raise_for_status()
        for cat in r.json():
            _category_cache[cat["name"].lower()] = cat["id"]
            _slug_cache[cat["slug"].lower()] = cat["id"]
    except Exception as e:
        print(f"    WARN: Could not fetch WP categories: {e}")


def get_wp_category_id(name: str) -> int | None:
    """Return WordPress category ID by name or slug (case-insensitive)."""
    _load_categories()
    name_lower = name.lower()
    return _category_cache.get(name_lower) or _slug_cache.get(name_lower)


def resolve_ai_categories(claude_category: str) -> list[int]:
    """
    Return the list of WP category IDs for an AI-generated article.
    Always includes The Shoreline. Adds the topic sub-category if it exists.
    Never includes blocked sections.
    """
    _load_categories()
    ids = []

    shoreline_id = get_wp_category_id(AI_PRIMARY_CATEGORY)
    if shoreline_id:
        ids.append(shoreline_id)
    else:
        print(f"    WARN: '{AI_PRIMARY_CATEGORY}' category not found in WordPress — article will be uncategorised.")

    mapped = CATEGORY_MAP.get(claude_category.lower(), claude_category.lower())
    if mapped and mapped not in BLOCKED_AI_CATEGORIES:
        sub_id = get_wp_category_id(mapped)
        if sub_id and sub_id not in ids:
            ids.append(sub_id)

    return ids


# ---------------------------------------------------------------------------
# Post creation
# ---------------------------------------------------------------------------

def create_wp_post(draft: dict) -> int | None:
    """Create a WordPress post as 'draft'. Returns wp_post_id or None."""
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        print("    WARN: WordPress credentials not configured in .env — skipping.")
        return None

    category_ids = resolve_ai_categories(draft.get("category", ""))

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

    # Always include the newsletter category so articles appear in MailerLite's RSS feed
    if NEWSLETTER_CATEGORY_ID not in category_ids:
        category_ids.append(NEWSLETTER_CATEGORY_ID)

    if category_ids:
        payload["categories"] = category_ids
        cat_names = [k for k, v in {**_category_cache, **_slug_cache}.items() if v in category_ids]
        print(f"    Categories: {', '.join(cat_names)}")

    # Tag as AI Workflow so owner can distinguish from human-written articles
    payload["tags"] = [AI_WORKFLOW_TAG_ID]

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
