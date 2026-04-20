# AI Newspaper Automation Pipeline

An end-to-end AI-powered news automation system built on the **WAT framework** (Workflows, Agents, Tools). Designed for online news publications — automatically fetches, curates, rewrites, and routes articles through an editorial approval process before publishing to WordPress.

---

## What It Does

Every morning at 8 AM the pipeline:

1. **Fetches** fresh articles from 8 curated RSS news sources
2. **Stores** them in a SQLite database with URL-based deduplication
3. **Groups** articles covering the same story across different sources
4. **Ranks** stories by recency, source authority, and cross-source frequency
5. **Generates** publication-ready drafts using Claude AI — matching the publication's editorial voice and brand guidelines
6. **Attaches** royalty-free images via Unsplash API
7. **Exports** drafts to a Google Sheet for editorial review
8. **Publishes** approved drafts to WordPress as drafts (never live without human approval)
9. **Notifies** the editorial team by email

---

## Architecture

```
RSS Sources (8 feeds)
        ↓
fetch_rss_feeds.py     → Filter by topic keywords, enforce 24hr freshness
        ↓
store_articles.py      → SQLite database with URL deduplication
        ↓
deduplicate.py         → Group same-story articles across sources (Jaccard similarity)
        ↓
rank_stories.py        → Score by recency + authority + cross-source frequency
        ↓
generate_draft.py      → Claude AI rewrites / multi-source synthesis
        ↓
fetch_image.py         → Unsplash royalty-free image search + WP upload
        ↓
publish_to_wordpress.py → WordPress REST API → status: draft (never auto-published)
        ↓
export_to_sheets.py    → Google Sheets for editorial review
        ↓
send_email.py          → SMTP notification to editor and publisher
```

### Human Approval Flow

```
AI Draft (WordPress: draft)
        ↓
Editor reviews → edits → approves
        ↓
(WordPress: pending review)
        ↓
Publisher final check → clicks Publish
        ↓
Article goes live ✓
```

Nothing is ever published without explicit human approval at two stages.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI / LLM | Claude API (claude-sonnet-4-6) |
| Language | Python 3.13 |
| Database | SQLite |
| CMS Integration | WordPress REST API |
| Editorial Review | Google Sheets API |
| Images | Unsplash API |
| Email | SMTP (Rackspace / Gmail compatible) |
| Infrastructure | AWS EC2 (Docker) |
| News Sources | RSS feeds (feedparser + requests) |

---

## Project Structure

```
.
├── CLAUDE.md                     # WAT framework agent instructions
├── .env.example                  # Environment variable template
├── .gitignore
├── tools/
│   ├── init_db.py                # Database initialisation (run once)
│   ├── fetch_rss_feeds.py        # RSS fetcher with keyword filtering
│   ├── store_articles.py         # SQLite storage with deduplication
│   ├── deduplicate.py            # Cross-source story grouping
│   ├── rank_stories.py           # Story scoring and selection
│   ├── generate_draft.py         # Claude AI draft generation
│   ├── fetch_image.py            # Unsplash image fetch + WP upload
│   ├── publish_to_wordpress.py   # WordPress REST API — always routes to "The Shoreline"
│   ├── sync_youtube.py           # YouTube new video → WordPress draft
│   ├── export_to_sheets.py       # Google Sheets export
│   ├── check_approvals.py        # Read approvals from sheet → update DB
│   ├── send_email.py             # Editor/publisher email notifications
│   └── preview_drafts.py         # Local HTML preview of drafts
└── workflows/
    ├── daily_news_fetch.md       # Full pipeline SOP
    ├── editorial_approval.md     # Approval workflow SOP
    └── google_sheets_setup.md    # Google API setup guide
```

---

## Setup

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/ai-newspaper-automation.git
cd ai-newspaper-automation
```

### 2. Install dependencies
```bash
pip3 install feedparser requests anthropic python-dotenv \
  google-auth google-auth-oauthlib google-auth-httplib2 \
  google-api-python-client
```

### 3. Configure environment variables
```bash
cp .env.example .env
# Fill in your API keys and credentials in .env
```

### 4. Initialise the database
```bash
python3 tools/init_db.py
```

### 5. Set up Google Sheets
Follow the guide in [workflows/google_sheets_setup.md](workflows/google_sheets_setup.md)

### 6. Run the pipeline
```bash
python3 tools/fetch_rss_feeds.py && \
python3 tools/store_articles.py && \
python3 tools/deduplicate.py && \
python3 tools/rank_stories.py && \
python3 tools/generate_draft.py && \
python3 tools/fetch_image.py && \
python3 tools/publish_to_wordpress.py && \
python3 tools/export_to_sheets.py && \
python3 tools/send_email.py
```

### 7. Schedule daily at 8 AM (optional)
```bash
crontab -e
# Add:
0 8 * * * cd /path/to/project && python3 tools/fetch_rss_feeds.py && ...
```

---

## News Sources

| Publication | Region | Focus |
|-------------|--------|-------|
| Vanguard Nigeria | Nigeria | Politics, Business |
| Daily Trust | Nigeria | News, Northern Nigeria |
| Premium Times | Nigeria | Investigative |
| ThisDay Live | Nigeria | Business, Politics |
| Al Jazeera | Global | International News |
| BBC Africa | Africa | Regional News |
| TechCabal | Africa | Tech & Startups |
| Kyiv Independent | Global | Geopolitics |

---

## Key Features

- **Multi-source synthesis** — when 3+ sources cover the same story, Claude synthesises them into one richer article rather than duplicating
- **Brand voice matching** — system prompt derived from deep analysis of the publication's existing articles
- **Staleness filter** — only processes articles published in the last 24 hours
- **Story ranking** — prioritises by recency, source authority, and cross-source frequency
- **Zero auto-publish** — every article requires human approval at editor and publisher level before going live
- **Google Sheets approval** — editorial team reviews drafts in a familiar interface without needing WordPress access
- **Category intelligence** — AI articles always route to "The Shoreline" (breaking news segment); The Pulse and Viewpoint (human-written only) are permanently blocked
- **Royalty-free images** — Claude generates topic keywords; Unsplash finds the best match and attaches it as featured image
- **YouTube sync** — new channel videos automatically create WordPress draft posts with embedded player

---

## Environment Variables

See [.env.example](.env.example) for the full list of required variables.

---

## Built With the WAT Framework

This project follows the **WAT (Workflows, Agents, Tools)** architecture:
- **Workflows** — Markdown SOPs in `workflows/` defining objectives and steps
- **Agents** — Claude Code orchestrates tool execution and handles errors
- **Tools** — Deterministic Python scripts in `tools/` for reliable execution

---

## License

MIT
