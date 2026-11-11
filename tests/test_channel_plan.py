"""Channel Otomatis, Fase I-2: riset, skrip, QC (+ tulis ulang), SEO, dan pipeline 'plan' dengan checkpoint.
Tanpa jaringan: Wikipedia/RSS dan LLM di-mock. Prompt tetap dirender dengan variabel asli, supaya templat
yang salah ketahuan."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import json
import os
from datetime import UTC, date, datetime
from unittest import mock
import unittest

from tests.util import ROOT  # noqa: F401
from tests.test_channel_core import ChannelSandbox

from features.channel import config, genres, llm, pipeline, research, state, writer
from features.channel.schemas import QC, HistoryEntry, Issue, Metadata, Research, ResearchDraft, Script, Source

URL1 = "https://id.wikipedia.org/wiki/Gurita"
URL2 = "https://en.wikipedia.org/wiki/Tardigrade"
URL3 = "https://id.wikipedia.org/wiki/Paus_biru"


def materials():
    return [Source(title="Gurita (Wikipedia id)", url=URL1, snippet="Gurita memiliki tiga jantung dan darah biru."),
            Source(title="Tardigrade (Wikipedia en)", url=URL2, snippet="Tardigrades survive extreme conditions."),
            Source(title="Paus biru (Wikipedia id)", url=URL3, snippet="Paus biru adalah hewan terbesar.")]


def draft(*cands):
    return {"candidates": list(cands)}


def cand(topic, ids, total=24, angle="sudut unik"):
    s = total / 4
    return {"topic": topic, "angle": angle, "why_now": "evergreen", "source_ids": ids,
            "scores": {"relevance": s, "freshness": s, "novelty": s, "learnings_fit": s}}


def words(n, word="kata"):
    return " ".join([word] * n)


def long_script(fmt="myth_vs_fact", url=URL1, n_words=20, hook="Hewan laut ini punya tiga jantung sekaligus"):
    types = ["stock", "motion_graphic", "ai_image", "stock", "motion_graphic", "stock"]
    segs = []
    for i, t in enumerate(types, 1):
        seg = {"id": i, "text": words(n_words, f"fakta{i}"), "visual_type": t, "visual_query": "octopus underwater"}
        if t == "ai_image":
            seg["image_prompt"] = "an octopus in deep blue ocean, soft light, wide angle"
        if t == "motion_graphic":
            seg["graphic"] = {"template": "big_number", "content": "3"}
        segs.append(seg)
    return {"topic": "Gurita tiga jantung", "format": fmt, "hook": hook, "segments": segs,
            "cta": "Ikuti untuk fakta laut lainnya", "claims": [{"claim": "tiga jantung", "source_url": url}]}


SEO = {"title": "Kenapa gurita punya tiga jantung?", "summary": "Gurita punya tiga jantung. Ini alasannya.",
       "tags": ["gurita", "fakta hewan", "hewan laut", "sains", "biologi", "laut", "fakta unik", "octopus"],
       "hashtags": ["#fakta", "gurita", "#Shorts"]}


def qc_answer(score=8, issues=(), supported=True, evidence=None):
    """Jawaban QC palsu: satu pemeriksaan per segmen, dengan kutipan bukti dari sumber pertama."""
    def answer(v):
        script = json.loads(v["script"])
        quote = evidence if evidence is not None else v["sources"][0]["snippet"][:40]
        checks = [{"segment_id": s["id"], "statement": s["text"][:60], "kind": "fact", "supported": supported,
                   "evidence": quote} for s in script["segments"]]
        checks.append({"segment_id": 0, "statement": script["hook"], "kind": "framing", "supported": True,
                       "evidence": ""})
        return {"pass": True, "score": score, "checks": checks, "issues": list(issues)}
    return answer


class FakeLLM:
    """Pengganti llm.generate_json: merender prompt asli, lalu menjawab sesuai nama prompt."""

    def __init__(self, **answers):
        self.answers = {k: list(v) if isinstance(v, list) else [v] for k, v in answers.items()}
        self.calls = []

    def __call__(self, prompt_name, variables, schema, temperature, *, check=None, allow_local=True, **kw):
        text = llm.render_prompt(prompt_name, variables)
        self.calls.append({"name": prompt_name, "vars": variables, "text": text, "allow_local": allow_local,
                           "temperature": temperature})
        queue = self.answers[prompt_name]
        ans = queue.pop(0) if len(queue) > 1 else queue[0]
        ans = ans(variables) if callable(ans) else ans
        return schema.model_validate(ans)

    def names(self):
        return [c["name"] for c in self.calls]


def cfg_with_genre():
    return genres.apply(config.ChannelConfig(), "fakta-sains")


# --------------------------------------------------------------------------
class Wikipedia(unittest.TestCase):
    def setUp(self):
        self.waits = []
        p = mock.patch.object(research, "_sleep", self.waits.append)
        p.start()
        self.addCleanup(p.stop)

    def resp(self, data, status=200, headers=None):
        r = mock.Mock(status_code=status, headers=headers or {})
        r.json.return_value = data
        r.raise_for_status.return_value = None
        return r

    def test_cari_dan_baca_artikel_satu_permintaan(self):
        s = mock.Mock()
        s.get.side_effect = [self.resp({"query": {"pages": [
            {"index": 2, "title": "Gurita cincin biru", "fullurl": URL3, "extract": "Gurita cincin biru " + words(20)},
            {"index": 1, "title": "Gurita", "fullurl": URL1, "touched": "2026-06-13T16:14:06Z",
             "extract": "== Anatomi ==\nGurita adalah moluska " + words(20)},
        ]}})]
        got = research.wiki_search("id", "gurita", 2, 900, s)
        self.assertEqual([x.url for x in got], [URL1, URL3])            # urut sesuai peringkat pencarian
        self.assertEqual(got[0].published, "2026-06-13")
        self.assertTrue(got[0].snippet.startswith("Anatomi. Gurita adalah moluska"))
        params = s.get.call_args.kwargs["params"]
        self.assertEqual((params["generator"], params["gsrsearch"], params["exintro"]), ("search", "gurita", 1))
        self.assertEqual(s.get.call_args.kwargs["headers"]["User-Agent"], research.HEADERS["User-Agent"])
        self.assertEqual(self.waits, [research.REQUEST_GAP])

    def test_429_ditunggu_lalu_diulang(self):
        s = mock.Mock()
        ok = self.resp({"query": {"pages": [{"title": "G", "fullurl": URL1, "extract": words(30)}]}})
        s.get.side_effect = [self.resp({}, 429, {"Retry-After": "5"}), self.resp({}, 429), ok]
        self.assertEqual(research.wiki_extract("id", "G", 4000, s).url, URL1)
        self.assertEqual(self.waits, [research.REQUEST_GAP, 5.0, research.REQUEST_GAP, 4.0, research.REQUEST_GAP])
        self.assertNotIn("exchars", s.get.call_args.kwargs["params"])   # teks penuh, dipotong di kode

    def test_disambiguasi_dan_kosong_dilewati(self):
        s = mock.Mock()
        s.get.side_effect = [
            self.resp({"query": {"pages": [{"title": "X", "fullurl": "u", "extract": words(30),
                                            "pageprops": {"disambiguation": ""}}]}}),
            self.resp({"query": {"pages": [{"title": "Y", "missing": True}]}}),
            self.resp({"query": {"pages": [{"title": "Z", "fullurl": "u", "extract": "pendek"}]}}),
        ]
        for _ in range(3):
            self.assertIsNone(research.wiki_extract("id", "x", 900, s))

    def test_perkaya_sumber_terpilih(self):
        c = research.Candidate(topic="t", angle="a", sources=materials()[:1] + [
            Source(title="Berita", url="https://contoh.id/x", snippet="isi")])
        longer = Source(title="Gurita", url=URL1, snippet=words(200))
        with mock.patch.object(research, "wiki_extract", return_value=longer) as wx:
            out = research.enrich(c, log=lambda m: None)
        wx.assert_called_once_with("id", "Gurita", research.SNIPPET_CHOSEN)
        self.assertEqual(len(out.sources[0].snippet), len(longer.snippet))
        self.assertEqual(out.sources[1].snippet, "isi")            # bukan Wikipedia: tetap
        self.assertEqual(c.sources[0].snippet, materials()[0].snippet)   # kandidat asli tidak berubah


class RSS(unittest.TestCase):
    FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
      <item><title>Baru &amp; segar</title><link>https://contoh.id/baru</link>
        <pubDate>Wed, 11 Nov 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Isi &lt;b&gt;baru&lt;/b&gt;&lt;/p&gt;</description></item>
      <item><title>Lama</title><link>https://contoh.id/lama</link>
        <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate><description>lama</description></item>
    </channel></rss>"""

    def test_hanya_item_dalam_rentang(self):
        r = mock.Mock(content=self.FEED)
        r.raise_for_status.return_value = None
        with mock.patch("requests.get", return_value=r):
            got = research.rss_materials(["https://contoh.id/rss"], 72, now=datetime(2026, 11, 12, tzinfo=UTC),
                                         log=lambda m: None)
        self.assertEqual([s.url for s in got], ["https://contoh.id/baru"])
        self.assertEqual(got[0].title, "Baru & segar")
        self.assertEqual(got[0].snippet, "Isi baru")
        self.assertEqual(got[0].published, "2026-11-11")


