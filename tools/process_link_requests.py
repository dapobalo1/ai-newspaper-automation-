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
TAB_NAME         = "Link Requests"
SOURCES_GUIDE_TAB = "Sources Guide"

WP_SITE_URL = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USERNAME = os.getenv("WP_USERNAME", "")
WP_APP_PASS = os.getenv("WP_APP_PASSWORD", "")
WP_POSTS_URL      = f"{WP_SITE_URL}/wp-json/wp/v2/posts"
WP_CATEGORIES_URL = f"{WP_SITE_URL}/wp-json/wp/v2/categories"
AUTH = (WP_USERNAME, WP_APP_PASS)

AI_CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL     = "claude-sonnet-4-6"

SMTP_HOST    = os.getenv("SMTP_HOST", "")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER", "")
SMTP_PASS    = os.getenv("SMTP_PASSWORD", "")
EDITOR_EMAIL = os.getenv("EDITOR_EMAIL", "")

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
- Name sources explicitly with title and affiliation (people and officials, not publications)
- Formal verbs: "noted," "explained," "characterised," "indicated," "said"
- Never anonymous sourcing
- NEVER mention other news outlets or publications by name in the article body (e.g., do not write "according to Reuters," "as reported by Vanguard," "Premium Times said," etc.). Atlantic Digest is the publisher — write accordingly. Source credit belongs only in the source_attribution JSON field, not in the body.

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
}

