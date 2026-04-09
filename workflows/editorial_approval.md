# Workflow: Editorial Approval

## Objective
Manage the 2-stage human review process (Editor → Publisher) before any AI-generated article is published on Atlantic Digest. No article goes live without explicit human sign-off at each stage.

## How It Works (Overview)

The workflow uses WordPress's native post status system — no separate dashboard needed.

```
AI generates draft
      ↓
WordPress post status: DRAFT
      ↓
Editor notified by email at 8 AM
      ↓
Editor reviews, edits, approves
      ↓
WordPress post status: PENDING REVIEW
      ↓
Publisher notified by email
      ↓
Publisher gives final approval
      ↓
WordPress post status: PUBLISHED ✓
```

---

## Stage 1 — Editor Review

### Who
The editor (or senior writer) designated in `EDITOR_EMAIL`.

### Trigger
Email notification received at 8 AM after the daily pipeline runs.

### What the Editor Sees
- An email listing all new AI drafts with direct links to each WordPress edit page
- Each article opens in the standard WordPress editor

### Editor Responsibilities
1. **Read the full draft** — check accuracy, tone, and Atlantic Digest voice
2. **Edit freely** — fix facts, improve phrasing, adjust headline, change category
3. **Check the featured image** — replace if the auto-selected image is a poor match
4. **Check the source** — click the source link in the article footer to verify the AI hasn't invented facts
5. **If the article is good**: Change WordPress post status from **Draft** → **Pending Review** and click Update
6. **If the article needs more work**: Keep as Draft, add editorial notes in the private notes field, and flag for revision
7. **If the article should not run**: Trash it in WordPress

### What Happens Next
- When an article moves to "Pending Review", the next `send_email.py` run (or end-of-day run) notifies the publisher
- The publisher notification can also be triggered manually: `python3 tools/send_email.py`

### Things to Watch For
- **Fabricated quotes**: Claude should not invent quotes, but always verify any direct quotes against the source article
- **Wrong dates**: Claude sometimes uses approximate dates — check against the original
- **Category mismatch**: Re-assign to the correct WordPress category if wrong
- **Nigerian spelling / terminology**: Verify local names, agency names, and institutions are spelled correctly

---

## Stage 2 — Publisher Review

### Who
The publisher or editor-in-chief designated in `PUBLISHER_EMAIL`.

### Trigger
Email notification sent by the system when articles reach "Pending Review" status.

### Publisher Responsibilities
1. **Final editorial check** — confirm the article meets publication standards
2. **Legal/sensitivity check** — flag anything potentially defamatory or legally sensitive before publishing
3. **Approve**: Click **Publish** in WordPress → article goes live immediately
4. **Return to editor**: Change status back to Draft if further edits are needed, with a note

### Notes
- The publisher can see all pending articles at: `{WP_SITE_URL}/wp-admin/edit.php?post_status=pending&post_type=post`
- Published articles are visible at: `{WP_SITE_URL}/wp-admin/edit.php?post_status=publish&post_type=post`

---

## Monitoring & Feedback Loop

### Checking Pipeline Status
To see what's in the pipeline at any time:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('articles.db')
for row in conn.execute('SELECT status, COUNT(*) FROM drafts GROUP BY status'):
    print(f'{row[0]}: {row[1]}')
conn.close()
"
```

### Recording Rejection Reasons
When an editor rejects (trashes) an article, note why in the WordPress private notes before trashing. This builds a feedback record that can be used to improve the AI prompt over time.

Common rejection categories to track:
- `WRONG_TONE` — too formal / too casual / sensational
- `WRONG_FACTS` — AI made a factual error
- `WRONG_CATEGORY` — story doesn't fit Atlantic Digest's focus
- `STALE` — story is outdated by the time of review
- `DUPLICATE` — same story already published elsewhere on the site

### Improving the System
If a category of rejection repeats 3+ times:
1. Review the pattern
2. Update the `SYSTEM_PROMPT` in `tools/generate_draft.py`
3. Update the keyword filters in `tools/fetch_rss_feeds.py` if wrong-category stories keep appearing
4. Document the change in this workflow file

---

## Escalation

| Issue | Action |
|-------|--------|
| AI draft contains defamatory content | Do NOT publish. Trash immediately. Flag to publisher. |
| AI appears to have fabricated a major fact | Trash. Report source article URL and the fabrication to improve the prompt. |
| Editor receives no email | Check SMTP settings in `.env`. Run `python3 tools/send_email.py` manually to debug. |
| WordPress posts not appearing | Check WP credentials. Run `python3 tools/publish_to_wordpress.py` manually. |
| Pipeline didn't run at 8 AM | Check cron log at `.tmp/cron.log`. Re-run manually if needed. |

---

## Manual Re-run
If the pipeline fails partway through, it is safe to re-run any individual step — all tools are idempotent (safe to run multiple times without side effects).
