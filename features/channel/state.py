"""
Nero · Channel Otomatis - data yang bertahan antar-run: riwayat video, performa, dan learnings.

Semua berkas ada di data/channel/ (ikut NERO_DATA). Semua tulisan atomik (berkas sementara lalu os.replace).
Membaca tidak membuat folder apa pun, supaya Beranda/tes tidak meninggalkan folder kosong.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
from datetime import date, timedelta

from pydantic import ValidationError

import common

from .config import ID
from .schemas import HistoryEntry

_lock = threading.RLock()


def data_path(*parts: str) -> str:
    return os.path.join(common.DATA, ID, *parts)


def write_json_atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def write_text_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# --------------------------------------------------------------------------
# history.json
# --------------------------------------------------------------------------
def load_history() -> list[HistoryEntry]:
    raw = read_json(data_path("history.json"), [])
    out = []
    for item in raw if isinstance(raw, list) else []:
        with contextlib.suppress(ValidationError):  # entri rusak dilewati, tidak menghentikan pipeline
            out.append(HistoryEntry.model_validate(item))
    return sorted(out, key=lambda e: e.date)


def save_history(entries: list[HistoryEntry]) -> None:
    with _lock:
        write_json_atomic(data_path("history.json"),
                          [e.model_dump(mode="json") for e in sorted(entries, key=lambda e: e.date)])


def upsert_history(entry: HistoryEntry) -> None:
    """Satu entri per tanggal: menjalankan ulang langkah state_update tidak menambah entri ganda."""
    with _lock:
        entries = [e for e in load_history() if e.date != entry.date]
        save_history(entries + [entry])


def recent(days: int, today: date | None = None) -> list[HistoryEntry]:
    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    return [e for e in load_history() if e.date >= since]


def last_formats(n: int = 2) -> list[str]:
    return [e.format for e in load_history()[-n:] if e.format]


def last_hooks(n: int = 5) -> list[str]:
    return [e.hook for e in load_history()[-n:] if e.hook]


def recent_topics(days: int = 60, today: date | None = None) -> list[str]:
    return [e.topic for e in recent(days, today) if e.topic]


def recent_clip_ids(days: int = 30, today: date | None = None) -> set[str]:
    return {c for e in recent(days, today) for c in e.clip_ids}


# --------------------------------------------------------------------------
# performance.json & learnings.md
# --------------------------------------------------------------------------
def load_performance() -> dict:
    data = read_json(data_path("performance.json"), {})
    return data if isinstance(data, dict) else {}


def save_performance(data: dict) -> None:
    with _lock:
        write_json_atomic(data_path("performance.json"), data)


def load_learnings() -> str:
    try:
        with open(data_path("learnings.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def save_learnings(text: str) -> None:
    with _lock:
        write_text_atomic(data_path("learnings.md"), text.strip() + "\n")


# --------------------------------------------------------------------------
# Bersih-bersih berkas sementara
# --------------------------------------------------------------------------
KEEP_RUN_DAYS = 7          # klip antara, gambar AI, dan suara mentah run yang lebih tua dari ini dihapus
KEEP_STOCK_DAYS = 2        # klip stok yang diunduh (klip yang dipakai tidak boleh dipakai lagi 30 hari)
KEEP_SEARCH_HOURS = 24     # cache hasil pencarian Pexels/Pixabay (aturan Pixabay: cache 24 jam)
_HEAVY_DIRS = ("clips", "images", "tts")
_HEAVY_FILES = ("voice.wav", "base.mkv")


def _remove(path: str) -> int:
    import shutil
    if os.path.isdir(path):
        n = sum(len(files) for _, _, files in os.walk(path))
        shutil.rmtree(path, ignore_errors=True)
        return n
    try:
        os.remove(path)
        return 1
    except OSError:
        return 0


def prune_runs(today: date, now: float | None = None, keep: tuple[str, ...] = ()) -> int:
    """Hapus berkas besar yang tidak diperlukan lagi. JSON, final.mp4, voice.mp3, dan captions tetap disimpan.
    `today` = tanggal hari ini yang sebenarnya; folder di `keep` dan run yang sedang berjalan (.lock) tidak
    disentuh. Hasil: jumlah berkas yang dihapus."""
    import time
    root = data_path("runs")
    if not os.path.isdir(root):
        return 0
    now = time.time() if now is None else now
    removed = 0
    for name in os.listdir(root):
        if name.startswith("."):
            continue
        try:
            day = date.fromisoformat(name)
        except ValueError:
            continue
        if (today - day).days <= KEEP_RUN_DAYS or name in keep or os.path.exists(os.path.join(root, name, ".lock")):
            continue
        for part in (*_HEAVY_DIRS, *_HEAVY_FILES):
            path = os.path.join(root, name, part)
            if os.path.exists(path):
                removed += _remove(path)
    for sub, max_age in (("stock", KEEP_STOCK_DAYS * 86400), ("search", KEEP_SEARCH_HOURS * 3600)):
        folder = os.path.join(root, ".cache", sub)
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            path = os.path.join(folder, f)
            with contextlib.suppress(OSError):
                if now - os.path.getmtime(path) > max_age:
                    removed += _remove(path)
    return removed
