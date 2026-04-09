"""
preview_drafts.py
Exports all pending drafts from the DB as a single HTML file you can
open in any browser to review article quality before touching WordPress.

Usage: python3 tools/preview_drafts.py
Output: .tmp/preview_drafts.html  ← open this in Chrome/Safari
"""

import os
import sqlite3
import json
from datetime import datetime

DB_PATH  = os.path.join(os.path.dirname(__file__), "..", "articles.db")
TMP_DIR  = os.path.join(os.path.dirname(__file__), "..", ".tmp")


def generate_preview():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT d.id, d.headline, d.body, d.category, d.seo_description,
               d.status, d.created_at, d.raw_article_ids,
               GROUP_CONCAT(r.source, ' + ') as sources,
               GROUP_CONCAT(r.url, '|||') as source_urls
        FROM drafts d
        LEFT JOIN raw_articles r ON r.id IN (
            SELECT value FROM json_each(d.raw_article_ids)
        )
        WHERE d.status = 'pending_editor'
        GROUP BY d.id
        ORDER BY d.id DESC
    """)
    drafts = [dict(r) for r in c.fetchall()]
    conn.close()

    if not drafts:
        print("No pending drafts found. Run generate_draft.py first.")
        return

    # Build HTML
    cards = ""
    for i, d in enumerate(drafts, 1):
        source_links = ""
        if d.get("source_urls"):
            for url in d["source_urls"].split("|||"):
                url = url.strip()
                if url:
                    source_links += f'<a href="{url}" target="_blank" style="color:#666;font-size:13px;display:block;margin-top:4px;">{url[:80]}...</a>'

        status_color = {"pending_editor": "#e67e22", "pending_publisher": "#2980b9", "published": "#27ae60"}.get(d["status"], "#999")

        cards += f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:32px;margin-bottom:32px;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;">
            <span style="background:#1a1a1a;color:#fff;padding:4px 10px;border-radius:4px;font-size:12px;font-family:sans-serif;">
              {d.get('category','General')}
            </span>
            <span style="background:{status_color};color:#fff;padding:4px 10px;border-radius:4px;font-size:12px;font-family:sans-serif;">
              Draft #{d['id']}
            </span>
          </div>

          <h2 style="font-size:24px;margin:0 0 8px;line-height:1.3;color:#1a1a1a;">{d['headline']}</h2>

          <p style="color:#666;font-size:14px;font-style:italic;margin:0 0 20px;border-left:3px solid #e0e0e0;padding-left:12px;">
            {d.get('seo_description','') or ''}
          </p>

          <div style="font-size:16px;line-height:1.8;color:#222;">
            {d.get('body','') or '<p><em>No body content</em></p>'}
          </div>

          <div style="margin-top:24px;padding-top:16px;border-top:1px solid #eee;">
            <strong style="font-size:13px;font-family:sans-serif;color:#444;">Source(s): {d.get('sources','')}</strong>
            {source_links}
          </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Atlantic Digest — Draft Preview ({len(drafts)} articles)</title>
  <style>
    body {{
      font-family: Georgia, 'Times New Roman', serif;
      background: #f5f5f5;
      color: #1a1a1a;
      margin: 0;
      padding: 32px 16px;
    }}
    .container {{ max-width: 800px; margin: 0 auto; }}
    h1 {{ font-size: 28px; margin-bottom: 4px; }}
    .meta {{ color: #666; font-family: sans-serif; font-size: 14px; margin-bottom: 32px; }}
    .legend {{ font-family: sans-serif; font-size: 13px; color: #666; margin-bottom: 24px; }}
  </style>
</head>
<body>
  <div class="container">
    <div style="border-top:4px solid #1a1a1a;padding-top:20px;margin-bottom:8px;">
      <h1>Atlantic Digest — AI Draft Preview</h1>
      <p class="meta">Generated: {datetime.now().strftime('%A, %d %B %Y at %H:%M')} &nbsp;|&nbsp; {len(drafts)} article(s) pending editor review</p>
      <p class="legend">
        Review each article below. Check: headline quality, tone, factual accuracy (click source links to verify),
        article length, and Atlantic Digest voice. Once satisfied, add WordPress credentials and run the full pipeline.
      </p>
    </div>
    {cards}
    <p style="text-align:center;color:#999;font-family:sans-serif;font-size:13px;padding:32px 0;">
      Atlantic Digest AI Workflow — Preview only. No content has been published.
    </p>
  </div>
</body>
</html>"""

    os.makedirs(TMP_DIR, exist_ok=True)
    out_path = os.path.join(TMP_DIR, "preview_drafts.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Preview saved: {out_path}")
    print(f"Open it in your browser to review {len(drafts)} draft(s).")

    # Also try to open it automatically on macOS
    import subprocess
    try:
        subprocess.run(["open", out_path], check=True)
        print("Opening in browser...")
    except Exception:
        pass


if __name__ == "__main__":
    generate_preview()
