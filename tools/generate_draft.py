"""
generate_draft.py
Reads ranked stories, calls the Claude API to generate Atlantic Digest–style
drafts, and saves them to the drafts table in the DB.

Handles:
  - Single-source rewrite
  - Multi-source synthesis (combines all articles in the same story group)

Usage:
  python3 tools/generate_draft.py                        # use latest ranked JSON
  python3 tools/generate_draft.py --file .tmp/ranked_2026-03-31.json
  python3 tools/generate_draft.py --dry-run              # print output, don't save
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from glob import glob

try:
    import anthropic
except ImportError:
    raise SystemExit("Missing dependency: pip3 install anthropic")

from dotenv import load_dotenv
load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "articles.db")
TMP_DIR = os.path.join(os.path.dirname(__file__), "..", ".tmp")

CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL  = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# System prompt (derived from Atlantic Digest brand voice analysis)
# ---------------------------------------------------------------------------

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
- Nigerian currency: ₦ symbol (e.g., ₦1,200 per litre)
- Nigerian institutions named in full on first mention, then acronym (ICPC, CBN, NDPC)
- Specific figures always: percentages, amounts, quantities, dates
- Never use vague temporal language — always use actual dates
- Explain global concepts in terms relevant to a Nigerian/diaspora audience

ATTRIBUTION:
- Name sources explicitly with title and affiliation
- Formal verbs: "noted," "explained," "characterised," "indicated," "said"
- Place attribution before the quote when possible
- Never use anonymous sourcing

ARTICLE LENGTH BY CATEGORY:
- Breaking news / Politics: 300–500 words
- Business analysis: 400–600 words
- Sports: 250–350 words
- Tech/startup features: 400–650 words with subheadings
- Diaspora: 200–300 words

STRICT RULES:
- Never copy sentences verbatim from source articles
- Always paraphrase and synthesise
- Never speculate beyond documented facts
- Use phrases like "however," "questions remain," "at this stage" for journalistic caution

OUTPUT FORMAT (JSON only, no other text):
{
  "headline": "Title Case headline, 12–18 words",
  "body": "Full article body in HTML paragraphs (<p> tags). Use <h3> for subheadings if needed.",
  "category": "Politics|Business|Sports|Technology|Culture|Diaspora|International|Nigeria",
  "seo_description": "SEO meta description, max 150 characters",
  "image_keywords": ["keyword1", "keyword2", "keyword3"],
  "source_attribution": "Source(s): [Publication Name(s)]"
}"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def latest_ranked_file() -> str:
    files = sorted(glob(os.path.join(TMP_DIR, "ranked_*.json")), reverse=True)
    if not files:
        raise FileNotFoundError(f"No ranked_*.json in {TMP_DIR}. Run rank_stories.py first.")
    return files[0]


def get_group_articles(conn, story_group_id: str) -> list[dict]:
    """Fetch all articles in a story group (for multi-source synthesis)."""
    c = conn.cursor()
    c.execute("""
        SELECT id, title, summary, source, url, raw_content
        FROM raw_articles
        WHERE story_group_id = ?
        ORDER BY LENGTH(raw_content) DESC
    """, (story_group_id,))
    return [dict(r) for r in c.fetchall()]


import re as _re

def _clean_text(raw: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = _re.sub(r"<[^>]+>", " ", raw or "")
    text = _re.sub(r"\s+", " ", text).strip()
    return text


def build_user_prompt(article: dict, group_articles: list[dict] | None = None) -> str:
    if group_articles and len(group_articles) > 1:
        sources_text = "\n\n".join(
            f"=== Source: {a['source']} ===\nTitle: {a['title']}\n{_clean_text(a.get('summary') or a.get('raw_content',''))}"
            for a in group_articles
        )
        return (
            f"Synthesise the following {len(group_articles)} news sources covering the same story "
            f"into one authoritative Atlantic Digest article. Combine unique details from each source.\n\n"
            f"{sources_text}"
        )
    else:
        content = _clean_text(article.get('summary') or article.get('raw_content', ''))
        # If RSS gave us no real content, ask Claude to write from the headline alone
        if len(content) < 80:
            content = (
                f"Note: Only the headline is available from the RSS feed. "
                f"Write a professional news article based on this headline and your knowledge of the topic."
            )
        return (
            f"Rewrite the following article in Atlantic Digest's editorial voice.\n\n"
            f"Source: {article['source']}\n"
            f"Original headline: {article['title']}\n\n"
            f"{content}"
        )


def call_claude(user_prompt: str) -> dict:
    response = CLIENT.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text_block = next(b for b in response.content if b.type == "text")
    raw = text_block.text.strip()

    # Strip markdown code fences if Claude wraps the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Extract JSON object if Claude added prose before/after it
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

    return json.loads(raw)


def save_draft(conn, article: dict, draft: dict, group_ids: list[int]) -> int:
    c = conn.cursor()
    c.execute("""
        INSERT INTO drafts
            (raw_article_ids, headline, body, category, seo_description,
             status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending_editor', ?)
    """, (
        json.dumps(group_ids),
        draft["headline"],
        draft["body"],
        draft["category"],
        draft.get("seo_description", ""),
        datetime.now(tz=timezone.utc).isoformat(),
    ))
    conn.commit()
    return c.lastrowid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate(dry_run: bool = False, feed_file: str | None = None):
    feed_file = feed_file or latest_ranked_file()
    print(f"Loading ranked stories from: {feed_file}\n")

    with open(feed_file, encoding="utf-8") as f:
        ranked = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    results = []

    for i, article in enumerate(ranked, 1):
        print(f"[{i}/{len(ranked)}] Generating draft: \"{article['title'][:60]}\"")

        # Gather articles for this story (single or multi-source)
        gid = article.get("story_group_id")
        group_articles = None
        group_ids = [article["id"]]

        if article.get("is_multi_source") and gid:
            group_articles = get_group_articles(conn, gid)
            group_ids = [a["id"] for a in group_articles] if group_articles else [article["id"]]
            # Deduplicate IDs
            seen = set()
            group_ids = [x for x in group_ids if not (x in seen or seen.add(x))]
            print(f"    → Multi-source synthesis from {len(group_articles)} articles")
        else:
            print(f"    → Single-source rewrite ({article['source']})")

        user_prompt = build_user_prompt(article, group_articles)

        try:
            draft = call_claude(user_prompt)
        except Exception as e:
            print(f"    ERROR calling Claude: {e}")
            continue

        results.append({
            "article_id": article["id"],
            "headline": draft.get("headline"),
            "category": draft.get("category"),
            "seo_description": draft.get("seo_description"),
            "image_keywords": draft.get("image_keywords", []),
            "source_attribution": draft.get("source_attribution", ""),
            "body_preview": (draft.get("body") or "")[:200] + "...",
        })

        if dry_run:
            print(f"\n{'='*60}")
            print(f"HEADLINE: {draft.get('headline')}")
            print(f"CATEGORY: {draft.get('category')}")
            print(f"SEO: {draft.get('seo_description')}")
            print(f"KEYWORDS: {draft.get('image_keywords')}")
            print(f"BODY (preview):\n{(draft.get('body') or '')[:400]}")
            print(f"{'='*60}\n")
        else:
            draft_id = save_draft(conn, article, draft, group_ids)
            print(f"    ✓ Draft saved (id={draft_id})")

    conn.close()

    # Save results summary to .tmp/
    if not dry_run:
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_path = os.path.join(TMP_DIR, f"drafts_summary_{date_str}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nDrafts summary saved to: {out_path}")

    print(f"\nDone. {len(results)}/{len(ranked)} drafts generated.")
    return results


if __name__ == "__main__":
    dry_run   = "--dry-run" in sys.argv
    feed_file = None
    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        feed_file = sys.argv[idx + 1]
    generate(dry_run=dry_run, feed_file=feed_file)
