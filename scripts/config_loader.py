#!/usr/bin/env python3
"""
凭证配置加载模块
从 credentials.yaml 读取所有敏感配置
"""
import os
import yaml
from pathlib import Path
from typing import Any

# 项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.yaml"

_config_cache: dict[str, Any] | None = None


def load_credentials() -> dict[str, Any]:
    """加载 credentials.yaml，缓存结果"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if not CREDENTIALS_FILE.exists():
        return {}

    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        _config_cache = yaml.safe_load(f) or {}
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
