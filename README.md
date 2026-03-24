# Daily AI Digest 🤖

每天自动推送 GitHub AI 近 24 小时热度项目 + HackerNews AI 要闻到飞书群，并同步写入 Mem0 与 Notion。

## 架构

```text
GitHub Actions (cron: 0 3 * * *)
    ↓ 目标时间：每天 11:00 AM CST（实际可能因 Actions 队列延后）
    ├── fetch_trends.py    → GitHub AI 热度榜 Top 5（近24h活跃度）
    ├── fetch_hn_ai_news.py → HackerNews AI 新闻 Top 5
    └── daily_brief.py     → 飞书 Interactive Card + Mem0 + Notion
```

## 排名口径

- GitHub 部分不是总 Stars 排行。
- 当前口径是“近窗口活跃度 × 项目新鲜度 + GitHub Trending 校准”的热度榜，目标是减少老牌大仓长期霸榜。
- HN 部分使用更严格的 AI 关键词匹配，避免 `remain` / `aircraft` / `chain` 这类误判。
- 默认排除 `openclaw/openclaw`，避免日报长期重复同一个项目。

## 日志位置

- 本地仓库默认不落独立日志文件。
- 运行日志以 GitHub Actions 为准：
  repo → Actions → `Daily AI Digest`

## 配置 Secrets

在 repo → Settings → Secrets and variables → Actions 添加：

| Secret | 说明 | 必填 |
|--------|------|------|
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook URL | ✅ |
| `MEM0_API_KEY` | Mem0 Cloud API Key (m0-...) | ✅ |
| `FEISHU_SECRET` | 飞书签名校验密钥（仅开启签名校验时填写）| 可选 |
| `NOTION_API_KEY` | Notion Integration Token | ✅ |
| `NOTION_DATABASE_ID` | Notion 数据库 ID（建议填写，避免搜索失败） | 建议 |
| `GITHUB_EXCLUDED_REPOS` | 额外排除的仓库，逗号分隔，如 `foo/bar,baz/qux` | 可选 |

`GITHUB_TOKEN` 由 Actions 自动注入，无需手动添加。

## 手动触发

Actions → Daily AI Digest → Run workflow
