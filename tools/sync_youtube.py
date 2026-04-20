"""
sync_youtube.py
Checks the client's YouTube channel for new videos published in the last 24 hours.
For each new video, creates a WordPress post embedding the video with a short intro.

Posts are created as 'draft' so the editor can review before publishing.

Usage: python3 tools/sync_youtube.py
Requirements:
  YOUTUBE_API_KEY      — Google Cloud Console → YouTube Data API v3
  YOUTUBE_CHANNEL_ID   — Channel ID from YouTube (starts with UC...)
  WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD in .env
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: pip3 install requests")

from dotenv import load_dotenv
load_dotenv()

DB_PATH         = os.path.join(os.path.dirname(__file__), "..", "articles.db")
YT_API_KEY      = os.getenv("YOUTUBE_API_KEY", "")
YT_CHANNEL_ID   = os.getenv("YOUTUBE_CHANNEL_ID", "")
WP_SITE_URL     = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USERNAME     = os.getenv("WP_USERNAME", "")
WP_APP_PASS     = os.getenv("WP_APP_PASSWORD", "")

WP_POSTS_URL    = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
WP_CATEGORIES_URL = f"{WP_SITE_URL}/wp-json/wp/v2/categories"
YT_SEARCH_URL   = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEO_URL    = "https://www.googleapis.com/youtube/v3/videos"

AUTH = (WP_USERNAME, WP_APP_PASS)


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def fetch_new_videos(hours: int = 24) -> list[dict]:
    """Return videos published on the channel in the last `hours` hours."""
    if not YT_API_KEY or not YT_CHANNEL_ID:
        print("WARN: YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID not set in .env — skipping.")
        return []

    published_after = (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()

    params = {
        "part":           "id,snippet",
        "channelId":      YT_CHANNEL_ID,
        "type":           "video",
        "order":          "date",
        "publishedAfter": published_after,
        "maxResults":     10,
        "key":            YT_API_KEY,
    }
    try:
        r = requests.get(YT_SEARCH_URL, params=params, timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        videos = []
        for item in items:
            vid_id = item["id"].get("videoId")
            if not vid_id:
                continue
            snippet = item["snippet"]
            videos.append({
                "video_id":    vid_id,
                "title":       snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
                "thumbnail":   snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                "embed_url":   f"https://www.youtube.com/embed/{vid_id}",
                "watch_url":   f"https://www.youtube.com/watch?v={vid_id}",
            })
        return videos
    except Exception as e:
        print(f"YouTube API error: {e}")
        return []


# ---------------------------------------------------------------------------
# Already-synced check (stored in raw_articles with source='youtube')
# ---------------------------------------------------------------------------

def is_already_synced(conn, video_id: str) -> bool:
    c = conn.cursor()
    c.execute("SELECT 1 FROM raw_articles WHERE url = ? LIMIT 1",
              (f"https://www.youtube.com/watch?v={video_id}",))
    return c.fetchone() is not None


def mark_synced(conn, video: dict):
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO raw_articles
            (url, url_hash, source, title, summary, published_at, fetched_at, category)
        VALUES (?, ?, 'youtube', ?, ?, ?, ?, 'Video')
    """, (
        video["watch_url"],
        video["video_id"],
        video["title"],
        video["description"][:500],
        video["published_at"],
        datetime.now(tz=timezone.utc).isoformat(),
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# WordPress post builder
# ---------------------------------------------------------------------------

def get_category_id(name: str) -> int | None:
    try:
        r = requests.get(WP_CATEGORIES_URL, params={"per_page": 100}, auth=AUTH, timeout=15)
        r.raise_for_status()
        for cat in r.json():
            if cat["name"].lower() == name.lower() or cat["slug"].lower() == name.lower():
                return cat["id"]
    except Exception:
        pass
    return None


def build_video_post_content(video: dict) -> str:
    description_html = ""
    if video["description"].strip():
        first_para = video["description"].split("\n")[0].strip()
        if first_para:
            description_html = f"<p>{first_para}</p>\n\n"

    return f"""{description_html}<figure class="wp-block-embed is-type-video">
<div class="wp-block-embed__wrapper">
<iframe width="560" height="315"
  src="{video['embed_url']}"
  title="{video['title']}"
  frameborder="0"
  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
  allowfullscreen></iframe>
</div>
</figure>

<p><a href="{video['watch_url']}" target="_blank" rel="noopener">Watch on YouTube →</a></p>"""


def create_video_post(video: dict) -> int | None:
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        print("    WARN: WordPress credentials not set — skipping.")
        return None

    content = build_video_post_content(video)
    category_id = get_category_id("the shoreline")

    payload = {
        "title":   video["title"],
        "content": content,
        "status":  "draft",
    }
    if category_id:
        payload["categories"] = [category_id]

    try:
        r = requests.post(WP_POSTS_URL, auth=AUTH, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        print(f"    WP post error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def sync_youtube():
    if not YT_API_KEY or not YT_CHANNEL_ID:
        print("YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID must be set in .env")
        print("Add them and re-run. See .env.example for details.")
        return

    print(f"Checking YouTube channel {YT_CHANNEL_ID} for new videos ...")
    videos = fetch_new_videos(hours=24)

    if not videos:
        print("No new videos in the last 24 hours.")
        return

    print(f"Found {len(videos)} new video(s).")
    conn = sqlite3.connect(DB_PATH)

    synced = 0
    for video in videos:
        if is_already_synced(conn, video["video_id"]):
            print(f"  Already synced: {video['title'][:60]}")
            continue

        print(f"\n  New video: \"{video['title'][:60]}\"")
        wp_id = create_video_post(video)
        if wp_id:
            mark_synced(conn, video)
            print(f"  ✓ WP draft created (id={wp_id}) → {WP_SITE_URL}/?p={wp_id}")
            synced += 1
        else:
            print("  ✗ Failed to create WP post.")

    conn.close()
    print(f"\nDone. {synced} new video post(s) created.")


if __name__ == "__main__":
    sync_youtube()
