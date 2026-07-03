"""
generate_whatsapp_digest.py
Fetches today's published articles from WordPress and writes a
WhatsApp-formatted digest to a dedicated Google Doc.

Run twice daily:
  Morning (Shoreline): python3 tools/generate_whatsapp_digest.py --section morning
  Evening (Viewpoint): python3 tools/generate_whatsapp_digest.py --section evening

The owner opens the Google Doc, selects all, copies, and pastes into WhatsApp.

Requirements: WHATSAPP_MORNING_DOC_ID, WHATSAPP_EVENING_DOC_ID, WP_SITE_URL,
              WP_USERNAME, WP_APP_PASSWORD in .env; credentials.json in project root
"""

import os
import re
import sys
import argparse
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: pip3 install requests")

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    raise SystemExit("Missing dependency: pip3 install google-auth google-auth-oauthlib google-api-python-client")

from dotenv import load_dotenv
load_dotenv()

WP_SITE_URL  = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USERNAME  = os.getenv("WP_USERNAME", "")
WP_APP_PASS  = os.getenv("WP_APP_PASSWORD", "")
AUTH         = (WP_USERNAME, WP_APP_PASS)

SHEET_ID   = os.getenv("GOOGLE_SHEET_ID", "")
CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "token.json")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Tab names in the Google Sheet
MORNING_TAB = "WhatsApp Morning Digest"
EVENING_TAB = "WhatsApp Evening Digest"

# WordPress category IDs
SHORELINE_CATEGORIES = [23]     # Breaking News
VIEWPOINT_CATEGORIES = [2, 76]  # View Point, The Pulse

ARTICLE_LIMIT = {"morning": 8, "evening": 5}


# ---------------------------------------------------------------------------
# Google Sheets auth
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


# ---------------------------------------------------------------------------
# WordPress article fetch
# ---------------------------------------------------------------------------

def fetch_articles(categories: list[int], limit: int) -> list[dict]:
    today_start = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT00:00:00")
    try:
        r = requests.get(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts",
            auth=AUTH,
            params={
                "categories": ",".join(str(c) for c in categories),
                "status":     "publish",
                "per_page":   limit,
                "orderby":    "date",
                "order":      "desc",
                "after":      today_start,
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"ERROR fetching articles from WordPress: {e}")
        return []


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------

def clean_html(text: str) -> str:
    """Strip HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", "", text)
    replacements = {
        "&#8211;": "–", "&#8212;": "—", "&#8217;": "'", "&#8216;": "'",
        "&#8220;": "“", "&#8221;": "”", "&amp;": "&",
        "&nbsp;": " ", "&#8230;": "...", "&lt;": "<", "&gt;": ">",
    }
    for entity, char in replacements.items():
        text = text.replace(entity, char)
    return text.strip()


def truncate(text: str, max_chars: int = 160) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."


def build_whatsapp_text(section: str, articles: list[dict]) -> str:
    date_str = datetime.now().strftime("%A, %d %B %Y")
    divider  = "─" * 28

    if section == "morning":
        header = (
            f"*ATLANTIC DIGEST* 🗞\n"
            f"_Navigating the Complexities of Our World_\n"
            f"📅 {date_str}\n\n"
            f"*TODAY'S TOP STORIES*\n"
            f"{divider}\n\n"
        )
    else:
        header = (
            f"*ATLANTIC DIGEST* 🗞\n"
            f"_Navigating the Complexities of Our World_\n"
            f"📅 {date_str}\n\n"
            f"*VIEWPOINT & OPINION*\n"
            f"{divider}\n\n"
        )

    body = ""
    for article in articles:
        title   = clean_html(article["title"]["rendered"])
        excerpt = clean_html(article.get("excerpt", {}).get("rendered", ""))
        excerpt = truncate(excerpt)
        link    = article["link"]

        body += f"📌 *{title}*\n{excerpt}\n🔗 {link}\n\n"

    footer = f"{divider}\nFor more news visit *www.atlanticdigest.com*"

    return header + body + footer


# ---------------------------------------------------------------------------
# Google Sheets write
# ---------------------------------------------------------------------------

def ensure_tab(service, tab_name: str):
    """Create the tab if it doesn't already exist."""
    meta     = service.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]
    if tab_name in existing:
        return
    service.batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    print(f"  Created '{tab_name}' tab.")


def write_to_sheet(service, tab_name: str, content: str):
    """Write the WhatsApp digest text into cell A1 of the tab."""
    service.values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": [[content]]},
    ).execute()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate WhatsApp digest in Google Doc")
    parser.add_argument(
        "--section",
        choices=["morning", "evening"],
        default="morning",
        help="morning = Shoreline/Breaking News | evening = Viewpoint/Pulse",
    )
    args = parser.parse_args()
    section = args.section

    tab_name   = MORNING_TAB if section == "morning" else EVENING_TAB
    categories = SHORELINE_CATEGORIES if section == "morning" else VIEWPOINT_CATEGORIES
    limit      = ARTICLE_LIMIT[section]

    print(f"Generating {section} WhatsApp digest ...")
    articles = fetch_articles(categories, limit)

    if not articles:
        print("No published articles found for today.")
        print("Make sure articles are published before running this script.")
        sys.exit(0)

    print(f"Found {len(articles)} article(s).")
    content = build_whatsapp_text(section, articles)

    print("Connecting to Google Sheets ...")
    service = get_sheets_service()
    ensure_tab(service, tab_name)
    write_to_sheet(service, tab_name, content)

    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    print(f"\n✓ WhatsApp digest ready in '{tab_name}' tab: {sheet_url}")
    print("\n--- Preview ---")
    print(content[:600] + ("\n..." if len(content) > 600 else ""))


if __name__ == "__main__":
    main()
