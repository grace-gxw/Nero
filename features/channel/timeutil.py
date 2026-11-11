"""
Nero · Channel Otomatis - waktu: jam tayang lokal (WIB) ↔ UTC (RFC3339) untuk YouTube publishAt.

Di Windows, zoneinfo butuh paket tzdata (tercantum di requirements.txt).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def parse_hhmm(text: str) -> time:
    h, m = text.replace(".", ":").split(":")
    return time(int(h), int(m))


def now_local(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def today_local(tz: str) -> date:
    return now_local(tz).date()


def publish_at(publish_time: str, tz: str, now: datetime | None = None, lead_minutes: int = 15) -> datetime:
    """Jam tayang berikutnya (zona waktu lokal): hari ini pukul `publish_time`, atau besok di jam yang sama
    bila waktunya sudah lewat atau kurang dari `lead_minutes` lagi (YouTube butuh waktu memproses video)."""
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    at = datetime.combine(now.date(), parse_hhmm(publish_time), tzinfo=zone)
    if at - now < timedelta(minutes=lead_minutes):
        at = datetime.combine(now.date() + timedelta(days=1), parse_hhmm(publish_time), tzinfo=zone)
    return at


def to_rfc3339_utc(dt: datetime) -> str:
    """Contoh: 2026-11-12T11:00:00Z (format publishAt YouTube)."""
    if dt.tzinfo is None:
        raise ValueError("waktu harus punya zona waktu")
    return dt.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_local(dt: datetime) -> str:
    """Contoh: 12-11-2026 18.00 WIB."""
    return f"{dt:%d-%m-%Y %H.%M} {dt.tzname() or ''}".strip()
