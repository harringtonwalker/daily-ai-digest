#!/usr/bin/env python3
"""Fetch active AI repos from GitHub and output a short-term heat leaderboard."""

import argparse
import json
import math
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
PERIOD_LABELS = {"daily": "日榜", "weekly": "周榜", "monthly": "月榜"}
PERIOD_EMOJI = {"daily": "📅", "weekly": "📊", "monthly": "📈"}
QUERY_TERMS = ["ai", "llm", "gpt", "agent", "transformer", "diffusion", "rag", "ml"]
TOPIC_TERMS = ["artificial-intelligence", "llm", "generative-ai", "ai-agent"]


def gh_search(query, sort="stars", order="desc", per_page=30, token=None):
    params = urllib.parse.urlencode({
        "q": query, "sort": sort, "order": order, "per_page": per_page
    })
    url = f"https://api.github.com/search/repositories?{params}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-ai-trends"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("items", [])
    except Exception as e:
        print(f"[WARN] GitHub API error: {e}", file=sys.stderr)
        return []


def parse_github_dt(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def repo_heat_score(repo, now=None):
    now = now or datetime.now(timezone.utc)
    created_at = parse_github_dt(repo["created_at"])
    pushed_at = parse_github_dt(repo["pushed_at"])
    updated_at = parse_github_dt(repo["updated_at"])

    age_days = max((now - created_at).total_seconds() / 86400, 1.0)
    pushed_hours = max((now - pushed_at).total_seconds() / 3600, 1.0)
    updated_hours = max((now - updated_at).total_seconds() / 3600, 1.0)

    base = math.log1p(repo.get("stargazers_count", 0)) + 0.35 * math.log1p(repo.get("forks_count", 0))

    if age_days <= 7:
        freshness = 1.9
    elif age_days <= 30:
        freshness = 1.6
    elif age_days <= 90:
        freshness = 1.3
    elif age_days <= 365:
        freshness = 0.95
    else:
        freshness = 0.65

    activity = (1.8 / math.sqrt(pushed_hours + 1.0)) + (1.1 / math.sqrt(updated_hours + 1.0))
    return round(base * freshness * activity, 4)


def fetch_trending(period="weekly", limit=30, token=None):
    days = PERIOD_DAYS.get(period, 7)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    seen, results = set(), []
    search_specs = []

    for kw in QUERY_TERMS:
        query = f"{kw} in:name,description pushed:>={since} stars:>=10 fork:false archived:false"
        search_specs.append((query, "updated", 20))

    for topic in TOPIC_TERMS:
        query = f"topic:{topic} pushed:>={since} stars:>=10 fork:false archived:false"
        search_specs.append((query, "updated", 20))

    # 回补一小批 stars 排序结果，避免只按 updated 时漏掉强信号仓库。
    for kw in QUERY_TERMS[:4]:
        query = f"{kw} in:name,description pushed:>={since} stars:>=10 fork:false archived:false"
        search_specs.append((query, "stars", 10))

    for query, sort, per_page in search_specs:
        items = gh_search(query, sort=sort, per_page=per_page, token=token)
        for item in items:
            name = item["full_name"]
            if name in seen or item.get("archived") or item.get("disabled") or item.get("fork"):
                continue
            seen.add(name)
            item["heat_score"] = repo_heat_score(item, now=now)
            results.append(item)

    results.sort(
        key=lambda r: (r.get("heat_score", 0), r.get("stargazers_count", 0)),
        reverse=True,
    )
    return results[:limit]


def fmt_num(n):
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def format_output(repos, period):
    label = PERIOD_LABELS.get(period, period)
    emoji = PERIOD_EMOJI.get(period, "📊")
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{emoji} **GitHub AI 热度榜 — {label}**",
        "排序依据：近窗口活跃度 × 项目新鲜度（不是总 Stars 排行）",
        f"生成时间：{now}",
        "",
    ]

    for i, r in enumerate(repos, 1):
        stars = fmt_num(r["stargazers_count"])
        forks = fmt_num(r.get("forks_count", 0))
        lang = r.get("language") or "N/A"
        desc = r.get("description") or ""
        if len(desc) > 80:
            desc = desc[:77] + "..."
        name = r["full_name"]
        url = r["html_url"]

        lines.append(f"**#{i}** [{name}]({url})")
        lines.append(f"⭐ {stars} · 🍴 {forks} · {lang}")
        if desc:
            lines.append(f"_{desc}_")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="GitHub AI Trends")
    parser.add_argument("--period", choices=["daily", "weekly", "monthly"],
                        default="weekly")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    print(f"Fetching {args.period} AI trends...", file=sys.stderr)
    repos = fetch_trending(args.period, args.limit, args.token)

    if not repos:
        print("No repos found.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        fetched_at = datetime.now(timezone.utc).isoformat()   # L1: 可审计时间戳
        json.dump([{
            "rank": i, "name": r["full_name"], "url": r["html_url"],
            "stars": r["stargazers_count"], "forks": r.get("forks_count", 0),
            "language": r.get("language"), "description": r.get("description"),
            "heat_score": r.get("heat_score"),
            "created_at": r.get("created_at"),
            "pushed_at": r.get("pushed_at"),
            "updated_at": r.get("updated_at"),
            "fetched_at": fetched_at,   # L1: 每条记录带抓取时间
        } for i, r in enumerate(repos, 1)], sys.stdout, ensure_ascii=False, indent=2)
    else:
        print(format_output(repos, args.period))


if __name__ == "__main__":
    main()
