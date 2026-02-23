#!/usr/bin/env python3
"""
L3: Notion 集成 — 将每日 Top AI 仓库写入 Notion 数据库。
首次运行时自动查找或创建 "Daily AI Digest" 数据库。
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_API = "https://api.notion.com/v1"
DB_NAME = "Daily AI Digest"

CST = timezone(timedelta(hours=8))
TODAY = datetime.now(CST).strftime("%Y-%m-%d")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def search_database() -> str | None:
    """搜索名为 'Daily AI Digest' 的数据库，返回 ID 或 None。"""
    try:
        resp = requests.post(
            f"{NOTION_API}/search",
            headers=_headers(),
            json={
                "query": DB_NAME,
                "filter": {"value": "database", "property": "object"},
            },
            timeout=15,
        )
        for r in resp.json().get("results", []):
            title_list = r.get("title", [])
            if title_list and DB_NAME.lower() in title_list[0].get("plain_text", "").lower():
                return r["id"]
    except Exception as e:
        print(f"[WARN] Notion search failed: {e}", file=sys.stderr)
    return None


def find_parent_page() -> str | None:
    """找到任意可访问的 Notion 页面作为数据库父节点。"""
    try:
        resp = requests.post(
            f"{NOTION_API}/search",
            headers=_headers(),
            json={"filter": {"value": "page", "property": "object"}, "page_size": 5},
            timeout=15,
        )
        pages = resp.json().get("results", [])
        # 优先选非数据库子页面
        for p in pages:
            if p.get("parent", {}).get("type") == "workspace":
                return p["id"]
        if pages:
            return pages[0]["id"]
    except Exception as e:
        print(f"[WARN] Notion find_parent failed: {e}", file=sys.stderr)
    return None


def create_database(parent_page_id: str) -> str | None:
    """在父页面下创建 'Daily AI Digest' 数据库，返回数据库 ID。"""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": DB_NAME}}],
        "properties": {
            "Repo": {"title": {}},
            "Stars": {"number": {"format": "number_with_commas"}},
            "Language": {"rich_text": {}},
            "Description": {"rich_text": {}},
            "URL": {"url": {}},
            "Date": {"date": {}},
            "Rank": {"number": {}},
            "Run ID": {"rich_text": {}},
        },
    }
    try:
        resp = requests.post(
            f"{NOTION_API}/databases", headers=_headers(), json=payload, timeout=15
        )
        db = resp.json()
        if "id" in db:
            print(f"✅ Notion: 创建数据库 '{DB_NAME}' id={db['id'][:8]}…")
            return db["id"]
        else:
            print(f"[WARN] Notion create_database failed: {db}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Notion create_database exception: {e}", file=sys.stderr)
    return None


def add_repo_row(db_id: str, repo: dict, run_id: str = "") -> bool:
    """向数据库插入一行仓库记录。"""
    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Repo": {
                "title": [{"text": {"content": repo["name"]}}]
            },
            "Stars": {"number": int(repo.get("stars", 0))},
            "Language": {
                "rich_text": [{"text": {"content": repo.get("language") or "N/A"}}]
            },
            "Description": {
                "rich_text": [
                    {"text": {"content": (repo.get("description") or "")[:200]}}
                ]
            },
            "URL": {"url": repo.get("url", "")},
            "Date": {"date": {"start": TODAY}},
            "Rank": {"number": int(repo.get("rank", 0))},
            "Run ID": {"rich_text": [{"text": {"content": run_id}}]},
        },
    }
    try:
        resp = requests.post(
            f"{NOTION_API}/pages", headers=_headers(), json=payload, timeout=15
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[WARN] Notion add_row failed for {repo.get('name')}: {e}", file=sys.stderr)
        return False


def log_top_repos(repos: list, top_n: int = 3, run_id: str = "") -> bool:
    """
    将 Top N 仓库写入 Notion 数据库。
    优先使用 NOTION_DATABASE_ID 环境变量（直接定位，跳过 search/create）。
    未设置时自动查找或创建 'Daily AI Digest' 数据库。
    返回是否成功写入至少一条。
    """
    if not NOTION_API_KEY:
        print("[SKIP] NOTION_API_KEY not set", file=sys.stderr)
        return False

    # 0. 优先使用显式指定的数据库 ID（稳定、无需授权搜索）
    db_id = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if db_id:
        print(f"📝 Notion: 使用指定数据库 ID={db_id[:8]}…")
    else:
        # 1. 查找现有数据库
        db_id = search_database()

        # 2. 找不到则自动创建
        if not db_id:
            parent_id = find_parent_page()
            if not parent_id:
                print("[WARN] Notion: 未找到可用父页面，无法创建数据库", file=sys.stderr)
                return False
            db_id = create_database(parent_id)
            if not db_id:
                return False

    # 3. 写入 Top N 记录
    ok = 0
    for repo in repos[:top_n]:
        if add_repo_row(db_id, repo, run_id=run_id):
            ok += 1

    print(f"✅ Notion: 写入 {ok}/{min(top_n, len(repos))} 条记录")
    return ok > 0
