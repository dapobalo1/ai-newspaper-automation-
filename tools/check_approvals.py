"""
check_approvals.py
Reads the Approval column from the Google Sheet and updates draft statuses in the DB.

Approval values (editor types in column J):
  APPROVE  → status = 'pending_publisher'  (ready for WordPress)
  REJECT   → status = 'rejected'
  EDIT     → status = 'needs_edit'  (stays in sheet, editor revises body)
  (blank)  → no change (still pending_editor)

Run this after editors have reviewed drafts in the sheet.
Then run publish_to_wordpress.py to push approved drafts live.

Usage: python3 tools/check_approvals.py
"""

import os
import sqlite3
from datetime import datetime, timezone

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    raise SystemExit("Run: pip3 install google-auth google-auth-oauthlib google-api-python-client")

from dotenv import load_dotenv
load_dotenv()

DB_PATH    = os.path.join(os.path.dirname(__file__), "..", "articles.db")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "token.json")
CREDS_PATH = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
SHEET_ID   = os.getenv("GOOGLE_SHEET_ID", "")
SHEET_NAME = "Drafts"
SCOPES     = ["https://www.googleapis.com/auth/spreadsheets"]

STATUS_MAP = {
    "APPROVE": "pending_publisher",
    "APPROVED": "pending_publisher",
    "YES": "pending_publisher",
    "REJECT": "rejected",
    "REJECTED": "rejected",
    "NO": "rejected",
    "EDIT": "needs_edit",
}


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
    return build("sheets", "v4", credentials=creds)


def check_approvals():
    if not SHEET_ID:
        print("GOOGLE_SHEET_ID not set in .env")
        return

    print("Reading approvals from Google Sheet ...")
    service = get_sheets_service()

    # Columns: A=Draft ID, J=Approval, K=Editor Notes
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A2:K"
    ).execute()

    rows = result.get("values", [])
    if not rows:
        print("No data found in sheet.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    approved = rejected = edited = skipped = 0

    for row in rows:
        # Pad row to at least 11 columns
        row = row + [""] * (11 - len(row))
        draft_id_raw = row[0]
        approval     = row[9].strip().upper()   # Column J
        notes        = row[10].strip()          # Column K

        if not draft_id_raw:
            continue
        try:
            draft_id = int(draft_id_raw)
        except ValueError:
            continue

        if not approval:
            skipped += 1
            continue

        new_status = STATUS_MAP.get(approval)
        if not new_status:
            print(f"  Draft {draft_id}: unrecognised approval value '{approval}' — skipping")
            skipped += 1
            continue

        c.execute("""
            UPDATE drafts
            SET status = ?,
                editor_notes = ?
            WHERE id = ? AND status NOT IN ('published', 'rejected')
        """, (new_status, notes, draft_id))

        if new_status == "pending_publisher":
            print(f"  Draft {draft_id}: ✓ APPROVED → ready for WordPress")
            approved += 1
        elif new_status == "rejected":
            print(f"  Draft {draft_id}: ✗ REJECTED — {notes or 'no reason given'}")
            rejected += 1
        elif new_status == "needs_edit":
            print(f"  Draft {draft_id}: ✎ EDIT NEEDED — {notes or 'no notes'}")
            edited += 1

    conn.commit()
    conn.close()

    print(f"\nSummary: {approved} approved, {rejected} rejected, {edited} need edits, {skipped} still pending.")

    if approved > 0:
        print(f"\nNext step: run  python3 tools/publish_to_wordpress.py  to push approved drafts to WordPress.")


if __name__ == "__main__":
    check_approvals()
