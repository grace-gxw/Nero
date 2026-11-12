"""
Nero · Channel Otomatis - rute /api/channel/* (didaftarkan app.py).

  GET  /settings                pengaturan + status kunci (tanpa nilai kunci) + genre & gaya subtitle
  POST /settings                {config, keys, clear_keys, genre}
  GET  /state                   run yang sedang berjalan (progres per langkah), daftar run, kesiapan
  POST /run                     {mode: plan|video|media, genre?, force?, date?} -> uji coba (dry-run)
  POST /run/cancel              batalkan run yang sedang berjalan
  GET  /runs/<tanggal>          isi satu run: riset, skrip, QC, metadata, visual, render, log
  GET  /runs/<tanggal>/file/<n> berkas run (final.mp4, final.jpg, voice.mp3, captions.srt)
  GET  /youtube                 status koneksi YouTube (tanpa token) + pemakaian kuota hari ini
  POST /youtube/connect         mulai "Hubungkan YouTube" -> {url} untuk dibuka di browser
  POST /youtube/connect/cancel  batalkan proses menghubungkan
  POST /youtube/disconnect      cabut izin & hapus token
  POST /youtube/upload          {date, confirm: true} -> unggah uji (private) sebagai pekerjaan latar
  POST /youtube/check/<tanggal> cek ulang status video yang sudah diunggah

Selama settings.channel.enabled = false, fitur ini tidak menjalankan apa pun di latar belakang; halaman hanya
menjalankan uji coba dan unggah uji (private) atas permintaan pengguna.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta

from flask import Blueprint, jsonify, request, send_file

import common

from . import config, genres, jobs, runs, state, timeutil, youtube
from .config import ID, SETTINGS, ConfigError
from .runctx import RunLocked

# refresh token hanya diisi lewat "Hubungkan YouTube" dan dihapus lewat "Putuskan" (izinnya ikut dicabut)
OAUTH_ONLY_KEYS = ("yt_refresh_token",)

bp = Blueprint(ID, __name__)


def _caption_styles() -> list[dict]:
    """Gaya subtitle yang bisa dipilih: preset Clip Maker + "Gaya saya"."""
    from features.clip import api as clip_api
    from features.clip import presets
    out = [{"id": p["id"], "name": p["name"], "preset": True} for p in presets.PRESETS]
    try:
        out += [{"id": s["id"], "name": s.get("name") or s["id"], "preset": False} for s in clip_api.load_styles()]
    except Exception as e:  # noqa: BLE001  pustaka gaya rusak tidak boleh membuat halaman gagal dimuat
        print(f"[channel] pustaka gaya Clip Maker tidak terbaca: {e}")
    return out


def _public() -> dict:
    return {**config.public_settings(), "genres": genres.GENRES, "styles": _caption_styles(),
            "steps": jobs.STEP_LABELS}


@bp.get("/settings")
def get_settings():
    """Pengaturan channel + status kunci (sudah diisi atau belum). Nilai kunci tidak pernah dikirim."""
    try:
        return jsonify(_public())
    except ConfigError as e:
        return jsonify(error=str(e)), 500


@bp.post("/settings")
def post_settings():
    """Body: {config: {...sebagian...}, keys: {nama: nilai}, clear_keys: [nama], genre: id}.
    Kunci kosong = kunci lama tetap dipakai. genre = pakai genre siap pakai (niche, penonton, kategori)."""
    body = request.get_json(force=True, silent=True) or {}
    gid = body.get("genre")
    patch = body.get("config") if isinstance(body.get("config"), dict) else {}
    if gid:
        if not isinstance(gid, str) or gid not in genres.GENRE_BY_ID:
            return jsonify(error=f"Genre '{gid}' tidak dikenal."), 400
        patch = common._merge(genres.as_patch(gid), patch)    # isian sendiri menimpa isian genre
    try:
        if patch:
            config.save_config(patch)            # divalidasi utuh dulu; gagal = tidak ada yang tersimpan
        keys, clear = body.get("keys"), body.get("clear_keys")
        if isinstance(keys, dict) or isinstance(clear, list):
            keys = {k: v for k, v in (keys if isinstance(keys, dict) else {}).items() if k not in OAUTH_ONLY_KEYS}
            clear = [k for k in (clear if isinstance(clear, list) else []) if k not in OAUTH_ONLY_KEYS]
            config.save_keys(keys, clear)
    except ConfigError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, **_public())


def _readiness(cfg: config.ChannelConfig, keys: dict[str, bool]) -> list[dict]:
    """Daftar kesiapan untuk halaman: yang wajib dan yang opsional."""
    music = cfg.video.music_dir.strip()
    return [
        {"id": "genre", "label": "Genre channel", "ok": bool(cfg.channel.niche.strip()), "required": True},
        {"id": "gemini", "label": "API key Gemini (penulis & QC)", "ok": keys.get("gemini", False), "required": True},
        {"id": "groq", "label": "API key Groq (cadangan AI, QC lebih teliti)", "ok": keys.get("groq", False),
         "required": False},
        {"id": "stock", "label": "Key Pexels / Pixabay (video stok)",
         "ok": keys.get("pexels", False) or keys.get("pixabay", False), "required": False},
        {"id": "cloudflare", "label": "Cloudflare Workers AI (gambar AI)",
         "ok": keys.get("cloudflare_account_id", False) and keys.get("cloudflare_token", False), "required": False},
        {"id": "music", "label": "Folder musik bebas royalti", "ok": bool(music) and os.path.isdir(music),
         "required": False},
        _youtube_ready(),
    ]


def _youtube_ready() -> dict:
    st = youtube.status()
    if st["connected"] and st["problem"]:
        return {"id": "youtube", "label": "YouTube perlu dihubungkan ulang", "ok": False, "required": False}
    return {"id": "youtube", "label": "YouTube terhubung (unggah lewat API)",
            "ok": st["connected"] and st["configured"], "required": False}


@bp.get("/state")
def get_state():
    job = jobs.current()
    try:
        cfg = config.load_config()
        keys = config.key_status()
        enabled, niche, tz = cfg.enabled, cfg.channel.niche, cfg.schedule.timezone
        ready = _readiness(cfg, keys)
    except ConfigError as e:
        return jsonify(error=str(e)), 500
    return jsonify(job=job.snapshot() if job else None, runs=runs.list_runs(20),
                   today=timeutil.today_local(tz).isoformat(), enabled=enabled, niche=niche, ready=ready,
                   youtube=youtube.status())


@bp.post("/run")
def post_run():
    """Mulai uji coba. Body: {mode: plan|video|media, genre?: id (hanya untuk run ini), force?: [langkah],
    date?: YYYY-MM-DD}."""
    from . import pipeline
    body = request.get_json(force=True, silent=True) or {}
    mode = str(body.get("mode") or "video")
    if mode == "upload":
        return jsonify(error="Unggah ke YouTube memakai tombol \"Unggah uji\" di hasil run."), 400
    genre = str(body.get("genre") or "") or None
    force = body.get("force") or []
    if not isinstance(force, list):
        return jsonify(error="force harus berupa daftar langkah."), 400
    try:
        cfg = pipeline.prepare_config(genre)          # gagal cepat bila genre belum dipilih, dll.
    except ConfigError as e:
        return jsonify(error=str(e)), 400
    today = timeutil.today_local(cfg.schedule.timezone)
    day = today
    if body.get("date"):
        text = str(body["date"])
        day = runs.parse_day(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
        if day is None:
            return jsonify(error="Tanggal harus berbentuk YYYY-MM-DD."), 400
        if not today - timedelta(days=30) <= day <= today + timedelta(days=7):
            return jsonify(error="Tanggal uji coba harus antara 30 hari lalu dan 7 hari ke depan."), 400
    try:
        job = jobs.start(day, mode, genre, [str(f) for f in force])
    except jobs.JobBusy as e:
        return jsonify(error=str(e)), 409
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, job=job.snapshot())


@bp.post("/run/cancel")
def post_cancel():
    n = 1 if jobs.cancel() else 0
    return jsonify(ok=True, count=n)


@bp.get("/runs/<day>")
def get_run(day):
    d = runs.parse_day(day)
    detail = runs.run_detail(d) if d else None
    if detail is None:
        return jsonify(error="Run tidak ditemukan."), 404
    return jsonify(detail)


@bp.get("/runs/<day>/file/<name>")
def get_run_file(day, name):
    d = runs.parse_day(day)
    path = runs.run_file(d, name) if d else None
    if path is None:
        return jsonify(error="Berkas tidak ditemukan."), 404
    return send_file(path, conditional=True, as_attachment=request.args.get("dl") == "1", download_name=name)


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------
@bp.get("/youtube")
def get_youtube():
    try:
        cfg = config.load_config()
    except ConfigError as e:
        return jsonify(error=str(e)), 500
    return jsonify(**youtube.status(), api_upload=cfg.youtube.api_upload)


def _uploading() -> bool:
    """Ada unggahan berjalan: pekerjaan halaman ini, atau proses lain (CLI) yang memegang kunci upload."""
    job = jobs.current()
    return (job is not None and job.running() and job.mode == "upload") or youtube.upload_active()


UPLOAD_BUSY = youtube.UPLOAD_BUSY


@bp.post("/youtube/connect")
def post_youtube_connect():
    """Mulai menghubungkan: listener sementara di 127.0.0.1 menunggu Google mengalihkan browser kembali."""
    if _uploading():
        return jsonify(error=UPLOAD_BUSY), 409
    try:
        flow = youtube.begin_connect(config.load_config())
    except (ConfigError, youtube.YouTubeError) as e:
        return jsonify(error=str(e)), 400
    except OSError as e:
        return jsonify(error=f"Port lokal untuk menerima jawaban Google tidak bisa dibuka ({e.strerror})."), 500
    return jsonify(ok=True, url=flow.url, youtube=youtube.status())


@bp.post("/youtube/connect/cancel")
def post_youtube_connect_cancel():
    flow = youtube.current_flow()
    if flow is not None:
        flow.cancel()
    return jsonify(ok=True, youtube=youtube.status())


@bp.post("/youtube/disconnect")
def post_youtube_disconnect():
    if config.key_from_env("yt_refresh_token"):
        return jsonify(error="Token YouTube diatur lewat environment variable YT_REFRESH_TOKEN; hapus env var itu."), 400
    if _uploading():
        return jsonify(error=UPLOAD_BUSY), 409
    try:
        revoked = youtube.disconnect()
    except youtube.YouTubeError as e:
        return jsonify(error=str(e)), 409
    return jsonify(ok=True, revoked=revoked, youtube=youtube.status())


@bp.post("/youtube/upload")
def post_youtube_upload():
    """Unggah uji video satu run sebagai private. Body: {date: YYYY-MM-DD, confirm: true}. `confirm` = pengguna
    sudah menyatakan videonya mematuhi Persyaratan Layanan YouTube (ToS API YouTube 9.1)."""
    body = request.get_json(force=True, silent=True) or {}
    if body.get("confirm") is not True:
        return jsonify(error="Setujui pernyataan Persyaratan Layanan YouTube dulu."), 400
    text = str(body.get("date") or "")
    day = runs.parse_day(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    if day is None:
        return jsonify(error="Tanggal harus berbentuk YYYY-MM-DD."), 400
    if not youtube.connected():
        return jsonify(error="YouTube belum dihubungkan. Hubungkan dulu di Pengaturan."), 400
    flow = youtube.current_flow()
    if flow is not None and not flow.done.is_set():
        return jsonify(error="Proses Hubungkan YouTube masih berjalan. Selesaikan atau batalkan dulu."), 409
    if youtube.upload_active():
        return jsonify(error=UPLOAD_BUSY), 409
    try:
        job = jobs.start(day, "upload")
    except jobs.JobBusy as e:
        return jsonify(error=str(e)), 409
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, job=job.snapshot())


@bp.post("/youtube/check/<day>")
def post_youtube_check(day):
    from . import pipeline
    d = runs.parse_day(day)
    if d is None:
        return jsonify(error="Tanggal tidak valid."), 400
    try:
        res = pipeline.recheck_upload(d)
    except RunLocked:
        return jsonify(error="Run tanggal ini sedang berjalan. Coba lagi setelah selesai."), 409
    except (ConfigError, youtube.YouTubeError) as e:
        return jsonify(error=str(e)), 400
    if res is None:
        return jsonify(error="Video tanggal ini belum diunggah."), 404
    return jsonify(ok=True, youtube=res.model_dump(mode="json"))


def start():
    """Dipanggil sekali saat Nero dibuka. Jadwal harian dibuat di fase berikutnya; run dari halaman memakai
    thread sendiri per run (jobs.py)."""


def summary():
    """Ringkasan untuk Beranda."""
    try:
        enabled = config.load_config().enabled
    except ConfigError:
        enabled = False
    return {"busy": 1 if jobs.busy() else 0, "queue": 0, "stats": [
        {"label": "Status", "value": "Aktif" if enabled else "Nonaktif"},
        {"label": "Video siap", "value": runs.count_ready()},
        {"label": "Diunggah", "value": len(state.load_history())},
    ]}


__all__ = ["ID", "SETTINGS", "bp", "start", "summary"]
