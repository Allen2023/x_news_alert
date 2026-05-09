#!/usr/bin/env python3
"""
xurl Token 主动刷新检查脚本
定时运行：每小时检查一次，如果 token 快过期（<30分钟）则刷新
用法：python scripts/xurl-token-check.py
Crontab: 57 * * * * cd /path/to/x-news-alert-v3 && python scripts/xurl-token-check.py >> logs/xurl-refresh.log 2>&1
"""
import json
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_loader import get_xurl_config, PROJECT_ROOT, CREDENTIALS_FILE

XURL_FILE = Path.home() / ".xurl"
LOG_FILE = PROJECT_ROOT / "logs" / "xurl-refresh.log"
BUFFER_MINUTES = 30  # 过期前30分钟就刷新


def log(msg: str):
    """写入日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {msg}"
    print(log_line, file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")


def is_token_expiring_soon() -> tuple[bool, str]:
    """
    检查 token 是否快过期
    返回 (是否快过期, 状态描述)
    """
    if not XURL_FILE.exists():
        # 如果没有 ~/.xurl，但配置文件里有 client_id/client_secret
        # 说明还没初始化，先不刷新
        xurl_config = get_xurl_config()
        if not xurl_config["client_id"]:
            return False, "xurl 未配置（无 ~/.xurl 且 credentials.yaml 无 client_id）"
        return True, "xurl 文件不存在，但配置文件存在"

    try:
        content = json.loads(XURL_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return True, f"解析 xurl 文件失败: {e}"

    # 检查 access_token 是否存在
    access_token = content.get("access_token") or content.get("access-token")
    if not access_token:
        return True, "没有 access_token"

    # 检查过期时间
    expires_at = content.get("expiration_time") or content.get("expires_at")
    if not expires_at:
        # 没有过期时间，保守起见当作快过期
        return True, "没有 expiration_time"

    # 解析时间
    try:
        if isinstance(expires_at, (int, float)):
            expiry_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        else:
            expiry_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except Exception as e:
        return True, f"解析过期时间失败: {e}"

    # 计算剩余时间
    now = datetime.now(timezone.utc)
    remaining = expiry_dt - now

    if remaining.total_seconds() <= 0:
        return True, "Token 已过期"

    remaining_minutes = remaining.total_seconds() / 60
    remaining_str = f"{int(remaining_minutes)}分钟"

    if remaining < timedelta(minutes=BUFFER_MINUTES):
        return True, f"Token 剩余 {remaining_str}，需要刷新"

    return False, f"Token 充足，剩余 {remaining_str}"


def refresh_token() -> bool:
    """执行刷新"""
    log("执行 xurl auth refresh...")

    # 尝试 xurl 自己的刷新命令
    result = subprocess.run(
        ["xurl", "auth", "refresh"],
        capture_output=True,
        text=True,
        timeout=120
    )

    if result.returncode == 0:
        log("刷新成功")
        return True

    # 如果 xurl 命令失败，尝试备用方案：调用 Twitter OAuth2 端点
    log(f"xurl 命令失败: {result.stderr}")
    log("尝试备用刷新方案...")

    try:
        if not XURL_FILE.exists():
            log("没有 ~/.xurl 文件，无法备用刷新")
            return False
        xurl_content = json.loads(XURL_FILE.read_text(encoding="utf-8"))
        return fallback_refresh(xurl_content)
    except Exception as e:
        log(f"读取 xurl 文件失败: {e}")
        return False


def fallback_refresh(xurl_content: dict) -> bool:
    """
    备用刷新方案：直接调 Twitter OAuth2 端点
    需要 client_id, client_secret, refresh_token
    """
    client_id = xurl_content.get("client_id")
    client_secret = xurl_content.get("client_secret")
    refresh_token_val = xurl_content.get("refresh_token") or xurl_content.get("refresh-token")

    # 如果 xurl 文件里没有，尝试从 credentials.yaml 读取
    if not client_id or not client_secret or not refresh_token_val:
        xurl_config = get_xurl_config()
        client_id = client_id or xurl_config["client_id"]
        client_secret = client_secret or xurl_config["client_secret"]
        refresh_token_val = refresh_token_val or xurl_config["refresh_token"]

    if not all([client_id, client_secret, refresh_token_val]):
        log("缺少 client_id/client_secret/refresh_token")
        return False

    import urllib.request
    import urllib.parse

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_val,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()

    req = urllib.request.Request(
        "https://api.twitter.com/2/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())

    new_access_token = result.get("access_token")
    new_refresh_token = result.get("refresh_token")

    if new_access_token:
        # 更新 xurl 文件
        xurl_content["access_token"] = new_access_token
        if new_refresh_token:
            xurl_content["refresh_token"] = new_refresh_token
        # 更新过期时间（约2小时）
        xurl_content["expiration_time"] = (
            datetime.now(timezone.utc) + timedelta(seconds=result.get("expires_in", 7200))
        ).timestamp()

        XURL_FILE.write_text(json.dumps(xurl_content, indent=2, ensure_ascii=False))
        log("备用刷新成功")
        return True

    return False


def main():
    log("=" * 50)
    log("开始检查 xurl token 状态")

    expiring, status = is_token_expiring_soon()
    log(f"检查结果: {status}")

    if not expiring:
        log("不需要刷新，退出")
        return 0

    log("开始刷新 token...")
    if refresh_token():
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
