"""Channel Otomatis, Fase I-5: YouTube (OAuth loopback + PKCE, upload resumable, penjaga upload ganda, verifikasi,
playlist, sinyal tren, langkah publish_youtube, unggah uji, rute halaman). Google diganti server palsu; tanpa
internet (hanya socket 127.0.0.1 untuk listener loopback)."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import base64
import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from unittest import mock
import unittest

import requests

from tests.util import ROOT  # noqa: F401
from tests.test_channel_core import ChannelSandbox
from tests.test_channel_plan import long_script

import app
import common
from features.channel import config, genres, jobs, llm, pipeline, runctx, runs, state, timeutil, youtube
from features.channel.config import ChannelConfig
from features.channel.schemas import QC, Metadata, RenderInfo, Research, Script, Visuals, Words, YouTubeResult

BASE = "http://127.0.0.1:7860"
DAY = date(2026, 11, 12)
SESSION_URI = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=SESI1"
RT = "RT-RAHASIA-123"


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("bukan JSON")
        return self._body


def api_error(status, reason, message="x"):
    return Resp(status, {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}})


class FakeGoogle:
    """Server Google palsu: token, channels, videos, playlistItems, search, dan upload resumable."""

    def __init__(self):
        self.calls = []
        self.scope = f"{youtube.SCOPE_UPLOAD} {youtube.SCOPE_READONLY} {youtube.SCOPE_ANALYTICS}"
        self.token_error = ""
        self.tokens = 0
        self.inserted = []            # body resource video per sesi baru
        self.insert_error = None
        self.size = None
        self.stored = bytearray()
        self.fail_chunks = 0          # potongan berikutnya dibalas 503
        self.drop_chunk = 0           # potongan ke-n (1..) memutus koneksi
        self.chunks = 0
        self.gone = False             # sesi kedaluwarsa (404)
        self.processing = 0           # jumlah cek pertama yang masih "diproses"
        self.privacy_override = ""
        self.uploads_items = []
        self.playlist_reason = ""
        self.search_titles = ["Fakta &#39;unik&#39; laut", "Gurita pintar"]
        self.fail_playlist_list = 0   # GET playlistItems berikutnya dibalas 503
        self.no_uploads_playlist = False  # channel baru tanpa video: daftar upload belum ada (404)
        self.reject_uri = ""          # PUT ke alamat sesi ini dibalas 401 (sesi milik akun lain)
        self.upload_status = ""       # mis. "rejected": video ditolak YouTube
        self.token_status = 400

    # ------------------------------------------------------------ antarmuka requests
    def post(self, url, data=None, params=None, json=None, headers=None, timeout=None):
        return self.request("POST", url, data=data, params=params, json=json, headers=headers)

    def put(self, url, data=None, headers=None, timeout=None):
        return self.request("PUT", url, data=data, headers=headers)

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params or {}, "json": json, "data": data,
                           "headers": headers or {}})
        if url == youtube.TOKEN_URL:
            return self._token(data)
        if url == youtube.REVOKE_URL:
            return Resp(200, {})
        if not (headers or {}).get("Authorization", "").startswith("Bearer AT"):
            return api_error(401, "authError")
        if method == "POST" and url == youtube.UPLOAD_URL:
            if self.insert_error is not None:
                return self.insert_error
            self.inserted.append(json)
            self.size = int(headers["X-Upload-Content-Length"])
            self.stored = bytearray()
            n = len(self.inserted)
            return Resp(200, {}, {"Location": SESSION_URI if n == 1 else f"{SESSION_URI}&n={n}"})
        if method == "PUT" and url.startswith(SESSION_URI):
            if self.reject_uri and url == self.reject_uri:
                return api_error(401, "authError")
            return self._put(data, headers)
        if url.endswith("/channels"):
            return Resp(200, {"items": [{"id": "UC123", "snippet": {"title": "Channel Uji", "customUrl": "@uji"},
                                         "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]})
        if url.endswith("/videos") and method == "GET":
            video = self._video(self.processing > 0)
            self.processing = max(0, self.processing - 1)
            return Resp(200, {"items": [video]})
        if url.endswith("/playlistItems") and method == "GET":
            if self.fail_playlist_list:
                self.fail_playlist_list -= 1
                return Resp(503, {})
            if self.no_uploads_playlist:
                return api_error(404, "playlistNotFound")
            return Resp(200, {"items": self.uploads_items})
        if url.endswith("/playlistItems") and method == "POST":
            return api_error(409, self.playlist_reason) if self.playlist_reason else Resp(200, {"id": "PLI1"})
        if url.endswith("/search"):
            return Resp(200, {"items": [{"id": {"videoId": f"s{i}"}, "snippet": {"title": t}}
                                        for i, t in enumerate(self.search_titles)]})
        return Resp(404, {"error": {"message": "tidak ada"}})

    def _token(self, data):
        if self.token_error:
            return Resp(self.token_status, {"error": self.token_error, "error_description": "x"})
        self.tokens += 1
        body = {"access_token": f"AT{self.tokens}", "expires_in": 3599, "token_type": "Bearer", "scope": self.scope}
        if data.get("grant_type") == "authorization_code":
            body["refresh_token"] = RT
        return Resp(200, body)

    def _put(self, data, headers):
        if self.gone:
            return Resp(404, {})
        rng = headers["Content-Range"]
        total = int(rng.rsplit("/", 1)[1])
        if rng.startswith("bytes */"):
            if len(self.stored) == total:
                return Resp(201, self._video())
            return Resp(308, None, {"Range": f"bytes=0-{len(self.stored) - 1}"} if self.stored else {})
        self.chunks += 1
        if self.drop_chunk and self.chunks == self.drop_chunk:
            self.stored += data[: len(data) // 2]     # sebagian sempat sampai sebelum koneksi putus
            raise requests.ConnectionError("putus")
        if self.fail_chunks:
            self.fail_chunks -= 1
            return Resp(503, {})
        first = int(re.match(r"bytes (\d+)-", rng).group(1))
        if first != len(self.stored):
            return Resp(400, {"error": {"message": "Content-Range salah"}})
        self.stored += data
        if len(self.stored) == total:
            return Resp(201, self._video())
        return Resp(308, None, {"Range": f"bytes=0-{len(self.stored) - 1}"})

    def _video(self, processing=False):
        body = self.inserted[-1] if self.inserted else {"status": {"privacyStatus": "private"}}
        st = body["status"]
        status = {"uploadStatus": self.upload_status or ("uploaded" if processing else "processed"),
                  "privacyStatus": self.privacy_override or st["privacyStatus"]}
        if self.upload_status == "rejected":
            status["rejectionReason"] = "duplicate"
        if st.get("publishAt"):
            status["publishAt"] = st["publishAt"].replace("Z", ".000Z")
        return {"id": "VID1", "status": status, "snippet": body.get("snippet", {}),
                "processingDetails": {"processingStatus": "processing" if processing else "succeeded"}}

    # ------------------------------------------------------------ pembantu
    def of(self, method, suffix):
        return [c for c in self.calls if c["method"] == method and c["url"].endswith(suffix)]


class YTSandbox(ChannelSandbox):
    def setUp(self):
        super().setUp()
        self.g = FakeGoogle()
        self.patches2 = [mock.patch.object(youtube.requests, name, getattr(self.g, name))
                         for name in ("post", "put", "request")]
        self.patches2.append(mock.patch.object(youtube, "_sleep", lambda s: None))
        for p in self.patches2:
            p.start()
        youtube._tokens.clear()
        youtube._flow = None
        jobs._job = None

    def tearDown(self):
        flow = youtube._flow
        if flow is not None:
            flow.cancel()
            if flow.thread:
                flow.thread.join(5)
        youtube._flow = None
        job = jobs.current()
        if job and job.thread:
            job.cancel.set()
            job.thread.join(10)
        jobs._job = None
        for p in reversed(self.patches2):
            p.stop()
        youtube._tokens.clear()
        super().tearDown()

    def connect_keys(self, refresh=True):
        keys = {"yt_client_id": "CID.apps.googleusercontent.com", "yt_client_secret": "SEC-RAHASIA"}
        if refresh:
            keys["yt_refresh_token"] = RT
        config.save_keys(keys)
        if refresh:
            youtube._save_account(channel_id="UC123", channel_title="Channel Uji", uploads="UU123",
                                  scopes=sorted(self.g.scope.split()), checked_at=youtube._now_iso(), problem="")

    def make_video_run(self, day=DAY, *, cfg=None, qc_pass=True, valid=True, needs_review=False, size=2500,
                       other_script=False):
        """Run lengkap dengan sidik yang sah: QC (sidik asli), metadata & video milik skrip sekarang."""
        cfg = cfg or config.load_config()
        ctx = runctx.RunContext(day, echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        script = Script.model_validate(long_script())
        res = Research.model_validate({"date": day.isoformat(), "candidates": [{
            "topic": "Gurita tiga jantung", "angle": "a", "sources": [{"title": "Gurita", "url": "https://x/g"}]}]})
        ctx.save("research", res)
        ctx.save("script", script)
        qc = QC.model_validate({"score": 8 if qc_pass else 5, "checks": [], "issues": []})
        ctx.save("qc", qc)
        ctx.save("metadata", Metadata(title="Kenapa <gurita> punya tiga jantung?", description="Deskripsi uji",
                                      tags=["gurita", "fakta hewan"], hashtags=["#Shorts"], category_id="27"))
        vis = Visuals(segments=[], uses_stock=False)
        words = Words(words=[], duration=49.5)
        ctx.save("visuals", vis)
        ctx.save("words", words)
        ctx.save("render", RenderInfo(width=1080, height=1920, fps=30, duration=50.2, has_audio=True, valid=valid,
                                      needs_review=needs_review))
        ctx.set_fingerprint("qc", pipeline.fp_qc(script, res, cfg))
        src = runctx.sha(script)
        ctx.set_fingerprint("seo_src", src)
        for step in ("tts", "visuals", "render"):
            ctx.set_fingerprint(f"{step}_src", "lain" if other_script else src)
        ctx.set_fingerprint("render", pipeline.fp_render(words, vis, cfg, day))
        ctx.set_fingerprint("render_media", runctx.sha(words, vis))
        ctx.set_fingerprint("seo", pipeline.fp_seo(script, res, cfg))
        ctx.set_fingerprint("research", pipeline.fp_research(cfg))
        ctx.set_fingerprint("script", pipeline.fp_script(res, cfg))
        with open(ctx.path("final.mp4"), "wb") as f:
            f.write(bytes(range(256)) * (size // 256) + b"z" * (size % 256))
        return ctx

    def make_run(self, ctx, cfg=None, dry_run=False):
        return pipeline.Run(ctx, cfg or config.load_config(), common.load_settings(), dry_run)


# --------------------------------------------------------------------------
class HubungkanYouTube(YTSandbox):
    def test_pkce_s256(self):
        verifier, challenge = youtube.pkce_pair()
        self.assertEqual(len(verifier), 64)
        self.assertRegex(verifier, r"^[A-Za-z0-9_-]+$")
        expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expect)
        self.assertNotIn("=", challenge)

    def test_alamat_otorisasi_dan_scope(self):
        cfg = ChannelConfig()
        self.assertNotIn(youtube.SCOPE_MANAGE, youtube.scopes_for(cfg))
        cfg.youtube.playlist_id = "PL1"
        self.assertIn(youtube.SCOPE_MANAGE, youtube.scopes_for(cfg))
        url = youtube.auth_url("CID", "http://127.0.0.1:5555", "CH", "ST", youtube.scopes_for(cfg))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q["redirect_uri"], ["http://127.0.0.1:5555"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertIn(youtube.SCOPE_UPLOAD, q["scope"][0].split(" "))
        self.assertEqual(q["prompt"], ["select_account consent"])

    def _browser(self, flow, query):
        url = flow.redirect_uri + "/?" + urllib.parse.urlencode(query)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # langsung ke 127.0.0.1
        try:
            with opener.open(url, timeout=10) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode("utf-8")

    def test_hubungkan_lewat_loopback(self):
        self.connect_keys(refresh=False)
        flow = youtube.begin_connect(config.load_config())
        self.assertRegex(flow.redirect_uri, r"^http://127\.0\.0\.1:\d+$")
        code, page = self._browser(flow, {"state": flow.state, "code": "KODE-1", "scope": "x"})
        self.assertEqual(code, 200)
        self.assertIn("Channel Uji", page)
        self.assertTrue(flow.done.wait(5))
        self.assertEqual(flow.status, "berhasil", flow.error)
        self.assertEqual(config.secret("yt_refresh_token"), RT)
        sent = self.g.of("POST", "/token")[0]["data"]
        self.assertEqual(sent["code_verifier"], flow.verifier)
        self.assertEqual(sent["redirect_uri"], flow.redirect_uri)    # identik dengan permintaan otorisasi
        self.assertEqual(sent["grant_type"], "authorization_code")
        st = youtube.status()
        self.assertTrue(st["connected"])
        self.assertTrue(st["can_upload"])
        self.assertEqual(st["channel_title"], "Channel Uji")
        self.assertNotIn(RT, json.dumps(st))
        self.assertNotIn("SEC-RAHASIA", json.dumps(st))

    def test_koneksi_diam_tidak_menahan_penerima(self):
        self.connect_keys(refresh=False)
        flow = youtube.begin_connect(config.load_config())
        port = int(flow.redirect_uri.rsplit(":", 1)[1])
        idle = socket.create_connection(("127.0.0.1", port))          # mis. preconnect browser tanpa permintaan
        try:
            t0 = time.time()
            code, _ = self._browser(flow, {"state": flow.state, "code": "KODE-1"})
            self.assertEqual(code, 200)
            self.assertLess(time.time() - t0, 5)
            self.assertTrue(flow.done.wait(5))
            self.assertEqual(flow.status, "berhasil")
        finally:
            idle.close()

    def test_batal_dengan_koneksi_diam(self):
        self.connect_keys(refresh=False)
        flow = youtube.begin_connect(config.load_config())
        port = int(flow.redirect_uri.rsplit(":", 1)[1])
        idle = socket.create_connection(("127.0.0.1", port))
        try:
            time.sleep(0.2)
            flow.cancel()
            flow.thread.join(3)
            self.assertFalse(flow.thread.is_alive())
            self.assertEqual(flow.status, "batal")
        finally:
            idle.close()

    def test_batal_saat_menukar_kode_tidak_menyimpan_token(self):
        self.connect_keys(refresh=False)
        flow = youtube.begin_connect(config.load_config())
        gate, entered = threading.Event(), threading.Event()
        real = youtube.fetch_channel

        def slow(**kw):
            entered.set()
            gate.wait(5)
            return real(**kw)

        with mock.patch.object(youtube, "fetch_channel", slow):
            t = threading.Thread(target=self._browser, args=(flow, {"state": flow.state, "code": "KODE"}))
            t.start()
            self.assertTrue(entered.wait(5))
            flow.cancel()                                  # pengguna menekan Batal saat Nero menukar kode
            gate.set()
            t.join(10)
        self.assertEqual(flow.status, "batal")
        self.assertFalse(youtube.connected())

    def test_state_salah_ditolak_dan_tetap_menunggu(self):
        self.connect_keys(refresh=False)
        flow = youtube.begin_connect(config.load_config())
        code, _ = self._browser(flow, {"state": "palsu", "code": "KODE"})
        self.assertEqual(code, 400)
        self.assertEqual(flow.status, "menunggu")
        self.assertEqual(self.g.of("POST", "/token"), [])
        flow.cancel()
        self.assertEqual(flow.status, "batal")

    def test_akses_ditolak(self):
        self.connect_keys(refresh=False)
        flow = youtube.begin_connect(config.load_config())
        self._browser(flow, {"state": flow.state, "error": "access_denied"})
        self.assertTrue(flow.done.wait(5))
        self.assertEqual(flow.status, "gagal")
        self.assertIn("ditolak", flow.error)
        self.assertFalse(youtube.connected())

    def test_izin_upload_tidak_dicentang(self):
        self.connect_keys(refresh=False)
        self.g.scope = youtube.SCOPE_READONLY
        flow = youtube.begin_connect(config.load_config())
        self._browser(flow, {"state": flow.state, "code": "KODE"})
        self.assertTrue(flow.done.wait(5))
        self.assertEqual(flow.status, "gagal")
        self.assertIn("upload video", flow.error)
        self.assertFalse(youtube.connected())       # token tanpa izin upload tidak disimpan

    def test_tanpa_client_ditolak(self):
        with self.assertRaises(youtube.AuthError):
            youtube.begin_connect(config.load_config())

    def test_refresh_token_dari_env_menolak_hubungkan(self):
        self.connect_keys(refresh=False)
        with mock.patch.dict(os.environ, {"YT_REFRESH_TOKEN": "dari-env"}), self.assertRaises(youtube.AuthError):
            youtube.begin_connect(config.load_config())

    def test_izin_kedaluwarsa_ditandai(self):
        self.connect_keys()
        self.g.token_error = "invalid_grant"
        with self.assertRaises(youtube.AuthError) as e:
            youtube.access_token()
        self.assertIn("Testing", str(e.exception))
        self.assertIn("Hubungkan ulang", youtube.status()["problem"])
        self.g.token_error = ""
        youtube.access_token()
        self.assertEqual(youtube.status()["problem"], "")     # tanda dihapus setelah berhasil lagi

    def test_access_token_disimpan_di_memori(self):
        self.connect_keys()
        a = youtube.access_token()
        b = youtube.access_token()
        self.assertEqual(a, b)
        self.assertEqual(len(self.g.of("POST", "/token")), 1)
        with open(os.path.join(self.data, "channel", youtube.ACCOUNT_FILE), encoding="utf-8") as f:
            self.assertNotIn(a, f.read())

    def test_putuskan(self):
        self.connect_keys()
        self.assertTrue(youtube.disconnect())
        revoke = self.g.of("POST", "/revoke")[0]
        self.assertEqual(revoke["params"], {"token": RT})
        self.assertFalse(youtube.connected())
        self.assertEqual(youtube.account(), {})


# --------------------------------------------------------------------------
class Unggah(YTSandbox):
    def setUp(self):
        super().setUp()
        self.connect_keys()
        self.p = mock.patch.object(youtube, "CHUNK_BYTES", 1000)
        self.p.start()
        self.path = os.path.join(self.tmp, "v.mp4")
        self.data_bytes = os.urandom(2500)
        with open(self.path, "wb") as f:
            f.write(self.data_bytes)

    def tearDown(self):
        self.p.stop()
        super().tearDown()

    def body(self, privacy="private", at=None):
        meta = Metadata(title="Judul <uji>", description="Isi", tags=["a"])
        return youtube.video_body(meta, ChannelConfig(), privacy=privacy, publish_at=at)

    def test_per_potongan_dengan_content_range(self):
        uri = youtube.start_session(self.body(), 2500)
        got = []
        video = youtube.send_file(uri, self.path, progress=got.append, log=lambda m: None)
        self.assertEqual(video["id"], "VID1")
        self.assertEqual(bytes(self.g.stored), self.data_bytes)
        ranges = [c["headers"]["Content-Range"] for c in self.g.of("PUT", "SESI1")]
        self.assertEqual(ranges, ["bytes */2500", "bytes 0-999/2500", "bytes 1000-1999/2500", "bytes 2000-2499/2500"])
        self.assertEqual(got[-1], 1.0)
        start = self.g.of("POST", "/videos")[0]
        self.assertEqual(start["params"], {"uploadType": "resumable", "part": "snippet,status"})
        self.assertEqual(start["headers"]["X-Upload-Content-Length"], "2500")
        self.assertEqual(youtube.usage()["insert"], 1)

    def test_503_dicoba_ulang_dari_posisi_yang_dilaporkan(self):
        uri = youtube.start_session(self.body(), 2500)
        self.g.fail_chunks = 2
        logs = []
        video = youtube.send_file(uri, self.path, log=logs.append)
        self.assertEqual(video["id"], "VID1")
        self.assertEqual(bytes(self.g.stored), self.data_bytes)
        self.assertTrue(any("dicoba lagi" in m for m in logs))

    def test_koneksi_putus_di_tengah_potongan(self):
        uri = youtube.start_session(self.body(), 2500)
        self.g.drop_chunk = 2
        video = youtube.send_file(uri, self.path, log=lambda m: None)
        self.assertEqual(video["id"], "VID1")
        self.assertEqual(bytes(self.g.stored), self.data_bytes)   # lanjut dari byte yang benar-benar sampai

    def test_menyerah_setelah_berkali_kali(self):
        uri = youtube.start_session(self.body(), 2500)
        self.g.fail_chunks = 100
        with self.assertRaises(youtube.YouTubeError) as e:
            youtube.send_file(uri, self.path, log=lambda m: None)
        self.assertIn("Jalankan lagi", str(e.exception))

    def test_kuota_habis_pesan_jelas(self):
        self.g.insert_error = api_error(403, "quotaExceeded")
        with self.assertRaises(youtube.YouTubeError) as e:
            youtube.start_session(self.body(), 2500)
        self.assertIn("Kuota YouTube API hari ini habis", str(e.exception))

    def test_metadata_video(self):
        meta = Metadata.model_construct(title="A<b>c " + "x" * 120, language="id", description="d<e>" + "é" * 3000, category_id="",
                        tags=["satu", "dua kata", "#tiga", "satu", *[f"tag{i:03d}" for i in range(200)]])
        cfg = ChannelConfig()
        at = datetime(2026, 11, 12, 18, 0, tzinfo=timeutil.ZoneInfo("Asia/Jakarta"))
        b = youtube.video_body(meta, cfg, privacy="private", publish_at=at)
        sn, st = b["snippet"], b["status"]
        self.assertTrue(sn["title"].startswith("Abc "))
        self.assertLessEqual(len(sn["title"]), 100)
        self.assertNotIn("<", sn["description"])
        self.assertLessEqual(len(sn["description"].encode("utf-8")), 5000)
        self.assertEqual(sn["tags"][:3], ["satu", "dua kata", "tiga"])
        self.assertLessEqual(youtube._tags_length(sn["tags"]), 500)
        self.assertEqual(sn["categoryId"], "27")
        self.assertEqual(st, {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": True, "publishAt": "2026-11-12T11:00:00Z"})
        with self.assertRaises(ValueError):
            youtube.video_body(meta, cfg, privacy="public", publish_at=at)

    def test_rencana_privasi(self):
        cfg = ChannelConfig()
        now = datetime(2026, 11, 12, 9, 0, tzinfo=timeutil.ZoneInfo("Asia/Jakarta"))
        self.assertEqual(youtube.privacy_plan(cfg, needs_review=False, test=True)[:2], ("private", None))
        self.assertEqual(youtube.privacy_plan(cfg, needs_review=False, test=False)[:2], ("private", None))
        cfg.approval.mode = "review_window"
        priv, at, _ = youtube.privacy_plan(cfg, needs_review=False, test=False, now=now)
        self.assertEqual((priv, timeutil.to_rfc3339_utc(at)), ("private", "2026-11-12T11:00:00Z"))
        late = datetime(2026, 11, 12, 17, 50, tzinfo=timeutil.ZoneInfo("Asia/Jakarta"))
        _, at, _ = youtube.privacy_plan(cfg, needs_review=False, test=False, now=late)
        self.assertEqual(timeutil.to_rfc3339_utc(at), "2026-11-13T11:00:00Z")      # sudah mepet: besok
        self.assertEqual(youtube.privacy_plan(cfg, needs_review=True, test=False)[:2], ("private", None))
        cfg.approval.mode = "auto"
        self.assertEqual(youtube.privacy_plan(cfg, needs_review=False, test=False)[:2], ("public", None))
        self.assertEqual(youtube.privacy_plan(cfg, needs_review=True, test=False)[:2], ("private", None))

    def test_penilaian_status(self):
        ok = {"status": {"uploadStatus": "processed", "privacyStatus": "private"},
              "processingDetails": {"processingStatus": "succeeded"}}
        self.assertEqual(youtube.assess(ok, "private", "")[0], "siap")
        self.assertEqual(youtube.assess(None, "private", "")[0], "masalah")
        busy = {"status": {"uploadStatus": "uploaded", "privacyStatus": "private"},
                "processingDetails": {"processingStatus": "processing"}}
        self.assertEqual(youtube.assess(busy, "private", "")[0], "diproses")
        rej = {"status": {"uploadStatus": "rejected", "rejectionReason": "duplicate", "privacyStatus": "private"}}
        self.assertIn("duplikat", youtube.assess(rej, "private", "")[1])
        locked = {"status": {"uploadStatus": "processed", "privacyStatus": "private"}}
        st, note = youtube.assess(locked, "public", "")
        self.assertEqual(st, "masalah")
        self.assertIn("private", note)
        at = "2026-11-12T11:00:00Z"
        sched = {"status": {"uploadStatus": "processed", "privacyStatus": "private",
                            "publishAt": "2026-11-12T11:00:00.000Z"}}
        before = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
        self.assertEqual(youtube.assess(sched, "private", at, before)[0], "siap")
        wrong = {"status": {"uploadStatus": "processed", "privacyStatus": "private"}}
        self.assertEqual(youtube.assess(wrong, "private", at, before)[0], "masalah")   # jadwal hilang
        aired = {"status": {"uploadStatus": "processed", "privacyStatus": "public"}}
        after = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
        self.assertEqual(youtube.assess(aired, "private", at, after)[0], "siap")     # sudah tayang sesuai jadwal


# --------------------------------------------------------------------------
class SatuVideoSekali(YTSandbox):
    """publish(): dicatat segera, tidak pernah dua kali, sesi terputus dilanjutkan, video yang sudah sampai
    dikenali dari judulnya."""

    def setUp(self):
        super().setUp()
        self.connect_keys()
        self.p = mock.patch.object(youtube, "CHUNK_BYTES", 1000)
        self.p.start()
        self.ctx = runctx.RunContext(DAY, echo=None)
        os.makedirs(self.ctx.dir)
        self.path = self.ctx.path("final.mp4")
        with open(self.path, "wb") as f:
            f.write(os.urandom(2500))
        self.b = youtube.video_body(Metadata(title="Judul uji", description="Isi", tags=[]), ChannelConfig(),
                                    privacy="private", publish_at=None)

    def tearDown(self):
        self.p.stop()
        super().tearDown()

    def go(self, **kw):
        return youtube.publish(self.ctx, self.path, self.b, log=lambda m: None, verify_wait=0, **kw)

    def test_sekali_saja(self):
        res = self.go()
        self.assertEqual(res.video_id, "VID1")
        self.assertTrue(res.verified)
        self.assertEqual(res.status, "siap")
        self.assertEqual(res.studio_url, "https://studio.youtube.com/video/VID1/edit")
        self.assertFalse(os.path.exists(self.ctx.path(youtube.SESSION_FILE)))
        again = self.go()
        self.assertEqual(again.video_id, "VID1")
        self.assertEqual(len(self.g.inserted), 1)

    def test_id_dicatat_walau_verifikasi_gagal(self):
        with mock.patch.object(youtube, "fetch_video", side_effect=youtube.YouTubeError("server sibuk")):
            res = self.go()
        self.assertEqual(res.video_id, "VID1")
        self.assertFalse(res.verified)
        self.assertIn("server sibuk", res.note)
        self.assertEqual(self.ctx.load("youtube", YouTubeResult).video_id, "VID1")

    def test_melanjutkan_sesi_tersimpan(self):
        uri = youtube.start_session(self.b, 2500)
        state.write_json_atomic(self.ctx.path(youtube.SESSION_FILE),
                                {"uri": uri, "file": youtube._file_sig(self.path), "title": "Judul uji",
                                 "body": youtube._body_sig(self.b), "channel": "UC123"})
        with open(self.path, "rb") as f:
            self.g.stored = bytearray(f.read(1000))     # proses sebelumnya mati setelah 1 potongan
        res = self.go()
        self.assertEqual(res.video_id, "VID1")
        self.assertEqual(len(self.g.inserted), 1)       # tidak membuat sesi (video) baru
        with open(self.path, "rb") as f:
            self.assertEqual(bytes(self.g.stored), f.read())

    def test_sesi_kedaluwarsa_tetapi_video_sudah_sampai(self):
        state.write_json_atomic(self.ctx.path(youtube.SESSION_FILE),
                                {"uri": SESSION_URI, "file": youtube._file_sig(self.path), "title": "Judul uji"})
        self.g.gone = True
        self.g.uploads_items = [{"snippet": {"title": "Judul uji", "publishedAt": youtube._now_iso()},
                                 "contentDetails": {"videoId": "LAMA9"}}]
        logs = []
        res = youtube.publish(self.ctx, self.path, self.b, log=logs.append, verify_wait=0)
        self.assertEqual(res.video_id, "LAMA9")
        self.assertEqual(self.g.inserted, [])            # tidak diunggah ulang
        self.assertTrue(any("tidak diunggah ulang" in m for m in logs))

    def _old_session(self, body, *, stored, title="Judul uji", created=None):
        """Sesi upload yang ditinggal proses sebelumnya (isi permintaan `body`)."""
        uri = youtube.start_session(body, 2500)
        with open(self.path, "rb") as f:
            self.g.stored = bytearray(f.read(stored))
        state.write_json_atomic(self.ctx.path(youtube.SESSION_FILE), {
            "uri": uri, "file": youtube._file_sig(self.path), "title": title, "body": youtube._body_sig(body),
            "channel": "UC123", "created": created or youtube._now_iso(),
            "privacy": body["status"]["privacyStatus"], "publish_at": body["status"].get("publishAt", "")})

    def test_sesi_lama_beda_isi_yang_sudah_selesai_dipakai_apa_adanya(self):
        public = youtube.video_body(Metadata(title="Judul uji", description="Isi", tags=[]), ChannelConfig(),
                                    privacy="public", publish_at=None)
        self._old_session(public, stored=2500)            # byte terakhir sempat sampai, catatan tidak
        logs = []
        res = youtube.publish(self.ctx, self.path, self.b, log=logs.append, verify_wait=0)
        self.assertEqual(len(self.g.inserted), 1)          # tidak ada upload baru
        self.assertEqual(res.privacy, "public")            # dicatat sesuai yang benar-benar ada di YouTube
        self.assertEqual(res.status, "siap")
        self.assertTrue(any("sudah selesai" in m for m in logs), logs)

    def test_sesi_lama_beda_isi_yang_belum_selesai_ditinggal(self):
        public = youtube.video_body(Metadata(title="Judul uji", description="Isi", tags=[]), ChannelConfig(),
                                    privacy="public", publish_at=None)
        self._old_session(public, stored=1000)
        res = self.go()
        self.assertEqual(len(self.g.inserted), 2)          # sesi baru dengan isi sekarang
        self.assertEqual(self.g.inserted[-1]["status"]["privacyStatus"], "private")
        self.assertEqual(res.privacy, "private")
        with open(self.path, "rb") as f:
            self.assertEqual(bytes(self.g.stored), f.read())

    def test_sesi_kedaluwarsa_mencocokkan_judul_lama_dan_tanggal_sesi(self):
        created = (datetime.now(UTC) - timedelta(days=6)).isoformat()
        self._old_session(self.b, stored=0, title="Judul lama", created=created)
        self.g.gone = True
        when = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        self.g.uploads_items = [{"snippet": {"title": "Judul lama", "publishedAt": when},
                                 "contentDetails": {"videoId": "LAMA7"}}]
        res = self.go()
        self.assertEqual(res.video_id, "LAMA7")
        self.assertEqual(len(self.g.inserted), 1)          # hanya sesi lama; tidak diunggah ulang

    def test_batal_saat_menunggu_status_tidak_menghilangkan_catatan(self):
        ev = threading.Event()
        noted = []
        self.g.processing = 5

        def uploaded(r):
            noted.append(r.video_id)
            ev.set()                                       # pengguna menekan Batal saat menunggu YouTube

        res = youtube.publish(self.ctx, self.path, self.b, log=lambda m: None, verify_wait=300, cancel=ev,
                              source="SRC", on_uploaded=uploaded)
        self.assertEqual(noted, ["VID1"])
        self.assertEqual(res.video_id, "VID1")
        self.assertEqual(res.source, "SRC")
        self.assertEqual(self.ctx.load("youtube", YouTubeResult).status, "diproses")

    def test_galat_jaringan_saat_memperbarui_token_dicoba_ulang(self):
        real = youtube.access_token
        calls = []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise youtube.YouTubeError("Server Google tidak bisa dihubungi (ConnectionError).")
            return real(*a, **kw)

        with mock.patch.object(youtube, "access_token", flaky):
            res = self.go()
        self.assertEqual(res.video_id, "VID1")
        with open(self.path, "rb") as f:
            self.assertEqual(bytes(self.g.stored), f.read())

    def test_sesi_disimpan_bila_cek_duplikat_gagal(self):
        created = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        self._old_session(self.b, stored=2500, title="Judul lama", created=created)
        self.g.gone = True                                 # sesi kedaluwarsa; videonya sebenarnya sudah sampai
        when = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        self.g.uploads_items = [{"snippet": {"title": "Judul lama", "publishedAt": when},
                                 "contentDetails": {"videoId": "LAMA8"}}]
        self.g.fail_playlist_list = youtube.API_ATTEMPTS + 1
        with self.assertRaises(youtube.YouTubeError):
            self.go()
        sess = state.read_json(self.ctx.path(youtube.SESSION_FILE), None)
        self.assertEqual((sess["state"], sess["title"]), (youtube.SESSION_EXPIRED, "Judul lama"))
        res = self.go()                                    # dicoba lagi: judul & waktu sesi lama masih dipakai
        self.assertEqual(res.video_id, "LAMA8")
        self.assertEqual(len(self.g.inserted), 1)          # tidak pernah diunggah dua kali

    def test_galat_server_token_tidak_membuang_sesi(self):
        public = youtube.video_body(Metadata(title="Judul uji", description="Isi", tags=[]), ChannelConfig(),
                                    privacy="public", publish_at=None)
        self._old_session(public, stored=2500)
        youtube._tokens.clear()
        self.g.token_error, self.g.token_status = "server_aneh", 403
        with self.assertRaises(youtube.YouTubeError) as e:
            self.go()
        self.assertNotIsInstance(e.exception, youtube.SessionUnusable)
        sess = state.read_json(self.ctx.path(youtube.SESSION_FILE), None)
        self.assertTrue(sess["uri"])                       # sesi tetap utuh untuk dicek lagi
        self.g.token_error = ""
        res = self.go()
        self.assertEqual(res.privacy, "public")            # upload lama yang selesai dipakai, tidak diunggah ulang
        self.assertEqual(len(self.g.inserted), 1)

    def test_sesi_milik_akun_lain_ditinggal(self):
        self._old_session(self.b, stored=1000)
        self.g.reject_uri = SESSION_URI                    # 401 walau token baru
        res = self.go()
        self.assertEqual(res.video_id, "VID1")
        self.assertEqual(len(self.g.inserted), 2)          # sesi baru
        self.assertFalse(os.path.exists(self.ctx.path(youtube.SESSION_FILE)))

    def test_video_ditemukan_dicatat_sesuai_youtube(self):
        at = datetime.now(UTC) + timedelta(hours=20)
        sched = youtube.video_body(Metadata(title="Judul uji", description="Isi", tags=[]), ChannelConfig(),
                                   privacy="private", publish_at=at)
        self.g.inserted = [sched]                          # upload sebelumnya (dijadwalkan) yang tidak tercatat
        self.g.uploads_items = [{"snippet": {"title": "Judul uji", "publishedAt": youtube._now_iso()},
                                 "contentDetails": {"videoId": "LAMA5"}}]
        res = self.go()                                    # sekarang mode manual (tanpa jadwal)
        self.assertEqual(res.video_id, "LAMA5")
        self.assertEqual(res.publish_at, timeutil.to_rfc3339_utc(at))
        self.assertTrue(res.publish_at_wib.endswith("WIB"))
        self.assertTrue(res.adopted)
        self.assertEqual(res.status, "siap")
        self.assertEqual(len(self.g.inserted), 1)

    def test_kunci_upload_satu_sekaligus(self):
        with youtube._upload_guard():
            self.assertTrue(youtube.upload_active())
            with self.assertRaises(youtube.YouTubeError):
                self.go()
            with self.assertRaises(youtube.YouTubeError):
                youtube.disconnect()
            with self.assertRaises(youtube.YouTubeError):
                youtube.begin_connect(ChannelConfig())
        self.assertFalse(youtube.upload_active())
        self.assertEqual(self.go().video_id, "VID1")

    def test_channel_baru_tanpa_daftar_upload(self):
        self.g.no_uploads_playlist = True
        res = self.go()
        self.assertEqual(res.video_id, "VID1")
        self.assertEqual(len(self.g.inserted), 1)

    def test_sesi_dari_channel_lain_tidak_ditinggal_diam_diam(self):
        self._old_session(self.b, stored=2500)             # mungkin sudah sampai di channel lama
        sess = state.read_json(self.ctx.path(youtube.SESSION_FILE), None)
        state.write_json_atomic(self.ctx.path(youtube.SESSION_FILE), {**sess, "channel": "UC999",
                                                                       "channel_title": "Channel Lama"})
        with self.assertRaises(youtube.YouTubeError) as e:
            self.go()
        self.assertIn("Channel Lama", str(e.exception))
        self.assertEqual(len(self.g.inserted), 1)          # tidak ada upload baru ke channel ini

    def test_token_run_beda_dengan_token_tersimpan(self):
        settings = common.load_settings()
        config.save_keys({"yt_refresh_token": "RT-BARU-LAIN"})   # dihubungkan ulang saat run berjalan
        with self.assertRaises(youtube.YouTubeError) as e:
            youtube.publish(self.ctx, self.path, self.b, log=lambda m: None, verify_wait=0, settings=settings)
        self.assertIn("dihubungkan ulang", str(e.exception))
        self.assertEqual(self.g.inserted, [])

    def test_cek_ketat_menerima_video_uji_yang_ditayangkan(self):
        self.g.privacy_override = "public"                 # pengguna menayangkan sebelum cek pertama
        res = self.go()
        self.assertEqual(res.status, "siap")
        self.assertIn("Privasi diubah", res.note)

    def test_jadwal_ditayangkan_lebih_awal_oleh_pengguna(self):
        at = (datetime.now(UTC) + timedelta(hours=20)).isoformat()
        video = {"status": {"uploadStatus": "processed", "privacyStatus": "unlisted", "publishAt": at}}
        st, note = youtube.assess(video, "private", at)
        self.assertEqual(st, "siap")
        self.assertIn("unlisted", note)
        aired = {"status": {"uploadStatus": "processed", "privacyStatus": "public"}}
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        self.assertEqual(youtube.assess(aired, "private", past, strict=False), ("siap", ""))   # tayang sesuai jadwal

    def test_cek_ketat_yang_masih_diproses_sudah_memeriksa_privasi(self):
        self.g.processing = 5
        res = self.go()
        self.assertEqual(res.status, "diproses")
        self.assertTrue(res.strict_ok)

    def test_judul_sama_yang_sudah_lama_bukan_duplikat(self):
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        self.g.uploads_items = [{"snippet": {"title": "Judul uji", "publishedAt": old},
                                 "contentDetails": {"videoId": "LAMA9"}}]
        res = self.go()
        self.assertEqual(res.video_id, "VID1")

    def test_playlist(self):
        res = self.go(playlist_id="PL1")
        self.assertFalse(res.playlist_added)                  # izin playlist belum diberikan
        self.assertIn("playlist", res.playlist_error)
        self.assertEqual(self.g.of("POST", "/playlistItems"), [])
        youtube._save_account(scopes=[*self.g.scope.split(), youtube.SCOPE_MANAGE])
        self.g.playlist_reason = "videoAlreadyInPlaylist"
        res = youtube.add_playlist_once(self.ctx, res, "PL1")
        self.assertTrue(res.playlist_added)
        self.assertEqual(res.playlist_error, "")

    def test_menunggu_selesai_diproses(self):
        self.g.processing = 2
        with mock.patch.object(youtube, "VERIFY_INTERVAL", 0):
            res = youtube.publish(self.ctx, self.path, self.b, log=lambda m: None, verify_wait=30)
        self.assertEqual(res.status, "siap")
        self.assertEqual(len(self.g.of("GET", "/videos")), 3)


# --------------------------------------------------------------------------
class LangkahPublish(YTSandbox):
    def setUp(self):
        super().setUp()
        config.save_config({"channel": {"niche": "fakta sains", "audience": "pelajar"}})
        self.connect_keys()
        self.p = mock.patch.object(youtube, "VERIFY_WAIT", 0)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        super().tearDown()

    def history(self):
        return state.load_history()

    def test_unggah_uji_private_tanpa_jadwal_dan_masuk_riwayat(self):
        config.save_config({"approval": {"mode": "review_window"}})    # unggah uji tetap private tanpa jadwal
        self.make_video_run()
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), 0, "\n".join(out))
        body = self.g.inserted[0]
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertNotIn("publishAt", body["status"])
        self.assertEqual(body["snippet"]["title"], "Kenapa gurita punya tiga jantung?")
        res = runctx.RunContext(DAY).load("youtube", YouTubeResult)
        self.assertTrue(res.test)
        self.assertTrue(res.verified)
        h = self.history()
        self.assertEqual([(e.date, e.video_id) for e in h], [(DAY.isoformat(), "VID1")])
        # tombol ditekan lagi: tidak diunggah ulang
        out.clear()
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), 0)
        self.assertEqual(len(self.g.inserted), 1)
        self.assertTrue(any("tidak diunggah ulang" in m for m in out))

    def test_qc_gagal_tidak_diunggah(self):
        self.make_video_run(qc_pass=False)
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertTrue(any("QC belum lulus" in m for m in out), out)
        self.assertEqual(self.g.inserted, [])

    def test_render_tidak_valid_tidak_diunggah(self):
        self.make_video_run(valid=False)
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertTrue(any("validasi render" in m for m in out), out)
        self.assertEqual(self.g.inserted, [])

    def test_video_milik_skrip_lain_tidak_diunggah(self):
        self.make_video_run(other_script=True)
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertTrue(any("naskah lain" in m for m in out), out)
        self.assertEqual(self.g.inserted, [])

    def test_pengaturan_berubah_setelah_qc_tidak_diunggah(self):
        self.make_video_run()
        config.save_config({"channel": {"banned_topics": ["gurita"]}})
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertEqual(self.g.inserted, [])

    def test_run_tanpa_video(self):
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_CONFIG)

    def test_dry_run_dan_api_upload_mati_tidak_mengunggah(self):
        ctx = self.make_video_run()
        logs = []
        ctx.echo = logs.append
        self.assertFalse(pipeline.step_publish_youtube(self.make_run(ctx, dry_run=True), False))
        self.assertFalse(pipeline.step_publish_youtube(self.make_run(ctx), False))
        self.assertEqual(self.g.calls, [])
        self.assertTrue(any("uji coba" in m for m in logs))
        self.assertTrue(any("api_upload = false" in m for m in logs))

    def test_review_window_dijadwalkan_dan_diverifikasi(self):
        cfg = config.save_config({"youtube": {"api_upload": True}, "approval": {"mode": "review_window"}})
        ctx = self.make_video_run(cfg=cfg)
        run = self.make_run(ctx, cfg)
        self.assertTrue(pipeline.step_publish_youtube(run, False))
        st = self.g.inserted[0]["status"]
        self.assertEqual(st["privacyStatus"], "private")
        self.assertRegex(st["publishAt"], r"T11:00:00Z$")            # 18.00 WIB
        res = ctx.load("youtube", YouTubeResult)
        self.assertTrue(res.verified)
        self.assertTrue(res.publish_at_wib.endswith("18.00 WIB"))
        # --force tidak pernah mengunggah ulang
        self.assertFalse(pipeline.step_publish_youtube(self.make_run(ctx, cfg), True))
        self.assertEqual(len(self.g.inserted), 1)

    def test_perlu_dicek_manusia_tidak_diunggah_otomatis(self):
        cfg = config.save_config({"youtube": {"api_upload": True}, "approval": {"mode": "auto"}})
        self.make_video_run(cfg=cfg, needs_review=True)
        ctx = runctx.RunContext(DAY, echo=None)
        logs = []
        ctx.echo = logs.append
        self.assertFalse(pipeline.step_publish_youtube(self.make_run(ctx, cfg), False))
        self.assertEqual(self.g.inserted, [])
        self.assertTrue(any("tidak diunggah otomatis" in m for m in logs))
        # pengguna sudah menonton: unggah uji tetap bisa, selalu private tanpa jadwal
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), 0)
        st = self.g.inserted[0]["status"]
        self.assertEqual(st["privacyStatus"], "private")
        self.assertNotIn("publishAt", st)

    def test_dikunci_private_ditandai(self):
        cfg = config.save_config({"youtube": {"api_upload": True}, "approval": {"mode": "auto"}})
        ctx = self.make_video_run(cfg=cfg)
        self.g.privacy_override = "private"
        logs = []
        ctx.echo = logs.append
        pipeline.step_publish_youtube(self.make_run(ctx, cfg), False)
        res = ctx.load("youtube", YouTubeResult)
        self.assertEqual(res.status, "masalah")
        self.assertFalse(res.verified)
        self.assertTrue(any("PERINGATAN" in m for m in logs))

    def test_batal_saat_menunggu_status_riwayat_tetap_tercatat(self):
        self.make_video_run()
        self.g.processing = 5
        ev = threading.Event()

        def progress(step, frac):
            if frac >= 0.9:
                ev.set()                                   # Batal ditekan setelah upload selesai

        with mock.patch.object(youtube, "VERIFY_WAIT", 300):
            code = pipeline.upload_test(DAY, echo=lambda m: None, cancel=ev, on_progress=progress)
        self.assertEqual(code, 0)
        self.assertEqual([e.video_id for e in self.history()], ["VID1"])
        self.assertTrue(runctx.RunContext(DAY).load("youtube", YouTubeResult).source)

    def test_riwayat_yang_hilang_dilengkapi(self):
        ctx = self.make_video_run()
        ctx.save("youtube", YouTubeResult(video_id="VID9", privacy="private", status="siap", verified=True))
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), 0)
        self.assertEqual([e.video_id for e in self.history()], ["VID9"])
        self.assertEqual(self.g.inserted, [])

    def test_video_bukan_render_terbaru_tidak_diunggah(self):
        ctx = self.make_video_run()
        ctx.save("visuals", Visuals(segments=[], uses_stock=True))     # visual dibuat ulang, render belum
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertTrue(any("belum dibuat ulang" in m for m in out), out)
        self.assertEqual(self.g.inserted, [])
        b = pipeline.upload_block(DAY)
        self.assertIn("belum dibuat ulang", b["block"])
        self.assertTrue(b["rebuild"])

    def test_run_dengan_genre_khusus(self):
        cfg = genres.apply(config.load_config(), "fakta-sains")
        ctx = self.make_video_run(cfg=cfg)
        self.assertIn("berubah setelah diperiksa QC", pipeline.upload_block(DAY)["block"])
        ctx.set_fingerprint("genre", "fakta-sains")                    # dicatat run_daily(--genre)
        self.assertEqual(pipeline.upload_block(DAY), {"block": "", "rebuild": False, "replan": False,
                                                      "genre": "fakta-sains"})
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), 0)

    def test_banner_gagal_hilang_setelah_unggah_berhasil(self):
        ctx = self.make_video_run()
        self.g.insert_error = api_error(403, "quotaExceeded")
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), pipeline.EXIT_FAILED)
        self.assertIn("Kuota", runs.failure(ctx)["message"])
        self.g.insert_error = None
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), 0)
        self.assertIsNone(runs.failure(ctx))

    def test_naskah_lain_di_suara_tidak_diunggah(self):
        ctx = self.make_video_run()
        ctx.set_fingerprint("tts_src", "naskah-lama")
        self.assertIn("naskah lain", pipeline.upload_block(DAY)["block"])
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), pipeline.EXIT_FAILED)
        self.assertEqual(self.g.inserted, [])

    def test_deskripsi_disusun_dari_visual_video(self):
        self.make_video_run()
        self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), 0)
        desc = self.g.inserted[0]["snippet"]["description"]
        self.assertNotEqual(desc, "Deskripsi uji")
        self.assertEqual(runctx.RunContext(DAY).load("metadata", Metadata).description, desc)

    def test_video_dari_naskah_lama_dicatat_dengan_judulnya(self):
        ctx = self.make_video_run()
        run = self.make_run(ctx)
        pipeline._record_upload(run, YouTubeResult(video_id="OLD1", title="Judul video lama", source="lain"))
        h = self.history()
        self.assertEqual([(e.video_id, e.topic, e.format) for e in h], [("OLD1", "Judul video lama", "")])

    def test_cek_ulang_menerima_perubahan_privasi_pengguna(self):
        self.make_video_run()
        pipeline.upload_test(DAY, echo=lambda m: None)
        self.g.privacy_override = "public"                 # pengguna menayangkan di YouTube Studio
        res = pipeline.recheck_upload(DAY)
        self.assertEqual(res.status, "siap")
        self.assertIn("Privasi diubah", res.note)

    def test_cek_ulang_channel_lain_ditolak(self):
        self.make_video_run()
        pipeline.upload_test(DAY, echo=lambda m: None)
        youtube._save_account(channel_id="UC999", channel_title="Channel Lain")
        with self.assertRaises(youtube.YouTubeError) as e:
            pipeline.recheck_upload(DAY)
        self.assertIn("channel lain", str(e.exception))
        self.assertEqual(runctx.RunContext(DAY).load("youtube", YouTubeResult).status, "siap")   # tidak diubah

    def test_cek_ulang_melengkapi_riwayat_dan_menghapus_tanda_gagal(self):
        ctx = self.make_video_run()
        ctx.save("youtube", YouTubeResult(video_id="VID1", privacy="private", channel_id="UC123",
                                          source=runctx.sha(Script.model_validate(long_script()))))
        ctx.log("GAGAL di langkah publish_youtube: bukan JSON")
        self.assertIsNotNone(runs.failure(ctx))
        pipeline.recheck_upload(DAY)
        self.assertEqual([e.video_id for e in self.history()], ["VID1"])
        self.assertEqual(self.history()[0].topic, "Gurita tiga jantung")   # naskah sama: riwayat lengkap
        self.assertIsNone(runs.failure(ctx))

    def test_riwayat_rusak_tidak_ditimpa(self):
        path = os.path.join(self.data, "channel", "history.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[{rusak")
        self.make_video_run()
        logs = []
        self.assertEqual(pipeline.upload_test(DAY, echo=logs.append), 0)    # video tetap terunggah
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "[{rusak")                          # isi lama tidak hilang
        self.assertTrue(any("belum tersimpan" in m for m in logs), logs)

    def test_genre_hanya_dicatat_setelah_langkah_selesai(self):
        ctx = self.make_video_run()
        ctx.set_fingerprint("genre", "fakta-sains")
        code = pipeline.run_daily(DAY, dry_run=True, only="render", echo=lambda m: None)
        self.assertEqual(code, pipeline.EXIT_FAILED)       # render gagal (tanpa voice.wav): tidak ada langkah selesai
        self.assertEqual(runctx.RunContext(DAY).fingerprint("genre"), "fakta-sains")

    def test_masalah_privasi_tidak_tertutup_cek_ulang(self):
        cfg = config.save_config({"youtube": {"api_upload": True}, "approval": {"mode": "auto"}})
        ctx = self.make_video_run(cfg=cfg)
        self.g.privacy_override = "private"                # minta publik, YouTube menyimpan private
        pipeline.step_publish_youtube(self.make_run(ctx, cfg), False)
        self.assertEqual(ctx.load("youtube", YouTubeResult).status, "masalah")
        res = pipeline.recheck_upload(DAY)
        self.assertEqual(res.status, "masalah")            # tetap masalah, bukan "diubah pengguna"
        self.assertFalse(res.verified)

    def test_catatan_asal_diperbarui_saat_dipakai_ulang(self):
        ctx = self.make_video_run()
        cfg = config.load_config()
        script = Script.model_validate(long_script())
        words = ctx.load("words", Words)
        with open(ctx.path("voice.wav"), "wb") as f:
            f.write(b"RIFF")
        ctx.set_fingerprint("tts", pipeline.fp_tts(script, cfg))
        for step in ("seo", "tts"):
            ctx.set_fingerprint(f"{step}_src", "naskah-sebelumnya")     # mis. QC menulis ulang tanpa ubah narasi
        run = self.make_run(ctx, cfg, dry_run=True)
        self.assertFalse(pipeline.step_seo(run, False))
        self.assertFalse(pipeline.step_tts(run, False))
        src = runctx.sha(script)
        self.assertEqual((ctx.fingerprint("seo_src"), ctx.fingerprint("tts_src")), (src, src))
        self.assertIsNotNone(words)

    def test_genre_tidak_dicatat_langkah_unggah(self):
        self.make_video_run()
        code = pipeline.run_daily(DAY, dry_run=True, only="publish_youtube", genre="fakta-sains",
                                  echo=lambda m: None)
        self.assertEqual(code, 0)
        self.assertEqual(runctx.RunContext(DAY).fingerprint("genre"), "")
        self.assertEqual(pipeline.upload_block(DAY)["block"], "")

    def test_kategori_berubah_perlu_buat_ulang(self):
        self.make_video_run()
        config.save_config({"youtube": {"category_id": "28"}})
        b = pipeline.upload_block(DAY)
        self.assertIn("kategori", b["block"])
        self.assertTrue(b["rebuild"])
        self.assertFalse(b["replan"])

    def test_pengaturan_channel_berubah_minta_konfirmasi(self):
        ctx = self.make_video_run()
        cfg = config.load_config()
        res = ctx.load("research", Research)
        ctx.set_fingerprint("research", pipeline.fp_research(cfg))
        ctx.set_fingerprint("script", pipeline.fp_script(res, cfg))
        self.assertEqual(pipeline.upload_block(DAY)["block"], "")
        config.save_config({"channel": {"audience": "anak SMA"}})
        b = pipeline.upload_block(DAY)
        self.assertTrue(b["rebuild"])
        self.assertTrue(b["replan"])                       # membuat ulang = riset & naskah baru

    def test_cek_ulang_status(self):
        self.make_video_run()
        self.g.processing = 5
        pipeline.upload_test(DAY, echo=lambda m: None)
        self.assertEqual(runctx.RunContext(DAY).load("youtube", YouTubeResult).status, "diproses")
        self.g.processing = 0
        res = pipeline.recheck_upload(DAY)
        self.assertEqual(res.status, "siap")
        self.assertIsNone(pipeline.recheck_upload(date(2026, 1, 1)))


# --------------------------------------------------------------------------
class TrenRiset(YTSandbox):
    def cfg(self, queries):
        cfg = ChannelConfig()
        cfg.research.youtube_search_queries = queries
        return cfg

    def test_tanpa_kata_kunci_tidak_mencari(self):
        self.connect_keys()
        self.assertEqual(youtube.trending_titles(self.cfg([]), log=lambda m: None), [])
        self.assertEqual(self.g.calls, [])

    def test_belum_terhubung_dilewati(self):
        logs = []
        self.assertEqual(youtube.trending_titles(self.cfg(["sains"]), log=logs.append), [])
        self.assertTrue(any("belum dihubungkan" in m for m in logs))
        self.assertEqual(self.g.calls, [])

    def test_maks_tiga_per_hari_dan_cache(self):
        self.connect_keys()
        cfg = self.cfg(["sains", "hewan", "luar angkasa"])
        titles = youtube.trending_titles(cfg, log=lambda m: None)
        self.assertEqual(titles, ["Fakta 'unik' laut", "Gurita pintar"])
        searches = self.g.of("GET", "/search")
        self.assertEqual(len(searches), 3)
        p = searches[0]["params"]
        self.assertEqual((p["type"], p["order"], p["regionCode"], p["relevanceLanguage"], p["videoDuration"]),
                         ("video", "viewCount", "ID", "id", "short"))
        self.assertRegex(p["publishedAfter"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(youtube.trending_titles(cfg, log=lambda m: None), titles)   # dari cache hari ini
        self.assertEqual(len(self.g.of("GET", "/search")), 3)
        os.remove(youtube._trends_path())
        logs = []
        self.assertEqual(youtube.trending_titles(cfg, log=logs.append), [])        # jatah hari ini habis
        self.assertEqual(len(self.g.of("GET", "/search")), 3)
        self.assertTrue(any("batas 3" in m for m in logs))

    def test_cache_judul_dihapus_setelah_dua_hari(self):
        self.connect_keys()
        youtube.trending_titles(self.cfg(["sains"]), log=lambda m: None)
        path = youtube._trends_path()
        old = time.time() - 3 * 86400
        os.utime(path, (old, old))
        state.prune_runs(DAY)
        self.assertFalse(os.path.exists(path))

    def test_prompt_riset_memuat_tren_sebagai_petunjuk(self):
        base = {"niche": "sains", "audience": "x", "tone": "x", "recent_topics": [], "learnings": "",
                "banned_topics": ["a"], "today": "2026-11-12", "evergreen": True, "n": 5,
                "materials": [{"n": 1, "title": "t", "url": "u", "published": "", "snippet": "s"}]}
        for name in ("research", "research_queries"):
            with_trends = llm.render_prompt(name, {**base, "trends": ["Gurita pintar"]})
            self.assertIn("Gurita pintar", with_trends)
            self.assertIn("BUKAN sumber fakta", with_trends)
            self.assertNotIn("BUKAN sumber fakta", llm.render_prompt(name, {**base, "trends": []}))


# --------------------------------------------------------------------------
class HalamanYouTube(YTSandbox):
    def setUp(self):
        super().setUp()
        self.c = app.app.test_client()
        config.save_config({"channel": {"niche": "fakta sains", "audience": "pelajar"}})

    def get(self, url):
        return self.c.get(url, base_url=BASE)

    def post(self, url, body=None):
        return self.c.post(url, json=body or {}, base_url=BASE)

    def test_status_tanpa_rahasia(self):
        self.connect_keys()
        r = self.get("/api/channel/youtube")
        self.assertEqual(r.status_code, 200)
        text = r.get_data(as_text=True)
        for secret in (RT, "SEC-RAHASIA"):
            self.assertNotIn(secret, text)
        self.assertTrue(r.get_json()["connected"])
        self.assertFalse(r.get_json()["api_upload"])
        st = self.get("/api/channel/state").get_json()
        self.assertEqual(st["youtube"]["channel_title"], "Channel Uji")
        self.assertTrue({x["id"]: x for x in st["ready"]}["youtube"]["ok"])
        self.assertNotIn(RT, self.get("/api/channel/settings").get_data(as_text=True))

    def test_refresh_token_tidak_bisa_diisi_dari_pengaturan(self):
        self.post("/api/channel/settings", {"keys": {"yt_refresh_token": "SUNTIK"}})
        self.assertFalse(youtube.connected())
        self.connect_keys()
        self.post("/api/channel/settings", {"clear_keys": ["yt_refresh_token"]})
        self.assertTrue(youtube.connected())          # dihapus hanya lewat "Putuskan" (izin ikut dicabut)

    def test_hubungkan_memberi_alamat_google(self):
        r = self.post("/api/channel/youtube/connect")
        self.assertEqual(r.status_code, 400)          # client belum diisi
        self.connect_keys(refresh=False)
        r = self.post("/api/channel/youtube/connect")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["url"].startswith(youtube.AUTH_URL))
        self.assertEqual(r.get_json()["youtube"]["flow"]["status"], "menunggu")
        r = self.post("/api/channel/youtube/connect/cancel")
        self.assertEqual(r.get_json()["youtube"]["flow"]["status"], "batal")

    def test_putuskan(self):
        self.connect_keys()
        r = self.post("/api/channel/youtube/disconnect")
        self.assertTrue(r.get_json()["revoked"])
        self.assertFalse(r.get_json()["youtube"]["connected"])

    def test_unggah_uji_perlu_persetujuan_dan_koneksi(self):
        r = self.post("/api/channel/youtube/upload", {"date": DAY.isoformat()})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Persyaratan Layanan", r.get_json()["error"])
        r = self.post("/api/channel/youtube/upload", {"date": DAY.isoformat(), "confirm": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("belum dihubungkan", r.get_json()["error"])
        r = self.post("/api/channel/run", {"mode": "upload"})
        self.assertEqual(r.status_code, 400)

    def test_unggah_uji_sebagai_pekerjaan_latar(self):
        self.connect_keys()
        calls = []

        def fake(day, *, echo, cancel, on_progress):
            calls.append(day)
            on_progress("publish_youtube", 0.5)
            on_progress("publish_youtube", 1.0)
            echo("[channel] 10:00:00 selesai. unggah uji")
            return 0

        with mock.patch.object(pipeline, "upload_test", fake):
            r = self.post("/api/channel/youtube/upload", {"date": DAY.isoformat(), "confirm": True})
            self.assertEqual(r.status_code, 200, r.get_json())
            job = r.get_json()["job"]
            self.assertEqual(job["mode"], "upload")
            self.assertEqual([s["id"] for s in job["steps"]], ["publish_youtube"])
            end = time.time() + 5
            while jobs.current().running() and time.time() < end:
                time.sleep(0.02)
        self.assertEqual(jobs.current().status, "selesai")
        self.assertEqual(calls, [DAY])

    def test_detail_run_memuat_status_unggah(self):
        self.connect_keys()
        with mock.patch.object(youtube, "VERIFY_WAIT", 0):
            self.make_video_run()
            ctx = runctx.RunContext(DAY)
            for step in ("research", "script"):
                ctx.set_fingerprint(step, "x")
            d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
            self.assertTrue(d["upload_ready"])
            self.assertIsNone(d["youtube"])
            self.assertEqual(pipeline.upload_test(DAY, echo=lambda m: None), 0)
        d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
        self.assertFalse(d["upload_ready"])
        self.assertFalse(d["youtube_stale"])
        self.assertEqual(d["youtube"]["video_id"], "VID1")
        self.assertEqual(d["youtube"]["source"], runctx.sha(Script.model_validate(long_script())))
        self.assertEqual(d["uploaded"]["video_id"], "VID1")
        r = self.post(f"/api/channel/youtube/check/{DAY.isoformat()}")
        self.assertEqual(r.get_json()["youtube"]["status"], "siap")
        self.assertEqual(self.post("/api/channel/youtube/check/2026-01-01").status_code, 404)

    def test_run_lama_tanpa_catatan_asal_tidak_ditawarkan(self):
        self.connect_keys()
        ctx = self.make_video_run()
        fps = ctx.fingerprints()
        fps = {k: v for k, v in fps.items() if not k.endswith("_src")}
        fps.update({"seo": "x", "render": "x"})
        state.write_json_atomic(ctx.path(runctx.FINGERPRINTS), fps)
        d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
        self.assertTrue(d["video"])                 # video lama tetap bisa ditonton
        self.assertFalse(d["upload_ready"])         # tetapi tidak ditawarkan untuk diunggah

    def test_hubungkan_dan_putuskan_ditolak_saat_mengunggah(self):
        self.connect_keys()
        release = threading.Event()

        def slow(day, *, echo, cancel, on_progress):
            release.wait(5)
            return 0

        with mock.patch.object(pipeline, "upload_test", slow):
            r = self.post("/api/channel/youtube/upload", {"date": DAY.isoformat(), "confirm": True})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self.post("/api/channel/youtube/connect").status_code, 409)
            self.assertEqual(self.post("/api/channel/youtube/disconnect").status_code, 409)
            self.assertTrue(youtube.connected())
            release.set()
            jobs.current().thread.join(5)

    def test_alasan_belum_bisa_diunggah_tampil(self):
        self.connect_keys()
        self.make_video_run(qc_pass=False)
        d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
        self.assertFalse(d["upload_ready"])
        self.assertIn("QC belum lulus", d["upload_block"])
        self.assertFalse(d["upload_rebuild"])          # QC gagal tidak diperbaiki dengan membuat ulang video

    def test_unggah_ditolak_youtube_jadi_peringatan(self):
        self.connect_keys()
        self.make_video_run()
        self.g.upload_status = "rejected"
        with mock.patch.object(youtube, "VERIFY_WAIT", 0):
            r = self.post("/api/channel/youtube/upload", {"date": DAY.isoformat(), "confirm": True})
            self.assertEqual(r.status_code, 200, r.get_json())
            jobs.current().thread.join(10)
        snap = jobs.current().snapshot()
        self.assertEqual(snap["status"], "selesai")
        self.assertIn("ditolak", snap["warning"])

    def test_kesiapan_youtube_mengikuti_masalah_izin(self):
        self.connect_keys()
        youtube._save_account(problem="Izin YouTube sudah tidak berlaku.")
        ready = {x["id"]: x for x in self.get("/api/channel/state").get_json()["ready"]}
        self.assertFalse(ready["youtube"]["ok"])
        self.assertIn("ulang", ready["youtube"]["label"])

    def test_unggah_ditolak_saat_menghubungkan(self):
        self.connect_keys()
        self.post("/api/channel/youtube/connect")
        r = self.post("/api/channel/youtube/upload", {"date": DAY.isoformat(), "confirm": True})
        self.assertEqual(r.status_code, 409)

    def test_detail_menawarkan_buat_ulang(self):
        self.connect_keys()
        ctx = self.make_video_run()
        ctx.save("visuals", Visuals(segments=[], uses_stock=True))     # visual berubah, render belum
        d = self.get(f"/api/channel/runs/{DAY.isoformat()}").get_json()
        self.assertFalse(d["upload_ready"])
        self.assertTrue(d["upload_rebuild"])
        self.assertIn("Buat ulang video", d["upload_block"])

    def test_cli_saat_mengunggah_tanpa_traceback(self):
        from features.channel import __main__ as cli
        self.connect_keys()
        with youtube._upload_guard():
            self.assertEqual(cli.main(["youtube-disconnect"]), 1)
            self.assertEqual(cli.main(["youtube-auth"]), 1)
        self.assertTrue(youtube.connected())

    def test_cek_ulang_membaca_channel_bila_belum_tercatat(self):
        self.connect_keys()
        ctx = self.make_video_run()
        ctx.save("youtube", YouTubeResult(video_id="VID1", privacy="private", channel_id="UC777", strict_ok=True))
        youtube._save_account(channel_id="")
        r = self.post(f"/api/channel/youtube/check/{DAY.isoformat()}")
        self.assertEqual(r.status_code, 400)               # akun sekarang UC123 (dibaca dari YouTube), bukan UC777
        self.assertIn("channel lain", r.get_json()["error"])

    def test_cek_status_saat_run_berjalan_409(self):
        self.connect_keys()
        ctx = self.make_video_run()
        ctx.save("youtube", YouTubeResult(video_id="VID1", privacy="private"))
        ctx.acquire()
        try:
            self.assertEqual(self.post(f"/api/channel/youtube/check/{DAY.isoformat()}").status_code, 409)
        finally:
            ctx.release()


if __name__ == "__main__":
    unittest.main()
