"""
process_link_requests.py
Reads URLs from the "Link Requests" tab in the editorial Google Sheet.
For each unprocessed row, fetches the article content, generates an
Atlantic Digest draft via Claude, publishes to WordPress as a draft,
and marks the row as Processed.

Sheet tab layout (Link Requests):
  Column A: URL          — client pastes article link here
  Column B: Notes        — optional context from client (e.g. "use this angle")
  Column C: Status       — blank = pending; script writes "Processed" or "Error"
  Column D: WP Post ID   — filled automatically after publishing
  Column E: Date Added   — optional, for client reference

Usage: python3 tools/process_link_requests.py
Requirements:
  GOOGLE_SHEET_ID, ANTHROPIC_API_KEY, WP_SITE_URL, WP_USERNAME,
  WP_APP_PASSWORD in .env; credentials.json in project root
"""

import os
import json
import sqlite3
import re
from datetime import datetime, timezone

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit(
        "Missing dependencies. Run:\n"
        "pip3 install requests beautifulsoup4"
    )

try:
    import anthropic
except ImportError:
    raise SystemExit("Missing dependency: pip3 install anthropic")

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    raise SystemExit(
        "Missing dependencies. Run:\n"
        "pip3 install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client"
    )

from dotenv import load_dotenv
load_dotenv()

DB_PATH    = os.path.join(os.path.dirname(__file__), "..", "articles.db")
CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "token.json")
SHEET_ID   = os.getenv("GOOGLE_SHEET_ID", "")
TAB_NAME   = "Link Requests"

WP_SITE_URL = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASS = os.getenv("WP_APP_PASSWORD", "")
WP_POSTS_URL      = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
WP_CATEGORIES_URL = f"{WP_SITE_URL}/wp-json/wp/v2/categories"
AUTH = (WP_USERNAME, WP_APP_PASS)

AI_CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL     = "claude-sonnet-4-6"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Minimum character count to consider fetched content usable
MIN_CONTENT_LENGTH = 200

SYSTEM_PROMPT = """You are a staff journalist writing for Atlantic Digest, a professional Nigerian news publication serving educated Nigerian and diaspora audiences globally.

VOICE & TONE:
- Professional, authoritative, analytical third person
- Measured — never sensational or speculative
- Balanced: report facts, acknowledge stakes and implications
- Not conversational; formal without being stiff

HEADLINES (12–18 words, Title Case):
- Specific: include names, numbers, dates, outcomes
- Structure: [WHO/WHAT] + [CONTEXT/CONSEQUENCE]
- Use quotes around directly cited provocative claims
- Never clickbait — headline must match article depth

STRUCTURE (Modified Inverted Pyramid):
- Open: Direct news lede with WHO, WHAT, WHEN, WHERE and specific detail
- Support immediately with quoted attribution or key facts
- Middle: Background, context, competing perspectives
- Close: Forward-looking implications or next steps
- Use subheadings only for complex analytical or tech pieces

LANGUAGE:
- British English spelling conventions (organisation, honour, colour, etc.)
- Nigerian currency: ₦ symbol
- Nigerian institutions named in full on first mention, then acronym
- Specific figures always: percentages, amounts, quantities, dates
- Never vague temporal language — always use actual dates

ATTRIBUTION:
- Name sources explicitly with title and affiliation
- Formal verbs: "noted," "explained," "characterised," "indicated," "said"
- Never anonymous sourcing

ARTICLE LENGTH BY CATEGORY:
- Breaking news / Politics: 300–500 words
- Business analysis: 400–600 words
- Sports: 250–350 words
- Tech/startup features: 400–650 words with subheadings
- Diaspora: 200–300 words

OUTPUT FORMAT (JSON only, no other text):
{
  "headline": "Title Case headline, 12–18 words",
  "body": "Full article body in HTML paragraphs (<p> tags). Use <h3> for subheadings if needed.",
  "category": "Politics|Business|Sports|Technology|Culture|Diaspora|International|Nigeria",
  "seo_description": "SEO meta description, max 150 characters",
  "image_keywords": ["keyword1", "keyword2", "keyword3"],
  "source_attribution": "Source(s): [Publication Name]"
}"""


