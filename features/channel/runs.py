"""
Nero · Channel Otomatis - membaca folder run (data/channel/runs/YYYY-MM-DD/) untuk halaman Nero.

Hanya membaca. Video yang lolos validasi diputar dari salinannya di output/channel/<tanggal>/ (rute /out/...);
video yang tidak lolos tetap bisa ditonton dari folder run lewat rute berkas yang dibatasi daftar nama.

Hasil yang usang tidak ditampilkan sebagai hasil sekarang: bila riset/naskah diulang (mis. uji coba "Rencana
saja"), video, suara, visual, dan metadata lama milik naskah sebelumnya. Pipeline mencatat naskah asal tiap hasil
di inputs.json (<langkah>_src = sidik naskah); halaman membandingkannya dengan naskah sekarang.
"""
from __future__ import annotations

import os
import re
import traceback
from datetime import date

import common

from . import config, state
from .runctx import FINGERPRINTS, OUTPUTS, RunContext, lock_active, sha
from .schemas import QC, Metadata, RenderInfo, Research, Script, Visuals, Words, YouTubeResult

STEP_ORDER = ("research", "script", "qc", "seo", "tts", "visuals", "render")
SOURCED = ("seo", "tts", "visuals", "render")      # hasil yang dibuat dari naskah tertentu
RUN_FILES = ("final.mp4", "final.jpg", "voice.mp3", "captions.srt")   # boleh diambil halaman dari folder run
LOG_TAIL_BYTES = 64 * 1024
_FAIL = re.compile(r"^\d{2}:\d{2}:\d{2} (GAGAL|DIBATALKAN) di langkah (\w+)(?::\s*(.*))?$")


def parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        return None


def run_days() -> list[date]:
    root = state.data_path("runs")
    if not os.path.isdir(root):
        return []
    days = [d for d in (parse_day(n) for n in os.listdir(root) if not n.startswith(".")) if d is not None]
    return sorted((d for d in days if os.path.isdir(os.path.join(root, d.isoformat()))), reverse=True)


