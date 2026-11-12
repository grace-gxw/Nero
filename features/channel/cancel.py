"""
Nero · Channel Otomatis - pembatalan yang bisa dicek di mana saja dalam satu run.

run_daily memasang event batal untuk thread-nya (cancel_scope). Kode yang lama berjalan tanpa parameter cancel
(panggilan AI berulang, riset Wikipedia) cukup memanggil check_current() atau sleep(); keduanya membaca event
milik thread yang sedang berjalan. Modul ini sengaja ringan (tanpa FFmpeg/engine) supaya bisa diimpor langkah
rencana.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_bound = threading.local()


class Cancelled(Exception):
    """Proses dibatalkan pengguna. Sengaja bukan RuntimeError: engine.render_clip menangkap RuntimeError untuk
    pindah dari NVENC ke libx264 (pembatalan tidak boleh memicu itu)."""

    def __init__(self, msg: str = "Dibatalkan."):
        super().__init__(msg)


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


@contextmanager
def cancel_scope(cancel: threading.Event | None):
    """Pasang event batal untuk thread ini selama blok berjalan."""
    prev = getattr(_bound, "event", None)
    _bound.event = cancel
    try:
        yield
    finally:
        _bound.event = prev


def current() -> threading.Event | None:
    return getattr(_bound, "event", None)


def check_current() -> None:
    check_cancel(current())


def sleep(seconds: float) -> None:
    """time.sleep yang langsung berhenti (Cancelled) bila run dibatalkan."""
    ev = current()
    if ev is None:
        time.sleep(seconds)
    elif ev.wait(max(0.0, seconds)):
        raise Cancelled()
