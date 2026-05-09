---
name: x-news-alert-v3
description: |
  EN: Use when tracking X/Twitter accounts or X Lists, running news alert checks, configuring Feishu/Discord/Telegram delivery, inspecting alert status, or managing market-aware tweet summaries across Codex, Claude Code, OpenClaw, Hermes, Windows, Linux, and macOS.
  CN: 用于追踪 X/Twitter 账号或 X Lists、执行新闻告警检查、配置飞书/Discord/Telegram 推送、查看告警状态，或在 Codex、Claude Code、OpenClaw、Hermes、Windows、Linux 和 macOS 上管理带有市场信息的推文摘要。
---

# x-news-alert-v3

## Overview / 概述

**EN:** Use the Python CLI in `scripts/x_news_alert.py` as the single entry point.

**CN:** 以 `scripts/x_news_alert.py` 中的 Python CLI 为唯一可信入口。

---

## Quick Start / 快速开始

**EN:** Prefer direct Python so the same command works across agent platforms:

**CN:** 建议直接使用 Python，使命令在所有 Agent 平台上保持一致：

```bash
python scripts/x_news_alert.py status
python scripts/x_news_alert.py init --platform telegram --chat-id=-100123456789 --twitter-cookie-env TWITTER_COOKIE
python scripts/x_news_alert.py init --interactive
python scripts/x_news_alert.py add-blogger https://x.com/username
python scripts/x_news_alert.py track https://x.com/username telegram:-100123456789
python scripts/x_news_alert.py add-list 123456789 discord:123456789
python scripts/x_news_alert.py run blogger username
python scripts/x_news_alert.py schedule doctor
```

**EN:** The same command works on Unix, Windows PowerShell, and all agent platforms.

**CN:** 同一条命令在 Unix、Windows PowerShell 及所有 Agent 平台上均可直接使用。

---

## Command Mapping / 命令映射

| Intent / 意图 | Command / 命令 |
| --- | --- |
| Initialize config / 初始化配置 | `python scripts/x_news_alert.py init --platform feishu --chat-id oc_xxx --twitter-cookie-env TWITTER_COOKIE` |
| Interactive init wizard / 交互式初始化向导 | `python scripts/x_news_alert.py init --interactive` |
| Track a blogger and run once / 追踪博主并立即运行 | `python scripts/x_news_alert.py track https://x.com/username [route]` |
| Add blogger without running / 添加博主但不运行 | `python scripts/x_news_alert.py add-blogger @username [route]` |
| Remove blogger / 移除博主 | `python scripts/x_news_alert.py remove-blogger username` |
| Add X List / 添加 X List | `python scripts/x_news_alert.py add-list 123456789 [route]` |
| Remove X List / 移除 X List | `python scripts/x_news_alert.py remove-list 123456789` |
| Set default platform / 设置默认平台 | `python scripts/x_news_alert.py set-platform feishu` |
| Set default chat / 设置默认聊天目标 | `python scripts/x_news_alert.py set-default-chat telegram -100123456789` |
| Run checks / 执行检查 | `python scripts/x_news_alert.py run all` |
| Fetch raw tweets / 抓取原始推文 | `python scripts/x_news_alert.py fetch blogger username` |
| Analyze tweet JSON / 分析推文 JSON | `python scripts/x_news_alert.py analyze batch tweets.json` |
| Fetch market snippets / 获取市场片段 | `python scripts/x_news_alert.py market AAPL TSLA` |
| Send a message / 发送消息 | `python scripts/x_news_alert.py send -100123456789 "hello" telegram` |
| Show scheduler config / 显示调度配置 | `python scripts/x_news_alert.py schedule show` |
| Print scheduler command / 打印调度命令 | `python scripts/x_news_alert.py schedule command` |
| Check scheduler readiness / 检查调度就绪状态 | `python scripts/x_news_alert.py schedule doctor` |
| Inspect status / 查看状态 | `python scripts/x_news_alert.py status` |
| Print config / 打印配置 | `python scripts/x_news_alert.py config` |
| Reset read-ids for a target / 重置已读状态 | `python scripts/x_news_alert.py reset-state blogger username` |

**EN:** Routes use `feishu:CHAT`, `discord:CHANNEL_OR_WEBHOOK`, or `telegram:CHAT`.

**CN:** 路由格式使用 `feishu:CHAT`、`discord:CHANNEL_OR_WEBHOOK` 或 `telegram:CHAT`。

---

## Scheduling / 调度

**EN:** Use platform-native schedulers to call the single-run command:

**CN:** 使用平台原生调度器调用单次运行命令：

```bash
python scripts/x_news_alert.py run all
```

**EN:** Ask the CLI for the exact absolute command when installing an external scheduler:

**CN:** 安装外部调度器时，向 CLI 查询确切的绝对路径命令：

```bash
python scripts/x_news_alert.py schedule command
python scripts/x_news_alert.py schedule doctor
```

**EN:** `schedule enable` records scheduling intent in config; it does not install Codex automations, crontab entries, Windows tasks, Claude Code jobs, OpenClaw jobs, or Hermes jobs by itself. Use `poll` only when a long-running process is acceptable.

**CN:** `schedule enable` 仅在配置中记录调度意图；它本身不会安装 Codex 自动化、crontab 条目、Windows 任务、Claude Code 任务、OpenClaw 任务或 Hermes 任务。仅在可接受常驻进程时使用 `poll`。

---

## Runtime Notes / 运行说明

**EN:** Python 3.10+ is required. Optional integrations are discovered at runtime: `xurl` for X Lists, `twitter` for blogger posts, `longbridge` for market snippets, and `lark-cli` for Feishu sends.

