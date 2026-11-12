"""
Nero · Channel Otomatis - menjalankan pipeline dari halaman Nero sebagai pekerjaan latar.

Hanya satu run sekaligus di proses ini (run dari CLI/jadwal dijaga kunci run per tanggal). Halaman membaca
progres lewat polling (GET /api/channel/state). Halaman menjalankan uji coba (dry-run: tanpa upload dan tanpa
kiriman Telegram) dan, atas perintah pengguna, unggah uji ke YouTube sebagai private (mode "upload").
"""
from __future__ import annotations

import re
import threading
import time
import traceback
import uuid
from datetime import date

from . import pipeline
from .runctx import STEPS

# label tahap untuk halaman
STEP_LABELS = {
    "research": "Riset topik", "script": "Menulis skrip", "qc": "Pemeriksaan fakta (QC)",
    "seo": "Judul & deskripsi", "tts": "Suara", "visuals": "Visual", "render": "Render video",
    "publish_youtube": "Unggah ke YouTube", "manual_pack": "Paket unggah manual", "notify": "Kirim ke Telegram",
    "analyst": "Analisis performa", "state_update": "Catat riwayat", "weekly_report": "Laporan mingguan",
}
# nama berkas hasil tiap langkah, untuk mengenali baris log "lewati <nama>: ..."
_RESULT_NAME = {"research": "research", "script": "script", "qc": "qc", "seo": "metadata", "tts": "words",
                "visuals": "visuals", "render": "render", "publish_youtube": "youtube", "manual_pack": "captions",
                "notify": "telegram", "analyst": "analyst"}
# upload = unggah uji ke YouTube (private); notify = kirim paket ke Telegram. Keduanya bukan dry-run.
# analyst = perbarui analisis performa; report = kirim laporan mingguan sekarang. Keduanya tanpa folder run.
MODES = ("plan", "video", "media", "upload", "notify", "analyst", "report")
GLOBAL_MODES = ("analyst", "report")
LOG_LINES = 300
_PREFIX = re.compile(r"^\[channel\]\s*(?:\d{2}:\d{2}:\d{2}\s+)?")


class JobBusy(RuntimeError):
    """Sudah ada run yang berjalan."""


def steps_for(mode: str) -> list[str]:
    if mode == "upload":
        return ["publish_youtube"]
    if mode == "notify":
        return ["notify"]
    if mode == "analyst":
        return ["analyst"]
    if mode == "report":
        return ["weekly_report"]
    # uji coba: langkah upload hanya mencatat "tidak diunggah", jadi tidak ditampilkan
    return [s for s in pipeline.resolve_steps(mode) if s in pipeline.IMPLEMENTED and s not in pipeline.PUBLISH_STEPS]


