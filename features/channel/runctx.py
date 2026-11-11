"""
Nero · Channel Otomatis - folder run harian, checkpoint per langkah, log, dan kunci run.

Setiap langkah menulis hasilnya ke data/channel/runs/YYYY-MM-DD/. Hasil yang sudah ada dan valid terhadap
skemanya dipakai lagi (langkah di-skip), kecuali langkah itu dipaksa (--force). Kunci run mencegah dua run
untuk tanggal yang sama berjalan bersamaan (mis. tombol di halaman + jadwal).

Integritas checkpoint:
- Sidik (hash) masukan tiap hasil disimpan di inputs.json. Hasil lama hanya dipakai lagi bila sidiknya cocok dengan
  masukan sekarang, jadi skrip yang diubah (mis. diedit tangan) tidak bisa lolos dengan QC/suara/video lama.
- Begitu sebuah langkah mulai bekerja, hasil lamanya sendiri dihapus (invalidate). Langkah yang gagal/dibatalkan
  di tengah jalan tidak meninggalkan hasil lama yang tampak valid; langkah sesudahnya terlindung oleh sidiknya.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime

from pydantic import BaseModel, ValidationError

from . import state

STEPS = ("analyst", "research", "script", "qc", "seo", "tts", "visuals", "render",
         "publish_youtube", "manual_pack", "notify", "state_update")
LOCK_STALE_SECONDS = 3 * 3600   # kunci lebih tua dari ini dianggap sisa run yang macet/dimatikan

# langkah -> langkah yang hasilnya ia pakai (SEO tidak dipakai media; deskripsi disusun ulang setelah visual)
DEPENDS = {"research": (), "script": ("research",), "qc": ("script",), "seo": ("qc",), "tts": ("qc",),
           "visuals": ("tts",), "render": ("visuals",)}
# berkas hasil tiap langkah (relatif terhadap folder run)
OUTPUTS = {"research": ("research.json",), "script": ("script.json",), "qc": ("qc.json",),
           "seo": ("metadata.json",), "tts": ("words.json", "voice.wav", "voice.mp3"),
           "visuals": ("visuals.json",),
           "render": ("render.json", "final.mp4", "final.jpg", "captions.srt", "base.mkv")}
FINGERPRINTS = "inputs.json"


def downstream(step: str) -> list[str]:
    """Semua langkah yang (langsung/tidak langsung) memakai hasil `step`, urut seperti STEPS."""
    found: set[str] = set()
    frontier = {step}
    while frontier:
        nxt = {s for s, deps in DEPENDS.items() if frontier & set(deps)} - found
        found |= nxt
        frontier = nxt
    return [s for s in STEPS if s in found]


def sha(*parts) -> str:
    """Sidik isi: model Pydantic, teks, atau angka (urutan berpengaruh)."""
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, BaseModel):
            p = p.model_dump_json(by_alias=True)
        elif not isinstance(p, str):
            p = json.dumps(p, sort_keys=True, default=str)
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:20]


class RunLocked(RuntimeError):
    """Run untuk tanggal ini sedang berjalan."""


class RunContext:
    def __init__(self, day: date, root: str | None = None, echo=print):
        self.day = day
        self.dir = root or state.data_path("runs", day.isoformat())
        self.echo = echo
        self._locked = False

    # ---------------------------------------------------------------- berkas
    def path(self, *parts: str) -> str:
        return os.path.join(self.dir, *parts)

    def load(self, name: str, schema: type[BaseModel]) -> BaseModel | None:
        """Isi <name>.json bila ada dan valid, selain itu None."""
        path = self.path(f"{name}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return schema.model_validate(json.load(f))
        except (OSError, ValueError, ValidationError):
            return None

    def save(self, name: str, model: BaseModel) -> str:
        path = self.path(f"{name}.json")
        state.write_json_atomic(path, model.model_dump(mode="json", by_alias=True))
        return path

    # ---------------------------------------------------------------- integritas
    def fingerprints(self) -> dict:
        return state.read_json(self.path(FINGERPRINTS), {}) or {}

    def fingerprint(self, step: str) -> str:
        return str(self.fingerprints().get(step) or "")

    def set_fingerprint(self, step: str, value: str) -> None:
        data = self.fingerprints()
        data[step] = value
        state.write_json_atomic(self.path(FINGERPRINTS), data)

    def invalidate(self, step: str) -> None:
        """Hapus hasil lama `step` beserta sidiknya (dipanggil saat langkah mulai bekerja)."""
        for name in OUTPUTS.get(step, ()):
            try:
                os.remove(self.path(name))
            except FileNotFoundError:
                pass
        data = self.fingerprints()
        if step in data:
            state.write_json_atomic(self.path(FINGERPRINTS), {k: v for k, v in data.items() if k != step})

    def cached(self, name: str, schema: type[BaseModel], force: bool = False) -> BaseModel | None:
        """Checkpoint: hasil lama yang valid, atau None bila langkah perlu dijalankan (atau dipaksa)."""
        if force:
            return None
        got = self.load(name, schema)
        if got is not None:
            self.log(f"lewati {name}: hasil sebelumnya masih valid")
        return got

    # ---------------------------------------------------------------- log
    def log(self, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        if self.echo:
            self.echo(f"[channel] {line}")
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(self.path("run.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def log_tail(self, n: int = 30) -> str:
        try:
            with open(self.path("run.log"), encoding="utf-8") as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""

    # ---------------------------------------------------------------- kunci
    def acquire(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        lock = self.path(".lock")
        try:
            if time.time() - os.path.getmtime(lock) > LOCK_STALE_SECONDS:
                os.remove(lock)
        except OSError:
            pass
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as e:
            raise RunLocked(f"Run tanggal {self.day.isoformat()} sedang berjalan.") from e
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        self._locked = True

    def release(self) -> None:
        if self._locked:
            try:
                os.remove(self.path(".lock"))
            except OSError:
                pass
            self._locked = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
