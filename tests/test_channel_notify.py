"""Channel Otomatis, Fase I-6: paket unggah manual (caption TikTok & Instagram), notifikasi Telegram (paket harian,
peringatan kegagalan, kirim uji), rute halaman, dan CLI. Telegram diganti server palsu; tanpa internet."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import json
import os
import threading
import types
from datetime import date
from unittest import mock
import unittest

import requests

from tests.util import ROOT  # noqa: F401
from tests.test_channel_youtube import DAY, YTSandbox, api_error, long_script

import app
import common
from features.channel import cancel, config, jobs, manual_pack, pipeline, runctx, runs, telegram
from features.channel.schemas import Captions, Metadata, RenderInfo, Script, TelegramSent, Visuals, YouTubeResult

BASE = "http://127.0.0.1:7860"
TOKEN = "123456:RAHASIA-TOKEN-BOT"
CHAT = "987654321"


class FakeTelegram:
    """Bot API palsu: mencatat panggilan, bisa diberi galat/429/putus koneksi."""

    def __init__(self):
        self.calls = []
        self.fail = {}              # method -> daftar (status, body) yang dipakai berurutan
        self.drop = 0               # n panggilan berikutnya: koneksi putus
        self.timeout_after_send = 0  # n panggilan berikutnya: terkirim, tetapi jawabannya tidak sampai
        self.updates = [{"update_id": 1, "message": {"chat": {"id": 987654321, "type": "private",
                                                               "first_name": "Rafa", "username": "rafa"}}}]

    def post(self, url, data=None, files=None, timeout=None, headers=None):
        method = url.rsplit("/", 1)[1]
        size, fields = 0, {}
        if hasattr(data, "read"):                    # badan multipart yang dibaca bertahap (sendVideo)
            raw = b"".join(iter(data))
            size = len(raw)
            for part in raw.split(b"--" + data.boundary.encode())[1:-1]:
                head, _, body = part.partition(b"\r\n\r\n")
                name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
                if b"filename=" in head:
                    fields["_file_bytes"] = len(body) - 2
                else:
                    fields[name] = body[:-2].decode()
            self.content_type = (headers or {}).get("Content-Type", "")
        else:
            fields = dict(data or {})
        self.calls.append({"url": url, "method": method, "data": fields, "file_size": fields.get("_file_bytes", size)})
        if self.drop:
            self.drop -= 1
            raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")
        if self.timeout_after_send:
            self.timeout_after_send -= 1
            raise requests.ReadTimeout("read timed out")
        queue = self.fail.get(method)
        if queue:
            status, body = queue.pop(0)
            return Resp(status, body)
        result = {"getMe": {"id": 1, "is_bot": True, "username": "nero_uji_bot"},
                  "getUpdates": self.updates}.get(method, {"message_id": len(self.calls)})
        return Resp(200, {"ok": True, "result": result})

    def of(self, method):
        return [c for c in self.calls if c["method"] == method]


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class NotifySandbox(YTSandbox):
    def setUp(self):
        super().setUp()
        self.t = FakeTelegram()
        fake_requests = types.SimpleNamespace(post=self.t.post, RequestException=requests.RequestException,
                                              ConnectTimeout=requests.ConnectTimeout)
        self.p_tg = [mock.patch.object(telegram, "requests", fake_requests),
                     mock.patch.object(telegram, "_sleep", lambda s: None),
                     mock.patch.object(telegram, "SEND_GAP", 0)]
        for p in self.p_tg:
            p.start()
        config.save_config({"channel": {"niche": "fakta sains", "audience": "pelajar"}})

    def tearDown(self):
        for p in reversed(self.p_tg):
            p.stop()
        super().tearDown()

    def tg_keys(self):
        config.save_keys({"telegram_token": TOKEN, "telegram_chat_id": CHAT})

    def make_pack_run(self, **kw):
        ctx = self.make_video_run(**kw)
        run = self.make_run(ctx)
        self.assertTrue(pipeline.step_manual_pack(run, False))
        return ctx

    def all_text(self):
        return json.dumps(self.t.calls, ensure_ascii=False)


# --------------------------------------------------------------------------
class PaketManual(NotifySandbox):
    def meta(self, **kw):
        base = {"title": "Kenapa gurita punya tiga jantung?", "description": "Gurita punya tiga jantung.\n\nSumber:\n- x",
                "summary": "Gurita punya tiga jantung: dua untuk insang, satu untuk tubuh.",
                "tags": ["gurita", "fakta hewan", "hewan laut"], "hashtags": ["#Shorts", "#fakta"]}
        base.update(kw)
        return Metadata.model_construct(**{"category_id": "27", "language": "id", **base})

    def test_caption_tiktok_dan_instagram(self):
        c = manual_pack.build(self.meta(), uses_ai=False)
        body, tags = c.tiktok.rsplit("\n\n", 1)
        self.assertLessEqual(manual_pack.utf16_len(body), manual_pack.TIKTOK_TEXT_MAX)
        self.assertEqual(tags.split(), c.hashtags)
        self.assertTrue(3 <= len(c.hashtags) <= 5)
        self.assertNotIn("#Shorts", c.hashtags)
        self.assertIn("Gurita punya tiga jantung: dua untuk insang", c.instagram)
        self.assertLessEqual(c.instagram.count("#"), 5)                   # batas hashtag Instagram
        self.assertNotIn("AI", c.tiktok)
        self.assertFalse(c.tiktok_ai_label)
        self.assertTrue(c.instagram_ai_label)                              # narasi suara AI

    def test_gambar_ai_diungkap(self):
        c = manual_pack.build(self.meta(), uses_ai=True)
        self.assertIn("Ilustrasi dibuat dengan AI.", c.tiktok)
        self.assertIn("ilustrasi dibuat dengan AI", c.instagram)
        self.assertTrue(c.tiktok_ai_label)

    def test_batas_panjang(self):
        c = manual_pack.build(self.meta(title="Judul " * 60, summary="kata " * 900,
                                        hashtags=["#Shorts", "#fyp", *[f"#tag{i}" for i in range(9)]]), uses_ai=True)
        body = c.tiktok.rsplit("\n\n", 1)[0]
        self.assertLessEqual(manual_pack.utf16_len(body), manual_pack.TIKTOK_TEXT_MAX)
        self.assertLessEqual(manual_pack.utf16_len(c.instagram), manual_pack.CAPTION_MAX)
        self.assertEqual(len(c.hashtags), 5)
        self.assertNotIn("#fyp", c.hashtags)

    def test_hashtag_platform_dan_judul_berhashtag_disaring(self):
        c = manual_pack.build(self.meta(title="Fakta gurita #shorts", tags=[],
                                        hashtags=["#Shorts", "#ShortsIndonesia", "#YouTube", "#fyp\u30b7"]),
                              uses_ai=False, topic="Gurita tiga jantung")
        for bad in ("#shorts", "#ShortsIndonesia", "#YouTube", "#fyp"):
            self.assertNotIn(bad, c.tiktok)
            self.assertNotIn(bad, c.instagram)
        self.assertGreaterEqual(len(c.hashtags), 3)             # dilengkapi dari kata topik
        self.assertLessEqual(c.instagram.count("#"), 5)

    def test_varian_shorts_dan_reels_disaring(self):
        c = manual_pack.build(self.meta(hashtags=["#Shorts", "#YTShorts", "#FaktaShorts", "#IGReels", "#Gurita"],
                                        tags=["hewan laut", "fakta sains"]), uses_ai=False)
        self.assertEqual(c.hashtags[0], "#Gurita")
        for bad in ("Shorts", "Reels"):
            self.assertNotIn(bad, " ".join(c.hashtags))
        self.assertIn("#Explorasi", manual_pack.pack_hashtags(self.meta(hashtags=["#Explorasi"], tags=[])))

    def test_langkah_paket_manual(self):
        ctx = self.make_pack_run()
        caps = ctx.load("captions", Captions)
        out = os.path.join(common.OUTPUT, config.ID, DAY.isoformat())
        for name in pipeline.PACK_TXT:
            self.assertTrue(os.path.isfile(ctx.path(name)), name)
            self.assertTrue(os.path.isfile(os.path.join(out, name)), name)
        with open(ctx.path("tiktok_caption.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), caps.tiktok.strip())
        self.assertFalse(pipeline.step_manual_pack(self.make_run(ctx), False))     # dipakai ulang
        ctx.save("visuals", Visuals(segments=[], uses_ai_images=True))
        ctx.set_fingerprint("visuals_src", runctx.sha(Script.model_validate(long_script())))
        self.assertTrue(pipeline.step_manual_pack(self.make_run(ctx), False))      # visual sebenarnya berubah
        self.assertTrue(ctx.load("captions", Captions).tiktok_ai_label)

    def test_paket_perlu_judul_naskah_sekarang(self):
        ctx = self.make_video_run()
        ctx.set_fingerprint("seo_src", "naskah-lain")
        with self.assertRaises(pipeline.StepError):
            pipeline.step_manual_pack(self.make_run(ctx), False)


# --------------------------------------------------------------------------
class IsiPesan(NotifySandbox):
    def test_urutan_dan_escape(self):
        script = Script.model_validate(long_script())
        meta = Metadata(title="A <b> & C", description="x", tags=[])
        caps = Captions(tiktok="tt", instagram="ig")
        items = telegram.compose_run(day="2026-11-12", script=script, meta=meta,
                                     render=RenderInfo(width=1080, height=1920, fps=30, duration=50.2,
                                                       has_audio=True, valid=True),
                                     captions=caps, video="v.mp4")
        self.assertEqual([i.kind for i in items], ["ringkasan", "video", "tiktok", "instagram", "naskah"])
        self.assertIn("A &lt;b&gt; &amp; C", items[0].text)
        self.assertIn("belum diunggah", items[0].text)
        self.assertEqual(items[1].video, "v.mp4")
        self.assertIn("<pre>tt</pre>", items[2].text)
        self.assertIn(script.hook, telegram.plain(items[4].text))

    def test_status_youtube_dan_jendela_tinjau(self):
        script = Script.model_validate(long_script())
        yt = YouTubeResult(video_id="V", studio_url="https://studio.youtube.com/video/V/edit", privacy="private",
                           publish_at="2026-11-12T11:00:00Z", publish_at_wib="12-11-2026 18.00 WIB", status="masalah",
                           note="Jadwal tidak sesuai.")
        text = telegram.compose_run(day="d", script=script, youtube=yt, approval="review_window")[0].text
        self.assertIn("tayang otomatis 12-11-2026 18.00 WIB", text)
        self.assertIn('href="https://studio.youtube.com/video/V/edit"', text)
        self.assertIn("PERINGATAN YouTube", text)
        self.assertIn("sebelum jam tayang", text)

    def test_naskah_panjang_dipecah(self):
        data = long_script(n_words=400)
        items = telegram.compose_run(day="d", script=Script.model_validate(data))
        parts = [i for i in items if i.kind.startswith("naskah")]
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(telegram.utf16_len(p.text), telegram.TEXT_LIMIT)

    def test_peringatan_gagal_30_baris(self):
        lines = [f"10:00:{i:02d} baris {i}" for i in range(60)]
        it = telegram.alert_items(day="2026-11-12", step="qc", error="QC gagal <skor 5>", log_lines=lines)[0]
        self.assertIn("Langkah: qc", it.text)
        self.assertIn("QC gagal &lt;skor 5&gt;", it.text)
        self.assertIn("baris 59", it.text)
        self.assertNotIn("baris 29\n", it.text)                             # hanya 30 baris terakhir
        self.assertLessEqual(telegram.utf16_len(it.text), telegram.TEXT_LIMIT)


# --------------------------------------------------------------------------
class Kirim(NotifySandbox):
    def setUp(self):
        super().setUp()
        self.tg_keys()

    def test_kirim_teks_html(self):
        telegram.send_text("<b>Halo</b>")
        c = self.t.of("sendMessage")[0]
        self.assertEqual((c["data"]["chat_id"], c["data"]["parse_mode"]), (CHAT, "HTML"))
        self.assertIn("is_disabled", c["data"]["link_preview_options"])

    def test_html_ditolak_dikirim_polos(self):
        self.t.fail["sendMessage"] = [(400, {"ok": False, "error_code": 400,
                                             "description": "Bad Request: can't parse entities: x"})]
        telegram.send_text("<b>Halo &amp; dunia</b>")
        second = self.t.of("sendMessage")[1]["data"]
        self.assertNotIn("parse_mode", second)
        self.assertEqual(second["text"], "Halo & dunia")

    def test_429_menunggu_lalu_berhasil(self):
        waits = []
        self.t.fail["sendMessage"] = [(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 3}})]
        with mock.patch.object(telegram, "_sleep", waits.append):
            telegram.send_text("x")
        self.assertEqual(waits, [3.0])
        self.assertEqual(len(self.t.of("sendMessage")), 2)

    def test_galat_jelas_tanpa_token(self):
        self.t.fail["sendMessage"] = [(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
        with self.assertRaises(telegram.TelegramError) as e:
            telegram.send_text("x")
        self.assertIn("Token bot", str(e.exception))
        self.t.drop = telegram.ATTEMPTS
        with self.assertRaises(telegram.TelegramError) as e:
            telegram.send_text("x")
        self.assertNotIn(TOKEN, str(e.exception))                           # URL berisi token tidak bocor
        self.assertIn("ConnectionError", str(e.exception))
        self.t.fail["sendMessage"] = [(400, {"ok": False, "description": "Bad Request: chat not found"})]
        with self.assertRaises(telegram.TelegramError) as e:
            telegram.send_text("x")
        self.assertIn("Start", str(e.exception))

    def test_cari_chat(self):
        chats = telegram.find_chats()
        self.assertEqual(chats, [{"id": CHAT, "type": "private", "name": "Rafa (@rafa)"}])
        self.t.fail["getUpdates"] = [(409, {"ok": False, "description": "Conflict: can't use getUpdates method "
                                                                        "while webhook is active"})]
        with self.assertRaises(telegram.TelegramError) as e:
            telegram.find_chats()
        self.assertIn("webhook", str(e.exception))

    def test_video_terlalu_besar_jadi_pesan(self):
        path = os.path.join(self.tmp, "v.mp4")
        with open(path, "wb") as f:
            f.write(b"0" * 100)
        logs = []
        with mock.patch.object(telegram, "VIDEO_LIMIT", 50):
            telegram.send_items([telegram.Item("video", "<b>Judul</b>", path)], log=logs.append)
        self.assertEqual(self.t.of("sendVideo"), [])
        self.assertIn("Folder hasil", self.t.of("sendMessage")[0]["data"]["text"])

    def test_video_tidak_dikirim_ulang_bila_hasilnya_tidak_pasti(self):
        path = os.path.join(self.tmp, "v.mp4")
        with open(path, "wb") as f:
            f.write(b"0" * 1000)
        self.t.timeout_after_send = 1
        with self.assertRaises(telegram.TelegramError) as e:
            telegram.send_video(path, "x")
        self.assertIn("mungkin sudah sampai", str(e.exception))
        self.assertEqual(len(self.t.of("sendVideo")), 1)

    def test_upload_video_bisa_dibatalkan_di_tengah(self):
        path = os.path.join(self.tmp, "v.mp4")
        with open(path, "wb") as f:
            f.write(b"0" * (telegram.Multipart.CHUNK * 3))
        ev = threading.Event()
        body = telegram.Multipart({"chat_id": "1"}, "video", path, "video/mp4", ev)
        try:
            self.assertTrue(body.read(1024))
            ev.set()                                            # Batal ditekan saat upload berjalan
            with self.assertRaises(cancel.Cancelled):
                body.read(1024)
        finally:
            body.close()

    def test_token_berawalan_bot_dan_token_salah_bentuk(self):
        config.save_keys({"telegram_token": "bot" + TOKEN})
        telegram.get_me()
        self.assertTrue(self.t.calls[-1]["url"].endswith(f"/bot{TOKEN}/getMe"))
        self.t.fail["getMe"] = [(404, {"ok": False, "error_code": 404, "description": "Not Found"})]
        with self.assertRaises(telegram.TelegramError) as e:
            telegram.get_me()
        self.assertIn("Token bot", str(e.exception))

    def test_kirim_video(self):
        path = os.path.join(self.tmp, "v.mp4")
        with open(path, "wb") as f:
            f.write(b"0" * 1234)
        telegram.send_items([telegram.Item("video", "<b>Judul</b>", path)])
        c = self.t.of("sendVideo")[0]
        self.assertEqual(c["file_size"], 1234)
        self.assertEqual(c["data"]["supports_streaming"], "true")
        self.assertEqual((c["data"]["chat_id"], c["data"]["caption"]), (CHAT, "<b>Judul</b>"))
        self.assertTrue(self.t.content_type.startswith("multipart/form-data; boundary="))


# --------------------------------------------------------------------------
class LangkahNotify(NotifySandbox):
    def test_uji_coba_tidak_mengirim(self):
        self.tg_keys()
        ctx = self.make_pack_run()
        logs = []
        ctx.echo = logs.append
        self.assertFalse(pipeline.step_notify(self.make_run(ctx, dry_run=True), False))
        self.assertEqual(self.t.calls, [])
        self.assertTrue(any("tidak dikirim" in m for m in logs))
        self.assertTrue(any("ringkasan:" in m for m in logs))

    def test_belum_diatur_hanya_peringatan(self):
        ctx = self.make_pack_run()
        logs = []
        ctx.echo = logs.append
        self.assertFalse(pipeline.step_notify(self.make_run(ctx), False))
        self.assertEqual(self.t.calls, [])
        self.assertTrue(any("belum diisi" in m for m in logs))

    def test_kirim_sekali_dan_lanjutkan_yang_terputus(self):
        self.tg_keys()
        ctx = self.make_pack_run()
        self.t.fail["sendMessage"] = [(200, {"ok": True, "result": {}}),        # ringkasan
                                      (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})]
        with self.assertRaises(pipeline.StepError):
            pipeline.step_notify(self.make_run(ctx), False)
        sent = ctx.load("telegram", TelegramSent)
        self.assertEqual(sent.sent, ["ringkasan", "video"])
        before = len(self.t.calls)
        self.assertTrue(pipeline.step_notify(self.make_run(ctx), False))         # lanjut dari caption TikTok
        kinds_after = [c["method"] for c in self.t.calls[before:]]
        self.assertNotIn("sendVideo", kinds_after)
        sent = ctx.load("telegram", TelegramSent)
        self.assertIn("naskah", sent.sent)
        self.assertTrue(sent.sent_at)
        n = len(self.t.calls)
        self.assertFalse(pipeline.step_notify(self.make_run(ctx), False))        # sudah terkirim semua
        self.assertEqual(len(self.t.calls), n)
        self.assertTrue(pipeline.step_notify(self.make_run(ctx), True))          # --force: kirim ulang
        with open(ctx.path("run.log"), encoding="utf-8") as f:
            self.assertNotIn(TOKEN, f.read())

    def test_isi_usang_tidak_ikut_dikirim(self):
        self.tg_keys()
        ctx = self.make_pack_run()
        ctx.save("youtube", YouTubeResult(video_id="LAMA", studio_url="https://studio.youtube.com/video/LAMA/edit",
                                          privacy="private", source="naskah-sebelumnya"))
        ctx.save("visuals", Visuals(segments=[], uses_ai_images=True))   # visual kini memakai AI, render belum
        items = pipeline._notify_items(self.make_run(ctx))
        kinds = [i.kind for i in items]
        self.assertNotIn("video", kinds)                                # video lama tidak dilampirkan
        self.assertNotIn("tiktok", kinds)                               # caption dari visual lama
        self.assertIn("naskah sebelumnya", items[0].text)
        self.assertNotIn("studio.youtube.com/video/LAMA", items[0].text)

    def test_salinan_caption_ditarik_saat_judul_dibuat_ulang(self):
        ctx = self.make_pack_run()
        out = os.path.join(common.OUTPUT, config.ID, DAY.isoformat())
        run = self.make_run(ctx)
        run.step = "seo"
        pipeline._begin(run, "seo")
        for name in pipeline.PACK_TXT:
            self.assertFalse(os.path.exists(os.path.join(out, name)), name)

    def test_kirim_uji_tidak_mengubah_status_run(self):
        self.tg_keys()
        ctx = self.make_pack_run()
        ctx.log("GAGAL di langkah render: Validasi video gagal")
        self.assertEqual(pipeline.notify_test(DAY, echo=lambda m: None), 0)
        self.assertEqual(runs.failure(ctx)["step"], "render")           # kegagalan lain tetap tampil
        ctx.log("selesai. dijalankan: render; dipakai ulang: -")
        self.t.fail["sendMessage"] = [(400, {"ok": False, "description": "Bad Request: chat not found"})]
        self.assertEqual(pipeline.notify_test(DAY, echo=lambda m: None), pipeline.EXIT_FAILED)
        self.assertIsNone(runs.failure(ctx))                            # kiriman uji gagal tidak menandai run
        ctx.log("GAGAL di langkah notify: Telegram tidak bisa dihubungi")
        self.assertEqual(pipeline.notify_test(DAY, echo=lambda m: None), 0)
        self.assertIsNone(runs.failure(ctx))                            # kiriman harian yang gagal kini terkirim

    def test_topik_dari_naskah_yang_dikirim(self):
        self.tg_keys()
        ctx = self.make_pack_run()
        res = ctx.load("research", pipeline.Research)
        res.candidates[0].topic = "Lubang hitam supermasif"          # riset baru, naskah belum ditulis ulang
        ctx.save("research", res)
        text = pipeline._notify_items(self.make_run(ctx))[0].text
        self.assertIn("Topik: Gurita tiga jantung", text)
        self.assertNotIn("Lubang hitam", text)

    def test_kirim_uji_menggantikan_kiriman_harian_yang_gagal(self):
        self.tg_keys()
        ctx = self.make_pack_run()
        ctx.log("GAGAL di langkah notify: Telegram tidak bisa dihubungi")
        self.assertEqual(pipeline.notify_test(DAY, echo=lambda m: None), 0)
        sent = ctx.load("telegram", TelegramSent)
        self.assertIn("video", sent.sent)
        n = len(self.t.calls)
        self.assertFalse(pipeline.step_notify(self.make_run(ctx), False))       # tidak dikirim dua kali
        self.assertEqual(len(self.t.calls), n)

    def test_unggah_uji_gagal_tidak_menimpa_kegagalan_lain(self):
        self.connect_keys()
        ctx = self.make_pack_run()
        ctx.log("GAGAL di langkah notify: Telegram tidak bisa dihubungi")
        self.g.insert_error = api_error(403, "quotaExceeded")
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertEqual(runs.failure(ctx)["step"], "notify")              # kegagalan harian tetap tampil
        self.assertTrue(any("(unggah uji) gagal" in m for m in out), out)
        job = jobs.RunJob(DAY, "upload")
        job.echo(out[-1])
        self.assertIn("Kuota", job.error)

    def test_dimatikan(self):
        self.tg_keys()
        config.save_config({"notify": {"telegram": False}})
        ctx = self.make_pack_run()
        self.assertFalse(pipeline.step_notify(self.make_run(ctx), False))
        self.assertEqual(self.t.calls, [])

    def test_peringatan_kegagalan_run_harian(self):
        self.tg_keys()
        config.save_config({"enabled": True})
        out = []
        code = pipeline.run_daily(DAY, only="render", echo=out.append)          # belum ada naskah: gagal
        self.assertEqual(code, pipeline.EXIT_FAILED)
        msgs = self.t.of("sendMessage")
        self.assertEqual(len(msgs), 1)
        self.assertIn("GAGAL", msgs[0]["data"]["text"])
        self.assertIn("Langkah: render", msgs[0]["data"]["text"])
        self.assertTrue(any("peringatan kegagalan terkirim" in m for m in out))

    def test_peringatan_tidak_dikirim_saat_uji_coba_atau_batal(self):
        self.tg_keys()
        config.save_config({"enabled": True})
        out = []
        pipeline.run_daily(DAY, dry_run=True, only="render", echo=out.append)
        self.assertEqual(self.t.calls, [])
        self.assertTrue(any("peringatan kegagalan tidak dikirim" in m for m in out))
        ev = threading.Event()
        ev.set()
        pipeline.run_daily(DAY, only="render", echo=lambda m: None, cancel=ev)
        self.assertEqual(self.t.calls, [])

    def test_peringatan_gagal_tidak_mengubah_hasil(self):
        self.tg_keys()
        config.save_config({"enabled": True})
        self.t.fail["sendMessage"] = [(401, {"ok": False, "description": "Unauthorized"})]
        out = []
        self.assertEqual(pipeline.run_daily(DAY, only="render", echo=out.append), pipeline.EXIT_FAILED)
        self.assertTrue(any("tidak terkirim" in m for m in out))

    def test_kirim_uji_walau_nonaktif(self):
        self.tg_keys()
        self.make_pack_run()
        self.assertEqual(pipeline.notify_test(DAY, echo=lambda m: None), 0)
        self.assertEqual(len(self.t.of("sendVideo")), 1)
        self.assertGreaterEqual(len(self.t.of("sendMessage")), 4)
        self.assertIsNone(runctx.RunContext(DAY).load("telegram", TelegramSent))   # kiriman harian tetap jalan
        self.assertEqual(pipeline.notify_test(date(2026, 1, 1), echo=lambda m: None), pipeline.EXIT_CONFIG)


# --------------------------------------------------------------------------
class HalamanTelegram(NotifySandbox):
    def setUp(self):
        super().setUp()
        self.c = app.app.test_client()

    def post(self, url, body=None):
        return self.c.post(url, json=body or {}, base_url=BASE)

    def get(self, url):
        return self.c.get(url, base_url=BASE)

    def test_cari_chat_dan_pesan_uji(self):
        self.assertEqual(self.post("/api/channel/telegram/chats").status_code, 400)      # token belum ada
        config.save_keys({"telegram_token": TOKEN})
        r = self.post("/api/channel/telegram/chats").get_json()
        self.assertEqual((r["bot"], r["chats"][0]["id"]), ("nero_uji_bot", CHAT))
        self.assertEqual(self.post("/api/channel/telegram/test").status_code, 400)       # chat ID belum ada
        config.save_keys({"telegram_chat_id": CHAT})
        self.assertEqual(self.post("/api/channel/telegram/test").status_code, 200)
        for path in ("/api/channel/settings", "/api/channel/state"):
            self.assertNotIn(TOKEN, self.get(path).get_data(as_text=True))
        st = self.get("/api/channel/state").get_json()
        self.assertTrue(st["telegram"]["configured"])
        self.assertTrue({x["id"]: x for x in st["ready"]}["telegram"]["ok"])

    def test_kirim_paket_sebagai_pekerjaan(self):
        self.tg_keys()
        self.make_pack_run()
        r = self.post("/api/channel/telegram/send", {"date": DAY.isoformat()})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["job"]["mode"], "notify")
        jobs.current().thread.join(10)
        self.assertEqual(jobs.current().status, "selesai", jobs.current().snapshot())
        self.assertEqual(len(self.t.of("sendVideo")), 1)
        self.assertEqual(self.post("/api/channel/run", {"mode": "notify"}).status_code, 400)

    def test_galat_kirim_uji_tampil_di_pekerjaan(self):
        self.tg_keys()
        self.make_pack_run()
        self.t.fail["sendMessage"] = [(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})]
        self.post("/api/channel/telegram/send", {"date": DAY.isoformat()})
        jobs.current().thread.join(10)
        snap = jobs.current().snapshot()
        self.assertEqual(snap["status"], "gagal")
        self.assertIn("Start", snap["error"])

    def test_status_terkirim_hanya_untuk_naskah_sekarang(self):
        ctx = self.make_pack_run()
        ctx.save("telegram", TelegramSent(source="naskah-lama", sent=["ringkasan"], sent_at="2026-11-12T18:05"))
        d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
        self.assertIsNone(d["telegram"])

    def test_detail_run_memuat_caption(self):
        self.make_pack_run()
        d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
        self.assertIn("tiktok", d["pack"])
        self.assertIn("manual_pack", [s["id"] for s in d["steps"]])

    def test_cli_cari_chat(self):
        from features.channel import __main__ as cli
        config.save_keys({"telegram_token": TOKEN})
        self.assertEqual(cli.main(["telegram-chats"]), 0)
        self.t.fail["getMe"] = [(401, {"ok": False, "description": "Unauthorized"})]
        self.assertEqual(cli.main(["telegram-chats"]), 1)                       # pesan, bukan traceback


if __name__ == "__main__":
    unittest.main()
