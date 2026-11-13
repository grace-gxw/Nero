"""
Nero · Channel Otomatis - jadwal harian di dalam Nero (Fase I-8; keputusan no. 4: jadwal di laptop, bukan GitHub
Actions).

Satu thread latar (dimulai api.start() saat Nero dibuka) memeriksa setiap menit:
- Channel Otomatis nonaktif (`enabled = false`): tidak melakukan apa pun.
- Jam run harian (`schedule.run_time`, waktu `schedule.timezone`) belum tiba: menunggu.
- Sudah lewat dan run sungguhan hari ini belum dijalankan: run dimulai sebagai pekerjaan "daily" (jobs.py), jadi
  progresnya tampil di halaman Channel. Bila Nero baru menyala setelah jam itu (laptop mati/tidur), run hari itu
  dikejar kapan pun di hari yang sama. Hari yang sudah lewat tidak dikejar.
- Run gagal: dicoba lagi 1 jam lalu 2 jam kemudian (maks. 3 percobaan per hari). Setiap kegagalan sudah dikirim ke
  Telegram oleh run harian (SPEC 6.11). Pengaturan yang tidak valid (mis. genre belum dipilih) tidak dicoba ulang dan
  diberitahukan sekali. Run yang dibatalkan pengguna tidak dicoba ulang otomatis.
- Pekerjaan lain dari halaman (uji coba, unggah uji) sedang berjalan, atau run tanggal ini sedang dijalankan proses
  lain (CLI): menunggu giliran.

Run harian memakai checkpoint, jadi percobaan ulang melanjutkan langkah yang belum selesai; video tidak pernah
diunggah dua kali (penjaga di youtube.py). Jam dinding diperiksa ulang setiap menit, sehingga laptop yang bangun dari
tidur langsung mengejar jadwal.

Status disimpan di data/channel/schedule.json: {day, state, attempts, job_id, started_at, finished_at,
next_retry, note}. state: menunggu | berjalan | selesai | gagal | dilewati | dibatalkan | menyerah.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, jobs, state, telegram, timeutil
from .config import ConfigError
from .runctx import RunContext, lock_active

TICK_SECONDS = 60
# Setelah Nero dibuka (login) atau laptop bangun dari tidur: tunggu sebentar supaya jaringan siap dan jam sistem sempat
# dikoreksi. Bangun terdeteksi dari lompatan time.monotonic() (QueryPerformanceCounter ikut berjalan selama tidur,
# sedangkan timeout Event.wait tidak).
GRACE_SECONDS = 120
RETRY_MINUTES = (60, 120)             # jeda sebelum percobaan ke-2 dan ke-3
MAX_ATTEMPTS = 1 + len(RETRY_MINUTES)
LOCKED_RETRY_MINUTES = 5              # run tanggal ini sedang dijalankan proses lain
STATE_FILE = "schedule.json"
EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_LOCKED = 0, 1, 2, 3     # sama dengan pipeline.EXIT_*


# --------------------------------------------------------------------------
# schedule.json
# --------------------------------------------------------------------------
def _path() -> str:
    return state.data_path(STATE_FILE)


def load_state() -> dict:
    data = state.read_json(_path(), {})
    return data if isinstance(data, dict) else {}


def _save(st: dict) -> None:
    state.write_json_atomic(_path(), st)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse(text) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else None


def clock_back(st: dict, today: date) -> bool:
    """Catatan jadwal tepat sehari di depan tanggal sekarang: jam komputer mundur (mis. koreksi waktu Windows).
    Selisih yang lebih jauh dianggap jam yang tadinya keliru maju: catatannya diganti hari ini seperti biasa."""
    try:
        ahead = (date.fromisoformat(str(st.get("day") or "")) - today).days
    except ValueError:
        return False
    return ahead == 1


def run_at(cfg: config.ChannelConfig, day: date) -> datetime:
    """Jam run harian pada tanggal `day` (zona waktu pengaturan)."""
    return datetime.combine(day, timeutil.parse_hhmm(cfg.schedule.run_time), tzinfo=ZoneInfo(cfg.schedule.timezone))


# --------------------------------------------------------------------------
# Satu pemeriksaan
# --------------------------------------------------------------------------
class Scheduler:
    def __init__(self):
        self._lock = threading.Lock()          # tick dari thread jadwal & tombol "Jalankan sekarang" tidak bentrok
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.last = ""                         # hasil pemeriksaan terakhir (untuk halaman & log)

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="channel-schedule", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def kick(self) -> None:
        """Periksa sekarang (mis. pengaturan baru disimpan atau pekerjaan halaman selesai)."""
        self._wake.set()

    def _loop(self) -> None:
        hold = time.monotonic() + GRACE_SECONDS            # baru dibuka (mis. saat login): jaringan belum tentu siap
        last = time.monotonic()
        while not self._stop.is_set():
            now_m = time.monotonic()
            if now_m - last > 3 * TICK_SECONDS:            # bangun dari tidur/hibernate
                hold = max(hold, now_m + GRACE_SECONDS)
                print(f"[channel] jadwal: laptop bangun setelah ±{(now_m - last) / 60:.0f} menit; diperiksa lagi "
                      f"{GRACE_SECONDS // 60} menit lagi")
            last = now_m
            try:
                self.tick(allow_start=now_m >= hold)
            except Exception:  # noqa: BLE001  satu pemeriksaan yang gagal tidak boleh mematikan jadwal
                traceback.print_exc()
            self._wake.wait(TICK_SECONDS)
            self._wake.clear()

    # ------------------------------------------------------------ logika
    def tick(self, now: datetime | None = None, allow_start: bool = True) -> str:
        """Satu pemeriksaan jadwal. Hasil: keadaan/aksi (untuk tes & halaman). allow_start=False: hanya mencatat
        hasil run (masa tunggu setelah Nero dibuka / laptop bangun)."""
        with self._lock:
            self.last = self._tick(now, allow_start)
            return self.last

    def _tick(self, now: datetime | None, allow_start: bool) -> str:
        try:
            cfg = config.load_config()
        except ConfigError:
            return "pengaturan_rusak"
        now = (now or timeutil.now_local(cfg.schedule.timezone)).astimezone(ZoneInfo(cfg.schedule.timezone))
        today = now.date()
        # hasil run yang tadinya berjalan dicatat dulu, juga saat nonaktif (mis. dimatikan di tengah run lalu dibatalkan)
        st = self._settle(load_state(), now, cfg)
        if not cfg.enabled:
            return "nonaktif"
        if clock_back(st, today):
            # jam sistem mundur (mis. koreksi waktu Windows): jangan memulai run kedua untuk hari yang sudah dijalankan
            return "jam_mundur"
        if st.get("day") != today.isoformat():
            st = {"day": today.isoformat(), "state": "menunggu", "attempts": 0}
            _save(st)
        if st["state"] in ("berjalan", "selesai", "dilewati", "dibatalkan", "menyerah"):
            return st["state"]
        if st["state"] != "gagal" and now < run_at(cfg, today):
            return "menunggu"                             # run yang gagal (mis. dari tombol) mengikuti jeda coba ulang
        retry = _parse(st.get("next_retry"))
        if retry is not None and now < retry:
            return "menunggu_ulang"
        if not allow_start:
            return "masa_tunggu"
        return self._start(cfg, st, now)

    def run_now(self) -> str:
        """Tombol "Jalankan run harian sekarang": seperti jadwal, tetapi tanpa menunggu jam run atau jeda coba ulang.
        Hanya bila Channel Otomatis aktif dan run hari ini belum selesai/berjalan."""
        with self._lock:
            cfg = config.load_config()
            if not cfg.enabled:
                raise ConfigError("Channel Otomatis masih nonaktif. Nyalakan dulu di pengaturan.")
            now = timeutil.now_local(cfg.schedule.timezone)
            today = now.date()
            st = self._settle(load_state(), now, cfg)
            if clock_back(st, today):
                raise ConfigError("Jam komputer mundur ke hari yang sudah dijalankan. Periksa tanggal & jam Windows.")
            if st.get("day") != today.isoformat():
                st = {"day": today.isoformat(), "state": "menunggu", "attempts": 0}
            if st["state"] == "berjalan":
                raise jobs.JobBusy("Run harian hari ini sedang berjalan.")
            if st["state"] == "selesai":
                raise ConfigError("Run harian hari ini sudah selesai.")
            st["attempts"] = 0                           # dimulai pengguna: jatah coba ulang dimulai lagi
            self.last = self._start(cfg, st, now, by_user=True)
            if self.last not in ("mulai",):
                raise jobs.JobBusy("Masih ada pekerjaan lain yang berjalan. Tunggu selesai dulu.")
            return self.last

    def _start(self, cfg: config.ChannelConfig, st: dict, now: datetime, by_user: bool = False) -> str:
        today = now.date()
        if jobs.busy():
            return "antri"                                # uji coba / unggah uji dari halaman sedang berjalan
        if lock_active(RunContext(today, echo=None).path(".lock")):
            return "antri"                                # run tanggal ini sedang dijalankan proses lain (CLI)
        try:
            job = jobs.start(today, "daily")
        except jobs.JobBusy:
            return "antri"
        st.update(state="berjalan", attempts=int(st.get("attempts") or 0) + 1, job_id=job.id,
                  started_at=_iso(now), finished_at="", next_retry="",
                  note="dimulai dari halaman" if by_user else "")
        _save(st)
        print(f"[channel] jadwal: run harian {today.isoformat()} dimulai (percobaan {st['attempts']})")
        return "mulai"

    def _settle(self, st: dict, now: datetime, cfg: config.ChannelConfig) -> dict:
        """Catat hasil run harian yang tadinya berjalan (pekerjaan selesai, atau Nero ditutup di tengah run)."""
        if st.get("state") != "berjalan":
            return st
        job = jobs.current()
        if job is not None and job.id == st.get("job_id"):
            if job.running():
                return st
            return self._finish(st, job.code, job.status, job.error, now, cfg)
        day = date.fromisoformat(st["day"]) if st.get("day") else None
        if day is not None and lock_active(RunContext(day, echo=None).path(".lock")):
            # run tanggal itu masih dipegang proses yang hidup (mis. CLI, atau Nero lain). Kunci milik Nero yang mati
            # tidak dihitung, termasuk bila PID-nya sudah dipakai proses baru (runctx.lock_active)
            return st
        # pekerjaannya tidak ada lagi: Nero ditutup/mati di tengah run. Dilanjutkan (checkpoint) di percobaan berikutnya.
        return self._finish(st, EXIT_FAILED, "gagal", "Run terputus (Nero ditutup di tengah run).", now, cfg)

    def _finish(self, st: dict, code: int | None, status: str, error: str, now: datetime,
                cfg: config.ChannelConfig) -> dict:
        st = {**st, "finished_at": _iso(now), "job_id": ""}
        if code == EXIT_OK:
            st.update(state="selesai", note="")
        elif status == "batal":
            st.update(state="dibatalkan", note="Dibatalkan dari halaman; tidak dicoba ulang otomatis.")
        elif code == EXIT_LOCKED:
            st.update(state="gagal", attempts=max(0, int(st.get("attempts") or 1) - 1),
                      next_retry=_iso(now + timedelta(minutes=LOCKED_RETRY_MINUTES)),
                      note="Run tanggal ini sedang dijalankan proses lain.")
        elif code == EXIT_CONFIG:
            st.update(state="dilewati", note=(error or "Pengaturan channel belum lengkap.")[:300])
            if cfg.enabled:
                _notify_skip(st["day"], st["note"])
        else:
            attempts = int(st.get("attempts") or 1)
            retry = now + timedelta(minutes=RETRY_MINUTES[min(attempts, MAX_ATTEMPTS - 1) - 1])
            note = (error or "Run gagal.")[:300]
            if attempts >= MAX_ATTEMPTS:
                st.update(state="menyerah", note=note, next_retry="")
            elif st.get("day") and retry.date().isoformat() > st["day"]:
                # hari sudah berganti sebelum coba ulang (hari lalu tidak dikejar): tidak dijanjikan di halaman
                st.update(state="menyerah", note=f"{note} Tidak dicoba lagi karena hari sudah berganti.", next_retry="")
            else:
                st.update(state="gagal", note=note, next_retry=_iso(retry))
        _save(st)
        print(f"[channel] jadwal: run harian {st['day']} {st['state']}" + (f" ({st['note']})" if st["note"] else ""))
        return st


def _notify_skip(day: str, why: str) -> None:
    """Run harian tidak bisa dimulai karena pengaturan (bukan kegagalan langkah, jadi belum ada peringatan dari run):
    beri tahu sekali lewat Telegram. Galat kiriman hanya dicatat."""
    try:
        cfg = config.load_config()
        if not cfg.notify.telegram or not telegram.configured():
            return
        telegram.send_text(f"<b>Channel Otomatis · {telegram.escape(day)}</b>\nRun harian tidak dijalankan: "
                           f"{telegram.escape(why)}")
    except Exception as e:  # noqa: BLE001  pemberitahuan yang gagal tidak boleh mengganggu jadwal
        print(f"[channel] jadwal: pemberitahuan Telegram gagal ({e})")


# --------------------------------------------------------------------------
# Untuk halaman
# --------------------------------------------------------------------------
_scheduler = Scheduler()


def get() -> Scheduler:
    return _scheduler


def status(cfg: config.ChannelConfig | None = None, now: datetime | None = None) -> dict:
    """Ringkasan jadwal tanpa efek samping: aktif, jam run, keadaan run hari ini, percobaan ulang, run berikutnya."""
    cfg = cfg or config.load_config()
    tz = ZoneInfo(cfg.schedule.timezone)
    now = (now or timeutil.now_local(cfg.schedule.timezone)).astimezone(tz)
    today = now.date()
    st = load_state()
    back = clock_back(st, today)
    if back:
        st = {**st, "state": "jam_mundur", "note": "Jam komputer mundur ke hari yang sudah dijalankan; run ditahan "
                                                   "sampai tanggalnya kembali benar."}
    elif st.get("day") != today.isoformat():
        st = {"day": today.isoformat(), "state": "menunggu", "attempts": 0}
    at = run_at(cfg, today)
    nxt = at if now < at and st["state"] == "menunggu" else run_at(cfg, today + timedelta(days=1))
    if st["state"] == "gagal" and st.get("next_retry"):
        nxt = _parse(st["next_retry"]) or nxt
    elif st["state"] == "menunggu" and now >= at:
        nxt = now                                          # dikejar pada pemeriksaan berikutnya (≤ 1 menit)
    return {"enabled": cfg.enabled, "run_time": cfg.schedule.run_time, "timezone": cfg.schedule.timezone,
            "today": {k: st.get(k, "") for k in ("day", "state", "attempts", "started_at", "finished_at",
                                                 "next_retry", "note")},
            "next_run": _iso(nxt) if cfg.enabled and not back else "", "max_attempts": MAX_ATTEMPTS,
            "thread": bool(_scheduler.thread and _scheduler.thread.is_alive()), "last": _scheduler.last}
