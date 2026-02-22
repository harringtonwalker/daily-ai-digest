#!/usr/bin/env python3
"""
Fetch AI-related news from HackerNews Top Stories.
No API key required. Filters by AI keywords.
"""
import json
import sys
import urllib.request

HN_TOPSTORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HN_LINK = "https://news.ycombinator.com/item?id={}"

AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "openai", "anthropic", "gemini",
    "agent", "model", "deepseek", "mistral", "llama", "diffusion",
    "neural", "machine learning", "deep learning", "chatgpt",
]


def fetch_url(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[WARN] fetch failed: {url} — {e}", file=sys.stderr)
        return None


def is_ai_related(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in AI_KEYWORDS)


def fetch_hn_ai_news(limit: int = 5, scan: int = 200) -> list:
    ids = fetch_url(HN_TOPSTORIES)
    if not ids:
        return []

    results = []
    for story_id in ids[:scan]:
        if len(results) >= limit:
            break
        item = fetch_url(HN_ITEM.format(story_id))
        if not item:
            continue
        title = item.get("title", "")
        if not is_ai_related(title):
            continue
        results.append({
            "title": title,
            "url": item.get("url") or HN_LINK.format(story_id),
            "hn_url": HN_LINK.format(story_id),
            "score": item.get("score", 0),
            "comments": item.get("descendants", 0),
            "id": story_id,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch AI news from HackerNews")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    news = fetch_hn_ai_news(limit=args.limit)
    if args.json:
        json.dump(news, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        for i, n in enumerate(news, 1):
            print(f"{i}. [{n['score']}pts/{n['comments']}cmts] {n['title']}")
            print(f"   {n['hn_url']}")
