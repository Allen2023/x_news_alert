#!/usr/bin/env python3
"""
凭证配置加载模块
从 credentials.yaml 读取所有敏感配置
"""
import importlib
import os
from pathlib import Path
from typing import Any

# 项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def default_app_dir() -> Path:
    configured = os.environ.get("X_NEWS_ALERT_HOME")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / "x-news-alert"
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config).expanduser() / "x-news-alert"
    return Path.home() / ".config" / "x-news-alert"


def default_credentials_file() -> Path:
    configured = os.environ.get("X_NEWS_ALERT_CREDENTIALS")
    if configured:
        return Path(configured).expanduser()
    candidates = [
        default_app_dir() / "credentials.yaml",
        Path.cwd() / "credentials.yaml",
        PROJECT_ROOT / "credentials.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


CREDENTIALS_FILE = default_credentials_file()

_config_cache: dict[str, Any] | None = None


def _load_yaml_module() -> Any:
    """延迟加载 PyYAML，避免没有凭证文件时把它变成硬依赖。"""
    try:
        return importlib.import_module("yaml")
    except ModuleNotFoundError as exc:
        raise RuntimeError("PyYAML is required to read credentials.yaml; install it with `pip install pyyaml`.") from exc


def load_credentials() -> dict[str, Any]:
    """加载 credentials.yaml，缓存结果"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not CREDENTIALS_FILE.exists():
        return {}

    yaml_module = _load_yaml_module()
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        loaded = yaml_module.safe_load(f) or {}
    _config_cache = loaded if isinstance(loaded, dict) else {}
    return _config_cache


def get_credential(*keys: str, default: str = "") -> str:
    """
    获取凭证值，支持多级 key 查找
    例如: get_credential("twitter", "cookie")
    如果环境变量存在同名值，优先使用环境变量
    """
    creds = load_credentials()
    val = creds
    for key in keys:
        if isinstance(val, dict):
            val = val.get(key)
        else:
            return default
        if val is None:
            return default

    # 尝试从环境变量读取（key 转为大写下划线格式）
    env_key = "_".join(k.upper() for k in keys)
    if env_key in os.environ and os.environ[env_key]:
        return os.environ[env_key]
    if isinstance(val, str) and val:
        return val
    return default


def get_twitter_cookie() -> str:
    """获取 Twitter cookie"""
    return get_credential("twitter", "cookie")


def get_xurl_config() -> dict[str, str]:
    """获取 xurl 完整配置"""
    return {
        "client_id": get_credential("xurl", "client_id"),
        "client_secret": get_credential("xurl", "client_secret"),
        "access_token": get_credential("xurl", "access_token"),
        "refresh_token": get_credential("xurl", "refresh_token"),
    }


def get_llm_config() -> dict[str, str]:
    """获取 LLM 配置"""
    return {
        "api_key": get_credential("llm", "api_key"),
        "api_base": get_credential("llm", "api_base", default="https://api.openai.com/v1"),
        "model": get_credential("llm", "model", default="gpt-4o-mini"),
    }


def get_discord_bot_token() -> str:
    """获取 Discord bot token"""
    return get_credential("discord", "bot_token")


def get_telegram_bot_token() -> str:
    """获取 Telegram bot token"""
    return get_credential("telegram", "bot_token")


def get_feishu_config() -> dict[str, str]:
    """获取 Feishu 配置"""
    return {
        "webhook": get_credential("feishu", "webhook"),
        "app_id": get_credential("feishu", "app_id"),
        "app_secret": get_credential("feishu", "app_secret"),
    }
