"""Channel Otomatis, Fase I-akhir (aktivasi): perbaikan dari dry-run ujung ke ujung dan audit kesiapan.
- QC yang sudah gagal tidak membuat percobaan ulang run harian gagal di tempat yang sama (riset diulang).
- Permintaan perbaikan isi ke AI yang gagal tidak membuang jawaban pertama yang sudah sesuai skema.
- Batas bawah jumlah kata memberi cadangan durasi; perbaikan diarahkan ke rentang ideal.
- Langkah yang tidak berlaku (upload API nonaktif, Telegram kosong) dicatat "tidak dijalankan", bukan "selesai".
- Jadwal: run terputus dilanjutkan begitu Nero siap; run yang melewati tengah malam tetap tercatat.
- Bukti run harian dari run.log (riwayat 7 hari, jenis run), dan saran jam tayang untuk unggah manual.
Tanpa jaringan."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from tests.util import ROOT  # noqa: F401
from tests.test_channel_core import ChannelSandbox, FallbackLLM
from tests.test_channel_plan import PipelinePlan
from tests.test_channel_schedule import ScheduleSandbox

import app
from features.channel import __main__ as cli
from features.channel import config, jobs, llm, pipeline, runctx, runs, scheduler, writer
from features.channel.schemas import HistoryEntry, Ping, Research, Script, YouTubeResult
from local_ai import LocalAIError

WIB = ZoneInfo("Asia/Jakarta")
BASE = "http://127.0.0.1:7860"
BAD_QC = {"score": 9, "issues": [{"type": "policy", "detail": "nasihat medis"}]}


def draft3():
    from tests.test_channel_plan import cand, draft
    return draft(cand("Gurita tiga jantung", [1], 30), cand("Paus biru", [3], 25), cand("Bintang laut", [2], 20))


def at(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=WIB)


# --------------------------------------------------------------------------
class QCGagalTidakMenempel(PipelinePlan):
    def real(self, fake, **kw):
        config.save_config({"enabled": True})
        return self.run_plan(fake, dry_run=False, **kw)

    def chosen(self):
        with open(self.run_dir("research.json"), encoding="utf-8") as f:
            return Research.model_validate(json.load(f)).chosen.topic

    def test_percobaan_ulang_run_harian_mencari_topik_baru(self):
        code, _ = self.run_plan(self.fake(qc=BAD_QC))                  # mis. uji coba pagi ini gagal QC
        self.assertEqual(code, 1)
        self.assertEqual(self.chosen(), "Gurita tiga jantung")
        fake = self.fake()
        code, out = self.real(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("riset diulang untuk topik baru", out)
        self.assertEqual(fake.names()[:2], ["research_queries", "research"])
        self.assertEqual(self.chosen(), "Paus biru")                    # topik yang gagal dihindari
        with open(self.run_dir("qc.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["pass"])

    def test_topik_gagal_hari_ini_tidak_dipilih_lagi(self):
        self.run_plan(self.fake(qc=BAD_QC))                             # A (Gurita) gagal QC
        fake = self.fake(qc=BAD_QC)
        fake.answers["research"] = [draft3()]
        code, out = self.real(fake)                                     # B (Paus) juga gagal QC
        self.assertEqual(code, 1)
        self.assertEqual(self.chosen(), "Paus biru")
        fake = self.fake()
        fake.answers["research"] = [draft3()]
        code, out = self.real(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.chosen(), "Bintang laut")                  # bukan kembali ke Gurita

    def test_qc_usang_tidak_mengulang_riset(self):
        self.run_plan(self.fake(qc=BAD_QC))
        runctx.RunContext(date(2026, 11, 12), echo=None).set_fingerprint("qc", "naskah-lain")
        fake = self.fake()
        code, out = self.real(fake)
        self.assertEqual(code, 0, out)
        self.assertNotIn("riset diulang", out)
        self.assertEqual(fake.names()[0], "qc")                          # QC usang diperiksa ulang apa adanya

    def test_run_tanpa_langkah_riset_tidak_menjanjikan_topik_baru(self):
        self.run_plan(self.fake(qc=BAD_QC))
        code, out = self.real(self.fake(), only="qc")
        self.assertEqual(code, 1)
        self.assertNotIn("riset diulang", out)

    def test_uji_coba_tetap_berhenti(self):
        self.run_plan(self.fake(qc=BAD_QC))
        code, out = self.run_plan(self.fake())
        self.assertEqual(code, 1)
        self.assertNotIn("riset diulang", out)
        self.assertIn("--force script", out)

    def test_force_pengguna_tidak_diubah(self):
        self.run_plan(self.fake(qc=BAD_QC))
        fake = self.fake()
        code, out = self.real(fake, force=["script"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("riset diulang", out)
        self.assertEqual(fake.names(), ["script", "qc", "seo"])          # topik sama, naskah ditulis ulang
        self.assertEqual(self.chosen(), "Gurita tiga jantung")

    def test_video_yang_sudah_di_youtube_tidak_diganti(self):
        self.run_plan(self.fake(qc=BAD_QC))
        ctx = runctx.RunContext(date(2026, 11, 12), echo=None)
        ctx.save("youtube", YouTubeResult(video_id="abc", url="https://youtu.be/abc"))
        code, out = self.real(self.fake())
        self.assertEqual(code, 1)
        self.assertNotIn("riset diulang", out)

    def test_qc_lulus_tidak_mengulang_apa_pun(self):
        self.run_plan(self.fake())
        fake = self.fake()
        code, out = self.real(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.calls, [])


# --------------------------------------------------------------------------
class PerbaikanAIGagal(FallbackLLM):
    def check(self, p):
        return ["pesan kurang lengkap"] if p.message == "pertama" else []

    def test_timeout_saat_perbaikan_memakai_jawaban_pertama(self):
        slow = LocalAIError("Gemini terlalu lama menjawab.")
        answers = iter(['{"ok": true, "message": "pertama"}', slow, slow])      # utama (+perbaikan), lalu ringan

        def fake(inputs, **kw):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a

        logs, calls = [], []
        with mock.patch("gemini_ai.interact", side_effect=fake) as gem, \
                mock.patch("local_ai.openai_chat_json", side_effect=LocalAIError("groq mati")) as groq:
            got = llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, log=logs.append,
                                    check=self.check, calls=calls)
        self.assertEqual(got.message, "pertama")
        self.assertEqual(groq.call_count, 1)                             # provider lain dicoba dulu
        self.assertEqual(gem.call_count, 3)                              # gemini utama x2, lalu gemini ringan
        self.assertTrue(any("provider lain juga gagal; jawaban pertama gemini/gemini-utama" in m for m in logs), logs)
        self.assertTrue(calls[0].ok)
        self.assertIn(("gemini", "gemini-utama"), llm._cooldown)         # provider yang macet tetap diistirahatkan

    def test_provider_lain_yang_lengkap_lebih_dulu(self):
        answers = iter(['{"ok": true, "message": "pertama"}', LocalAIError("Gemini terlalu lama menjawab.")])

        def fake(inputs, **kw):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a

        with mock.patch("gemini_ai.interact", side_effect=fake), \
                mock.patch("local_ai.openai_chat_json", return_value={"ok": True, "message": "groq lengkap"}):
            got = llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, log=lambda m: None,
                                    check=self.check)
        self.assertEqual(got.message, "groq lengkap")                   # mis. QC lengkap dari provider berikutnya

    def test_jawaban_perbaikan_rusak_memakai_jawaban_pertama(self):
        answers = iter(['{"ok": true, "message": "pertama"}', '{"message": 1}', '{"message": 2}'])

        def fake(inputs, model, **kw):
            if model != "gemini-utama":
                raise LocalAIError("Gemini terlalu lama menjawab.")
            return next(answers)

        with mock.patch("gemini_ai.interact", side_effect=fake), \
                mock.patch("local_ai.openai_chat_json", side_effect=LocalAIError("groq mati")):
            calls = []
            got = llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, log=lambda m: None,
                                    check=self.check, calls=calls)
        self.assertEqual(got.message, "pertama")
        self.assertTrue(calls[0].ok)
        self.assertFalse(calls[1].ok)
        self.assertNotIn(("gemini", "gemini-utama"), llm._cooldown)

    def test_perbaikan_berhasil_tetap_dipakai(self):
        answers = iter(['{"ok": true, "message": "pertama"}', '{"ok": true, "message": "kedua"}'])
        with mock.patch("gemini_ai.interact", side_effect=lambda inputs, **kw: next(answers)):
            got = llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, log=lambda m: None,
                                    check=self.check)
        self.assertEqual(got.message, "kedua")

    def test_tanpa_jawaban_layak_tetap_gagal(self):
        with mock.patch("gemini_ai.interact", side_effect=LocalAIError("Gemini terlalu lama menjawab.")), \
                mock.patch("local_ai.openai_chat_json", side_effect=LocalAIError("groq mati")), \
                self.assertRaises(llm.LLMError):
            llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, log=lambda m: None,
                              check=self.check)


# --------------------------------------------------------------------------
class JumlahKata(unittest.TestCase):
    def test_batas_bawah_memberi_cadangan_durasi(self):
        cfg = config.ChannelConfig()
        lo, _ = config.words_limits(cfg)
        # 13 Nov 2026: 112 kata Ardi (+5%) = video 41,3 dtk (< 42 dtk, gagal validasi) -> naskah ditulis ulang
        self.assertGreater(lo, 112)
        self.assertLess(lo, config.words_range(cfg)[0])

    def test_perbaikan_menuju_rentang_ideal(self):
        from tests.test_channel_core import script_data
        cfg = config.ChannelConfig()
        script = Script.model_validate(script_data(word_count=100))
        res = Research.model_validate({"date": "2026-11-13", "candidates": [{
            "topic": "Gurita", "angle": "a", "sources": [{"title": "x", "url": "https://id.wikipedia.org/wiki/Gurita"}]}]})
        issues = [i for i in writer.script_issues(script, cfg, res, script.format, []) if i.type == "length"]
        ideal = config.words_range(cfg)
        self.assertEqual(len(issues), 1)
        self.assertIn(f"{ideal[0]}–{ideal[1]} kata", issues[0].fix)


# --------------------------------------------------------------------------
class LangkahTidakDijalankan(ChannelSandbox):
    def make(self):
        config.save_config({"channel": {"niche": "sains"}})
        cfg = pipeline.prepare_config()
        return pipeline.Run(runctx.RunContext(date(2026, 11, 13), echo=None), cfg, {}, dry_run=False)

    def test_dibedakan_dari_dipakai_ulang(self):
        run = self.make()

        def made(r, force):
            return True

        def reused(r, force):
            r.log("lewati render: hasil sebelumnya masih valid")
            return False

        def idle(r, force):
            r.log("youtube: upload lewat API nonaktif (youtube.api_upload = false)")
            return False

        funcs = {"tts": made, "render": reused, "publish_youtube": idle}
        with mock.patch.dict(pipeline.STEP_FUNCS, funcs):
            pipeline._run_steps(run, ["tts", "render", "publish_youtube"], [], None)
        self.assertEqual((run.ran, run.skipped, run.idle), (["tts"], ["render"], ["publish_youtube"]))
        with open(run.ctx.path("run.log"), encoding="utf-8") as f:
            self.assertIn("tidak dijalankan: publish_youtube", f.read())

    def test_riwayat_yang_dilengkapi_bukan_tidak_dijalankan(self):
        run = self.make()
        run.ctx.save("youtube", YouTubeResult(video_id="abc", url="https://youtu.be/abc"))
        with mock.patch.object(pipeline, "_record_upload") as rec:
            self.assertTrue(pipeline.step_state_update(run, False))
        rec.assert_called_once()
        from features.channel import state
        state.upsert_history(HistoryEntry(date="2026-11-13", topic="x", video_id="abc"))
        run.reused = False
        self.assertFalse(pipeline.step_state_update(run, False))
        self.assertTrue(run.reused)                                     # sudah tercatat = dipakai ulang

    def test_halaman_menampilkan_tidak_dijalankan(self):
        job = jobs.RunJob(date(2026, 11, 13), "daily")
        for step, line in (("render", "lewati render: hasil sebelumnya masih valid"),
                           ("notify", "telegram: PERINGATAN token bot / chat ID belum diisi; paket tidak dikirim"),
                           ("notify", "tidak dijalankan: notify"), ("publish_youtube", "")):
            if line:
                job.echo(f"[channel] 10:00:00 {line}")
            job.on_progress(step, 1.0)
        self.assertEqual(job.steps["render"]["state"], "dipakai")
        self.assertEqual(job.steps["notify"]["state"], "lewat")
        self.assertEqual(job.steps["publish_youtube"]["state"], "selesai")


# --------------------------------------------------------------------------
class JadwalAktivasi(ScheduleSandbox):
    def test_run_terputus_dilanjutkan_begitu_nero_siap(self):
        self.enable(run_time="05:00")
        self.sch.tick(at(13, 5))
        jobs._job = None                                               # Nero ditutup di tengah run lalu dibuka
        sch2 = scheduler.Scheduler()
        self.assertEqual(sch2.tick(at(13, 9), allow_start=False), "masa_tunggu")   # ±2 menit untuk internet
        self.assertIn("terputus", self.st()["note"])
        self.assertEqual(sch2.tick(at(13, 9, 2)), "mulai")             # bukan 1 jam lagi
        self.assertEqual(self.st()["attempts"], 2)                    # tetap dihitung sebagai percobaan

    def test_terputus_berulang_tetap_dibatasi(self):
        self.enable(run_time="05:00")
        for k in range(scheduler.MAX_ATTEMPTS):
            self.assertEqual(scheduler.Scheduler().tick(at(13, 5 + k)), "mulai")
            jobs._job = None
        self.assertEqual(scheduler.Scheduler().tick(at(13, 9)), "menyerah")

    def test_run_kemarin_lewat_tengah_malam_tetap_tercatat(self):
        self.enable(run_time="05:00")
        scheduler._save({"day": "2026-11-12", "state": "menunggu", "attempts": 0})
        self.assertEqual(self.sch.tick(at(12, 23, 50)), "mulai")       # Nero baru dibuka larut malam
        job = self.started[-1]
        self.assertEqual(self.sch.tick(at(13, 0, 5)), "berjalan")       # lewat tengah malam: catatan tidak diganti
        self.assertEqual(self.st()["day"], "2026-11-12")
        st = scheduler.status(now=at(13, 0, 6))
        self.assertEqual(st["today"]["day"], "2026-11-12")
        self.assertEqual(st["today"]["state"], "berjalan")
        self.assertEqual(st["next_run"][:16], "2026-11-13T05:00")
        job.finish(0)
        self.assertEqual(self.sch.tick(at(13, 0, 20)), "menunggu")      # hasil kemarin dicatat, lalu hari baru
        self.assertEqual(self.st()["day"], "2026-11-13")
        self.assertEqual(self.sch.tick(at(13, 5)), "mulai")

    def test_kemarin_gagal_lewat_tengah_malam_tidak_diulang(self):
        self.enable(run_time="05:00")
        scheduler._save({"day": "2026-11-12", "state": "menunggu", "attempts": 0})
        self.sch.tick(at(12, 23, 50))
        self.started[-1].finish(pipeline.EXIT_FAILED, error="AI penuh")
        self.sch._settle(self.st(), at(13, 0, 5), config.load_config())
        self.assertEqual(self.st()["state"], "menyerah")               # hari lalu tidak dikejar
        self.assertEqual(self.sch.tick(at(13, 0, 6)), "menunggu")


class SimpanPengaturan(ScheduleSandbox):
    def test_jadwal_dibangunkan_setelah_key_tersimpan(self):
        seen = []

        def kick():
            seen.append(config.key_status()["telegram_chat_id"])

        with mock.patch.object(scheduler.get(), "kick", side_effect=kick):
            r = app.app.test_client().post("/api/channel/settings", base_url=BASE, json={
                "config": {"enabled": True}, "keys": {"telegram_chat_id": "12345"}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(seen, [True])

    def test_jadwal_tetap_dibangunkan_bila_key_ditolak(self):
        with mock.patch.object(scheduler.get(), "kick") as kick, \
                mock.patch.object(config, "save_keys", side_effect=config.ConfigError("key rusak")):
            r = app.app.test_client().post("/api/channel/settings", base_url=BASE, json={
                "config": {"enabled": True}, "keys": {"telegram_chat_id": "x"}})
        self.assertEqual(r.status_code, 400)
        kick.assert_called_once()                                      # pengaturan sudah tersimpan


# --------------------------------------------------------------------------
class BuktiRunHarian(ChannelSandbox):
    def log(self, day, text):
        ctx = runctx.RunContext(day, echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        with open(ctx.path("run.log"), "w", encoding="utf-8") as f:
            f.write(text)
        return ctx

    def test_percobaan_dari_run_log(self):
        d = date(2026, 11, 14)
        self.log(d, "05:00:01 mulai run 2026-11-14 (uji coba), niche: x\n"
                    "05:03:00 selesai. dijalankan: research; dipakai ulang: -\n"
                    "05:17:05 mulai run 2026-11-14 (sungguhan), niche: x\n"
                    "05:18:00 GAGAL di langkah seo: Semua provider AI gagal\n"
                    "Traceback (most recent call last):\n  x\n"
                    "06:18:05 mulai run 2026-11-14 (sungguhan), niche: x\n"
                    "06:19:00 lewati research: hasil sebelumnya masih valid\n"
                    "07:00:00 mulai run 2026-11-14 (sungguhan), niche: x\n"
                    "07:05:00 selesai. dijalankan: seo, tts, visuals, render, manual_pack; dipakai ulang: research, "
                    "script, qc; tidak dijalankan: publish_youtube, notify\n")
        got = runs.daily_attempts(d)
        self.assertEqual([a["hasil"] for a in got], ["gagal", "terputus", "selesai"])
        self.assertEqual(got[0]["langkah"], "seo")
        self.assertEqual(got[2]["dijalankan"], ["seo", "tts", "visuals", "render", "manual_pack"])
        self.assertEqual(runs.run_kind(runctx.RunContext(d, echo=None)), "sungguhan")
        self.assertEqual(runs.run_summary(d)["kind"], "sungguhan")

    def test_run_yang_sedang_berjalan(self):
        d = date(2026, 11, 14)
        ctx = self.log(d, "05:17:05 mulai run 2026-11-14 (sungguhan), niche: x\n")
        with ctx:
            self.assertEqual(runs.daily_attempts(d)[-1]["hasil"], "berjalan")
        self.assertEqual(runs.daily_attempts(d)[-1]["hasil"], "terputus")

    def test_uji_coba_sesudah_run_harian(self):
        d = date(2026, 11, 14)
        real = ("05:17:05 mulai run 2026-11-14 (sungguhan), niche: x\n"
                "05:25:00 selesai. dijalankan: research, script, qc, seo, tts, visuals, render; dipakai ulang: -\n")
        ctx = self.log(d, real + "10:00:00 mulai run 2026-11-14 (uji coba), niche: x\n"
                                 "10:00:30 selesai. dijalankan: manual_pack; dipakai ulang: research, script\n")
        self.assertEqual(runs.run_kind(ctx), "sungguhan")               # hanya memakai ulang: video run harian
        self.log(d, real + "10:00:00 mulai run 2026-11-14 (uji coba), niche: x\n"
                           "10:04:00 selesai. dijalankan: research, script, qc; dipakai ulang: -\n")
        self.assertEqual(runs.run_kind(ctx), "uji coba")                # isi tanggal itu diganti uji coba
        self.log(d, real + "10:00:00 mulai run 2026-11-14 (uji coba), niche: x\n10:01:00 GAGAL di langkah script: x\n")
        self.assertEqual(runs.run_kind(ctx), "uji coba")

    def test_unggah_uji_bukan_bagian_run_terputus(self):
        d = date(2026, 11, 14)
        self.log(d, "05:17:05 mulai run 2026-11-14 (sungguhan), niche: x\n"
                    "05:18:00 lewati research: hasil sebelumnya masih valid\n"
                    "19:00:00 mulai unggah uji 2026-11-14 (private)\n"
                    "19:03:00 selesai. dijalankan: publish_youtube; dipakai ulang: - (unggah uji)\n"
                    "19:10:00 selesai. dijalankan: -; dipakai ulang: notify (kirim uji ke Telegram)\n")
        got = runs.daily_attempts(d)
        self.assertEqual([a["hasil"] for a in got], ["terputus"])

    def test_uji_coba_bukan_run_harian(self):
        d = date(2026, 11, 14)
        self.log(d, "09:00:00 mulai run 2026-11-14 (uji coba), niche: x\n09:05:00 selesai. dijalankan: research; "
                    "dipakai ulang: -\n")
        self.assertEqual(runs.daily_attempts(d), [])
        self.assertEqual(runs.run_summary(d)["kind"], "uji coba")

    def test_status_jadwal_menampilkan_tujuh_hari(self):
        self.log(date(2026, 11, 13), "05:17:00 mulai run 2026-11-13 (sungguhan), niche: x\n"
                                     "05:20:00 selesai. dijalankan: -; dipakai ulang: research, script\n")
        self.log(date(2026, 11, 14), "05:17:00 mulai run 2026-11-14 (sungguhan), niche: x\n"
                                     "05:25:00 selesai. dijalankan: research, script, qc, seo, tts, visuals, render; "
                                     "dipakai ulang: -\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli._print_daily_history(date(2026, 11, 15))
        out = buf.getvalue()
        self.assertIn("2026-11-09  -", out)
        self.assertIn("2026-11-13  selesai  (1 percobaan, mulai 05:17:00, selesai 05:20:00)  dibuat hari itu: - "
                      "(dipakai ulang dari uji coba atau dari percobaan yang terputus)", out)
        self.assertIn("2026-11-14  selesai", out)
        self.assertIn("dibuat hari itu: research, script, qc, seo, tts, visuals, render", out)
        self.assertIn("2026-11-15  -", out)

    def test_saran_tayang_untuk_unggah_manual(self):
        d = date(2026, 11, 14)
        self.log(d, "05:17:00 mulai run 2026-11-14 (sungguhan), niche: x\n")
        now = datetime(2026, 11, 14, 9, 0, tzinfo=WIB)
        with mock.patch("features.channel.timeutil.datetime", wraps=datetime) as dt:
            dt.now.return_value = now
            hint = runs.run_detail(d)["manual_publish"]
            self.assertEqual(hint, {"publish_time": "18:00", "publish_at": "2026-11-14T18:00:00+07:00",
                                    "made_for_kids": False, "api_upload": False})
            dt.now.return_value = datetime(2026, 11, 14, 19, 0, tzinfo=WIB)   # jam tayang tanggal run sudah lewat
            self.assertEqual(runs.run_detail(d)["manual_publish"]["publish_at"], "2026-11-15T18:00:00+07:00")


class RingkasanAkhirRun(ChannelSandbox):
    def summary(self, funcs):
        config.save_config({"channel": {"niche": "sains"}})
        out = []
        steps = pipeline.resolve_steps("plan")
        funcs = {s: funcs.get(s, lambda r, force: True) for s in steps}    # mis. analyst & paket manual
        with mock.patch.dict(pipeline.STEP_FUNCS, funcs):
            code = pipeline.run_daily(date(2026, 11, 13), dry_run=True, only="plan", echo=out.append)
        self.assertEqual(code, 0, out)
        return out[-1]

    def test_baris_selesai_memisahkan_yang_tidak_dijalankan(self):
        def made(r, force):
            return True

        def reused(r, force):
            r.log("lewati script: hasil sebelumnya masih valid")
            return False

        def idle(r, force):
            r.log("contoh: tidak berlaku untuk run ini")
            return False

        last = self.summary({"research": made, "script": reused, "qc": idle, "seo": made})
        self.assertTrue(last.endswith("selesai. dijalankan: research, seo, manual_pack; dipakai ulang: script; "
                                      "tidak dijalankan: qc"), last)
        last = self.summary({"research": made, "script": reused, "qc": reused, "seo": reused})
        self.assertTrue(last.endswith("selesai. dijalankan: research, manual_pack; dipakai ulang: script, qc, seo"), last)


# --------------------------------------------------------------------------
class JawabanTakTerpakai(FallbackLLM):
    """15 Nov 2026: Gemini ringan dua kali menjawab riset tanpa kandidat bersumber; jawaban itu diterima "dengan
    catatan" lalu riset gagal, padahal AI lain tersedia."""

    def check(self, p):
        return [] if p.message == "lengkap" else [f"isi '{p.message}' belum lengkap"]

    def usable(self, p):
        return p.message != "kosong"

    def ask(self, gemini, groq, **kw):
        calls, logs = [], []
        with mock.patch("gemini_ai.interact", side_effect=gemini), \
                mock.patch("local_ai.openai_chat_json", side_effect=groq):
            got = llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, log=logs.append,
                                    check=self.check, usable=self.usable, calls=calls, **kw)
        return got, calls, logs

    def test_tetap_kosong_setelah_perbaikan_pindah_ke_ai_lain(self):
        asked = []

        def gemini(inputs, **kw):
            asked.append(inputs[0]["text"])
            return '{"ok": true, "message": "kosong"}'

        got, calls, logs = self.ask(gemini, lambda *a, **kw: {"ok": True, "message": "lengkap"})
        self.assertEqual(got.message, "lengkap")
        self.assertEqual([c.provider for c in calls], ["gemini", "groq"])
        self.assertEqual(len(asked), 2)                                  # jawaban + satu permintaan perbaikan
        self.assertIn("tidak bisa dipakai", calls[0].error)
        self.assertNotIn(("gemini", "gemini-utama"), llm._cooldown)      # servernya menjawab: tanpa cooldown
        self.assertFalse(any("diterima dengan catatan" in m for m in logs))

    def test_semua_kosong_tetap_gagal(self):
        with self.assertRaises(llm.LLMError) as e:
            self.ask(lambda inputs, **kw: '{"ok": true, "message": "kosong"}',
                     lambda *a, **kw: {"ok": True, "message": "kosong"})
        self.assertIn("tidak bisa dipakai", str(e.exception))

    def test_jawaban_kosong_bukan_cadangan(self):
        answers = iter(['{"ok": true, "message": "kosong"}', LocalAIError("Gemini terlalu lama menjawab.")])

        def gemini(inputs, model, **kw):
            if model != "gemini-utama":
                raise LocalAIError("Gemini terlalu lama menjawab.")
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a

        with self.assertRaises(llm.LLMError):                          # bukan jawaban "kosong" dengan catatan
            self.ask(gemini, LocalAIError("groq mati"))

    def test_kurang_sempurna_tetap_diterima(self):
        got, calls, logs = self.ask(lambda inputs, **kw: '{"ok": true, "message": "sebagian"}',
                                    lambda *a, **kw: {"ok": True, "message": "lengkap"})
        self.assertEqual(got.message, "sebagian")                         # masih bisa dipakai: tidak pindah
        self.assertEqual([c.provider for c in calls], ["gemini"])
        self.assertTrue(any("diterima dengan catatan" in m for m in logs))


