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
    date_str = datetime.now().strftime("%A, %d %B %Y")

    cards = ""
    for a in articles:
        wp_link  = f"{WP_SITE_URL}/wp-admin/post.php?post={a['wp_post_id']}&action=edit" if a.get("wp_post_id") else "#"
        category = a.get("category", "")
        desc     = a.get("seo_description", "")[:90]
        cards += f"""
        <div style="border:1px solid #e8ecf0;border-radius:8px;padding:18px 20px;
                    margin-bottom:12px;background:#fff;">
          <span style="display:inline-block;background:#f0f4ff;color:#3b5bdb;
                       font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;
                       text-transform:uppercase;letter-spacing:0.6px;margin-bottom:10px;">
            {category}
          </span>
          <p style="margin:0 0 8px;">
            <a href="{wp_link}"
               style="font-size:16px;font-weight:700;color:#1a1a2e;text-decoration:none;
                      line-height:1.4;">{a['headline']}</a>
          </p>
          <p style="margin:0;font-size:13px;color:#6b7280;line-height:1.5;">{desc}</p>
        </div>"""

    return f"""<!DOCTYPE html>
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
    <p style="margin:0 0 8px;font-size:11px;font-weight:700;color:#64748b;
              text-transform:uppercase;letter-spacing:1.5px;">Atlantic Digest · AI Workflow</p>
    <h1 style="margin:0 0 10px;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">
      {subject_line}
    </h1>
    <p style="margin:0;font-size:14px;color:#8892b0;">{date_str}</p>
  </td></tr>

  <!-- Body -->
  <tr><td style="background:#f8fafc;padding:36px 40px;">

    <p style="margin:0 0 24px;font-size:16px;color:#444;line-height:1.75;">{intro}</p>

    {cards}

    <!-- CTA -->
    <div style="text-align:center;margin-top:28px;">
      <a href="{cta_url}"
         style="display:inline-block;background:#1a1a2e;color:#ffffff;
                font-size:15px;font-weight:600;padding:15px 36px;border-radius:8px;
                text-decoration:none;letter-spacing:0.4px;">
        {cta_label}
      </a>
    </div>

  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#f0f2f5;padding:24px 40px;text-align:center;">
    <p style="margin:0;font-size:12px;color:#94a3b8;line-height:1.8;">
      All articles require human review before publication.<br>
      Sent automatically &nbsp;·&nbsp; Atlantic Digest AI Workflow
    </p>
  </td></tr>

</table>
</td></tr>
</table>
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
