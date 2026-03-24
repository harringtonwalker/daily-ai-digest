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
THEME_KEYWORDS = {
    "agent": ["agent", "agentic", "tool", "assistant", "automation"],
    "workflow": ["workflow", "orchestration", "pipeline", "n8n", "langflow"],
    "multimodal": ["vision", "image", "video", "voice", "multimodal"],
    "local": ["local", "offline", "on-device", "laptop", "tinybox"],
    "research": ["reasoning", "inference", "moe", "benchmark", "model"],
    "infra": ["rag", "search", "embed", "retrieval", "vector", "memory"],
}
THEME_LABELS = {
    "agent": "智能体 / 工具调用",
    "workflow": "工作流编排",
    "multimodal": "多模态",
    "local": "本地推理",
    "research": "模型研究",
    "infra": "RAG / 检索基础设施",
}


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


def parse_iso_utc(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def repo_age_days(repo: dict) -> int:
    created_at = parse_iso_utc(repo.get("created_at"))
    if not created_at:
        return 9999
    return max(int((datetime.now(timezone.utc) - created_at).total_seconds() // 86400), 0)


def detect_themes(texts) -> Counter:
    joined = " ".join(texts).lower()
    counts = Counter()
    for theme, keywords in THEME_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in joined)
        if hits:
            counts[theme] = hits
    return counts


def theme_labels(counter: Counter, top_n: int = 2) -> list:
    return [THEME_LABELS[key] for key, _ in counter.most_common(top_n)]


def pick_action_repo(repos: list, repo_themes: Counter, news_themes: Counter) -> tuple:
    if not repos:
        return None, "数据不足"

    overlap = [theme for theme, _ in repo_themes.most_common() if theme in news_themes]
    if overlap:
        theme = overlap[0]
        themed = [
            repo for repo in repos
            if theme in detect_themes([repo.get("name", ""), repo.get("description") or ""])
        ]
        if themed:
            themed.sort(key=lambda repo: (-float(repo.get("heat_score") or 0), repo_age_days(repo)))
            return themed[0], f"GitHub 与 HN 同时升温：{THEME_LABELS[theme]}"

    fresh = [repo for repo in repos if repo_age_days(repo) <= 30]
    if fresh:
        fresh.sort(key=lambda repo: (-float(repo.get("heat_score") or 0), repo_age_days(repo)))
        return fresh[0], "近30天新项目，且近24h热度靠前"

    ranked = sorted(
        repos,
        key=lambda repo: (-float(repo.get("heat_score") or 0), -int(repo.get("stars") or 0)),
    )
    return ranked[0], "近24h综合热度最高"


def shorten(text: str, limit: int = 42) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def repo_badges(repo: dict) -> list:
    badges = []
    age = repo_age_days(repo)
    if age <= 7:
        badges.append("7天内新项目")
    elif age <= 30:
        badges.append("30天内新项目")

    labels = theme_labels(
        detect_themes([repo.get("name", ""), repo.get("description") or ""]),
        top_n=1,
    )
    badges.extend(labels)
    return badges[:2]


def build_summary_md(repos: list, news: list, action: str) -> str:
    bullets = []
    repo_themes = detect_themes([f"{repo.get('name', '')} {repo.get('description') or ''}" for repo in repos])
    news_themes = detect_themes([item.get("title", "") for item in news])

    repo_labels = theme_labels(repo_themes, top_n=2)
    if repo_labels:
        bullets.append(f"主线：{' / '.join(repo_labels)}")

    if repos:
        fresh = sum(repo_age_days(repo) <= 30 for repo in repos)
        bullets.append(f"新项目：Top{len(repos)} 中有 {fresh} 个近30天项目")

    overlap = [THEME_LABELS[key] for key, _ in repo_themes.most_common() if key in news_themes]
    if overlap:
        bullets.append(f"共振：GitHub 与 HN 同时指向 {overlap[0]}")
    elif news:
        bullets.append(f"头条：{shorten(news[0]['title'], 34)}")

    bullets.append(action.replace("建议深入：", "动作：").replace("建议关注：", "动作："))
    return "**🧭 今日摘要**\n\n" + "\n".join(f"• {bullet}" for bullet in bullets[:4])


def build_repo_watch_md(repos: list, detail_limit: int = 3) -> str:
    if not repos:
        return "（今日暂无 GitHub 数据）"

    lines = ["**🔥 GitHub 重点项目**", ""]
    for repo in repos[:detail_limit]:
        meta = [f"⭐{fmt_num(repo['stars'])}", repo.get("language") or "N/A"]
        meta.extend(repo_badges(repo))
        lines.append(f"{repo['rank']}. [{repo['name']}]({repo['url']}) — {' · '.join(meta)}")
        desc = shorten(repo.get("description") or "", 46)
        if desc:
            lines.append(f"   _{desc}_")
        lines.append("")

    if len(repos) > detail_limit:
        rest = " / ".join(f"#{repo['rank']} {repo['name']}" for repo in repos[detail_limit:detail_limit + 2])
        lines.append(f"补充关注：{rest}")

    return "\n".join(lines).strip()


def build_news_watch_md(news: list, detail_limit: int = 3) -> str:
    if not news:
        return "（今日暂无 HN AI 新闻）"

    lines = ["**📰 HN 重点新闻**", ""]
    for idx, item in enumerate(news[:detail_limit], 1):
        article_url = item.get("url") or item["hn_url"]
        lines.append(
            f"{idx}. [{item['title']}]({article_url}) · [HN讨论]({item['hn_url']}) — 🔺{item['score']}分 · 💬{item['comments']}条"
        )
    if len(news) > detail_limit:
        rest = " / ".join(shorten(item["title"], 18) for item in news[detail_limit:detail_limit + 2])
        lines.extend(["", f"补充阅读：{rest}"])
    return "\n".join(lines)


def build_judgement_md(insights: list) -> str:
    if not insights:
        return "（今日暂无判断）"
    return "**📌 今日判断**\n\n" + "\n".join(f"• {item}" for item in insights)


def generate_insights(repos: list, news: list) -> tuple:
    """
    生成 3 条规则推断的洞察 + 1 条建议行动。
    纯规则，无 LLM 调用，零额外延迟。
    Returns: (insights: list[str], action: str)
    """
    insights = []

    repo_texts = [f"{repo.get('name', '')} {repo.get('description') or ''}" for repo in repos]
    repo_themes = detect_themes(repo_texts)
    news_themes = detect_themes([item.get("title", "") for item in news])

    # 洞察 1：榜单新鲜度
    if repos:
        fresh = sum(repo_age_days(repo) <= 30 for repo in repos)
        if fresh:
            insights.append(f"新鲜度：Top{len(repos)} 中有 {fresh} 个项目创建于近30天，发现价值高于纯总星榜")
        else:
            insights.append(f"成熟度：Top{len(repos)} 暂无近30天新项目，今天更适合看“活跃变化”而不是追新")

    # 洞察 2：GitHub 主线主题
    repo_labels = theme_labels(repo_themes, top_n=2)
    if repo_labels:
        insights.append(f"GitHub 主线：{' / '.join(repo_labels)}")
    elif repos:
        langs = [repo.get("language") for repo in repos if repo.get("language")]
        if langs:
            top_lang, top_count = Counter(langs).most_common(1)[0]
            insights.append(f"语言分布：Top{len(repos)} 中 {top_lang} 占 {top_count} 席")

    # 洞察 3：HN 与 GitHub 是否共振
    overlap = [THEME_LABELS[key] for key, _ in repo_themes.most_common() if key in news_themes]
    news_labels = theme_labels(news_themes, top_n=2)
    if overlap:
        insights.append(f"HN 共振：GitHub 与新闻同时指向 {overlap[0]}")
    elif news and news_labels:
        insights.append(f"HN 信号：今天讨论更偏 {news_labels[0]}")
    elif news:
        top = news[0]
        insights.append(f"HN 最热：「{top['title'][:38]}…」🔺{top['score']}分·💬{top['comments']}条")

    # 建议行动
    repo, reason = pick_action_repo(repos, repo_themes, news_themes)
    if repo:
        action = f"建议深入：{repo['name']}（{reason}）"
    else:
        action = "数据不足，建议次日补采"

    return insights, action


# ── 3. 飞书卡片（L2: 含洞察 section）────────────────────────

def build_feishu_card(repos: list, news: list, insights: list, action: str) -> dict:
    """构建飞书 Interactive Card（含 GitHub 表格 + HN 新闻 + L2 洞察）"""
    summary_md = build_summary_md(repos, news, action)
    gh_md = build_repo_watch_md(repos, detail_limit=3)
    hn_md = build_news_watch_md(news, detail_limit=3)
    insight_md = build_judgement_md(insights)

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
                {"tag": "div", "text": {"tag": "lark_md", "content": summary_md}},
                {"tag": "hr"},
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
        f"{TODAY} GitHub AI热度榜Top3: {top3_repos}。"
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
    failures = []
    if not send_feishu(card):
        failures.append("飞书")

    print("🧠 存入 Mem0 长期记忆（L1+L2）...")
    if not store_to_mem0(repos, news, insights, action):
        failures.append("Mem0")

    print("📝 写入 Notion 数据库（L3）...")
    if not log_to_notion(repos):
        failures.append("Notion")

    if failures:
        print(f"❌ 下游步骤失败：{', '.join(failures)}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Daily AI Digest 完成 | run_id={RUN_ID}")


if __name__ == "__main__":
    main()
