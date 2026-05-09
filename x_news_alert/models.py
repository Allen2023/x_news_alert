"""Data models for x-news-alert."""
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class AlertError(RuntimeError):
    """Expected operational error reported cleanly by the CLI."""


@dataclass
class AlertPaths:
    """File paths for alert runtime state."""
    base: Path
    config: Path
    bloggers: Path
    lists: Path
    state: Path
    lock: Path

    def __init__(self, base_dir: str | Path):
        self.base = Path(base_dir)
        self.config = self.base / "alert-config.json"
        self.bloggers = self.base / "alert-bloggers.json"
        self.lists = self.base / "alert-lists.json"
        self.state = self.base / "alert-state"
        self.lock = self.state / "run.lock"


@dataclass
class RunLock:
    """Lock file to prevent concurrent runs."""
    path: Path
    stale_seconds: int = 6 * 60 * 60
    fd: int | None = None
    acquired: bool = False

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.acquired = self._try_acquire()
        return self.acquired

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def _try_acquire(self) -> bool:
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if self._is_stale():
                self.path.unlink(missing_ok=True)
                return self._try_acquire()
            return False
        payload = json.dumps({"pid": os.getpid(), "time": time.time()}).encode("utf-8")
        os.write(self.fd, payload)
        return True

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid and os.name == "posix":
                try:
                    os.kill(pid, 0)
                    return False
                except ProcessLookupError:
                    return True
                except OSError:
                    pass
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        try:
            return time.time() - self.path.stat().st_mtime > self.stale_seconds
        except FileNotFoundError:
            return True


@dataclass
class TwitterCredentials:
    """Twitter/X authentication credentials."""
    cookie: str = ""
    cookie_env: str = "TWITTER_COOKIE"


@dataclass
class XUrlCredentials:
    """X URL OAuth credentials for List API."""
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    client_id_env: str = "XURL_CLIENT_ID"
    client_secret_env: str = "XURL_CLIENT_SECRET"
    access_token_env: str = "XURL_ACCESS_TOKEN"
    refresh_token_env: str = "XURL_REFRESH_TOKEN"


@dataclass
class LLMConfig:
    """LLM API configuration."""
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    max_tokens: int = 8192
    api_key_env: str = "OPENAI_API_KEY"


@dataclass
class PlatformConfig:
    """Messaging platform configuration."""
    feishu_webhook: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    discord_bot_token: str = ""
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    default_chat_id: str = ""
    default_platform: str = "feishu"


@dataclass
class ScheduleConfig:
    """Scheduler configuration."""
    enabled: bool = False
    mode: str = "external"
    cron: str = "0 0,2,5,7,9,10,12,15,17,20,22 * * *"
    timezone: str = "Asia/Shanghai"
    polling_interval_minutes: int = 30
    jitter_seconds: int = 60
    lock_ttl_seconds: int = 21600


@dataclass
class AlertConfig:
    """Main alert configuration."""
    default_platform: str = "feishu"
    platforms: dict[str, Any] = field(default_factory=dict)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_api_key_env: str = "OPENAI_API_KEY"
    market_suffix: str = "US"
    twitter_cli: TwitterCredentials = field(default_factory=TwitterCredentials)
    xurl_auth: XUrlCredentials = field(default_factory=XUrlCredentials)
    xurl_auto_refresh: bool = True
    xurl_refresh_script: str = "~/x-token-refresh/refresh_x_token.py"


@dataclass
class BloggerTarget:
    """A tracked blogger target."""
    username: str
    display_name: str = ""
    chat_id: str = ""
    platform: str = "auto"
    enabled: bool = True


@dataclass
class ListTarget:
    """A tracked X List target."""
    id: str
    display_name: str = ""
    chat_id: str = ""
    platform: str = "auto"
    enabled: bool = True


@dataclass
class TweetMetrics:
    """Tweet engagement metrics."""
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    quotes: int = 0
    views: int = 0
    bookmarks: int = 0


@dataclass
class Tweet:
    """A single tweet."""
    id: str
    text: str
    author: str
    author_id: str = ""
    created_at: str = ""
    metrics: TweetMetrics = field(default_factory=TweetMetrics)
    is_retweet: bool = False
    is_reply: bool = False
    is_quote: bool = False
    has_media: bool = False
    has_links: bool = False
    lang: str = ""


@dataclass
class AnalysisResult:
    """LLM analysis result for a tweet."""
    id: str
    chinese_summary: str = ""
    symbols: list[str] = field(default_factory=list)
    logic_score: int = 0
    sleaze_score: int = 0
    trend: str = ""
    sourcing_note: str = ""
    logic_detail: str = ""
    sleaze_note: str = ""
    sleaze_detail: str = ""
    prediction_check: str = ""
    market_context: str = ""


@dataclass
class MarketQuote:
    """Market quote for a symbol."""
    symbol: str
    last_done: str = ""
    change_rate: str = ""
    prev_close: str = ""
    kline_5day: str = ""


@dataclass
class SendResult:
    """Result of sending an alert."""
    platform: str
    chat_id: str
    success: bool
    tweet_id: str = ""
    error: str = ""
