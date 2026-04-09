"""
export_to_sheets.py
Exports pending drafts from the DB into a Google Sheet for editorial review.

Each draft becomes one row. Editors review the article and type one of:
  APPROVE  → will be published to WordPress
  REJECT   → will be discarded
  EDIT     → needs revision (add notes in Editor Notes column)
  (blank)  → pending review (default)

Usage: python3 tools/export_to_sheets.py
Requirements:
  - GOOGLE_SHEET_ID in .env
  - credentials.json in project root (Google OAuth client secret)
  - Run once: python3 tools/export_to_sheets.py  (will open browser for auth on first run)
"""

import os
import sqlite3
import json
from datetime import datetime

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

DB_PATH      = os.path.join(os.path.dirname(__file__), "..", "articles.db")
CREDS_PATH   = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
TOKEN_PATH   = os.path.join(os.path.dirname(__file__), "..", "token.json")
SHEET_ID     = os.getenv("GOOGLE_SHEET_ID", "")
SHEET_NAME   = "Drafts"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column layout (1-indexed for Sheets API)
COLUMNS = [
    "Draft ID",
    "Date Generated",
    "Category",
    "Headline",
    "SEO Description",
    "Body",
    "Word Count",
    "Sources",
    "Source URLs",
    "Approval",       # ← Editor fills this: APPROVE / REJECT / EDIT
    "Editor Notes",   # ← Editor adds comments here
    "WP Post ID",     # ← Filled automatically after publishing
]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_sheets_service():
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_PATH):
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDS_PATH}\n"
                    "Follow the setup instructions in workflows/google_sheets_setup.md"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("sheets", "v4", credentials=creds)


# ---------------------------------------------------------------------------
# Sheet setup
# ---------------------------------------------------------------------------

def ensure_sheet_exists(service):
    """Create the Drafts sheet tab if it doesn't exist, add header row."""
    sheet = service.spreadsheets()

    # Get existing sheets
    meta = sheet.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]

    if SHEET_NAME not in existing:
        # Add new sheet tab
        sheet.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME}}}]}
        ).execute()
        print(f"  Created sheet tab: {SHEET_NAME}")

    # Check if header row exists
    result = sheet.values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A1:L1"
    ).execute()

    if not result.get("values"):
        # Write header row
        sheet.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [COLUMNS]}
        ).execute()

        # Format header: bold, frozen, background colour
        sheet_id = next(
            s["properties"]["sheetId"]
            for s in service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()["sheets"]
            if s["properties"]["title"] == SHEET_NAME
        )
        sheet.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [
                # Bold header
                {"repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "backgroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1},
                    }},
                    "fields": "userEnteredFormat(textFormat,backgroundColor)"
                }},
                # Freeze header row
                {"updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount"
                }},
            ]}
        ).execute()
        print("  Header row written.")


def get_existing_draft_ids(service) -> set[int]:
    """Return set of Draft IDs already in the sheet."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A2:A"
    ).execute()
    rows = result.get("values", [])
    ids = set()
    for row in rows:
        try:
            ids.add(int(row[0]))
        except (ValueError, IndexError):
            pass
    return ids


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def word_count(html: str) -> int:
    import re
    text = re.sub(r"<[^>]+>", " ", html or "")
    return len(text.split())


def strip_html(html: str) -> str:
    """Keep text readable in Sheets without HTML tags."""
    import re
    text = re.sub(r"<h[1-6][^>]*>", "\n### ", html or "")
    text = re.sub(r"</h[1-6]>", "\n", text)
    text = re.sub(r"<p[^>]*>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Main export
# ---------------------------------------------------------------------------

def export():
    if not SHEET_ID:
        print("GOOGLE_SHEET_ID not set in .env — cannot export.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT d.id, d.headline, d.body, d.category, d.seo_description,
               d.created_at, d.raw_article_ids, d.wp_post_id
        FROM drafts d
        WHERE d.status = 'pending_editor'
        ORDER BY d.id DESC
    """)
    drafts = [dict(r) for r in c.fetchall()]

    # Get source info per draft
    for d in drafts:
        ids = json.loads(d.get("raw_article_ids") or "[]")
        if ids:
            placeholders = ",".join("?" * len(ids))
            c.execute(f"""
                SELECT source, url FROM raw_articles WHERE id IN ({placeholders})
            """, ids)
            rows = c.fetchall()
            d["sources"]     = " + ".join(set(r["source"] for r in rows))
            d["source_urls"] = "\n".join(r["url"] for r in rows)
        else:
            d["sources"] = ""
            d["source_urls"] = ""

    conn.close()

    if not drafts:
        print("No pending drafts to export.")
        return

    print(f"Connecting to Google Sheets ...")
    service = get_sheets_service()
    ensure_sheet_exists(service)

    existing_ids = get_existing_draft_ids(service)
    new_drafts = [d for d in drafts if d["id"] not in existing_ids]

    if not new_drafts:
        print(f"All {len(drafts)} draft(s) already in sheet — nothing new to add.")
        return

    print(f"Exporting {len(new_drafts)} new draft(s) ...")

    rows = []
    for d in new_drafts:
        created = d.get("created_at", "")[:16].replace("T", " ") if d.get("created_at") else ""
        rows.append([
            d["id"],
            created,
            d.get("category", ""),
            d.get("headline", ""),
            d.get("seo_description", ""),
            strip_html(d.get("body", "")),
            word_count(d.get("body", "")),
            d.get("sources", ""),
            d.get("source_urls", ""),
            "",   # Approval — blank, editor fills this
            "",   # Editor Notes
            d.get("wp_post_id") or "",
        ])

    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A2",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()

    print(f"✓ {len(new_drafts)} draft(s) exported to Google Sheet.")
    print(f"  Open: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")


if __name__ == "__main__":
    export()