# ---------------------------------------------------------------------------
# Google Sheets auth (reuses same token as export_to_sheets.py)
# ---------------------------------------------------------------------------

def get_sheets_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds).spreadsheets()


def ensure_tab_exists(service):
    """Create the Link Requests tab if it doesn't exist yet."""
    meta = service.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]
    if TAB_NAME in existing:
        return

    print(f"  Creating '{TAB_NAME}' tab ...")
    service.batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": TAB_NAME}}}]},
    ).execute()

    # Write header row
    service.values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_NAME}'!A1:E1",
        valueInputOption="RAW",
        body={"values": [["URL", "Notes (optional)", "Status", "WP Post ID", "Date Added"]]},
    ).execute()
    print(f"  '{TAB_NAME}' tab created with headers.")


def read_pending_rows(service) -> list[dict]:
    """Return rows where Status (col C) is blank or 'Pending'."""
    result = service.values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_NAME}'!A2:E",
    ).execute()
    rows = result.get("values", [])
    pending = []
    for i, row in enumerate(rows, start=2):  # row index 2 = first data row
        url    = row[0].strip() if len(row) > 0 else ""
        notes  = row[1].strip() if len(row) > 1 else ""
        status = row[2].strip() if len(row) > 2 else ""
        if url and status.lower() not in ("processed", "error", "skipped"):
            pending.append({"row": i, "url": url, "notes": notes})
    return pending


def update_row_status(service, row_index: int, status: str, wp_post_id: int | None = None):
    values = [[status, str(wp_post_id) if wp_post_id else ""]]
    service.values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_NAME}'!C{row_index}:D{row_index}",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()


# ---------------------------------------------------------------------------
# Article content fetcher
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Tags that almost always contain boilerplate rather than article body
NOISE_TAGS = {"script", "style", "nav", "header", "footer", "aside",
              "form", "button", "iframe", "noscript", "figure"}


def fetch_article_content(url: str) -> tuple[str, str]:
    """
    Fetch and parse article text from a URL.
    Returns (title, body_text). Raises on failure.
    """
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Remove noise elements in-place
    for tag in soup(list(NOISE_TAGS)):
        tag.decompose()

    # Title
    title = ""
    if soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)
    elif soup.title:
        title = soup.title.get_text(strip=True)

    # Body: prefer <article> tag, fall back to <main>, then <body>
    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        raise ValueError("Could not find article body in page.")

    # Collect paragraph text
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    body = "\n\n".join(p for p in paragraphs if len(p) > 40)

    if len(body) < MIN_CONTENT_LENGTH:
        # Fall back to all visible text in container
        body = re.sub(r"\s+", " ", container.get_text(" ", strip=True))

    return title, body


# ---------------------------------------------------------------------------
# Claude draft generation
# ---------------------------------------------------------------------------

def generate_draft_from_content(title: str, body: str, url: str,
                                  client_notes: str) -> dict:
    notes_block = f"\n\nClient notes: {client_notes}" if client_notes else ""
    user_prompt = (
        f"Rewrite the following article in Atlantic Digest's editorial voice.\n\n"
        f"Source URL: {url}\n"
        f"Original headline: {title}{notes_block}\n\n"
        f"{body[:4000]}"  # cap to avoid token overflow
    )

    response = AI_CLIENT.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = next(b for b in response.content if b.type == "text").text.strip()

    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Extract JSON if prose wraps it
    if not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

    return json.loads(raw)


# ---------------------------------------------------------------------------
# WordPress helpers (category routing identical to publish_to_wordpress.py)
# ---------------------------------------------------------------------------

_cat_cache: dict[str, int] = {}

CATEGORY_MAP = {
    "politics":      "politics",
    "business":      "business",
    "sports":        "sports",
    "technology":    "tech-tainment",
    "culture":       "culture",
    "diaspora":      "diaspora news",
    "international": "international",
    "nigeria":       "politics",
}
BLOCKED_AI_CATEGORIES = {"the pulse", "pulse", "viewpoint", "opinion"}


