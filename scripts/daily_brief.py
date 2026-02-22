#!/usr/bin/env python3
"""
Daily AI Digest — 编排脚本

L0: fetch → Feishu card → Mem0
L1: 可审计（fetched_at + run_id + source metadata）
L2: 规则洞察（语言分布 + 技术热词 + HN最热 → 结论写 Mem0）
L3: Notion 待办（Top3 仓库自动写入 Daily AI Digest 数据库）
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta

import requests

# ── 常量 ──────────────────────────────────────────────────────

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_SECRET  = os.environ.get("FEISHU_SECRET", "")
MEM0_API_KEY   = os.environ.get("MEM0_API_KEY", "")
MEM0_ADD_URL   = "https://api.mem0.ai/v1/memories/"
SCRIPTS_DIR    = os.path.dirname(os.path.abspath(__file__))

CST    = timezone(timedelta(hours=8))
TODAY  = datetime.now(CST).strftime("%Y-%m-%d")
NOW    = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
RUN_ID = f"{TODAY}-{uuid.uuid4().hex[:8]}"  # L1: 每次运行唯一 ID，用于审计追溯

# L2 洞察关键词表
TOPIC_KEYWORDS = [
    "agent", "rag", "workflow", "fine-tun", "multimodal",
    "reasoning", "code", "vision", "embed", "inference",
    "mcp", "tool", "search", "voice", "image",
]


# ── 1. 数据获取 ──────────────────────────────────────────────

def get_github_trends(limit: int = 5) -> list:
    """调用 fetch_trends.py（L1: 返回含 fetched_at 的 JSON list）"""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_trends.py"),
         "--period", "daily", "--limit", str(limit), "--json"],
        capture_output=True, text=True, timeout=60,
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
    """调用 fetch_hn_ai_news.py（L1: 返回含 id + hn_url 的 JSON list）"""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_hn_ai_news.py"),
         "--limit", str(limit), "--json"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"[WARN] fetch_hn_news failed: {result.stderr}", file=sys.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except Exception as e:
        print(f"[WARN] parse HN JSON failed: {e}", file=sys.stderr)
        return []


# ── 2. L2 规则洞察 ───────────────────────────────────────────

def fmt_num(n) -> str:
    n = int(n) if n else 0
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def generate_insights(repos: list, news: list) -> tuple:
    """
    生成 3 条规则推断的洞察 + 1 条建议行动。
    纯规则，无 LLM 调用，零额外延迟。
    Returns: (insights: list[str], action: str)
    """
    insights = []

    # 洞察 1：语言格局
    langs = [r.get("language") for r in repos if r.get("language")]
    if langs:
        top_lang, top_count = Counter(langs).most_common(1)[0]
        others = len(repos) - top_count
        insights.append(
            f"语言格局：Top{len(repos)} 中 **{top_lang}** 占 {top_count} 席"
            + (f"，其余 {others} 席多语言并存" if others > 0 else "，一语独大")
        )

    # 洞察 2：技术热词聚焦方向
    all_text = " ".join(
        (r.get("description") or "") + " " + r.get("name", "")
        for r in repos
    ).lower()
    hot = [kw for kw in TOPIC_KEYWORDS if kw in all_text]
    if hot:
        insights.append(f"热门方向：{'  ·  '.join(hot[:4])}")

    # 洞察 3：HN 最热信号
    if news:
        top = news[0]
        insights.append(
            f"HN 最热：「{top['title'][:38]}…」🔺{top['score']}分·💬{top['comments']}条"
        )
    elif repos:
        top_repo = repos[0]
        insights.append(
            f"GitHub #1：{top_repo['name']} ⭐{fmt_num(top_repo['stars'])}，今日最强信号"
        )

    # 建议行动
    if repos and hot:
        action = f"建议深入：{repos[0]['name']}（#{repos[0]['rank']}，主题 {hot[0]}）"
    elif repos:
        action = f"建议关注：{repos[0]['name']}（⭐{fmt_num(repos[0]['stars'])}，今日 #1）"
    else:
        action = "数据不足，建议次日补采"

    return insights, action


# ── 3. 飞书卡片（L2: 含洞察 section）────────────────────────

def build_feishu_card(repos: list, news: list, insights: list, action: str) -> dict:
    """构建飞书 Interactive Card（含 GitHub 表格 + HN 新闻 + L2 洞察）"""

    # GitHub 趋势表格（L1: url 可追溯）
    gh_rows = "\n".join(
        f"| {r['rank']} | [{r['name']}]({r['url']}) | "
        f"⭐{fmt_num(r['stars'])} | {r.get('language') or 'N/A'} | "
        f"{(r.get('description') or '')[:38]} |"
        for r in repos
    )
    gh_md = (
        "**🔥 GitHub AI 热门项目 Top 5（日榜）**\n\n"
        "| # | 仓库 | Stars | 语言 | 描述 |\n"
        "|---|---|---|---|---|\n" + gh_rows
    ) if repos else "（今日暂无 GitHub 数据）"

    # HN 新闻（L1: hn_url 可追溯）
    hn_lines = "\n".join(
        f"{i}. [{n['title']}]({n['hn_url']}) — 🔺{n['score']}分 · 💬{n['comments']}条"
        for i, n in enumerate(news, 1)
    )
    hn_md = (
        "**📰 AI 今日大事 (HackerNews)**\n\n" + hn_lines
    ) if news else "（今日暂无 HN AI 新闻）"

    # L2 洞察
    ins_lines = "\n".join(f"• {s}" for s in insights) if insights else "（洞察生成中）"
    insight_md = f"**🔍 今日洞察**\n\n{ins_lines}\n\n💡 {action}"

    # Footer（L1: run_id 可追溯）
    footer_md = (
        f"📝 已写入 Notion · 🧠 已存 Mem0\n"
        f"run_id: `{RUN_ID}` · {NOW}"
    )

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 Daily AI Digest — {TODAY}",
                },
                "template": "blue",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": gh_md}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": hn_md}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": insight_md}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": footer_md}},
            ],
        },
    }


def send_feishu(payload: dict) -> bool:
    """发送飞书消息，可选签名校验（FEISHU_SECRET）"""
    if not FEISHU_WEBHOOK:
        print("[SKIP] FEISHU_WEBHOOK_URL not set", file=sys.stderr)
        return False

    if FEISHU_SECRET:
        # 飞书官方算法：key = f"{timestamp}\n{secret}" 编码为 bytes，msg = b""
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
        print(f"❌ 飞书发送失败: {resp.status_code} {resp.text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ 飞书异常: {e}", file=sys.stderr)
        return False


# ── 4. Mem0（L1: run_id 元数据 + L2: 存洞察结论）─────────────

def store_to_mem0(repos: list, news: list, insights: list, action: str) -> bool:
    """
    存储核心结论到 Mem0（不存原始全文）。
    L1: 携带 run_id + date 元数据，支持按日回溯。
    L2: 存洞察文本，而非 Top5 全量数据。
    """
    if not MEM0_API_KEY:
        print("[SKIP] MEM0_API_KEY not set", file=sys.stderr)
        return False

    top3_repos = ", ".join(
        f"{r['name']}(⭐{fmt_num(r['stars'])})" for r in repos[:3]
    ) or "无"
    top3_news = "; ".join(n["title"][:38] for n in news[:3]) or "无"
    insight_text = "; ".join(insights) if insights else "无"

    content = (
        f"{TODAY} GitHub AI日榜Top3: {top3_repos}。"
        f"HN热议: {top3_news}。"
        f"洞察: {insight_text}。"
        f"建议: {action}"
    )

    try:
        resp = requests.post(
            MEM0_ADD_URL,
            json={
                "messages": [{"role": "user", "content": content}],
                "user_id": "gonbling",
                "metadata": {
                    "source": "daily-ai-digest",
                    "date": TODAY,
                    "run_id": RUN_ID,      # L1: 审计追溯
                },
            },
            headers={
                "Authorization": f"Token {MEM0_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            print("✅ Mem0 存储成功")
            return True
        print(f"⚠️ Mem0 存储失败: {resp.status_code} {resp.text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠️ Mem0 异常: {e}", file=sys.stderr)
        return False


# ── 5. L3: Notion 待办 ──────────────────────────────────────

def log_to_notion(repos: list) -> bool:
    """将 Top3 仓库写入 Notion 数据库（L3）。"""
    try:
        import notion_tracker
        return notion_tracker.log_top_repos(repos, top_n=3, run_id=RUN_ID)
    except ImportError:
        print("[WARN] notion_tracker.py not found", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠️ Notion 异常: {e}", file=sys.stderr)
        return False


# ── 主流程 ───────────────────────────────────────────────────

def main():
    print(f"=== Daily AI Digest {TODAY} | run_id={RUN_ID} ===")

    print("📡 获取 GitHub AI 趋势...")
    repos = get_github_trends(limit=5)
    print(f"   → {len(repos)} 个仓库")

    print("📡 获取 HackerNews AI 新闻...")
    news = get_hn_news(limit=5)
    print(f"   → {len(news)} 条新闻")

    if not repos and not news:
        print("❌ 两个数据源均为空，终止执行", file=sys.stderr)
        sys.exit(1)

    print("🔍 生成洞察（L2）...")
    insights, action = generate_insights(repos, news)
    for ins in insights:
        print(f"   • {ins}")
    print(f"   💡 {action}")

    print("📨 发送飞书卡片...")
    card = build_feishu_card(repos, news, insights, action)
    send_feishu(card)

    print("🧠 存入 Mem0 长期记忆（L1+L2）...")
    store_to_mem0(repos, news, insights, action)

    print("📝 写入 Notion 数据库（L3）...")
    log_to_notion(repos)

    print(f"✅ Daily AI Digest 完成 | run_id={RUN_ID}")


if __name__ == "__main__":
    main()