class RisetMemeriksaKandidatBersumber(ChannelSandbox):
    def test_riset_tanpa_kandidat_bersumber_tidak_terpakai(self):
        from features.channel import research
        from features.channel.schemas import ResearchDraft
        from tests.test_channel_plan import cand, cfg_with_genre, draft, materials
        seen = {}

        def fake(name, variables, schema, temperature, *, check=None, usable=None, **kw):
            if name == "research_queries":
                return schema.model_validate({"queries": [{"id": "gurita"}, {"id": "paus"}]})
            seen["check"], seen["usable"] = check, usable
            return schema.model_validate(draft(cand("Gurita tiga jantung", [1])))

        with mock.patch.object(llm, "generate_json", fake), \
                mock.patch.object(research, "wikipedia_materials", return_value=materials()), \
                mock.patch.object(research, "enrich", side_effect=lambda c, log=None: c):
            research.run(cfg_with_genre(), date(2026, 11, 15), log=lambda m: None)
        unsourced = ResearchDraft.model_validate(draft(cand("Tanpa sumber", [99]), cand("Juga tanpa", [0])))
        one = ResearchDraft.model_validate(draft(cand("Gurita", [1]), cand("Tanpa sumber", [99])))
        self.assertFalse(seen["usable"](unsourced))
        self.assertTrue(seen["usable"](one))                             # kurang dari 3 tetap bisa dipakai
        self.assertTrue(seen["check"](one))                              # tetapi diminta perbaikan dulu


if __name__ == "__main__":
    unittest.main()