CRITICAL JSON RULES — you must follow these exactly or the output will be rejected:
- Use &quot; instead of " for any quoted speech within the body field (e.g. He said &quot;yes&quot; not He said "yes")
- Never include raw double-quote characters inside any string value
- Never include literal newline characters inside string values — all text must be on a single line within each JSON field"""


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
        # Tab exists — patch F1 header if it was created before this update
        result = service.values().get(
            spreadsheetId=SHEET_ID,
            range=f"'{TAB_NAME}'!F1",
        ).execute()
        if not result.get("values"):
            service.values().update(
                spreadsheetId=SHEET_ID,
                range=f"'{TAB_NAME}'!F1",
                valueInputOption="RAW",
                body={"values": [["Category (optional)"]]},
            ).execute()
            print("  Added 'Category (optional)' header to F1.")
        return

    print(f"  Creating '{TAB_NAME}' tab ...")
    service.batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": TAB_NAME}}}]},
    ).execute()

    # Write header row
    service.values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_NAME}'!A1:F1",
        valueInputOption="RAW",
        body={"values": [["URL", "Notes (optional)", "Status", "WP Post ID", "Date Added", "Category (optional)"]]},
    ).execute()
    print(f"  '{TAB_NAME}' tab created with headers.")


def ensure_sources_guide_tab(service):
    """Create or refresh the Sources Guide tab so the client always has a reference."""
    meta = service.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]

    if SOURCES_GUIDE_TAB not in existing:
        service.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SOURCES_GUIDE_TAB}}}]},
        ).execute()
        print(f"  Created '{SOURCES_GUIDE_TAB}' tab.")

    rows = [
        ["Atlantic Digest — News Sources Reference Guide"],
        ["Last updated automatically by the AI workflow."],
        [""],
        ["✅  RECOMMENDED SOURCES — These work reliably"],
        ["Publication",                  "Domain",                   "Best for"],
        ["Punch Nigeria",                "punchng.com",              "Nigerian politics, sports, breaking news"],
        ["Daily Trust",                  "dailytrust.com",           "Nigerian & northern regional news"],
        ["Premium Times",                "premiumtimesng.com",       "Investigative journalism, Nigerian politics"],
        ["Guardian Nigeria",             "guardian.ng",              "Nigerian news, business, opinion"],
        ["ThisDay Live",                 "thisdaylive.com",          "Nigerian business, politics"],
        ["The Nation Nigeria",           "thenationonline.net",      "Nigerian general news"],
        ["Vanguard Nigeria",             "vanguardngr.com",          "Nigerian news (may occasionally block bots — retry if failed)"],
        ["BBC News",                     "bbc.com/news",             "International news"],
        ["AP News",                      "apnews.com",               "International wire service — reliable"],
        ["Al Jazeera",                   "aljazeera.com",            "Middle East, Africa, global news"],
        ["CNN",                          "cnn.com",                  "International breaking news"],
        ["The Guardian UK",              "theguardian.com",          "International news & analysis"],
        [""],
        ["❌  DO NOT USE — These are paywalled and will always fail"],
        ["Publication",                  "Why it fails"],
        ["Washington Post",              "Hard paywall — blocks all automated access"],
        ["Bloomberg",                    "Hard paywall — blocks all automated access"],
        ["Reuters",                      "Paywall — blocks all automated access"],
        ["Financial Times (FT)",         "Hard paywall — blocks all automated access"],
        ["New York Times",               "Hard paywall — blocks all automated access"],
        ["Wall Street Journal",          "Hard paywall — blocks all automated access"],
        ["The Economist",                "Hard paywall — blocks all automated access"],
        [""],
        ["📋  HOW TO SUBMIT A LINK"],
        ["Step", "What to do"],
        ["1", "Go to the 'Link Requests' tab"],
        ["2", "Paste the article URL in Column A (one URL per row)"],
        ["3", "Add optional context in Column B (e.g. 'focus on the economic angle')"],
        ["4", "Optionally pick a category in Column F: Politics / Business / Sports / Technology / Culture / Diaspora / International"],
        ["5", "Leave Column C (Status) blank — the system fills it automatically within 30 minutes"],
        ["6", "Once processed, the draft appears in WordPress for editorial review"],
        [""],
        ["⚠️  TIPS"],
        ["- Twitter/X links (t.co or x.com) are supported — the system extracts the article"],
        ["- If a link fails, check Column C in Link Requests for the reason"],
        ["- If Status shows 'Skipped — paywalled', replace with a link from the ✅ list above"],
        ["- You will receive an email notification if any of your links cannot be processed"],
    ]

    service.values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{SOURCES_GUIDE_TAB}'!A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def read_pending_rows(service) -> list[dict]:
    """Return rows where Status (col C) is blank or 'Pending'."""
    result = service.values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{TAB_NAME}'!A2:F",
    ).execute()
    rows = result.get("values", [])
    pending = []
    for i, row in enumerate(rows, start=2):  # row index 2 = first data row
        url               = row[0].strip() if len(row) > 0 else ""
        notes             = row[1].strip() if len(row) > 1 else ""
        status            = row[2].strip() if len(row) > 2 else ""
        category_override = row[5].strip() if len(row) > 5 else ""
        if url and status.lower() not in ("processed", "error", "skipped"):
            pending.append({"row": i, "url": url, "notes": notes, "category_override": category_override})
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
# URL type detection
# ---------------------------------------------------------------------------

TWITTER_DOMAINS = {"twitter.com", "x.com"}
SHORTENER_DOMAINS = {"t.co"}
BLOCKED_DOMAINS = {"instagram.com", "facebook.com", "fb.com", "tiktok.com",
                   "linkedin.com", "threads.net", "snapchat.com"}

# Sites with hard paywalls or aggressive anti-scraping — cannot be scraped
PAYWALL_DOMAINS = {
    "washingtonpost.com": "Washington Post",
    "bloomberg.com":      "Bloomberg",
    "reuters.com":        "Reuters",
    "ft.com":             "Financial Times",
    "nytimes.com":        "New York Times",
    "economist.com":      "The Economist",
    "wsj.com":            "Wall Street Journal",
    "thetimes.co.uk":     "The Times",
}

OEMBED_URL = "https://publish.twitter.com/oembed"


def resolve_url(url: str) -> str:
    """Follow redirects and return the final URL (handles t.co and other shorteners)."""
    import subprocess
    from urllib.parse import urlparse
    netloc = urlparse(url).netloc.lower()
    domain = netloc[4:] if netloc.startswith("www.") else netloc
    if domain in SHORTENER_DOMAINS:
        try:
            result = subprocess.run(
                ["curl", "-sI", "-L", "--max-redirs", "5", "-o", "/dev/null",
                 "-w", "%{url_effective}", url],
                capture_output=True, text=True, timeout=20
            )
            resolved = result.stdout.strip()
            if resolved and resolved != url:
                print(f"  Resolved {url} → {resolved}")
                return resolved
        except Exception as e:
            print(f"  WARN: Could not resolve short URL {url}: {e}")
    return url


def detect_url_type(url: str) -> tuple[str, str]:
    """
    Return (type, label) where type is one of:
    'twitter', 'blocked_social', 'paywall', 'article'
    """
    from urllib.parse import urlparse
    netloc = urlparse(url).netloc.lower()
    domain = netloc[4:] if netloc.startswith("www.") else netloc
    if domain in TWITTER_DOMAINS:
        return "twitter", ""
    if domain in BLOCKED_DOMAINS:
        return "blocked_social", ""
    for paywall_domain, label in PAYWALL_DOMAINS.items():
        if domain == paywall_domain or domain.endswith("." + paywall_domain):
            return "paywall", label
    return "article", ""


# ---------------------------------------------------------------------------
# Twitter/X fetcher (uses public oEmbed — no API key required)
# ---------------------------------------------------------------------------

def fetch_tweet_content(url: str) -> tuple[str, str]:
    """
    Fetch tweet text via Twitter's public oEmbed endpoint.
    Returns (author_line, tweet_text). Raises on failure.
    """
    r = requests.get(OEMBED_URL, params={"url": url, "omit_script": True}, timeout=15)
    r.raise_for_status()
    data = r.json()

    # oEmbed html looks like: <blockquote ...><p>tweet text</p>&mdash; Name (@handle)...
    html = data.get("html", "")
    soup = BeautifulSoup(html, "html.parser")

    tweet_text = ""
    p_tag = soup.find("p")
    if p_tag:
        tweet_text = p_tag.get_text(" ", strip=True)

    author = data.get("author_name", "")
    author_line = f"{author} (@{data.get('author_url', '').rstrip('/').split('/')[-1]})" if author else ""

    if not tweet_text:
        raise ValueError("Could not extract tweet text from oEmbed response.")

    return author_line, tweet_text


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
    Retries once on timeout.
    """
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            break
        except requests.exceptions.Timeout:
            if attempt == 0:
                print("  Timeout — retrying once ...")
                continue
            raise
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 401):
                raise ValueError(
                    f"Access denied (HTTP {e.response.status_code}). "
                    "This site blocks automated access. Try a different source URL."
                ) from e
            raise
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
                                  client_notes: str,
                                  is_tweet: bool = False,
                                  category_hint: str | None = None) -> dict:
    notes_block    = f"\n\nClient notes: {client_notes}" if client_notes else ""
    category_block = (
        f"\n\nCategory override: Write this article as a '{category_hint.title()}' piece. "
        "Follow the word count and structural guidelines for that category exactly."
        if category_hint else ""
    )

    if is_tweet:
        user_prompt = (
            f"A tweet from {title} has been shared as a news item. "
            f"Write a short Atlantic Digest news article reporting what was said. "
            f"Attribute the statement clearly to the author. "
            f"Source: {url}{notes_block}{category_block}\n\n"
            f"Tweet text: {body}"
        )
    else:
        user_prompt = (
            f"Rewrite the following article in Atlantic Digest's editorial voice.\n\n"
            f"Source URL: {url}\n"
            f"Original headline: {title}{notes_block}{category_block}\n\n"
            f"{body[:4000]}"
        )

    response = AI_CLIENT.messages.create(
        model=MODEL,
        max_tokens=2500,
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

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Escape newlines that appear inside JSON string values only
        fixed = []
        in_string = False
        escaped = False
        for ch in raw:
            if escaped:
                fixed.append(ch)
                escaped = False
            elif ch == "\\":
                fixed.append(ch)
                escaped = True
            elif ch == '"':
                fixed.append(ch)
                in_string = not in_string
            elif ch == "\n" and in_string:
                fixed.append("\\n")
            else:
                fixed.append(ch)
        result = json.loads("".join(fixed))
    if category_hint:
        result["category"] = category_hint.title()
    return result


# ---------------------------------------------------------------------------
# WordPress helpers (category routing identical to publish_to_wordpress.py)
# ---------------------------------------------------------------------------

_cat_cache: dict[str, int] = {}

NEWSLETTER_CATEGORY_ID   = 74  # "Daily Newsletter Update" — feeds MailerLite RSS
CLIENT_SUBMITTED_TAG_ID  = 87  # "Client Submitted" tag — owner can see this came via Google Sheets
AI_WORKFLOW_TAG_ID       = 86  # "AI Workflow" tag

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

VALID_OVERRIDE_CATEGORIES = {
    "politics", "business", "sports", "technology",
    "culture", "diaspora", "international", "nigeria",
}
BLOCKED_OVERRIDE_CATEGORIES = {"the pulse", "pulse", "viewpoint", "opinion"}


def validate_category_override(raw: str) -> tuple[str | None, str | None]:
    """
    Validate a category string typed by the client in column F.
    Returns (normalised_key, None) on success, (None, error_string) on failure,
    or (None, None) when the cell is blank (caller falls back to Claude).
    """
    normalised = raw.strip().lower()
    if not normalised or normalised in ("shoreline", "the shoreline", "breaking news", "breaking"):
        return None, None
    if normalised in BLOCKED_OVERRIDE_CATEGORIES:
        return None, (
            f"Error — '{raw.strip()}' is a human-written section; AI articles cannot be "
            "filed there. Leave blank to let Claude choose, or use: "
            "Politics, Business, Sports, Technology, Culture, Diaspora, International"
        )
    if normalised not in VALID_OVERRIDE_CATEGORIES:
        valid_list = ", ".join(sorted(v.title() for v in VALID_OVERRIDE_CATEGORIES))
        return None, (
            f"Error — unknown category '{raw.strip()}'. "
            f"Valid options: {valid_list}. "
            "Leave column F blank to let Claude choose automatically."
        )
    return normalised, None


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
    shoreline_id = _cat_cache.get("breaking news")
    if shoreline_id:
        ids.append(shoreline_id)
    mapped = CATEGORY_MAP.get(claude_category.lower(), claude_category.lower())
    if mapped and mapped not in BLOCKED_AI_CATEGORIES:
        sub_id = _cat_cache.get(mapped)
        if sub_id and sub_id not in ids:
            ids.append(sub_id)
    return ids


def fetch_image_for_article(keywords: list[str]) -> int | None:
    """Search WP media library for a relevant image. Falls back to Unsplash."""
    if not all([WP_SITE_URL, WP_USERNAME, WP_APP_PASS]):
        return None

    # 1. Search existing WP media library
    for keyword in keywords[:3]:
        try:
            r = requests.get(
                f"{WP_SITE_URL}/wp-json/wp/v2/media",
                auth=AUTH,
                params={"search": keyword, "per_page": 5, "media_type": "image"},
                timeout=10,
            )
            r.raise_for_status()
            results = r.json()
            if results:
                img_id = results[0]["id"]
                print(f"    Image: WP media match [{img_id}] for '{keyword}'")
                return img_id
        except Exception:
            pass

    # 2. Fall back to Unsplash
    unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
    if not unsplash_key:
        return None
    try:
        query = " ".join(keywords[:3])
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 5, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {unsplash_key}"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None

        photo = max(results, key=lambda p: p.get("downloads", 0))
        img_bytes = requests.get(photo["urls"]["regular"], timeout=30).content
        filename  = f"ad_{photo['id']}.jpg"

        upload = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/media",
            auth=AUTH,
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Content-Type": "image/jpeg"},
            data=img_bytes,
            timeout=30,
        )
        upload.raise_for_status()
        img_id = upload.json()["id"]
        print(f"    Image: Unsplash upload [{img_id}]")
        return img_id
    except Exception as e:
        print(f"    Image: could not fetch from Unsplash: {e}")
        return None


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
    # Add newsletter category so article appears in MailerLite RSS feed
    if NEWSLETTER_CATEGORY_ID not in category_ids:
        category_ids.append(NEWSLETTER_CATEGORY_ID)

    if category_ids:
        payload["categories"] = category_ids

    # Tag as Client Submitted so owner knows this came via the Google Sheets Link Requests tab
    payload["tags"] = [CLIENT_SUBMITTED_TAG_ID]

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
# Skip notification email
# ---------------------------------------------------------------------------

