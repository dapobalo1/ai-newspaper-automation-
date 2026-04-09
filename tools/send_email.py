"""
send_email.py
Sends notification emails to the Editor and Publisher based on current draft statuses.

  - Editor email:    sent when drafts have status = 'pending_editor' and wp_post_id set
  - Publisher email: sent when drafts have status = 'pending_publisher' and wp_post_id set

Usage: python3 tools/send_email.py
Requirements: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
              EDITOR_EMAIL, PUBLISHER_EMAIL, WP_SITE_URL in .env
"""

import os
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

DB_PATH      = os.path.join(os.path.dirname(__file__), "..", "articles.db")
SMTP_HOST    = os.getenv("SMTP_HOST", "")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER", "")
SMTP_PASS    = os.getenv("SMTP_PASSWORD", "")
EDITOR_EMAIL = os.getenv("EDITOR_EMAIL", "")
PUB_EMAIL    = os.getenv("PUBLISHER_EMAIL", "")
WP_SITE_URL  = os.getenv("WP_SITE_URL", "").rstrip("/")

WP_DRAFTS_URL  = f"{WP_SITE_URL}/wp-admin/edit.php?post_status=draft&post_type=post"
WP_PENDING_URL = f"{WP_SITE_URL}/wp-admin/edit.php?post_status=pending&post_type=post"


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def build_html(subject_line: str, intro: str, articles: list[dict], cta_url: str, cta_label: str) -> str:
    rows = ""
    for a in articles:
        wp_link = f"{WP_SITE_URL}/wp-admin/post.php?post={a['wp_post_id']}&action=edit" if a.get("wp_post_id") else "#"
        rows += f"""
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid #eee;">
            <strong><a href="{wp_link}" style="color:#1a1a1a;text-decoration:none;">{a['headline']}</a></strong><br>
            <span style="color:#666;font-size:13px;">{a.get('category','')} &mdash; {a.get('seo_description','')[:80]}</span>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;color:#1a1a1a;max-width:600px;margin:0 auto;padding:20px;">
  <div style="border-top:4px solid #1a1a1a;padding-top:20px;margin-bottom:20px;">
    <img src="{WP_SITE_URL}/wp-content/uploads/site-logo.png"
         alt="News Publication" style="height:40px;" onerror="this.style.display='none'">
    <h1 style="font-size:22px;margin:10px 0 4px;">{subject_line}</h1>
    <p style="color:#666;font-size:14px;margin:0;">{datetime.now().strftime('%A, %d %B %Y')}</p>
  </div>

  <p style="font-size:16px;">{intro}</p>

  <table width="100%" cellpadding="0" cellspacing="0">
    {rows}
  </table>

  <div style="margin-top:24px;text-align:center;">
    <a href="{cta_url}"
       style="background:#1a1a1a;color:#fff;padding:12px 24px;
              text-decoration:none;font-size:15px;display:inline-block;">
      {cta_label}
    </a>
  </div>

  <p style="font-size:12px;color:#999;margin-top:30px;border-top:1px solid #eee;padding-top:12px;">
    This notification was sent automatically by the Atlantic Digest AI workflow.
    All articles require human review before publication.
  </p>
</body>
</html>"""


def send(to: str, subject: str, html_body: str):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, to]):
        print(f"    WARN: SMTP not configured or recipient missing — email to {to} skipped.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Atlantic Digest Workflow <{SMTP_USER}>"
    msg["To"]      = to
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"    SMTP error: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def notify():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # --- Editor notification: drafts ready for review ---
    c.execute("""
        SELECT id, headline, category, seo_description, wp_post_id
        FROM drafts
        WHERE status = 'pending_editor'
          AND wp_post_id IS NOT NULL AND wp_post_id != 0
        ORDER BY id DESC
    """)
    editor_drafts = [dict(r) for r in c.fetchall()]

    if editor_drafts:
        print(f"Notifying editor: {len(editor_drafts)} draft(s) ready ...")
        html = build_html(
            subject_line=f"Atlantic Digest: {len(editor_drafts)} AI Draft(s) Ready for Your Review",
            intro=(
                f"{len(editor_drafts)} new AI-generated article(s) are waiting for your editorial review. "
                f"Please review, edit as needed, and change the post status to <strong>Pending Review</strong> "
                f"to pass them to the publisher."
            ),
            articles=editor_drafts,
            cta_url=WP_DRAFTS_URL,
            cta_label=f"Review {len(editor_drafts)} Draft(s) →",
        )
        ok = send(
            to=EDITOR_EMAIL,
            subject=f"[Atlantic Digest] {len(editor_drafts)} AI Drafts Ready for Review",
            html_body=html,
        )
        print(f"  Editor email: {'✓ Sent' if ok else '✗ Failed'}")
    else:
        print("No editor drafts to notify about.")

    # --- Publisher notification: pending final approval ---
    c.execute("""
        SELECT id, headline, category, seo_description, wp_post_id
        FROM drafts
        WHERE status = 'pending_publisher'
          AND wp_post_id IS NOT NULL AND wp_post_id != 0
        ORDER BY id DESC
    """)
    pub_drafts = [dict(r) for r in c.fetchall()]

    if pub_drafts:
        print(f"Notifying publisher: {len(pub_drafts)} article(s) pending approval ...")
        html = build_html(
            subject_line=f"Atlantic Digest: {len(pub_drafts)} Article(s) Pending Final Approval",
            intro=(
                f"{len(pub_drafts)} article(s) have been reviewed by the editor and are now awaiting "
                f"your final approval. Please review and publish or return to draft."
            ),
            articles=pub_drafts,
            cta_url=WP_PENDING_URL,
            cta_label=f"Approve & Publish {len(pub_drafts)} Article(s) →",
        )
        ok = send(
            to=PUB_EMAIL,
            subject=f"[Atlantic Digest] {len(pub_drafts)} Articles Pending Your Approval",
            html_body=html,
        )
        print(f"  Publisher email: {'✓ Sent' if ok else '✗ Failed'}")
    else:
        print("No publisher-pending articles to notify about.")

    conn.close()


if __name__ == "__main__":
    notify()
