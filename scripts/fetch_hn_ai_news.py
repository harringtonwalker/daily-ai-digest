#!/usr/bin/env python3
"""
Fetch AI-related stories from HackerNews Top Stories.
No API key required. Uses stricter matching to avoid obvious false positives.
"""
import json
import re
import sys
import urllib.request
from urllib.parse import urlparse

HN_TOPSTORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HN_LINK = "https://news.ycombinator.com/item?id={}"

STRONG_TITLE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bllms?\b",
        r"\bgpt(?:-\d+)?\b",
        r"\brag\b",
        r"\bchatgpt\b",
        r"\bopenai\b",
        r"\banthropic\b",
        r"\bclaude\b",
        r"\bgemini\b",
        r"\bdeepseek\b",
        r"\bmistral\b",
        r"\bllama\b",
        r"\bdiffusion\b",
        r"\btransformers?\b",
        r"\bmultimodal\b",
        r"\bneural\b",
        r"\binference\b",
        r"\breasoning\b",
        r"\bmachine learning\b",
        r"\bdeep learning\b",
        r"\blanguage models?\b",
        r"\bfoundation models?\b",
        r"\bcoding agents?\b",
        r"\bai agents?\b",
    ]
]

SUPPORTING_TITLE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bai[- ]proof\b",
        r"\bmcp\b",
    ]
]

MODEL_TITLE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bmoe\b",
        r"\bparameter models?\b",
        r"\bdeep learning\b",
        r"\bmodels?\b",
    ]
]

AI_HOST_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"(^|\.)openai\.com$",
        r"(^|\.)anthropic\.com$",
        r"(^|\.)huggingface\.co$",
        r"(^|\.)replicate\.com$",
        r"(^|\.)mistral\.ai$",
        r"(^|\.)cohere\.com$",
        r"(^|\.)deepseek\.com$",
        r"(^|\.)ollama\.com$",
        r"(^|\.)vllm\.ai$",
        r"(^|\.)perplexity\.ai$",
    ]
]


def fetch_url(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[WARN] fetch failed: {url} — {e}", file=sys.stderr)
        return None


def ai_relevance_score(title: str, url: str = "") -> int:
    title = title or ""
    host = urlparse(url).netloc.lower() if url else ""
    score = 0

    score += 3 * sum(1 for pattern in STRONG_TITLE_PATTERNS if pattern.search(title))
    score += 3 * sum(1 for pattern in MODEL_TITLE_PATTERNS if pattern.search(title))
    score += 2 * sum(1 for pattern in SUPPORTING_TITLE_PATTERNS if pattern.search(title))
    score += 2 * sum(1 for pattern in AI_HOST_PATTERNS if pattern.search(host))
    return score


def is_ai_related(title: str, url: str = "") -> bool:
    return ai_relevance_score(title, url) >= 3


def fetch_hn_ai_news(limit: int = 5, scan: int = 200) -> list:
    ids = fetch_url(HN_TOPSTORIES)
    if not ids:
        return []

    results = []
    for story_id in ids[:scan]:
        item = fetch_url(HN_ITEM.format(story_id))
        if not item:
            continue
        title = item.get("title", "")
        url = item.get("url") or HN_LINK.format(story_id)
        if not is_ai_related(title, url):
            continue
        relevance_score = ai_relevance_score(title, url)
        results.append({
            "title": title,
            "url": url,
            "hn_url": HN_LINK.format(story_id),
            "score": item.get("score", 0),
            "comments": item.get("descendants", 0),
            "relevance_score": relevance_score,
            "id": story_id,
        })

    results.sort(key=lambda x: (x["relevance_score"], x["score"], x["comments"]), reverse=True)
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
