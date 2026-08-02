"""Application-wide timezone helpers for UTC database timestamps."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"
COMMON_TIMEZONES = (
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Singapore",
    "UTC",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
)


def normalize_timezone(value: str | None) -> str:
    name = str(value or DEFAULT_TIMEZONE).strip()
    aliases = {item.casefold(): item for item in COMMON_TIMEZONES}
    name = aliases.get(name.casefold(), name)
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"无效时区：{name}") from error
    return name


def format_utc_timestamp(value: str, timezone_name: str | None = None) -> str:
    if not value:
        return ""
    try:
        source = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if source.tzinfo is None:
            source = source.replace(tzinfo=timezone.utc)
        target = ZoneInfo(normalize_timezone(timezone_name))
        return source.astimezone(target).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, ZoneInfoNotFoundError):
        return value

