# AGENTS.md - Agent Developer Guide for x-news-alert

This file provides context for AI agents working in this repository.

## Project Overview

- **Project**: x-news-alert - X/Twitter news alert automation for bloggers and X Lists.
- **Language**: Python 3.10+
- **Package Manager**: pip / pipx
- **CLI Entry Points**: `x-news-alert`, `xna`, `x`
- **Repository**: https://github.com/Allen2023/x_news_alert

## Build, Install, and Test Commands

```bash
# Install locally for development
python -m pip install -e .

# Install local dev dependencies
python -m pip install -e ".[dev]"

# Run all tests
python -m pytest

# Compile-check core modules
python -m py_compile x_news_alert/cli.py x_news_alert/config_loader.py x_news_alert/models.py x_news_alert/output.py x_news_alert/tweets.py scripts/x_news_alert.py tests/test_core.py

# Build a wheel locally
python -m pip install build
python -m build
```

Remote installation should use Python packaging, not shell installers:

```bash
pipx install git+https://github.com/Allen2023/x_news_alert.git
```

## Code Style

- Use `from __future__ import annotations` in Python modules.
- Use `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Keep the runtime mostly standard-library based; add dependencies only when they materially simplify installation or maintenance.
- Prefer explicit `AlertError` failures for expected operational errors.
- Do not store real tokens, cookies, chat IDs, or API keys in committed files.
- Keep CLI behavior backward-compatible unless a breaking change is intentional and documented.

## Project Structure

```text
x_news_alert/
├── cli.py              # argparse CLI, run loop, integrations, message formatting
├── config_loader.py    # credentials.yaml and environment credential loading
├── models.py           # shared dataclasses and operational classes
├── output.py           # structured output envelope and UTF-8 stream handling
├── tweets.py           # tweet normalization, scoring, timestamps, message builders
└── __init__.py         # package exports

scripts/
├── x_news_alert.py     # thin compatibility wrapper for script-style execution
└── xurl-token-check.py # helper for checking xurl auth

tests/
└── test_core.py        # unit and integration-style CLI tests
```

## Runtime Configuration

- Default config directory is user-local:
- Windows: `%APPDATA%\x-news-alert`
- macOS/Linux: `$XDG_CONFIG_HOME/x-news-alert` or `~/.config/x-news-alert`
- Override with `X_NEWS_ALERT_HOME`.
- Override credentials file with `X_NEWS_ALERT_CREDENTIALS`.
- Use `credentials.yaml` or environment variables for secrets.

Common environment variables:

```bash
TWITTER_COOKIE="auth_token=xxx; ct0=xxx"
OPENAI_API_KEY="sk-xxx"
TELEGRAM_BOT_TOKEN="xxx"
DISCORD_BOT_TOKEN="xxx"
XURL_CLIENT_ID="..."
XURL_CLIENT_SECRET="..."
XURL_ACCESS_TOKEN="..."
XURL_REFRESH_TOKEN="..."
```

## External Tools

- `twitter`: required for blogger timelines.
- `xurl`: required for X List fetches.
- `lark-cli`: required only for Feishu delivery.
- `longbridge`: optional market quote enrichment.

## Packaging Rules

- Console scripts must point at `x_news_alert.cli:main`.
- Do not add `install.sh`, `install.ps1`, or other shell installer wrappers unless explicitly requested.
- Keep remote install documentation centered on `pipx install git+https://github.com/Allen2023/x_news_alert.git`.
- Keep `scripts/x_news_alert.py` as a thin compatibility wrapper only.

## CI and Publishing

- GitHub Actions CI lives in `.github/workflows/ci.yml`.
- PyPI publishing lives in `.github/workflows/publish.yml`.
- Publishing expects PyPI Trusted Publishing configured for the `pypi` environment.
- Keep `SCHEMA.md` in sync whenever structured output changes.

## Housekeeping

- Do not remove or modify `.idea/` unless the user explicitly asks.
- Clean generated artifacts after validation when practical: `.pytest_cache/`, `__pycache__/`, `build/`, `*.egg-info/`.
- Do not commit generated runtime state such as `alert-state/`, `raw.json`, `analysis-cache.json`, or local credential files.
