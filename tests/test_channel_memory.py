"""Channel Otomatis, Fase I-akhir: ingatan konten antarhari (topik 60 hari, 2 format & 5 hook terakhir, klip stok
30 hari) dari riwayat YouTube DAN video yang sudah dibuat run sebelumnya. Tanpa jaringan."""
# ruff: noqa: I001  (tests.util harus diimpor lebih dulu: menambahkan folder aplikasi ke sys.path)
import os
import unittest
from datetime import date
from unittest import mock

from tests.util import ROOT  # noqa: F401
from tests.test_channel_core import ChannelSandbox, script_data

import app  # noqa: F401  diimpor di luar patch: app.py mendaftarkan pengaturan semua fitur
from features.channel import config, memory, pipeline, runctx, state, writer
from features.channel.schemas import QC, HistoryEntry, RenderInfo, Research, Script, Visuals


def visuals(*clip_ids):
    segs = [{"segment_id": i + 1, "type": "stock", "requested": "stock", "file": f"clips/seg_{i + 1}.mp4",
             "source": c.split(":")[0], "source_id": c.split(":")[1]} for i, c in enumerate(clip_ids)]
    segs.append({"segment_id": 99, "type": "motion_graphic", "requested": "motion_graphic", "file": "clips/x.mp4",
                 "source": "nero"})
    return Visuals.model_validate({"segments": segs})


class MemorySandbox(ChannelSandbox):
    def made(self, d, *, topic=None, fmt="myth_vs_fact", hook=None, clips=(), qc_ok=True, valid=True,
             render=True, stale=False, research_topic=None):
        """Folder run tanggal `d` yang menghasilkan video (bawaan: QC lulus, video valid milik naskah ini)."""
        day = date.fromisoformat(d)
        ctx = runctx.RunContext(day, echo=None)
        os.makedirs(ctx.dir, exist_ok=True)
        script = Script.model_validate(script_data(topic=topic or f"naskah {d}", format=fmt,
                                                   hook=hook or f"Hook unik untuk tanggal {d}"))
        ctx.save("research", Research.model_validate({"date": d, "candidates": [{
            "topic": research_topic or f"topik {d}", "angle": "a",
            "sources": [{"title": "x", "url": "https://x/1"}]}]}))
        ctx.save("script", script)
        ctx.save("qc", QC.model_validate({"score": 8 if qc_ok else 4, "passed": qc_ok, "checks": [], "issues": []}))
        ctx.save("visuals", visuals(*clips))
        if render:
            ctx.save("render", RenderInfo(width=1080, height=1920, fps=30, duration=50, has_audio=True, valid=valid,
                                          needs_review=True))
        src = "naskah-lama" if stale else runctx.sha(script)
        for step in ("research", "script", "qc", "visuals", "render"):
            ctx.set_fingerprint(step, "x")
        for step in ("visuals", "render"):
            ctx.set_fingerprint(f"{step}_src", src)
        return ctx


