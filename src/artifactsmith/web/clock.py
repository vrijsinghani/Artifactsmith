"""Format catalog times in an IANA zone. The pages ship no JavaScript."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "America/New_York"
TZ_COOKIE = "am_tz"
_NAME_MAX = 64


def zone_from_name(name: str) -> ZoneInfo | None:
    text = name.strip()
    if not text or len(text) > _NAME_MAX or any(ch.isspace() for ch in text):
        return None
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def default_zone() -> ZoneInfo:
    zone = zone_from_name(DEFAULT_ZONE)
    if zone is None:
        raise RuntimeError(f"{DEFAULT_ZONE} is not available")
    return zone


def format_when(ts: float | None, zone: ZoneInfo) -> str:
    if ts is None:
        return ""
    moment = datetime.fromtimestamp(float(ts), zone)
    hour = int(moment.strftime("%I"))
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}, {hour}:{moment.strftime('%M %p')}"
