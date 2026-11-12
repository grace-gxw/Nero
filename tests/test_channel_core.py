"""Channel Otomatis, Fase I-1: pengaturan & kunci, waktu tayang, skema, checkpoint, riwayat, dan fallback LLM.
Tanpa jaringan: semua panggilan AI di-mock. Folder data sementara (tidak menyentuh data/ milik pengguna)."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from tests.util import ROOT

import app  # diimpor di luar patch: app.py mendaftarkan pengaturan semua fitur
import common
from features.channel import config, llm, runctx, state, timeutil
from features.channel.schemas import QC, Metadata, Ping, Research, Script
from features.clip import api as clip
from local_ai import LocalAIError

WIB = ZoneInfo("Asia/Jakarta")


class ChannelSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nero-channel-")
        data, out = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "output")
        os.makedirs(data)
        os.makedirs(out)
        self.patches = [
            mock.patch.object(common, "DATA", data),
            mock.patch.object(common, "OUTPUT", out),
            mock.patch.object(common, "SETTINGS_PATH", os.path.join(data, "settings.json")),
            mock.patch.dict(common._feature_defaults, {"clip": clip.SETTINGS, "channel": config.SETTINGS},
                            clear=True),
            # environment pengguna tidak boleh ikut memengaruhi tes
            mock.patch.dict(os.environ, {k: "" for k in config.ENV_KEYS.values()}),
        ]
        for p in self.patches:
            p.start()
        self.data = data

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------
class PengaturanChannel(ChannelSandbox):
    def test_bawaan(self):
        cfg = config.load_config()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.approval.mode, "manual")
        self.assertFalse(cfg.youtube.api_upload)
        self.assertEqual(cfg.schedule.publish_time, "18:00")
        self.assertEqual(cfg.channel.niche, "")
        self.assertIn(cfg.tts.voice, config.VOICES)

    def test_simpan_sebagian_bertingkat(self):
        config.save_config({"schedule": {"publish_time": "19.30"}, "visuals": {"ai_image": {"steps": 6}},
                            "tts": {"voice": "id-ID-GadisNeural"}})
        cfg = config.load_config()
        self.assertEqual(cfg.schedule.publish_time, "19:30")
        self.assertEqual(cfg.schedule.timezone, "Asia/Jakarta")        # saudara di bagian yang sama tetap
        self.assertEqual(cfg.visuals.ai_image.steps, 6)
        self.assertEqual(cfg.visuals.ai_image.width, 768)              # bawaan tingkat ketiga tetap
        self.assertEqual(cfg.tts.voice, "id-ID-GadisNeural")
        # bawaan baru yang belum pernah tersimpan ikut muncul (merge bertingkat di common._merge)
        raw = common.read_settings_file()
        raw["channel"]["visuals"]["ai_image"].pop("height")
        common.write_settings_file(raw)
        self.assertEqual(config.load_config().visuals.ai_image.height, 1344)

    def test_tolak_nilai_salah(self):
        for bad in ({"schedule": {"publish_time": "25:00"}}, {"tts": {"voice": "en-US-Guy"}},
                    {"video": {"target_seconds": [60, 40]}}, {"approval": {"mode": "langsung"}},
                    {"llm": {"providers": []}}):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.save_config(bad)
        self.assertEqual(config.load_config().schedule.publish_time, "18:00")  # tidak ada yang tersimpan

    def test_daftar_diganti_utuh(self):
        config.save_config({"channel": {"banned_topics": ["a"]}})
        config.save_config({"channel": {"banned_topics": ["b", "c"]}})
        self.assertEqual(config.load_config().channel.banned_topics, ["b", "c"])

    def test_kunci_disimpan_dan_env_menimpa(self):
        config.save_keys({"pexels": " pk-1 ", "gemini": "g-1", "asing": "x"})
        raw = common.read_settings_file()
        self.assertEqual(raw["channel"]["keys"]["pexels"], "pk-1")
        self.assertEqual(raw["gemini_key"], "g-1")                     # kunci Gemini umum Nero dipakai bersama
        self.assertNotIn("asing", raw["channel"]["keys"])
        config.save_keys({"pexels": ""})                               # kosong = tetap
        self.assertEqual(config.secret("pexels"), "pk-1")
        with mock.patch.dict(os.environ, {"PEXELS_API_KEY": "dari-env"}):
            self.assertEqual(config.secret("pexels"), "dari-env")
        config.save_keys(clear=["pexels", "gemini"])
        self.assertEqual(config.secret("pexels"), "")
        self.assertEqual(common.read_settings_file()["gemini_key"], "")

    def test_kunci_tidak_dikirim_ke_halaman(self):
        config.save_keys({"telegram_token": "tg-rahasia-123", "groq": "gsk-rahasia-456"})
        c = app.app.test_client()
        r = c.get("/api/channel/settings", base_url="http://127.0.0.1:7860")
        self.assertEqual(r.status_code, 200)
        text = r.get_data(as_text=True)
        self.assertNotIn("tg-rahasia-123", text)
        self.assertNotIn("gsk-rahasia-456", text)
        self.assertTrue(r.get_json()["keys"]["telegram_token"])
        self.assertFalse(r.get_json()["keys"]["pexels"])
        r = c.post("/api/channel/settings", json={"config": {"schedule": {"publish_time": "99:99"}}},
                   base_url="http://127.0.0.1:7860")
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/channel/settings", json={"config": {"channel": {"niche": "fakta sains"}},
                                                  "keys": {"pexels": "pk-baru"}},
                   base_url="http://127.0.0.1:7860")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("pk-baru", r.get_data(as_text=True))
        self.assertEqual(config.load_config().channel.niche, "fakta sains")

    def test_pengaturan_clip_tidak_berubah(self):
        common.update_settings({"style": {"caption_size": 70}}, "clip")
        s = common.feature_settings("clip")
        self.assertEqual(s["style"]["caption_size"], 70)
        self.assertEqual(s["style"]["caption_font"], "impact")
        self.assertEqual(s["n_clips"], 5)


# --------------------------------------------------------------------------
class WaktuTayang(unittest.TestCase):
    def test_hari_ini(self):
        at = timeutil.publish_at("18:00", "Asia/Jakarta", now=datetime(2026, 11, 12, 5, 17, tzinfo=WIB))
        self.assertEqual(at, datetime(2026, 11, 12, 18, 0, tzinfo=WIB))
        self.assertEqual(timeutil.to_rfc3339_utc(at), "2026-11-12T11:00:00Z")
        self.assertEqual(timeutil.format_local(at), "12-11-2026 18.00 WIB")

    def test_sudah_lewat_jadi_besok(self):
        at = timeutil.publish_at("18:00", "Asia/Jakarta", now=datetime(2026, 11, 12, 18, 30, tzinfo=WIB))
        self.assertEqual(timeutil.to_rfc3339_utc(at), "2026-11-13T11:00:00Z")

    def test_terlalu_mepet_jadi_besok(self):
        at = timeutil.publish_at("18:00", "Asia/Jakarta", now=datetime(2026, 11, 12, 17, 50, tzinfo=WIB))
        self.assertEqual(at.date(), date(2026, 11, 13))

    def test_lewat_tengah_malam_utc(self):
        # 06.30 WIB = 23.30 UTC hari sebelumnya
        at = timeutil.publish_at("06:30", "Asia/Jakarta", now=datetime(2026, 12, 31, 1, 0, tzinfo=WIB))
        self.assertEqual(timeutil.to_rfc3339_utc(at), "2026-12-30T23:30:00Z")

    def test_jam_dari_zona_lain(self):
        now = datetime(2026, 11, 12, 1, 0, tzinfo=ZoneInfo("UTC"))  # = 08.00 WIB
        at = timeutil.publish_at("18:00", "Asia/Jakarta", now=now)
        self.assertEqual(timeutil.to_rfc3339_utc(at), "2026-11-12T11:00:00Z")

    def test_tanpa_zona_ditolak(self):
        with self.assertRaises(ValueError):
            timeutil.to_rfc3339_utc(datetime(2026, 11, 12, 18, 0))


# --------------------------------------------------------------------------
def script_data(**over):
    d = {
        "topic": "Gurita punya tiga jantung", "format": "myth_vs_fact",
        "hook": "Hewan ini punya tiga jantung, dan satu berhenti saat berenang.",
        "segments": [
            {"id": 1, "text": "Gurita memompa darah biru dengan tiga jantung.", "visual_type": "stock",
             "visual_query": "octopus underwater"},
            {"id": 2, "text": "Dua jantung untuk insang, satu untuk tubuh.", "visual_type": "motion_graphic",
             "visual_query": "octopus closeup", "graphic": {"template": "big_number", "content": "3"}},
            {"id": 3, "text": "Saat berenang, jantung utama berhenti.", "visual_type": "ai_image",
             "visual_query": "octopus swimming", "image_prompt": "an octopus swimming in deep blue ocean"},
        ],
        "cta": "Ikuti untuk fakta laut lainnya.", "word_count": 999,
        "claims": [{"claim": "gurita punya tiga jantung", "source_url": "https://id.wikipedia.org/wiki/Gurita"}],
    }
    d.update(over)
    return d


class Skema(unittest.TestCase):
    def test_skrip_valid_dan_jumlah_kata_dihitung(self):
        s = Script.model_validate(script_data())
        self.assertEqual(s.word_count, s.spoken_words())
        self.assertNotEqual(s.word_count, 999)
        self.assertEqual([i for i, _ in s.narration()], [0, 1, 2, 3, -1])

    def test_skrip_tidak_valid(self):
        segs = script_data()["segments"]
        cases = {
            "format asing": {"format": "vlog"},
            "id kembar": {"segments": [segs[0], dict(segs[1], id=1)]},
            "visual asing": {"segments": [dict(segs[0], visual_type="video_ai")]},
            "tanpa visual_query": {"segments": [dict(segs[0], visual_query="")]},
            "tanpa segmen": {"segments": []},
        }
        for name, over in cases.items():
            with self.subTest(name), self.assertRaises(ValidationError):
                Script.model_validate(script_data(**over))

    def test_visual_kosong_diperbaiki_bukan_ditolak(self):
        segs = script_data()["segments"]
        s = Script.model_validate(script_data(segments=[
            dict(segs[2], id=1, image_prompt=""),                                        # ai_image tanpa prompt
            dict(segs[1], id=2, graphic={"template": "versus", "content": ""}),          # grafik tanpa isi
            dict(segs[1], id=3, graphic=None, on_screen_text="3 jantung"),               # isi dari teks layar
            dict(segs[1], id=4, graphic={"template": "spiral", "content": "x"}),         # templat asing
        ]))
        self.assertEqual([x.visual_type for x in s.segments], ["stock", "stock", "motion_graphic", "motion_graphic"])
        self.assertEqual(s.segments[2].graphic.content, "3 jantung")
        self.assertEqual(s.segments[3].graphic.template, "title_card")
        # kolom visual wajib ada di skema JSON, supaya AI selalu mengisinya
        seg_schema = llm.json_schema_for(Script)["properties"]["segments"]["items"]
        for k in ("image_prompt", "graphic", "on_screen_text", "label_illustration"):
            self.assertIn(k, seg_schema["required"])
        self.assertTrue({"cta", "claims"} <= set(llm.json_schema_for(Script)["required"]))
        bare = script_data()
        bare.pop("claims")
        bare.pop("cta")
        self.assertEqual(Script.model_validate(bare).claims, [])     # data lama tanpa kolom tetap terbaca

    def test_aturan_lulus_qc(self):
        self.assertTrue(QC.model_validate({"pass": False, "score": 7, "issues": []}).passed)
        self.assertFalse(QC.model_validate({"pass": True, "score": 6.5, "issues": []}).passed)
        bad = QC.model_validate({"pass": True, "score": 9,
                                 "issues": [{"type": "fact", "detail": "klaim tanpa sumber"}]})
        self.assertFalse(bad.passed)
        ok = QC.model_validate({"score": 8, "issues": [{"type": "quality", "detail": "hook bisa lebih kuat"}]})
        self.assertTrue(ok.passed)
        self.assertIn('"pass": true', json.dumps(ok.model_dump(by_alias=True)))

    def test_riset_dan_metadata(self):
        r = Research.model_validate({"date": "2026-11-12", "chosen_index": 0, "candidates": [
            {"topic": "t", "angle": "a", "sources": [{"title": "s", "url": "https://x"}],
             "scores": {"relevance": 8, "freshness": 5, "novelty": 7, "learnings_fit": 6}}]})
        self.assertEqual(r.chosen.scores.total, 26)
        with self.assertRaises(ValidationError):
            Research.model_validate({"date": "2026-11-12", "chosen_index": 3, "candidates": r.model_dump()["candidates"]})
        with self.assertRaises(ValidationError):
            Metadata.model_validate({"title": "x" * 71, "description": "d"})

    def test_skema_json_sederhana(self):
        for model in (Script, QC, Research, Metadata, Ping):
            text = json.dumps(llm.json_schema_for(model))
            with self.subTest(model.__name__):
                self.assertNotIn("$ref", text)
                self.assertNotIn("$defs", text)
                self.assertNotRegex(text, r'"title": "')          # judul skema Pydantic dibuang
        meta = llm.json_schema_for(Metadata)
        self.assertIn("title", meta["properties"])                 # ... tetapi kolom bernama title tetap ada
        self.assertEqual(meta["properties"]["title"]["maxLength"], 70)
        src = llm.json_schema_for(Research)["properties"]["candidates"]["items"]["properties"]["sources"]
        self.assertIn("title", src["items"]["properties"])


# --------------------------------------------------------------------------
class Checkpoint(ChannelSandbox):
    def test_lewati_dan_paksa(self):
        ctx = runctx.RunContext(date(2026, 11, 12), echo=None)
        self.assertIsNone(ctx.cached("qc", QC))
        ctx.save("qc", QC.model_validate({"score": 8, "issues": []}))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "channel", "runs", "2026-11-12", "qc.json")))
        self.assertEqual(ctx.cached("qc", QC).score, 8)
        self.assertIsNone(ctx.cached("qc", QC, force=True))
        self.assertIn("lewati qc", ctx.log_tail())

    def test_berkas_rusak_dijalankan_ulang(self):
        ctx = runctx.RunContext(date(2026, 11, 12), echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        for content in ("{bukan json", json.dumps({"score": 99})):
            with open(ctx.path("qc.json"), "w", encoding="utf-8") as f:
                f.write(content)
            self.assertIsNone(ctx.cached("qc", QC))

    def test_kunci_run(self):
        a = runctx.RunContext(date(2026, 11, 12), echo=None)
        b = runctx.RunContext(date(2026, 11, 12), echo=None)
        with a, self.assertRaises(runctx.RunLocked):
            b.acquire()
        b.acquire()          # setelah dilepas boleh lagi
        b.release()
        # kunci sisa run lama yang macet diabaikan
        a.acquire()
        old = time.time() - runctx.LOCK_STALE_SECONDS - 10
        os.utime(a.path(".lock"), (old, old))
        b.acquire()
        b.release()

    def test_kunci_proses_mati_diabaikan(self):
        import subprocess
        import sys
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()                                          # PID ini sudah tidak berjalan
        a = runctx.RunContext(date(2026, 11, 12), echo=None)
        os.makedirs(a.dir, exist_ok=True)
        with open(a.path(".lock"), "w") as f:
            f.write(str(p.pid))
        self.assertFalse(runctx.pid_alive(p.pid))
        self.assertTrue(runctx.pid_alive(os.getpid()))
        a.acquire()                                       # tidak menunggu 3 jam
        a.release()


class Riwayat(ChannelSandbox):
    def entry(self, d, **kw):
        from features.channel.schemas import HistoryEntry
        return HistoryEntry(date=d, topic=kw.pop("topic", f"topik {d}"), **kw)

    def test_satu_entri_per_tanggal(self):
        state.upsert_history(self.entry("2026-11-10", format="story", hook="h1", clip_ids=["px:1"]))
        state.upsert_history(self.entry("2026-11-11", format="list_3", hook="h2", clip_ids=["px:2"]))
        state.upsert_history(self.entry("2026-11-11", format="question", hook="h3", clip_ids=["px:3"]))
        hist = state.load_history()
        self.assertEqual([e.date for e in hist], ["2026-11-10", "2026-11-11"])
        self.assertEqual(state.last_formats(2), ["story", "question"])
        self.assertEqual(state.last_hooks(5), ["h1", "h3"])
        self.assertEqual(state.recent_clip_ids(30, date(2026, 11, 12)), {"px:1", "px:3"})
        self.assertEqual(state.recent_topics(1, date(2026, 11, 12)), ["topik 2026-11-11"])

    def test_baca_tanpa_membuat_folder(self):
        self.assertEqual(state.load_history(), [])
        self.assertEqual(state.load_learnings(), "")
        self.assertFalse(os.path.exists(os.path.join(self.data, "channel")))

    def test_learnings_dan_performa(self):
        state.save_learnings("- data belum cukup")
        state.save_performance({"abc": {"views": 10}})
        self.assertEqual(state.load_learnings(), "- data belum cukup\n")
        self.assertEqual(state.load_performance()["abc"]["views"], 10)
        leftovers = [n for n in os.listdir(os.path.join(self.data, "channel")) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])


# --------------------------------------------------------------------------
class FallbackLLM(unittest.TestCase):
    def setUp(self):
        self.cfg = config.ChannelConfig()
        self.cfg.llm.providers = ["gemini", "groq", "gemini_lite"]
        self.settings = {"gemini_key": "g", "gemini_model": "gemini-utama", "local_ai_url": "http://127.0.0.1:9",
                         "local_ai_model": "", "channel": {"keys": {"groq": "gsk"}}}
        self.waits = []
        llm.reset_cooldown()
        self.addCleanup(llm.reset_cooldown)
        self.patches = [mock.patch.object(llm, "_sleep", self.waits.append),
                        mock.patch.dict(os.environ, {k: "" for k in config.ENV_KEYS.values()})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def run_ping(self, **kw):
        calls = []
        got = llm.generate_json("ping", {}, Ping, 0.2, cfg=self.cfg, settings=self.settings, calls=calls,
                                log=lambda m: None, **kw)
        return got, calls

    def test_429_lalu_pindah_ke_groq(self):
        err = LocalAIError("kuota", status=429, retry_after=7)
        with mock.patch("gemini_ai.interact", side_effect=err) as gem, \
                mock.patch("local_ai.openai_chat_json", return_value={"ok": True, "message": "halo"}) as groq:
            got, calls = self.run_ping()
        self.assertEqual(got.message, "halo")
        self.assertEqual(gem.call_count, self.cfg.llm.max_retries)
        self.assertEqual(self.waits, [7.0] * (self.cfg.llm.max_retries - 1))   # Retry-After dihormati
        self.assertEqual(groq.call_count, 1)
        self.assertEqual(groq.call_args.kwargs["temperature"], 0.2)
        self.assertEqual([c.provider for c in calls], ["gemini", "groq"])
        self.assertFalse(calls[0].ok)
        self.assertTrue(calls[1].ok)

    def test_jeda_bertambah_tanpa_retry_after(self):
        with mock.patch("gemini_ai.interact", side_effect=LocalAIError("sibuk", status=503)), \
                mock.patch("local_ai.openai_chat_json", return_value={"ok": True}):
            self.run_ping()
        self.assertEqual(self.waits, [2.0, 4.0, 8.0])

    def test_galat_tetap_langsung_pindah(self):
        with mock.patch("gemini_ai.interact", side_effect=LocalAIError("model tidak ada", status=404)) as gem, \
                mock.patch("local_ai.openai_chat_json", return_value={"ok": True}):
            self.run_ping()
        self.assertEqual(gem.call_count, 1)
        self.assertEqual(self.waits, [])

    def test_jawaban_salah_diulang_sekali_dengan_pesan_galat(self):
        answers = iter([{"message": "tanpa ok"}, {"ok": True, "message": "benar"}])
        prompts = []

        def fake(inputs, **kw):
            prompts.append(inputs[0]["text"])
            return json.dumps(next(answers))

        with mock.patch("gemini_ai.interact", side_effect=fake):
            got, calls = self.run_ping()
        self.assertEqual(got.message, "benar")
        self.assertEqual(len(prompts), 2)
        self.assertIn("ok", prompts[1].split("TIDAK lolos")[1])
        self.assertNotIn("TIDAK lolos", prompts[0])
        self.assertEqual(calls[0].provider, "gemini")

    def test_jawaban_tetap_salah_pindah_provider(self):
        with mock.patch("gemini_ai.interact", return_value='{"message": "x"}') as gem, \
                mock.patch("local_ai.openai_chat_json", return_value={"ok": True}):
            _, calls = self.run_ping()
        self.assertEqual(gem.call_count, 2)
        self.assertEqual(calls[-1].provider, "groq")

    def test_cadangan_gemini_ringan(self):
        self.settings["channel"]["keys"]["groq"] = ""     # tanpa kunci Groq: dilewati

        def fake(inputs, model, **kw):
            if model == "gemini-utama":
                raise LocalAIError("Gemini terlalu lama menjawab.")
            return '{"ok": true, "message": "ringan"}'

        with mock.patch("gemini_ai.interact", side_effect=fake):
            got, calls = self.run_ping()
        self.assertEqual(got.message, "ringan")
        self.assertEqual([(c.provider, c.model) for c in calls],
                         [("gemini", "gemini-utama"), ("groq", "openai/gpt-oss-120b"),
                          ("gemini_lite", "gemini-3.5-flash-lite")])
        self.assertEqual(self.waits, [])   # timeout & kunci kosong tidak ditunggu

    def test_provider_penuh_diistirahatkan(self):
        def fake(inputs, model, **kw):
            if model == "gemini-utama":
                raise LocalAIError("Gemini terlalu lama menjawab.")
            return '{"ok": true}'

        with mock.patch("gemini_ai.interact", side_effect=fake) as gem, \
                mock.patch("local_ai.openai_chat_json", return_value={"ok": True}):
            self.run_ping()
            first = gem.call_count
            _, calls = self.run_ping()         # panggilan berikutnya: model utama dilewati dulu
        self.assertEqual(first, 1)
        self.assertEqual(gem.call_count, 1)
        self.assertEqual(calls[0].provider, "groq")

    def test_pemeriksaan_isi_diulang_sekali_lalu_diterima(self):
        answers = iter(['{"ok": true, "message": "pendek"}', '{"ok": true, "message": "masih pendek"}'])
        with mock.patch("gemini_ai.interact", side_effect=lambda *a, **k: next(answers)) as gem:
            got, _ = self.run_ping(check=lambda r: ["pesan terlalu pendek"] if len(r.message) < 20 else [])
        self.assertEqual(gem.call_count, 2)
        self.assertEqual(got.message, "masih pendek")
        self.assertIn("pesan terlalu pendek", gem.call_args.args[0][0]["text"])

    def test_semua_gagal(self):
        with mock.patch("gemini_ai.interact", side_effect=LocalAIError("x", status=400)), \
                mock.patch("local_ai.openai_chat_json", side_effect=LocalAIError("y", status=401)), \
                self.assertRaises(llm.LLMError) as cm:
            self.run_ping()
        self.assertEqual(len(cm.exception.calls), 3)
        self.assertIn("groq", str(cm.exception))

    def test_qc_tanpa_ai_lokal(self):
        self.cfg.llm.providers = ["ollama", "gemini"]
        with mock.patch("local_ai.chat_json") as ollama, \
                mock.patch("gemini_ai.interact", return_value='{"ok": true}'):
            _, calls = self.run_ping(allow_local=False)
        ollama.assert_not_called()
        self.assertEqual([c.provider for c in calls], ["gemini"])

    def test_token_dicatat(self):
        def fake(inputs, usage=None, **kw):
            usage.update(input=12, output=3)
            return '{"ok": true}'

        with mock.patch("gemini_ai.interact", side_effect=fake):
            _, calls = self.run_ping()
        self.assertEqual((calls[0].tokens_in, calls[0].tokens_out), (12, 3))

    def test_thinking_level_mengikuti_suhu(self):
        with mock.patch("gemini_ai.interact", return_value='{"ok": true}') as gem:
            llm.generate_json("ping", {}, Ping, 0.9, cfg=self.cfg, settings=self.settings, log=lambda m: None)
            self.assertEqual(gem.call_args.kwargs["thinking"], self.cfg.llm.gemini_thinking_creative)
            self.run_ping()
            self.assertEqual(gem.call_args.kwargs["thinking"], self.cfg.llm.gemini_thinking_strict)
            self.assertEqual(gem.call_args.kwargs["model"], "gemini-utama")
            self.assertIn("JSON Schema", gem.call_args.args[0][0]["text"])


# --------------------------------------------------------------------------
class Larangan(unittest.TestCase):
    def test_tidak_memakai_pengunduh_video_kreator_lain(self):
        """SPEC bagian 13: modul channel tidak boleh mengunduh/memakai ulang konten kreator lain."""
        folder = os.path.join(ROOT, "features", "channel")
        pattern = re.compile(r"yt_dlp|yt-dlp|download_video|download_reference")
        for dirpath, _, files in os.walk(folder):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                        self.assertIsNone(pattern.search(f.read()), f"{name} memakai pengunduh video")

    def test_nama_model_tidak_di_kode_llm(self):
        with open(os.path.join(ROOT, "features", "channel", "llm.py"), encoding="utf-8") as f:
            self.assertNotRegex(f.read(), r"gemini-\d|gpt-oss|llama-")


if __name__ == "__main__":
    unittest.main()