class IngatanDariFolderRun(MemorySandbox):
    def test_mode_manual_tanpa_youtube_tetap_ingat(self):
        self.made("2026-11-10", fmt="myth_vs_fact", clips=("pexels:1",))
        self.made("2026-11-11", fmt="list_3", clips=("pexels:2",))
        self.made("2026-11-12", fmt="question", clips=("pixabay:3",))
        self.assertEqual(state.load_history(), [])                    # riwayat YouTube kosong
        today = date(2026, 11, 13)
        self.assertEqual(memory.last_formats(today), ["list_3", "question"])
        self.assertEqual(memory.last_hooks(today), [f"Hook unik untuk tanggal 2026-11-{d}" for d in (10, 11, 12)])
        self.assertEqual(memory.recent_clip_ids(today), {"pexels:1", "pexels:2", "pixabay:3"})
        topics = memory.recent_topics(today)
        for d in ("10", "11", "12"):
            self.assertIn(f"topik 2026-11-{d}", topics)               # topik riset
            self.assertIn(f"naskah 2026-11-{d}", topics)              # dan topik naskah

    def test_format_berputar_dari_hari_ke_hari(self):
        rot = ["myth_vs_fact", "list_3", "question", "story"]
        days = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
        chosen = []
        for d in days:
            fmt = writer.pick_format(rot, memory.last_formats(date.fromisoformat(d)))
            chosen.append(fmt)
            self.made(d, fmt=fmt)
        self.assertEqual(chosen, rot)                                 # tanpa ingatan: myth_vs_fact setiap hari

    def test_hanya_tanggal_sebelum_run(self):
        self.made("2026-11-12", fmt="list_3")
        self.made("2026-11-13", fmt="question")                       # run hari ini sendiri tidak dihitung
        self.made("2026-11-14", fmt="story")
        self.assertEqual(memory.last_formats(date(2026, 11, 13)), ["list_3"])

    def test_membuat_ulang_video_lama_menghindari_tanggal_sesudahnya(self):
        self.made("2026-11-13", clips=("pexels:13",))                 # video yang dibuat ulang
        self.made("2026-11-14", clips=("pexels:14",))
        state.upsert_history(HistoryEntry(date="2026-11-15", topic="di YouTube 15", clip_ids=["pexels:15"]))
        day = date(2026, 11, 13)
        self.assertEqual(memory.recent_clip_ids(day), {"pexels:14", "pexels:15"})
        topics = memory.recent_topics(day)
        self.assertIn("topik 2026-11-14", topics)
        self.assertIn("di YouTube 15", topics)
        self.assertNotIn("topik 2026-11-13", topics)                   # tanggal itu sendiri tidak
        self.assertEqual(memory.last_formats(day), [])                 # "terakhir" = sebelum tanggal itu

    def test_tanpa_video_tidak_dihitung(self):
        self.made("2026-11-08", qc_ok=False)
        self.made("2026-11-09", valid=False)
        self.made("2026-11-10", render=False)
        self.made("2026-11-11", stale=True)                           # video milik naskah sebelumnya
        os.makedirs(runctx.RunContext(date(2026, 11, 12), echo=None).dir)  # folder kosong
        self.assertEqual(memory.entries(date(2026, 11, 13)), [])

    def test_rentang_hari(self):
        self.made("2026-09-01", clips=("pexels:9",))
        self.made("2026-10-10", clips=("pexels:8",))
        self.made("2026-11-12", clips=("pexels:7",))
        today = date(2026, 11, 13)
        topics = memory.recent_topics(today)
        self.assertNotIn("topik 2026-09-01", topics)                  # > 60 hari
        self.assertIn("topik 2026-10-10", topics)
        self.assertEqual(memory.recent_clip_ids(today), {"pexels:7"})  # klip: 30 hari

    def test_riwayat_youtube_menang_untuk_tanggal_sama(self):
        self.made("2026-11-11", fmt="list_3")
        state.upsert_history(HistoryEntry(date="2026-11-11", topic="topik di YouTube", format="story", hook="h-yt",
                                          video_id="abc", clip_ids=["pexels:5"]))
        state.upsert_history(HistoryEntry(date="2026-11-12", topic="hanya di YouTube", format="question"))
        got = memory.entries(date(2026, 11, 13))
        self.assertEqual([(m.date, m.format) for m in got], [("2026-11-11", "story"), ("2026-11-12", "question")])
        self.assertEqual(memory.recent_topics(date(2026, 11, 13)), ["topik di YouTube", "hanya di YouTube"])

    def test_folder_rusak_dilewati(self):
        self.made("2026-11-11", fmt="list_3")
        ctx = self.made("2026-11-12", fmt="question")
        with open(ctx.path("script.json"), "w", encoding="utf-8") as f:
            f.write("{rusak")
        self.assertEqual(memory.last_formats(date(2026, 11, 13)), ["list_3"])
        with mock.patch.object(memory, "produced", side_effect=[OSError("x"), None]), \
                mock.patch("traceback.print_exc"):
            self.assertEqual(memory.entries(date(2026, 11, 13)), [])


class PipelineMemakaiIngatan(MemorySandbox):
    def test_format_run_berikutnya(self):
        self.made("2026-11-11", fmt="myth_vs_fact")
        self.made("2026-11-12", fmt="list_3")
        config.save_config({"channel": {"niche": "sains"}})
        cfg = pipeline.prepare_config()
        run = pipeline.Run(runctx.RunContext(date(2026, 11, 13), echo=None), cfg, {}, dry_run=True)
        fmt = pipeline._format(run)
        self.assertNotIn(fmt, ("myth_vs_fact", "list_3"))


if __name__ == "__main__":
    unittest.main()
