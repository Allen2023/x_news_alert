"""Structured output helpers for x-news-alert."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

SCHEMA_VERSION = "1"


def ensure_utf8_streams() -> None:
    """Use UTF-8 streams on Windows consoles when possible."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def success_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and "ok" in data and "schema_version" in data:
        return data
    return {"ok": True, "schema_version": SCHEMA_VERSION, "data": data}


def error_payload(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "schema_version": SCHEMA_VERSION, "error": error}


def print_json(out: TextIO, value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2), file=out)


def emit_data(out: TextIO, value: Any, structured: bool = False) -> None:
    print_json(out, success_payload(value) if structured else value)