**CN:** 需要 Python 3.10+。可选集成在运行时动态发现：`xurl` 用于 X Lists，`twitter` 用于博主推文，`longbridge` 用于市场片段，`lark-cli` 用于飞书推送。

**EN:** Users must provide X/Twitter credentials before fetches can work:

**CN:** 用户必须提供 X/Twitter 凭据后才能进行抓取：

- **EN:** `init` requires a `twitter-cli` cookie. Prefer setting `TWITTER_COOKIE` and running `init --twitter-cookie-env TWITTER_COOKIE`; if `twitter` is installed, v3 immediately runs `twitter auth set cookie --value ...`.
- **CN:** `init` 需要 `twitter-cli` cookie。建议设置 `TWITTER_COOKIE` 环境变量并运行 `init --twitter-cookie-env TWITTER_COOKIE`；如果已安装 `twitter` 命令，v3 会立即执行 `twitter auth set cookie --value ...`。

- **EN:** X List monitoring requires `xurl` authorization. The user must provide client id and client secret through `XURL_CLIENT_ID`/`XURL_CLIENT_SECRET`, `~/.xurl`, or `init --xurl-client-id ... --xurl-client-secret ...`.
- **CN:** X List 监控需要 `xurl` 授权。用户必须通过 `XURL_CLIENT_ID`/`XURL_CLIENT_SECRET`、 `~/.xurl` 文件或 `init --xurl-client-id ... --xurl-client-secret ...` 提供 client id 和 client secret。

- **EN:** X List monitoring should also provide `XURL_ACCESS_TOKEN` and `XURL_REFRESH_TOKEN`; `schedule doctor` warns when these are missing.
- **CN:** X List 监控还应提供 `XURL_ACCESS_TOKEN` 和 `XURL_REFRESH_TOKEN`；`schedule doctor` 会在缺失时发出警告。

- **EN:** If the X List bearer token expires, v3 can auto-run `~/x-token-refresh/refresh_x_token.py` once on `401`/`Unauthorized`, then retry. Override with `xurl_refresh_script` or disable with `xurl_auto_refresh=false` in config.
- **CN:** 如果 X List 的 bearer token 过期，v3 可以在遇到 `401`/`Unauthorized` 时自动运行 `~/x-token-refresh/refresh_x_token.py` 一次，然后重试。可通过配置中的 `xurl_refresh_script` 覆盖路径，或通过 `xurl_auto_refresh=false` 禁用。

**EN:** If no LLM API key is configured, v3 still runs with a deterministic fallback summary and cashtag extraction. Configure `OPENAI_API_KEY` or set `llm_api_key_env` in `alert-config.json` for LLM summaries.

**CN:** 如果未配置 LLM API 密钥，v3 仍会使用确定性的降级摘要和股票代码提取继续运行。配置 `OPENAI_API_KEY` 或在 `alert-config.json` 中设置 `llm_api_key_env` 以启用 LLM 摘要。

**EN:** Delivery sends retry transient `AlertError` failures up to 3 attempts during the current run. Failed sends are still recorded in `send-log.json` so the same tweet can be retried on a later run.

**CN:** 推送发送会在当前运行中对瞬时的 `AlertError` 失败最多重试 3 次。失败的发送仍会记录在 `send-log.json` 中，以便在后续运行中重试同一条推文。

---

## State and Config / 状态与配置

**EN:** The CLI creates these files in `scripts/` by default:

**CN:** CLI 默认在 `scripts/` 中创建以下文件：

- `alert-config.json`
- `alert-bloggers.json`
- `alert-lists.json`
- `alert-state/`

**EN:** Each target state directory can also contain `raw.json` and `analysis-cache.json` from the latest non-empty run for debugging.

**CN:** 每个目标的状态目录还可以包含最近一次非空运行的 `raw.json` 和 `analysis-cache.json`，用于调试。

---

## Base Directory / 基础目录

**EN:** Use `--base-dir <dir>` before the subcommand when a platform needs writable state elsewhere:

**CN:** 当平台需要将可写状态放在其他位置时，在子命令前使用 `--base-dir <dir>`：

```bash
python scripts/x_news_alert.py --base-dir /tmp/x-news-alert status
```

---

## Message Templates / 消息推送模板

**EN:** v3 uses two different push formats based on target type:

**CN:** v3 根据目标类型使用两种不同的推送格式：

- **Template A（博主 blogger）** — Single tweet, deep analysis: author header, credibility score, trend emoji, 🔍 sourcing / 🧠 logic / ⚠️ prediction / 🚩 sleaze blocks, market quotes, raw text, link.
- **Template B（X List）** — Multi-tweet digest: each tweet as a compact entry (author · time, summary, text, link), paginated to fit platform character limits (Telegram 4096 / Discord 2000).

**EN:** LLM prompts are selected automatically by mode. Override via `alert-config.json`:

**CN:** LLM prompt 按模式自动选择，可通过 `alert-config.json` 覆盖：

```json
{
  "llm_system_prompt_blogger": "...",
  "llm_system_prompt_list": "...",
  "llm_system_prompt": "..."
}
```

Priority: `llm_system_prompt_blogger` / `llm_system_prompt_list` → `llm_system_prompt` → built-in default.

**EN:** See `references/prompts.md` for all template definitions, LLM output schemas, and prompt variants.

**CN:** 所有模板定义、LLM 输出字段说明和 prompt 变体见 `references/prompts.md`。

---

## Further Reading / 延伸阅读

**EN:** Read `references/config.md` when detailed config fields, environment variables, or route syntax are needed.

**CN:** 需要详细的配置字段、环境变量或路由语法时，请阅读 `references/config.md`。
