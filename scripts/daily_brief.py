#!/usr/bin/env python3
"""
Daily AI Digest — 编排脚本
调用 fetch_trends.py + fetch_hn_ai_news.py，发送飞书卡片，存入 Mem0。
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_SECRET  = os.environ.get("FEISHU_SECRET", "")   # 可选：签名校验
MEM0_API_KEY   = os.environ.get("MEM0_API_KEY", "")
MEM0_ADD_URL   = "https://api.mem0.ai/v1/memories/"    # 官方文档 v1 端点
SCRIPTS_DIR    = os.path.dirname(os.path.abspath(__file__))

CST = timezone(timedelta(hours=8))
TODAY = datetime.now(CST).strftime("%Y-%m-%d")
NOW   = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")


# ── 1. 数据获取 ──────────────────────────────────────────────

def get_github_trends(limit: int = 5) -> list:
    """调用 fetch_trends.py，返回 JSON list"""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_trends.py"),
         "--period", "daily", "--limit", str(limit), "--json"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"[WARN] fetch_trends failed: {result.stderr}", file=sys.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except Exception as e:
        print(f"[WARN] parse trends JSON failed: {e}", file=sys.stderr)
        return []


def get_hn_news(limit: int = 5) -> list:
    """调用 fetch_hn_ai_news.py，返回 JSON list"""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_hn_ai_news.py"),
         "--limit", str(limit), "--json"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"[WARN] fetch_hn_news failed: {result.stderr}", file=sys.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except Exception as e:
        print(f"[WARN] parse HN JSON failed: {e}", file=sys.stderr)
        return []


# ── 2. 飞书卡片 ──────────────────────────────────────────────

def fmt_num(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def build_feishu_card(repos: list, news: list) -> dict:
    """构建飞书 Interactive Card payload"""

    # GitHub 趋势表格
    gh_rows = "\n".join(
        f"| {r['rank']} | [{r['name']}]({r['url']}) | "
        f"⭐{fmt_num(r['stars'])} | {r.get('language') or 'N/A'} | "
        f"{(r.get('description') or '')[:40]} |"
        for r in repos
    )
    gh_md = (
        "**🔥 GitHub AI 热门项目 Top 5（日榜）**\n\n"
        "| # | 仓库 | Stars | 语言 | 描述 |\n"
        "|---|---|---|---|---|\n"
        + gh_rows
    ) if repos else "（今日暂无 GitHub 数据）"

    # HackerNews AI 新闻
    hn_lines = "\n".join(
        f"{i}. [{n['title']}]({n['hn_url']}) — 🔺{n['score']}分 · 💬{n['comments']}条"
        for i, n in enumerate(news, 1)
    )
    hn_md = (
        "**📰 AI 今日大事 (HackerNews)**\n\n" + hn_lines
    ) if news else "（今日暂无 HN AI 新闻）"

    footer_md = f"✅ 已存入 Mem0 长期记忆 · 生成时间：{NOW}"

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 Daily AI Digest — {TODAY}"
                },
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": gh_md}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": hn_md}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": footer_md}},
            ]
        }
    }


def send_feishu(payload: dict) -> bool:
    """发送飞书消息，可选签名校验（FEISHU_SECRET）"""
    if not FEISHU_WEBHOOK:
        print("[SKIP] FEISHU_WEBHOOK_URL not set", file=sys.stderr)
        return False

    if FEISHU_SECRET:
        # 飞书官方签名算法：
        # key = f"{timestamp}\n{secret}" 编码为 bytes，msg = b""
        # 参考：https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
        ts = str(int(time.time()))
        key = f"{ts}\n{FEISHU_SECRET}".encode("utf-8")
        sig = base64.b64encode(
            hmac.new(key, b"", digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        payload["timestamp"] = ts
        payload["sign"] = sig

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("code", 0) == 0:
            print("✅ 飞书发送成功")
            return True
        else:
            print(f"❌ 飞书发送失败: {resp.status_code} {resp.text}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"❌ 飞书异常: {e}", file=sys.stderr)
        return False


# ── 3. Mem0 存储 ─────────────────────────────────────────────

def store_to_mem0(repos: list, news: list) -> bool:
    """仅存核心结论（Top 3 一句话摘要），不存完整卡片内容"""
    if not MEM0_API_KEY:
        print("[SKIP] MEM0_API_KEY not set", file=sys.stderr)
        return False

    top3_repos = ", ".join(
        f"{r['name']}(⭐{fmt_num(r['stars'])})" for r in repos[:3]
    ) or "无"
    top3_news = "; ".join(n['title'][:40] for n in news[:3]) or "无"
    content = (
        f"{TODAY} GitHub AI日榜Top3: {top3_repos}。"
        f"HN AI热议: {top3_news}。"
    )

    try:
        resp = requests.post(
            MEM0_ADD_URL,
            json={
                "messages": [{"role": "user", "content": content}],
                "user_id": "gonbling",
                "metadata": {"source": "daily-ai-digest", "date": TODAY}
            },
            headers={
                "Authorization": f"Token {MEM0_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=15
        )
        if resp.status_code in (200, 201):
            print("✅ Mem0 存储成功")
            return True
        else:
            print(f"⚠️ Mem0 存储失败: {resp.status_code} {resp.text}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"⚠️ Mem0 异常: {e}", file=sys.stderr)
        return False


# ── 主流程 ───────────────────────────────────────────────────

def main():
    print(f"=== Daily AI Digest {TODAY} ===")

    print("📡 获取 GitHub AI 趋势...")
    repos = get_github_trends(limit=5)
    print(f"   → {len(repos)} 个仓库")

    print("📡 获取 HackerNews AI 新闻...")
    news = get_hn_news(limit=5)
    print(f"   → {len(news)} 条新闻")

    if not repos and not news:
        print("❌ 两个数据源均为空，终止执行", file=sys.stderr)
        sys.exit(1)

    print("📨 发送飞书卡片...")
    card = build_feishu_card(repos, news)
    send_feishu(card)

    print("🧠 存入 Mem0 长期记忆...")
    store_to_mem0(repos, news)

    print("✅ Daily AI Digest 完成")


if __name__ == "__main__":
    main()