def send_skip_notification(skipped: list[dict]):
    """Email the editor when URLs could not be processed, with clear instructions."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from datetime import datetime

    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, EDITOR_EMAIL]):
        print("  WARN: SMTP not configured — skip notification not sent.")
        return

    date_str  = datetime.now().strftime("%A, %d %B %Y")
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    n         = len(skipped)

    cards = ""
    for item in skipped:
        cards += f"""
        <div style="border:1px solid #fde0e0;border-left:4px solid #e74c3c;border-radius:8px;
                    padding:16px 20px;margin-bottom:12px;background:#fff;">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;color:#e74c3c;
                    text-transform:uppercase;letter-spacing:0.8px;">Cannot Process</p>
          <p style="margin:0 0 10px;font-size:15px;color:#1a1a1a;line-height:1.5;">{item['reason']}</p>
          <p style="margin:0;font-size:12px;color:#888;word-break:break-all;
                    background:#f8f8f8;padding:8px 10px;border-radius:4px;
                    font-family:'Courier New',monospace;">{item['url']}</p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;border-radius:12px;overflow:hidden;
              box-shadow:0 4px 24px rgba(0,0,0,0.08);">

  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);padding:36px 40px;">
    <p style="margin:0 0 8px;font-size:11px;font-weight:700;color:#e74c3c;
              text-transform:uppercase;letter-spacing:1.5px;">Atlantic Digest · AI Workflow Alert</p>
    <h1 style="margin:0 0 10px;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
      {n} Link{'s Need' if n>1 else ' Needs'} Replacing
    </h1>
    <p style="margin:0;font-size:14px;color:#8892b0;">{date_str}</p>
  </td></tr>

  <!-- Body -->
  <tr><td style="background:#ffffff;padding:36px 40px;">

    <p style="margin:0 0 24px;font-size:16px;color:#444;line-height:1.75;">
      The following {'links were' if n>1 else 'link was'} submitted to the
      <strong style="color:#1a1a2e;">Link Requests</strong> sheet but
      {'could not be processed — the sources are' if n>1 else 'could not be processed — the source is'}
      paywalled or block automated access.
    </p>

    {cards}

    <!-- Recommended sources -->
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;
                padding:24px 28px;margin:28px 0 16px;">
      <p style="margin:0 0 16px;font-size:12px;font-weight:700;color:#15803d;
                text-transform:uppercase;letter-spacing:0.8px;">
        ✅ &nbsp;Use These Sources Instead
      </p>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:5px 8px 5px 0;width:50%;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">Punch Nigeria</span>
            <span style="font-size:12px;color:#6b7280;display:block;">punchng.com</span>
          </td>
          <td style="padding:5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">BBC News</span>
            <span style="font-size:12px;color:#6b7280;display:block;">bbc.com/news</span>
          </td>
        </tr>
        <tr>
          <td style="padding:5px 8px 5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">Daily Trust</span>
            <span style="font-size:12px;color:#6b7280;display:block;">dailytrust.com</span>
          </td>
          <td style="padding:5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">AP News</span>
            <span style="font-size:12px;color:#6b7280;display:block;">apnews.com</span>
          </td>
        </tr>
        <tr>
          <td style="padding:5px 8px 5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">Premium Times</span>
            <span style="font-size:12px;color:#6b7280;display:block;">premiumtimesng.com</span>
          </td>
          <td style="padding:5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">Al Jazeera</span>
            <span style="font-size:12px;color:#6b7280;display:block;">aljazeera.com</span>
          </td>
        </tr>
        <tr>
          <td style="padding:5px 8px 5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">Guardian Nigeria</span>
            <span style="font-size:12px;color:#6b7280;display:block;">guardian.ng</span>
          </td>
          <td style="padding:5px 0;vertical-align:top;">
            <span style="font-size:14px;color:#1a1a2e;font-weight:600;">The Guardian UK</span>
            <span style="font-size:12px;color:#6b7280;display:block;">theguardian.com</span>
          </td>
        </tr>
      </table>
    </div>

    <!-- Blocked -->
    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;
                padding:16px 28px;margin-bottom:32px;">
      <p style="margin:0 0 8px;font-size:12px;font-weight:700;color:#dc2626;
                text-transform:uppercase;letter-spacing:0.8px;">❌ &nbsp;These Will Never Work</p>
      <p style="margin:0;font-size:14px;color:#666;line-height:1.8;">
        Washington Post &nbsp;·&nbsp; Bloomberg &nbsp;·&nbsp; Reuters &nbsp;·&nbsp;
        Financial Times &nbsp;·&nbsp; New York Times &nbsp;·&nbsp; Wall Street Journal
      </p>
    </div>

    <!-- CTA -->
    <div style="text-align:center;">
      <a href="{sheet_url}"
         style="display:inline-block;background:#1a1a2e;color:#ffffff;
                font-size:15px;font-weight:600;padding:15px 36px;border-radius:8px;
                text-decoration:none;letter-spacing:0.4px;">
        Open Link Requests Sheet &rarr;
      </a>
    </div>

  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#f8fafc;padding:24px 40px;text-align:center;
                 border-top:1px solid #e8ecf0;">
    <p style="margin:0 0 4px;font-size:12px;color:#94a3b8;line-height:1.7;">
      See the <strong style="color:#64748b;">Sources Guide</strong> tab in the sheet for the full reference list.
    </p>
    <p style="margin:0;font-size:12px;color:#cbd5e1;">
      Sent automatically &nbsp;·&nbsp; Atlantic Digest AI Workflow
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Atlantic Digest] {n} Link{'s Need' if n>1 else ' Needs'} Replacing — Unsupported Sources"
    msg["From"]    = f"Atlantic Digest Workflow <{SMTP_USER}>"
    msg["To"]      = EDITOR_EMAIL
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, EDITOR_EMAIL, msg.as_string())
        print(f"  ✓ Skip notification sent to {EDITOR_EMAIL}")
    except Exception as e:
        print(f"  WARN: Could not send skip notification: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_link_requests():
    print("Connecting to Google Sheets ...")
    service = get_sheets_service()
    ensure_tab_exists(service)
    ensure_sources_guide_tab(service)

    pending = read_pending_rows(service)
    if not pending:
        print(f"No pending links in '{TAB_NAME}' tab.")
        return

    print(f"Found {len(pending)} pending link(s) to process.\n")
    conn = sqlite3.connect(DB_PATH)
    skipped_items: list[dict] = []

    for item in pending:
        url    = item["url"]
        notes  = item["notes"]
        row_ix = item["row"]
        print(f"[Row {row_ix}] {url[:80]}")

        # 1. Validate category override BEFORE any network calls
        category_override  = item.get("category_override", "")
        validated_category, cat_error = validate_category_override(category_override)
        if cat_error:
            print(f"  INVALID CATEGORY: {cat_error}")
            update_row_status(service, row_ix, cat_error)
            continue

        # 2. Resolve short URLs (t.co etc.) then detect type
        url = resolve_url(url)
        url_type, site_label = detect_url_type(url)

        if url_type == "blocked_social":
            print(f"  SKIPPED: Instagram/Facebook/TikTok URLs cannot be scraped.")
            update_row_status(service, row_ix, "Skipped — paste original news article URL")
            skipped_items.append({"url": url, "reason": "Social media link (Instagram/Facebook/TikTok) — cannot be scraped. Paste the original article URL."})
            continue

        if url_type == "paywall":
            print(f"  SKIPPED: {site_label} is paywalled — cannot scrape content.")
            update_row_status(service, row_ix,
                f"Skipped — {site_label} is paywalled. Use a non-paywalled source instead "
                "(e.g. Punch, Daily Trust, Premium Times, Guardian Nigeria, BBC, AP, Al Jazeera).")
            skipped_items.append({"url": url, "reason": f"{site_label} is paywalled — replace with a link from a supported source."})
            continue

        is_tweet = url_type == "twitter"

        try:
            if is_tweet:
                title, body = fetch_tweet_content(url)
                print(f"  Tweet by {title}: \"{body[:80]}...\"")
            else:
                title, body = fetch_article_content(url)
                print(f"  Fetched: \"{title[:60]}\" ({len(body)} chars)")
        except Exception as e:
            msg = str(e)
            print(f"  ERROR fetching URL: {msg}")
            if "blocks automated access" in msg:
                status_msg = ("Skipped — this site blocks automated access. "
                    "Use Punch, Daily Trust, Premium Times, Guardian Nigeria, BBC, AP, or Al Jazeera instead.")
                update_row_status(service, row_ix, status_msg)
                skipped_items.append({"url": url, "reason": "Site blocks automated access — replace with a link from a supported source."})
            else:
                update_row_status(service, row_ix, f"Error — {msg[:120]}")
            continue

        # 3. Generate draft via Claude
        try:
            draft = generate_draft_from_content(
                title, body, url, notes,
                is_tweet=is_tweet,
                category_hint=validated_category,
            )
            cat_label = validated_category.title() if validated_category else draft.get("category", "unknown")
            print(f"  Draft: \"{draft.get('headline', '')[:60]}\" [{cat_label}]")
        except Exception as e:
            print(f"  ERROR generating draft: {e}")
            update_row_status(service, row_ix, "Error")
            continue

        # 4. Fetch image from WP media library or Unsplash
        keywords = draft.get("image_keywords", [])
        if not keywords:
            keywords = [w for w in draft.get("headline", "").split() if len(w) > 4][:3]
        wp_image_id = fetch_image_for_article(keywords)

        # 5. Push to WordPress as draft
        wp_post_id = create_wp_draft(draft, wp_image_id=wp_image_id)
        if wp_post_id:
            print(f"  ✓ WP draft created (id={wp_post_id})")
        else:
            print("  WARN: WordPress post not created — saved to DB only.")

        # 6. Save to DB
        draft_id = save_draft_to_db(conn, url, draft, wp_post_id)
        print(f"  ✓ Saved to DB (draft id={draft_id})")

        # 7. Mark row as processed
        update_row_status(service, row_ix, "Processed", wp_post_id)
        print(f"  ✓ Sheet row marked Processed\n")

    conn.close()

    # 7. Notify editor of any skipped links
    if skipped_items:
        print(f"\n{len(skipped_items)} link(s) skipped — sending notification ...")
        send_skip_notification(skipped_items)

    print("Done.")


if __name__ == "__main__":
    process_link_requests()
