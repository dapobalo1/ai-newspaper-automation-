"""
fetch_image.py
For each draft in the DB with no image yet:
  1. Searches the WordPress Media Library for an existing relevant image (no upload needed)
  2. Falls back to Unsplash if the media library has nothing relevant
  3. Downloads and uploads the Unsplash image to WordPress
  4. Updates the draft record with wp_image_id

Usage: python3 tools/fetch_image.py
Requirements: WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD in .env
              UNSPLASH_ACCESS_KEY optional (fallback only)
"""

import os
import io
import json
import sqlite3

from datetime import datetime, timezone

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: pip3 install requests")

from dotenv import load_dotenv
load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "articles.db")

UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
WP_SITE_URL  = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USERNAME  = os.getenv("WP_USERNAME", "")
WP_APP_PASS  = os.getenv("WP_APP_PASSWORD", "")

UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"
WP_MEDIA_URL    = f"{WP_SITE_URL}/wp-json/wp/v2/media"
AUTH            = (WP_USERNAME, WP_APP_PASS)


# ---------------------------------------------------------------------------
# WordPress Media Library search (primary source)
# ---------------------------------------------------------------------------

def search_wp_media(keywords: list[str]) -> int | None:
    """
    Search the WordPress media library for an existing image matching the keywords.
    Returns wp_image_id if found, None otherwise.
    Tries each keyword in turn and returns the first match.
    """
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        return None

    for keyword in keywords[:3]:
        try:
            r = requests.get(
                WP_MEDIA_URL,
                auth=AUTH,
                params={"search": keyword, "per_page": 5, "media_type": "image"},
                timeout=10,
            )
            r.raise_for_status()
            results = r.json()
            if results:
                chosen = results[0]
                print(f"    WP media match: [{chosen['id']}] {chosen['title']['rendered'][:50]}")
                return chosen["id"]
        except Exception as e:
            print(f"    WP media search error: {e}")

    return None


# ---------------------------------------------------------------------------
# Unsplash (fallback)
# ---------------------------------------------------------------------------

def search_unsplash(keywords: list[str]) -> dict | None:
    """Return best matching photo dict from Unsplash or None."""
    if not UNSPLASH_KEY:
        print("    WARN: UNSPLASH_ACCESS_KEY not set — skipping image fetch.")
        return None

    query = " ".join(keywords[:3])
    params = {
        "query":       query,
        "per_page":    5,
        "orientation": "landscape",
        "content_filter": "high",
    }
    headers = {"Authorization": f"Client-ID {UNSPLASH_KEY}"}
    try:
        r = requests.get(UNSPLASH_SEARCH, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        # Pick the photo with the most downloads (most popular)
        return max(results, key=lambda p: p.get("downloads", 0))
    except Exception as e:
        print(f"    Unsplash error: {e}")
        return None


def download_image(photo: dict) -> tuple[bytes, str, str]:
    """Download photo bytes. Returns (bytes, filename, mime_type)."""
    url = photo["urls"]["regular"]  # 1080px wide
    # Trigger Unsplash download tracking (required by their guidelines)
    track_url = photo.get("links", {}).get("download_location")
    if track_url and UNSPLASH_KEY:
        try:
            requests.get(track_url, headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"}, timeout=5)
        except Exception:
            pass

    r = requests.get(url, timeout=30)
    r.raise_for_status()
    filename = f"ad_{photo['id']}.jpg"
    return r.content, filename, "image/jpeg"


# ---------------------------------------------------------------------------
# WordPress Media Upload
# ---------------------------------------------------------------------------

def upload_to_wordpress(image_bytes: bytes, filename: str, mime_type: str,
                         alt_text: str = "") -> int | None:
    """Upload image to WP media library. Returns wp_image_id or None."""
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        print("    WARN: WordPress credentials not set — skipping upload.")
        return None

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type":        mime_type,
    }
    try:
        r = requests.post(
            WP_MEDIA_URL,
            auth=AUTH,
            headers=headers,
            data=image_bytes,
            timeout=30,
        )
        r.raise_for_status()
        media = r.json()
        wp_id = media["id"]
        # Set alt text
        if alt_text:
            requests.post(
                f"{WP_MEDIA_URL}/{wp_id}",
                auth=AUTH,
                json={"alt_text": alt_text},
                timeout=10,
            )
        return wp_id
    except Exception as e:
        print(f"    WordPress upload error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_images_for_drafts():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT id, headline, category, seo_description, image_keywords, wp_post_id
        FROM drafts
        WHERE (wp_image_id IS NULL OR wp_image_id = 0)
        ORDER BY id DESC
    """)
    drafts = [dict(r) for r in c.fetchall()]

    if not drafts:
        print("No drafts need images.")
        conn.close()
        return

    print(f"Fetching images for {len(drafts)} draft(s) ...")

    for draft in drafts:
        print(f"\n  Draft {draft['id']}: \"{draft['headline'][:60]}\"")

        # Use Claude's image_keywords if available, otherwise fall back to headline words
        keywords = []
        raw_kw = draft.get("image_keywords")
        if raw_kw:
            try:
                keywords = json.loads(raw_kw)[:3]
            except Exception:
                keywords = []
        if not keywords:
            words = [w for w in draft["headline"].split() if len(w) > 4][:3]
            keywords = words + [draft.get("category", "")]
        print(f"    Keywords: {keywords}")

        # Step 1: Search existing WordPress media library
        wp_image_id = search_wp_media(keywords)

        if not wp_image_id:
            # Step 2: Fall back to Unsplash
            print("    No match in WP media library — trying Unsplash ...")
            photo = search_unsplash(keywords)
            if not photo:
                print("    No image found anywhere — skipping.")
                continue

            print(f"    Unsplash: {photo['urls']['regular'][:60]}...")
            image_bytes, filename, mime = download_image(photo)
            alt_text = f"{draft['headline']} — Photo credit: {photo['user']['name']} / Unsplash"
            wp_image_id = upload_to_wordpress(image_bytes, filename, mime, alt_text)
            if not wp_image_id:
                print("    Upload failed — skipping.")
                continue
            print(f"    ✓ Unsplash image uploaded to WP media (id={wp_image_id})")
        else:
            print(f"    ✓ Using existing WP media image (id={wp_image_id})")

        # Save to DB and attach to WP post
        c.execute("UPDATE drafts SET wp_image_id = ? WHERE id = ?", (wp_image_id, draft["id"]))
        conn.commit()

        wp_post_id = draft.get("wp_post_id")
        if wp_post_id and all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
            try:
                r = requests.patch(
                    f"{WP_SITE_URL}/wp-json/wp/v2/posts/{wp_post_id}",
                    auth=(WP_USERNAME, WP_APP_PASS),
                    json={"featured_media": wp_image_id},
                    timeout=15,
                )
                r.raise_for_status()
                print(f"    ✓ Featured image set on WP post {wp_post_id}")
            except Exception as e:
                print(f"    WARN: Could not set featured image on WP post: {e}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    fetch_images_for_drafts()
