"""Channel Otomatis, Fase I-8: jadwal harian di dalam Nero (scheduler.py), pekerjaan "daily", tetap terjaga selama
run (power.py), dan pengaman jam tayang ganda. Jam & pekerjaan palsu; tanpa thread jadwal sungguhan dan tanpa
internet."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import os
import threading
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo
import unittest

from tests.util import ROOT  # noqa: F401
from tests.test_channel_notify import CHAT, TOKEN, NotifySandbox  # noqa: F401

import app
from features.channel import api, config, jobs, pipeline, power, runctx, scheduler, timeutil, youtube
from features.channel.config import ConfigError
from features.channel.schemas import YouTubeResult

WIB = ZoneInfo("Asia/Jakarta")
BASE = "http://127.0.0.1:7860"


def at(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=WIB)


class FakeJob:
    """Pekerjaan palsu: berjalan sampai finish() dipanggil."""
    n = 0

    def __init__(self, day, mode):
        FakeJob.n += 1
        self.id, self.day, self.mode = f"J{FakeJob.n}", day, mode
        self.status, self.code, self.error = "proses", None, ""
        self.cancel = threading.Event()

    def running(self):
        return self.status == "proses"

    def finish(self, code=0, status=None, error=""):
        self.code, self.error = code, error
        self.status = status or ("selesai" if code == 0 else "gagal")

    def snapshot(self):
        return {"id": self.id, "day": self.day.isoformat(), "mode": self.mode, "status": self.status}


class ScheduleSandbox(NotifySandbox):
    def setUp(self):
        super().setUp()
        self.started = []

        def start(day, mode, genre=None, force=()):
            if jobs.busy():
                raise jobs.JobBusy("sibuk")
            job = FakeJob(day, mode)
            self.started.append(job)
            jobs._job = job
            return job

        self.p_start = mock.patch.object(jobs, "start", start)
        self.p_start.start()
        self.sch = scheduler.Scheduler()

    def tearDown(self):
        self.p_start.stop()
        jobs._job = None
        super().tearDown()

    def enable(self, **sched):
        config.save_config({"enabled": True, "schedule": sched})

    def st(self):
        return scheduler.load_state()


# --------------------------------------------------------------------------
class Jadwal(ScheduleSandbox):
    def test_nonaktif_tidak_menjalankan_apa_pun(self):
        self.assertEqual(self.sch.tick(at(13, 6)), "nonaktif")
        self.assertEqual(self.started, [])
        self.assertFalse(os.path.exists(scheduler._path()))

    def test_menunggu_lalu_mulai_tepat_waktu(self):
        self.enable(run_time="05:17")
        self.assertEqual(self.sch.tick(at(13, 5, 16)), "menunggu")
        self.assertEqual(self.started, [])
        self.assertEqual(self.sch.tick(at(13, 5, 17)), "mulai")
        job = self.started[0]
        self.assertEqual((job.day.isoformat(), job.mode), ("2026-11-13", "daily"))
        st = self.st()
        self.assertEqual((st["state"], st["attempts"]), ("berjalan", 1))
        self.assertEqual(self.sch.tick(at(13, 5, 18)), "berjalan")
        job.finish(0)
        self.assertEqual(self.sch.tick(at(13, 5, 40)), "selesai")
        self.assertEqual(self.sch.tick(at(13, 23, 59)), "selesai")
        self.assertEqual(len(self.started), 1)                       # satu run per hari

    def test_dikejar_saat_nero_baru_menyala(self):
        self.enable(run_time="05:17")
        self.assertEqual(self.sch.tick(at(13, 20, 30)), "mulai")       # laptop mati pagi tadi

    def test_hari_baru_mulai_lagi_tanpa_mengejar_kemarin(self):
        self.enable(run_time="05:17")
        self.sch.tick(at(12, 6))
        self.started[0].finish(0)
        self.sch.tick(at(12, 7))
        self.assertEqual(self.sch.tick(at(14, 4)), "menunggu")        # 13 Nov terlewat (Nero mati): tidak dikejar
        self.assertEqual(self.st()["day"], "2026-11-14")
        self.assertEqual(self.sch.tick(at(14, 5, 20)), "mulai")
        self.assertEqual(self.started[-1].day.isoformat(), "2026-11-14")

    def test_gagal_dicoba_ulang_lalu_menyerah(self):
        self.enable(run_time="05:00")
        self.sch.tick(at(13, 5))
        self.started[-1].finish(1, error="Gemini penuh")
        self.assertEqual(self.sch.tick(at(13, 5, 10)), "menunggu_ulang")
        st = self.st()
        self.assertEqual((st["state"], st["note"]), ("gagal", "Gemini penuh"))
        self.assertEqual(st["next_retry"], at(13, 6, 10).isoformat())
        self.assertEqual(self.sch.tick(at(13, 6, 10)), "mulai")
        self.started[-1].finish(1, error="lagi")
        self.sch.tick(at(13, 6, 20))
        self.assertEqual(self.st()["next_retry"], at(13, 8, 20).isoformat())     # +2 jam
        self.assertEqual(self.sch.tick(at(13, 8, 20)), "mulai")
        self.started[-1].finish(1, error="masih gagal")
        self.assertEqual(self.sch.tick(at(13, 9)), "menyerah")
        self.assertEqual(self.sch.tick(at(13, 23)), "menyerah")
        self.assertEqual(len(self.started), scheduler.MAX_ATTEMPTS)

    def test_pengaturan_belum_lengkap_dilewati_dan_diberitahu_sekali(self):
        self.enable(run_time="05:00")
        self.tg_keys()
        self.sch.tick(at(13, 5))
        self.started[-1].finish(pipeline.EXIT_CONFIG, error="Genre/niche channel belum dipilih.")
        self.assertEqual(self.sch.tick(at(13, 5, 1)), "dilewati")
        self.assertEqual(self.sch.tick(at(13, 9)), "dilewati")
        sent = self.t.of("sendMessage")
        self.assertEqual(len(sent), 1)
        self.assertIn("Genre/niche", sent[0]["data"]["text"])
        self.assertEqual(len(self.started), 1)

    def test_dibatalkan_pengguna_tidak_diulang_otomatis(self):
        self.enable(run_time="05:00")
        self.sch.tick(at(13, 5))
        self.started[-1].finish(1, status="batal")
        self.assertEqual(self.sch.tick(at(13, 9)), "dibatalkan")
        self.assertEqual(len(self.started), 1)

    def test_menunggu_giliran_pekerjaan_lain(self):
        self.enable(run_time="05:00")
        user = FakeJob(at(13, 0).date(), "video")                     # uji coba dari halaman
        jobs._job = user
        self.assertEqual(self.sch.tick(at(13, 5)), "antri")
        self.assertEqual(self.st()["attempts"], 0)
        user.finish(0)
        self.assertEqual(self.sch.tick(at(13, 5, 2)), "mulai")

    def test_run_tanggal_ini_dikunci_proses_lain(self):
        self.enable(run_time="05:00")
        ctx = runctx.RunContext(at(13, 0).date(), echo=None)
        ctx.acquire()                                                 # mis. CLI run-daily
        try:
            self.assertEqual(self.sch.tick(at(13, 5)), "antri")
        finally:
            ctx.release()
        self.assertEqual(self.sch.tick(at(13, 5, 1)), "mulai")

    def test_nero_ditutup_di_tengah_run(self):
        self.enable(run_time="05:00")
        self.sch.tick(at(13, 5))
        jobs._job = None                                              # Nero dibuka ulang: pekerjaannya hilang
        sch2 = scheduler.Scheduler()
        self.assertEqual(sch2.tick(at(13, 5, 30), allow_start=False), "masa_tunggu")
        self.assertEqual(self.st()["state"], "gagal")
        self.assertIn("terputus", self.st()["note"])
        self.assertEqual(sch2.tick(at(13, 5, 32)), "mulai")          # dilanjutkan dari checkpoint begitu Nero siap

    def test_kunci_proses_lain_tidak_menghabiskan_jatah(self):
        self.enable(run_time="05:00")
        self.sch.tick(at(13, 5))
        self.started[-1].finish(pipeline.EXIT_LOCKED)
        self.sch.tick(at(13, 5, 1))
        st = self.st()
        self.assertEqual(st["attempts"], 0)
        self.assertEqual(st["next_retry"], at(13, 5, 6).isoformat())

    def test_jam_sistem_mundur_tidak_menjalankan_ulang(self):
        self.enable(run_time="05:00")
        self.sch.tick(at(14, 6))
        self.started[-1].finish(0)
        self.sch.tick(at(14, 6, 30))
        self.assertEqual(self.sch.tick(at(13, 7)), "jam_mundur")      # mis. koreksi waktu Windows ke kemarin
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.st()["day"], "2026-11-14")

    def test_masa_tunggu_setelah_dibuka_atau_bangun(self):
        self.enable(run_time="05:00")
        self.assertEqual(self.sch.tick(at(13, 7), allow_start=False), "masa_tunggu")
        self.assertEqual(self.started, [])
        self.assertEqual(self.sch.tick(at(13, 7, 2)), "mulai")

    def test_loop_menahan_lalu_mengejar_setelah_bangun(self):
        self.enable(run_time="05:00")
        clock = iter([0.0, 0.0, 30.0, 30.0 + 4 * 3600, 30.0 + 4 * 3600 + 130])   # dibuka, tick, bangun dari tidur, +130 dtk
        calls = []

        def fake_tick(now=None, allow_start=True):
            calls.append(allow_start)
            if len(calls) == 3:
                self.sch._stop.set()
            return "x"

        with mock.patch.object(scheduler.time, "monotonic", lambda: next(clock)), \
                mock.patch.object(self.sch, "tick", fake_tick), \
                mock.patch.object(self.sch._wake, "wait", lambda t: None):
            self.sch._loop()
        self.assertEqual(calls, [False, False, True])                 # masa tunggu awal, masa tunggu bangun, lalu boleh

    def test_jam_run_tidak_valid_ditolak(self):
        with self.assertRaises(ConfigError):
            config.save_config({"schedule": {"run_time": "25:00"}})
        config.save_config({"schedule": {"run_time": "6.05"}})
        self.assertEqual(config.load_config().schedule.run_time, "06:05")


class PerbaikanReview(ScheduleSandbox):
    def test_hasil_dicatat_walau_dinonaktifkan_di_tengah_run(self):
        self.enable(run_time="05:00")
        self.tg_keys()
        self.sch.tick(at(13, 5))
        config.save_config({"enabled": False})
        self.started[-1].finish(1, status="batal")                    # dibatalkan pengguna setelah dimatikan
        self.assertEqual(self.sch.tick(at(13, 5, 30)), "nonaktif")
        self.assertEqual(self.st()["state"], "dibatalkan")           # tetap tercatat walau nonaktif
        jobs._job = FakeJob(at(13, 0).date(), "plan")                 # uji coba lain menggantikan pekerjaan terakhir
        jobs._job.finish(0)
        config.save_config({"enabled": True})
        self.assertEqual(self.sch.tick(at(13, 9)), "dibatalkan")      # tidak dianggap terputus, tidak diulang
        self.assertEqual(len(self.started), 1)

    def test_dilewati_saat_nonaktif_tidak_mengirim_telegram(self):
        self.enable(run_time="05:00")
        self.tg_keys()
        self.sch.tick(at(13, 5))
        config.save_config({"enabled": False})
        self.started[-1].finish(pipeline.EXIT_CONFIG, error="Channel Otomatis masih nonaktif.")
        self.sch.tick(at(13, 5, 1))
        self.assertEqual(self.st()["state"], "dilewati")
        self.assertEqual(self.t.of("sendMessage"), [])

    def test_mematikan_di_halaman_membatalkan_run_harian(self):
        self.enable(run_time="00:00")
        self.sch.run_now()
        job = self.started[-1]
        c = app.app.test_client()
        r = c.post("/api/channel/settings", json={"config": {"enabled": False}}, base_url=BASE)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(job.cancel.is_set())

    def test_kunci_basi_milik_nero_sebelumnya_dilepas(self):
        self.enable(run_time="05:00")
        day = at(13, 0).date()
        ctx = runctx.RunContext(day, echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        other = os.getppid()                                          # PID hidup milik proses lain (didaur ulang)
        with open(ctx.path(".lock"), "w") as f:
            f.write(str(other))
        scheduler._save({"day": day.isoformat(), "state": "berjalan", "attempts": 1, "job_id": "LAMA"})
        with mock.patch.object(runctx, "started_after", return_value=True):   # proses itu dibuat setelah kunci ditulis
            self.assertFalse(runctx.lock_active(ctx.path(".lock")))
            self.assertEqual(self.sch.tick(at(13, 6), allow_start=False), "masa_tunggu")
            self.assertIn("terputus", self.st()["note"])
            self.assertEqual(self.sch.tick(at(13, 6, 2)), "mulai")

    def test_waktu_pembuatan_proses(self):
        import time
        self.assertTrue(runctx.started_after(os.getpid(), time.time() - 365 * 86400))
        self.assertFalse(runctx.started_after(os.getpid(), time.time() + 10))
        self.assertFalse(runctx.started_after(0, 0))

    def test_kunci_proses_lain_tetap_ditunggu(self):
        self.enable(run_time="05:00")
        day = at(13, 0).date()
        ctx = runctx.RunContext(day, echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        with open(ctx.path(".lock"), "w") as f:
            f.write(str(os.getppid()))                                 # mis. CLI run-daily yang sedang berjalan
        scheduler._save({"day": day.isoformat(), "state": "berjalan", "attempts": 1, "job_id": "LAMA"})
        self.assertEqual(self.sch.tick(at(13, 6)), "berjalan")
        self.assertTrue(os.path.exists(ctx.path(".lock")))            # kunci proses lain yang hidup tidak disentuh

    def test_coba_ulang_lewat_tengah_malam_tidak_dijanjikan(self):
        self.enable(run_time="22:00")
        self.sch.tick(at(13, 23, 30))
        self.started[-1].finish(1, error="Gemini penuh")
        self.sch.tick(at(13, 23, 40))
        st = self.st()
        self.assertEqual((st["state"], st["next_retry"]), ("menyerah", ""))
        self.assertIn("hari sudah berganti", st["note"])
        s = scheduler.status(config.load_config(), at(13, 23, 45))
        self.assertEqual(s["next_run"], at(14, 22).isoformat())

    def test_gagal_dari_tombol_sebelum_jam_run_ikut_jeda_coba_ulang(self):
        self.enable(run_time="05:17")
        with mock.patch.object(timeutil, "now_local", return_value=at(13, 1)):
            self.sch.run_now()
        self.started[-1].finish(1)
        self.assertEqual(self.sch.tick(at(13, 1, 20)), "menunggu_ulang")
        self.assertEqual(self.sch.tick(at(13, 2, 21)), "mulai")       # tidak menunggu sampai 05.17

    def test_jam_yang_sempat_maju_jauh_tidak_menahan_jadwal(self):
        self.enable(run_time="05:00")
        scheduler._save({"day": "2026-11-20", "state": "selesai", "attempts": 1})
        self.assertEqual(self.sch.tick(at(13, 6)), "mulai")           # selisih > 1 hari: hari ini dimulai biasa
        scheduler._save({"day": "2026-11-14", "state": "selesai", "attempts": 1})
        jobs._job = None
        self.assertEqual(self.sch.tick(at(13, 6)), "jam_mundur")      # tepat sehari: jam mundur, ditahan
        s = scheduler.status(config.load_config(), at(13, 6))
        self.assertEqual((s["today"]["state"], s["next_run"]), ("jam_mundur", ""))
        with mock.patch.object(timeutil, "now_local", return_value=at(13, 6)), self.assertRaises(ConfigError):
            self.sch.run_now()

    def test_folder_run_lama_dengan_kunci_basi_dibersihkan(self):
        from features.channel import state
        old = at(1, 0).date()
        ctx = runctx.RunContext(old, echo=None)
        os.makedirs(os.path.join(ctx.dir, "clips"), exist_ok=True)
        with open(os.path.join(ctx.dir, "clips", "a.mp4"), "wb") as f:
            f.write(b"x")
        with open(ctx.path(".lock"), "w") as f:
            f.write("0")                                              # pemiliknya sudah tidak ada
        removed = state.prune_runs(at(13, 0).date())
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(os.path.exists(os.path.join(ctx.dir, "clips")))
        self.assertFalse(os.path.exists(ctx.path(".lock")))


class SakelarDimatikanDiTengahRun(ScheduleSandbox):
    def test_upload_dan_telegram_dilewati(self):
        from tests.test_channel_youtube import DAY
        self.connect_keys()
        self.tg_keys()
        config.save_config({"enabled": True, "youtube": {"api_upload": True}})
        ctx = self.make_pack_run()
        run = self.make_run(ctx, config.load_config())                  # run dimulai saat aktif
        config.save_config({"enabled": False})                         # lalu dimatikan di halaman
        self.assertFalse(pipeline.step_publish_youtube(run, False))
        self.assertFalse(pipeline.step_notify(run, False))
        self.assertEqual(self.g.inserted, [])
        self.assertEqual(self.t.calls, [])
        self.assertIn("dimatikan di pengaturan selama run berjalan", ctx.log_tail())
        self.assertEqual(run.ctx.day, DAY)

    def test_upload_otomatis_dimatikan_saja(self):
        self.connect_keys()
        config.save_config({"enabled": True, "youtube": {"api_upload": True}})
        ctx = self.make_video_run()
        run = self.make_run(ctx, config.load_config())
        config.save_config({"youtube": {"api_upload": False}})
        self.assertFalse(pipeline.step_publish_youtube(run, False))
        self.assertEqual(self.g.inserted, [])


class JalankanSekarang(ScheduleSandbox):
    def test_hanya_saat_aktif(self):
        with self.assertRaises(ConfigError):
            self.sch.run_now()
        self.enable(run_time="23:59")
        self.assertEqual(self.sch.run_now(), "mulai")                 # tanpa menunggu jam run
        with self.assertRaises(jobs.JobBusy):
            self.sch.run_now()

    def test_setelah_menyerah_jatah_dimulai_lagi(self):
        self.enable(run_time="05:00")
        for t in (at(13, 5), at(13, 6, 10), at(13, 8, 20)):
            self.assertEqual(self.sch.tick(t), "mulai")
            self.started[-1].finish(1)
            self.sch.tick(t + timedelta(minutes=10))
        self.assertEqual(self.st()["state"], "menyerah")
        with mock.patch.object(timeutil, "now_local", return_value=at(13, 12)):
            self.assertEqual(self.sch.run_now(), "mulai")
        self.assertEqual((self.st()["state"], self.st()["attempts"]), ("berjalan", 1))

    def test_sudah_selesai_ditolak(self):
        self.enable(run_time="00:00")
        self.sch.run_now()
        self.started[-1].finish(0)
        self.sch.tick()
        with self.assertRaises(ConfigError):
            self.sch.run_now()

    def test_rute_halaman(self):
        c = app.app.test_client()
        r = c.post("/api/channel/schedule/run", json={}, base_url=BASE)
        self.assertEqual(r.status_code, 400)                          # nonaktif
        self.enable(run_time="23:59")
        with mock.patch.object(scheduler, "_scheduler", self.sch):
            r = c.post("/api/channel/schedule/run", json={}, base_url=BASE)
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(r.get_json()["job"]["mode"], "daily")
            st = c.get("/api/channel/state", base_url=BASE).get_json()["schedule"]
        self.assertTrue(st["enabled"])
        self.assertEqual(st["today"]["state"], "berjalan")
        self.assertIn("autostart", st)
        self.assertEqual(c.post("/api/channel/run", json={"mode": "daily"}, base_url=BASE).status_code, 400)


class StatusJadwal(ScheduleSandbox):
    def test_run_berikutnya(self):
        self.enable(run_time="05:17")
        cfg = config.load_config()
        s = scheduler.status(cfg, at(13, 4))
        self.assertEqual(s["next_run"], at(13, 5, 17).isoformat())
        s = scheduler.status(cfg, at(13, 9))                          # terlewat: dikejar sekarang
        self.assertEqual(s["next_run"], at(13, 9).isoformat())
        self.sch.tick(at(13, 9))
        self.started[-1].finish(0)
        self.sch.tick(at(13, 9, 30))
        s = scheduler.status(cfg, at(13, 10))
        self.assertEqual((s["today"]["state"], s["next_run"]), ("selesai", at(14, 5, 17).isoformat()))

    def test_nonaktif_tanpa_run_berikutnya(self):
        s = scheduler.status(config.load_config(), at(13, 4))
        self.assertFalse(s["enabled"])
        self.assertEqual(s["next_run"], "")


class PekerjaanHarian(ScheduleSandbox):
    def setUp(self):
        super().setUp()
        self.p_start.stop()                                           # pekerjaan sungguhan (jobs.RunJob)

    def tearDown(self):
        job = jobs.current()
        if job is not None and getattr(job, "thread", None):
            job.thread.join(10)
        self.p_start.start()
        super().tearDown()

    def test_run_sungguhan_tetap_terjaga_dan_memberi_tahu_jadwal(self):
        calls, done, awake = [], threading.Event(), []

        def fake_run(day, **kw):
            calls.append((day, kw))
            return 0

        def listener(job):
            done.set()

        jobs.LISTENERS.append(listener)
        try:
            with mock.patch.object(pipeline, "run_daily", fake_run), \
                    mock.patch.object(power, "_set", side_effect=lambda f: awake.append(f) or True):
                job = jobs.start(at(13, 0).date(), "daily")
                self.assertTrue(done.wait(10))
        finally:
            jobs.LISTENERS.remove(listener)
        self.assertEqual(job.status, "selesai")
        self.assertFalse(calls[0][1]["dry_run"])
        self.assertNotIn("only", calls[0][1])
        self.assertEqual(awake, [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS])
        self.assertIn("analyst", [s["id"] for s in job.snapshot()["steps"]])

    def test_keep_awake_di_luar_windows_aman(self):
        with mock.patch.object(power.os, "name", "posix"), power.keep_awake() as ok:
            self.assertFalse(ok)

    def test_mulai_nero_menyalakan_jadwal(self):
        with mock.patch.object(scheduler, "_scheduler", scheduler.Scheduler()) as sch:
            api.start()
            try:
                self.assertTrue(sch.thread.is_alive())
                self.assertIn(api._kick_schedule, jobs.LISTENERS)
                api.start()                                           # dua kali: satu thread, satu pendengar
                self.assertEqual(jobs.LISTENERS.count(api._kick_schedule), 1)
            finally:
                sch.stop()
                sch.thread.join(5)
                jobs.LISTENERS.remove(api._kick_schedule)


class JamTayangGanda(ScheduleSandbox):
    def test_slot_yang_sudah_dipakai_tidak_dipakai_lagi(self):
        from tests.test_channel_youtube import DAY
        self.connect_keys()
        config.save_config({"approval": {"mode": "review_window"}})
        cfg = config.load_config()
        ctx = self.make_video_run(DAY, cfg=cfg)
        slot = timeutil.publish_at(cfg.schedule.publish_time, cfg.schedule.timezone)
        other = runctx.RunContext(DAY + timedelta(days=1), echo=None)
        os.makedirs(other.dir, exist_ok=True)
        other.save("youtube", YouTubeResult(video_id="LAIN", publish_at=timeutil.to_rfc3339_utc(slot)))
        run = self.make_run(ctx, cfg)
        self.assertEqual(pipeline._slot_taken(run, slot), (DAY + timedelta(days=1)).isoformat())
        with mock.patch.object(youtube, "VERIFY_WAIT", 0):
            pipeline._publish(run, test=False)
        body = self.g.inserted[-1]
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertNotIn("publishAt", body["status"])
        self.assertIn("sudah dipakai video run", ctx.log_tail())


if __name__ == "__main__":
    unittest.main()
