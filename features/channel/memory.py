"""
Nero · Channel Otomatis - ingatan konten antarhari.

SPEC 6.3–6.6: topik 60 hari terakhir tidak diulang, format tidak sama dengan 2 video terakhir, hook bervariasi
(5 hook terakhir), dan klip stok 30 hari terakhir tidak dipakai ulang. Ingatannya berasal dari dua sumber:
  - riwayat (history.json): video yang ada di YouTube, diunggah lewat Nero atau ditemukan analis;
  - video yang sudah dibuat run tanggal sebelumnya (naskah lulus QC, video valid milik naskah itu) tetapi belum
    ada di riwayat, misalnya belum diunggah atau diunggah sendiri saat YouTube belum dihubungkan.
Tanpa sumber kedua, mode unggah manual tanpa YouTube terhubung tidak punya ingatan sama sekali: setiap hari format
yang sama, dan topik serta hook bisa berulang.

Tanggal run itu sendiri tidak dihitung, supaya menjalankan ulang run hari ini tidak menghindari hasilnya sendiri
(riset yang diulang sudah meminta topik baru lewat avoid_topics). Topik & klip juga menghindari video tanggal
sesudahnya (mis. membuat ulang video kemarin); format & hook "terakhir" hanya melihat tanggal sebelumnya.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import state
from .runctx import RunContext
from .runs import current_steps, run_days
from .schemas import QC, RenderInfo, Research, Script, Visuals

TOPIC_DAYS = 60            # SPEC 6.3: dedupe topik 60 hari
CLIP_DAYS = 30             # SPEC 6.6: klip stok tidak dipakai ulang 30 hari
SCAN_DAYS = TOPIC_DAYS     # format & hook terakhir dicari di rentang yang sama


@dataclass
class Made:
    """Satu video (satu tanggal) dalam ingatan."""
    date: str
    topics: list[str]
    format: str = ""
    hook: str = ""
    clip_ids: list[str] = field(default_factory=list)


def produced(day: date) -> Made | None:
    """Video yang dibuat run `day`: naskah lulus QC dan video valid yang dibuat dari naskah itu. None bila run
    tanggal itu tidak menghasilkan video."""
    ctx = RunContext(day, echo=None)
    script = ctx.load("script", Script)
    qc = ctx.load("qc", QC)
    if script is None or qc is None or not qc.passed:
        return None
    cur = current_steps(ctx, script)
    info = ctx.load("render", RenderInfo) if cur["render"] else None
    if info is None or not info.valid:
        return None
    found = ctx.load("research", Research)
    vis = ctx.load("visuals", Visuals) if cur["visuals"] else None
    topics = [t for t in dict.fromkeys([found.chosen.topic if found else "", script.topic]) if t.strip()]
    return Made(date=day.isoformat(), topics=topics, format=script.format, hook=script.hook,
                clip_ids=vis.clip_ids if vis else [])


def entries(before: date, days: int = SCAN_DAYS, later: bool = False) -> list[Made]:
    """Ingatan untuk run tanggal `before`: video `days` hari sebelumnya (dan, bila `later`, sampai `days` hari
    sesudahnya), tanpa tanggal `before` sendiri, urut tanggal. Riwayat YouTube menang atas folder run untuk tanggal
    yang sama."""
    own = before.isoformat()
    since = (before - timedelta(days=days)).isoformat()
    until = (before + timedelta(days=days)).isoformat() if later else own

    def wanted(key: str) -> bool:
        return key != own and since <= key and (key < own or key <= until)

    out = {e.date: Made(date=e.date, topics=[e.topic] if e.topic.strip() else [], format=e.format, hook=e.hook,
                        clip_ids=list(e.clip_ids))
           for e in state.load_history() if wanted(e.date)}
    for d in run_days():
        key = d.isoformat()
        if not wanted(key) or key in out:
            continue
        try:
            made = produced(d)
        except Exception:  # noqa: BLE001  satu folder run rusak tidak boleh menghentikan run hari ini
            traceback.print_exc()
            continue
        if made is not None:
            out[key] = made
    return [out[k] for k in sorted(out)]


def recent_topics(day: date, days: int = TOPIC_DAYS) -> list[str]:
    return [t for m in entries(day, days, later=True) for t in m.topics]


def last_formats(day: date, n: int = 2) -> list[str]:
    return [m.format for m in entries(day)[-n:] if m.format]


def last_hooks(day: date, n: int = 5) -> list[str]:
    return [m.hook for m in entries(day)[-n:] if m.hook]


def recent_clip_ids(day: date, days: int = CLIP_DAYS) -> set[str]:
    return {c for m in entries(day, days, later=True) for c in m.clip_ids}
