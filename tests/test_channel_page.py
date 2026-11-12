"""Channel Otomatis, Fase I-4: rute halaman /channel (pengaturan, uji coba sebagai pekerjaan latar, progres per
langkah, daftar & isi run, berkas run, kartu Beranda). Pipeline di-mock; tanpa jaringan."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import json
import os
import threading
import time
from datetime import date
from unittest import mock
import unittest

from tests.util import ROOT  # noqa: F401
from tests.test_channel_core import ChannelSandbox
from tests.test_channel_plan import SEO, FakeLLM, cand, draft, long_script, materials, qc_answer

import app
from features.channel import cancel, config, jobs, llm, pipeline, research, runctx, runs, state, timeutil
from features.channel.schemas import QC, Metadata, RenderInfo, Research, Script

TODAY = timeutil.today_local("Asia/Jakarta")

BASE = "http://127.0.0.1:7860"
DAY = date(2026, 11, 12)


class FakeRun:
    """Pengganti pipeline.run_daily: melaporkan progres seperti aslinya, bisa ditahan & dibatalkan."""

    def __init__(self, code=0, fail_step=None, hold=False):
        self.code, self.fail_step, self.hold = code, fail_step, hold
        self.release = threading.Event()
        self.started = threading.Event()
        self.kwargs = None

    def __call__(self, day, *, dry_run, only, force, genre, echo, cancel, on_progress):
        self.kwargs = {"day": day, "dry_run": dry_run, "only": only, "force": force, "genre": genre}
        echo(f"[channel] 10:00:00 mulai run {day.isoformat()} (uji coba), niche: x")
        on_progress("research", 0.0)
        echo("[channel] 10:00:01 lewati research: hasil sebelumnya masih valid")
        on_progress("research", 1.0)
        on_progress("script", 0.0)
        on_progress("script", 0.5)
        self.started.set()
        if self.hold:
            while not self.release.wait(0.02):
                if cancel.is_set():
                    echo("[channel] 10:00:02 DIBATALKAN di langkah script")
                    return pipeline.EXIT_FAILED
        if self.fail_step:
            echo(f"[channel] 10:00:03 GAGAL di langkah {self.fail_step}: sesuatu rusak")
            return pipeline.EXIT_FAILED
        for s in jobs.steps_for(only)[1:]:
            on_progress(s, 1.0)
        echo("[channel] 10:00:04 selesai. dijalankan: script; dipakai ulang: research")
        return self.code


class PageSandbox(ChannelSandbox):
    def setUp(self):
        super().setUp()
        jobs._job = None
        self.c = app.app.test_client()
        config.save_config({"channel": {"niche": "fakta sains", "audience": "pelajar"}})

    def tearDown(self):
        job = jobs.current()
        if job and job.thread:
            job.cancel.set()
            job.thread.join(5)
        jobs._job = None
        super().tearDown()

    def get(self, url, **kw):
        return self.c.get(url, base_url=BASE, **kw)

    def post(self, url, body=None, **kw):
        return self.c.post(url, json=body or {}, base_url=BASE, **kw)

    def wait_job(self, status, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            job = jobs.current()
            if job and job.status == status:
                return job.snapshot()
            time.sleep(0.02)
        self.fail(f"pekerjaan tidak mencapai status {status}: {jobs.current() and jobs.current().snapshot()}")

    def make_run(self, day=DAY, *, render_valid=True, fail_line=None, video=True):
        ctx = runctx.RunContext(day, echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        script = Script.model_validate(long_script())
        res = Research.model_validate({"date": day.isoformat(), "candidates": [{
            "topic": "Gurita tiga jantung", "angle": "a", "sources": [{"title": "Gurita", "url": "https://x/g"}]}]})
        ctx.save("research", res)
        ctx.save("script", script)
        ctx.save("qc", QC.model_validate({"score": 8, "checks": [], "issues": []}))
        ctx.save("metadata", Metadata(title="Judul uji", description="Deskripsi", tags=["a"], hashtags=["#Shorts"]))
        ctx.save("render", RenderInfo(width=1080, height=1920, fps=30, duration=50, has_audio=True,
                                      valid=render_valid, needs_review=True, warnings=["hanya 1 jenis visual"]))
        for step in ("research", "script", "qc", "seo", "render"):
            ctx.set_fingerprint(step, "x")
        for step in ("seo", "render"):
            ctx.set_fingerprint(f"{step}_src", runctx.sha(script))
        if video:
            with open(ctx.path("final.mp4"), "wb") as f:
                f.write(b"0123456789" * 50)
        with open(ctx.path("run.log"), "w", encoding="utf-8") as f:
            f.write(f"09:00:00 mulai run {day.isoformat()} (uji coba), niche: x\n")
            if fail_line:
                f.write(fail_line + "\nTraceback (most recent call last):\n  x\n")
            else:
                f.write("09:05:00 selesai. dijalankan: research; dipakai ulang: -\n")
        return ctx


# --------------------------------------------------------------------------
class Pengaturan(PageSandbox):
    def test_pengaturan_berisi_genre_gaya_dan_tanpa_nilai_key(self):
        config.save_keys({"pexels": "RAHASIA-PEXELS-123"})
        r = self.get("/api/channel/settings")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["keys"]["pexels"])
        self.assertNotIn("RAHASIA-PEXELS-123", r.get_data(as_text=True))
        self.assertEqual(len(data["genres"]), 10)
        self.assertIn("preset:klasik", [s["id"] for s in data["styles"]])
        self.assertEqual(data["steps"]["qc"], "Pemeriksaan fakta (QC)")

    def test_pilih_genre(self):
        r = self.post("/api/channel/settings", {"genre": "luar-angkasa"})
        self.assertEqual(r.status_code, 200)
        cfg = config.load_config()
        self.assertIn("astronomi", cfg.channel.niche)
        self.assertEqual(cfg.youtube.category_id, "28")
        # isian sendiri menimpa isian genre
        self.post("/api/channel/settings", {"genre": "luar-angkasa", "config": {"channel": {"audience": "anak SMA"}}})
        self.assertEqual(config.load_config().channel.audience, "anak SMA")
        self.assertEqual(self.post("/api/channel/settings", {"genre": "tidak-ada"}).status_code, 400)

    def test_pengaturan_tidak_valid_ditolak(self):
        r = self.post("/api/channel/settings", {"config": {"tts": {"rate": "cepat"}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("rate", r.get_json()["error"])

    def test_simpan_atomik_dan_genre_tidak_valid(self):
        before = config.load_config().channel.niche
        r = self.post("/api/channel/settings", {"genre": "luar-angkasa", "config": {"tts": {"rate": "cepat"}},
                                                "keys": {"pexels": "abc"}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(config.load_config().channel.niche, before)       # genre tidak ikut tersimpan
        self.assertFalse(config.key_status()["pexels"])                     # key juga tidak
        for bad in (["luar-angkasa"], {"a": 1}, 5):
            self.assertEqual(self.post("/api/channel/settings", {"genre": bad}).status_code, 400, bad)

    def test_volume_musik_dibatasi(self):
        for v in (0, -22, -60):
            self.assertEqual(self.post("/api/channel/settings", {"config": {"video": {"music_volume_db": v}}})
                             .status_code, 200, v)
        for v in (5, -80):
            self.assertEqual(self.post("/api/channel/settings", {"config": {"video": {"music_volume_db": v}}})
                             .status_code, 400, v)

    def test_key_dari_environment_ditandai(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "dari-env"}):
            data = self.get("/api/channel/settings").get_json()
        self.assertTrue(data["keys"]["groq"])
        self.assertTrue(data["keys_env"]["groq"])
        self.assertFalse(data["keys_env"]["pexels"])
        self.assertNotIn("dari-env", json.dumps(data))

    def test_permintaan_dari_situs_lain_ditolak(self):
        r = self.c.post("/api/channel/run", json={}, base_url=BASE, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)


class UjiCoba(PageSandbox):
    def test_jalankan_progres_selesai(self):
        fake = FakeRun()
        with mock.patch.object(pipeline, "run_daily", fake):
            r = self.post("/api/channel/run", {"mode": "video", "date": TODAY.isoformat()})
            self.assertEqual(r.status_code, 200, r.get_json())
            snap = self.wait_job("selesai")
        self.assertEqual(fake.kwargs, {"day": TODAY, "dry_run": True, "only": "video", "force": [], "genre": None})
        steps = {s["id"]: s["state"] for s in snap["steps"]}
        self.assertEqual(steps["research"], "dipakai")
        self.assertEqual(steps["script"], "selesai")
        self.assertEqual(steps["render"], "selesai")
        self.assertEqual(list(steps), ["research", "script", "qc", "seo", "tts", "visuals", "render", "manual_pack"])
        self.assertIn("selesai. dijalankan", snap["log"][-1])
        self.assertFalse(snap["log"][0].startswith("[channel]"))

    def test_progres_terbaca_saat_berjalan_dan_tidak_bisa_dobel(self):
        fake = FakeRun(hold=True)
        with mock.patch.object(pipeline, "run_daily", fake):
            self.post("/api/channel/run", {"mode": "plan"})
            self.assertTrue(fake.started.wait(5))
            st = self.get("/api/channel/state").get_json()
            self.assertEqual(st["job"]["status"], "proses")
            self.assertEqual(st["job"]["stage"], "Menulis skrip")
            self.assertAlmostEqual(st["job"]["progress"], (1 + 0.5) / 5)     # 4 langkah rencana + paket manual
            self.assertEqual(app.app.test_client().get("/api/overview", base_url=BASE).get_json()
                             ["features"]["channel"]["busy"], 1)
            r = self.post("/api/channel/run", {"mode": "plan"})
            self.assertEqual(r.status_code, 409)
            fake.release.set()
            self.wait_job("selesai")

    def test_batal(self):
        fake = FakeRun(hold=True)
        with mock.patch.object(pipeline, "run_daily", fake):
            self.post("/api/channel/run", {"mode": "video"})
            self.assertTrue(fake.started.wait(5))
            self.assertEqual(self.post("/api/channel/run/cancel").get_json()["count"], 1)
            snap = self.wait_job("batal")
        self.assertEqual(snap["error"], "Dibatalkan.")
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}["script"], "batal")
        self.assertEqual(self.post("/api/channel/run/cancel").get_json()["count"], 0)

    def test_gagal_membawa_pesan_langkah(self):
        with mock.patch.object(pipeline, "run_daily", FakeRun(fail_step="script")):
            self.post("/api/channel/run", {"mode": "video"})
            snap = self.wait_job("gagal")
        self.assertEqual(snap["error"], "sesuatu rusak")
        steps = {s["id"]: s["state"] for s in snap["steps"]}
        self.assertEqual((steps["script"], steps["render"]), ("gagal", "lewat"))

    def test_galat_tak_terduga_tetap_dilaporkan(self):
        with mock.patch.object(pipeline, "run_daily", side_effect=ZeroDivisionError("bagi nol")):
            self.post("/api/channel/run", {"mode": "plan"})
            snap = self.wait_job("gagal")
        self.assertIn("bagi nol", snap["error"])

    def test_masukan_tidak_valid(self):
        for body, word in (({"mode": "semua"}, "Mode"), ({"force": ["terbang"]}, "tidak dikenal"),
                           ({"date": "12-11-2026"}, "YYYY-MM-DD"), ({"force": "research"}, "daftar"),
                           ({"date": "9999-12-31"}, "7 hari"), ({"date": "2026-1-1"}, "YYYY-MM-DD")):
            r = self.post("/api/channel/run", body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(word, r.get_json()["error"])
        config.save_config({"channel": {"niche": ""}})
        r = self.post("/api/channel/run", {"mode": "plan"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Genre", r.get_json()["error"])

    def test_genre_hanya_untuk_run_ini(self):
        config.save_config({"channel": {"niche": ""}})
        fake = FakeRun()
        with mock.patch.object(pipeline, "run_daily", fake):
            r = self.post("/api/channel/run", {"mode": "plan", "genre": "alam-hewan", "force": ["research"]})
            self.assertEqual(r.status_code, 200)
            self.wait_job("selesai")
        self.assertEqual((fake.kwargs["genre"], fake.kwargs["force"]), ("alam-hewan", ["research"]))
        self.assertEqual(config.load_config().channel.niche, "")


class DaftarRun(PageSandbox):
    def test_status_kesiapan_dan_daftar(self):
        self.make_run()
        self.make_run(date(2026, 11, 11), fail_line="09:01:00 GAGAL di langkah tts: edge-tts putus", video=False)
        os.makedirs(os.path.join(self.data, "channel", "runs", ".cache"), exist_ok=True)
        st = self.get("/api/channel/state").get_json()
        self.assertIsNone(st["job"])
        self.assertEqual([r["date"] for r in st["runs"]], ["2026-11-12", "2026-11-11"])
        first, second = st["runs"]
        self.assertEqual((first["title"], first["qc"]["passed"], first["render"]["needs_review"]),
                         ("Judul uji", True, True))
        self.assertTrue(first["video"].startswith("/api/channel/runs/2026-11-12/file/final.mp4"))
        self.assertFalse(first["published"])
        self.assertEqual(second["failed"], {"step": "tts", "cancelled": False, "message": "edge-tts putus"})
        ready = {r["id"]: r for r in st["ready"]}
        self.assertTrue(ready["genre"]["ok"])
        self.assertFalse(ready["gemini"]["ok"])
        self.assertTrue(ready["gemini"]["required"])
        self.assertFalse(ready["stock"]["required"])

    def test_video_yang_sudah_disalin_diputar_dari_folder_hasil(self):
        self.make_run()
        out = os.path.join(self.tmp, "output", "channel", "2026-11-12")
        os.makedirs(out)
        with open(os.path.join(out, "final.mp4"), "wb") as f:
            f.write(b"x")
        r = runs.run_summary(DAY)
        self.assertTrue(r["video"].startswith("/out/channel/2026-11-12/final.mp4?v="))
        self.assertTrue(r["published"])

    def test_isi_run(self):
        self.make_run()
        d = self.get("/api/channel/runs/2026-11-12").get_json()
        self.assertEqual(d["research"]["topic"], "Gurita tiga jantung")
        self.assertTrue(d["qc"]["pass"])                      # nama kolom sama dengan qc.json
        self.assertEqual(d["script"]["format"], "myth_vs_fact")
        self.assertEqual({s["id"]: s["state"] for s in d["steps"]}["tts"], "belum")
        self.assertEqual({s["id"]: s["state"] for s in d["steps"]}["render"], "selesai")
        self.assertNotIn("Traceback", "\n".join(d["log"]))
        self.assertEqual(self.get("/api/channel/runs/2026-11-30").status_code, 404)
        self.assertEqual(self.get("/api/channel/runs/bukan-tanggal").status_code, 404)

    def test_berkas_run_dibatasi(self):
        self.make_run()
        r = self.get("/api/channel/runs/2026-11-12/file/final.mp4", headers={"Range": "bytes=0-9"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.data, b"0123456789")
        r.close()
        for bad in ("/api/channel/runs/2026-11-12/file/run.log", "/api/channel/runs/2026-11-12/file/script.json",
                    "/api/channel/runs/2026-11-12/file/..%2F..%2Fsettings.json",
                    "/api/channel/runs/..%2F..%2F/file/final.mp4", "/api/channel/runs/2026-11-12/file/voice.mp3"):
            self.assertEqual(self.get(bad).status_code, 404, bad)
        r = self.get("/api/channel/runs/2026-11-12/file/final.mp4?dl=1")
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        r.close()

    def test_kartu_beranda(self):
        self.make_run()
        self.make_run(date(2026, 11, 11), render_valid=False)
        ch = self.get("/api/overview").get_json()["features"]["channel"]
        self.assertEqual(ch["busy"], 0)
        stats = {s["label"]: s["value"] for s in ch["stats"]}
        self.assertEqual(stats, {"Status": "Nonaktif", "Video siap": 1, "Diunggah": len(state.load_history())})

    def test_run_yang_sedang_berjalan_terdeteksi(self):
        ctx = self.make_run()
        with open(ctx.path(".lock"), "w") as f:
            f.write(str(os.getpid()))
        self.assertTrue(runs.run_summary(DAY)["running"])
        old = time.time() - runctx.LOCK_STALE_SECONDS - 60          # kunci basi: sama dengan aturan run
        os.utime(ctx.path(".lock"), (old, old))
        self.assertFalse(runs.run_summary(DAY)["running"])

    def test_video_usang_tidak_ditampilkan(self):
        """Uji coba "Rencana saja" dengan riset baru: video lama milik naskah sebelumnya tidak boleh tampil
        sebagai "Video siap" di samping judul baru."""
        ctx = self.make_run()
        self.assertTrue(runs.run_summary(DAY)["render"]["valid"])
        self.assertEqual(runs.count_ready(), 1)
        new = Script.model_validate(long_script(hook="Hook baru dari riset baru"))
        ctx.save("script", new)
        s = runs.run_summary(DAY)
        self.assertIsNone(s["render"])
        self.assertEqual((s["video"], s["title"]), ("", ""))
        self.assertTrue(s["stale_video"])
        self.assertEqual(runs.count_ready(), 0)
        d = runs.run_detail(DAY)
        self.assertIsNone(d["metadata"])
        self.assertIsNone(d["render"])
        self.assertEqual({x["id"]: x["state"] for x in d["steps"]}["render"], "belum")
        # run lama tanpa catatan asal tetap tampil (dianggap cocok)
        data = ctx.fingerprints()
        data.pop("render_src")
        data.pop("seo_src")
        state.write_json_atomic(ctx.path("inputs.json"), data)
        self.assertTrue(runs.run_summary(DAY)["render"]["valid"])

    def test_log_rusak_tidak_menjatuhkan_halaman(self):
        ctx = self.make_run()
        with open(ctx.path("run.log"), "ab") as f:
            f.write(b"09:10:00 GAGAL di langkah qc: rusak \xff\xfe\n")
        r = self.get("/api/channel/state")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["runs"][0]["failed"]["step"], "qc")
        with mock.patch.object(runs, "run_summary", side_effect=RuntimeError("folder aneh")):
            st = self.get("/api/channel/state").get_json()
        self.assertIn("Folder run rusak", st["runs"][0]["failed"]["message"])


class BerkasTerkunci(PageSandbox):
    def test_video_yang_sedang_diputar_memberi_pesan_jelas(self):
        ctx = self.make_run()
        real = os.remove

        def locked(path):
            if path.endswith("final.mp4"):
                raise PermissionError(32, "dipakai proses lain")
            return real(path)

        with mock.patch.object(os, "remove", locked), self.assertRaises(runctx.FileBusy) as e:
            ctx.invalidate("render")
        self.assertIn("sedang dibuka", str(e.exception))


class BentrokBerkas(PageSandbox):
    def test_ganti_dan_hapus_dicoba_ulang(self):
        path = os.path.join(self.tmp, "a.json")
        real_replace, real_remove = os.replace, os.remove
        calls = {"replace": 0, "remove": 0}

        def flaky_replace(a, b):
            calls["replace"] += 1
            if calls["replace"] < 3:
                raise PermissionError(5, "Access is denied")
            return real_replace(a, b)

        def flaky_remove(p):
            calls["remove"] += 1
            if calls["remove"] < 3:
                raise PermissionError(32, "dipakai proses lain")
            return real_remove(p)

        with mock.patch.object(os, "replace", flaky_replace), mock.patch.object(state, "RETRY_DELAY", 0.001):
            state.write_json_atomic(path, {"x": 1})
        self.assertEqual(state.read_json(path, None), {"x": 1})
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])
        with mock.patch.object(os, "remove", flaky_remove), mock.patch.object(state, "RETRY_DELAY", 0.001):
            state.remove_retry(path)
        self.assertFalse(os.path.exists(path))
        state.remove_retry(path)                                      # sudah tidak ada: diam saja


class Pembatalan(PageSandbox):
    def test_sleep_dan_cek_mengikuti_event_thread(self):
        ev = threading.Event()
        cancel.sleep(0)                                               # tanpa event: tidur biasa
        with cancel.cancel_scope(ev):
            cancel.check_current()
            ev.set()
            t0 = time.monotonic()
            with self.assertRaises(cancel.Cancelled):
                cancel.sleep(30)
            self.assertLess(time.monotonic() - t0, 1)
        cancel.check_current()                                        # di luar blok: tidak terpengaruh

    def run_plan(self, fake, ev):
        out = []
        with mock.patch.object(llm, "generate_json", fake), \
                mock.patch.object(research, "wikipedia_materials", return_value=materials()), \
                mock.patch.object(research, "enrich", side_effect=lambda c, log=None: c):
            code = pipeline.run_daily(DAY, echo=out.append, dry_run=True, only="plan", genre="fakta-sains",
                                      cancel=ev)
        return code, "\n".join(out)

    def test_batal_saat_langkah_terakhir_bukan_selesai(self):
        ev = threading.Event()

        def seo(v):
            ev.set()                                                  # dibatalkan di tengah langkah SEO
            return SEO

        fake = FakeLLM(research_queries={"queries": [{"id": "gurita", "en": "octopus"}, {"id": "paus"}]},
                       research=draft(cand("Gurita tiga jantung", [1], 30)), dedupe={"duplicate_ids": []},
                       script=lambda v: long_script(v["format"], v["sources"][0]["url"]), qc=qc_answer(8), seo=seo)
        code, out = self.run_plan(fake, ev)
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("DIBATALKAN", out)
        self.assertNotIn("selesai. dijalankan", out)

    def test_batal_menghentikan_panggilan_ai_berikutnya(self):
        ev = threading.Event()

        def script(v):
            ev.set()
            return long_script(v["format"], v["sources"][0]["url"])

        fake = FakeLLM(research_queries={"queries": [{"id": "gurita", "en": "octopus"}, {"id": "paus"}]},
                       research=draft(cand("Gurita tiga jantung", [1], 30)), dedupe={"duplicate_ids": []},
                       script=script, qc=qc_answer(8), seo=SEO)
        real = llm.generate_json

        def guarded(*a, **kw):
            cancel.check_current()                                    # sama dengan pemeriksaan di llm.py
            return fake(*a, **kw)

        with mock.patch.object(llm, "generate_json", guarded):
            code, _out = self.run_plan(guarded, ev)
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertNotIn("qc", [c["name"] for c in fake.calls])       # QC tidak dipanggil lagi
        self.assertIs(real, llm.generate_json)

    def test_generate_json_memeriksa_batal(self):
        ev = threading.Event()
        ev.set()
        with cancel.cancel_scope(ev), self.assertRaises(cancel.Cancelled):
            llm.generate_json("ping", {}, Metadata, 0.1)

    def test_snapshot_menandai_sedang_membatalkan(self):
        fake = FakeRun(hold=True)
        with mock.patch.object(pipeline, "run_daily", fake):
            self.post("/api/channel/run", {"mode": "plan"})
            self.assertTrue(fake.started.wait(5))
            self.assertFalse(self.get("/api/channel/state").get_json()["job"]["cancelling"])
            jobs.current().cancel.set()
            snap = jobs.current().snapshot()
            self.assertTrue(snap["cancelling"] or snap["status"] == "batal")
            self.wait_job("batal")

    def test_gagal_sebelum_batal_tetap_gagal(self):
        job = jobs.RunJob(DAY, "plan")
        job.echo("[channel] 10:00:00 GAGAL di langkah qc: kuota habis")
        job.cancel.set()
        with mock.patch.object(pipeline, "run_daily", return_value=pipeline.EXIT_FAILED):
            job._run()
        self.assertEqual((job.status, job.error), ("gagal", "kuota habis"))


class RisetUlang(PageSandbox):
    def test_riset_ulang_memilih_topik_lain(self):
        fake = FakeLLM(research_queries={"queries": [{"id": "gurita", "en": "octopus"}, {"id": "paus"}]},
                       research=draft(cand("Gurita tiga jantung", [1], 30), cand("Paus biru raksasa", [3], 20)),
                       dedupe={"duplicate_ids": []},
                       script=lambda v: long_script(v["format"], v["sources"][0]["url"]), qc=qc_answer(8), seo=SEO)

        def go(force=()):
            with mock.patch.object(llm, "generate_json", fake), \
                    mock.patch.object(research, "wikipedia_materials", return_value=materials()), \
                    mock.patch.object(research, "enrich", side_effect=lambda c, log=None: c):
                return pipeline.run_daily(DAY, echo=lambda m: None, dry_run=True, only="research",
                                          genre="fakta-sains", force=list(force))

        self.assertEqual(go(), 0)
        ctx = runctx.RunContext(DAY, echo=None)
        self.assertEqual(ctx.load("research", Research).chosen.topic, "Gurita tiga jantung")
        self.assertEqual(go(["research"]), 0)
        res = ctx.load("research", Research)
        self.assertEqual(res.chosen.topic, "Paus biru raksasa")
        self.assertTrue(any(r.startswith("Gurita tiga jantung") for r in res.rejected_as_duplicate))


class TandaLog(unittest.TestCase):
    def test_baris_lewati_menandai_dipakai_ulang(self):
        job = jobs.RunJob(DAY, "media")
        job.echo("[channel] 10:00:00 lewati words: hasil sebelumnya masih valid")
        job.on_progress("tts", 0.0)
        job.on_progress("tts", 1.0)
        job.on_progress("visuals", 0.0)
        job.on_progress("visuals", 0.5)
        snap = job.snapshot()
        self.assertEqual([s["state"] for s in snap["steps"]], ["dipakai", "proses", "antri", "antri"])
        self.assertEqual(snap["stage"], "Visual")
        self.assertAlmostEqual(snap["progress"], 1.5 / 4)                  # suara, visual, render, paket manual
        self.assertNotIn("reused", json.dumps(snap))


if __name__ == "__main__":
    unittest.main()
