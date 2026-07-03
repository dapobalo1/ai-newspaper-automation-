"""
fetch_rss_feeds.py
Fetches articles from all configured RSS sources, filters by topic keywords,
enforces a 24-hour staleness cutoff, and saves results to .tmp/.

Usage: python3 tools/fetch_rss_feeds.py
Output: .tmp/raw_feed_YYYY-MM-DD.json
"""

import json
import os
import hashlib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    import feedparser
    import requests
except ImportError:
    raise SystemExit("Missing dependencies: pip3 install feedparser requests")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AtlanticDigestBot/1.0)"}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOURCES = [
    {"name": "Vanguard Nigeria",    "url": "https://www.vanguardngr.com/feed/"},
    {"name": "Daily Trust",         "url": "https://dailytrust.com/feed/"},
    {"name": "Kyiv Independent",    "url": "https://kyivindependent.com/feed/rss/"},
    {"name": "Al Jazeera",          "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "Premium Times",       "url": "https://www.premiumtimesng.com/feed/"},
    {"name": "BBC Africa",          "url": "https://feeds.bbci.co.uk/news/world/africa/rss.xml"},
    {"name": "TechCabal",           "url": "https://techcabal.com/feed/"},
    {"name": "ThisDay Live",        "url": "https://www.thisdaylive.com/index.php/feed/"},
    {"name": "Sahara Reporters",    "url": "https://saharareporters.com/rss.xml"},
    {"name": "Gazette Nigeria",     "url": "https://gazettengr.com/feed/"},
]

TOPIC_KEYWORDS = {
    "Politics":  ["election", "elections", "government", "senate", "minister", "president",
                  "inec", "policy", "court", "icpc", "tribunal", "parliament", "governor",
                  "legislature", "democracy", "constitution", "tinubu", "obi", "atiku"],
    "Business":  ["gdp", "economy", "naira", "oil", "investment", "startup", "cbn",
                  "inflation", "refinery", "gantry", "trade", "market", "stocks", "budget",
                  "revenue", "export", "import", "dangote", "nnpc", "opec"],
    "Sports":    ["super eagles", "afcon", "premier league", "fifa", "olympics", "football",
                  "basketball", "tennis", "athletics", "world cup", "champions league",
                  "nba", "sport", "sports", "medal", "tournament", "championship"],
    "Technology": ["artificial intelligence", "ai", "automation", "fintech", "startup",
                   "cybersecurity", "deepfake", "tech", "software", "app", "digital",
                   "blockchain", "crypto", "data", "cloud", "5g", "robot"],
    "Culture":   ["nollywood", "afrobeats", "fashion", "diaspora", "festival", "entertainment",
                  "music", "film", "art", "culture", "dance", "award", "grammy", "oscars"],
    "Nigeria":   ["lagos", "abuja", "nigeria", "nigerian", "nnpc", "delta", "kano",
                  "ogun", "rivers", "enugu", "kaduna", "borno", "anambra"],
    "Diaspora":  ["diaspora", "african union", "ecowas", "immigration", "african",
                  "africa", "africans abroad", "black", "emigration", "migration"],
    "International": ["ukraine", "russia", "iran", "israel", "gaza", "usa", "china",
                      "europe", "nato", "united nations", "war", "conflict", "geopolit",
                      "sanctions", "treaty", "global", "world"],
}

MAX_AGE_HOURS = int(os.getenv("ARTICLE_MAX_AGE_HOURS", "24"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def parse_published(entry) -> datetime | None:
    """Return an aware datetime or None."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                import time
                ts = time.mktime(val)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
    # fallback: try published string
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except Exception:
            pass
    return None


def categorise(title: str, summary: str) -> str:
    """Return the best matching category or 'General'."""
    text = (title + " " + summary).lower()
    scores = {}
    for cat, kws in TOPIC_KEYWORDS.items():
        scores[cat] = sum(1 for kw in kws if kw in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"


def is_relevant(title: str, summary: str) -> bool:
    """True if the article matches at least one keyword."""
    text = (title + " " + summary).lower()
    return any(kw in text for kws in TOPIC_KEYWORDS.values() for kw in kws)


# ---------------------------------------------------------------------------
# Main fetch
# ---------------------------------------------------------------------------

def fetch_all() -> list[dict]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    articles = []
    seen_hashes = set()

    for source in SOURCES:
        print(f"  Fetching {source['name']} ...")
        try:
            resp = requests.get(source["url"], headers=HEADERS, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        for entry in feed.entries:
            url = getattr(entry, "link", None)
            if not url:
                continue

            h = url_hash(url)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            title   = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "").strip()

            # Staleness check
            pub_dt = parse_published(entry)
            if pub_dt and pub_dt < cutoff:
                continue

            # Relevance filter
            if not is_relevant(title, summary):
                continue

            articles.append({
                "url":          url,
                "url_hash":     h,
                "source":       source["name"],
                "title":        title,
                "summary":      summary,
                "published_at": pub_dt.isoformat() if pub_dt else None,
                "fetched_at":   datetime.now(tz=timezone.utc).isoformat(),
                "category":     categorise(title, summary),
                "raw_content":  summary,  # RSS summaries; full content added in v2 via scraping
            })

        source_count = len([a for a in articles if a["source"] == source["name"]])
        print(f"    → {source_count} relevant articles")

    return articles


def save_to_tmp(articles: list[dict]) -> str:
    tmp_dir = os.path.join(os.path.dirname(__file__), "..", ".tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(tmp_dir, f"raw_feed_{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    return path


if __name__ == "__main__":
    print(f"Fetching RSS feeds (max age: {MAX_AGE_HOURS}h) ...")
    articles = fetch_all()
    path = save_to_tmp(articles)
    print(f"\nTotal relevant articles: {len(articles)}")
    print(f"Saved to: {path}")
