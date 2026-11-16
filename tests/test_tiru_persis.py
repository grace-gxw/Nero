"""Tiru editan persis & referensi lebih luas: teks kinetik, tekstur, warna hex, ukuran persis, sisipan b-roll,
font sendiri, referensi horizontal/gambar/banyak clip/link akun. Butuh ~2 menit (render & ffmpeg sungguhan)."""
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

from tests.util import ROOT  # noqa: F401  (menambahkan folder aplikasi ke sys.path)

import common  # noqa: E402
import gemini_ai  # noqa: E402
from features.clip import api as clip  # noqa: E402
from features.clip import engine as core  # noqa: E402
from features.clip import style_ai  # noqa: E402
from features.clip.presets import DEFAULT_STYLE, PRESET_BY_ID  # noqa: E402
from tests.test_gaya_ai import FakeOllama, WithOllama, textured_source  # noqa: E402
from tests.test_render_gaya import TITLE, fake_words  # noqa: E402


def grab(path, t, scale=None):
    import cv2
    vf = ["-vf", f"scale={scale}:-2"] if scale else []
    b = subprocess.run([core.ffmpeg_exe(), "-loglevel", "error", "-ss", str(t), "-i", path, "-frames:v", "1", *vf,
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)


class Mesin(unittest.TestCase):
    """Bagian render tanpa video: warna hex, garis tepi persis, teks kinetik, judul, progress bar."""

    def test_warna_hex_dan_nama(self):
        self.assertEqual(core.color_rgb("kuning"), (255, 221, 0))
        self.assertEqual(core.color_rgb("#E4E5E3"), (228, 229, 227))
        self.assertEqual(core.color_rgb("bukan", (1, 2, 3)), (1, 2, 3))
        self.assertEqual(core._rgb_name((250, 250, 250)), "putih")
        self.assertEqual(core._rgb_name((228, 200, 140)), "#e4c88c")
        self.assertEqual(core._stroke_px(100, 2), 9)
        self.assertEqual(core._stroke_px(100, 2, 0.05), 5)          # rasio persis menggantikan tingkat

    def test_kinetik_bertumpuk_dan_stabil(self):
        words = [{"start": 0.0, "end": 0.3, "word": "Dia"}, {"start": 0.3, "end": 0.8, "word": "bangun"},
                 {"start": 0.8, "end": 1.3, "word": "zombie"}, {"start": 1.3, "end": 1.5, "word": "pada"},
                 {"start": 1.5, "end": 2.1, "word": "umumnya."}, {"start": 2.4, "end": 3.0, "word": "impulsnya"}]
        k = core.KineticRenderer(words, {**DEFAULT_STYLE, "caption_mode": "kinetik", "caption_size": 150})
        self.assertGreaterEqual(len(k.phrases), 2)                 # titik / jeda memulai frasa baru
        p = k.phrases[0]
        ys = [y for *_, y in p["placed"]]
        self.assertGreater(len(set(ys)), 1)                         # bertumpuk dalam beberapa baris
        sizes = {w["w"]: f.size for w, _, f, *_ in p["placed"]}
        self.assertLess(sizes["Dia"], sizes["bangun"])              # kata sambung lebih kecil
        # letak kata tidak berubah saat kata berikutnya muncul: gambar kata ke-1 sama di n=2 dan n=3
        _, w2, x2, y2 = k._images(0, 2)
        base3, _, _, _ = k._images(0, 3)
        self.assertGreater(base3[y2:y2 + w2.shape[0], x2:x2 + w2.shape[1], 3].max(), 0)
        frame = np.full((core.OUT_H, core.OUT_W, 3), 60, np.uint8)
        k.draw(frame, 1.0)
        self.assertGreater(int((frame > 200).sum()), 1000)          # tulisan tergambar

    def test_tekstur_melubangi_huruf(self):
        arr = np.zeros((200, 400, 4), np.uint8)
        arr[50:150, 50:350] = 255
        out = core._apply_texture(arr, 0, 900)
        holes = float((out[50:150, 50:350, 3] == 0).mean())
        self.assertTrue(0.02 < holes < 0.3, holes)
        np.testing.assert_array_equal(out, core._apply_texture(arr, 0, 900))   # pola tetap (tidak berkedip)

    def test_judul_hex_dan_progress_persis(self):
        st = {**DEFAULT_STYLE, "title_bg": "#2255aa", "progress_color": "#ff3366", "progress_h": 24, "captions": False,
              "layout": "center"}
        comp = core.FrameComposer(1920, 1080, "center", None, None, st, [], "Judul uji", 100)
        out = comp.frame(np.full((1080, 1920, 3), 90, np.uint8), 99)
        np.testing.assert_array_equal(out[core.OUT_H - 5, 10], (0x66, 0x33, 0xff))     # BGR
        self.assertTrue((out[core.OUT_H - 24, 10] == (0x66, 0x33, 0xff)).all())
        self.assertFalse((out[core.OUT_H - 26, 10] == (0x66, 0x33, 0xff)).all())
        y = comp.title_y + 30
        row = out[y, core.OUT_W // 2 - 300:core.OUT_W // 2 + 300]
        self.assertTrue(any(abs(int(b) - 0xaa) < 6 and abs(int(r) - 0x22) < 6 for b, g, r in row))

    def test_rencana_broll(self):
        with mock.patch.object(core, "_media_duration", return_value=6.0):
            plan = core.plan_broll({"broll": True, "broll_every": 3, "broll_len": 2, "broll_after": 4}, 600,
                                   ["a.mp4", "b.mp4"], seed="x")
            self.assertEqual(plan, core.plan_broll({"broll": True, "broll_every": 3, "broll_len": 2,
                                                    "broll_after": 4}, 600, ["a.mp4", "b.mp4"], seed="x"))
        self.assertEqual(plan[0]["k"], 120)                         # pembicara 4 dtk dulu
        self.assertEqual([p["m"] - p["k"] for p in plan[:2]], [60, 60])
        self.assertEqual(plan[1]["k"] - plan[0]["m"], 90)           # 3 dtk pembicara di antaranya
        self.assertLessEqual(plan[-1]["m"], 600 - 30)               # detik terakhir selalu pembicara
        self.assertEqual(core.plan_broll({"broll": False}, 600, ["a.mp4"]), [])
        self.assertEqual(core.plan_broll({"broll": True}, 600, []), [])

    def test_gabung_banyak_referensi(self):
        res = [{"style": {"caption_size": 80, "caption_words": 2, "caption_upper": True, "caption_base": "#e4e5e3",
                          "layout": "face"}},
               {"style": {"caption_size": 90, "caption_words": 3, "caption_upper": True, "caption_base": "#e8e8e6",
                          "layout": "face", "zoom": "hentak"}},
               {"style": {"caption_size": 100, "caption_words": 3, "caption_upper": False, "caption_base": "putih",
                          "layout": "blur"}}]
        st, notes = style_ai.combine(res)
        self.assertEqual(st["caption_size"], 90)                    # nilai tengah
        self.assertEqual(st["caption_words"], 3)
        self.assertTrue(st["caption_upper"])                        # suara terbanyak
        self.assertEqual(st["layout"], "face")
        self.assertEqual(st["caption_base"], "putih")               # median RGB dekat putih
        self.assertNotIn("zoom", st)                                # hanya 1 dari 3 referensi
        self.assertIn("3 referensi", notes[0])

    def test_link_akun(self):
        self.assertTrue(core.is_profile_url("https://www.tiktok.com/@akun"))
        self.assertTrue(core.is_profile_url("https://www.youtube.com/@channel/shorts"))
        self.assertFalse(core.is_profile_url("https://www.tiktok.com/@akun/video/123"))
        self.assertFalse(core.is_profile_url("https://youtube.com/shorts/abc"))
        info = {"entries": [{"url": "abc"}, {"url": "def"}, {"url": "ghi"}]}
        ydl = mock.MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = info
        with mock.patch("yt_dlp.YoutubeDL", return_value=ydl) as m:
            urls = core.expand_profile("https://www.youtube.com/@channel", 2)
        self.assertEqual(urls, ["https://www.youtube.com/shorts/abc", "https://www.youtube.com/shorts/def"])
        self.assertTrue(m.call_args[0][0]["playlistend"] == 2)


class TiruPersis(unittest.TestCase):
    """Render gaya yang diketahui, lalu baca kembali: harus persis (warna hex, tebal, tekstur, kinetik, b-roll)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="nero-test-")
        cls.src = os.path.join(cls.tmp, "sumber.mp4")
        core.run_ffmpeg(["-f", "lavfi", "-i", "gradients=s=1280x720:speed=0.03:n=3,noise=alls=25:allf=t+u",
                         "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000", "-t", "16",
                         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", cls.src])
        cls.broll = os.path.join(cls.tmp, "broll.mp4")
        core.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30", "-t", "8", "-c:v", "libx264", "-preset",
                         "veryfast", "-pix_fmt", "yuv420p", cls.broll])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, name, style, end=14.0, **kw):
        out = os.path.join(self.tmp, f"{name}.mp4")
        core.render_clip(self.src, 0.0, end, out, fake_words(), {**DEFAULT_STYLE, "loudnorm": False, **style}, TITLE,
                         **kw)
        return out

    def test_warna_tebal_judul_progress_persis(self):
        style = {"layout": "center", "caption_base": "#f0d8a8", "caption_color": "#ff6a00", "caption_stroke": 3,
                 "caption_size": 96, "title_bg": "#2255aa", "progress_color": "#33cc99", "progress_h": 22}
        got = core.analyze_reference_style(self.render("persis", style))["style"]
        for k in ("caption_base", "caption_color", "title_bg", "progress_color"):
            want, have = np.array(core.color_rgb(style[k])), np.array(core.color_rgb(got.get(k), (0, 0, 0)))
            self.assertLess(np.abs(want - have).max(), 26, f"{k}: {got.get(k)}")
        self.assertAlmostEqual(got["caption_stroke_ratio"], 1 / 7, delta=0.04)
        self.assertEqual(got["caption_stroke"], 3)
        self.assertAlmostEqual(got["progress_h"], 22, delta=5)
        self.assertEqual(got.get("caption_texture"), False)

    def test_kinetik_bertekstur_terbaca(self):
        style = {"layout": "center", "caption_mode": "kinetik", "caption_texture": True, "caption_font": "bebas",
                 "caption_size": 150, "caption_stroke": 1, "caption_highlight": "tanpa", "caption_pos": 0.5,
                 "title_on": False, "progress_bar": False}
        got = core.analyze_reference_style(self.render("kinetik", style))["style"]
        self.assertEqual(got.get("caption_mode"), "kinetik")
        self.assertTrue(got.get("caption_texture"))
        self.assertAlmostEqual(got["caption_pos"], 0.5, delta=0.08)

    def test_broll_berlatar_hitam(self):
        style = {"layout": "center", "broll": True, "broll_after": 3, "broll_len": 3, "broll_every": 3}
        out = self.render("broll", style, broll_files=[self.broll])
        self.assertAlmostEqual(core.probe(out).duration, 14.0, delta=0.2)
        speaker, inserted = grab(out, 1.5), grab(out, 4.5)
        self.assertGreater(float(speaker[:150].mean()), 30)          # pembicara: layar penuh
        self.assertLess(float(inserted[:150].mean()), 12)            # sisipan: latar hitam di atas
        got = core.analyze_reference_style(out)["style"]
        self.assertTrue(got.get("broll"))
        self.assertEqual(got.get("broll_fit"), "hitam")
        self.assertAlmostEqual(got["broll_after"], 3, delta=1.0)
        self.assertEqual(got.get("layout"), "center")                # tata letak dari bagian pembicara

    def test_referensi_horizontal_dan_gambar(self):
        style = {"layout": "center", "caption_color": "hijau", "title_bg": "kuning", "caption_words": 2}
        vert = self.render("untuk_horizontal", style)
        horiz = os.path.join(self.tmp, "horizontal.mp4")
        core.run_ffmpeg(["-i", vert, "-vf", "scale=-2:1080,pad=1920:1080:(ow-iw)/2:0", "-c:v", "libx264",
                         "-preset", "veryfast", "-an", horiz])
        res = core.analyze_reference_style(horiz)
        self.assertEqual(res["style"].get("caption_color"), "hijau")
        self.assertNotIn("layout", res["style"])                     # tata letak tidak ditiru dari video horizontal
        self.assertTrue(any("bukan video tegak" in n for n in res["notes"]))
        # screenshot: dua gambar dari saat yang berbeda
        import cv2
        shots = []
        for i, t in enumerate((3.0, 8.0)):
            p = os.path.join(self.tmp, f"shot{i}.png")
            cv2.imwrite(p, grab(vert, t))
            shots.append(p)
        got = core.analyze_reference_images(shots)
        self.assertTrue(got["style"].get("captions"))
        self.assertEqual(got["style"].get("caption_color"), "hijau")
        self.assertEqual(got["style"].get("title_bg"), "kuning")
        self.assertNotIn("progress_bar", got["style"])               # tidak bisa dibaca dari gambar diam


class APIBaru(WithOllama):
    """Rute: banyak referensi sekaligus, link akun, font sendiri, pustaka b-roll."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media = tempfile.mkdtemp(prefix="nero-test-media-")
        src = os.path.join(cls.media, "sumber.mp4")
        textured_source(src, 12)
        cls.refs = []
        for name, pid in (("a", "preset:kotak-ungu"), ("b", "preset:neon-pink")):
            out = os.path.join(cls.media, f"{name}.mp4")
            core.render_clip(src, 0.0, 9.0, out, fake_words(),
                             {**DEFAULT_STYLE, **PRESET_BY_ID[pid]["style"], "loudnorm": False, "layout": "center"},
                             TITLE)
            cls.refs.append(out)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media, ignore_errors=True)

    def setUp(self):
        super().setUp()
        import app as server
        self.tmp = tempfile.mkdtemp(prefix="nero-test-")
        data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(data, "clip", "projects"))
        self.patches = [
            mock.patch.object(common, "DATA", data),
            mock.patch.object(common, "SETTINGS_PATH", os.path.join(data, "settings.json")),
            mock.patch.object(clip, "DATA", os.path.join(data, "clip")),
            mock.patch.object(clip, "PROJ", os.path.join(data, "clip", "projects")),
            mock.patch.object(clip, "STYLES_PATH", os.path.join(data, "clip", "styles.json")),
            mock.patch.object(clip, "THUMBS", os.path.join(data, "clip", "thumbs")),
            mock.patch.dict(clip.projects, {}, clear=True),
            mock.patch.dict(core.USER_FONTS, {}, clear=True),
        ]
        for p in self.patches:
            p.start()
        common.update_settings({"local_ai_url": self.url})
        self.c = server.app.test_client()
        self.base = "http://127.0.0.1"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, code=200, **kw):
        r = self.c.open(path, method=method, base_url=self.base, **kw)
        self.assertEqual(r.status_code, code, r.data[:300])
        return r.get_json(silent=True)

    def wait_job(self, jid):
        for _ in range(900):
            job = self.call("GET", f"/api/clip/style-jobs/{jid}")
            if job["status"] != "proses":
                return job
            time.sleep(0.1)
        self.fail("pembacaan gaya tidak selesai")

    def test_banyak_file_video_dan_gambar(self):
        import cv2
        shot = os.path.join(self.tmp, "shot.png")
        cv2.imwrite(shot, grab(self.refs[0], 3.0))
        data = {"files": [(open(self.refs[0], "rb"), "a.mp4"), (open(self.refs[1], "rb"), "b.mp4"),
                          (open(shot, "rb"), "shot.png")]}
        jid = self.call("POST", "/api/clip/style-jobs", content_type="multipart/form-data", data=data)["id"]
        for f, _ in data["files"]:
            f.close()
        job = self.wait_job(jid)
        self.assertEqual(job["status"], "selesai", job.get("error"))
        res = job["result"]
        self.assertEqual(res["refs"], 3)
        self.assertTrue(any("3 referensi" in n for n in res["notes"]))
        mine = self.call("GET", "/api/clip/styles")["styles"]
        self.assertEqual(mine[0]["source"]["count"], 3)
        self.assertIn("+2 referensi", mine[0]["source"]["title"])

    def test_link_akun_diambil_beberapa_video(self):
        calls = []

        def fake_download(url, out_dir, cookies=None, video=False):
            calls.append(url)
            os.makedirs(out_dir, exist_ok=True)
            dst = os.path.join(out_dir, "ref.mp4")
            shutil.copy(self.refs[len(calls) % 2], dst)
            return {"path": dst, "title": f"video {len(calls)}", "duration": 9}
        with mock.patch.object(core, "expand_profile", return_value=["https://x/v1", "https://x/v2"]) as ex, \
                mock.patch.object(core, "download_reference", side_effect=fake_download):
            jid = self.call("POST", "/api/clip/style-jobs",
                            json={"urls": "https://www.tiktok.com/@kreator", "per_profile": 2, "use_ai": False})["id"]
            job = self.wait_job(jid)
        self.assertEqual(job["status"], "selesai", job.get("error"))
        self.assertEqual(ex.call_args[0][1], 2)
        self.assertEqual(calls, ["https://x/v1", "https://x/v2"])
        mine = self.call("GET", "/api/clip/styles")["styles"]
        self.assertEqual(mine[0]["source"]["title"], "@kreator (2 video)")
        self.assertEqual(mine[0]["source"]["urls"], ["https://www.tiktok.com/@kreator"])

    def test_terlalu_banyak_dan_jenis_salah(self):
        self.call("POST", "/api/clip/style-jobs", 400, json={"urls": "\n".join(f"https://x/{i}" for i in range(6))})
        self.call("POST", "/api/clip/style-jobs", 400, content_type="multipart/form-data",
                  data={"files": [(io.BytesIO(b"x"), "dok.pdf")]})

    def test_font_sendiri(self):
        with open(os.path.join(core.FONTS_DIR, "Anton-Regular.ttf"), "rb") as f:
            font = self.call("POST", "/api/clip/fonts", content_type="multipart/form-data",
                             data={"file": (io.BytesIO(f.read()), "anton.ttf")})["font"]
        self.assertTrue(font["key"].startswith("user-anton"))
        self.assertIn(font["key"], core.USER_FONTS)
        self.assertIn(font["key"], style_ai.available_fonts())       # ikut dibandingkan AI
        self.assertEqual(self.call("GET", "/api/clip/fonts")["fonts"][0]["key"], font["key"])
        r = self.c.get(f"/api/clip/fonts/{font['key']}", base_url=self.base)
        self.assertEqual(r.status_code, 200)
        r.close()
        self.assertEqual(core._font_by_key(font["key"], 40).getname()[0], "Anton")
        self.call("POST", "/api/clip/fonts", 400, content_type="multipart/form-data",
                  data={"file": (io.BytesIO(b"bukan font"), "x.ttf")})
        self.call("DELETE", f"/api/clip/fonts/{font['key']}")
        self.assertNotIn(font["key"], core.USER_FONTS)

    def test_pustaka_broll(self):
        with open(self.refs[0], "rb") as f:
            added = self.call("POST", "/api/clip/broll", content_type="multipart/form-data",
                              data={"files": [(io.BytesIO(f.read()), "anime keren.mp4")]})["added"]
        items = self.call("GET", "/api/clip/broll")["items"]
        self.assertEqual([x["name"] for x in items], added)
        self.assertAlmostEqual(items[0]["seconds"], 9, delta=0.5)
        r = self.c.get(f"/api/clip/broll/{added[0]}/thumb", base_url=self.base)
        self.assertEqual(r.data[:2], b"\xff\xd8")
        r.close()
        self.assertEqual(clip.broll_files(), [os.path.join(clip.broll_dir(), added[0])])
        self.call("POST", "/api/clip/broll", 400, content_type="multipart/form-data",
                  data={"files": [(io.BytesIO(b"x"), "lagu.mp3")]})
        self.call("DELETE", f"/api/clip/broll/{added[0]}")
        for _ in range(50):
            if not clip.broll_files():
                break
            time.sleep(0.1)
        self.assertEqual(clip.broll_files(), [])

    def test_gemini_menonton_beberapa_video(self):
        got = gemini_ai.chat_json("x", {"type": "object"}, key="kunci-uji", videos=[b"v1", b"v2"], images=[b"\xff\xd8"])
        self.assertTrue(got)
        body = [b for p, b in FakeOllama.log if p == "/v1beta/interactions"][-1]
        self.assertEqual([x["type"] for x in body["input"]], ["video", "video", "image", "text"])


if __name__ == "__main__":
    unittest.main()