# --------------------------------------------------------------------------
class PilihTopik(ChannelSandbox):
    def run_research(self, fake, history=()):
        for h in history:
            state.upsert_history(h)
        with mock.patch.object(llm, "generate_json", fake), \
                mock.patch.object(research, "wikipedia_materials", return_value=materials()), \
                mock.patch.object(research, "enrich", side_effect=lambda c, log=None: c):
            return research.run(cfg_with_genre(), date(2026, 11, 12), log=lambda m: None)

    def test_sumber_dari_bahan_dan_skor_tertinggi(self):
        fake = FakeLLM(research_queries={"queries": [{"id": "gurita", "en": "octopus"}, {"id": "paus"}]},
                       research=draft(cand("Gurita tiga jantung", [1], 24), cand("Tanpa sumber", [9], 40),
                                      cand("Tardigrada tahan segalanya", [2, 2, 3], 30)))
        r = self.run_research(fake)
        self.assertEqual([c.topic for c in r.candidates], ["Gurita tiga jantung", "Tardigrada tahan segalanya"])
        self.assertEqual(r.chosen.topic, "Tardigrada tahan segalanya")
        self.assertEqual([s.url for s in r.chosen.sources], [URL2, URL3])   # nomor kembar dibuang
        self.assertEqual(fake.names(), ["research_queries", "research"])     # tanpa riwayat: tanpa cek makna
        self.assertIn("[2] Tardigrade (Wikipedia en)", fake.calls[1]["text"])

    def test_duplikat_dan_terlarang_ditolak(self):
        hist = [HistoryEntry(date="2026-11-01", topic="Gurita punya tiga jantung")]
        fake = FakeLLM(research_queries={"queries": [{"id": "aa"}, {"id": "bb"}]},
                       research=draft(cand("Gurita tiga jantung", [1]), cand("Politik praktis di laut", [3]),
                                      cand("Tardigrada", [2]), cand("Paus biru raksasa", [3])),
                       dedupe={"duplicate_ids": [2]})
        r = self.run_research(fake, hist)
        self.assertEqual([c.topic for c in r.candidates], ["Tardigrada"])
        self.assertEqual(len(r.rejected_as_duplicate), 3)
        self.assertIn("mirip video lama", r.rejected_as_duplicate[0])
        self.assertIn("topik terlarang", r.rejected_as_duplicate[1])
        self.assertIn("sama maknanya", r.rejected_as_duplicate[2])
        self.assertEqual(fake.calls[-1]["vars"]["recent_topics"], ["Gurita punya tiga jantung"])

    def test_semua_ditolak(self):
        fake = FakeLLM(research_queries={"queries": [{"id": "aa"}, {"id": "bb"}]},
                       research=draft(cand("SARA di laut", [1])))
        with self.assertRaises(research.ResearchError):
            self.run_research(fake)

    def test_kata_terlarang_utuh_saja(self):
        self.assertIsNone(research.banned_hit("sarana belajar", ["SARA"]))
        self.assertEqual(research.banned_hit("isu SARA", ["SARA"]), "SARA")

    def test_pemeriksaan_jumlah_kandidat_bersumber(self):
        d = ResearchDraft.model_validate(draft(cand("a", [1]), cand("b", [7])))
        self.assertEqual(len(research.build_candidates(d, materials())), 1)


