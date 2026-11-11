"""
Nero · Channel Otomatis - rute /api/channel/* (didaftarkan app.py).

Fase I-1: hanya pengaturan & kunci API. Pipeline harian, halaman, dan jadwal ditambahkan di fase berikutnya
(lihat INTEGRATION_PLAN.md). Selama settings.channel.enabled = false, fitur ini tidak menjalankan apa pun
di latar belakang.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import config, state
from .config import ID, SETTINGS, ConfigError

bp = Blueprint(ID, __name__)


@bp.get("/settings")
def get_settings():
    """Pengaturan channel + status kunci (sudah diisi atau belum). Nilai kunci tidak pernah dikirim."""
    try:
        return jsonify(config.public_settings())
    except ConfigError as e:
        return jsonify(error=str(e)), 500


@bp.post("/settings")
def post_settings():
    """Body: {config: {...sebagian...}, keys: {nama: nilai}, clear_keys: [nama]}.
    Kunci kosong = kunci lama tetap dipakai."""
    body = request.get_json(force=True, silent=True) or {}
    try:
        if isinstance(body.get("config"), dict):
            config.save_config(body["config"])
        keys, clear = body.get("keys"), body.get("clear_keys")
        if isinstance(keys, dict) or isinstance(clear, list):
            config.save_keys(keys if isinstance(keys, dict) else {}, clear if isinstance(clear, list) else [])
    except ConfigError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, **config.public_settings())


def start():
    """Dipanggil sekali saat Nero dibuka. Belum ada pekerja/jadwal di Fase I-1."""


def summary():
    """Ringkasan untuk Beranda."""
    try:
        enabled = config.load_config().enabled
    except ConfigError:
        enabled = False
    return {"busy": 0, "queue": 0, "stats": [
        {"label": "Status", "value": "Aktif" if enabled else "Nonaktif"},
        {"label": "Video dibuat", "value": len(state.load_history())},
    ]}


__all__ = ["ID", "SETTINGS", "bp", "start", "summary"]
