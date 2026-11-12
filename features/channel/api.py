"""
Nero · Channel Otomatis - rute /api/channel/* (didaftarkan app.py).

  GET  /settings                pengaturan + status kunci (tanpa nilai kunci) + genre & gaya subtitle
  POST /settings                {config, keys, clear_keys, genre}
  GET  /state                   run yang sedang berjalan (progres per langkah), daftar run, kesiapan
  POST /run                     {mode: plan|video|media, genre?, force?, date?} -> uji coba (dry-run)
  POST /run/cancel              batalkan run yang sedang berjalan
  GET  /runs/<tanggal>          isi satu run: riset, skrip, QC, metadata, visual, render, log
  GET  /runs/<tanggal>/file/<n> berkas run (final.mp4, final.jpg, voice.mp3, captions.srt)

Selama settings.channel.enabled = false, fitur ini tidak menjalankan apa pun di latar belakang; halaman hanya
menjalankan uji coba atas permintaan pengguna.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta

from flask import Blueprint, jsonify, request, send_file

import common

from . import config, genres, jobs, runs, state, timeutil
from .config import ID, SETTINGS, ConfigError

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
            config.save_keys(keys if isinstance(keys, dict) else {}, clear if isinstance(clear, list) else [])
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
    ]


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
                   today=timeutil.today_local(tz).isoformat(), enabled=enabled, niche=niche, ready=ready)


@bp.post("/run")
def post_run():
    """Mulai uji coba. Body: {mode: plan|video|media, genre?: id (hanya untuk run ini), force?: [langkah],
    date?: YYYY-MM-DD}."""
    from . import pipeline
    body = request.get_json(force=True, silent=True) or {}
    mode = str(body.get("mode") or "video")
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