class RunJob:
    """Satu run pipeline (uji coba atau unggah uji) di thread latar. Semua atribut dibaca halaman lewat
    snapshot()."""

    def __init__(self, day: date, mode: str, genre: str | None = None, force: list[str] = ()):
        self.id = uuid.uuid4().hex[:10]
        self.day, self.mode, self.genre, self.force = day, mode, genre, list(force)
        self.status = "proses"           # proses | selesai | gagal | batal
        self.error = ""
        self.warning = ""                # selesai tetapi perlu perhatian (mis. YouTube menolak video)
        self.started, self.finished = time.time(), 0.0
        self.code: int | None = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.log: list[str] = []
        self.current = ""
        self.steps = {s: {"id": s, "label": STEP_LABELS.get(s, s), "state": "antri", "progress": 0.0}
                      for s in steps_for(mode)}
        self.thread: threading.Thread | None = None

    # ------------------------------------------------------------ dari pipeline
    def echo(self, line: str) -> None:
        text = _PREFIX.sub("", str(line)).strip()
        with self.lock:
            self.log.append(text)
            del self.log[:-LOG_LINES]
            m = re.match(r"lewati (\w+):", text)
            if m:
                for step, name in _RESULT_NAME.items():
                    if name == m.group(1) and step in self.steps:
                        self.steps[step]["reused"] = True
            m = re.match(r"(telegram \(kirim uji\)|youtube \(unggah uji\)|analis|laporan mingguan) gagal: (.*)",
                         text)
            if m:                                   # tindakan pengguna yang tidak memakai penanda run
                self.error = m.group(2)[:600]
                step = {"telegram": "notify", "youtube": "publish_youtube", "analis": "analyst",
                        "laporan": "weekly_report"}[m.group(1).split(" ", 1)[0]]
                if step in self.steps:
                    self.steps[step]["state"] = "gagal"
            m = re.match(r"(GAGAL|DIBATALKAN) di langkah (\w+)(?::\s*(.*))?", text)
            if m:
                step = m.group(2)
                if step in self.steps:
                    self.steps[step]["state"] = "batal" if m.group(1) == "DIBATALKAN" else "gagal"
                if m.group(1) == "GAGAL":
                    self.error = (m.group(3) or text)[:600]

    def on_progress(self, step: str, frac: float) -> None:
        with self.lock:
            st = self.steps.get(step)
            if st is None:
                return
            self.current = step
            st["progress"] = round(max(0.0, min(1.0, frac)), 3)
            st["state"] = ("dipakai" if st.get("reused") else "selesai") if frac >= 1 else "proses"

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name=f"channel-run-{self.id}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            if self.mode == "upload":
                code = pipeline.upload_test(self.day, echo=self.echo, cancel=self.cancel,
                                            on_progress=self.on_progress)
            elif self.mode == "notify":
                code = pipeline.notify_test(self.day, echo=self.echo, cancel=self.cancel,
                                            on_progress=self.on_progress)
            elif self.mode == "analyst":
                code = pipeline.analyze_now(echo=self.echo, cancel=self.cancel, on_progress=self.on_progress)
            elif self.mode == "report":
                code = pipeline.weekly_report_now(echo=self.echo, cancel=self.cancel, on_progress=self.on_progress)
            else:
                code = pipeline.run_daily(self.day, dry_run=True, only=self.mode, force=self.force,
                                          genre=self.genre, echo=self.echo, cancel=self.cancel,
                                          on_progress=self.on_progress)
        except Exception as e:  # noqa: BLE001  galat tak terduga tetap dilaporkan ke halaman
            traceback.print_exc()
            code, self.error = pipeline.EXIT_FAILED, f"Galat tak terduga: {e}"[:600]
        warning = ""
        if code == pipeline.EXIT_OK and self.mode == "upload":
            from .runctx import RunContext
            from .schemas import YouTubeResult
            yt = RunContext(self.day, echo=None).load("youtube", YouTubeResult)
            if yt is not None and yt.status == "masalah":
                warning = (yt.note or "Video terunggah tetapi ada masalah di YouTube.")[:600]
        if code == pipeline.EXIT_OK and self.mode == "analyst":
            from . import analyst
            perf = analyst.load()
            if perf is not None and perf.notes:
                warning = perf.notes[0][:600]           # mis. YouTube Analytics API belum diaktifkan
            elif perf is None:                          # YouTube belum/tidak lagi terhubung: alasannya di learnings.md
                from .state import load_learnings
                why = next((x for x in load_learnings().splitlines() if x.strip() and not x.startswith("#")), "")
                warning = (why or "Performa video belum bisa dibaca.")[:600]
        with self.lock:
            self.code = code
            self.finished = time.time()
            self.warning = warning
            if code == pipeline.EXIT_OK:
                self.status = "selesai"
            elif self.cancel.is_set() and not self.error:
                # dibatalkan pengguna (bukan gagal karena hal lain sebelum pembatalan sempat terbaca)
                self.status, self.error = "batal", "Dibatalkan."
            else:
                self.status = "gagal"
                if not self.error:
                    last = next((x for x in reversed(self.log) if x), "")
                    self.error = (last or "Run gagal.")[:600]
            for st in self.steps.values():
                if st["state"] in ("antri", "proses") and self.status != "selesai":
                    st["state"] = "batal" if self.status == "batal" else "lewat"

    def running(self) -> bool:
        return self.status == "proses"

    def snapshot(self) -> dict:
        with self.lock:
            steps = [dict(s) for s in self.steps.values()]
            done = sum(1.0 if s["state"] in ("selesai", "dipakai") else s["progress"] for s in steps)
            cur = self.steps.get(self.current)
            return {
                "id": self.id, "day": self.day.isoformat(), "mode": self.mode, "genre": self.genre or "",
                "status": self.status, "error": self.error, "warning": self.warning, "code": self.code,
                "cancelling": self.status == "proses" and self.cancel.is_set(),
                "started": self.started, "finished": self.finished,
                "stage": (cur["label"] if cur and self.status == "proses" else ""),
                "progress": round(done / len(steps), 3) if steps else 0.0,
                "steps": [{k: v for k, v in s.items() if k != "reused"} for s in steps],
                "log": list(self.log[-80:]),
            }


# --------------------------------------------------------------------------
# Satu pekerjaan aktif per proses
# --------------------------------------------------------------------------
_lock = threading.Lock()
_job: RunJob | None = None


def current() -> RunJob | None:
    return _job


def busy() -> bool:
    return _job is not None and _job.running()


def start(day: date, mode: str, genre: str | None = None, force: list[str] = ()) -> RunJob:
    global _job
    bad = [f for f in force if f not in STEPS]
    if mode not in MODES:
        raise ValueError(f"Mode '{mode}' tidak dikenal.")
    if bad:
        raise ValueError(f"Langkah tidak dikenal: {', '.join(bad)}")
    with _lock:
        if busy():
            raise JobBusy("Masih ada run yang berjalan. Tunggu selesai atau batalkan dulu.")
        _job = RunJob(day, mode, genre, force)
        _job.start()
        return _job


def cancel() -> bool:
    job = _job
    if job is None or not job.running():
        return False
    job.cancel.set()
    return True
