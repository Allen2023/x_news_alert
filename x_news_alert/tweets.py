"""Tweet normalization, scoring, and message formatting helpers."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

DEFAULT_LIST_FILTER: dict[str, Any] = {
    "enabled": False,
    "mode": "topN",
    "topN": 20,
    "minScore": 0,
    "lang": [],
    "excludeRetweets": False,
    "weights": {
        "likes": 1.0,
        "retweets": 3.0,
        "replies": 2.0,
        "bookmarks": 5.0,
        "views_log": 0.5,
    },
}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return ""


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _metric_from(sources: Iterable[Mapping[str, Any]], *names: str) -> Any:
    for source in sources:
        for name in names:
            value = source.get(name)
            if value is not None and value != "":
                return value
    return 0


def _normalize_metrics(row: Mapping[str, Any]) -> dict[str, int]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    public_metrics = row.get("public_metrics") if isinstance(row.get("public_metrics"), Mapping) else {}
    legacy = row.get("legacy") if isinstance(row.get("legacy"), Mapping) else {}
    sources = [metrics, public_metrics, legacy, row]
    return {
        "likes": _coerce_int(_metric_from(sources, "likes", "like_count", "favorite_count", "favoriteCount")),
        "retweets": _coerce_int(_metric_from(sources, "retweets", "retweet_count", "retweetCount")),
        "replies": _coerce_int(_metric_from(sources, "replies", "reply_count", "replyCount")),
        "quotes": _coerce_int(_metric_from(sources, "quotes", "quote_count", "quoteCount")),
        "bookmarks": _coerce_int(_metric_from(sources, "bookmarks", "bookmark_count", "bookmarkCount")),
        "views": _coerce_int(
            _metric_from(
                sources,
                "views",
                "view_count",
                "viewCount",
                "impression_count",
                "impressions",
                "viewsCount",
            )
        ),
    }


def _normalize_author(author: Any, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = author if isinstance(author, Mapping) else {}
    fallback = fallback or {}
    username = str(
        _first_non_empty(
            source.get("username"),
            source.get("screenName"),
            source.get("screen_name"),
            fallback.get("username"),
            fallback.get("screenName"),
            fallback.get("screen_name"),
        )
    )
    author_id = str(
        _first_non_empty(
            source.get("id"),
            source.get("id_str"),
            source.get("rest_id"),
            source.get("author_id"),
            fallback.get("author_id"),
            fallback.get("user_id"),
        )
    )
    name = str(_first_non_empty(source.get("name"), fallback.get("name"), username, "unknown"))
    normalized: dict[str, Any] = {"id": author_id, "name": name, "username": username}
    if username:
        normalized["screenName"] = username
    for key in ("verified", "is_blue_verified", "profileImageUrl", "profile_image_url"):
        if source.get(key) is not None:
            normalized[key] = source.get(key)
    return normalized


def _author_lookup_from_raw(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}
    includes = raw.get("includes") if isinstance(raw.get("includes"), Mapping) else {}
    users = includes.get("users") if isinstance(includes.get("users"), list) else []
    lookup: dict[str, dict[str, Any]] = {}
    for user in users:
        if not isinstance(user, Mapping):
            continue
        normalized = _normalize_author(user)
        for key in ("id", "id_str", "rest_id", "username", "screenName", "screen_name"):
            value = user.get(key) or normalized.get(key)
            if value:
                lookup[str(value)] = normalized
    return lookup


def normalize_tweet_row(
    row: Mapping[str, Any],
    author_lookup: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    legacy = row.get("legacy") if isinstance(row.get("legacy"), Mapping) else {}
    tweet_id = str(_first_non_empty(row.get("id"), row.get("id_str"), row.get("rest_id"), row.get("tweet_id")))
    author_id = str(
        _first_non_empty(
            row.get("author_id"),
            row.get("user_id"),
            row.get("userId"),
            legacy.get("user_id_str"),
        )
    )
    raw_author = row.get("author") if isinstance(row.get("author"), Mapping) else row.get("user")
    lookup_author = (author_lookup or {}).get(author_id) if author_id else None
    if isinstance(lookup_author, Mapping):
        merged_author = dict(lookup_author)
        if isinstance(raw_author, Mapping):
            merged_author.update(raw_author)
        raw_author = merged_author

    metrics = _normalize_metrics(row)
    normalized = dict(row)
    normalized["id"] = tweet_id
    normalized["text"] = str(
        _first_non_empty(
            row.get("text"),
            row.get("fullText"),
            row.get("full_text"),
            legacy.get("full_text"),
            legacy.get("text"),
        )
    )
    normalized["author"] = _normalize_author(raw_author, row)
    normalized["created_at"] = str(
        _first_non_empty(
            row.get("created_at"),
            row.get("createdAt"),
            row.get("createdAtISO"),
            row.get("created_at_iso"),
            legacy.get("created_at"),
        )
    )
    normalized["createdAt"] = normalized["created_at"]
    normalized["metrics"] = metrics
    normalized["public_metrics"] = {
        "like_count": metrics["likes"],
        "retweet_count": metrics["retweets"],
        "reply_count": metrics["replies"],
        "quote_count": metrics["quotes"],
        "bookmark_count": metrics["bookmarks"],
        "impression_count": metrics["views"],
    }
    if author_id and not normalized.get("author_id"):
        normalized["author_id"] = author_id
    if "isRetweet" not in normalized:
        normalized["isRetweet"] = bool(row.get("is_retweet") or row.get("retweeted_status"))
    return normalized


def rows_from_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        rows = raw
        author_lookup: dict[str, dict[str, Any]] = {}
    elif isinstance(raw, dict) and isinstance(raw.get("data"), list):
        rows = raw["data"]
        author_lookup = _author_lookup_from_raw(raw)
    else:
        rows = []
        author_lookup = {}
    return [normalize_tweet_row(row, author_lookup) for row in rows if isinstance(row, Mapping)]


def tweet_metrics(tweet: Mapping[str, Any]) -> dict[str, int]:
    return _normalize_metrics(tweet)


def list_filter_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = config.get("list_filter") if isinstance(config, Mapping) else {}
    raw = raw if isinstance(raw, Mapping) else {}
    weights_raw = raw.get("weights") if isinstance(raw.get("weights"), Mapping) else {}
    default_weights = DEFAULT_LIST_FILTER["weights"]
    weights = {
        key: _coerce_float(weights_raw.get(key, default_weights[key]), float(default_weights[key]))
        for key in default_weights
    }
    lang_value = raw.get("lang", [])
    if isinstance(lang_value, str):
        languages = [item.strip().lower() for item in lang_value.split(",") if item.strip()]
    elif isinstance(lang_value, list):
        languages = [str(item).strip().lower() for item in lang_value if str(item).strip()]
    else:
        languages = []
    mode = str(raw.get("mode") or DEFAULT_LIST_FILTER["mode"])
    if mode not in {"topN", "minScore", "all"}:
        mode = DEFAULT_LIST_FILTER["mode"]
    return {
        "enabled": _coerce_bool(raw.get("enabled"), bool(DEFAULT_LIST_FILTER["enabled"])),
        "mode": mode,
        "topN": max(1, _coerce_int(raw.get("topN", DEFAULT_LIST_FILTER["topN"]), int(DEFAULT_LIST_FILTER["topN"]))),
        "minScore": _coerce_float(raw.get("minScore", DEFAULT_LIST_FILTER["minScore"])),
        "lang": languages,
        "excludeRetweets": _coerce_bool(raw.get("excludeRetweets"), bool(DEFAULT_LIST_FILTER["excludeRetweets"])),
        "weights": weights,
    }


def tweet_engagement_score(tweet: Mapping[str, Any], weights: Mapping[str, float] | None = None) -> float:
    weights = weights or DEFAULT_LIST_FILTER["weights"]
    metrics = tweet_metrics(tweet)
    views = max(1, metrics["views"])
    return (
        metrics["likes"] * float(weights.get("likes", 0))
        + metrics["retweets"] * float(weights.get("retweets", 0))
        + metrics["replies"] * float(weights.get("replies", 0))
        + metrics["bookmarks"] * float(weights.get("bookmarks", 0))
        + math.log10(views) * float(weights.get("views_log", 0))
    )


def filter_list_tweets(tweets: list[dict[str, Any]], config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    filter_config = list_filter_config(config)
    if not filter_config["enabled"]:
        return list(tweets)

    selected: list[dict[str, Any]] = []
    languages = set(filter_config["lang"])
    for tweet in tweets:
        lang = str(tweet.get("lang") or "").lower()
        if languages and lang and lang not in languages:
            continue
        if filter_config["excludeRetweets"] and (tweet.get("isRetweet") or tweet.get("is_retweet")):
            continue

        score = tweet_engagement_score(tweet, filter_config["weights"])
        if score < filter_config["minScore"]:
            continue
        scored_tweet = dict(tweet)
        scored_tweet["score"] = round(score, 2)
        selected.append(scored_tweet)

    selected.sort(key=lambda item: (float(item.get("score") or 0), str(item.get("id") or "")), reverse=True)
    if filter_config["mode"] == "topN":
        return selected[: filter_config["topN"]]
    return selected


def _parse_tweet_timestamp(created_at: str) -> int | None:
    if not created_at:
        return None
    text = str(created_at).strip()
    if not text:
        return None

    try:
        iso_text = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(iso_text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        pass

    try:
        parsed = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        return int(parsed.timestamp())
    except ValueError:
        return None


def format_time_ago(created_at: str) -> str:
    timestamp = _parse_tweet_timestamp(created_at)
    if timestamp is None:
        return ""
    diff = int(time.time()) - timestamp
    if diff < 0:
        return ""
    if diff < 60:
        return f"{diff}秒前"
    if diff < 3600:
        return f"{diff // 60}分钟前"
    if diff < 86400:
        return f"{diff // 3600}小时前"
    return f"{diff // 86400}天前"


def _trend_emoji(trend: str) -> str:
    return {"bullish": "📈", "bearish": "📉"}.get(str(trend).lower(), "➡️")


def build_message_blogger(
    tweet: dict[str, Any],
    analysis: dict[str, Any],
    market_info: str,
    source_label: str,
) -> str:
    tweet_id = str(tweet.get("id", ""))
    text = str(tweet.get("text", ""))[:500]
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    author_name = author.get("name") or author.get("username") or "unknown"
    username = str(author.get("username") or author_name)
    time_ago = format_time_ago(str(tweet.get("created_at") or ""))
    tweet_url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""

    chinese_summary = str(analysis.get("chinese_summary") or "")
    raw_symbols = analysis.get("symbols") if isinstance(analysis.get("symbols"), list) else []
    symbols_slash = "/".join(str(s) for s in raw_symbols) if raw_symbols else ""
    logic_score = analysis.get("logic_score", 0)
    sleaze_score = analysis.get("sleaze_score", 0)
    trend_emoji = _trend_emoji(str(analysis.get("trend") or "neutral"))
    sourcing_note = str(analysis.get("sourcing_note") or "")
    logic_detail = str(analysis.get("logic_detail") or "")
    sleaze_detail = str(analysis.get("sleaze_detail") or analysis.get("sleaze_note") or "")
    prediction = str(analysis.get("prediction_check") or "无预判")

    header = f"{author_name} (@{username})" + (f" · {time_ago}" if time_ago else "")
    stats_parts = []
    if symbols_slash:
        stats_parts.append(symbols_slash)
    stats_parts.append(f"观点可信度：{logic_score}🔵/10")
    stats_parts.append(f"趋势：{trend_emoji}")
    stats = " | ".join(stats_parts)

    # Analysis blocks first — preserved even when truncated by platform limits
    parts: list[str] = [header, stats, "", chinese_summary, ""]
    if sourcing_note:
        parts += [f"🔍 观点溯源（{logic_score}/10）", sourcing_note, ""]
    if logic_detail:
        parts += [f"🧠 逻辑评估（{logic_score}/10）", logic_detail, ""]
    parts += [f"⚠️ 预判核查", prediction, ""]
    parts += [f"🚩 私货检测（{sleaze_score}/10）", sleaze_detail or "-", ""]
    # Market data and raw text deprioritized — trimmed first on truncation
    if market_info:
        parts += [market_info, ""]
    parts += [text]
    if tweet_url:
        parts += ["", f"查看原文：{tweet_url}"]
    return "\n".join(parts).strip()


def build_message_list_entry(tweet: dict[str, Any], analysis: dict[str, Any]) -> str:
    tweet_id = str(tweet.get("id", ""))
    text = str(tweet.get("text", ""))[:200]
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    author_name = author.get("name") or author.get("username") or "unknown"
    username = str(author.get("username") or author_name)
    time_ago = format_time_ago(str(tweet.get("created_at") or ""))
    summary = str(analysis.get("chinese_summary") or text)
    tweet_url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
    header = f"{author_name} (@{username})" + (f" · {time_ago}" if time_ago else "")

    lines = [header]
    if summary:
        lines.append(summary)
    lines.append(text)
    if tweet_url:
        lines.append(f"查看原文：{tweet_url}")
    return "\n".join(lines)


def extract_symbols(text: str) -> list[str]:
    symbols = re.findall(r"(?<![A-Za-z0-9_])\$([A-Z]{1,5})(?![A-Za-z0-9_])", text)
    return sorted(set(symbols))


def fallback_analysis(tweet: dict[str, Any]) -> dict[str, Any]:
    text = str(tweet.get("text", ""))
    return {
        "id": str(tweet.get("id", "")),
        "chinese_summary": text[:100],
        "symbols": extract_symbols(text),
        "logic_score": 0,
        "sleaze_score": 0,
        "sleaze_note": "",
        "prediction_check": "LLM not configured; fallback analysis used.",
    }
