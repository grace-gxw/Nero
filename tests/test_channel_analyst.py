"""Channel Otomatis, Fase I-7: agen analis (videos.list + YouTube Analytics), learnings.md, video yang diunggah
sendiri, laporan mingguan, penghapusan data performa, rute halaman, dan CLI. Google, Telegram, dan AI diganti server
palsu; tanpa internet."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import json
import os
from datetime import UTC, date, datetime, timedelta
from unittest import mock
import unittest

from tests.util import ROOT  # noqa: F401
from tests.test_channel_youtube import FakeGoogle, Resp, api_error, long_script
from tests.test_channel_notify import CHAT, TOKEN, NotifySandbox  # noqa: F401

import app
from features.channel import __main__ as cli
from features.channel import analyst, config, jobs, llm, pipeline, runctx, state, telegram, timeutil, youtube
from features.channel.schemas import (AnalystDraft, HistoryEntry, Metadata, RenderInfo, Research,
                                      Script, YouTubeResult)

BASE = "http://127.0.0.1:7860"
TODAY = timeutil.today_local("Asia/Jakarta")
PT = analyst.pt_today()
THROUGH = PT - timedelta(days=analyst.LATENCY_DAYS)


class FakeAnalytics(FakeGoogle):
    """FakeGoogle + videos.list (statistik) + YouTube Analytics reports.query."""

    def __init__(self):
        super().__init__()
        self.videos = {}             # video_id -> resource videos.list
        self.numbers = {}            # video_id -> {metrik: nilai} (7 hari pertama)
        self.through = THROUGH       # hari terakhir (PT) yang ada datanya
        self.analytics_error = None  # Resp untuk semua panggilan Analytics
        self.reject_pct = False      # 400 bila averageViewPercentage diminta
        self.channel_rows = True

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):
        params = params or {}
        is_stats = url.endswith("/videos") and method == "GET" and "statistics" in str(params.get("part", ""))
        if url != youtube.ANALYTICS_URL and not is_stats:
            return super().request(method, url, params=params, json=json, data=data, headers=headers)
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "data": data,
                           "headers": headers or {}})
        if not (headers or {}).get("Authorization", "").startswith("Bearer AT"):
            return api_error(401, "authError")
        if is_stats:
            ids = str(params["id"]).split(",")
            return Resp(200, {"items": [self.videos[i] for i in ids if i in self.videos]})
        if self.analytics_error is not None:
            return self.analytics_error
        metrics = str(params["metrics"]).split(",")
        if self.reject_pct and "averageViewPercentage" in metrics:
            return Resp(400, {"error": {"code": 400, "message": "The query is not supported.",
                                        "errors": [{"reason": "badRequest"}]}})
        end = min(date.fromisoformat(params["endDate"]), self.through)
        if params.get("dimensions") == "day":
            rows = []
            d = date.fromisoformat(params["startDate"])
            while d <= end and self.channel_rows:
                rows.append([d.isoformat(), *[10 for _ in metrics]])
                d += timedelta(days=1)
            heads = [{"name": "day", "columnType": "DIMENSION"}] + [{"name": m, "columnType": "METRIC"} for m in metrics]
            return Resp(200, {"kind": "youtubeAnalytics#resultTable", "columnHeaders": heads, "rows": rows})
        vid = str(params["filters"]).split("==", 1)[1]
        heads = [{"name": m, "columnType": "METRIC"} for m in reversed(metrics)]   # urutan kolom tidak dijamin
        nums = self.numbers.get(vid)
        body = {"kind": "youtubeAnalytics#resultTable", "columnHeaders": heads}
        if nums is not None and date.fromisoformat(params["startDate"]) <= end:
            body["rows"] = [[nums.get(m, 0) for m in reversed(metrics)]]
        return Resp(200, body)

    def analytics_calls(self):
        return [c for c in self.calls if c["url"] == youtube.ANALYTICS_URL]


class FakeAI:
    """llm.generate_json palsu untuk prompt analyst."""

    def __init__(self, points=None):
        self.calls = []
        self.points = points if points is not None else [
            {"kind": "berhasil", "text": "Format mitos vs fakta lebih banyak ditonton sampai habis.", "videos": [1, 2]},
            {"kind": "kurang", "text": "Topik luar angkasa kurang menarik penonton channel ini.", "videos": [5, 6]},
            {"kind": "saran", "text": "Pakai hook berupa pertanyaan langsung di detik pertama.", "videos": [1]},
        ]
        self.problems = []

    def __call__(self, prompt_name, variables, schema, temperature, **kw):
        self.calls.append({"prompt": prompt_name, "variables": variables, "temperature": temperature})
        llm.render_prompt(prompt_name, variables)            # variabel prompt lengkap (StrictUndefined)
        draft = AnalystDraft.model_validate({"points": self.points})
        if kw.get("check"):
            self.problems.append(kw["check"](draft))
        return draft


class AnalystSandbox(NotifySandbox):
    def setUp(self):
        super().setUp()
        self.g = FakeAnalytics()
        self.p_an = [mock.patch.object(youtube.requests, n, getattr(self.g, n)) for n in ("post", "put", "request")]
        for p in self.p_an:
            p.start()
        self.ai = FakeAI()
        self.p_ai = mock.patch.object(analyst.llm, "generate_json", self.ai)
        self.p_ai.start()
        self.logs = []

    def tearDown(self):
        self.p_ai.stop()
        for p in reversed(self.p_an):
            p.stop()
        super().tearDown()

    def add_video(self, k, published, *, engaged=100, views=300, pct=60.0, privacy="public", channel="UC123",
                  api=True, history=True, title=None, run=True):
        """Satu video Nero: folder run (naskah, judul, render), youtube.json, riwayat, dan data palsu YouTube."""
        vid = f"VID{k}"
        title = title or f"Judul video nomor {k}"
        if run:
            ctx = runctx.RunContext(published, echo=None)
            os.makedirs(ctx.dir, exist_ok=True)
            script = Script.model_validate(long_script())
            ctx.save("script", script)
            ctx.save("research", Research.model_validate({"date": published.isoformat(), "candidates": [{
                "topic": f"Topik {k}", "angle": "a", "sources": [{"title": "s", "url": "https://x/s"}]}]}))
            ctx.save("metadata", Metadata(title=title, description="d"))
            ctx.save("render", RenderInfo(width=1080, height=1920, fps=30, duration=50.0, has_audio=True, valid=True))
            if api:
                ctx.save("youtube", YouTubeResult(video_id=vid, url="u", privacy="private", channel_id=channel,
                                                  title=title, status="siap"))
        if history:
            state.upsert_history(HistoryEntry(date=published.isoformat(), topic=f"Topik {k}", title=title,
                                              format="myth_vs_fact" if k % 2 else "story", hook=f"Hook {k}?",
                                              video_id=vid, duration=50.0))
        self.g.videos[vid] = {"id": vid, "snippet": {"publishedAt": f"{published.isoformat()}T20:00:00Z",
                                                     "channelId": channel, "title": title},
                              "status": {"privacyStatus": privacy, "uploadStatus": "processed"},
                              "statistics": {"viewCount": str(views * 2), "likeCount": "7", "commentCount": "2"}}
        self.g.numbers[vid] = {"engagedViews": engaged, "views": views, "estimatedMinutesWatched": 40,
                               "averageViewDuration": 31, "averageViewPercentage": pct, "likes": 5, "comments": 1,
                               "shares": 2}
        return vid

    def mature(self, n, start=10):
        """n video yang 7 hari pertamanya sudah lengkap (tayang ≥ 10 hari lalu, waktu Pasifik)."""
        return [self.add_video(i + 1, PT - timedelta(days=start + i), engaged=100 * (i + 1)) for i in range(n)]

    def analyze(self, **kw):
        cfg = config.load_config()
        return analyst.run(cfg, None, TODAY, log=self.logs.append, on_found=pipeline._record_found, **kw)

    def share(self, on=True):
        config.save_config({"analyst": {"share_with_ai": on}})


# --------------------------------------------------------------------------
class TanpaData(AnalystSandbox):
    def test_tanpa_youtube_learnings_tetap_dibuat(self):
        code = pipeline.analyze_now(echo=self.logs.append)
        self.assertEqual(code, 0, self.logs)
        text = state.load_learnings()
        self.assertTrue(text.startswith(analyst.HEADING))
        self.assertIn("Data belum cukup", text)
        self.assertIn("YouTube belum dihubungkan", text)
        self.assertFalse(os.path.exists(state.data_path("performance.json")))
        self.assertEqual(analyst.prompt_learnings(config.load_config()), "")          # tanpa pelajaran: prompt tidak diberi apa pun

    def test_kurang_dari_lima_video(self):
        self.connect_keys()
        self.mature(3)
        perf, did = self.analyze()
        self.assertTrue(did)
        self.assertEqual((perf.mature, perf.learnings_state), (3, "data_belum_cukup"))
        self.assertEqual(self.ai.calls, [])                       # tanpa pola: AI tidak dipanggil
        self.assertIn("baru 3 video", state.load_learnings())
        v = {x.video_id: x for x in perf.videos}["VID2"]
        self.assertEqual(v.first_week.engaged_views, 200)          # kolom dipetakan dari columnHeaders
        self.assertEqual(v.first_week.avg_view_pct, 60.0)
        self.assertEqual(v.public_views, 600)
        self.assertEqual((v.title, v.format), ("Judul video nomor 2", "story"))   # label Nero dari riwayat


class TujuhHariPertama(AnalystSandbox):
    def test_rentang_dan_status(self):
        self.connect_keys()
        old = self.add_video(1, PT - timedelta(days=12))
        fresh = self.add_video(2, PT - timedelta(days=5))
        new = self.add_video(3, PT - timedelta(days=1))
        private = self.add_video(4, PT - timedelta(days=11), privacy="private")
        perf, _ = self.analyze()
        by = {v.video_id: v for v in perf.videos}
        self.assertEqual(perf.data_through, THROUGH.isoformat())
        self.assertEqual(by[old].status, "lengkap")
        self.assertEqual(by[old].window_start, (PT - timedelta(days=12)).isoformat())
        self.assertEqual(by[old].window_end, (PT - timedelta(days=6)).isoformat())
        self.assertEqual(by[fresh].status, "sementara")
        self.assertEqual(by[new].status, "belum_ada_data")
        self.assertEqual(by[private].status, "belum_tayang")
        calls = {c["params"].get("filters"): c["params"] for c in self.g.analytics_calls()}
        self.assertEqual(calls[f"video=={old}"]["endDate"], (PT - timedelta(days=6)).isoformat())
        self.assertEqual(calls[f"video=={fresh}"]["endDate"], THROUGH.isoformat())   # dipotong s.d. data terakhir
        self.assertNotIn(f"video=={new}", calls)                  # tayang setelah data terakhir: tidak ditanya
        self.assertNotIn(f"video=={private}", calls)
        for c in self.g.analytics_calls():
            self.assertEqual(c["params"]["ids"], "channel==MINE")
            self.assertNotIn("estimatedRevenue", c["params"]["metrics"])
        self.assertEqual(perf.mature, 1)
        self.assertEqual(perf.this_week.days, 7)
        self.assertEqual(perf.this_week.end, THROUGH.isoformat())
        self.assertEqual(perf.prev_week.end, (THROUGH - timedelta(days=7)).isoformat())

    def test_tanpa_baris_harian_anggap_tertunda_tiga_hari(self):
        self.connect_keys()
        self.g.channel_rows = False
        self.mature(1)
        perf, _ = self.analyze()
        self.assertEqual(perf.data_through, (PT - timedelta(days=analyst.LATENCY_DAYS)).isoformat())

    def test_kombinasi_ditolak_turun_ke_metrik_inti(self):
        self.connect_keys()
        self.g.reject_pct = True
        self.mature(2)
        perf, _ = self.analyze()
        self.assertTrue(perf.analytics)
        self.assertTrue(all(v.status == "lengkap" for v in perf.videos))
        self.assertIsNone(perf.videos[0].first_week.avg_view_pct)
        self.assertEqual(perf.videos[0].first_week.avg_view_duration, 31)

    def test_analytics_api_belum_aktif(self):
        self.connect_keys()
        self.g.analytics_error = Resp(403, {"error": {"code": 403, "message": "YouTube Analytics API has not been "
                                                      "used in project 123 before or it is disabled.",
                                                      "errors": [{"reason": "accessNotConfigured"}]}})
        self.mature(2)
        perf, did = self.analyze()
        self.assertTrue(did)
        self.assertFalse(perf.analytics)
        self.assertIn("YouTube Analytics API belum diaktifkan", perf.notes[0])
        self.assertEqual(perf.videos[0].public_views, 600)          # statistik Data API tetap terbaca
        self.assertEqual(perf.learnings_state, "data_belum_cukup")

    def test_token_env_tanpa_izin_statistik(self):
        self.connect_keys(refresh=False)
        with mock.patch.dict(os.environ, {"YT_REFRESH_TOKEN": "RT-ENV"}):
            self.assertTrue(youtube.can_analytics())                  # scope token env tidak tercatat: dicoba
            self.g.analytics_error = api_error(403, "insufficientPermissions")
            self.mature(1)
            perf, did = self.analyze()
        self.assertTrue(did)
        self.assertIn("YT_REFRESH_TOKEN", perf.notes[0])
        self.assertNotIn("Hubungkan ulang", perf.notes[0])


    def test_tanpa_izin_statistik(self):
        self.g.scope = f"{youtube.SCOPE_UPLOAD} {youtube.SCOPE_READONLY}"
        self.connect_keys()
        self.mature(2)
        perf, _ = self.analyze()
        self.assertEqual(self.g.analytics_calls(), [])
        self.assertIn("Hubungkan ulang", perf.notes[0])


class GalatSesaat(AnalystSandbox):
    def test_analytics_503_pelajaran_lama_tetap(self):
        self.connect_keys()
        self.share()
        self.mature(6)
        first, _ = self.analyze()
        before_md = state.load_learnings()
        self.g.analytics_error = Resp(503, {"error": {"code": 503, "message": "backend"}})
        perf, did = self.analyze(force=True)
        self.assertFalse(did)
        self.assertEqual(perf.updated_at, first.updated_at)           # data sebelumnya dipakai, bukan data kosong
        self.assertEqual(perf.learnings_state, "siap")
        self.assertEqual(state.load_learnings(), before_md)
        self.assertTrue(any("PERINGATAN" in m for m in self.logs))

    def test_kuota_habis_juga_sementara(self):
        self.connect_keys()
        self.mature(1)
        self.g.analytics_error = api_error(403, "quotaExceeded")
        perf, did = self.analyze()
        self.assertFalse(did)
        self.assertIsNone(perf)



class PilihVideo(AnalystSandbox):
    def test_video_hilang_dan_channel_lain_dilewati(self):
        self.connect_keys()
        self.mature(2)
        gone = self.add_video(3, PT - timedelta(days=13))
        del self.g.videos[gone]
        other = self.add_video(4, PT - timedelta(days=14), channel="UCLAIN")
        perf, _ = self.analyze()
        ids = {v.video_id for v in perf.videos}
        self.assertEqual(ids, {"VID1", "VID2"})
        self.assertNotIn(other, ids)
        self.assertTrue(any("tidak ada lagi di YouTube" in m for m in self.logs), self.logs)

    def test_video_yang_diunggah_sendiri_dikenali_dari_judul(self):
        self.connect_keys()
        day = PT - timedelta(days=11)
        vid = self.add_video(7, day, api=False, history=False, title="Kenapa <gurita> punya tiga jantung?")
        self.g.uploads_items = [{"snippet": {"title": "Kenapa gurita punya tiga jantung?",
                                             "publishedAt": f"{day.isoformat()}T21:00:00Z"},
                                 "contentDetails": {"videoId": vid, "videoPublishedAt": f"{day.isoformat()}T20:00:00Z"}}]
        perf, _ = self.analyze()
        v = {x.video_id: x for x in perf.videos}[vid]
        self.assertEqual(v.source, "manual")
        self.assertEqual(v.format, "myth_vs_fact")                  # label dari naskah run itu
        entry = {e.video_id: e for e in state.load_history()}[vid]  # masuk riwayat: dedupe topik & rotasi format
        self.assertEqual((entry.date, entry.title), (day.isoformat(), "Kenapa <gurita> punya tiga jantung?"))
        self.assertEqual(entry.topic, "Topik 7")
        perf2, _ = self.analyze(force=True)                          # tidak dicatat dua kali
        self.assertEqual(sum(1 for e in state.load_history() if e.video_id == vid), 1)
        self.assertEqual(len(perf2.videos), 1)

    def test_video_tercatat_dihapus_lalu_diunggah_ulang_sendiri(self):
        self.connect_keys()
        day = PT - timedelta(days=11)
        old = self.add_video(1, day)                                  # unggah uji, lalu dihapus di Studio
        del self.g.videos[old]
        self.add_video(9, day, run=False, history=False, title="Judul video nomor 1")
        self.g.uploads_items = [{"snippet": {"title": "Judul video nomor 1"}, "contentDetails": {"videoId": "VID9"}}]
        perf, _ = self.analyze()
        self.assertEqual([(v.video_id, v.source) for v in perf.videos], [("VID9", "manual")])

    def test_upload_nero_yang_terputus_bukan_unggahan_sendiri(self):
        self.connect_keys()
        day = PT - timedelta(days=11)
        vid = self.add_video(1, day, api=False, history=False)        # byte terakhir sampai, youtube.json belum
        ctx = runctx.RunContext(day, echo=None)
        state.write_json_atomic(ctx.path(youtube.SESSION_FILE), {"state": "aktif", "uri": "https://x/sesi"})
        self.g.uploads_items = [{"snippet": {"title": "Judul video nomor 1"}, "contentDetails": {"videoId": vid}}]
        perf, _ = self.analyze()
        self.assertEqual(perf.videos, [])
        self.assertEqual(state.load_history(), [])                    # pemulihan upload I-5 yang mencatatnya
        state.upsert_history(HistoryEntry(date=day.isoformat(), topic="t", title="t", video_id="LAIN"))
        run = pipeline.Run(ctx, config.load_config(), {}, dry_run=True)
        self.assertEqual(pipeline._manual_upload(run), "")             # sesi upload Nero: tidak diblokir

    def test_judul_kembar_tidak_dipasangkan(self):
        self.connect_keys()
        d1, d2 = PT - timedelta(days=11), PT - timedelta(days=12)
        self.add_video(1, d1, api=False, history=False, title="Judul sama persis")
        self.add_video(2, d2, api=False, history=False, title="Judul sama persis")
        self.g.uploads_items = [{"snippet": {"title": "Judul sama persis"}, "contentDetails": {"videoId": "VID1"}}]
        perf, _ = self.analyze()
        self.assertEqual(perf.videos, [])
        self.assertEqual(state.load_history(), [])
        self.assertTrue(any("lebih dari satu" in m for m in self.logs))

    def test_label_run_tidak_dipakai_bila_naskah_sudah_berganti(self):
        self.connect_keys()
        day = PT - timedelta(days=11)
        self.add_video(1, day, history=False)
        state.upsert_history(HistoryEntry(date=day.isoformat(), topic="Judul lama", title="Judul lama",
                                          video_id="VID1"))               # dicatat dari naskah sebelumnya
        perf, _ = self.analyze()
        v = perf.videos[0]
        self.assertEqual((v.title, v.format, v.hook), ("Judul lama", "", ""))


class PenjagaUnggahGanda(AnalystSandbox):
    def test_video_yang_diunggah_sendiri_tidak_diunggah_lagi(self):
        from tests.test_channel_youtube import DAY
        self.connect_keys()
        ctx = self.make_video_run()
        state.upsert_history(HistoryEntry(date=DAY.isoformat(), topic="t", title="t", video_id="MANUAL1"))
        block = pipeline.upload_block(DAY)
        self.assertIn("diunggah sendiri", block["block"])
        self.assertFalse(block["rebuild"])
        config.save_config({"enabled": True, "youtube": {"api_upload": True}})
        run = self.make_run(ctx, config.load_config())
        self.assertFalse(pipeline.step_publish_youtube(run, False))
        self.assertIn("sudah diunggah sendiri", ctx.log_tail())
        self.assertEqual(self.g.inserted, [])
        out = []
        self.assertEqual(pipeline.upload_test(DAY, echo=out.append), pipeline.EXIT_FAILED)
        self.assertEqual(self.g.inserted, [])



class Pelajaran(AnalystSandbox):
    def test_izin_ai_diubah_setelah_analisis_hari_ini(self):
        self.connect_keys()
        self.mature(6)
        self.analyze()                                                # izin mati: izin_ai
        self.share(True)
        perf, did = self.analyze()                                    # tanpa force: YouTube tidak dibaca lagi
        self.assertFalse(did)
        self.assertEqual(perf.learnings_state, "siap")
        self.assertEqual(len(self.ai.calls), 1)
        self.assertTrue(analyst.prompt_learnings(config.load_config()))
        self.share(False)                                             # izin dicabut: pelajaran tidak dipakai lagi
        self.assertEqual(analyst.prompt_learnings(config.load_config()), "")
        perf, _ = self.analyze()
        self.assertEqual((perf.learnings_state, perf.learnings), ("izin_ai", []))
        self.assertNotIn("- ", state.load_learnings())

    def test_izin_ai_mati(self):
        self.connect_keys()
        self.mature(6)
        perf, _ = self.analyze()
        self.assertEqual(perf.learnings_state, "izin_ai")
        self.assertEqual(self.ai.calls, [])
        self.assertIn("izin mengirim statistik video ke AI", state.load_learnings())
        self.assertEqual(analyst.prompt_learnings(config.load_config()), "")

    def test_ai_merangkum_dengan_bukti_yang_dicek(self):
        self.connect_keys()
        self.share()
        self.ai.points += [
            {"kind": "berhasil", "text": "Satu video saja tidak boleh jadi pola umum.", "videos": [3]},
            {"kind": "kurang", "text": "Video dengan 1.234 views kurang ramai.", "videos": [3, 4]},
            {"kind": "saran", "text": "Nomor video yang tidak ada di data dibuang.", "videos": [99]},
        ]
        self.mature(6)
        perf, _ = self.analyze()
        self.assertEqual(perf.learnings_state, "siap")
        texts = [p.text for p in perf.learnings]
        self.assertEqual(len(texts), 3, texts)
        self.assertEqual(perf.learnings[0].video_ids, ["VID6", "VID5"])  # nomor urut tanggal → ID video
        self.assertTrue(self.ai.problems[0])                          # AI diminta memperbaiki sekali
        call = self.ai.calls[0]
        self.assertEqual(call["prompt"], "analyst")
        self.assertEqual(call["temperature"], config.load_config().llm.temperature_strict)
        sent = json.dumps(call["variables"], ensure_ascii=False)
        for secret in ("VID", "UC123", "Channel Uji"):              # tanpa ID video/channel
            self.assertNotIn(secret, sent)
        md = state.load_learnings()
        self.assertIn("## Yang berhasil", md)
        self.assertIn("## Saran untuk video berikutnya", md)
        prompt = analyst.prompt_learnings(config.load_config())
        self.assertIn("- Format mitos vs fakta", prompt)
        self.assertNotIn(analyst.HEADING, prompt)
        # data sama: AI tidak dipanggil lagi
        self.analyze(force=True)
        self.assertEqual(len(self.ai.calls), 1)
        # pelajaran masuk prompt riset & naskah lewat pipeline
        self.assertEqual(pipeline.analyst.prompt_learnings(config.load_config()), prompt)

    def test_ai_gagal_pelajaran_lama_tetap(self):
        self.connect_keys()
        self.share()
        self.mature(6)
        self.analyze()
        before = state.load_learnings()
        self.g.numbers["VID1"]["engagedViews"] = 999                 # data berubah: AI dipanggil lagi, lalu gagal
        with mock.patch.object(analyst.llm, "generate_json", side_effect=llm.LLMError("semua penuh", [])):
            perf, _ = self.analyze(force=True)
        self.assertEqual(perf.learnings_state, "gagal")
        self.assertTrue(perf.learnings)
        self.assertEqual(state.load_learnings().split("\n", 3)[3], before.split("\n", 3)[3])

    def test_prompt_analis_lengkap(self):
        self.connect_keys()
        self.mature(5)
        perf, _ = self.analyze()
        variables, numbered = analyst.ai_payload(perf)
        text = llm.render_prompt("analyst", {**variables, "niche": "sains", "audience": "pelajar",
                                             "banned_topics": ["SARA"], "min_evidence": 2})
        self.assertIn("[1] tayang", text)
        self.assertIn("engaged views", text)
        self.assertIn("ATURAN KONTEN", text)                          # _guardrails ikut
        self.assertEqual(sorted(numbered), [1, 2, 3, 4, 5])


class Kebijakan(AnalystSandbox):
    def test_izin_dicabut_data_dihapus(self):
        self.connect_keys()
        self.mature(2)
        self.analyze()
        self.assertTrue(os.path.exists(state.data_path("performance.json")))
        youtube._tokens.clear()
        self.g.token_error = "invalid_grant"
        perf, did = self.analyze(force=True)
        self.assertTrue(did)
        self.assertFalse(os.path.exists(state.data_path("performance.json")))
        self.assertIn("izin YouTube tidak berlaku", state.load_learnings())
        self.assertEqual(perf.learnings_state, "belum_terhubung")

    def test_putuskan_menghapus_data_performa(self):
        self.connect_keys()
        self.mature(2)
        self.analyze()
        state.save_weekly({"key": "2026-11-09"})
        youtube.disconnect()
        for name in state.PERFORMANCE_FILES:
            self.assertFalse(os.path.exists(state.data_path(name)), name)
        self.assertTrue(state.load_history())                        # riwayat (penjaga dedupe/upload) tetap
        self.assertEqual(state.load_weekly()["key"], "2026-11-09")    # tanggal laporan tetap: tidak terkirim dua kali

    def test_putuskan_ditolak_saat_analis_berjalan(self):
        self.connect_keys()
        with analyst.guard(), self.assertRaises(youtube.YouTubeError):
            youtube.disconnect()
        self.assertTrue(youtube.connected())

    def test_invalid_grant_dari_jalur_lain_menghapus_data(self):
        self.connect_keys()
        self.mature(1)
        self.analyze()
        youtube._tokens.clear()
        self.g.token_error = "invalid_grant"
        with self.assertRaises(youtube.AuthError):
            youtube.fetch_video("VID1")                               # mis. Cek status / Unggah uji
        self.assertFalse(os.path.exists(state.data_path("performance.json")))
        self.assertIsNone(analyst.load())

    def test_channel_lain_data_lama_dihapus(self):
        self.connect_keys()
        self.mature(1)
        self.analyze()
        youtube._save_account(channel_id="UCLAIN")                   # dihubungkan ulang ke channel lain
        self.assertIsNone(analyst.load())
        self.assertFalse(os.path.exists(state.data_path("performance.json")))

    def test_izin_tidak_dicek_tiga_puluh_hari_dihapus(self):
        self.connect_keys()
        self.mature(1)
        perf, _ = self.analyze()
        old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        state.save_performance({**perf.model_dump(mode="json"), "auth_verified_at": old})
        self.assertIsNone(analyst.load())
        self.assertFalse(os.path.exists(state.data_path("performance.json")))

    def test_judul_dari_youtube_tidak_disimpan(self):
        self.connect_keys()
        self.add_video(1, PT - timedelta(days=11))
        self.g.videos["VID1"]["snippet"]["title"] = "Judul diganti di YouTube Studio"
        self.analyze()
        raw = state.read_text(state.data_path("performance.json"))
        self.assertNotIn("diganti di YouTube Studio", raw)
        self.assertIn("Judul video nomor 1", raw)

    def test_peringkat_satu_metrik_mentah(self):
        self.connect_keys()
        self.mature(7)
        perf, _ = self.analyze()
        top, bottom = analyst.ranking(perf)
        self.assertEqual([v.video_id for v in top], ["VID7", "VID6", "VID5"])
        self.assertEqual([v.video_id for v in bottom], ["VID1", "VID2", "VID3"])
        self.assertFalse({v.video_id for v in top} & {v.video_id for v in bottom})


class CacheDanGalat(AnalystSandbox):
    def test_sekali_sehari(self):
        self.connect_keys()
        self.mature(1)
        self.analyze()
        n = len(self.g.calls)
        _perf, did = self.analyze()
        self.assertFalse(did)
        self.assertEqual(len(self.g.calls), n)
        self.assertTrue(any("lewati analyst" in m for m in self.logs))

    def test_youtube_tidak_terjangkau_tidak_menggagalkan_run(self):
        self.connect_keys()
        self.mature(1)
        self.analyze()
        youtube._tokens.clear()
        with mock.patch.object(self.g, "_token", side_effect=youtube.requests.ConnectionError("putus")):
            perf, did = self.analyze(force=True)
        self.assertFalse(did)
        self.assertIsNotNone(perf)                                   # data sebelumnya tetap dipakai
        self.assertTrue(any("PERINGATAN" in m for m in self.logs))

    def test_langkah_analyst_di_run_harian(self):
        self.connect_keys()
        self.mature(1)
        cfg = config.load_config()
        ctx = runctx.RunContext(TODAY, echo=None)
        run = pipeline.Run(ctx, cfg, {}, dry_run=True)
        run.settings = __import__("common").load_settings()
        with mock.patch.object(analyst, "run", side_effect=OSError("disk penuh")):
            self.assertFalse(pipeline.step_analyst(run, False))     # galat apa pun: peringatan, run lanjut
        self.assertIn("PERINGATAN", ctx.log_tail())

    def test_analis_sibuk(self):
        with analyst.guard():
            self.assertEqual(pipeline.analyze_now(echo=self.logs.append), pipeline.EXIT_LOCKED)
            self.assertTrue(analyst.running())
        self.assertFalse(analyst.running())


class LaporanMingguan(AnalystSandbox):
    def cfg(self, day):
        config.save_config({"notify": {"weekly_report_day": day}})
        return config.load_config()

    def test_kunci_minggu(self):
        cfg = self.cfg("monday")
        mon = date(2026, 11, 9)
        self.assertEqual(analyst.weekly_key(cfg, mon), "2026-11-09")
        self.assertEqual(analyst.weekly_key(cfg, date(2026, 11, 13)), "2026-11-09")   # Jumat: dikejar
        self.assertEqual(analyst.weekly_key(cfg, date(2026, 11, 16)), "2026-11-16")
        self.assertTrue(analyst.weekly_due(cfg, mon))
        analyst.mark_weekly(cfg, mon)
        self.assertFalse(analyst.weekly_due(cfg, date(2026, 11, 15)))
        self.assertTrue(analyst.weekly_due(cfg, date(2026, 11, 16)))

    def test_ganti_hari_laporan_tidak_mengirim_ulang(self):
        cfg = self.cfg("monday")
        analyst.mark_weekly(cfg, date(2026, 11, 9))                   # Senin terkirim
        cfg = self.cfg("thursday")
        self.assertFalse(analyst.weekly_due(cfg, date(2026, 11, 10)))  # Kamis lalu < Senin yang baru terkirim
        self.assertTrue(analyst.weekly_due(cfg, date(2026, 11, 12)))   # Kamis ini: laporan berikutnya

    def test_galat_sesaat_tidak_menandai_minggu(self):
        self.connect_keys()
        self.tg_keys()
        self.cfg(config.DAYS[TODAY.weekday()])
        youtube._tokens.clear()
        with mock.patch.object(self.g, "_token", side_effect=youtube.requests.ConnectionError("putus")):
            ctx = self.run_analyst(dry_run=False)
        self.assertIn("dicoba lagi di run berikutnya", ctx.log_tail())
        self.assertTrue(analyst.weekly_due(config.load_config(), TODAY))
        self.assertEqual(self.t.of("sendMessage"), [])

    def test_minggu_pembanding_tidak_lengkap(self):
        self.connect_keys()
        self.g.through = PT - timedelta(days=20)                        # data sangat tertunda
        self.mature(1, start=25)
        perf, _ = self.analyze()
        self.assertIsNone(perf.prev_week)
        self.assertFalse(analyst.page_data(config.load_config(), TODAY)["comparable"])
        trend = telegram.plain(analyst.weekly_text(perf)).split("Tren", 1)[1].split("Teratas")[0]
        self.assertNotIn("%", trend)

    def test_isi_laporan(self):
        self.connect_keys()
        self.mature(7)
        perf, _ = self.analyze()
        text = analyst.weekly_text(perf)
        plain = telegram.plain(text)
        self.assertIn("Teratas", plain)
        self.assertIn("Terbawah", plain)
        self.assertIn("engaged views 7 hari pertama", plain)
        self.assertIn("Tren seluruh channel", plain)
        self.assertIn("waktu Pasifik", plain)
        self.assertIn("angka dari YouTube", plain)
        self.assertIn("https://www.youtube.com/watch?v=VID7", text)
        self.assertLessEqual(telegram.utf16_len(text), telegram.TEXT_LIMIT)

    def run_analyst(self, dry_run):
        cfg = config.load_config()
        ctx = runctx.RunContext(TODAY, echo=None)
        run = pipeline.Run(ctx, cfg, __import__("common").load_settings(), dry_run=dry_run, alerts=not dry_run)
        pipeline.step_analyst(run, True)
        return ctx

    def test_terkirim_sekali_per_minggu(self):
        self.connect_keys()
        self.tg_keys()
        self.mature(3)
        self.cfg(config.DAYS[TODAY.weekday()])
        ctx = self.run_analyst(dry_run=True)                          # uji coba: hanya dicatat
        self.assertEqual(self.t.of("sendMessage"), [])
        self.assertIn("laporan mingguan (uji coba)", ctx.log_tail())
        for stat in ("engaged views", "Teratas", "Pelanggan"):        # statistik tidak ditulis ke run.log
            self.assertNotIn(stat, ctx.log_tail())
        self.run_analyst(dry_run=False)
        self.assertEqual(len(self.t.of("sendMessage")), 1)
        self.assertIn("Laporan mingguan", self.t.of("sendMessage")[0]["data"]["text"])
        self.run_analyst(dry_run=False)                               # minggu yang sama: tidak dikirim lagi
        self.assertEqual(len(self.t.of("sendMessage")), 1)

    def test_gagal_kirim_dicoba_lagi(self):
        self.connect_keys()
        self.tg_keys()
        self.mature(1)
        self.cfg(config.DAYS[TODAY.weekday()])
        self.t.fail["sendMessage"] = [(400, {"ok": False, "description": "Bad Request: chat not found"})]
        ctx = self.run_analyst(dry_run=False)
        self.assertIn("laporan mingguan: PERINGATAN", ctx.log_tail())
        self.assertTrue(analyst.weekly_due(config.load_config(), TODAY))
        self.run_analyst(dry_run=False)
        self.assertFalse(analyst.weekly_due(config.load_config(), TODAY))

    def test_kirim_sekarang_menandai_minggu_ini(self):
        self.connect_keys()
        self.tg_keys()
        self.mature(2)
        self.cfg(config.DAYS[TODAY.weekday()])
        self.assertEqual(pipeline.weekly_report_now(echo=self.logs.append), 0, self.logs)
        self.assertEqual(len(self.t.of("sendMessage")), 1)
        self.assertFalse(analyst.weekly_due(config.load_config(), TODAY))
        self.assertNotIn(TOKEN, "\n".join(self.logs))

    def test_kirim_sekarang_galat_sesaat_alasannya_jelas(self):
        self.connect_keys()
        self.tg_keys()
        self.mature(1)
        self.g.analytics_error = Resp(503, {"error": {"code": 503, "message": "backend"}})
        self.assertEqual(pipeline.weekly_report_now(echo=self.logs.append), pipeline.EXIT_FAILED)
        failed = [m for m in self.logs if "laporan mingguan gagal" in m]
        self.assertTrue(failed)
        self.assertNotIn("belum ada video", failed[0])
        self.assertIn("HTTP 503", failed[0])


    def test_kirim_sekarang_tanpa_video(self):
        self.tg_keys()
        self.assertEqual(pipeline.weekly_report_now(echo=self.logs.append), pipeline.EXIT_FAILED)
        self.assertTrue(any("laporan mingguan gagal" in m for m in self.logs))
        self.assertEqual(self.t.of("sendMessage"), [])


class HalamanAnalitik(AnalystSandbox):
    def setUp(self):
        super().setUp()
        self.c = app.app.test_client()

    def post(self, url, body=None):
        return self.c.post(url, json=body or {}, base_url=BASE)

    def get(self, url):
        return self.c.get(url, base_url=BASE)

    def wait(self):
        job = jobs.current()
        job.thread.join(15)
        return job.snapshot()

    def test_perbarui_lalu_tampil(self):
        self.connect_keys()
        self.mature(2)
        r = self.post("/api/channel/analytics/run")
        self.assertEqual(r.status_code, 200, r.get_json())
        snap = self.wait()
        self.assertEqual(snap["status"], "selesai", snap)
        self.assertEqual(snap["steps"][0]["id"], "analyst")
        data = self.get("/api/channel/analytics").get_json()
        self.assertEqual(len(data["performance"]["videos"]), 2)
        self.assertIn("Data belum cukup", data["learnings"])
        self.assertFalse(data["share_with_ai"])
        self.assertEqual(data["weekly"]["day"], "monday")
        self.assertNotIn(youtube.requests.__name__, json.dumps(data))
        self.assertNotIn("RT-RAHASIA", self.get("/api/channel/analytics").get_data(as_text=True))
        self.assertEqual(self.post("/api/channel/run", {"mode": "analyst"}).status_code, 400)

    def test_peringatan_analytics_di_pekerjaan(self):
        self.connect_keys()
        self.g.analytics_error = api_error(403, "accessNotConfigured")
        self.mature(1)
        self.post("/api/channel/analytics/run")
        snap = self.wait()
        self.assertEqual(snap["status"], "selesai")
        self.assertIn("YouTube Analytics API", snap["warning"])

    def test_gagal_tampil_di_pekerjaan(self):
        self.connect_keys()
        self.mature(1)
        youtube._tokens.clear()
        with mock.patch.object(self.g, "_token", side_effect=youtube.requests.ConnectionError("putus")):
            self.post("/api/channel/analytics/run")
            snap = self.wait()
        self.assertEqual(snap["status"], "gagal")
        self.assertIn("tidak bisa dihubungi", snap["error"])

    def test_tanpa_youtube_pekerjaan_memberi_peringatan(self):
        self.post("/api/channel/analytics/run")
        snap = self.wait()
        self.assertEqual(snap["status"], "selesai")
        self.assertIn("YouTube belum dihubungkan", snap["warning"])


    def test_hapus_data(self):
        self.connect_keys()
        self.mature(1)
        self.analyze()
        r = self.post("/api/channel/analytics/clear")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.get_json()["removed"], 2)
        self.assertIsNone(self.get("/api/channel/analytics").get_json()["performance"])

    def test_laporan_butuh_telegram(self):
        self.assertEqual(self.post("/api/channel/analytics/report").status_code, 400)
        self.tg_keys()
        self.connect_keys()
        self.mature(1)
        r = self.post("/api/channel/analytics/report")
        self.assertEqual(r.status_code, 200)
        snap = self.wait()
        self.assertEqual(snap["status"], "selesai", snap)
        self.assertEqual(snap["steps"][0]["id"], "weekly_report")
        self.assertEqual(len(self.t.of("sendMessage")), 1)

    def test_izin_ai_lewat_pengaturan(self):
        r = self.post("/api/channel/settings", {"config": {"analyst": {"share_with_ai": True}}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(config.load_config().analyst.share_with_ai)
        self.assertTrue(r.get_json()["config"]["analyst"]["share_with_ai"])


class PerintahCLI(AnalystSandbox):
    def test_analyst_dan_laporan(self):
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(str(x) for x in a))):
            self.assertEqual(cli.main(["analyst"]), 0)
            self.assertEqual(cli.main(["weekly-report"]), 1)          # belum ada data performa
            self.connect_keys()
            self.mature(1)
            self.assertEqual(cli.main(["analyst"]), 0)
            self.assertEqual(cli.main(["weekly-report"]), 0)
            self.assertEqual(cli.main(["analytics-clear"]), 0)
        text = "\n".join(out)
        self.assertIn("Pelajaran performa channel", text)
        self.assertIn("Laporan mingguan", text)
        self.assertIn("berkas data performa dihapus", text)
        self.assertFalse(os.path.exists(state.data_path("performance.json")))


if __name__ == "__main__":
    unittest.main()