def _load_wp_categories():
    global _cat_cache
    if _cat_cache:
        return
    try:
        r = requests.get(WP_CATEGORIES_URL, params={"per_page": 100}, auth=AUTH, timeout=15)
        r.raise_for_status()
        for cat in r.json():
            _cat_cache[cat["name"].lower()] = cat["id"]
            _cat_cache[cat["slug"].lower()] = cat["id"]
    except Exception as e:
        print(f"    WARN: Could not fetch WP categories: {e}")


def resolve_categories(claude_category: str) -> list[int]:
    _load_wp_categories()
    ids = []
    shoreline_id = _cat_cache.get("the shoreline")
    if shoreline_id:
        ids.append(shoreline_id)
    mapped = CATEGORY_MAP.get(claude_category.lower(), claude_category.lower())
    if mapped and mapped not in BLOCKED_AI_CATEGORIES:
        sub_id = _cat_cache.get(mapped)
        if sub_id and sub_id not in ids:
            ids.append(sub_id)
    return ids


def create_wp_draft(draft: dict, wp_image_id: int | None = None) -> int | None:
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        print("    WARN: WordPress credentials not configured — skipping.")
        return None

    category_ids = resolve_categories(draft.get("category", ""))
    payload = {
        "title":   draft["headline"],
        "content": draft["body"],
        "status":  "draft",
        "excerpt": {"raw": draft.get("seo_description", "")},
    }
    if category_ids:
        payload["categories"] = category_ids
    if wp_image_id:
        payload["featured_media"] = wp_image_id

    try:
        r = requests.post(WP_POSTS_URL, auth=AUTH, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        print(f"    WordPress error: {e}")
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def save_draft_to_db(conn, url: str, draft: dict, wp_post_id: int | None) -> int:
    c = conn.cursor()
    cols = {row[1] for row in c.execute("PRAGMA table_info(drafts)")}
    if "image_keywords" not in cols:
        c.execute("ALTER TABLE drafts ADD COLUMN image_keywords TEXT")
    c.execute("""
        INSERT INTO drafts
            (raw_article_ids, headline, body, category, seo_description,
             image_keywords, wp_post_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_editor', ?)
    """, (
        json.dumps([]),
        draft["headline"],
        draft["body"],
        draft["category"],
        draft.get("seo_description", ""),
        json.dumps(draft.get("image_keywords", [])),
        wp_post_id,
        datetime.now(tz=timezone.utc).isoformat(),
    ))
    conn.commit()
    return c.lastrowid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_link_requests():
    print("Connecting to Google Sheets ...")
    service = get_sheets_service()
    ensure_tab_exists(service)

    pending = read_pending_rows(service)
    if not pending:
        print(f"No pending links in '{TAB_NAME}' tab.")
        return

    print(f"Found {len(pending)} pending link(s) to process.\n")
    conn = sqlite3.connect(DB_PATH)

    for item in pending:
        url    = item["url"]
        notes  = item["notes"]
        row_ix = item["row"]
        print(f"[Row {row_ix}] {url[:80]}")

        # 1. Fetch article content
        try:
            title, body = fetch_article_content(url)
            print(f"  Fetched: \"{title[:60]}\" ({len(body)} chars)")
        except Exception as e:
            print(f"  ERROR fetching URL: {e}")
            update_row_status(service, row_ix, "Error")
            continue

        # 2. Generate draft via Claude
        try:
            draft = generate_draft_from_content(title, body, url, notes)
            print(f"  Draft: \"{draft.get('headline', '')[:60]}\"")
        except Exception as e:
            print(f"  ERROR generating draft: {e}")
            update_row_status(service, row_ix, "Error")
            continue

        # 3. Push to WordPress as draft
        wp_post_id = create_wp_draft(draft)
        if wp_post_id:
            print(f"  ✓ WP draft created (id={wp_post_id})")
        else:
            print("  WARN: WordPress post not created — saved to DB only.")

        # 4. Save to DB
        draft_id = save_draft_to_db(conn, url, draft, wp_post_id)
        print(f"  ✓ Saved to DB (draft id={draft_id})")

        # 5. Mark row as processed
        update_row_status(service, row_ix, "Processed", wp_post_id)
        print(f"  ✓ Sheet row marked Processed\n")

    conn.close()
    print("Done. Run send_email.py to notify the editor.")


if __name__ == "__main__":
    process_link_requests()