def _log_lines(ctx: RunContext) -> list[str]:
    """Baris akhir run.log (maks. 64 KB). Byte rusak diganti, tidak membuat halaman gagal."""
    try:
        with open(ctx.path("run.log"), "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LOG_TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > LOG_TAIL_BYTES else lines      # baris pertama mungkin terpotong


def last_run_lines(ctx: RunContext) -> list[str]:
    """Baris log run terakhir (sejak 'mulai run' terakhir), tanpa traceback."""
    lines = [x for x in _log_lines(ctx) if re.match(r"^\d{2}:\d{2}:\d{2} ", x)]
    start = max((i for i, x in enumerate(lines) if " mulai run " in x), default=0)
    return lines[start:]


def failure(ctx: RunContext) -> dict | None:
    """Langkah yang gagal/dibatalkan di run terakhir, bila ada."""
    for line in reversed(last_run_lines(ctx)):
        m = _FAIL.match(line)
        if m:
            return {"step": m.group(2), "cancelled": m.group(1) == "DIBATALKAN", "message": (m.group(3) or "")[:600]}
        if " selesai. dijalankan:" in line:
            return None
    return None


def locked(ctx: RunContext) -> bool:
    return lock_active(ctx.path(".lock"))


def _mtime(path: str) -> int:
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


def _stamp(path: str) -> tuple[int, int]:
    """Penanda perubahan berkas (waktu ubah dalam ns + ukuran)."""
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return 0, 0


def current_steps(ctx: RunContext, script: Script | None) -> dict[str, bool]:
    """{langkah: hasilnya milik naskah sekarang?} untuk seo/tts/visuals/render. Run lama tanpa catatan asal:
    dianggap cocok bila sidik langkahnya ada."""
    fps = ctx.fingerprints() if os.path.exists(ctx.path(FINGERPRINTS)) else {}
    cur = sha(script) if script is not None else None
    out = {}
    for step in SOURCED:
        src = fps.get(f"{step}_src")
        out[step] = bool(fps.get(step)) and (src == cur if src else script is not None)
    return out


def video_urls(day: date, current: bool = True) -> dict:
    """URL pratinjau: salinan yang sudah lolos validasi di output/, kalau tidak ada dari folder run. Video usang
    (milik naskah sebelumnya) tidak diberikan."""
    d = day.isoformat()
    out = {"video": "", "thumb": "", "captions": "", "published": False}
    if not current:
        return out
    for name, key in (("final.mp4", "video"), ("final.jpg", "thumb"), ("captions.srt", "captions")):
        published = os.path.join(common.OUTPUT, config.ID, d, name)
        local = os.path.join(state.data_path("runs", d), name)
        if os.path.isfile(published):
            out[key] = f"/out/{config.ID}/{d}/{name}?v={_mtime(published)}"
        elif os.path.isfile(local):
            out[key] = f"/api/{config.ID}/runs/{d}/file/{name}?v={_mtime(local)}"
    out["published"] = os.path.isfile(os.path.join(common.OUTPUT, config.ID, d, "final.mp4"))
    return out


def run_summary(day: date) -> dict:
    ctx = RunContext(day, echo=None)
    script = ctx.load("script", Script)
    research = ctx.load("research", Research)
    cur = current_steps(ctx, script)
    meta = ctx.load("metadata", Metadata) if cur["seo"] else None
    qc = ctx.load("qc", QC)
    render = ctx.load("render", RenderInfo) if cur["render"] else None
    yt = ctx.load("youtube", YouTubeResult)
    topic = script.topic if script else (research.chosen.topic if research else "")
    return {
        "date": day.isoformat(),
        "topic": topic,
        "title": meta.title if meta else "",
        "format": script.format if script else "",
        "qc": {"score": qc.score, "passed": qc.passed} if qc else None,
        "render": ({"valid": render.valid, "duration": render.duration, "needs_review": render.needs_review}
                   if render else None),
        "stale_video": os.path.isfile(ctx.path("final.mp4")) and not cur["render"],
        "uploaded": ({"video_id": yt.video_id, "url": yt.url, "status": yt.status, "verified": yt.verified}
                     if yt is not None and yt.video_id else None),
        "failed": failure(ctx),
        "running": locked(ctx),
        "updated": max((_mtime(ctx.path(n)) for n in os.listdir(ctx.dir)), default=0) if os.path.isdir(ctx.dir) else 0,
        **video_urls(day, cur["render"]),
    }


def _broken(day: date, e: Exception) -> dict:
    return {"date": day.isoformat(), "topic": "", "title": "", "format": "", "qc": None, "render": None,
            "stale_video": False, "uploaded": None, "running": False, "updated": 0, "video": "", "thumb": "",
            "captions": "",
            "published": False, "failed": {"step": "-", "cancelled": False, "message": f"Folder run rusak: {e}"[:300]}}


def list_runs(limit: int = 30) -> list[dict]:
    out = []
    for d in run_days()[:limit]:
        try:
            out.append(run_summary(d))
        except Exception as e:  # noqa: BLE001  satu folder rusak tidak boleh membuat seluruh halaman gagal
            traceback.print_exc()
            out.append(_broken(d, e))
    return out


_ready_cache: dict[str, tuple[tuple, bool]] = {}


def count_ready() -> int:
    """Jumlah run dengan video yang lolos validasi dan masih milik naskahnya. Disimpan per folder menurut waktu
    ubah render.json & inputs.json, jadi pemanggilan berkala (Beranda) hanya membaca folder yang berubah."""
    total = 0
    for d in run_days():
        ctx = RunContext(d, echo=None)
        key = tuple(_stamp(ctx.path(n)) for n in ("render.json", FINGERPRINTS, "script.json"))
        hit = _ready_cache.get(ctx.dir)
        if hit is None or hit[0] != key:
            ok = False
            try:
                r = ctx.load("render", RenderInfo)
                ok = r is not None and r.valid and current_steps(ctx, ctx.load("script", Script))["render"]
            except Exception:  # noqa: BLE001
                ok = False
            hit = (key, ok)
            _ready_cache[ctx.dir] = hit
        total += hit[1]
    return total


def step_states(ctx: RunContext, fail: dict | None, cur: dict[str, bool]) -> list[dict]:
    from .jobs import STEP_LABELS
    fps = ctx.fingerprints() if os.path.exists(ctx.path(FINGERPRINTS)) else {}
    qc = ctx.load("qc", QC)
    out = []
    for s in STEP_ORDER:
        main = OUTPUTS[s][0] if s != "render" else "render.json"
        done = os.path.isfile(ctx.path(main)) and bool(fps.get(s)) and cur.get(s, True)
        st = "selesai" if done else "belum"
        if s == "qc" and qc is not None and not qc.passed:
            st = "gagal"
        if fail and fail["step"] == s:
            st = "batal" if fail["cancelled"] else "gagal"
        out.append({"id": s, "label": STEP_LABELS[s], "state": st})
    return out


def run_detail(day: date) -> dict | None:
    ctx = RunContext(day, echo=None)
    if not os.path.isdir(ctx.dir):
        return None
    summary = run_summary(day)
    script = ctx.load("script", Script)
    cur = current_steps(ctx, script)
    research = ctx.load("research", Research)
    words = ctx.load("words", Words) if cur["tts"] else None

    def current(name, schema, ok=True):
        m = ctx.load(name, schema) if ok else None
        return m.model_dump(mode="json", by_alias=True) if m is not None else None

    chosen = None
    if research is not None:
        c = research.chosen
        chosen = {"topic": c.topic, "angle": c.angle, "why_now": c.why_now,
                  "sources": [{"title": s.title, "url": s.url} for s in c.sources],
                  "candidates": len(research.candidates), "rejected": research.rejected_as_duplicate}
    return {
        **summary,
        "research": chosen,
        "script": script.model_dump(mode="json") if script else None,
        "qc": current("qc", QC),
        "metadata": current("metadata", Metadata, cur["seo"]),
        "visuals": current("visuals", Visuals, cur["visuals"]),
        "render": current("render", RenderInfo, cur["render"]),
        "voice": ({"duration": words.duration, "voice": words.voice, "rate": words.rate, "source": words.source,
                   "warnings": words.warnings} if words else None),
        "steps": step_states(ctx, summary["failed"], cur),
        "log": last_run_lines(ctx)[-80:],
        "youtube": current("youtube", YouTubeResult),
        "youtube_stale": _youtube_stale(ctx, script),
        **_upload_fields(day, summary),
    }


def _upload_fields(day: date, summary: dict) -> dict:
    """upload_ready + alasan bila belum siap. Run tanpa video yang lolos validasi tidak perlu diperiksa."""
    if summary.get("uploaded") or not (summary.get("render") or {}).get("valid"):
        return {"upload_ready": False, "upload_block": "", "upload_rebuild": False, "upload_replan": False,
                "run_genre": ""}
    b = upload_block(day)
    return {"upload_ready": b["block"] == "", "upload_block": b["block"], "upload_rebuild": b["rebuild"],
            "upload_replan": b.get("replan", False), "run_genre": b["genre"]}


def _youtube_stale(ctx: RunContext, script: Script | None) -> bool:
    """Video di YouTube dibuat dari naskah sebelumnya (run diulang setelah diunggah)."""
    yt = ctx.load("youtube", YouTubeResult)
    return bool(yt is not None and yt.video_id and yt.source and script is not None and yt.source != sha(script))


def upload_block(day: date) -> dict:
    """{"block", "rebuild", "genre"}: lihat pipeline.upload_block. Pemeriksaannya sama dengan saat upload."""
    from . import pipeline
    try:
        return pipeline.upload_block(day)
    except Exception as e:  # noqa: BLE001  kegagalan memeriksa tidak boleh membuat halaman gagal dimuat
        traceback.print_exc()
        return {"block": f"Kesiapan unggah tidak bisa diperiksa: {e}"[:300], "rebuild": False, "replan": False,
                "genre": ""}


def run_file(day: date, name: str) -> str | None:
    """Path berkas di folder run yang boleh diambil halaman; None bila tidak diizinkan/tidak ada."""
    if name not in RUN_FILES:
        return None
    path = os.path.join(state.data_path("runs", day.isoformat()), name)
    return path if os.path.isfile(path) else None
