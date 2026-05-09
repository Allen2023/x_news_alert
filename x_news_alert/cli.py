#!/usr/bin/env python3
"""Portable X/Twitter alert runner for the x-news-alert-v3 skill.

The implementation intentionally uses the Python standard library for JSON,
HTTP, subprocess execution, and file handling so it works in Codex, Claude
Code, OpenClaw, Hermes, Windows, Linux, and macOS without a Bash runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Callable, TextIO

try:
    from .models import AlertError, AlertPaths, RunLock
    from .output import SCHEMA_VERSION, emit_data, ensure_utf8_streams, error_payload, print_json, success_payload
    from .tweets import (
        DEFAULT_LIST_FILTER,
        build_message_blogger,
        build_message_list_entry,
        extract_symbols,
        fallback_analysis,
        filter_list_tweets,
        format_time_ago,
        list_filter_config,
        normalize_tweet_row,
        rows_from_raw,
        tweet_engagement_score,
        tweet_metrics,
    )
except ImportError:
    # Fallback for direct script execution - add scripts dir to path
    import sys
    from pathlib import Path
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from models import AlertError, AlertPaths, RunLock
    from output import SCHEMA_VERSION, emit_data, ensure_utf8_streams, error_payload, print_json, success_payload
    from tweets import (
        DEFAULT_LIST_FILTER,
        build_message_blogger,
        build_message_list_entry,
        extract_symbols,
        fallback_analysis,
        filter_list_tweets,
        format_time_ago,
        list_filter_config,
        normalize_tweet_row,
        rows_from_raw,
        tweet_engagement_score,
        tweet_metrics,
    )

try:
    from .config_loader import (
        get_credential,
        get_xurl_config,
        get_llm_config,
        get_discord_bot_token,
        get_telegram_bot_token,
    )
    HAS_CREDENTIALS = True
except ImportError:
    try:
        from config_loader import (
            get_credential,
            get_xurl_config,
            get_llm_config,
            get_discord_bot_token,
            get_telegram_bot_token,
        )
        HAS_CREDENTIALS = True
    except ImportError:
        HAS_CREDENTIALS = False



# ── Constants & Defaults ─────────────────────────────────────

DEFAULT_CRON = "0 0,2,5,7,9,10,12,15,17,20,22 * * *"
DEFAULT_XURL_REFRESH_SCRIPT = "~/x-token-refresh/refresh_x_token.py"
APP_HOME_ENV = "X_NEWS_ALERT_HOME"
APP_COMMAND_ENV = "X_NEWS_ALERT_COMMAND"
APP_DIR_NAME = "x-news-alert"
DEFAULT_SCHEDULE: dict[str, Any] = {
    "enabled": False,
    "mode": "external",
    "cron": DEFAULT_CRON,
    "timezone": "Asia/Shanghai",
    "polling_interval_minutes": 30,
    "jitter_seconds": 60,
    "lock_ttl_seconds": 21600,
}
DEFAULT_CONFIG: dict[str, Any] = {
    "default_platform": "feishu",
    "platforms": {
        "feishu": {"default_chat_id": "", "chat_ids": {}},
        "discord": {
            "webhook_url": "",
            "bot_token_env": "DISCORD_BOT_TOKEN",
            "default_channel_id": "",
        },
        "telegram": {
            "bot_token_env": "TELEGRAM_BOT_TOKEN",
            "default_chat_id": "",
        },
    },
    "schedule": DEFAULT_SCHEDULE,
    "cron_enabled": False,
    "cron_schedule": DEFAULT_CRON,
    "polling_interval_minutes": 30,
    "llm_provider": "openai",
    "llm_model": "gpt-4o-mini",
    "llm_api_base": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_api_key_env": "OPENAI_API_KEY",
    "max_tokens": 8192,
    "twitter_cli": {
        "cookie_env": "TWITTER_COOKIE",
        "cookie_provided": False,
        "cookie_configured": False,
    },
    "xurl_auth": {
        "client_id_env": "XURL_CLIENT_ID",
        "client_secret_env": "XURL_CLIENT_SECRET",
        "access_token_env": "XURL_ACCESS_TOKEN",
        "refresh_token_env": "XURL_REFRESH_TOKEN",
        "client_id_provided": False,
        "client_secret_provided": False,
        "access_token_provided": False,
        "refresh_token_provided": False,
    },
    "xurl_auto_refresh": True,
    "xurl_refresh_script": DEFAULT_XURL_REFRESH_SCRIPT,
    "market_suffix": "US",
    "list_filter": DEFAULT_LIST_FILTER,
}


DEFAULT_BLOGGERS = {"bloggers": []}
DEFAULT_LISTS = {"lists": []}
SUPPORTED_PLATFORMS = {"feishu", "discord", "telegram"}
XURL_TOKEN_ENV_NAMES = ("XURL_BEARER_TOKEN", "X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN")
PLATFORM_MESSAGE_LIMITS: dict[str, int] = {"telegram": 4096, "discord": 2000}
MAX_READ_IDS = 10_000
ANALYZE_CHUNK_SIZE = 10



# ── Core Classes ─────────────────────────────────────────────

def default_base_dir() -> Path:
    configured = os.environ.get(APP_HOME_ENV)
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / APP_DIR_NAME
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


# ── Config I/O ───────────────────────────────────────────────

def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AlertError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def ensure_files(paths: AlertPaths) -> None:
    paths.base.mkdir(parents=True, exist_ok=True)
    paths.state.mkdir(parents=True, exist_ok=True)
    if not paths.config.exists():
        write_json(paths.config, DEFAULT_CONFIG)
    if not paths.bloggers.exists():
        write_json(paths.bloggers, DEFAULT_BLOGGERS)
    if not paths.lists.exists():
        write_json(paths.lists, DEFAULT_LISTS)



# ── Input Normalization ──────────────────────────────────────

def normalize_username(value: str) -> str:
    cleaned = value.strip()
    cleaned = cleaned.split("?", 1)[0]
    for prefix in (
        "https://x.com/",
        "http://x.com/",
        "https://twitter.com/",
        "http://twitter.com/",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.lstrip("@")
    cleaned = cleaned.split("/", 1)[0]
    return cleaned.rstrip("/")


def normalize_list_id(value: str) -> str:
    cleaned = value.strip().split("?", 1)[0]
    for prefix in (
        "https://x.com/i/lists/",
        "http://x.com/i/lists/",
        "https://twitter.com/i/lists/",
        "http://twitter.com/i/lists/",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned.split("/", 1)[0].strip()


def parse_route(route_args: Iterable[str]) -> tuple[str, str]:
    platform = "auto"
    chat_id = ""
    for route in route_args:
        if route.startswith("feishu:"):
            platform = "feishu"
            chat_id = route.removeprefix("feishu:")
        elif route.startswith("telegram:"):
            platform = "telegram"
            chat_id = route.removeprefix("telegram:")
        elif route.startswith("discord:"):
            platform = "discord"
            value = route.removeprefix("discord:")
            if (
                value.startswith("https://discord.com/api/webhooks/")
                or value.startswith("discord_dm_")
                or value.startswith("discord_channel_")
            ):
                chat_id = value
            else:
                chat_id = f"discord_channel_{value}"
        else:
            raise AlertError(f"Unsupported route: {route}")
    return platform, chat_id


def validate_platform(platform: str) -> None:
    if platform not in SUPPORTED_PLATFORMS:
        raise AlertError("platform must be feishu, discord, or telegram")



# ── Target Management ────────────────────────────────────────

def add_blogger(paths: AlertPaths, target: str, route_args: Iterable[str] = ()) -> str:
    username = normalize_username(target)
    if not username:
        raise AlertError("empty username")
    platform, chat_id = parse_route(route_args)
    data = read_json(paths.bloggers, DEFAULT_BLOGGERS)
    bloggers = [item for item in data.get("bloggers", []) if item.get("username") != username]
    bloggers.append(
        {
            "username": username,
            "display_name": username,
            "enabled": True,
            "platform": platform,
            "chat_id": chat_id,
        }
    )
    write_json(paths.bloggers, {"bloggers": bloggers})
    return username


def remove_blogger(paths: AlertPaths, target: str) -> str:
    username = normalize_username(target)
    data = read_json(paths.bloggers, DEFAULT_BLOGGERS)
    bloggers = [item for item in data.get("bloggers", []) if item.get("username") != username]
    write_json(paths.bloggers, {"bloggers": bloggers})
    return username


def add_list(paths: AlertPaths, target: str, route_args: Iterable[str] = ()) -> str:
    list_id = normalize_list_id(target)
    if not list_id:
        raise AlertError("empty list id")
    platform, chat_id = parse_route(route_args)
    data = read_json(paths.lists, DEFAULT_LISTS)
    lists = [item for item in data.get("lists", []) if item.get("id") != list_id]
    lists.append(
        {
            "id": list_id,
            "display_name": f"list-{list_id}",
            "enabled": True,
            "platform": platform,
            "chat_id": chat_id,
        }
    )
    write_json(paths.lists, {"lists": lists})
    return list_id


def remove_list(paths: AlertPaths, target: str) -> str:
    list_id = normalize_list_id(target)
    data = read_json(paths.lists, DEFAULT_LISTS)
    lists = [item for item in data.get("lists", []) if item.get("id") != list_id]
    write_json(paths.lists, {"lists": lists})
    return list_id


def set_platform(paths: AlertPaths, platform: str) -> None:
    validate_platform(platform)
    config = read_json(paths.config, DEFAULT_CONFIG)
    config["default_platform"] = platform
    write_json(paths.config, config)


def set_default_chat(paths: AlertPaths, platform: str, chat_id: str) -> None:
    validate_platform(platform)
    config = read_json(paths.config, DEFAULT_CONFIG)
    platforms = config.setdefault("platforms", {})
    platform_config = platforms.setdefault(platform, {})
    if platform == "feishu":
        platform_config["default_chat_id"] = chat_id
    elif platform == "telegram":
        platform_config["default_chat_id"] = chat_id
    else:
        if chat_id.startswith("https://discord.com/api/webhooks/"):
            platform_config["webhook_url"] = chat_id
        else:
            platform_config["default_channel_id"] = chat_id.removeprefix("discord_channel_")
    write_json(paths.config, config)



# ── Schedule ─────────────────────────────────────────────────

def schedule_config(config: dict[str, Any]) -> dict[str, Any]:
    schedule = dict(DEFAULT_SCHEDULE)
    raw_schedule = config.get("schedule")
    if isinstance(raw_schedule, dict):
        schedule.update({key: value for key, value in raw_schedule.items() if value is not None})
    else:
        schedule["enabled"] = bool(config.get("cron_enabled", schedule["enabled"]))
        schedule["cron"] = str(config.get("cron_schedule") or schedule["cron"])
        schedule["polling_interval_minutes"] = int(
            config.get("polling_interval_minutes") or schedule["polling_interval_minutes"]
        )

    schedule["enabled"] = bool(schedule.get("enabled"))
    schedule["mode"] = str(schedule.get("mode") or "external")
    if schedule["mode"] not in {"external", "poll", "system-cron", "codex"}:
        schedule["mode"] = "external"
    schedule["cron"] = str(schedule.get("cron") or DEFAULT_CRON)
    schedule["timezone"] = str(schedule.get("timezone") or "Asia/Shanghai")
    schedule["polling_interval_minutes"] = max(1, int(schedule.get("polling_interval_minutes") or 30))
    schedule["jitter_seconds"] = max(0, int(schedule.get("jitter_seconds") or 0))
    schedule["lock_ttl_seconds"] = max(60, int(schedule.get("lock_ttl_seconds") or 21600))
    return schedule


def sync_schedule_legacy_fields(config: dict[str, Any]) -> dict[str, Any]:
    schedule = schedule_config(config)
    config["schedule"] = schedule
    config["cron_enabled"] = schedule["enabled"]
    config["cron_schedule"] = schedule["cron"]
    config["polling_interval_minutes"] = schedule["polling_interval_minutes"]
    return config


def set_schedule(
    paths: AlertPaths,
    *,
    enabled: bool | None = None,
    mode: str | None = None,
    cron: str | None = None,
    interval: int | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    config = read_json(paths.config, DEFAULT_CONFIG)
    schedule = schedule_config(config)
    if enabled is not None:
        schedule["enabled"] = enabled
    if mode is not None:
        if mode not in {"external", "poll", "system-cron", "codex"}:
            raise AlertError("schedule mode must be external, poll, system-cron, or codex")
        schedule["mode"] = mode
    if cron is not None:
        schedule["cron"] = cron
    if interval is not None:
        if interval < 1:
            raise AlertError("polling interval must be at least 1 minute")
        schedule["polling_interval_minutes"] = interval
    if timezone is not None:
        schedule["timezone"] = timezone
    config["schedule"] = schedule
    write_json(paths.config, sync_schedule_legacy_fields(config))
    return schedule



# ── Auth & Credentials ───────────────────────────────────────

def twitter_cli_config(config: dict[str, Any]) -> dict[str, Any]:
    current = dict(DEFAULT_CONFIG["twitter_cli"])
    raw = config.get("twitter_cli")
    if isinstance(raw, dict):
        current.update(raw)
    return current


def xurl_auth_config(config: dict[str, Any]) -> dict[str, Any]:
    current = dict(DEFAULT_CONFIG["xurl_auth"])
    raw = config.get("xurl_auth")
    if isinstance(raw, dict):
        current.update(raw)
    return current


def env_lookup(env: Mapping[str, str] | None, name: str) -> str:
    current_env = env if env is not None else os.environ
    return str(current_env.get(name) or "")


def resolve_token(env_var: str, creds_path: tuple[str, ...], config: dict[str, Any] | None = None, default: str = "", fallback_key: str = "") -> str:
    """
    优先从环境变量获取，如果不存在则从 credentials.yaml 获取，最后从 config 获取。
    env_var: 环境变量名，如 "TELEGRAM_BOT_TOKEN"
    creds_path: credentials.yaml 中的路径，如 ("telegram", "bot_token")
    config: 可选的 config 字典
    default: 默认返回值
    fallback_key: config 中的备用 key，如 "llm_api_key"
    """
    # 1. 优先使用环境变量
    if env_var and env_var in os.environ:
        return os.environ[env_var]
    # 2. 尝试从 credentials.yaml 获取
    if HAS_CREDENTIALS:
        val = get_credential(*creds_path)
        if val:
            return val
    # 3. 从 config 获取
    if config:
        # 尝试 env_var 对应的 key（如 "OPENAI_API_KEY"）
        val = config.get(env_var) or config.get(env_var.lower()) or config.get(env_var.upper())
        if val:
            return val
        # 尝试 fallback_key（如 "llm_api_key"）
        if fallback_key:
            val = config.get(fallback_key)
            if val:
                return val
    return default


def configure_twitter_cookie(
    config: dict[str, Any],
    *,
    cookie: str,
    cookie_env: str = "TWITTER_COOKIE",
    runner: Callable[[list[str], int], str] | None = None,
    command_exists: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    if not cookie.strip():
        raise AlertError("twitter cookie is required during init")
    runner = runner or run_command
    command_exists = command_exists or (lambda command: shutil.which(command) is not None)
    twitter = twitter_cli_config(config)
    twitter["cookie_env"] = cookie_env or "TWITTER_COOKIE"
    twitter["cookie_provided"] = True
    twitter["cookie_configured"] = False
    if command_exists("twitter"):
        runner(["twitter", "auth", "set", "cookie", "--value", cookie], 120)
        twitter["cookie_configured"] = True
    config["twitter_cli"] = twitter
    return config


def record_xurl_credentials(
    config: dict[str, Any],
    *,
    client_id: str = "",
    client_secret: str = "",
    access_token: str = "",
    refresh_token: str = "",
    client_id_env: str = "XURL_CLIENT_ID",
    client_secret_env: str = "XURL_CLIENT_SECRET",
    access_token_env: str = "XURL_ACCESS_TOKEN",
    refresh_token_env: str = "XURL_REFRESH_TOKEN",
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    xurl = xurl_auth_config(config)
    xurl["client_id_env"] = client_id_env or "XURL_CLIENT_ID"
    xurl["client_secret_env"] = client_secret_env or "XURL_CLIENT_SECRET"
    xurl["access_token_env"] = access_token_env or "XURL_ACCESS_TOKEN"
    xurl["refresh_token_env"] = refresh_token_env or "XURL_REFRESH_TOKEN"
    xurl["client_id_provided"] = bool(client_id or env_lookup(env, str(xurl["client_id_env"])))
    xurl["client_secret_provided"] = bool(client_secret or env_lookup(env, str(xurl["client_secret_env"])))
    xurl["access_token_provided"] = bool(access_token or env_lookup(env, str(xurl["access_token_env"])))
    xurl["refresh_token_provided"] = bool(refresh_token or env_lookup(env, str(xurl["refresh_token_env"])))
    config["xurl_auth"] = xurl
    return config



# ── Init ─────────────────────────────────────────────────────

def init_runtime(
    paths: AlertPaths,
    *,
    platform: str = "feishu",
    chat_id: str = "",
    llm_provider: str = "openai",
    llm_api_base: str = "https://api.openai.com/v1",
    llm_api_key_env: str = "OPENAI_API_KEY",
    llm_model: str = "gpt-4o-mini",
    blogger: str = "",
    list_id: str = "",
    enable_schedule: bool = False,
    schedule_mode: str = "external",
    interval: int | None = None,
    cron: str | None = None,
    timezone: str | None = None,
    twitter_cookie: str = "",
    twitter_cookie_env: str = "TWITTER_COOKIE",
    xurl_client_id: str = "",
    xurl_client_secret: str = "",
    xurl_access_token: str = "",
    xurl_refresh_token: str = "",
    xurl_client_id_env: str = "XURL_CLIENT_ID",
    xurl_client_secret_env: str = "XURL_CLIENT_SECRET",
    xurl_access_token_env: str = "XURL_ACCESS_TOKEN",
    xurl_refresh_token_env: str = "XURL_REFRESH_TOKEN",
    env: Mapping[str, str] | None = None,
    runner: Callable[[list[str], int], str] | None = None,
    command_exists: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    validate_platform(platform)
    config = read_json(paths.config, DEFAULT_CONFIG)
    cookie_value = twitter_cookie or env_lookup(env, twitter_cookie_env)
    if not cookie_value:
        raise AlertError("twitter cookie is required during init; pass --twitter-cookie or set TWITTER_COOKIE")
    configure_twitter_cookie(
        config,
        cookie=cookie_value,
        cookie_env=twitter_cookie_env,
        runner=runner,
        command_exists=command_exists,
    )
    record_xurl_credentials(
        config,
        client_id=xurl_client_id,
        client_secret=xurl_client_secret,
        access_token=xurl_access_token,
        refresh_token=xurl_refresh_token,
        client_id_env=xurl_client_id_env,
        client_secret_env=xurl_client_secret_env,
        access_token_env=xurl_access_token_env,
        refresh_token_env=xurl_refresh_token_env,
        env=env,
    )
    xurl = xurl_auth_config(config)
    if list_id and not xurl["client_id_provided"]:
        raise AlertError("xurl client id is required when initializing an X List target")
    if list_id and not xurl["client_secret_provided"]:
        raise AlertError("xurl client secret is required when initializing an X List target")
    config["default_platform"] = platform
    config["llm_provider"] = llm_provider
    config["llm_api_base"] = llm_api_base
    config["llm_api_key_env"] = llm_api_key_env
    config["llm_model"] = llm_model
    platforms = config.setdefault("platforms", {})
    platform_config = platforms.setdefault(platform, {})
    if chat_id:
        if platform == "discord" and chat_id.startswith("https://discord.com/api/webhooks/"):
            platform_config["webhook_url"] = chat_id
        elif platform == "discord":
            platform_config["default_channel_id"] = chat_id.removeprefix("discord_channel_")
        else:
            platform_config["default_chat_id"] = chat_id

    schedule = schedule_config(config)
    schedule["enabled"] = enable_schedule
    schedule["mode"] = schedule_mode
    if interval is not None:
        if interval < 1:
            raise AlertError("polling interval must be at least 1 minute")
        schedule["polling_interval_minutes"] = interval
    if cron is not None:
        schedule["cron"] = cron
    if timezone is not None:
        schedule["timezone"] = timezone
    config["schedule"] = schedule_config({"schedule": schedule})
    write_json(paths.config, sync_schedule_legacy_fields(config))

    if blogger:
        add_blogger(paths, blogger)
    if list_id:
        add_list(paths, list_id)
    return read_json(paths.config, DEFAULT_CONFIG)


def prompt_value(input_func: Callable[[str], str], prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input_func(f"{prompt}{suffix}: ").strip()
    return value or default


def run_init_wizard(
    paths: AlertPaths,
    input_func: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
) -> dict[str, Any]:
    print("x-news-alert-v3 init", file=out)
    llm_provider = prompt_value(input_func, "LLM provider", "openai")
    llm_api_base = prompt_value(input_func, "LLM API base", "https://api.openai.com/v1")
    llm_api_key_env = prompt_value(input_func, "LLM API key env", "OPENAI_API_KEY")
    llm_model = prompt_value(input_func, "LLM model", "gpt-4o-mini")
    platform = prompt_value(input_func, "Default platform (feishu/discord/telegram)", "feishu")
    if platform not in SUPPORTED_PLATFORMS:
        platform = "feishu"
    chat_id = prompt_value(input_func, "Default chat/webhook", "")
    twitter_cookie = prompt_value(input_func, "Twitter CLI cookie (required)", "")
    xurl_client_id = prompt_value(input_func, "xurl client id (required for X Lists)", "")
    xurl_client_secret = prompt_value(input_func, "xurl client secret (required for X Lists)", "")
    xurl_access_token = prompt_value(input_func, "xurl access token (recommended)", "")
    xurl_refresh_token = prompt_value(input_func, "xurl refresh token (recommended)", "")
    blogger = prompt_value(input_func, "Initial blogger URL or username", "")
    enable_schedule = prompt_value(input_func, "Enable schedule metadata? y/N", "N").lower().startswith("y")
    return init_runtime(
        paths,
        platform=platform,
        chat_id=chat_id,
        llm_provider=llm_provider,
        llm_api_base=llm_api_base,
        llm_api_key_env=llm_api_key_env,
        llm_model=llm_model,
        blogger=blogger,
        enable_schedule=enable_schedule,
        twitter_cookie=twitter_cookie,
        xurl_client_id=xurl_client_id,
        xurl_client_secret=xurl_client_secret,
        xurl_access_token=xurl_access_token,
        xurl_refresh_token=xurl_refresh_token,
    )



# ── Scheduler Inspection ─────────────────────────────────────

def command_string(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def schedule_command(paths: AlertPaths) -> str:
    configured_command = os.environ.get(APP_COMMAND_ENV)
    if configured_command:
        return command_string([configured_command, "--base-dir", str(paths.base), "run", "all"])
    installed_command = shutil.which("x-news-alert")
    if installed_command:
        return command_string([installed_command, "--base-dir", str(paths.base), "run", "all"])
    script = Path(__file__).resolve()
    return command_string([sys.executable, str(script), "--base-dir", str(paths.base), "run", "all"])


def enabled_target_count(paths: AlertPaths) -> int:
    bloggers = read_json(paths.bloggers, DEFAULT_BLOGGERS).get("bloggers", [])
    lists = read_json(paths.lists, DEFAULT_LISTS).get("lists", [])
    count = 0
    for target in [*bloggers, *lists]:
        if isinstance(target, dict) and target.get("enabled") is not False:
            count += 1
    return count


def enabled_list_count(paths: AlertPaths) -> int:
    lists = read_json(paths.lists, DEFAULT_LISTS).get("lists", [])
    return sum(1 for target in lists if isinstance(target, dict) and target.get("enabled") is not False)


def enabled_blogger_count(paths: AlertPaths) -> int:
    bloggers = read_json(paths.bloggers, DEFAULT_BLOGGERS).get("bloggers", [])
    return sum(1 for target in bloggers if isinstance(target, dict) and target.get("enabled") is not False)


def has_delivery_target(paths: AlertPaths, config: dict[str, Any]) -> bool:
    default_platform = str(config.get("default_platform") or "feishu")
    if default_chat_for_platform(config, default_platform):
        return True
    bloggers = read_json(paths.bloggers, DEFAULT_BLOGGERS).get("bloggers", [])
    lists = read_json(paths.lists, DEFAULT_LISTS).get("lists", [])
    for target in [*bloggers, *lists]:
        if isinstance(target, dict) and target.get("enabled") is not False and target.get("chat_id"):
            return True
    return False


def read_xurl_file(home: Path | None = None) -> tuple[str, str]:
    home_dir = home or Path.home()
    xurl_file = home_dir / ".xurl"
    if not xurl_file.exists():
        return "", f"{xurl_file} not found"
    try:
        return xurl_file.read_text(encoding="utf-8", errors="ignore"), str(xurl_file)
    except OSError as exc:
        return "", f"cannot read {xurl_file}: {exc}"


def xurl_file_has(content: str, names: Iterable[str]) -> bool:
    lowered = content.lower()
    return any(name.lower() in lowered for name in names)


def twitter_cookie_configured(config: dict[str, Any], env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    twitter = twitter_cli_config(config)
    cookie_env = str(twitter.get("cookie_env") or "TWITTER_COOKIE")
    if env_lookup(env, cookie_env):
        return True, f"{cookie_env} is set"
    if twitter.get("cookie_configured"):
        return True, "twitter auth set cookie was run during init"
    return False, f"{cookie_env} not set and twitter cookie was not configured during init"


def xurl_auth_configured(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    current_env = env if env is not None else os.environ
    for name in XURL_TOKEN_ENV_NAMES:
        if current_env.get(name):
            return True, f"{name} is set"

    content, detail = read_xurl_file(home)
    if not content:
        return False, detail
    if xurl_file_has(content, ("bearer_token", "access_token")):
        return True, f"{detail} contains xurl token"
    return False, f"{detail} does not contain bearer_token or access_token"


def xurl_client_credentials_configured(
    config: dict[str, Any],
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    xurl = xurl_auth_config(config)
    content, _ = read_xurl_file(home)
    client_id_ok = bool(xurl.get("client_id_provided")) or bool(env_lookup(env, str(xurl.get("client_id_env")))) or xurl_file_has(content, ("client_id", "client-id"))
    client_secret_ok = bool(xurl.get("client_secret_provided")) or bool(env_lookup(env, str(xurl.get("client_secret_env")))) or xurl_file_has(content, ("client_secret", "client-secret"))
    if client_id_ok and client_secret_ok:
        return True, "xurl client id and client secret are available"
    missing = []
    if not client_id_ok:
        missing.append("client id")
    if not client_secret_ok:
        missing.append("client secret")
    return False, f"xurl {' and '.join(missing)} missing"


def xurl_token_pair_configured(
    config: dict[str, Any],
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    xurl = xurl_auth_config(config)
    content, _ = read_xurl_file(home)
    access_ok = bool(xurl.get("access_token_provided")) or bool(env_lookup(env, str(xurl.get("access_token_env")))) or xurl_file_has(content, ("access_token", "access-token", "bearer_token"))
    refresh_ok = bool(xurl.get("refresh_token_provided")) or bool(env_lookup(env, str(xurl.get("refresh_token_env")))) or xurl_file_has(content, ("refresh_token", "refresh-token"))
    if access_ok and refresh_ok:
        return True, "xurl access token and refresh token are available"
    missing = []
    if not access_ok:
        missing.append("access token")
    if not refresh_ok:
        missing.append("refresh token")
    return False, f"xurl {' and '.join(missing)} missing; recommended for refresh"


def auth_error_message(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ("401", "unauthorized", "authentication required"))


def resolve_refresh_script(refresh_script: str | None = None) -> Path:
    return Path(refresh_script or DEFAULT_XURL_REFRESH_SCRIPT).expanduser()


def refresh_xurl_token(
    runner: Callable[[list[str], int], str] | None = None,
    refresh_script: str | None = None,
) -> None:
    runner = runner or run_command
    script = resolve_refresh_script(refresh_script)
    if not script.exists():
        raise AlertError(f"xurl refresh script not found: {script}")
    runner([sys.executable, str(script)], 120)


def schedule_doctor(
    paths: AlertPaths,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    config = read_json(paths.config, DEFAULT_CONFIG)
    schedule = schedule_config(config)
    checks: list[dict[str, str]] = []

    checks.append({"check": "python", "status": "pass", "detail": sys.version.split()[0]})
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        probe = paths.state / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append({"check": "state_writable", "status": "pass", "detail": str(paths.state)})
    except OSError as exc:
        checks.append({"check": "state_writable", "status": "fail", "detail": str(exc)})

    targets = enabled_target_count(paths)
    checks.append(
        {
            "check": "targets",
            "status": "pass" if targets > 0 else "fail",
            "detail": f"{targets} enabled target(s)",
        }
    )

    checks.append(
        {
            "check": "default_chat",
            "status": "pass" if has_delivery_target(paths, config) else "fail",
            "detail": "delivery target configured" if has_delivery_target(paths, config) else "no default or per-target chat configured",
        }
    )

    blogger_count = enabled_blogger_count(paths)
    if blogger_count > 0:
        twitter_ok, twitter_detail = twitter_cookie_configured(config, env=env)
        checks.append(
            {
                "check": "twitter_cookie",
                "status": "pass" if twitter_ok else "fail",
                "detail": twitter_detail if twitter_ok else f"{twitter_detail}; blogger targets require user-provided twitter-cli cookie",
            }
        )
    else:
        checks.append({"check": "twitter_cookie", "status": "skip", "detail": "no enabled blogger targets"})

    list_count = enabled_list_count(paths)
    if list_count > 0:
        xurl_ok, xurl_detail = xurl_auth_configured(home=home, env=env)
        checks.append(
            {
                "check": "xurl_auth",
                "status": "pass" if xurl_ok else "fail",
                "detail": xurl_detail if xurl_ok else f"{xurl_detail}; X List targets require user-provided xurl bearer token",
            }
        )
        client_ok, client_detail = xurl_client_credentials_configured(config, home=home, env=env)
        checks.append(
            {
                "check": "xurl_client_credentials",
                "status": "pass" if client_ok else "fail",
                "detail": client_detail if client_ok else f"{client_detail}; xurl requires user-provided client id and client secret",
            }
        )
        token_pair_ok, token_pair_detail = xurl_token_pair_configured(config, home=home, env=env)
        checks.append(
            {
                "check": "xurl_token_pair",
                "status": "pass" if token_pair_ok else "warn",
                "detail": token_pair_detail,
            }
        )
        refresh_script = resolve_refresh_script(str(config.get("xurl_refresh_script") or DEFAULT_XURL_REFRESH_SCRIPT))
        checks.append(
            {
                "check": "xurl_refresh_script",
                "status": "pass" if refresh_script.exists() else "warn",
                "detail": str(refresh_script) if refresh_script.exists() else f"{refresh_script} not found; expired xurl token must be refreshed manually",
            }
        )
    else:
        checks.append({"check": "xurl_auth", "status": "skip", "detail": "no enabled X List targets"})
        checks.append({"check": "xurl_client_credentials", "status": "skip", "detail": "no enabled X List targets"})
        checks.append({"check": "xurl_token_pair", "status": "skip", "detail": "no enabled X List targets"})
        checks.append({"check": "xurl_refresh_script", "status": "skip", "detail": "no enabled X List targets"})

    checks.append({"check": "run_command", "status": "pass", "detail": schedule_command(paths)})

    for tool in ("xurl", "twitter", "longbridge", "lark-cli"):
        checks.append(
            {
                "check": f"tool:{tool}",
                "status": "pass" if shutil.which(tool) else "warn",
                "detail": "available" if shutil.which(tool) else "not found; required only for related integration",
            }
        )

    ok = not any(item["status"] == "fail" for item in checks)
    return {"ok": ok, "schedule": schedule, "command": schedule_command(paths), "checks": checks}



# ── Tweet State ──────────────────────────────────────────────


def dedup_tweets(target_dir: Path, raw: Any) -> list[dict[str, Any]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    read_ids = read_json(target_dir / "read-ids.json", [])
    known = {str(item) for item in read_ids if item is not None}

    latest_send_status: dict[str, bool] = {}
    send_log = read_json(target_dir / "send-log.json", {"tweets": []})
    for entry in send_log.get("tweets", []):
        if isinstance(entry, dict) and entry.get("id") is not None and "sent" in entry:
            latest_send_status[str(entry["id"])] = bool(entry.get("sent"))
    failed = {tweet_id for tweet_id, sent in latest_send_status.items() if sent is False}

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for tweet in rows_from_raw(raw):
        tweet_id = str(tweet.get("id", ""))
        if not tweet_id or tweet_id in selected_ids:
            continue
        if tweet_id not in known or tweet_id in failed:
            selected.append(tweet)
            selected_ids.add(tweet_id)
    return selected


def mark_read(target_dir: Path, tweet_id: str) -> None:
    ids_file = target_dir / "read-ids.json"
    ids = read_json(ids_file, [])
    merged = {str(item) for item in ids if item is not None} | {str(tweet_id)}
    try:
        normalized = sorted(merged, key=lambda x: int(x))
    except (ValueError, TypeError):
        normalized = sorted(merged)
    if len(normalized) > MAX_READ_IDS:
        normalized = normalized[-MAX_READ_IDS:]
    write_json(ids_file, normalized)


def update_send_log(target_dir: Path, tweet_id: str, sent: bool, error: str = "") -> None:
    log_file = target_dir / "send-log.json"
    data = read_json(log_file, {"tweets": []})
    tweets = data.get("tweets", [])
    tweets.append(
        {
            "id": str(tweet_id),
            "sent": bool(sent),
            "error": error,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    write_json(log_file, {"tweets": tweets[-30:]})


def safe_state_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9@._-]", "_", value)



# ── LLM Analysis ─────────────────────────────────────────────


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw.strip():
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AlertError(f"HTTP response was not JSON: {raw[:300]}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AlertError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise AlertError(f"Network error calling {url}: {exc}") from exc


_BLOGGER_DEFAULT_SYSTEM_PROMPT = (
    "You are a financial tweet analyst producing structured analysis for a push notification system.\n\n"
    "Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain ALL of the following fields:\n\n"
    "- id: string, same as input id, do not modify\n"
    "- chinese_summary: string, ≤100 Chinese characters; summarize the core claim in plain, neutral language\n"
    "- symbols: array of stock/crypto tickers mentioned (e.g. [\"FISV\",\"PYPL\",\"INTC\"]); empty array if none\n"
    "- trend: string, one of \"bullish\" | \"bearish\" | \"neutral\"; reflect the author's implied direction on the mentioned assets\n"
    "- logic_score: integer 1–10 (10=well-sourced with data/facts, 1=pure emotion or anecdote)\n"
    "- logic_detail: string, 1–2 Chinese sentences explaining the logic score; cite the specific weakness or strength\n"
    "- sourcing_note: string, 1–2 Chinese sentences on how well the author's claims are sourced\n"
    "- sleaze_score: integer 1–10 (10=clear conflict of interest or promotional intent, 1=neutral/educational)\n"
    "- sleaze_detail: string, 1–2 Chinese sentences explaining the sleaze score; name the specific red flag if score ≥5\n"
    "- sleaze_note: string, one-sentence summary of sleaze_detail for compact display; empty string if score ≤3\n"
    "- prediction_check: string, extract any verifiable prediction with a timeframe in Chinese; use \"无预判\" if none\n\n"
    "Scoring guidance:\n"
    "- logic_score falls with: emotional language, unverified return claims, self-referential authority\n"
    "- logic_score rises with: cited filings, specific data, named sources, historical comparisons\n"
    "- sleaze_score rises with: undisclosed positions, urgency framing, repeated ticker mentions with no analysis, self-promotion\n"
    "- trend mapping: bullish=author implies price/sector up; bearish=down; neutral=observational or mixed\n\n"
    "Return only the JSON array, no markdown, no extra text."
)

_LIST_DEFAULT_SYSTEM_PROMPT = (
    "You are a financial news digest assistant. Tweets come from a curated X/Twitter List containing multiple accounts.\n\n"
    "Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain ONLY these fields:\n\n"
    "- id: string, same as input id, do not modify\n"
    "- chinese_summary: string, ≤60 Chinese characters; use neutral third-person voice starting with the author's name followed by a verb: 分析/认为/转推并评论/引用/预测/警告/披露\n"
    "- symbols: array of stock/crypto tickers explicitly mentioned (e.g. [\"TSMC\",\"NVDA\"]); empty array if none\n\n"
    "Rules for chinese_summary:\n"
    "- Start with the author's name (from the author field) followed by a verb\n"
    "- Capture the single most important claim or action in one sentence only\n"
    "- Do NOT evaluate, score, or editorialize; just describe what the author said or did\n"
    "- If it is a retweet with comment (RT), lead with \"[Name]转推并评论[核心观点]\"\n"
    "- If it is a quote tweet, lead with \"[Name]引用[被引用内容摘要]\"\n"
    "- Keep it factual and free of emotional language\n\n"
    "Return only the JSON array, no markdown, no extra text."
)


def _coerce_analysis(item: dict[str, Any], tweet_id: str) -> dict[str, Any]:
    result = dict(item)
    result["id"] = str(result.get("id") or tweet_id)
    result["chinese_summary"] = str(result.get("chinese_summary") or "")
    symbols = result.get("symbols")
    result["symbols"] = [str(s) for s in symbols] if isinstance(symbols, list) else []
    for key in ("logic_score", "sleaze_score"):
        try:
            result[key] = max(0, min(10, int(result.get(key) or 0)))
        except (ValueError, TypeError):
            result[key] = 0
    for key in ("trend", "sourcing_note", "logic_detail", "sleaze_note", "sleaze_detail", "prediction_check"):
        result[key] = str(result.get(key) or "")
    return result


def analyze_tweets(
    tweets: list[dict[str, Any]],
    config: dict[str, Any],
    mode: str = "blogger",
    warn: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    api_key_env = str(config.get("llm_api_key_env") or "OPENAI_API_KEY")
    api_key = resolve_token(api_key_env, ("llm", "api_key"), config=config, fallback_key="llm_api_key")
    if not api_key:
        return [fallback_analysis(tweet) for tweet in tweets]

    model = str(config.get("llm_model") or "gpt-4o-mini")
    base_url = str(config.get("llm_api_base") or "https://api.openai.com/v1").rstrip("/")
    max_tokens = int(config.get("max_tokens") or 8192)
    if mode == "list":
        default_prompt = _LIST_DEFAULT_SYSTEM_PROMPT
        prompt_key = "llm_system_prompt_list"
    else:
        default_prompt = _BLOGGER_DEFAULT_SYSTEM_PROMPT
        prompt_key = "llm_system_prompt_blogger"
    system_prompt = str(
        config.get(prompt_key) or config.get("llm_system_prompt") or default_prompt
    )

    by_id: dict[str, dict[str, Any]] = {}
    for chunk_start in range(0, len(tweets), ANALYZE_CHUNK_SIZE):
        chunk = tweets[chunk_start : chunk_start + ANALYZE_CHUNK_SIZE]
        compact_tweets = [
            {
                "id": str(tweet.get("id", "")),
                "author": tweet.get("author", {}),
                "text": str(tweet.get("text", ""))[:1000],
            }
            for tweet in chunk
        ]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(compact_tweets, ensure_ascii=False)},
            ],
            "max_tokens": max_tokens,
        }
        try:
            response = post_json(
                f"{base_url}/chat/completions",
                payload,
                {"Authorization": f"Bearer {api_key}"},
                timeout=120,
            )
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            content = strip_json_fence(str(content))
            parsed = json.loads(content)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("id"):
                        tid = str(item["id"])
                        by_id[tid] = _coerce_analysis(item, tid)
        except (AlertError, json.JSONDecodeError) as exc:
            if warn:
                warn(f"LLM chunk failed, using fallback: {exc}")

    return [by_id.get(str(tweet.get("id", "")), fallback_analysis(tweet)) for tweet in tweets]


def strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```json"):
        stripped = stripped.removeprefix("```json").strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```").strip()
    if stripped.endswith("```"):
        stripped = stripped[: -3].strip()
    return stripped



# ── Fetch ────────────────────────────────────────────────────

def run_command(args: list[str], timeout: int = 120) -> str:
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AlertError(f"Required command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[:300]
        raise AlertError(f"Command failed: {' '.join(args)}; {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AlertError(f"Command timed out after {timeout}s: {' '.join(args)}") from exc
    return completed.stdout


def fetch_tweets(
    mode: str,
    identifier: str,
    runner: Callable[[list[str], int], str] = run_command,
    refresh_script: str | None = None,
    auto_refresh: bool = True,
) -> Any:
    if mode == "list":
        endpoint = (
            f"/2/lists/{identifier}/tweets?max_results=20&"
            "tweet.fields=id,text,created_at,author_id,public_metrics&"
            "expansions=author_id&user.fields=id,name,username"
        )
        try:
            output = runner(["xurl", endpoint], 120)
        except AlertError as exc:
            if not auto_refresh or not auth_error_message(str(exc)):
                raise
            try:
                refresh_xurl_token(runner=runner, refresh_script=refresh_script)
            except AlertError as refresh_exc:
                raise AlertError(f"{exc}; {refresh_exc}") from refresh_exc
            output = runner(["xurl", endpoint], 120)
    elif mode == "blogger":
        output = runner(["twitter", "user-posts", identifier, "--max", "5", "--json"], 120)
    else:
        raise AlertError("mode must be list or blogger")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise AlertError(f"Fetch command returned invalid JSON: {output[:300]}") from exc


def fetch_market(
    symbols: Iterable[str],
    runner: Callable[[list[str], int], str] = run_command,
    config: dict[str, Any] | None = None,
) -> str:
    unique = sorted({str(symbol).upper() for symbol in symbols if str(symbol).strip()})
    if not unique or shutil.which("longbridge") is None:
        return ""
    market_suffix = str((config or {}).get("market_suffix") or "US")
    lines: list[str] = []
    for symbol in unique[:8]:
        safe_symbol = re.sub(r"[^A-Z0-9_.-]", "", symbol)
        if not safe_symbol:
            continue
        ticker = safe_symbol if "." in safe_symbol else f"{safe_symbol}.{market_suffix}"
        try:
            quote = json.loads(runner(["longbridge", "quote", ticker, "--format", "json"], 25))
            last = quote.get("last_done", "?")
            change = quote.get("change_rate", "0")
            prev = quote.get("prev_close", "?")
            lines.append(f"{ticker}: close ${last}, change {change}%, prev ${prev}")
            try:
                kline = json.loads(
                    runner(
                        ["longbridge", "kline", ticker, "--period", "day", "--count", "5", "--format", "json"],
                        25,
                    )
                )
                candles = kline.get("data", {}).get("candles", [])
                closes = [str(item.get("close")) for item in candles[-5:] if isinstance(item, dict) and item.get("close") is not None]
                if closes:
                    lines.append(f"{ticker} 5d: {'->'.join(closes)}")
            except (AlertError, json.JSONDecodeError):
                pass
        except AlertError:
            continue
    return "\n".join(lines)



# ── Routing ──────────────────────────────────────────────────

def resolve_platform(chat_id: str, platform: str = "auto") -> str:
    if platform and platform not in {"auto", "null"}:
        validate_platform(platform)
        return platform
    if chat_id.startswith(("oc_", "ou_")):
        return "feishu"
    if chat_id.startswith("-100") or chat_id.startswith("@"):
        return "telegram"
    if (
        chat_id.startswith("discord_dm_")
        or chat_id.startswith("discord_channel_")
        or chat_id.startswith("https://discord.com/api/webhooks/")
    ):
        return "discord"
    return "feishu"


def resolve_target_platform(config: dict[str, Any], chat_id: str, platform: str = "auto") -> str:
    if platform and platform not in {"auto", "null"}:
        validate_platform(platform)
        return platform
    if chat_id:
        return resolve_platform(chat_id, "auto")
    default_platform = str(config.get("default_platform") or "feishu")
    validate_platform(default_platform)
    return default_platform


def default_chat_for_platform(config: dict[str, Any], platform: str) -> str:
    platform_config = config.get("platforms", {}).get(platform, {})
    if platform == "feishu":
        return str(platform_config.get("default_chat_id") or "")
    if platform == "telegram":
        return str(platform_config.get("default_chat_id") or "")
    webhook = str(platform_config.get("webhook_url") or "")
    if webhook:
        return webhook
    return str(platform_config.get("default_channel_id") or "")



# ── Delivery ─────────────────────────────────────────────────

def send_with_retry(
    action: Callable[[], None],
    label: str,
    attempts: int = 3,
    sleeper: Callable[[int], None] = time.sleep,
) -> None:
    last_error: AlertError | None = None
    for attempt in range(1, attempts + 1):
        try:
            action()
            return
        except AlertError as exc:
            last_error = exc
            if attempt == attempts:
                break
            sleeper(3 if attempt == 1 else 10)
    raise AlertError(f"{label} send failed after {attempts} attempts: {last_error}")


def send_message(paths: AlertPaths, chat_id: str, message: str, platform: str = "auto") -> None:
    config = read_json(paths.config, DEFAULT_CONFIG)
    resolved = resolve_platform(chat_id, platform)
    limit = PLATFORM_MESSAGE_LIMITS.get(resolved, 0)
    if limit and len(message) > limit:
        message = message[: limit - 3] + "..."
    if resolved == "feishu":
        send_with_retry(lambda: send_feishu(chat_id, message), "feishu")
    elif resolved == "telegram":
        send_with_retry(lambda: send_telegram(config, chat_id, message), "telegram")
    elif resolved == "discord":
        send_with_retry(lambda: send_discord(config, chat_id, message), "discord")
    else:
        raise AlertError(f"Unsupported platform: {resolved}")


def send_feishu(chat_id: str, message: str) -> None:
    run_command(
        [
            "lark-cli",
            "im",
            "message",
            "send",
            "--as-bot",
            "--chat-id",
            chat_id,
            "--msg-type",
            "markdown",
            "--content",
            message,
        ],
        60,
    )


def env_from_config(config: dict[str, Any], path: list[str], fallback_env: str) -> str:
    current: Any = config
    for key in path:
        if not isinstance(current, dict):
            current = {}
            break
        current = current.get(key, {})
    env_name = current if isinstance(current, str) and current else fallback_env
    return os.environ.get(env_name) or os.environ.get(fallback_env, "")


def send_telegram(config: dict[str, Any], chat_id: str, message: str) -> None:
    token_env = str(
        config.get("platforms", {})
        .get("telegram", {})
        .get("bot_token_env")
        or "TELEGRAM_BOT_TOKEN"
    )
    token = resolve_token(token_env, ("telegram", "bot_token"), config=config)
    if not token:
        raise AlertError("Telegram bot token is not configured")
    response = post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": chat_id, "text": message},
        {},
        timeout=60,
    )
    if isinstance(response, dict) and response.get("ok") is not True:
        raise AlertError(f"Telegram send failed: {str(response)[:300]}")


def send_discord(config: dict[str, Any], chat_id: str, message: str) -> None:
    if chat_id.startswith("https://discord.com/api/webhooks/"):
        post_json(chat_id, {"content": message}, {}, timeout=60)
        return
    token_env = str(
        config.get("platforms", {})
        .get("discord", {})
        .get("bot_token_env")
        or "DISCORD_BOT_TOKEN"
    )
    token = resolve_token(token_env, ("discord", "bot_token"), config=config)
    if not token:
        raise AlertError("Discord bot token is not configured")
    channel_id = chat_id.removeprefix("discord_channel_")
    if chat_id.startswith("discord_dm_"):
        response = post_json(
            "https://discord.com/api/v10/users/@me/channels",
            {"recipient_id": chat_id.removeprefix("discord_dm_")},
            {"Authorization": f"Bot {token}"},
            timeout=60,
        )
        channel_id = str(response.get("id") or "")
        if not channel_id:
            raise AlertError("Discord DM channel creation failed")
    post_json(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        {"content": message},
        {"Authorization": f"Bot {token}"},
        timeout=60,
    )



# ── Run Loop ─────────────────────────────────────────────────

def _split_digest(entries: list[str], limit: int) -> list[str]:
    if not limit or not entries:
        return ["\n\n".join(entries)] if entries else []
    SEP = "\n\n"
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for entry in entries:
        entry = entry[:limit]
        add_len = len(SEP) + len(entry) if current else len(entry)
        if current and current_len + add_len > limit:
            chunks.append(SEP.join(current))
            current = [entry]
            current_len = len(entry)
        else:
            current.append(entry)
            current_len += add_len
    if current:
        chunks.append(SEP.join(current))
    return chunks


def process_target(
    paths: AlertPaths,
    mode: str,
    identifier: str,
    display_name: str,
    chat_id: str,
    platform: str,
    source_label: str,
    out: TextIO,
    config: dict[str, Any] | None = None,
) -> None:
    if config is None:
        config = read_json(paths.config, DEFAULT_CONFIG)
    if mode == "blogger":
        state_key = f"@{normalize_username(identifier)}"
    elif mode == "list":
        state_key = f"list-{normalize_list_id(identifier)}"
    else:
        state_key = source_label
    target_dir = paths.state / safe_state_key(state_key)
    raw = fetch_tweets(
        mode,
        identifier,
        refresh_script=str(config.get("xurl_refresh_script") or DEFAULT_XURL_REFRESH_SCRIPT),
        auto_refresh=bool(config.get("xurl_auto_refresh", True)),
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    write_json(target_dir / "raw.json", raw)
    candidate_tweets = dedup_tweets(target_dir, raw)
    new_tweets = candidate_tweets
    if mode == "list":
        new_tweets = filter_list_tweets(candidate_tweets, config)
        selected_ids = {str(tweet.get("id", "")) for tweet in new_tweets if tweet.get("id") is not None}
        for tweet in candidate_tweets:
            tweet_id = str(tweet.get("id", ""))
            if tweet_id and tweet_id not in selected_ids:
                mark_read(target_dir, tweet_id)
    print(f"{source_label}: {len(new_tweets)} new tweet(s)", file=out)
    if not new_tweets:
        return

    _warn = lambda msg: print(f"{source_label}: {msg}", file=out)  # noqa: E731
    analyses = analyze_tweets(new_tweets, config, mode=mode, warn=_warn)
    write_json(target_dir / "analysis-cache.json", analyses)
    analysis_by_id = {str(item.get("id", "")): item for item in analyses}
    resolved_platform = resolve_target_platform(config, chat_id, platform)
    target_chat = chat_id or default_chat_for_platform(config, resolved_platform)
    if not target_chat:
        raise AlertError(f"No chat target configured for {source_label}")

    if mode == "list":
        entries = [
            build_message_list_entry(
                tweet,
                analysis_by_id.get(str(tweet.get("id", "")), fallback_analysis(tweet)),
            )
            for tweet in new_tweets
        ]
        limit = PLATFORM_MESSAGE_LIMITS.get(resolved_platform, 0)
        chunks = _split_digest(entries, limit)
        for chunk_idx, chunk in enumerate(chunks):
            try:
                send_message(paths, target_chat, chunk, resolved_platform)
            except AlertError as exc:
                for tweet in new_tweets:
                    update_send_log(target_dir, str(tweet.get("id", "")), False, str(exc))
                print(f"{source_label}: send failed on chunk {chunk_idx + 1}/{len(chunks)}: {exc}", file=out)
                return
        for tweet in new_tweets:
            tweet_id = str(tweet.get("id", ""))
            mark_read(target_dir, tweet_id)
            update_send_log(target_dir, tweet_id, True)
        print(f"{source_label}: sent {len(new_tweets)} tweet(s) in {len(chunks)} message(s)", file=out)
    else:
        for tweet in new_tweets:
            tweet_id = str(tweet.get("id", ""))
            analysis = analysis_by_id.get(tweet_id, fallback_analysis(tweet))
            symbols = analysis.get("symbols") if isinstance(analysis.get("symbols"), list) else []
            market_info = fetch_market(symbols, config=config)
            message = build_message_blogger(tweet, analysis, market_info, source_label)
            try:
                send_message(paths, target_chat, message, resolved_platform)
            except AlertError as exc:
                update_send_log(target_dir, tweet_id, False, str(exc))
                print(f"{source_label}: send failed for {tweet_id}: {exc}", file=out)
                continue
            mark_read(target_dir, tweet_id)
            update_send_log(target_dir, tweet_id, True)
            print(f"{source_label}: sent {tweet_id}", file=out)


def iter_targets(paths: AlertPaths, target_mode: str, target_identifier: str) -> Iterable[tuple[str, dict[str, Any]]]:
    if target_mode == "blogger":
        target_identifier = normalize_username(target_identifier)
    elif target_mode == "list":
        target_identifier = normalize_list_id(target_identifier)

    if target_mode in {"all", "blogger"}:
        for blogger in read_json(paths.bloggers, DEFAULT_BLOGGERS).get("bloggers", []):
            if blogger.get("enabled") is False:
                continue
            username = str(blogger.get("username") or "")
            if not username or (target_mode == "blogger" and target_identifier != username):
                continue
            yield "blogger", blogger
    if target_mode in {"all", "list"}:
        for item in read_json(paths.lists, DEFAULT_LISTS).get("lists", []):
            if item.get("enabled") is False:
                continue
            list_id = str(item.get("id") or "")
            if not list_id or (target_mode == "list" and target_identifier != list_id):
                continue
            yield "list", item


def run_once(paths: AlertPaths, mode: str, identifier: str, out: TextIO) -> None:
    with RunLock(paths.lock) as acquired:
        if not acquired:
            print("another x-news-alert run is already active", file=out)
            return
        _run_once_unlocked(paths, mode, identifier, out)


def _run_once_unlocked(paths: AlertPaths, mode: str, identifier: str, out: TextIO) -> None:
    config = read_json(paths.config, DEFAULT_CONFIG)
    for target_mode, target in iter_targets(paths, mode, identifier):
        try:
            if target_mode == "blogger":
                username = str(target.get("username"))
                source_label = f"@{target.get('display_name') or username}"
                process_target(
                    paths,
                    "blogger",
                    username,
                    str(target.get("display_name") or username),
                    str(target.get("chat_id") or ""),
                    str(target.get("platform") or "auto"),
                    source_label,
                    out,
                    config=config,
                )
            else:
                list_id = str(target.get("id"))
                source_label = f"List {target.get('display_name') or list_id}"
                process_target(
                    paths,
                    "list",
                    list_id,
                    str(target.get("display_name") or list_id),
                    str(target.get("chat_id") or ""),
                    str(target.get("platform") or "auto"),
                    source_label,
                    out,
                    config=config,
                )
        except AlertError as exc:
            target_name = str(target.get("username") or target.get("id") or target_mode)
            print(f"{target_name}: {exc}", file=out)


def reset_state(paths: AlertPaths, mode: str, identifier: str) -> Path:
    if mode == "blogger":
        state_key = f"@{normalize_username(identifier)}"
    elif mode == "list":
        state_key = f"list-{normalize_list_id(identifier)}"
    else:
        raise AlertError("reset-state mode must be blogger or list")
    ids_file = paths.state / safe_state_key(state_key) / "read-ids.json"
    ids_file.unlink(missing_ok=True)
    return ids_file


def show_status(paths: AlertPaths, out: TextIO) -> None:
    config = read_json(paths.config, DEFAULT_CONFIG)
    schedule = schedule_config(config)
    bloggers = read_json(paths.bloggers, DEFAULT_BLOGGERS).get("bloggers", [])
    lists = read_json(paths.lists, DEFAULT_LISTS).get("lists", [])
    print("Config", file=out)
    print(json.dumps(
        {
            "default_platform": config.get("default_platform"),
            "schedule_enabled": schedule["enabled"],
            "schedule_mode": schedule["mode"],
            "cron_schedule": schedule["cron"],
            "polling_interval_minutes": schedule["polling_interval_minutes"],
        },
        ensure_ascii=False,
        indent=2,
    ), file=out)
    print("Bloggers", file=out)
    print(json.dumps(bloggers, ensure_ascii=False, indent=2), file=out)
    print("Lists", file=out)
    print(json.dumps(lists, ensure_ascii=False, indent=2), file=out)



# ── CLI ──────────────────────────────────────────────────────

def normalize_cli_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    normalized = list(argv)
    chat_position_commands = {"send": 1, "set-default-chat": 2}
    for index, token in enumerate(normalized):
        if token not in chat_position_commands:
            continue
        value_index = index + chat_position_commands[token]
        if value_index < len(normalized) and normalized[value_index].startswith("-") and normalized[value_index] != "--":
            normalized.insert(value_index, "--")
        break
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="x-news-alert-v3 portable CLI")
    parser.add_argument(
        "--base-dir",
        default=str(default_base_dir()),
        help="Directory for config and state files. Defaults to X_NEWS_ALERT_HOME or the user config directory.",
    )
    parser.add_argument(
        "--structured",
        action="store_true",
        help="Wrap machine-readable JSON output in an ok/schema_version/data envelope.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", aliases=["wizard"], help="Run the interactive first-time setup wizard")

    init = sub.add_parser("init", aliases=["i"], help="Initialize config, targets, and scheduler metadata")
    init.add_argument("--platform", choices=sorted(SUPPORTED_PLATFORMS), default="feishu")
    init.add_argument("--chat-id", default="")
    init.add_argument("--llm-provider", default="openai")
    init.add_argument("--llm-api-base", default="https://api.openai.com/v1")
    init.add_argument("--llm-api-key-env", default="OPENAI_API_KEY")
    init.add_argument("--llm-model", default="gpt-4o-mini")
    init.add_argument("--twitter-cookie", default="")
    init.add_argument("--twitter-cookie-env", default="TWITTER_COOKIE")
    init.add_argument("--xurl-client-id", default="")
    init.add_argument("--xurl-client-secret", default="")
    init.add_argument("--xurl-access-token", default="")
    init.add_argument("--xurl-refresh-token", default="")
    init.add_argument("--xurl-client-id-env", default="XURL_CLIENT_ID")
    init.add_argument("--xurl-client-secret-env", default="XURL_CLIENT_SECRET")
    init.add_argument("--xurl-access-token-env", default="XURL_ACCESS_TOKEN")
    init.add_argument("--xurl-refresh-token-env", default="XURL_REFRESH_TOKEN")
    init.add_argument("--blogger", default="")
    init.add_argument("--list", dest="list_id", default="")
    init.add_argument("--enable-schedule", action="store_true")
    init.add_argument("--schedule-mode", choices=["external", "poll", "system-cron", "codex"], default="external")
    init.add_argument("--interval", type=int, default=None)
    init.add_argument("--cron", default=None)
    init.add_argument("--timezone", default=None)
    init.add_argument("--interactive", action="store_true")

    track = sub.add_parser("track", aliases=["t"], help="Add a blogger and run it once")
    track.add_argument("target")
    track.add_argument("routes", nargs="*")

    add_b = sub.add_parser("add-blogger", aliases=["ab"], help="Add or replace a blogger target")
    add_b.add_argument("target")
    add_b.add_argument("routes", nargs="*")

    rm_b = sub.add_parser("remove-blogger", aliases=["rm", "rb"], help="Remove a blogger target")
    rm_b.add_argument("target")

    add_l = sub.add_parser("add-list", aliases=["addList", "al"], help="Add or replace an X List target")
    add_l.add_argument("target")
    add_l.add_argument("routes", nargs="*")

    rm_l = sub.add_parser("remove-list", aliases=["rmList", "rl"], help="Remove an X List target")
    rm_l.add_argument("target")

    sp = sub.add_parser("set-platform", aliases=["platform", "p"], help="Set default send platform")
    sp.add_argument("platform", choices=sorted(SUPPORTED_PLATFORMS))

    sdc = sub.add_parser("set-default-chat", aliases=["chat", "c"], help="Set default chat/webhook for a platform")
    sdc.add_argument("platform", choices=sorted(SUPPORTED_PLATFORMS))
    sdc.add_argument("chat_id")

    cron = sub.add_parser("cron", aliases=["cr"], help="Toggle cron_enabled in config")
    cron.add_argument("state", choices=["on", "off"])

    schedule = sub.add_parser("schedule", aliases=["sched", "sc"], help="Inspect and configure platform-neutral scheduling")
    schedule_sub = schedule.add_subparsers(dest="schedule_action", required=True)
    schedule_sub.add_parser("show", aliases=["s"], help="Print normalized schedule config")
    schedule_sub.add_parser("command", aliases=["cmd"], help="Print the single-run command for external schedulers")
    schedule_sub.add_parser("doctor", aliases=["d"], help="Check local readiness for scheduled runs")
    schedule_enable = schedule_sub.add_parser("enable", aliases=["e"], help="Enable schedule metadata")
    schedule_enable.add_argument("--mode", choices=["external", "poll", "system-cron", "codex"], default="external")
    schedule_enable.add_argument("--cron", default=None)
    schedule_enable.add_argument("--interval", type=int, default=None)
    schedule_enable.add_argument("--timezone", default=None)
    schedule_disable = schedule_sub.add_parser("disable", aliases=["dis"], help="Disable schedule metadata")
    schedule_disable.set_defaults(schedule_action="disable")

    run = sub.add_parser("run", aliases=["rn"], help="Run all, one blogger, or one list")
    run.add_argument("mode", choices=["all", "blogger", "list"], nargs="?", default="all")
    run.add_argument("identifier", nargs="?")

    poll = sub.add_parser("poll", aliases=["pl"], help="Run forever with polling_interval_minutes")
    poll.add_argument("--once", action="store_true", help="Run one loop and exit")

    fetch = sub.add_parser("fetch", aliases=["f"], help="Compatibility: fetch raw tweets as JSON")
    fetch.add_argument("mode", choices=["blogger", "list"])
    fetch.add_argument("identifier")

    analyze = sub.add_parser("analyze", aliases=["an"], help="Compatibility: analyze a tweet JSON file")
    analyze.add_argument("mode", choices=["single", "batch"])
    analyze.add_argument("file")

    market = sub.add_parser("market", aliases=["m"], help="Compatibility: fetch market snippets")
    market.add_argument("symbols", nargs="+")

    send = sub.add_parser("send", aliases=["snd"], help="Compatibility: send one message")
    send.add_argument("chat_id")
    send.add_argument("message")
    send.add_argument("platform", nargs="?", default="auto")

    reset = sub.add_parser("reset-state", aliases=["reset"], help="Clear read-ids for a target so past tweets can be re-sent")
    reset.add_argument("mode", choices=["blogger", "list"])
    reset.add_argument("identifier")

    sub.add_parser("status", aliases=["st"], help="Show compact status")
    sub.add_parser("config", aliases=["cfg"], help="Print full config JSON")
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    ensure_utf8_streams()
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(normalize_cli_argv(argv))
    paths = AlertPaths(args.base_dir)
    structured = bool(getattr(args, "structured", False))
    try:
        ensure_files(paths)
        if args.command in ("setup", "wizard"):
            run_init_wizard(paths, out=out)
            print("initialized", file=out)
        elif args.command in ("init", "i"):
            if args.interactive:
                run_init_wizard(paths, out=out)
            else:
                init_runtime(
                    paths,
                    platform=args.platform,
                    chat_id=args.chat_id,
                    llm_provider=args.llm_provider,
                    llm_api_base=args.llm_api_base,
                    llm_api_key_env=args.llm_api_key_env,
                    llm_model=args.llm_model,
                    twitter_cookie=args.twitter_cookie,
                    twitter_cookie_env=args.twitter_cookie_env,
                    xurl_client_id=args.xurl_client_id,
                    xurl_client_secret=args.xurl_client_secret,
                    xurl_access_token=args.xurl_access_token,
                    xurl_refresh_token=args.xurl_refresh_token,
                    xurl_client_id_env=args.xurl_client_id_env,
                    xurl_client_secret_env=args.xurl_client_secret_env,
                    xurl_access_token_env=args.xurl_access_token_env,
                    xurl_refresh_token_env=args.xurl_refresh_token_env,
                    blogger=args.blogger,
                    list_id=args.list_id,
                    enable_schedule=args.enable_schedule,
                    schedule_mode=args.schedule_mode,
                    interval=args.interval,
                    cron=args.cron,
                    timezone=args.timezone,
                )
            print("initialized", file=out)
        elif args.command in ("track", "t"):
            username = add_blogger(paths, args.target, args.routes)
            print(f"blogger added: @{username}", file=out)
            run_once(paths, "blogger", username, out)
        elif args.command in ("add-blogger", "ab"):
            username = add_blogger(paths, args.target, args.routes)
            print(f"blogger added: @{username}", file=out)
        elif args.command in ("remove-blogger", "rm", "rb"):
            username = remove_blogger(paths, args.target)
            print(f"blogger removed: @{username}", file=out)
        elif args.command in ("add-list", "addList", "al"):
            list_id = add_list(paths, args.target, args.routes)
            print(f"list added: {list_id}", file=out)
        elif args.command in ("remove-list", "rmList", "rl"):
            list_id = remove_list(paths, args.target)
            print(f"list removed: {list_id}", file=out)
        elif args.command in ("set-platform", "platform", "p"):
            set_platform(paths, args.platform)
            print(f"default platform: {args.platform}", file=out)
        elif args.command in ("set-default-chat", "chat", "c"):
            set_default_chat(paths, args.platform, args.chat_id)
            print(f"default {args.platform} target set", file=out)
        elif args.command in ("cron", "cr"):
            schedule = set_schedule(paths, enabled=args.state == "on")
            print(f"cron_enabled={str(schedule['enabled']).lower()}", file=out)
        elif args.command == "schedule":
            if args.schedule_action == "show":
                config = sync_schedule_legacy_fields(read_json(paths.config, DEFAULT_CONFIG))
                emit_data(out, schedule_config(config), structured)
            elif args.schedule_action == "command":
                print(schedule_command(paths), file=out)
            elif args.schedule_action == "doctor":
                report = schedule_doctor(paths)
                emit_data(out, report, structured)
                return 0 if report["ok"] else 1
            elif args.schedule_action == "enable":
                schedule = set_schedule(
                    paths,
                    enabled=True,
                    mode=args.mode,
                    cron=args.cron,
                    interval=args.interval,
                    timezone=args.timezone,
                )
                emit_data(out, schedule, structured)
            elif args.schedule_action == "disable":
                schedule = set_schedule(paths, enabled=False)
                emit_data(out, schedule, structured)
        elif args.command in ("reset-state", "reset"):
            ids_file = reset_state(paths, args.mode, args.identifier)
            print(f"reset: {ids_file}", file=out)
        elif args.command in ("run", "rn"):
            if args.mode in {"blogger", "list"} and not args.identifier:
                raise AlertError("run blogger/list requires an identifier")
            run_once(paths, args.mode, args.identifier or "", out)
        elif args.command in ("poll", "pl"):
            while True:
                run_once(paths, "all", "", out)
                if args.once:
                    break
                config = read_json(paths.config, DEFAULT_CONFIG)
                sched = schedule_config(config)
                minutes = int(sched.get("polling_interval_minutes") or 30)
                jitter = int(sched.get("jitter_seconds") or 0)
                sleep_seconds = max(1, minutes) * 60
                if jitter > 0:
                    sleep_seconds += random.uniform(0, jitter)
                time.sleep(sleep_seconds)
        elif args.command in ("fetch", "f"):
            raw = fetch_tweets(args.mode, args.identifier)
            emit_data(out, raw, structured)
        elif args.command in ("analyze", "an"):
            config = read_json(paths.config, DEFAULT_CONFIG)
            raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
            if args.mode == "single":
                result = analyze_tweets([raw], config)[0]
            else:
                tweets = raw if isinstance(raw, list) else rows_from_raw(raw)
                result = analyze_tweets(tweets, config)
            emit_data(out, result, structured)
        elif args.command in ("market", "m"):
            print(fetch_market(args.symbols), file=out)
        elif args.command in ("send", "snd"):
            send_message(paths, args.chat_id, args.message, args.platform)
            print("sent", file=out)
        elif args.command in ("status", "st"):
            show_status(paths, out)
        elif args.command in ("config", "cfg"):
            emit_data(out, read_json(paths.config, DEFAULT_CONFIG), structured)
        else:
            parser.print_help(err)
            return 2
    except AlertError as exc:
        if structured:
            print_json(err, error_payload("alert_error", str(exc)))
        else:
            print(f"error: {exc}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
