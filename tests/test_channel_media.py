"""Channel Otomatis, Fase I-3: suara (TTS + timing kata), visual (stok, gambar AI, motion graphic, cadangan),
render, dan pipeline 'media'. Tanpa jaringan: edge-tts, Pexels/Pixabay, dan Cloudflare di-mock. FFmpeg sungguhan
dipakai supaya ukuran klip, jumlah frame, dan validasi video benar-benar teruji."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import asyncio
import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from datetime import UTC, date, datetime
from unittest import mock
import unittest

import numpy as np
from PIL import Image

from tests.util import ROOT  # noqa: F401
from tests.test_channel_core import ChannelSandbox
from tests.test_channel_plan import SEO, FakeLLM, cand, draft, long_script, materials, qc_answer

from features.clip import api as clip_api
from features.clip import engine
from features.channel import (ai_images, config, genres, graphics, llm, media, pipeline, render, research, stock,
                              visuals, voice, writer)
from features.channel.config import ChannelConfig
from features.channel.schemas import (Metadata, RenderInfo, Script, SegmentTiming, SegmentVisual, Visuals, Words,
                                      WordTiming)

DAY = date(2026, 11, 12)


# --------------------------------------------------------------------------
# Pembantu
# --------------------------------------------------------------------------
def tone_samples(seconds: float, rate: int = 24000, lead: float = 0.1, tail: float = 0.1) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    body = (np.sin(2 * np.pi * 220 * t) * 9000).astype(np.int16)
    return np.concatenate([np.zeros(int(lead * rate), np.int16), body, np.zeros(int(tail * rate), np.int16)])


def wav_bytes(samples: np.ndarray, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class FakeEdge:
    """Pengganti voice.edge_synthesize: nada sinus sepanjang jumlah kata, plus WordBoundary tanpa tanda baca."""

    def __init__(self, spw: float = 0.05, marks: bool = True):
        self.spw, self.marks, self.rates = spw, marks, []

    def __call__(self, texts, voice_name, rate, *, log=print, cancel=None, progress=None):
        self.rates.append(rate)
        out = []
        for text in texts:
            toks = text.split()
            m = [(re.sub(r"[^\w-]", "", w), 0.1 + i * self.spw, self.spw * 0.9) for i, w in enumerate(toks)]
            out.append((wav_bytes(tone_samples(len(toks) * self.spw)), m if self.marks else []))
        return out


def count_frames(path: str) -> int:
    r = subprocess.run([engine.ffmpeg_exe(), "-hide_banner", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "null",
                        "-"], check=False, capture_output=True, **engine._popen_kwargs())
    found = re.findall(r"frame=\s*(\d+)", r.stderr.decode("utf-8", "ignore"))
    return int(found[-1]) if found else 0


def make_video(path: str, seconds: float, size: str = "1080x1920", audio: bool = True, fps: int = 30) -> str:
    args = ["-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "aac", "-shortest"]
    engine.run_ffmpeg([*args, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path])
    return path


def cfg_test(**video) -> ChannelConfig:
    cfg = genres.apply(ChannelConfig(), "fakta-sains")
    if video:
        cfg = cfg.model_copy(update={"video": cfg.video.model_copy(update=video)})
    return cfg


def two_segment_script(**seg2) -> Script:
    s2 = {"id": 2, "text": "kata kedua di sini", "visual_type": "motion_graphic", "visual_query": "ocean",
          "graphic": {"template": "big_number", "content": "-272 °C"}, "on_screen_text": "Suhu ekstrem"}
    s2.update(seg2)
    return Script.model_validate({
        "topic": "t", "format": "explainer", "hook": "Hook singkat sekali", "cta": "Ikuti ya",
        "segments": [{"id": 1, "text": "kata pertama", "visual_type": "stock", "visual_query": "ocean waves aerial",
                      "on_screen_text": "Laut dalam"}, s2],
        "claims": []})


def words_for(script: Script, piece: float = 0.6, pause: float = 0.3) -> Words:
    """Timing palsu: tiap bagian narasi `piece` detik, dipisah jeda."""
    t, segs, ws = 0.0, [], []
    for k, (sid, text) in enumerate(script.narration()):
        if k:
            t += pause
        toks = text.split()
        for i, w in enumerate(toks):
            ws.append(WordTiming(word=w, start=round(t + i * piece / len(toks), 3),
                                 end=round(t + (i + 1) * piece / len(toks), 3), segment_id=sid))
        segs.append(SegmentTiming(segment_id=sid, start=round(t, 3), speech_start=round(t, 3), end=round(t + piece, 3)))
        t += piece
    return Words(words=ws, duration=round(t, 3), segments=segs)


def png_image(seed: int = 1, w: int = 96, h: int = 168) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), "RGB")


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nero-media-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)


# --------------------------------------------------------------------------
# Suara
# --------------------------------------------------------------------------
class Suara(TempDir):
    def test_tanda_baca_dan_token_gabungan(self):
        text = "Dalam penelitian pada September 2007, tardigrada hidup di suhu -272 °C!"
        marks = [("Dalam", 0.1, 0.2), ("penelitian", 0.3, 0.2), ("pada", 0.5, 0.1), ("September 2007", 0.6, 0.8),
                 ("tardigrada", 1.4, 0.3), ("hidup", 1.7, 0.2), ("di", 1.9, 0.1), ("suhu", 2.0, 0.2),
                 ("-272 °C", 2.2, 1.0)]
        got = [w for w, _, _ in voice.attach_punctuation(text, marks)]
        self.assertEqual(got, ["Dalam", "penelitian", "pada", "September 2007,", "tardigrada", "hidup", "di", "suhu",
                               "-272 °C!"])

    def test_susun_jeda_dan_timing(self):
        pieces = []
        for sid, n in ((0, 3), (1, 4), (-1, 2)):
            text = " ".join(f"kata{i}" for i in range(n))
            marks = [(f"kata{i}", 0.1 + i * 0.2, 0.18) for i in range(n)]
            pieces.append((sid, text, tone_samples(n * 0.2, lead=0.1, tail=0.4), marks))
        samples, timings, words = voice.assemble(pieces, 0.3)
        self.assertEqual([t.segment_id for t in timings], [0, 1, -1])
        self.assertEqual(timings[0].start, 0)
        # jeda setelah hook dan sebelum CTA 1,3× lebih panjang
        self.assertAlmostEqual(timings[1].start - timings[0].end, 0.39, places=2)
        self.assertAlmostEqual(timings[2].start - timings[1].end, 0.39, places=2)
        self.assertAlmostEqual(samples.size / 24000, timings[-1].end, places=2)
        # hening panjang di ujung potongan dipangkas (0,4 dtk -> ±TAIL)
        self.assertLess(timings[0].end - timings[0].start, 3 * 0.2 + 0.1 + 0.25)
        self.assertEqual(len(words), 9)
        for w in words:
            t = next(x for x in timings if x.segment_id == w.segment_id)
            self.assertGreaterEqual(w.start, t.start)
            self.assertLessEqual(w.end, t.end + 1e-6)
        self.assertEqual(sorted(w.start for w in words), [w.start for w in words])

    def test_sintesis_menulis_berkas(self):
        script = Script.model_validate(long_script())
        edge = FakeEdge()
        cfg = cfg_test(target_seconds=(1, 60))
        with mock.patch.object(voice, "edge_synthesize", edge):
            w = voice.synthesize(script, cfg, self.tmp, log=lambda m: None)
        self.assertEqual(edge.rates, ["+5%"])
        for name in ("voice.wav", "voice.mp3"):
            self.assertTrue(os.path.getsize(self.p(name)) > 0, name)
        self.assertFalse(os.path.exists(self.p("tts")))
        self.assertEqual([t.segment_id for t in w.segments], [0, 1, 2, 3, 4, 5, 6, -1])
        self.assertEqual(len(w.words), script.word_count)
        self.assertEqual(w.source, "edge")
        self.assertAlmostEqual(media.wav_duration(self.p("voice.wav")), w.duration, places=2)
        self.assertEqual(w.voice, cfg.tts.voice)

    def test_terlalu_panjang_rate_naik_lalu_toolong(self):
        script = Script.model_validate(long_script())
        edge = FakeEdge(spw=0.3)
        with mock.patch.object(voice, "edge_synthesize", edge), self.assertRaises(voice.TooLong) as e:
            voice.synthesize(script, cfg_test(target_seconds=(5, 20)), self.tmp, log=lambda m: None)
        self.assertEqual(edge.rates, ["+5%", "+10%", "+15%"])
        self.assertGreater(e.exception.seconds, 20)

    def test_terlalu_pendek_hanya_peringatan(self):
        script = Script.model_validate(long_script())
        with mock.patch.object(voice, "edge_synthesize", FakeEdge()):
            w = voice.synthesize(script, cfg_test(target_seconds=(45, 58)), self.tmp, log=lambda m: None)
        self.assertTrue(w.warnings and "di bawah" in w.warnings[0])

    def test_timing_cadangan_whisper(self):
        script = two_segment_script()
        seen = {}

        def fake_transcribe(path, model_size="small", language=None, progress=None):
            seen.update(path=path, model=model_size, lang=language)
            return [{"start": 0.0, "end": 9.0, "text": "x",
                     "words": [{"start": 0.2, "end": 0.4, "word": "Hook"}, {"start": 3.0, "end": 3.2, "word": "akhir"}]}]

        with mock.patch.object(voice, "edge_synthesize", FakeEdge(marks=False)), \
                mock.patch.object(engine, "transcribe", fake_transcribe):
            w = voice.synthesize(script, cfg_test(target_seconds=(1, 60)), self.tmp, log=lambda m: None)
        self.assertEqual(w.source, "whisper")
        self.assertEqual((seen["model"], seen["lang"]), ("base", "id"))
        self.assertTrue(seen["path"].endswith(".16k.wav"))         # transcribe() butuh 16 kHz
        self.assertEqual([x.segment_id for x in w.words], [0, -1])   # 3,0 dtk lewat akhir: ikut bagian terakhir

    def test_whisper_kata_di_jeda_ikut_bagian_sebelumnya(self):
        timings = [SegmentTiming(segment_id=0, start=0, speech_start=0, end=1.0),
                   SegmentTiming(segment_id=1, start=1.3, speech_start=1.3, end=2.0)]
        segs = [{"words": [{"start": 0.95, "end": 1.15, "word": "pada"}, {"start": 1.4, "end": 1.6, "word": "x"}]}]
        with mock.patch.object(engine, "extract_audio"), mock.patch.object(engine, "transcribe", return_value=segs):
            got = voice.whisper_words(self.p("v.wav"), timings, lambda m: None)
        self.assertEqual([w.segment_id for w in got], [0, 1])

    def test_piper_belum_tersedia(self):
        cfg = cfg_test()
        cfg = cfg.model_copy(update={"tts": cfg.tts.model_copy(update={"engine": "piper"})})
        with self.assertRaises(voice.TTSError):
            voice.synthesize(two_segment_script(), cfg, self.tmp, log=lambda m: None)

    def test_edge_coba_ulang_ipv4_lalu_jalur_biasa(self):
        calls = []

        async def stream(text, voice_name, rate, ipv4, cancel):
            calls.append(ipv4)
            if len(calls) == 1:
                raise ConnectionResetError("WinError 64")
            return b"audio", [("halo", 0.1, 0.2)]

        with mock.patch.object(voice, "_edge_stream", stream), mock.patch.object(voice, "RETRY_DELAY", 0):
            got = voice.edge_synthesize(["halo"], "id-ID-ArdiNeural", "+5%", log=lambda m: None)
        self.assertEqual(calls, [True, False])
        self.assertEqual(got, [(b"audio", [("halo", 0.1, 0.2)])])

    def test_edge_gagal_tiga_kali(self):
        async def stream(*a):
            raise ConnectionResetError("putus")

        with mock.patch.object(voice, "_edge_stream", stream), mock.patch.object(voice, "RETRY_DELAY", 0), \
                self.assertRaises(voice.TTSError):
            voice.edge_synthesize(["halo"], "id-ID-ArdiNeural", "+5%", log=lambda m: None)

    def test_edge_dibatalkan(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(media.Cancelled):
            voice.edge_synthesize(["halo"], "id-ID-ArdiNeural", "+5%", log=lambda m: None, cancel=ev)

    def test_edge_stream_meminta_wordboundary(self):
        seen = {}

        class FakeCommunicate:
            def __init__(self, text, voice_name, **kw):
                seen.update(kw)

            async def stream(self):
                yield {"type": "audio", "data": b"abc"}
                yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "halo"}
                await seen["connector"].close()

        import edge_tts
        with mock.patch.object(edge_tts, "Communicate", FakeCommunicate):
            audio, marks = asyncio.run(voice._edge_stream("halo", "id-ID-ArdiNeural", "+5%", True, None))
        self.assertEqual(seen["boundary"], "WordBoundary")
        self.assertIsNotNone(seen["connector"])           # IPv4
        self.assertEqual((audio, marks), (b"abc", [("halo", 0.1, 0.2)]))


class RentangKata(unittest.TestCase):
    def test_target_sempit_tetap_punya_rentang(self):
        for voice_name in config.VOICES:
            for ts in ((55, 58), (58, 58), (2, 30), (5, 8), (10, 12), (1, 1)):
                cfg = cfg_test(target_seconds=ts)
                cfg = cfg.model_copy(update={"tts": cfg.tts.model_copy(update={"voice": voice_name})})
                lo, hi = config.words_range(cfg)
                self.assertGreaterEqual(lo, 20, ts)
                self.assertGreaterEqual(hi - lo, 8, (voice_name, ts))

    def test_durasi_nyata_masuk_target(self):
        """Ukur lewat voice.synthesize (FakeEdge dengan kecepatan terkalibrasi), bukan menghitung ulang rumus."""
        for voice_name, pace in config.VOICE_SECONDS_PER_WORD.items():
            cfg = cfg_test()
            cfg = cfg.model_copy(update={"tts": cfg.tts.model_copy(update={"voice": voice_name})})
            lo, hi = config.words_range(cfg)
            for n_words in (lo, hi):
                per_seg = (n_words - 12) // 6
                script = Script.model_validate(long_script(n_words=per_seg))
                edge = FakeEdge(spw=pace)
                with tempfile.TemporaryDirectory() as d, mock.patch.object(voice, "edge_synthesize", edge):
                    w = voice.synthesize(script, cfg, d, log=lambda m: None)
                total = w.duration + voice.TAIL_SECONDS
                self.assertTrue(45 - 3 <= total <= 58, (voice_name, n_words, total))

    def test_otomatis_per_suara(self):
        ardi = config.words_range(cfg_test())
        gadis_cfg = cfg_test()
        gadis_cfg = gadis_cfg.model_copy(update={"tts": gadis_cfg.tts.model_copy(update={"voice": "id-ID-GadisNeural"})})
        gadis = config.words_range(gadis_cfg)
        self.assertLess(gadis[1], ardi[0] + 5)              # suara lebih lambat -> naskah lebih pendek
        for cfg, (lo, hi) in ((cfg_test(), ardi), (gadis_cfg, gadis)):
            pace = config.VOICE_SECONDS_PER_WORD[cfg.tts.voice]
            over = config.VIDEO_TAIL_SECONDS + config.NARRATION_GAPS * 0.3
            self.assertGreaterEqual(lo * pace + over, 45)
            self.assertLessEqual(hi * pace + over, 58)

    def test_bisa_dikunci(self):
        self.assertEqual(config.words_range(cfg_test(words_range=(100, 120))), (100, 120))
        self.assertEqual(config.words_limits(cfg_test(words_range=(100, 120))), (100, 120))

    def test_batas_keras_qc_lebih_longgar_tapi_tetap_lolos_render(self):
        """Uji nyata 12 Nov: Gemini ringan menulis ±115–120 kata; itu masih menghasilkan video yang sah, jadi
        kode tidak boleh menolaknya. Batas keras = durasi yang masih lolos validasi render (±3 dtk)."""
        for voice_name, pace in config.VOICE_SECONDS_PER_WORD.items():
            cfg = cfg_test()
            cfg = cfg.model_copy(update={"tts": cfg.tts.model_copy(update={"voice": voice_name})})
            (lo, hi), (lo_ok, hi_ok) = config.words_range(cfg), config.words_limits(cfg)
            self.assertLess(lo_ok, lo)
            self.assertGreater(hi_ok, hi)
            per_seg = (lo_ok - 12 + 5) // 6
            script = Script.model_validate(long_script(n_words=per_seg))
            self.assertGreaterEqual(script.word_count, lo_ok)
            with tempfile.TemporaryDirectory() as d, mock.patch.object(voice, "edge_synthesize", FakeEdge(spw=pace)):
                w = voice.synthesize(script, cfg, d, log=lambda m: None)
            self.assertGreaterEqual(w.duration + voice.TAIL_SECONDS, 45 - 3, voice_name)


class BersihBersih(ChannelSandbox):
    def test_hapus_berkas_besar_run_lama(self):
        from features.channel import state
        runs = os.path.join(self.data, "channel", "runs")
        old, new = os.path.join(runs, "2026-11-01"), os.path.join(runs, "2026-11-10")
        for d in (old, new):
            os.makedirs(os.path.join(d, "clips"))
            for name in ("clips/seg_1.mp4", "voice.wav", "final.mp4", "script.json"):
                open(os.path.join(d, name), "w").close()
        cache = os.path.join(runs, ".cache", "stock")
        os.makedirs(cache)
        stale, fresh = os.path.join(cache, "lama.mp4"), os.path.join(cache, "baru.mp4")
        for p in (stale, fresh):
            open(p, "w").close()
        os.utime(stale, (0, 0))
        n = state.prune_runs(DAY, keep=("2026-11-01",))
        self.assertEqual(n, 1)                                   # hanya cache stok; folder 'keep' tidak disentuh
        self.assertTrue(os.path.exists(os.path.join(old, "voice.wav")))
        open(os.path.join(old, ".lock"), "w").close()
        self.assertEqual(state.prune_runs(DAY), 0)               # run yang sedang berjalan tidak disentuh
        os.remove(os.path.join(old, ".lock"))
        n = state.prune_runs(DAY)
        self.assertEqual(n, 2)
        self.assertFalse(os.path.exists(os.path.join(old, "clips")))
        self.assertFalse(os.path.exists(os.path.join(old, "voice.wav")))
        for keep in (os.path.join(old, "final.mp4"), os.path.join(old, "script.json"),
                     os.path.join(new, "clips", "seg_1.mp4"), os.path.join(new, "voice.wav"), fresh):
            self.assertTrue(os.path.exists(keep), keep)

    def test_piper_ditolak_sebelum_ai_dipakai(self):
        config.save_config({"tts": {"engine": "piper"}})
        with self.assertRaises(config.ConfigError):
            pipeline.prepare_config("fakta-sains")


# --------------------------------------------------------------------------
# Motion graphic
# --------------------------------------------------------------------------
class Grafis(unittest.TestCase):
    def test_baca_angka(self):
        s = graphics.parse_number("-272 °C")
        self.assertEqual((s.value, s.unit, s.label, s.animate), (-272, "°C", "", True))
        s = graphics.parse_number("1.200 km per jam")
        self.assertEqual((s.value, s.unit, s.label, s.thousands), (1200, "km", "per jam", True))
        s = graphics.parse_number("3,5 juta tahun")
        self.assertEqual((s.value, s.decimals, s.label), (3.5, 1, "juta tahun"))
        self.assertFalse(graphics.parse_number("tahun 2007").animate)      # tahun tidak dihitung naik
        self.assertIsNone(graphics.parse_number("tanpa angka"))

    def test_format_angka_indonesia(self):
        self.assertEqual(graphics.format_number(12000, 0, True), "12.000")
        self.assertEqual(graphics.format_number(3.5, 1, False), "3,5")
        self.assertEqual(graphics.format_number(-272, 0, False), "-272")

    def test_semua_template_menghasilkan_frame(self):
        cases = {"big_number": "10 hari", "bullet_list": "a | b | c", "versus": "kiri | kanan",
                 "quote_card": "Kutipan fakta singkat", "title_card": "Judul"}
        for tpl, content in cases.items():
            scene = graphics.make_scene(tpl, content, 12)
            self.assertEqual(type(scene), graphics.SCENES[tpl], tpl)
            for i in (0, 6, 11):
                f = scene.frame(i)
                self.assertEqual(f.shape, (1920, 1080, 3), tpl)
                self.assertEqual(f.dtype, np.uint8)

    def test_isi_tidak_cocok_jatuh_ke_kartu_judul(self):
        for tpl, content in (("big_number", "tanpa angka"), ("versus", "satu sisi"), ("bullet_list", "satu poin")):
            self.assertIsInstance(graphics.make_scene(tpl, content, 5), graphics.TitleCard, tpl)

    def test_hitung_naik(self):
        scene = graphics.make_scene("big_number", "100", 60)
        early, late = scene.frame(3), scene.frame(59)
        self.assertFalse(np.array_equal(early, late))

    def test_lapisan_teks_layar_dan_label(self):
        self.assertIsNone(graphics.overlay_layer("", False))
        img = graphics.overlay_layer("Teks layar", True)
        self.assertEqual(img.size, (1080, 1920))
        alpha = np.asarray(img.getchannel("A"))
        self.assertGreater(alpha[graphics.OSD_Y:graphics.OSD_Y + 60, 300:780].max(), 200)
        self.assertGreater(alpha[graphics.LABEL_Y:graphics.LABEL_Y + 30, 60:200].max(), 100)
        self.assertEqual(alpha[1000:].max(), 0)              # tidak ada apa-apa di area subtitle/bawah


# --------------------------------------------------------------------------
# Visual
# --------------------------------------------------------------------------
class RentangVisual(unittest.TestCase):
    def test_hook_dan_cta_ikut_segmen_tepi(self):
        script = two_segment_script()
        w = words_for(script)
        sp = visuals.spans(script, w)
        self.assertEqual(sp[0].f0, 0)
        self.assertEqual(sp[1].f0, media.frame_at(w.segments[2].start))   # bagian [hook, 1, 2, cta]
        self.assertEqual(sp[0].f1, sp[1].f0)
        self.assertEqual(sp[-1].f1, media.frame_at(w.duration + voice.TAIL_SECONDS))

    def test_timing_hilang_ditolak(self):
        script = two_segment_script()
        w = words_for(script)
        w.segments = [t for t in w.segments if t.segment_id != 2]
        with self.assertRaises(visuals.VisualError):
            visuals.spans(script, w)

    def test_teks_layar_tidak_dobel_dengan_grafik(self):
        seg = two_segment_script(on_screen_text="-272 °C").segments[1]
        self.assertFalse(visuals.show_osd(seg, "-272 °C"))
        self.assertTrue(visuals.show_osd(seg, "Daftar poin lain"))
        self.assertTrue(visuals.show_osd(seg))
        self.assertFalse(visuals.show_osd(two_segment_script(on_screen_text="").segments[1]))
        # uji nyata 12 Nov: teks layar dengan kata baru tetap tampil, yang tidak menambah kata disembunyikan
        self.assertFalse(visuals.similar("Wilayah heliosheath", "Masih di heliosheath"))
        self.assertTrue(visuals.similar("Angin bergolak", "Angin matahari bergolak"))
        self.assertTrue(visuals.similar("Suhu -272°C", "-272 °C"))

    def test_rantai_cadangan(self):
        ai = Script.model_validate(long_script()).segments[2]
        self.assertEqual(ai.visual_type, "ai_image")
        self.assertEqual(visuals.chain_for(ai, ["stock", "ai_image", "motion_graphic"]),
                         ["ai_image", "stock", "motion_graphic", "plain"])
        self.assertEqual(visuals.chain_for(ai, ["stock", "motion_graphic"]), ["stock", "motion_graphic", "plain"])

    def test_filter_zoompan(self):
        f = visuals.zoompan_filter(90, "zoom_in")
        self.assertIn("d=90", f)
        self.assertIn("s=1080x1920", f)
        self.assertIn("fps=30", f)
        self.assertNotEqual(visuals.zoompan_filter(90, "pan_left"), f)


class KlipFFmpeg(TempDir):
    def test_ken_burns_satu_dan_dua_gambar(self):
        a, b = self.p("a.png"), self.p("b.png")
        png_image(1).save(a)
        png_image(2).save(b)
        ov = graphics.overlay_layer("Teks", True)
        ov.save(self.p("ov.png"))
        for imgs, n, overlay in (([a], 24, None), ([a, b], 40, self.p("ov.png"))):
            out = self.p(f"kb{len(imgs)}.mp4")
            media.ffmpeg(visuals.ken_burns_args(imgs, n, 1, out, overlay))
            info = engine.probe(out)
            self.assertEqual((info.width, info.height, info.fps, info.has_audio), (1080, 1920, 30, False))
            self.assertEqual(count_frames(out), n)

    def test_stok_diulang_dan_dipotong_tepat(self):
        src = make_video(self.p("src.mp4"), 0.5, size="720x1280", audio=True, fps=25)
        out = self.p("seg.mp4")
        media.ffmpeg(visuals.stock_args(src, 0.5, 45, out))           # klip 0,5 dtk untuk segmen 1,5 dtk
        info = engine.probe(out)
        self.assertEqual((info.width, info.height, info.has_audio), (1080, 1920, False))
        self.assertEqual(count_frames(out), 45)
        long_src = make_video(self.p("long.mp4"), 3, size="1080x1920", audio=False)
        out2 = self.p("seg2.mp4")
        graphics.overlay_layer("Teks", False).save(self.p("ov.png"))
        media.ffmpeg(visuals.stock_args(long_src, 3, 30, out2, self.p("ov.png")))
        self.assertEqual(count_frames(out2), 30)

    def test_encode_frame_tepat(self):
        out = self.p("g.mp4")
        scene = graphics.make_scene("title_card", "Judul", 15)
        media.encode_frames(media.frames_of(scene.frame, 10), 15, out)   # pembuat frame kurang: frame terakhir diulang
        self.assertEqual(count_frames(out), 15)

    def test_galat_pembuat_frame_tidak_tertelan(self):
        def frames():
            for i in range(30):
                if i == 10:
                    raise OSError("image file is truncated")
                yield np.zeros((1920, 1080, 3), np.uint8)

        with self.assertRaises(OSError):
            media.encode_frames(frames(), 30, self.p("x.mp4"))

    def test_durasi_stream_video_bukan_kontainer(self):
        src = self.p("av.mp4")
        engine.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=2", "-f", "lavfi", "-i",
                           "sine=duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src])
        self.assertGreater(engine.probe(src).duration, 2.5)       # kontainer mengikuti audio
        self.assertAlmostEqual(media.video_seconds(src), 2.0, delta=0.1)
        self.assertEqual(media.video_seconds(self.p("tidak-ada.mp4")), 0.0)

    def test_ffmpeg_bisa_dibatalkan(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(media.Cancelled):
            media.ffmpeg(["-f", "lavfi", "-i", "testsrc2=d=1", "-f", "null", "-"], cancel=ev)
        ev = threading.Event()
        timer = threading.Timer(0.4, ev.set)
        timer.start()
        t0 = time.monotonic()
        with self.assertRaises(media.Cancelled):     # dihentikan di tengah proses yang panjang
            media.ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=600", "-c:v", "libx264", "-preset",
                          "ultrafast", "-f", "null", "-"], cancel=ev)
        self.assertLess(time.monotonic() - t0, 5)


class BangunVisual(ChannelSandbox):
    def setUp(self):
        super().setUp()
        self.run_dir = os.path.join(self.data, "channel", "runs", DAY.isoformat())
        os.makedirs(self.run_dir)

    def build(self, script, **kw):
        return visuals.build(script, words_for(script), cfg_test(), self.run_dir, log=lambda m: None, seed=7, **kw)

    def test_cadangan_saat_api_gagal(self):
        script = two_segment_script()
        with mock.patch.object(stock, "find", side_effect=stock.StockError("kosong")):
            vis = self.build(script)
        self.assertEqual([v.type for v in vis.segments], ["motion_graphic", "motion_graphic"])
        first = vis.segments[0]
        self.assertEqual((first.requested, first.source_id, first.prompt), ("stock", "title_card", "Laut dalam"))
        self.assertTrue(any("stock: kosong" in f for f in first.fallbacks))
        self.assertFalse(first.on_screen_text)          # teks layar sudah jadi isi kartu judul
        self.assertTrue(vis.segments[1].on_screen_text)  # beda dengan isi grafik (-272 °C)
        self.assertEqual(vis.total_frames, sum(v.frames for v in vis.segments))
        for v in vis.segments:
            self.assertEqual(count_frames(os.path.join(self.run_dir, v.file)), v.frames)
        self.assertFalse(vis.uses_stock or vis.uses_ai_images)

    def test_cadangan_terakhir_latar_polos(self):
        script = two_segment_script()
        script.segments[0].on_screen_text = ""
        with mock.patch.object(stock, "find", side_effect=stock.StockError("kosong")):
            vis = self.build(script)
        self.assertEqual(vis.segments[0].type, "plain")
        self.assertTrue(any("motion_graphic" in f for f in vis.segments[0].fallbacks))

    def test_stok_dipakai_dan_dicatat(self):
        src = make_video(os.path.join(self.tmp, "clip.mp4"), 2, audio=False)
        clip = stock.StockClip("pexels", "123", "https://www.pexels.com/video/123/", "Budi", "https://p/u", 2.0,
                               1080, 1920, "https://x/clip.mp4")
        seen = {}

        def find(query, need, *, keys, exclude, **kw):
            seen.update(query=query, exclude=set(exclude))
            return clip, src, []

        with mock.patch.object(stock, "find", find):
            vis = self.build(two_segment_script(), exclude={"pexels:9"})
        v = vis.segments[0]
        self.assertEqual((v.type, v.source, v.source_id, v.author), ("stock", "pexels", "123", "Budi"))
        self.assertEqual(seen["query"], "ocean waves aerial")
        self.assertIn("pexels:9", seen["exclude"])
        self.assertEqual(vis.clip_ids, ["pexels:123"])
        self.assertTrue(vis.uses_stock)
        self.assertEqual(writer.stock_credits(vis), ["Budi / Pexels: https://www.pexels.com/video/123/"])

    def test_klip_stok_rusak_diganti(self):
        good = make_video(os.path.join(self.tmp, "good.mp4"), 2, audio=False)
        broken = os.path.join(self.tmp, "rusak.mp4")
        with open(broken, "wb") as f:
            f.write(b"bukan video")
        clips = [stock.StockClip("pexels", "1", "u1", "A", "", 5, 1080, 1920, "x"),
                 stock.StockClip("pexels", "2", "u2", "B", "", 5, 1080, 1920, "y")]
        found = iter([(clips[0], broken, []), (clips[1], good, [])])
        with mock.patch.object(stock, "find", side_effect=lambda *a, **k: next(found)):
            vis = self.build(two_segment_script())
        v = vis.segments[0]
        self.assertEqual((v.type, v.source_id), ("stock", "2"))
        self.assertFalse(os.path.exists(broken))             # dibuang dari cache
        self.assertTrue(any("pexels:1" in f for f in v.fallbacks))

    def test_gambar_ai_dua_gambar_dan_label_ilustrasi(self):
        script = two_segment_script(visual_type="ai_image", image_prompt="a tardigrade in space, soft light",
                                    label_illustration=True)
        seeds = []

        def gen(prompt, cfg, *, seed, **kw):
            seeds.append(seed)
            return png_image(seed or 1)

        with mock.patch.object(stock, "find", side_effect=stock.StockError("kosong")), \
                mock.patch.object(ai_images, "generate", gen), mock.patch.object(visuals, "LONG_SEGMENT", 0.5), \
                mock.patch.object(graphics, "overlay_layer", wraps=graphics.overlay_layer) as ov:
            vis = self.build(script)
        v = vis.segments[1]
        self.assertEqual((v.type, v.source), ("ai_image", "cloudflare"))
        self.assertEqual(len(v.images), 2)
        self.assertEqual(len(set(seeds)), 2)                    # dua gambar, seed berbeda (model flux-2)
        self.assertEqual(v.seed, seeds[0])
        self.assertIn("soft light", v.prompt)
        self.assertIn(config.AIImageCfg().style_suffix, v.prompt)
        for f in v.images:
            self.assertTrue(os.path.isfile(os.path.join(self.run_dir, f)))
        self.assertIn(mock.call("Suhu ekstrem", True), ov.call_args_list)   # label ILUSTRASI
        self.assertTrue(vis.uses_ai_images)
        self.assertEqual(count_frames(os.path.join(self.run_dir, v.file)), v.frames)

    def test_kuota_ai_habis_pindah_ke_stok(self):
        script = two_segment_script(visual_type="ai_image", image_prompt="prompt")
        src = make_video(os.path.join(self.tmp, "clip.mp4"), 2, audio=False)
        clip = stock.StockClip("pixabay", "5", "https://pixabay.com/videos/id-5/", "ani", "", 2.0, 1080, 1920, "u")
        with mock.patch.object(ai_images, "generate", side_effect=ai_images.QuotaExhausted("habis")), \
                mock.patch.object(stock, "find", return_value=(clip, src, [])):
            vis = self.build(script)
        v = vis.segments[1]
        self.assertEqual((v.requested, v.type), ("ai_image", "stock"))
        self.assertTrue(any("habis" in f for f in v.fallbacks))


# --------------------------------------------------------------------------
# Video stok
# --------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status=200, data=None, text="", chunks=(), headers=None, content=b""):
        self.status_code, self._data, self.text = status, data, text
        self._chunks, self.headers, self.content = list(chunks), headers or {}, content

    def json(self):
        if self._data is None:
            raise ValueError("bukan json")
        return self._data

    def iter_content(self, size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def _next(self, method, url, kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def get(self, url, **kw):
        return self._next("GET", url, kw)

    def post(self, url, **kw):
        return self._next("POST", url, kw)


PEXELS = {"videos": [
    {"id": 1, "url": "https://www.pexels.com/video/1/", "duration": 4, "user": {"name": "A", "url": "https://p/a"},
     "video_files": [{"quality": "hls", "width": None, "height": None, "link": "x.m3u8"},
                     {"quality": "uhd", "width": 2160, "height": 3840, "link": "4k.mp4"},
                     {"quality": "hd", "width": 1080, "height": 1920, "link": "hd.mp4"},
                     {"quality": "sd", "width": 540, "height": 960, "link": "sd.mp4"}]},
    {"id": 2, "url": "u2", "duration": 12, "user": {"name": "B"},
     "video_files": [{"quality": "hd", "width": 1920, "height": 1080, "link": "landscape.mp4"}]},
    {"id": 3, "url": "u3", "duration": 15, "user": {"name": "C"},
     "video_files": [{"quality": "sd", "width": 720, "height": 1280, "link": "sd3.mp4"}]}]}
PIXABAY = {"hits": [
    {"id": 10, "pageURL": "https://pixabay.com/videos/id-10/", "duration": 20, "user": "budi", "user_id": 7,
     "videos": {"large": {"url": "", "width": 0, "height": 0}, "medium": {"url": "m.mp4", "width": 1080,
                                                                          "height": 1920}}},
    {"id": 11, "pageURL": "p11", "duration": 30, "user": "x", "user_id": 1,
     "videos": {"medium": {"url": "land.mp4", "width": 1920, "height": 1080}}}]}


class Stok(TempDir):
    def test_pilih_berkas(self):
        self.assertEqual(stock.pick_file([(2160, 3840, "4k"), (1080, 1920, "hd"), (720, 1280, "sd")])[2], "hd")
        self.assertEqual(stock.pick_file([(2160, 3840, "4k"), (720, 1280, "sd")])[2], "sd")
        self.assertEqual(stock.pick_file([(2160, 3840, "4k")])[2], "4k")
        self.assertIsNone(stock.pick_file([(1920, 1080, "land"), (0, 0, "hls")]))

    def test_baca_jawaban(self):
        px = stock.parse_pexels(PEXELS)
        self.assertEqual([(c.id, c.file_url) for c in px], [("1", "hd.mp4"), ("3", "sd3.mp4")])
        self.assertEqual((px[0].author, px[0].author_url, px[0].key), ("A", "https://p/a", "pexels:1"))
        pb = stock.parse_pixabay(PIXABAY)
        self.assertEqual([(c.id, c.file_url) for c in pb], [("10", "m.mp4")])
        self.assertEqual(pb[0].author_url, "https://pixabay.com/users/budi-7/")

    def test_pilih_klip(self):
        clips = stock.parse_pexels(PEXELS)
        self.assertEqual(stock.choose(clips, 8, set()).id, "3")           # klip 1 terlalu pendek
        self.assertEqual(stock.choose(clips, 3, set()).id, "1")
        self.assertEqual(stock.choose(clips, 3, {"pexels:1"}).id, "3")   # dipakai 30 hari terakhir
        self.assertEqual(stock.choose(clips, 60, set()).id, "3")          # tidak ada yang cukup: terpanjang
        self.assertIsNone(stock.choose(clips, 3, {"pexels:1", "pexels:3"}))

    def test_kata_kunci_disederhanakan(self):
        self.assertEqual(stock.simplify("ocean waves aerial"), ["ocean waves aerial", "ocean waves", "ocean"])
        self.assertEqual(stock.simplify("ocean"), ["ocean"])

    def test_pencarian_dicache_dan_header_pexels(self):
        s = FakeSession(FakeResponse(data=PEXELS))
        stock.search("pexels", "Ocean  waves", "KEYPX", cache_dir=self.tmp, session=s)
        stock.search("pexels", "ocean waves", "KEYPX", cache_dir=self.tmp, session=s)
        self.assertEqual(len(s.calls), 1)
        _, url, kw = s.calls[0]
        self.assertEqual(url, "https://api.pexels.com/v1/videos/search")
        self.assertEqual(kw["headers"]["Authorization"], "KEYPX")
        self.assertEqual((kw["params"]["orientation"], kw["params"]["query"]), ("portrait", "Ocean  waves"))

    def test_galat_tanpa_key(self):
        s = FakeSession(FakeResponse(status=429, text="API rate limit exceeded"))
        with self.assertRaises(stock.StockError) as e:
            stock.search("pixabay", "ocean", "RAHASIA123", cache_dir=self.tmp, session=s)
        self.assertNotIn("RAHASIA123", str(e.exception))
        self.assertIn("batas", str(e.exception))
        self.assertEqual(s.calls[0][2]["params"]["safesearch"], "true")

    def test_unduh_dan_cache(self):
        clip = stock.parse_pexels(PEXELS)[0]
        s = FakeSession(FakeResponse(chunks=[b"abc", b"def"]))
        path = stock.download(clip, self.tmp, session=s)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"abcdef")
        stock.download(clip, self.tmp, session=s)
        self.assertEqual(len(s.calls), 1)
        self.assertFalse(os.path.exists(path + ".part"))

    def test_pexels_kosong_lalu_pixabay(self):
        s = FakeSession(FakeResponse(data={"videos": []}), FakeResponse(data=PIXABAY), FakeResponse(chunks=[b"x"]))
        clip, _path, notes = stock.find("ocean waves", 5, keys={"pexels": "a", "pixabay": "b"}, exclude=set(),
                                       cache_dir=self.tmp, session=s, log=lambda m: None)
        self.assertEqual(clip.key, "pixabay:10")
        self.assertTrue(notes and notes[0].startswith("pexels"))

    def test_tanpa_key(self):
        with self.assertRaises(stock.StockError):
            stock.find("ocean", 5, keys={}, exclude=set(), cache_dir=self.tmp)

    def test_unduhan_gagal_coba_kandidat_berikutnya(self):
        s = FakeSession(FakeResponse(data=PEXELS), FakeResponse(status=500), FakeResponse(chunks=[b"ok"]))
        exclude = set()
        clip, path, _notes = stock.find("ocean", 3, keys={"pexels": "a"}, exclude=exclude, cache_dir=self.tmp,
                                       session=s, log=lambda m: None)
        self.assertEqual(clip.key, "pexels:3")
        self.assertIn("pexels:1", exclude)
        self.assertTrue(os.path.isfile(path))

    def test_content_length_aneh_tidak_menggagalkan(self):
        clip = stock.parse_pexels(PEXELS)[0]
        s = FakeSession(FakeResponse(chunks=[b"abc"], headers={"Content-Length": "abc"}))
        self.assertTrue(os.path.isfile(stock.download(clip, self.tmp, session=s)))
        clip2 = stock.parse_pexels(PEXELS)[1]
        gz = FakeSession(FakeResponse(chunks=[b"isi"], headers={"Content-Length": "2", "Content-Encoding": "gzip"}))
        self.assertTrue(os.path.isfile(stock.download(clip2, self.tmp, session=gz)))
        self.assertEqual(gz.calls[0][2]["headers"]["Accept-Encoding"], "identity")

    def test_unduhan_tidak_lengkap_ditolak(self):
        clip = stock.parse_pexels(PEXELS)[0]
        s = FakeSession(FakeResponse(chunks=[b"abc"], headers={"Content-Length": "10"}))
        with self.assertRaises(stock.StockError):
            stock.download(clip, self.tmp, session=s)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "stock")), [])
        big = FakeSession(FakeResponse(chunks=[b"x"], headers={"Content-Length": str(stock.MAX_DOWNLOAD_BYTES + 1)}))
        with self.assertRaises(stock.StockError):
            stock.download(clip, self.tmp, session=big)


# --------------------------------------------------------------------------
# Gambar AI
# --------------------------------------------------------------------------
def b64_png(seed=3):
    buf = io.BytesIO()
    png_image(seed).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class GambarAI(ChannelSandbox):
    def gen(self, session, model=None, **kw):
        cf = config.AIImageCfg(**({"model": model} if model else {}))
        with mock.patch.object(ai_images, "_sleep", lambda s: None):
            return ai_images.generate("a tardigrade", cf, account="ACC", token="TOK", seed=kw.pop("seed", 42),
                                      session=session, log=lambda m: None, **kw)

    def test_flux2_multipart_ukuran_dan_seed(self):
        s = FakeSession(FakeResponse(data={"success": True, "result": {"image": b64_png()}}))
        img = self.gen(s)
        self.assertEqual(img.size, (96, 168))
        _, url, kw = s.calls[0]
        self.assertEqual(url, "https://api.cloudflare.com/client/v4/accounts/ACC/ai/run/"
                              "@cf/black-forest-labs/flux-2-klein-4b")
        self.assertEqual(kw["headers"]["Authorization"], "Bearer TOK")
        fields = {k: v[1] for k, v in kw["files"].items()}
        self.assertEqual((fields["width"], fields["height"], fields["seed"]), ("768", "1344", "42"))
        self.assertIn("no text", fields["prompt"])
        self.assertAlmostEqual(ai_images.neurons_used(), ai_images.estimate_neurons(config.AIImageCfg()))

    def test_model_json_dan_gambar_mentah(self):
        raw = base64.b64decode(b64_png())
        s = FakeSession(FakeResponse(headers={"Content-Type": "image/png"}, content=raw))
        self.gen(s, model="@cf/black-forest-labs/flux-1-schnell", seed=None)
        kw = s.calls[0][2]
        self.assertEqual(set(kw["json"]), {"prompt", "steps"})
        self.assertNotIn("files", kw)

    def test_kuota_habis(self):
        s = FakeSession(FakeResponse(status=429, data={"success": False, "errors": [
            {"code": 3036, "message": "You have used up your daily free allocation of 10,000 neurons."}]}))
        with self.assertRaises(ai_images.QuotaExhausted):
            self.gen(s)
        self.assertEqual(len(s.calls), 1)                  # tidak dicoba ulang
        self.assertFalse(ai_images.quota_ok(config.AIImageCfg()))

    def test_kapasitas_penuh_dicoba_ulang(self):
        s = FakeSession(FakeResponse(status=429, data={"errors": [{"code": 3040, "message": "Capacity"}]}),
                        FakeResponse(data={"result": {"image": b64_png()}}))
        self.gen(s)
        self.assertEqual(len(s.calls), 2)

    def test_token_ditolak_tidak_diulang(self):
        s = FakeSession(FakeResponse(status=401, data={"errors": [{"code": 10000, "message": "Authentication error"}]}))
        with self.assertRaises(ai_images.AIImageError):
            self.gen(s)
        self.assertEqual(len(s.calls), 1)

    def test_gambar_hitam_ditolak(self):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64)).save(buf, "PNG")
        s = FakeSession(FakeResponse(data={"result": {"image": base64.b64encode(buf.getvalue()).decode()}}))
        with self.assertRaises(ai_images.AIImageError):
            self.gen(s)

    def test_perkiraan_neurons_ikut_ukuran(self):
        self.assertAlmostEqual(ai_images.estimate_neurons(config.AIImageCfg()), 6 * 26.05)      # 768×1344
        self.assertAlmostEqual(ai_images.estimate_neurons(config.AIImageCfg(width=1080, height=1920)), 12 * 26.05)
        schnell = config.AIImageCfg(model="@cf/black-forest-labs/flux-1-schnell", width=1024, height=1024,
                                    neurons_per_tile=4.8, neurons_per_step=9.6)
        self.assertAlmostEqual(ai_images.estimate_neurons(schnell), 4 * 4.8 + 4 * 9.6)
        self.assertEqual(ai_images.estimate_neurons(config.AIImageCfg(neurons_per_image=99)), 99)

    def test_berhenti_di_80_persen(self):
        cf = config.AIImageCfg()
        ai_images.add_neurons(7800)
        self.assertTrue(ai_images.quota_ok(cf))             # 7800 + 160 <= 8000
        ai_images.add_neurons(100)
        self.assertFalse(ai_images.quota_ok(cf))
        with self.assertRaises(ai_images.QuotaExhausted):
            self.gen(FakeSession(FakeResponse(data={})))

    def test_pemakaian_per_tanggal_utc(self):
        ai_images.add_neurons(500, datetime(2026, 11, 12, 23, 30, tzinfo=UTC))
        self.assertEqual(ai_images.neurons_used(datetime(2026, 11, 12, 23, 59, tzinfo=UTC)), 500)
        self.assertEqual(ai_images.neurons_used(datetime(2026, 11, 13, 0, 1, tzinfo=UTC)), 0)

    def test_tanpa_akun(self):
        with self.assertRaises(ai_images.AIImageError):
            ai_images.generate("x", config.AIImageCfg(), account="", token="", seed=None)


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
class Render(ChannelSandbox):
    def setUp(self):
        super().setUp()
        self.styles = mock.patch.object(clip_api, "STYLES_PATH", os.path.join(self.tmp, "styles.json"))
        self.styles.start()

    def tearDown(self):
        self.styles.stop()
        super().tearDown()

    def test_gaya_subtitle(self):
        st = render.caption_style(cfg_test(caption_style="preset:tegas-hijau"))
        self.assertEqual((st["cut_pauses"], st["zoom"], st["title_on"]), (0, "tanpa", False))
        self.assertTrue(0.55 <= st["caption_pos"] <= 0.65)
        msgs = []
        st = render.caption_style(cfg_test(caption_style="tidak-ada"), msgs.append)
        self.assertTrue(msgs)
        self.assertEqual(st["caption_color"], clip_api.PRESET_BY_ID["preset:klasik"]["style"]["caption_color"])

    def test_pilih_musik(self):
        d = os.path.join(self.tmp, "musik")
        os.makedirs(d)
        self.assertIsNone(render.pick_music(d, DAY))
        self.assertIsNone(render.pick_music("", DAY))
        for n in ("a.mp3", "b.WAV"):
            engine.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=400:duration=3", os.path.join(d, n)])
        open(os.path.join(d, "catatan.txt"), "w").close()
        got = render.pick_music(d, DAY)
        self.assertIn(os.path.basename(got), ("a.mp3", "b.WAV"))
        self.assertEqual(render.pick_music(d, DAY), got)

    def test_musik_rusak_dilewati(self):
        d = os.path.join(self.tmp, "musik")
        os.makedirs(d)
        open(os.path.join(d, "a_kosong.mp3"), "wb").close()
        with open(os.path.join(d, "b_bukan.mp3"), "w") as f:
            f.write("<html>bukan musik</html>")
        self.assertIsNone(render.pick_music(d, DAY, log=lambda m: None))
        engine.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=500:duration=3", os.path.join(d, "c_lagu.mp3")])
        for day in (DAY, date(2026, 11, 13), date(2026, 11, 14)):
            self.assertEqual(os.path.basename(render.pick_music(d, day, log=lambda m: None)), "c_lagu.mp3")

    def test_filter_audio(self):
        self.assertNotIn("amix", render.audio_graph(10, False, -22))
        g = render.audio_graph(10, True, -22)
        self.assertIn("amix", g)
        self.assertIn("volume=-22", g)
        self.assertIn("afade=t=out:st=8.5000", g)

    def test_validasi(self):
        cfg = cfg_test(target_seconds=(1, 3))
        ok = make_video(os.path.join(self.tmp, "ok.mp4"), 2)
        self.assertTrue(render.validate(ok, cfg).valid)
        bad = render.validate(ok, cfg_test(target_seconds=(10, 20)))
        self.assertFalse(bad.valid)
        self.assertIn("durasi", bad.problems[0])
        silent = make_video(os.path.join(self.tmp, "sunyi.mp4"), 2, audio=False)
        self.assertIn("tidak ada audio", render.validate(silent, cfg).problems)
        small = make_video(os.path.join(self.tmp, "kecil.mp4"), 2, size="720x1280")
        self.assertIn("ukuran", render.validate(small, cfg).problems[0])
        self.assertFalse(render.validate(os.path.join(self.tmp, "tidak-ada.mp4"), cfg).valid)
        # durasi video tidak sama dengan rencana (klip/suara terpotong)
        vis = Visuals(segments=[SegmentVisual(segment_id=1, type="stock", requested="stock", file="x", frames=90)],
                      total_frames=90)
        info = render.validate(ok, cfg, vis)
        self.assertTrue(any("rencana" in p for p in info.problems))

    def test_perlu_dicek_manusia(self):
        def vis(*types):
            return Visuals(segments=[SegmentVisual(segment_id=i, type=t, requested="stock", file="x")
                                     for i, t in enumerate(types, 1)])
        cfg = cfg_test()
        self.assertEqual(render.needs_review(cfg, vis("stock", "ai_image", "motion_graphic", "stock")), [])
        self.assertTrue(render.needs_review(cfg, vis("motion_graphic", "motion_graphic", "plain")))
        self.assertTrue(render.needs_review(cfg, vis("stock", "ai_image", "plain", "plain")))

    def test_render_lengkap_dengan_musik(self):
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        script = two_segment_script()
        w = words_for(script)
        media.write_wav(os.path.join(run_dir, "voice.wav"), tone_samples(w.duration, lead=0, tail=0))
        music = os.path.join(self.tmp, "musik")
        os.makedirs(music)
        engine.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=500:duration=3", os.path.join(music, "lagu.mp3")])
        cfg = cfg_test(target_seconds=(1, 10), music_dir=music)
        with mock.patch.object(stock, "find", side_effect=stock.StockError("kosong")):
            vis = visuals.build(script, w, cfg, run_dir, log=lambda m: None)
        info = render.render(run_dir, w, vis, cfg, DAY, log=lambda m: None)
        self.assertTrue(info.valid, info.problems)
        self.assertEqual((info.width, info.height, info.fps, info.has_audio), (1080, 1920, 30, True))
        self.assertEqual(info.music, "lagu.mp3")
        self.assertAlmostEqual(info.duration, vis.total_frames / 30, delta=0.1)
        # engine memakai -shortest: video pendek bisa kehilangan 1-2 frame terakhir (tidak terjadi di 45-58 dtk)
        self.assertLessEqual(abs(count_frames(os.path.join(run_dir, "final.mp4")) - vis.total_frames), 2)
        probe = subprocess.run([engine.ffmpeg_exe(), "-hide_banner", "-i", os.path.join(run_dir, "final.mp4")],
                               capture_output=True, check=False).stderr.decode("utf-8", "ignore")
        self.assertIn("bt709", next(x for x in probe.splitlines() if "Video:" in x))   # warna diberi tanda
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "captions.srt")))
        self.assertFalse(os.path.exists(os.path.join(run_dir, "base.mkv")))
        self.assertTrue(info.warnings)                   # hanya 1 jenis visual (motion_graphic)


# --------------------------------------------------------------------------
# Pipeline media
# --------------------------------------------------------------------------
class PipelineMedia(ChannelSandbox):
    def setUp(self):
        super().setUp()
        config.save_config({"video": {"target_seconds": [2, 30], "words_range": [100, 160]}})
        self.edge = FakeEdge(spw=0.05)
        self.styles = mock.patch.object(clip_api, "STYLES_PATH", os.path.join(self.tmp, "styles.json"))
        self.styles.start()

    def tearDown(self):
        self.styles.stop()
        super().tearDown()

    def fake(self, script=None):
        return FakeLLM(research_queries={"queries": [{"id": "gurita", "en": "octopus"}, {"id": "paus"}]},
                       research=draft(cand("Gurita tiga jantung", [1], 30)), dedupe={"duplicate_ids": []},
                       script=script or (lambda v: long_script(v["format"], v["sources"][0]["url"])),
                       qc=qc_answer(8), seo=SEO)

    def go(self, only="video", fake=None, **kw):
        out = []
        fake = fake or self.fake()
        with mock.patch.object(llm, "generate_json", fake), \
                mock.patch.object(research, "wikipedia_materials", return_value=materials()), \
                mock.patch.object(research, "enrich", side_effect=lambda c, log=None: c), \
                mock.patch.object(voice, "edge_synthesize", self.edge), \
                mock.patch.object(stock, "find", side_effect=stock.StockError("tanpa key")):
            code = pipeline.run_daily(DAY, echo=out.append, dry_run=True, only=only, genre="fakta-sains", **kw)
        return code, "\n".join(out), fake

    def rd(self, *p):
        return os.path.join(self.data, "channel", "runs", DAY.isoformat(), *p)

    def load(self, name, schema):
        with open(self.rd(name + ".json"), encoding="utf-8") as f:
            return schema.model_validate(json.load(f))

    def test_video_lengkap_dari_awal(self):
        progress = []
        code, out, _ = self.go(only=None, on_progress=lambda s, p: progress.append((s, p)))
        self.assertEqual(code, 0, out)
        for name in ("words.json", "visuals.json", "render.json", "voice.wav", "voice.mp3", "final.mp4",
                     "captions.srt"):
            self.assertTrue(os.path.isfile(self.rd(name)), name)
        self.assertTrue(self.load("render", RenderInfo).valid)
        self.assertIn("analyst belum tersedia", out)
        self.assertIn("publish_youtube belum tersedia", out)
        copied = os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")
        self.assertTrue(os.path.isfile(copied))
        # deskripsi disusun ulang dari visual sebenarnya: rencana memakai stok & AI, hasilnya grafik semua
        meta = self.load("metadata", Metadata)
        self.assertNotIn("Pexels", meta.description)
        self.assertNotIn("dibuat dengan AI", meta.description)
        self.assertTrue(meta.summary)
        steps = {s for s, _ in progress}
        self.assertTrue({"tts", "visuals", "render"} <= steps)
        self.assertIn(("render", 1.0), progress)

    def test_checkpoint_dan_force(self):
        self.go()
        code, out, _ = self.go(only="media")
        self.assertEqual(code, 0, out)
        self.assertIn("dipakai ulang: tts, visuals, render", out)
        code, out, _ = self.go(only="media", force=["visuals"])
        # klip dibuat ulang; hasilnya sama persis, jadi video tidak perlu di-render ulang
        self.assertIn("dijalankan: visuals; dipakai ulang: tts, render", out)
        code, out, _ = self.go(only="media", force=["render"])
        self.assertIn("dijalankan: render; dipakai ulang: tts, visuals", out)
        os.remove(self.rd("clips", "seg_2.mp4"))            # klip hilang: visual dibuat ulang
        code, out, _ = self.go(only="media")
        self.assertIn("dijalankan: visuals;", out)
        self.assertTrue(os.path.isfile(self.rd("clips", "seg_2.mp4")))

    def test_video_tidak_valid_gagal(self):
        self.go()
        out_copy = os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")
        self.assertTrue(os.path.isfile(out_copy))
        bad = RenderInfo(width=720, height=1280, fps=30, duration=9, has_audio=True, valid=False,
                         problems=["ukuran 720x1280"])
        with mock.patch.object(render, "validate", return_value=bad):
            code, out, _ = self.go(only="media", force=["render"])
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("Validasi video gagal", out)
        self.assertFalse(self.load("render", RenderInfo).valid)
        self.assertFalse(os.path.exists(out_copy))          # salinan lama di output/ tidak boleh tertinggal
        code, out, _ = self.go(only="media")                 # run berikutnya membuat ulang, bukan memakai yang gagal
        self.assertEqual(code, 0, out)
        self.assertIn("dijalankan: render", out)
        self.assertTrue(os.path.isfile(out_copy))

    def test_skrip_ditulis_ulang_media_ikut_dibuat_ulang(self):
        self.go()
        self.assertTrue(os.path.isfile(self.rd("final.mp4")))
        scripts = [lambda v: long_script(v["format"], v["sources"][0]["url"], hook="Hook baru yang lain sama sekali")]
        out_copy = os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")
        self.assertTrue(os.path.isfile(out_copy))
        code, out, _ = self.go(only="plan", force=["script"], fake=self.fake(script=scripts))
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(out_copy))             # video lama ditarik dari folder hasil
        code, out, _ = self.go(only="media")
        self.assertIn("dijalankan: tts, visuals, render", out)
        words = self.load("words", Words)
        self.assertEqual(words.words[0].word, "Hook")

    def test_skrip_diedit_setelah_qc_ditolak(self):
        self.go(only="plan")
        with open(self.rd("script.json"), encoding="utf-8") as f:
            data = json.load(f)
        data["hook"] = "Hook yang diedit tangan tanpa diperiksa"
        with open(self.rd("script.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        code, out, _ = self.go(only="tts")
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("Skrip atau pengaturan berubah setelah diperiksa QC", out)
        code, out, _ = self.go(only="video")                  # langkah qc memeriksa ulang skrip yang diedit
        self.assertEqual(code, 0, out)
        self.assertIn("dijalankan: qc, seo, tts, visuals, render", out)

    def test_seo_ulang_tidak_membuat_ulang_video(self):
        self.go()
        code, out, fake = self.go(force=["seo"])
        self.assertEqual(code, 0, out)
        self.assertIn("dijalankan: seo;", out)
        self.assertEqual(fake.names(), ["seo"])
        meta = self.load("metadata", Metadata)
        self.assertNotIn("Pexels", meta.description)          # disusun ulang dari visual yang sebenarnya

    def test_suara_gagal_di_tengah_tidak_meninggalkan_hasil_lama(self):
        self.go()
        with mock.patch.object(voice, "edge_synthesize", side_effect=voice.TTSError("putus")):
            out = []
            with mock.patch.object(llm, "generate_json", self.fake()):
                code = pipeline.run_daily(DAY, echo=out.append, dry_run=True, only="media", genre="fakta-sains",
                                          force=["tts"])
        self.assertEqual(code, pipeline.EXIT_FAILED)
        for name in ("words.json", "voice.wav"):
            self.assertFalse(os.path.exists(self.rd(name)), name)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")))
        code, out, _ = self.go(only="media")                   # suara sama -> visual & video lama cocok lagi
        self.assertEqual(code, 0, out)
        self.assertIn("dijalankan: tts; dipakai ulang: visuals, render", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")))

    def test_qc_gagal_setelah_force_menarik_video(self):
        self.go()
        out_copy = os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")
        fake = FakeLLM(qc=qc_answer(3), script=lambda v: long_script(v["format"], v["sources"][0]["url"]))
        with mock.patch.object(llm, "generate_json", fake):
            code = pipeline.run_daily(DAY, echo=lambda m: None, dry_run=True, only="video", genre="fakta-sains",
                                      force=["qc"])
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertFalse(os.path.exists(out_copy))
        code, out, _ = self.go(only="media")
        self.assertEqual(code, pipeline.EXIT_FAILED)          # QC gagal: video tidak boleh dibuat/dipakai
        self.assertIn("QC belum lulus", out)

    def test_force_qc_skrip_sama_tidak_mengulang_media(self):
        self.go()
        code, out, _ = self.go(force=["qc"])
        self.assertEqual(code, 0, out)
        self.assertIn("dijalankan: qc, seo; dipakai ulang: research, script, tts, visuals, render", out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "output", "channel", DAY.isoformat(), "final.mp4")))

    def test_topik_terlarang_baru_memicu_qc_ulang(self):
        self.go(only="plan")
        cfg = config.load_config()
        config.save_config({"channel": {"banned_topics": [*cfg.channel.banned_topics, "gurita"]}})
        code, out, _ = self.go(only="tts")
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("pengaturan berubah", out)
        code, out, fake = self.go(only="plan")
        self.assertIn("research", fake.names())                 # riset diulang dengan aturan baru
        self.assertEqual(code, pipeline.EXIT_FAILED)            # satu-satunya kandidat kini terlarang

    def test_run_lama_tanpa_sidik_diadopsi(self):
        self.go()
        os.remove(self.rd("inputs.json"))
        code, out, fake = self.go()
        self.assertEqual(code, 0, out)
        self.assertIn("run lama", out)
        self.assertEqual(fake.names(), [])
        self.assertIn("dijalankan: -", out)

    def test_narasi_terlalu_pendek_dipanjangkan(self):
        config.save_config({"video": {"target_seconds": [20, 30], "words_range": [10, 200]}})
        self.edge.spw = 0.12
        scripts = [lambda v: long_script(v["format"], v["sources"][0]["url"], n_words=4),
                   lambda v: long_script(v["format"], v["sources"][0]["url"])]
        code, out, fake = self.go(fake=self.fake(script=scripts))
        self.assertEqual(code, 0, out)
        self.assertIn("dipanjangkan", out)
        self.assertGreater(self.load("script", Script).word_count, 100)
        self.assertEqual(fake.names().count("seo"), 2)
        self.assertTrue(self.load("render", RenderInfo).valid)

    def test_variasi_kurang_perlu_dicek(self):
        code, out, _ = self.go()
        self.assertEqual(code, 0, out)
        info = self.load("render", RenderInfo)
        self.assertTrue(info.valid)
        self.assertTrue(info.needs_review)                    # semua grafik/latar polos karena tanpa key
        self.assertIn("PERLU DICEK", out)

    def test_dibatalkan_saat_render(self):
        ev = threading.Event()
        before = engine._ENCODER

        def on_progress(step, p):
            if step == "render" and 0 < p < 1:
                ev.set()

        code, out, _ = self.go(cancel=ev, on_progress=on_progress)
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("DIBATALKAN di langkah render", out)
        self.assertEqual(engine._ENCODER, before)             # pembatalan tidak memaksa pindah ke libx264
        leftovers = [n for n in os.listdir(self.rd()) if ".tmp" in n or n.endswith(".log") and n != "run.log"]
        self.assertEqual(leftovers, [])
        self.assertFalse(os.path.exists(self.rd("final.mp4")))

    def test_qc_gagal_tidak_ada_media(self):
        self.go(only="plan")
        with open(self.rd("qc.json"), encoding="utf-8") as f:
            qc = json.load(f)
        qc["score"] = 3
        with open(self.rd("qc.json"), "w", encoding="utf-8") as f:
            json.dump(qc, f)
        code, out, _ = self.go(only="tts")
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("QC belum lulus", out)
        self.assertFalse(os.path.exists(self.rd("voice.wav")))

    def test_terlalu_panjang_naskah_dipendekkan(self):
        config.save_config({"video": {"target_seconds": [2, 9], "words_range": [20, 160]}})
        self.edge.spw = 0.06
        scripts = [lambda v: long_script(v["format"], v["sources"][0]["url"]),
                   lambda v: long_script(v["format"], v["sources"][0]["url"], n_words=4)]
        fake = self.fake(script=scripts)
        code, out, fake = self.go(fake=fake)
        self.assertEqual(code, 0, out)
        self.assertIn("dipendekkan", out)
        self.assertEqual(self.edge.rates, ["+5%", "+10%", "+15%", "+5%"])
        script = self.load("script", Script)
        self.assertLess(script.word_count, 60)
        self.assertEqual(fake.names().count("seo"), 2)        # metadata dibuat ulang untuk naskah baru
        last_script_call = [c for c in fake.calls if c["name"] == "script"][-1]
        self.assertTrue(last_script_call["vars"]["issues"])

    def test_dibatalkan(self):
        ev = threading.Event()

        def on_progress(step, p):
            if step == "visuals":
                ev.set()

        code, out, _ = self.go(cancel=ev, on_progress=on_progress)
        self.assertEqual(code, pipeline.EXIT_FAILED)
        self.assertIn("DIBATALKAN di langkah visuals", out)
        self.assertFalse(os.path.exists(self.rd(".lock")))
        self.assertFalse(os.path.exists(self.rd("final.mp4")))


if __name__ == "__main__":
    unittest.main()
