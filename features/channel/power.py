"""
Nero · Channel Otomatis - menjaga Windows tetap terjaga selama pekerjaan panjang (render, upload).

    with keep_awake() as ok:
        ...

SetThreadExecutionState (kernel32), dicek di dokumentasi Microsoft 13 Nov 2026:
- ES_CONTINUOUS | ES_SYSTEM_REQUIRED mereset idle timer sistem sampai panggilan berikutnya dari thread yang sama,
  jadi Windows tidak tidur karena idle. Render ffmpeg/NVENC tidak dihitung sebagai aktivitas, sehingga tanpa ini
  laptop yang memakai baterai bisa tidur di tengah run. Layar tetap boleh mati (tanpa ES_DISPLAY_REQUIRED); away mode
  tidak dipakai (tidak untuk laptop).
- Efeknya per thread: dipanggil dan dikembalikan (ES_CONTINUOUS saja) dari thread pekerjaan itu sendiri.
- Tidak mencegah tidur yang diminta pengguna (tutup laptop, tombol power, menu Sleep). Run yang terputus dilanjutkan
  dari checkpoint pada percobaan berikutnya (scheduler.py).
- Nilai kembalian 0 = gagal. Di luar Windows tidak melakukan apa pun.
"""
from __future__ import annotations

import contextlib
import os

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_fn = None


def _set(flags: int) -> bool:
    global _fn
    if os.name != "nt":
        return False
    try:
        if _fn is None:
            import ctypes
            from ctypes import wintypes
            fn = ctypes.WinDLL("kernel32", use_last_error=True).SetThreadExecutionState
            fn.argtypes = [wintypes.DWORD]
            fn.restype = wintypes.DWORD
            _fn = fn
        return _fn(flags) != 0
    except (AttributeError, OSError):
        return False


@contextlib.contextmanager
def keep_awake(log=None):
    """Windows tidak tidur karena idle selama blok ini berjalan di thread ini. Hasil: True bila permintaan diterima."""
    ok = _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    if not ok and os.name == "nt" and log:
        log("PERINGATAN Windows menolak permintaan tetap terjaga; laptop bisa tidur di tengah run")
    try:
        yield ok
    finally:
        if ok:
            _set(ES_CONTINUOUS)