# --------------------------------------------------------------------------
class Rotasi(unittest.TestCase):
    ROT = ("myth_vs_fact", "list_3", "story", "question", "explainer")

    def test_rotasi(self):
        self.assertEqual(writer.pick_format(self.ROT, []), "myth_vs_fact")
        self.assertEqual(writer.pick_format(self.ROT, ["myth_vs_fact"]), "list_3")
        self.assertEqual(writer.pick_format(self.ROT, ["story", "explainer"]), "myth_vs_fact")
        for hist in (["list_3", "story"], ["explainer", "myth_vs_fact"], ["question", "question"]):
            got = writer.pick_format(self.ROT, hist)
            self.assertNotIn(got, hist[-2:])

    def test_format_asing_di_riwayat(self):
        self.assertEqual(writer.pick_format(self.ROT, ["vlog"]), "myth_vs_fact")


class PemeriksaanSkrip(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_with_genre()
        self.res = Research(date="2026-11-12", candidates=[research.Candidate(topic="Gurita", angle="a",
                                                                               sources=materials()[:1])])

    def issues(self, data, fmt="myth_vs_fact", hooks=()):
        return writer.script_issues(Script.model_validate(data), self.cfg, self.res, fmt, list(hooks))

    def test_skrip_baik_tanpa_masalah(self):
        s = Script.model_validate(long_script())
        lo, hi = config.words_range(self.cfg)
        self.assertTrue(lo <= s.word_count <= hi, (s.word_count, lo, hi))
        self.assertEqual(self.issues(long_script()), [])

    def test_berbagai_masalah(self):
        allai = long_script()
        for seg in allai["segments"]:
            seg.update(visual_type="ai_image", image_prompt="a calm ocean scene at dawn")
        cases = {
            "length": long_script(n_words=5),
            "repetition": long_script(fmt="story"),
            "quality": allai,
            "fact": long_script(url="https://karangan.example/x"),
        }
        for kind, data in cases.items():
            with self.subTest(kind):
                self.assertIn(kind, {i.type for i in self.issues(data)})
        hook = self.issues(long_script(), hooks=["Hewan laut ini punya tiga jantung sekaligus!"])
        self.assertEqual([i.type for i in hook], ["repetition"])
        long_hook = self.issues(long_script(hook=words(13, "hook")))
        self.assertTrue(any("hook 13 kata" in i.detail for i in long_hook))
        osd = long_script()
        osd["segments"][0]["on_screen_text"] = "satu dua tiga empat lima enam"
        self.assertTrue(any("on_screen_text" in i.detail for i in self.issues(osd)))

    def test_satu_jenis_visual(self):
        data = long_script()
        for seg in data["segments"]:
            seg.update(visual_type="stock")
        self.assertTrue(any("jenis visual" in i.detail for i in self.issues(data)))


class TulisDanQC(ChannelSandbox):
    def setUp(self):
        super().setUp()
        self.cfg = cfg_with_genre()
        self.res = Research(date="2026-11-12", candidates=[research.Candidate(topic="Gurita", angle="a",
                                                                               sources=materials()[:1])])

    def go(self, fake, **kw):
        with mock.patch.object(llm, "generate_json", fake):
            return writer.write_and_review(self.cfg, self.res, "myth_vs_fact", log=lambda m: None, **kw)

    def test_gagal_lalu_tulis_ulang_lalu_lulus(self):
        saved = []
        fake = FakeLLM(script=[long_script(hook="Versi pertama"), long_script(hook="Versi kedua yang lebih baik")],
                       qc=[qc_answer(5, [{"type": "quality", "detail": "hook lemah", "fix": "pertajam"}]),
                           qc_answer(8)])
        script, qc = self.go(fake, on_script=saved.append)
        self.assertTrue(qc.passed)
        self.assertEqual(script.hook, "Versi kedua yang lebih baik")
        self.assertEqual(fake.names(), ["script", "qc", "script", "qc"])
        rewrite = fake.calls[2]
        self.assertIn("INI PENULISAN ULANG", rewrite["text"])
        self.assertIn("hook lemah", rewrite["text"])
        self.assertIn("Versi pertama", rewrite["text"])
        self.assertEqual(len(saved), 2)
        qc_call = fake.calls[1]
        self.assertFalse(qc_call["allow_local"])                       # QC tidak boleh memakai AI lokal
        self.assertEqual(qc_call["temperature"], self.cfg.llm.temperature_strict)

    def test_maksimal_dua_kali_tulis_ulang(self):
        fake = FakeLLM(script=long_script(), qc={"score": 9, "issues": [{"type": "fact", "detail": "tanpa sumber"}]})
        _, qc = self.go(fake)
        self.assertFalse(qc.passed)
        self.assertEqual(fake.names(), ["script", "qc", "script", "qc", "script", "qc"])

    def test_bukti_dicocokkan_dengan_sumber(self):
        script = Script.model_validate(long_script())
        def run(**kw):
            return self.review(script, qc_answer(9, **kw))

        ok = run()
        self.assertTrue(ok.passed, ok.issues)
        self.assertEqual(len(ok.checks), len(script.segments) + 1)
        # beda spasi & huruf besar tetap dianggap kutipan yang sama
        self.assertTrue(run(evidence="GURITA  memiliki tiga\njantung").passed)
        for kw, why in (({"supported": False}, "tidak didukung"),
                        ({"evidence": "Gurita hidup seribu tahun di gurun pasir."}, "tidak ditemukan"),
                        ({"evidence": ""}, "tidak ditemukan")):
            with self.subTest(why):
                qc = run(**kw)
                self.assertFalse(qc.passed)
                self.assertTrue(any(i.type == "fact" and why in i.detail for i in qc.issues))

    def test_kata_mutlak_butuh_bukti_setara(self):
        data = long_script()
        data["segments"][0]["text"] = "Faktanya mereka hanya dapat hidup selama sepuluh hari di luar angkasa " + words(9)
        data["segments"][1]["text"] = "Banyak yang mengira hewan ini abadi di ruang hampa " + words(10)
        data["segments"][3]["text"] = "Ukuran paling kecil hewan ini nol koma satu milimeter " + words(10)
        script = Script.model_validate(data)

        def answer(v):
            got = qc_answer(9)(v)
            for c in got["checks"]:
                if c["segment_id"] == 4:
                    c["evidence"] = "Gurita memiliki tiga jantung dan darah biru."
            return got

        qc = self.review(script, answer)
        flagged = [i.detail for i in qc.issues if "sumber tidak menyatakan" in i.detail]
        self.assertTrue(any("segmen 1" in d and "hanya" in d for d in flagged), flagged)
        self.assertTrue(any("segmen 2" in d and "banyak yang" in d for d in flagged), flagged)
        self.assertTrue(any("segmen 4" in d and "paling" in d for d in flagged), flagged)
        self.assertFalse(any("segmen 3" in d for d in flagged), flagged)   # tanpa kata mutlak
        self.assertFalse(qc.passed)
        # kata mutlak yang memang ada di bukti tidak dipersoalkan
        ok = writer.absolute_issues(QC.model_validate({"score": 9, "checks": [
            {"segment_id": 4, "statement": "s", "kind": "fact", "supported": True,
             "evidence": "paling kecil ukurannya 0,1 mm"}]}), script)
        self.assertFalse(any("segmen 4" in i.detail for i in ok))

    def test_qc_harus_memeriksa_semua_segmen(self):
        script = Script.model_validate(long_script())

        def partial(v):
            got = qc_answer(9)(v)
            got["checks"] = got["checks"][:2]
            return got

        qc = self.review(script, partial)
        self.assertFalse(qc.passed)
        self.assertTrue(any("belum mencakup segmen [3, 4, 5, 6]" in i.detail for i in qc.issues))

    def test_pembuka_berangka_bukan_framing(self):
        def sneaky(v):
            got = qc_answer(9)(v)
            got["checks"].append({"segment_id": 1, "statement": "90% orang percaya mitos ini", "kind": "framing",
                                  "supported": True, "evidence": ""})
            return got

        qc = self.review(Script.model_validate(long_script()), sneaky)
        self.assertFalse(qc.passed)

    def test_angka_nama_diri_bukan_klaim(self):
        """Uji nyata 12 Nov 2026: hook "Benarkah Voyager 1 keluar dari tata surya?" ditolak karena angka '1'."""
        res = Research.model_validate({"date": "2026-11-12", "candidates": [{
            "topic": "Perjalanan Voyager 1", "angle": "a",
            "sources": [{"title": "Voyager 1", "url": URL1, "snippet": "Voyager 1 dan Apollo 11. COVID-19."}]}]})
        self.assertFalse(writer.has_quantity("Benarkah Voyager 1 keluar dari tata surya?", res))
        self.assertFalse(writer.has_quantity("Benarkah COVID-19 menular?", res))
        self.assertTrue(writer.has_quantity("Benarkah 5 planet bisa terlihat?", res))
        self.assertTrue(writer.has_quantity("Apollo 11 membawa 3 astronot", res))
        self.assertTrue(writer.has_quantity("Benarkah Pioneer 10 lebih dulu?", res))   # nama tidak ada di sumber

    def test_kata_mutlak_di_kalimat_mitos(self):
        self.assertEqual(writer.asserted_markers("Ada anggapan bahwa Voyager 1 meninggalkan seluruh tata surya."), [])
        self.assertEqual(writer.asserted_markers("Benarkah semua bintang sama? Faktanya hanya sebagian."), ["hanya"])
        self.assertEqual(writer.asserted_markers("Ada anggapan bahwa banyak orang percaya ini."), ["banyak orang"])
        self.assertEqual(writer.asserted_markers("Voyager adalah wahana paling jauh."), ["paling"])

    def review(self, script, answer):
        with mock.patch.object(llm, "generate_json", FakeLLM(qc=answer)):
            return writer.review(self.cfg, script, self.res, "myth_vs_fact", log=lambda m: None)

    def test_masalah_pasti_menurunkan_skor(self):
        fake = FakeLLM(qc={"score": 9, "issues": []})
        with mock.patch.object(llm, "generate_json", fake):
            qc = writer.review(self.cfg, Script.model_validate(long_script(n_words=4)), self.res, "myth_vs_fact",
                               log=lambda m: None)
        self.assertFalse(qc.passed)
        self.assertLessEqual(qc.score, writer.CODE_ISSUE_SCORE_CAP)
        self.assertIn("length", [i.type for i in qc.issues])


class Seo(unittest.TestCase):
    def test_hashtag_dan_tag(self):
        self.assertEqual(writer.normalize_hashtags(["fakta", "#Shorts", "#gurita laut", "#a", "#b", "#c"]),
                         ["#Shorts", "#fakta", "#guritalaut", "#a", "#b"])
        self.assertEqual(writer.normalize_tags(["Gurita", "gurita", "#laut"], ["fakta"]), ["Gurita", "laut", "fakta"])

    def test_deskripsi_dan_metadata(self):
        cfg = cfg_with_genre()
        cfg.youtube.default_tags = ["nero"]
        res = Research(date="2026-11-12", candidates=[research.Candidate(topic="Gurita", angle="a",
                                                                          sources=materials()[:1])])
        fake = FakeLLM(seo=SEO)
        with mock.patch.object(llm, "generate_json", fake):
            meta = writer.seo(cfg, Script.model_validate(long_script()), res, log=lambda m: None)
        self.assertEqual(meta.hashtags[0], "#Shorts")
        self.assertIn("nero", meta.tags)
        self.assertEqual(meta.category_id, "28")
        d = meta.description
        self.assertIn("Sumber:\n- " + URL1, d)
        self.assertIn("Video: Pexels/Pixabay", d)
        self.assertIn("Sebagian visual dibuat dengan AI", d)
        self.assertTrue(d.rstrip().endswith("#Shorts #fakta #gurita"))
        self.assertNotIn("\n\n\n", d)
        few = FakeLLM(seo={**SEO, "hashtags": ["#Shorts"]})
        with mock.patch.object(llm, "generate_json", few):
            meta = writer.seo(cfg, Script.model_validate(long_script()), res, log=lambda m: None)
        self.assertEqual(meta.hashtags, ["#Shorts", "#Gurita", "#FaktaHewan", "#HewanLaut", "#Sains"])
        plain = writer.build_description("Ringkas.", Script.model_validate(long_script()), ["#Shorts"],
                                         uses_stock=False, uses_ai=False)
        self.assertNotIn("Pexels", plain)
        self.assertNotIn("AI", plain)
        self.assertEqual(plain, f"Ringkas.\n\nSumber:\n- {URL1}\n\n#Shorts")
        ai_only = writer.build_description("Ringkas.", Script.model_validate(long_script()), ["#Shorts"],
                                           uses_stock=False, uses_ai=True)
        self.assertEqual(ai_only, f"Ringkas.\n\nSumber:\n- {URL1}\n\nSebagian visual dibuat dengan AI.\n\n#Shorts")


# --------------------------------------------------------------------------
class PipelinePlan(ChannelSandbox):
    def fake(self, qc=None):
        return FakeLLM(research_queries={"queries": [{"id": "gurita", "en": "octopus"}, {"id": "paus"}]},
                       research=draft(cand("Gurita tiga jantung", [1], 30), cand("Paus biru", [3], 20)), dedupe={"duplicate_ids": []},
                       script=lambda v: long_script(v["format"], v["sources"][0]["url"]),
                       qc=qc or qc_answer(8), seo=SEO)

    def run_plan(self, fake, **kw):
        out = []
        with mock.patch.object(llm, "generate_json", fake), \
                mock.patch.object(research, "wikipedia_materials", return_value=materials()), \
                mock.patch.object(research, "enrich", side_effect=lambda c, log=None: c):
            code = pipeline.run_daily(date(2026, 11, 12), echo=out.append, **{"dry_run": True, "only": "plan",
                                                                              "genre": "fakta-sains", **kw})
        return code, "\n".join(out)

    def run_dir(self, *p):
        return os.path.join(self.data, "channel", "runs", "2026-11-12", *p)

    def test_plan_menghasilkan_empat_json(self):
        code, out = self.run_plan(self.fake())
        self.assertEqual(code, 0, out)
        for name in ("research.json", "script.json", "qc.json", "metadata.json", "run.log"):
            self.assertTrue(os.path.isfile(self.run_dir(name)), name)
        with open(self.run_dir("qc.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["pass"])
        self.assertFalse(os.path.exists(self.run_dir(".lock")))
        self.assertFalse(os.path.exists(os.path.join(self.data, "channel", "history.json")))  # plan tidak mencatat

    def test_checkpoint_dan_force_berantai(self):
        self.run_plan(self.fake())
        again = self.fake()
        code, out = self.run_plan(again)
        self.assertEqual(code, 0)
        self.assertEqual(again.calls, [])
        self.assertIn("dipakai ulang: research, script, qc, seo", out)
        forced = self.fake()
        code, out = self.run_plan(forced, force=["script"])
        self.assertEqual(code, 0)
        self.assertEqual(forced.names(), ["script", "qc", "seo"])    # riset tidak diulang, sesudahnya diulang

    def test_format_mengikuti_riwayat(self):
        state.upsert_history(HistoryEntry(date="2026-11-10", topic="a", format="myth_vs_fact"))
        state.upsert_history(HistoryEntry(date="2026-11-11", topic="b", format="list_3"))
        fake = self.fake()
        self.run_plan(fake)
        with open(self.run_dir("script.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["format"], "story")

    def test_qc_gagal_menghentikan(self):
        bad = {"score": 9, "issues": [{"type": "policy", "detail": "nasihat medis"}]}
        code, out = self.run_plan(self.fake(qc=bad))
        self.assertEqual(code, 1)
        self.assertIn("QC gagal", out)
        self.assertFalse(os.path.exists(self.run_dir("metadata.json")))
        with open(self.run_dir("qc.json"), encoding="utf-8") as f:
            self.assertFalse(json.load(f)["pass"])
        with open(self.run_dir("run.log"), encoding="utf-8") as f:
            self.assertIn("Traceback", f.read())
        code, out = self.run_plan(self.fake())                      # tanpa --force: tetap berhenti
        self.assertEqual(code, 1)
        self.assertIn("--force script", out)
        code, _ = self.run_plan(self.fake(), force=["script"])
        self.assertEqual(code, 0)

    def test_penjaga(self):
        out = []
        self.assertEqual(pipeline.run_daily(date(2026, 11, 12), dry_run=True, only="plan", echo=out.append),
                         pipeline.EXIT_CONFIG)                       # genre belum dipilih
        self.assertIn("belum dipilih", out[-1])
        self.assertEqual(pipeline.run_daily(date(2026, 11, 12), only="plan", genre="fakta-sains", echo=out.append),
                         pipeline.EXIT_CONFIG)                       # masih nonaktif & bukan uji coba
        config.save_config(genres.as_patch("alam-hewan"))
        self.assertEqual(config.load_config().channel.niche, genres.GENRE_BY_ID["alam-hewan"]["niche"])
        from features.channel.runctx import RunContext
        with RunContext(date(2026, 11, 12), echo=None):
            code, out = self.run_plan(self.fake())
        self.assertEqual(code, pipeline.EXIT_LOCKED)

    def test_langkah_hanya_satu(self):
        self.run_plan(self.fake())
        fake = self.fake()
        code, _ = self.run_plan(fake, only="seo", force=["seo"])
        self.assertEqual(code, 0)
        self.assertEqual(fake.names(), ["seo"])
        with open(self.run_dir("metadata.json"), encoding="utf-8") as f:
            meta = Metadata.model_validate(json.load(f))
        self.assertEqual(meta.title, SEO["title"])


class Prompt(unittest.TestCase):
    def test_qc_ditulis_dengan_masalah(self):
        text = llm.render_prompt("script", {
            "niche": "n", "audience": "a", "tone": "t", "banned_topics": ["x"], "format": "story", "topic": "tp",
            "angle": "ag", "words_min": 110, "words_max": 135, "seconds_min": 45, "seconds_max": 58,
            "sources": [{"title": "s", "url": URL1, "snippet": "isi"}], "recent_hooks": ["hook lama"],
            "learnings": "", "allowed_visuals": ["stock"], "max_ai_pct": 60, "min_types": 1,
            "issues": [Issue(type="fact", detail="d", fix="f").model_dump()], "previous": "{}"})
        self.assertIn("hook lama", text)
        self.assertIn("[fact] d → perbaikan: f", text)
        self.assertIn("Topik terlarang: x.", text)
        self.assertNotIn("PELAJARAN DARI PERFORMA", text)


if __name__ == "__main__":
    unittest.main()
