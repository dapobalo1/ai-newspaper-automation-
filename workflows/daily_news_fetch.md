# Workflow: Daily News Fetch & Draft Generation

## Objective
Every morning at 8 AM, automatically fetch fresh news from curated RSS sources, store it in the database, remove duplicates, rank the top stories, generate AI drafts in Atlantic Digest's editorial voice, attach images, publish drafts to WordPress, and notify the editor by email.

## Trigger
- **Schedule**: Daily at 8:00 AM (cron or scheduler)
- **Manual**: Run `python3 tools/fetch_rss_feeds.py` followed by the sequence below

## Required Inputs
- `.env` file populated with:
  - `ANTHROPIC_API_KEY`
  - `WP_SITE_URL`, `WP_USERNAME`, `WP_APP_PASSWORD`
  - `UNSPLASH_ACCESS_KEY`
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
  - `EDITOR_EMAIL`, `PUBLISHER_EMAIL`
  - `DAILY_ARTICLE_LIMIT` (default: 8)
  - `ARTICLE_MAX_AGE_HOURS` (default: 24)
- `articles.db` initialised (run `python3 tools/init_db.py` once on setup)

## Pipeline (run in order)

### Step 1 — Fetch RSS Feeds
```bash
python3 tools/fetch_rss_feeds.py
```
- Hits all 8 RSS sources with a browser User-Agent
- Filters articles by topic keywords (Politics, Business, Sports, Tech, etc.)
- Discards articles older than 24 hours
- Saves to `.tmp/raw_feed_YYYY-MM-DD.json`

**Expected output**: 80–150 relevant articles

**If output is 0**: Check internet connectivity. Check if any source URLs have changed (RSS URLs can move). Update `SOURCES` list in the script.

---

### Step 2 — Store to Database
```bash
python3 tools/store_articles.py
```
- Reads the latest `raw_feed_*.json` from `.tmp/`
- Inserts new articles into `raw_articles` table
- Skips URLs already in the database (UNIQUE constraint)

**Expected output**: `Inserted: N | Skipped (duplicates): M`

---

### Step 3 — Deduplicate
```bash
python3 tools/deduplicate.py
```
- Groups articles covering the same story across different sources using Jaccard title similarity
- Assigns `story_group_id` to grouped articles
- Marks secondary articles `is_duplicate = 1`
- Flags multi-source groups for synthesis

**Expected output**: 3–8 story groups identified

**If too many false groups**: Increase `THRESHOLD` in `deduplicate.py` (default 0.35 → try 0.45).
**If too few**: Decrease threshold.

---

### Step 4 — Rank Stories
```bash
python3 tools/rank_stories.py
```
- Scores non-duplicate articles by: recency, source authority, cross-source frequency, category
- Selects top N (controlled by `DAILY_ARTICLE_LIMIT` env var, default 8)
- Saves to `.tmp/ranked_YYYY-MM-DD.json`

**Expected output**: Top 8 stories with scores, saved to `.tmp/`

---

### Step 5 — Generate AI Drafts
```bash
python3 tools/generate_draft.py
```
- Reads `ranked_*.json`
- For each story:
  - If `is_multi_source`: fetches all articles in the group and asks Claude to synthesise
  - Otherwise: asks Claude to rewrite the single article
- Saves drafts to `drafts` table with status `pending_editor`
- Uses Claude `claude-sonnet-4-6` with the Atlantic Digest system prompt

**Expected output**: 8 drafts inserted into DB

**If Claude errors**: Check `ANTHROPIC_API_KEY`. If rate limited, wait 60s and retry. Claude errors are logged per article — the run continues for remaining articles.

**Dry run (no DB write)**:
```bash
python3 tools/generate_draft.py --dry-run
```

---

### Step 6 — Fetch Images
```bash
python3 tools/fetch_image.py
```
- For each new draft without an image:
  - Searches Unsplash using headline keywords
  - Downloads the best match
  - Uploads to WordPress Media Library
  - Updates `drafts.wp_image_id`

**If no Unsplash key**: Drafts are created without images. Editor adds manually in WP.

---

### Step 7 — Publish Drafts to WordPress
```bash
python3 tools/publish_to_wordpress.py
```
- Creates each draft as a WordPress `draft` post
- Attaches featured image (if available)
- Sets correct category using WP category API
- Updates `drafts.wp_post_id` in DB

**If WP credentials missing**: Script warns and exits cleanly. Set credentials in `.env` and re-run.

**To get WordPress Application Password**:
1. Log into WP Admin → Users → Your Profile
2. Scroll to "Application Passwords"
3. Enter name "Atlantic Digest Bot" → Add
4. Copy the generated password into `.env` as `WP_APP_PASSWORD`

---

### Step 8 — Notify Editor
```bash
python3 tools/send_email.py
```
- Sends a formatted HTML email to `EDITOR_EMAIL` listing all new drafts
- Each draft links directly to its WordPress edit page
- If publisher-pending articles exist, also notifies `PUBLISHER_EMAIL`

---

## Running the Full Pipeline (One Command)
```bash
python3 tools/fetch_rss_feeds.py && \
python3 tools/store_articles.py && \
python3 tools/deduplicate.py && \
python3 tools/rank_stories.py && \
python3 tools/generate_draft.py && \
python3 tools/fetch_image.py && \
python3 tools/publish_to_wordpress.py && \
python3 tools/send_email.py
```

## Scheduling (8 AM Daily)
```bash
# Add to crontab (run: crontab -e)
0 8 * * * cd /path/to/NewsPaper\ automation\ Demo && \
  python3 tools/fetch_rss_feeds.py && \
  python3 tools/store_articles.py && \
  python3 tools/deduplicate.py && \
  python3 tools/rank_stories.py && \
  python3 tools/generate_draft.py && \
  python3 tools/fetch_image.py && \
  python3 tools/publish_to_wordpress.py && \
  python3 tools/send_email.py >> .tmp/cron.log 2>&1
```

## Known Constraints
- **Punch Nigeria RSS** (`punchng.com/feed/`) returns empty — replaced with ThisDay Live. Monitor for when Punch fixes their feed.
- **Kyiv Independent** uses `/feed/rss/` not `/feed/` — tested working as of March 2026.
- **Reuters** DNS resolves inconsistently — dropped from sources. Add back when stable.
- **Daily article limit**: Default is 8 to keep editor workload manageable. Adjust `DAILY_ARTICLE_LIMIT` in `.env`.
- **Claude API**: ~$0.43/day at 8 articles. Check usage at console.anthropic.com.

## Output Files
| File | Purpose |
|------|---------|
| `.tmp/raw_feed_YYYY-MM-DD.json` | All filtered RSS articles from today |
| `.tmp/ranked_YYYY-MM-DD.json` | Top scored articles selected for drafting |
| `.tmp/drafts_summary_YYYY-MM-DD.json` | Summary of generated drafts |
| `.tmp/cron.log` | Cron execution log |
| `articles.db` | All raw articles + drafts (permanent) |
