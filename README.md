# Daily AI Digest 🤖

每天 11:00 AM CST 自动推送 GitHub AI 热门项目 + HackerNews AI 要闻到飞书群，同步存入 Mem0 长期记忆。

## 架构

```
GitHub Actions (cron: 0 3 * * *)
    ↓ 每天 11:00 AM CST
    ├── fetch_trends.py    → GitHub AI 日榜 Top 5
    ├── fetch_hn_ai_news.py → HackerNews AI 新闻 Top 5
    └── daily_brief.py     → 飞书 Interactive Card + Mem0 存储
```

## 配置 Secrets

在 repo → Settings → Secrets and variables → Actions 添加：

| Secret | 说明 | 必填 |
|--------|------|------|
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook URL | ✅ |
| `MEM0_API_KEY` | Mem0 Cloud API Key (m0-...) | ✅ |
| `FEISHU_SECRET` | 飞书签名校验密钥（仅开启签名校验时填写）| 可选 |

`GITHUB_TOKEN` 由 Actions 自动注入，无需手动添加。

## 手动触发

Actions → Daily AI Digest → Run workflow
