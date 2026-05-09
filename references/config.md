# x-news-alert-v3 Config Reference

Runtime files live next to `scripts/x_news_alert.py` by default:

- `alert-config.json`: global platform, LLM, and polling settings.
- `alert-bloggers.json`: tracked X/Twitter accounts.
- `alert-lists.json`: tracked X Lists.
- `alert-state/`: read tweet IDs and send logs.

Per-target state directories also store `raw.json` and `analysis-cache.json` from the latest run that found new tweets. Use these files when debugging fetch, dedup, or LLM output.

Use `--base-dir <dir>` when a platform needs state outside the skill folder.

## Schedule Config

`schedule` is the canonical scheduler metadata:

```json
{
  "enabled": false,
  "mode": "external",
  "cron": "0 0,2,5,7,9,10,12,15,17,20,22 * * *",
  "timezone": "Asia/Shanghai",
  "polling_interval_minutes": 30,
  "jitter_seconds": 60,
  "lock_ttl_seconds": 21600
}
```

Modes:

- `external`: a platform scheduler should invoke `run all`.
- `poll`: use `python scripts/x_news_alert.py poll` as a long-running process.
- `system-cron`: intended for a future system crontab/Task Scheduler adapter.
- `codex`: intended for a future Codex automation adapter.

Use `schedule command` to print the exact `run all` command and `schedule doctor` before registering it in Codex, Claude Code, OpenClaw, Hermes, cron, or another scheduler.

## Required Runtime

- Python 3.10 or newer.

## Optional External Commands

- `xurl`: fetch X List tweets. The user must provide client id and client secret, and should provide access token and refresh token. If the token expires, v2 can run `xurl_refresh_script` once and retry.
- `twitter`: fetch blogger posts. Init requires a cookie and configures `twitter auth set cookie --value ...` when the command exists.
- `longbridge`: add US stock quote snippets and 5-day close snippets when kline data is available.
- `lark-cli`: send Feishu messages.

Telegram and Discord sending use Python HTTP calls and only need token environment variables.

## Environment Variables

- LLM: default `OPENAI_API_KEY`, configurable with `llm_api_key_env`.
- Discord bot API: default `DISCORD_BOT_TOKEN`.
- Telegram bot API: default `TELEGRAM_BOT_TOKEN`.
- twitter-cli cookie: default `TWITTER_COOKIE`; required during `init`.
- xurl OAuth: defaults `XURL_CLIENT_ID`, `XURL_CLIENT_SECRET`, `XURL_ACCESS_TOKEN`, and `XURL_REFRESH_TOKEN`.

## X/Twitter Credentials

`twitter-cli` cookie is mandatory during initialization. Prefer:

```bash
set TWITTER_COOKIE=auth_token=...; ct0=...
python scripts/x_news_alert.py init --twitter-cookie-env TWITTER_COOKIE
```

Passing `--twitter-cookie "..."` is supported for immediate setup, but the value is not written to `alert-config.json`. If the `twitter` command is installed, init immediately runs `twitter auth set cookie --value ...`.

For `xurl`, List monitoring requires client id and client secret. Provide them by environment, `~/.xurl`, or init flags:

```bash
python scripts/x_news_alert.py init --twitter-cookie-env TWITTER_COOKIE --list 123456789 --xurl-client-id "$XURL_CLIENT_ID" --xurl-client-secret "$XURL_CLIENT_SECRET"
```

Access token and refresh token are recommended for stable refresh behavior. `schedule doctor` fails when client id/client secret are missing for List targets and warns when access/refresh tokens are missing.

## X URL Token Refresh

Defaults:

```json
{
  "xurl_auto_refresh": true,
  "xurl_refresh_script": "~/x-token-refresh/refresh_x_token.py"
}
```

When an X List fetch fails with `401`, `Unauthorized`, or `authentication required`, v3 runs the refresh script once and retries the same `xurl` request. If the script is missing or refresh fails, the target fails for that run and `schedule doctor` reports the missing refresh script as a warning.

## Route Syntax

- `feishu:oc_xxx`
- `discord:123456789`
- `discord:discord_dm_123456789`
- `discord:https://discord.com/api/webhooks/...`
- `telegram:-100123456789`

## Init and Compatibility Commands

Use `init` for portable first-run setup:

```bash
python scripts/x_news_alert.py init --platform telegram --chat-id=-100123456789 --llm-api-key-env OPENAI_API_KEY --blogger https://x.com/username
python scripts/x_news_alert.py init --interactive
```

Send operations retry transient failures up to 3 attempts during the same run. If all attempts fail, the tweet remains retryable through `send-log.json`.

## LLM Prompt Overrides

Three keys control which system prompt is sent to the LLM:

| Key | Scope | Default |
|-----|-------|---------|
| `llm_system_prompt_blogger` | blogger targets only | built-in 11-field analyst prompt |
| `llm_system_prompt_list` | X List targets only | built-in 3-field digest prompt |
| `llm_system_prompt` | fallback for both modes | same built-in defaults |

Priority: `llm_system_prompt_blogger` / `llm_system_prompt_list` → `llm_system_prompt` → built-in default.

Set in `alert-config.json`:

```json
{
  "llm_system_prompt_blogger": "Return JSON array. Each item: id, ...",
  "llm_system_prompt_list": "Return JSON array. Each item: id, chinese_summary ≤60 chars, symbols."
}
```

See `references/prompts.md` for the full built-in prompt text and alternative templates.

## Market Suffix

`market_suffix` controls the exchange suffix appended to bare cashtag symbols when querying `longbridge`. Default is `"US"`.

```json
{ "market_suffix": "HK" }
```

Symbols that already contain a `.` (e.g. `700.HK`, `TSLA.US`) are used as-is and ignore this setting.
