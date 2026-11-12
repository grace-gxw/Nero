"""
Nero · Channel Otomatis - orkestrator run harian (SPEC.md bagian 5).

    run_daily(day, dry_run=True, only="plan")

Setiap langkah membaca masukannya dari folder run (data/channel/runs/YYYY-MM-DD/) dan menulis hasilnya ke sana.

Checkpoint (lihat juga runctx.py):
- Hasil lama dipakai lagi hanya bila valid DAN sidik (hash) masukannya, termasuk pengaturan yang relevan, sama
  dengan masukan sekarang. Skrip yang diubah, pengaturan yang diganti, atau langkah hulu yang gagal di tengah jalan
  tidak bisa meloloskan hasil lama.
- Langkah yang mulai bekerja menghapus hasilnya sendiri lebih dulu, dan menarik salinan video di
  output/channel/<tanggal>/ bila video bergantung padanya.
- --force STEP mengulang langkah itu. Di langkah rencana (riset, skrip, QC, SEO), langkah yang memakai hasilnya ikut
  diulang (runctx.DEPENDS). Langkah media (suara, visual, render) diulang bila sidik masukannya berubah, jadi
  `--force qc` yang menghasilkan skrip yang sama tidak membuat ulang suara dan gambar AI.

Fase I-2: research, script, qc, seo. Fase I-3: tts, visuals, render. Fase I-5: publish_youtube (hanya run
sungguhan dengan youtube.api_upload = true; unggah uji lewat upload_test). Fase I-6: manual_pack, notify.
Fase I-7: analyst (paling awal, tidak pernah menggagalkan run; laporan mingguan) dan state_update (riwayat).
"""
from __future__ import annotations

import os
import shutil
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import common

from . import analyst, config, genres, manual_pack, media, research, state, telegram, timeutil, writer, youtube
from . import cancel as cancelmod
from .config import ChannelConfig, ConfigError
from .runctx import DEPENDS, FINGERPRINTS, STEPS, RunContext, RunLocked, downstream, lock_active, sha
from .schemas import (
    QC,
    Captions,
    HistoryEntry,
    Issue,
    Metadata,
    RenderInfo,
    Research,
    Script,
    TelegramSent,
    Visuals,
    Words,
    YouTubeResult,
)

PLAN_STEPS = ("research", "script", "qc", "seo")
MEDIA_STEPS = ("tts", "visuals", "render")
PUBLISH_STEPS = ("publish_youtube",)
PACK_STEPS = ("manual_pack",)
NOTIFY_STEPS = ("notify",)
STATE_STEPS = ("analyst", "state_update")
IMPLEMENTED = {*PLAN_STEPS, *MEDIA_STEPS, *PUBLISH_STEPS, *PACK_STEPS, *NOTIFY_STEPS, *STATE_STEPS}
# notify hanya ikut run lengkap (tanpa --only): uji coba dari halaman tidak mengirim ke Telegram
ALIASES = {"plan": PLAN_STEPS + PACK_STEPS, "media": MEDIA_STEPS + PACK_STEPS,
           "video": PLAN_STEPS + MEDIA_STEPS + PACK_STEPS}
PACK_TXT = ("tiktok_caption.txt", "instagram_caption.txt")
PUBLISHED = ("final.mp4", "final.jpg", "captions.srt")    # disalin ke output/channel/<tanggal>/

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_LOCKED = 0, 1, 2, 3


class StepError(RuntimeError):
    def __init__(self, step: str, msg: str):
        super().__init__(msg)
        self.step = step


@dataclass
class Run:
    ctx: RunContext
    cfg: ChannelConfig
    settings: dict
    dry_run: bool
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    cancel: threading.Event | None = None
    on_progress: Callable[[str, float], None] | None = None
    step: str = ""
    genre: str = ""                  # genre khusus run ini (--genre), dicatat di inputs.json
    alerts: bool = False             # kegagalan dikirim ke Telegram (hanya run harian sungguhan)

    def log(self, msg: str) -> None:
        self.ctx.log(msg)

    def progress(self, frac: float) -> None:
        """Progres langkah yang sedang berjalan (0..1)."""
        if self.on_progress:
            self.on_progress(self.step, max(0.0, min(1.0, frac)))


# --------------------------------------------------------------------------
# Sidik masukan tiap langkah (isi + pengaturan yang memengaruhi hasilnya)
# --------------------------------------------------------------------------
def fp_research(cfg: ChannelConfig) -> str:
    return sha(cfg.channel, cfg.research)


def fp_script(res: Research, cfg: ChannelConfig) -> str:
    v = cfg.visuals
    return sha(res, config.words_range(cfg), v.allowed_types, v.max_ai_image_ratio, v.min_types_per_video,
               cfg.channel.tone, cfg.channel.audience)


def fp_qc(script: Script, res: Research, cfg: ChannelConfig) -> str:
    return sha(script, res, cfg.channel.banned_topics, cfg.channel.audience, config.words_limits(cfg),
               cfg.visuals.allowed_types, cfg.visuals.max_ai_image_ratio, cfg.visuals.min_types_per_video)


def fp_seo(script: Script, res: Research, cfg: ChannelConfig) -> str:
    return sha(script, res, cfg.youtube.category_id, cfg.youtube.default_tags, cfg.channel.language)


def fp_tts(script: Script, cfg: ChannelConfig) -> str:
    return sha(script.narration(), cfg.tts, list(cfg.video.target_seconds))


def _visual_settings(cfg: ChannelConfig) -> dict:
    # angka kuota tidak mengubah klip yang sudah jadi
    return cfg.visuals.model_dump(mode="json", exclude={"ai_image": {"neurons_per_tile", "neurons_per_step",
                                                                     "neurons_per_image", "daily_neurons",
                                                                     "stop_ratio"}})


def fp_visuals(script: Script, words: Words, cfg: ChannelConfig) -> str:
    return sha([s.model_dump() for s in script.segments], [t.model_dump() for t in words.segments],
               words.duration, _visual_settings(cfg))


def _music_sig(cfg: ChannelConfig, day: date) -> list:
    from .render import pick_music
    path = pick_music(cfg.video.music_dir, day, log=lambda m: None)
    if not path:
        return []
    st = os.stat(path)
    return [os.path.basename(path), st.st_size, int(st.st_mtime)]


def fp_render(words: Words, vis: Visuals, cfg: ChannelConfig, day: date) -> str:
    from .render import caption_style
    v = cfg.video
    return sha(words, vis, caption_style(cfg, log=lambda m: None), _music_sig(cfg, day), v.music_volume_db,
               list(v.target_seconds))


# --------------------------------------------------------------------------
# Pembantu
# --------------------------------------------------------------------------
def _need(run: Run, name: str, schema, step: str):
    got = run.ctx.load(name, schema)
    if got is None:
        raise StepError(step, f"{name}.json belum ada atau rusak; jalankan langkah sebelumnya dulu.")
    return got


def _reuse(run: Run, step: str, name: str, schema, fp: str, ok: Callable = lambda m: True):
    """Hasil lama bila valid, sidiknya cocok, dan lolos pemeriksaan `ok`."""
    got = run.ctx.load(name, schema)
    if got is None or run.ctx.fingerprint(step) != fp or not ok(got):
        return None
    run.log(f"lewati {name}: hasil sebelumnya masih valid")
    return got


def _output_dir(run: Run) -> str:
    return os.path.join(common.output_dir(config.ID), run.ctx.day.isoformat())


def _unpublish(run: Run, names: tuple[str, ...] = PUBLISHED) -> None:
    """Tarik salinan di folder hasil (video/caption lama tidak boleh tertinggal untuk diunggah)."""
    for name in names:
        path = os.path.join(_output_dir(run), name)
        try:
            state.remove_retry(path, 20.0 if name.endswith(".mp4") else 2.0)
        except OSError as e:
            raise StepError(run.step, f"{path} sedang dibuka program lain ({e.strerror}). Tutup berkas itu lalu "
                                      "jalankan ulang.") from None


def _begin(run: Run, step: str) -> None:
    """Langkah akan bekerja: tarik salinan video bila video bergantung pada langkah ini, lalu hapus hasil lama
    langkah ini (langkah yang gagal di tengah jalan tidak meninggalkan hasil lama yang tampak valid)."""
    if "render" in (step, *downstream(step)):
        _unpublish(run)
    if step in (*PLAN_STEPS, "tts", "visuals", *PACK_STEPS):
        _unpublish(run, PACK_TXT)       # caption bergantung pada naskah, judul, dan visual; dibuat lagi oleh manual_pack
    run.ctx.invalidate(step)


def _mark_src(run: Run, step: str, script: Script) -> None:
    """Hasil langkah dipakai ulang karena sidik masukannya cocok: hasil itu juga milik naskah sekarang (mis. QC
    menulis ulang naskah tanpa mengubah narasi). Tanpa ini, halaman & syarat unggah menganggapnya usang."""
    if run.ctx.fingerprint(f"{step}_src") != sha(script):
        run.ctx.set_fingerprint(f"{step}_src", sha(script))


def _files_exist(run: Run, *names: str) -> bool:
    return all(os.path.isfile(run.ctx.path(n)) and os.path.getsize(run.ctx.path(n)) > 0 for n in names)


def _passed_qc(run: Run, step: str) -> Script:
    script = _need(run, "script", Script, step)
    res = _need(run, "research", Research, step)
    qc = _need(run, "qc", QC, step)
    if run.ctx.fingerprint("qc") != fp_qc(script, res, run.cfg):
        raise StepError(step, "Skrip atau pengaturan berubah setelah diperiksa QC; jalankan ulang langkah qc "
                              "(--force qc).")
    if not qc.passed:
        raise StepError(step, "QC belum lulus; video tidak dibuat.")
    return script


# --------------------------------------------------------------------------
# Langkah rencana
# --------------------------------------------------------------------------
def step_research(run: Run, force: bool) -> bool:
    fp = fp_research(run.cfg)
    if not force and _reuse(run, "research", "research", Research, fp):
        return False
    old = run.ctx.load("research", Research)        # riset diulang = minta topik baru, bukan topik yang sama
    _begin(run, "research")
    res = research.run(run.cfg, run.ctx.day, settings=run.settings, learnings=analyst.prompt_learnings(run.cfg),
                       avoid_topics=[old.chosen.topic] if old else [], log=run.log)
    run.ctx.save("research", res)
    run.ctx.set_fingerprint("research", fp)
    return True


def _format(run: Run) -> str:
    return writer.pick_format(run.cfg.video.formats_rotation, state.last_formats(2))


def step_script(run: Run, force: bool) -> bool:
    res = _need(run, "research", Research, "script")
    fp = fp_script(res, run.cfg)
    if not force and _reuse(run, "script", "script", Script, fp):
        return False
    _begin(run, "script")
    fmt = _format(run)
    run.log(f"skrip: format {fmt}")
    script = writer.write_script(run.cfg, res, fmt, settings=run.settings, learnings=analyst.prompt_learnings(run.cfg),
                                 recent_hooks=state.last_hooks(5), log=run.log)
    run.ctx.save("script", script)
    run.ctx.set_fingerprint("script", fp)
    return True


def _save_review(run: Run, res: Research, script: Script, qc: QC) -> None:
    run.ctx.save("script", script)
    run.ctx.save("qc", qc)
    run.ctx.set_fingerprint("qc", fp_qc(script, res, run.cfg))


def step_qc(run: Run, force: bool) -> bool:
    script = _need(run, "script", Script, "qc")
    res = _need(run, "research", Research, "qc")
    if not force:
        cached = _reuse(run, "qc", "qc", QC, fp_qc(script, res, run.cfg))
        if cached:
            if not cached.passed:
                raise StepError("qc", "QC sebelumnya gagal. Jalankan ulang dengan --force script untuk menulis ulang.")
            return False
    _begin(run, "qc")
    script, qc = writer.write_and_review(
        run.cfg, res, script.format, script=script, settings=run.settings, learnings=analyst.prompt_learnings(run.cfg),
        recent_topics=state.recent_topics(research.HISTORY_DAYS, run.ctx.day), recent_hooks=state.last_hooks(5),
        on_script=lambda s: run.ctx.save("script", s), log=run.log)
    _save_review(run, res, script, qc)
    if not qc.passed:
        reasons = "; ".join(f"{i.type}: {i.detail}" for i in qc.issues)[:600]
        raise StepError("qc", f"QC gagal setelah {writer.MAX_REWRITES} kali tulis ulang (skor {qc.score:g}). "
                              f"Video tidak dibuat. Alasan: {reasons}")
    return True


def step_seo(run: Run, force: bool) -> bool:
    script = _passed_qc(run, "seo")
    res = _need(run, "research", Research, "seo")
    fp = fp_seo(script, res, run.cfg)
    if not force and _reuse(run, "seo", "metadata", Metadata, fp):
        _mark_src(run, "seo", script)
        return False
    _begin(run, "seo")
    run.ctx.save("metadata", writer.seo(run.cfg, script, res, settings=run.settings, log=run.log))
    run.ctx.set_fingerprint("seo", fp)
    run.ctx.set_fingerprint("seo_src", sha(script))     # halaman: metadata ini milik skrip yang mana
    _refresh_description(run)
    return True


def _refresh_description(run: Run) -> None:
    """Deskripsi disusun ulang dari visual yang benar-benar dipakai (kredit stok, pengungkapan AI), bila visual
    yang cocok dengan skrip sekarang sudah ada. Tanpa AI, jadi aman dipanggil berulang."""
    meta = run.ctx.load("metadata", Metadata)
    script = run.ctx.load("script", Script)
    words = run.ctx.load("words", Words)
    vis = run.ctx.load("visuals", Visuals)
    if not (meta and script and words and vis) or run.ctx.fingerprint("visuals") != fp_visuals(script, words,
                                                                                               run.cfg):
        return
    summary = meta.summary or meta.description.split("\n\nSumber:", 1)[0]
    desc = writer.build_description(summary, script, meta.hashtags, uses_stock=vis.uses_stock,
                                    uses_ai=vis.uses_ai_images, credits=writer.stock_credits(vis))
    if desc != meta.description:
        meta.description = desc
        run.ctx.save("metadata", meta)


# --------------------------------------------------------------------------
# Langkah media
# --------------------------------------------------------------------------
def _rewrite_length(run: Run, script: Script, *, seconds: float, goal: float, longer: bool) -> Script:
    """SPEC 6.7: narasi terlalu panjang (walau rate sudah maksimal) atau terlalu pendek untuk lolos validasi
    render -> agen skrip menulis ulang ke jumlah kata yang pas, lalu QC dan SEO diulang."""
    lo_w, hi_w = config.words_limits(run.cfg)
    target = min(hi_w, max(lo_w, round(script.word_count * goal / max(seconds, 0.1))))
    if (target <= script.word_count) if longer else (target >= script.word_count):
        raise StepError("tts", f"Narasi {seconds:.1f} dtk tidak bisa disesuaikan ke ±{goal:.0f} dtk dalam rentang "
                               f"{lo_w}–{hi_w} kata. Periksa pengaturan suara, rate, atau target durasi.")
    if longer:
        issue = Issue(type="length", detail=f"Narasi hanya {seconds:.0f} detik, kurang dari batas minimal video.",
                      fix=f"Panjangkan naskah menjadi sekitar {target} kata dengan fakta lain dari sumber.")
    else:
        issue = Issue(type="length", detail=f"Narasi {seconds:.0f} detik, melebihi batas {goal:.0f} detik.",
                      fix=f"Pendekkan naskah menjadi sekitar {target} kata tanpa mengubah fakta.")
    run.log(f"tts: naskah diminta {'dipanjangkan' if longer else 'dipendekkan'} ke ±{target} kata")
    res = _need(run, "research", Research, "tts")
    media.check_cancel(run.cancel)
    run.ctx.invalidate("qc")                 # skrip akan berubah: QC lama tidak berlaku lagi
    _unpublish(run, PACK_TXT)                # caption naskah lama tidak boleh tertinggal di folder hasil
    new = writer.write_script(run.cfg, res, script.format, settings=run.settings, learnings=analyst.prompt_learnings(run.cfg),
                              recent_hooks=state.last_hooks(5), issues=[issue], previous=script, log=run.log)
    run.ctx.save("script", new)
    media.check_cancel(run.cancel)
    new, qc = writer.write_and_review(
        run.cfg, res, script.format, script=new, settings=run.settings, learnings=analyst.prompt_learnings(run.cfg),
        recent_topics=state.recent_topics(research.HISTORY_DAYS, run.ctx.day), recent_hooks=state.last_hooks(5),
        on_script=lambda s: run.ctx.save("script", s), log=run.log)
    _save_review(run, res, new, qc)
    if not qc.passed:
        raise StepError("tts", f"Naskah yang ditulis ulang gagal QC (skor {qc.score:g}); video tidak dibuat.")
    media.check_cancel(run.cancel)
    step_seo(run, True)
    return new


def _tail() -> float:
    from .voice import TAIL_SECONDS
    return TAIL_SECONDS


def _video_seconds(words: Words) -> float:
    return media.frame_at(words.duration + _tail()) / media.FPS


def step_tts(run: Run, force: bool) -> bool:
    script = _passed_qc(run, "tts")
    if not force and _files_exist(run, "voice.wav") and _reuse(run, "tts", "words", Words,
                                                                fp_tts(script, run.cfg)):
        _mark_src(run, "tts", script)
        return False
    _begin(run, "tts")
    from . import render, voice
    lo = run.cfg.video.target_seconds[0]

    def speak(s: Script) -> Words:
        return voice.synthesize(s, run.cfg, run.ctx.dir, log=run.log, cancel=run.cancel, progress=run.progress)

    try:
        try:
            words = speak(script)
        except voice.TooLong as e:
            run.log(f"tts: {e}")
            script = _rewrite_length(run, script, seconds=e.seconds, goal=e.limit * 0.95, longer=False)
            try:
                words = speak(script)
            except voice.TooLong as e2:
                raise StepError("tts", f"{e2} Naskah sudah dipendekkan sekali; perlu dicek manual.") from None
        if _video_seconds(words) < lo - render.DURATION_SLACK:
            # pasti gagal validasi render: perbaiki sekarang, sebelum kuota visual terpakai
            secs = _video_seconds(words)
            run.log(f"tts: video hanya {secs:.1f} dtk, kurang dari batas {lo - render.DURATION_SLACK:g} dtk")
            script = _rewrite_length(run, script, seconds=secs, goal=lo + 1, longer=True)
            words = speak(script)
            if _video_seconds(words) < lo - render.DURATION_SLACK:
                raise StepError("tts", f"Narasi masih {_video_seconds(words):.1f} dtk setelah dipanjangkan; "
                                       "periksa naskah (--force script) atau target durasi.")
    except BaseException as e:
        run.ctx.invalidate("tts")            # suara yang tidak cocok dengan skrip terakhir tidak disimpan
        if isinstance(e, voice.TTSError):
            raise StepError("tts", str(e)) from None
        raise
    run.ctx.save("words", words)
    run.ctx.set_fingerprint("tts", fp_tts(script, run.cfg))
    run.ctx.set_fingerprint("tts_src", sha(script))
    return True


def _visuals_ok(run: Run, vis: Visuals, words: Words) -> bool:
    """Klip cocok dengan suara: jumlah frame tiap segmen sesuai words.json, semua berkas ada, voice.wav utuh."""
    from .visuals import spans_frames
    return (vis.total_frames == media.frame_at(words.duration + _tail())
            and [v.frames for v in vis.segments] == spans_frames(vis, words)
            and _files_exist(run, "voice.wav", *(v.file for v in vis.segments))
            and abs(media.wav_duration(run.ctx.path("voice.wav")) - words.duration) <= 0.05)


def step_visuals(run: Run, force: bool) -> bool:
    script = _passed_qc(run, "visuals")
    words = _need(run, "words", Words, "visuals")
    fp = fp_visuals(script, words, run.cfg)
    if not force and _reuse(run, "visuals", "visuals", Visuals, fp, lambda v: _visuals_ok(run, v, words)):
        _mark_src(run, "visuals", script)
        return False
    _begin(run, "visuals")
    from . import visuals
    try:
        vis = visuals.build(script, words, run.cfg, run.ctx.dir, settings=run.settings,
                            exclude=state.recent_clip_ids(30, run.ctx.day), log=run.log, cancel=run.cancel,
                            progress=run.progress)
    except visuals.VisualError as e:
        raise StepError("visuals", str(e)) from None
    run.ctx.save("visuals", vis)
    run.ctx.set_fingerprint("visuals", fp)
    run.ctx.set_fingerprint("visuals_src", sha(script))
    _refresh_description(run)
    run.log("visual: selesai (" + ", ".join(f"{v.segment_id}:{v.type}" for v in vis.segments) + ")")
    return True


def _publish_local(run: Run, names: tuple[str, ...] = PUBLISHED) -> str:
    """Salin hasil ke output/channel/<tanggal>/ supaya tombol "Folder hasil" di Nero bekerja. Atomik dan
    idempoten: berkas yang sudah sama dilewati. Gagal menyalin tidak menggagalkan run (dicoba lagi run berikutnya)."""
    out = _output_dir(run)
    os.makedirs(out, exist_ok=True)
    for name in names:
        src, dst = run.ctx.path(name), os.path.join(out, name)
        if not os.path.isfile(src):
            continue
        st = os.stat(src)
        if os.path.isfile(dst) and os.path.getsize(dst) == st.st_size and os.path.getmtime(dst) >= st.st_mtime:
            continue
        tmp = dst + ".tmp"
        try:
            shutil.copy2(src, tmp)
            state.replace_retry(tmp, dst)
        except OSError as e:
            run.log(f"{run.step}: PERINGATAN {name} belum bisa disalin ke folder hasil ({e.strerror}); dicoba lagi "
                    "nanti")
            if os.path.exists(tmp):
                os.remove(tmp)
    return out


def step_render(run: Run, force: bool) -> bool:
    _passed_qc(run, "render")
    words = _need(run, "words", Words, "render")
    vis = _need(run, "visuals", Visuals, "render")
    if not _visuals_ok(run, vis, words):
        raise StepError("render", "Visual tidak cocok dengan suara (durasi/klip); jalankan ulang langkah visuals "
                                  "(--force visuals).")
    fp = fp_render(words, vis, run.cfg, run.ctx.day)
    if not force and _reuse(run, "render", "render", RenderInfo, fp,
                            lambda r: r.valid and _files_exist(run, "final.mp4")):
        if run.ctx.fingerprint("render_media") != sha(words, vis):
            run.ctx.set_fingerprint("render_media", sha(words, vis))
        _mark_src(run, "render", _need(run, "script", Script, "render"))
        _refresh_description(run)
        _publish_local(run)
        return False
    _begin(run, "render")
    from . import render
    try:
        info = render.render(run.ctx.dir, words, vis, run.cfg, run.ctx.day, log=run.log, cancel=run.cancel,
                             progress=run.progress)
    except render.RenderError as e:
        raise StepError("render", str(e)) from None
    run.ctx.save("render", info)
    if not info.valid:
        raise StepError("render", "Validasi video gagal: " + "; ".join(info.problems) + ". Video tidak di-upload.")
    run.ctx.set_fingerprint("render", fp)
    run.ctx.set_fingerprint("render_src", sha(_need(run, "script", Script, "render")))   # video milik skrip ini
    run.ctx.set_fingerprint("render_media", sha(words, vis))     # suara & visual yang benar-benar ada di video
    _refresh_description(run)
    run.log(f"render: hasil disalin ke {_publish_local(run)}")
    if info.needs_review:
        run.log("render: PERLU DICEK MANUSIA sebelum diunggah: " + "; ".join(info.warnings))
    return True


# --------------------------------------------------------------------------
# Langkah publikasi
# --------------------------------------------------------------------------
AGAIN = "Buat ulang videonya (tombol \"Buat ulang video\" di halaman Channel; hasil yang masih cocok dipakai ulang)."


class UploadBlocked(StepError):
    """Video belum boleh diunggah. rebuild = bisa diperbaiki dengan membuat ulang video run itu."""

    def __init__(self, msg: str, rebuild: bool):
        super().__init__("publish_youtube", msg)
        self.rebuild = rebuild


def _manual_upload(run: Run) -> str:
    """ID video yang diunggah sendiri oleh pengguna untuk tanggal run ini (dikenali analis dari judulnya, tercatat di
    riwayat) bila run ini belum punya youtube.json; selain itu "". Penjaga "tidak pernah upload dua kali" untuk
    video yang tidak diunggah lewat Nero."""
    yt = run.ctx.load("youtube", YouTubeResult)
    if (yt is not None and yt.video_id) or os.path.exists(run.ctx.path(youtube.SESSION_FILE)):
        return ""                # sudah tercatat, atau upload Nero yang terputus: pemulihan upload yang menanganinya
    day = run.ctx.day.isoformat()
    return next((e.video_id for e in state.load_history() if e.date == day and e.video_id), "")


def _upload_ready(run: Run, step: str) -> tuple[Script, Metadata, RenderInfo, Visuals]:
    """Syarat upload (SPEC bagian 8: tidak pernah upload bila QC atau validasi render gagal):
    - QC lulus untuk naskah dan pengaturan sekarang;
    - video lolos validasi, dibuat dari naskah itu, dan merupakan render terbaru dari suara, visual, dan pengaturan
      video sekarang (deskripsi, kredit stok, dan riwayat disusun dari visual yang sama dengan isi video);
    - judul & deskripsi dibuat untuk naskah itu.
    Pesannya ditampilkan apa adanya di halaman (upload_block) dan di log."""
    def stop(msg: str, rebuild: bool = True):
        raise UploadBlocked(msg + (" " + AGAIN if rebuild else ""), rebuild)

    manual = _manual_upload(run)
    if manual:
        stop(f"Video run ini sudah ada di YouTube (diunggah sendiri: {manual}); tidak diunggah lagi.", rebuild=False)
    script = run.ctx.load("script", Script)
    res = run.ctx.load("research", Research)
    qc = run.ctx.load("qc", QC)
    if script is None or res is None or qc is None:
        stop("Rencana video belum lengkap (riset, naskah, atau QC belum ada).")
    if run.ctx.fingerprint("qc") != fp_qc(script, res, run.cfg):
        stop("Naskah atau pengaturan channel berubah setelah diperiksa QC.")
    if not qc.passed:
        stop("QC belum lulus; video tidak diunggah.", rebuild=False)
    info = run.ctx.load("render", RenderInfo)
    if info is None or not info.valid or not _files_exist(run, "final.mp4"):
        stop("Video belum ada atau tidak lolos validasi render; video tidak diunggah.",
             rebuild=info is None or info.valid)
    src = sha(script)
    fps = run.ctx.fingerprints()
    if not all(fps.get(f"{s}_src") == src for s in ("tts", "visuals", "render")):
        stop("Suara, visual, atau video dibuat dari naskah lain.")
    words = run.ctx.load("words", Words)
    vis = run.ctx.load("visuals", Visuals)
    # isi video = suara & visual sekarang. Run lama tanpa catatan itu: sidik render lengkap (termasuk pengaturan).
    media_ok = words is not None and vis is not None and (
        fps.get("render_media") == sha(words, vis) if fps.get("render_media")
        else fps.get("render") == fp_render(words, vis, run.cfg, run.ctx.day))
    if not media_ok:
        stop("Video belum dibuat ulang setelah suara atau visual berubah.")
    meta = run.ctx.load("metadata", Metadata)
    if meta is None or fps.get("seo_src") != src:
        stop("Judul & deskripsi belum dibuat untuk naskah ini.")
    if fps.get("seo") != fp_seo(script, res, run.cfg):
        stop("Judul, tag, atau kategori belum dibuat ulang untuk pengaturan sekarang.")
    return script, meta, info, vis


def _record_history(run: Run, script: Script, meta: Metadata, vis: Visuals, info: RenderInfo,
                    res: YouTubeResult) -> None:
    """Video yang sudah ada di YouTube masuk riwayat (dedupe topik 60 hari, rotasi format, klip stok)."""
    found = run.ctx.load("research", Research)
    qc = run.ctx.load("qc", QC)
    state.upsert_history(HistoryEntry(
        date=run.ctx.day.isoformat(), topic=found.chosen.topic if found else script.topic, title=meta.title,
        format=script.format, hook=script.hook, video_id=res.video_id, clip_ids=vis.clip_ids,
        qc_score=qc.score if qc else 0, duration=round(info.duration, 2)))


def _report_upload(run: Run, res: YouTubeResult) -> None:
    if res.status == "masalah":
        run.log(f"youtube: PERINGATAN video terunggah tetapi tidak sesuai harapan: {res.note} ({res.studio_url})")
    elif res.verified:
        run.log(f"youtube: terverifikasi ({res.privacy}{', tayang ' + res.publish_at_wib if res.publish_at_wib else ''})")
    else:
        run.log(f"youtube: {res.note or 'belum terverifikasi'} Status dicek lagi nanti (tombol Cek status).")
    if res.playlist_error:
        run.log(f"youtube: PERINGATAN playlist: {res.playlist_error}")


def _publish(run: Run, *, test: bool) -> YouTubeResult:
    step = "publish_youtube"
    script, meta, info, vis = _upload_ready(run, step)
    privacy, at, why = youtube.privacy_plan(run.cfg, needs_review=info.needs_review, test=test)
    run.log(f"youtube: {why}")
    # deskripsi disusun dari visual yang sama dengan isi video (kredit stok, pengungkapan AI)
    summary = meta.summary or meta.description.split("\n\nSumber:", 1)[0]
    desc = writer.build_description(summary, script, meta.hashtags, uses_stock=vis.uses_stock,
                                    uses_ai=vis.uses_ai_images, credits=writer.stock_credits(vis))
    if desc != meta.description:
        meta = meta.model_copy(update={"description": desc})
        run.ctx.save("metadata", meta)
    try:
        body = youtube.video_body(meta, run.cfg, privacy=privacy, publish_at=at)
        res = youtube.publish(run.ctx, run.ctx.path("final.mp4"), body,
                              publish_at_wib=timeutil.format_local(at) if at else "",
                              playlist_id=run.cfg.youtube.playlist_id.strip(), test=test, source=sha(script),
                              on_uploaded=lambda r: _record_upload(run, r), tz=run.cfg.schedule.timezone,
                              cancel=run.cancel, progress=lambda f: run.progress(0.9 * f), log=run.log,
                              settings=run.settings)
    except youtube.YouTubeError as e:
        raise StepError(step, str(e)) from None
    _report_upload(run, res)
    return res


def _record_upload(run: Run, res: YouTubeResult) -> None:
    """Catat video di YouTube ke riwayat. Isi lengkap (topik, format, hook, klip) hanya bila video itu dibuat dari
    naskah run sekarang; video lama dari naskah sebelumnya dicatat dengan judulnya saja."""
    script = run.ctx.load("script", Script)
    meta = run.ctx.load("metadata", Metadata)
    info = run.ctx.load("render", RenderInfo)
    if script is not None and meta is not None and info is not None and res.source and res.source == sha(script):
        _record_history(run, script, meta, run.ctx.load("visuals", Visuals) or Visuals(segments=[]), info, res)
        return
    title = res.title or "(judul tidak diketahui)"
    state.upsert_history(HistoryEntry(date=run.ctx.day.isoformat(), topic=title, title=title,
                                      video_id=res.video_id))
    run.log("youtube: video di YouTube berasal dari naskah sebelumnya; riwayat dicatat dengan judulnya saja")


def _ensure_history(run: Run, res: YouTubeResult) -> None:
    """Video sudah di YouTube tetapi belum masuk riwayat (mis. proses mati tepat setelah upload): lengkapi."""
    if any(e.date == run.ctx.day.isoformat() and e.video_id == res.video_id for e in state.load_history()):
        return
    try:
        _record_upload(run, res)
        run.log("youtube: riwayat unggahan dilengkapi")
    except (OSError, ValueError) as e:
        run.log(f"youtube: PERINGATAN riwayat belum bisa dilengkapi ({e})")


def step_publish_youtube(run: Run, force: bool) -> bool:
    """Upload otomatis (run sungguhan, youtube.api_upload = true). --force tidak berlaku: satu run = satu video."""
    old = run.ctx.load("youtube", YouTubeResult)
    if old is not None and old.video_id:
        run.log(f"lewati youtube: video sudah diunggah ({old.url}); tidak pernah diunggah dua kali")
        if not run.dry_run:
            _ensure_history(run, old)
            if old.status == "diproses":
                try:
                    _report_upload(run, youtube.verify(run.ctx, old, settings=run.settings, strict=False))
                except youtube.YouTubeError as e:
                    run.log(f"youtube: status belum bisa dicek ({e})")
        return False
    if run.dry_run:
        run.log("youtube: uji coba, video tidak diunggah")
        return False
    manual = _manual_upload(run)
    if manual:
        run.log(f"lewati youtube: video tanggal ini sudah diunggah sendiri ({manual}); tidak pernah diunggah dua kali")
        return False
    if not run.cfg.youtube.api_upload:
        run.log("youtube: upload lewat API nonaktif (youtube.api_upload = false); unggah manual memakai paket di "
                "halaman Channel")
        return False
    info = run.ctx.load("render", RenderInfo)
    if info is not None and info.valid and info.needs_review:
        # render.py: video yang sah tetapi variasi visualnya kurang wajib dicek manusia dulu
        run.log("youtube: PERINGATAN video perlu dicek manusia, jadi tidak diunggah otomatis. Tonton dulu, lalu "
                "pakai Unggah uji (private) di halaman Channel.")
        return False
    _publish(run, test=False)
    return True


# --------------------------------------------------------------------------
# Paket manual & Telegram (Fase I-6)
# --------------------------------------------------------------------------
def _current(run: Run, name: str, schema, step: str, script: Script):
    """Hasil langkah `step` bila milik naskah sekarang (<step>_src), selain itu None."""
    if run.ctx.fingerprint(f"{step}_src") != sha(script):
        return None
    return run.ctx.load(name, schema)


def _pack_inputs(ctx: RunContext, script: Script) -> tuple[Metadata | None, bool]:
    """(metadata naskah ini, video memakai gambar AI). Gambar AI dari visual sebenarnya bila sudah dibuat untuk
    naskah ini, selain itu dari rencana."""
    src = sha(script)
    meta = ctx.load("metadata", Metadata) if ctx.fingerprint("seo_src") == src else None
    vis = ctx.load("visuals", Visuals) if ctx.fingerprint("visuals_src") == src else None
    return meta, vis.uses_ai_images if vis is not None else writer.planned_visuals(script)[1]


def current_pack(ctx: RunContext, script: Script) -> Captions | None:
    """Caption yang dibuat dari judul/visual sekarang (bukan sekadar milik naskah ini), selain itu None."""
    meta, uses_ai = _pack_inputs(ctx, script)
    if meta is None or ctx.fingerprint("manual_pack_src") != sha(script) or \
            ctx.fingerprint("manual_pack") != sha(meta, uses_ai):
        return None
    return ctx.load("captions", Captions)


def _video_current(run: Run, script: Script) -> RenderInfo | None:
    """render.json bila final.mp4 sah dan merupakan render terbaru dari suara & visual naskah ini (pemeriksaan yang
    sama dengan syarat unggah), selain itu None."""
    info = run.ctx.load("render", RenderInfo)
    if info is None or not info.valid or not _files_exist(run, "final.mp4"):
        return None
    fps = run.ctx.fingerprints()
    src = sha(script)
    if not all(fps.get(f"{s}_src") == src for s in ("tts", "visuals", "render")):
        return None
    words, vis = run.ctx.load("words", Words), run.ctx.load("visuals", Visuals)
    if words is None or vis is None:
        return None
    ok = (fps.get("render_media") == sha(words, vis) if fps.get("render_media")
          else fps.get("render") == fp_render(words, vis, run.cfg, run.ctx.day))
    return info if ok else None


def step_manual_pack(run: Run, force: bool) -> bool:
    """Caption TikTok & Instagram (selalu diunggah manual) dari judul/ringkasan/hashtag naskah sekarang."""
    script = _passed_qc(run, "manual_pack")
    meta, uses_ai = _pack_inputs(run.ctx, script)
    if meta is None:
        raise StepError("manual_pack", "Judul & deskripsi belum dibuat untuk naskah ini; jalankan langkah seo.")
    fp = sha(meta, uses_ai)
    if not force and _reuse(run, "manual_pack", "captions", Captions, fp, lambda c: _files_exist(run, *PACK_TXT)):
        _mark_src(run, "manual_pack", script)
        _publish_local(run, PACK_TXT)
        return False
    _begin(run, "manual_pack")
    caps = manual_pack.build(meta, uses_ai=uses_ai, topic=script.topic)
    run.ctx.save("captions", caps)
    state.write_text_atomic(run.ctx.path("tiktok_caption.txt"), caps.tiktok + "\n")
    state.write_text_atomic(run.ctx.path("instagram_caption.txt"), caps.instagram + "\n")
    run.ctx.set_fingerprint("manual_pack", fp)
    run.ctx.set_fingerprint("manual_pack_src", sha(script))
    _publish_local(run, PACK_TXT)
    run.log("paket manual: caption TikTok & Instagram siap")
    return True


def _notify_items(run: Run) -> list:
    """Isi kiriman Telegram dari hasil run yang milik naskah sekarang (yang usang tidak ikut dikirim)."""
    script = _need(run, "script", Script, "notify")
    src = sha(script)
    found = run.ctx.load("research", Research)
    qc = run.ctx.load("qc", QC)
    if qc is not None and (found is None or run.ctx.fingerprint("qc") != fp_qc(script, found, run.cfg)):
        qc = None                                   # QC milik naskah/pengaturan lain
    info = _video_current(run, script)
    yt = run.ctx.load("youtube", YouTubeResult)
    stale_yt = bool(yt is not None and yt.video_id and yt.source and yt.source != src)
    meta, _ = _pack_inputs(run.ctx, script)
    return telegram.compose_run(
        day=run.ctx.day.isoformat(), script=script, meta=meta, qc=qc, render=info,
        youtube=None if stale_yt else yt, youtube_stale=stale_yt, captions=current_pack(run.ctx, script),
        video=run.ctx.path("final.mp4") if info is not None else "", approval=run.cfg.approval.mode,
        topic=script.topic)


def step_notify(run: Run, force: bool) -> bool:
    """Kirim paket harian ke Telegram (SPEC 6.11). Uji coba hanya mencatat isinya. Bagian yang sudah terkirim untuk
    naskah ini tidak dikirim lagi (kiriman yang terputus dilanjutkan); --force mengirim ulang semuanya."""
    if not run.cfg.notify.telegram:
        run.log("telegram: kiriman dimatikan (notify.telegram = false)")
        return False
    items = _notify_items(run)
    if run.dry_run:
        run.log(f"telegram (uji coba): {len(items)} pesan tidak dikirim (" + ", ".join(i.kind for i in items) + ")")
        run.log("telegram (uji coba) ringkasan: " + telegram.plain(items[0].text).replace("\n", " | "))
        return False
    if not telegram.configured(run.settings):
        run.log("telegram: PERINGATAN token bot / chat ID belum diisi; paket tidak dikirim")
        return False
    src = sha(_need(run, "script", Script, "notify"))
    old = run.ctx.load("telegram", TelegramSent)
    done = set(old.sent) if old is not None and old.source == src and not force else set()
    if {i.kind for i in items} <= done:
        run.log("lewati telegram: paket naskah ini sudah dikirim")
        return False
    record = TelegramSent(source=src, sent=sorted(done))

    def sent(kind: str) -> None:
        record.sent.append(kind)
        run.ctx.save("telegram", record)

    try:
        telegram.send_items(items, settings=run.settings, done=done, on_sent=sent, cancel=run.cancel,
                            progress=run.progress, log=run.log)
    except telegram.TelegramError as e:
        raise StepError("notify", str(e)) from None
    record.sent_at = timeutil.now_local(run.cfg.schedule.timezone).isoformat(timespec="seconds")
    run.ctx.save("telegram", record)
    run.log(f"telegram: paket terkirim ({len(items)} pesan)")
    return True


def _alert(run: Run, step: str, error: str) -> None:
    """SPEC 6.11: kegagalan run harian dikirim ke Telegram (nama langkah + 30 baris log terakhir). Hanya run
    sungguhan; galat saat mengirim peringatan hanya dicatat."""
    if not run.alerts:
        if run.dry_run:
            run.log("telegram (uji coba): peringatan kegagalan tidak dikirim")
        return
    if not run.cfg.notify.telegram:
        return
    if not telegram.configured(run.settings):
        run.log("telegram: token bot / chat ID belum diisi; peringatan kegagalan tidak dikirim")
        return
    from .runs import last_run_lines
    try:
        telegram.send_items(telegram.alert_items(day=run.ctx.day.isoformat(), step=step, error=error,
                                                 log_lines=last_run_lines(run.ctx)),
                            settings=run.settings, log=run.log)
        run.log("telegram: peringatan kegagalan terkirim")
    except Exception as e:  # noqa: BLE001  peringatan yang gagal tidak boleh menutupi kegagalan aslinya
        run.log(f"telegram: peringatan kegagalan tidak terkirim ({e})")


# --------------------------------------------------------------------------
# Analis & riwayat (Fase I-7)
# --------------------------------------------------------------------------
def _record_found(found: analyst.Found) -> None:
    """Video yang diunggah sendiri oleh pengguna (dikenali analis dari judulnya) masuk riwayat, seperti video yang
    diunggah lewat Nero: dedupe topik 60 hari, rotasi format & hook, dan klip stok yang tidak dipakai ulang."""
    day = date.fromisoformat(found.day)
    ctx = RunContext(day, echo=None)
    if any(e.video_id == found.video_id for e in state.load_history()):
        return
    if lock_active(ctx.path(".lock")):
        raise OSError(f"run {found.day} sedang berjalan; dicatat di analisis berikutnya")
    script, meta = ctx.load("script", Script), ctx.load("metadata", Metadata)
    if script is None or meta is None or analyst.norm_title(meta.title) != analyst.norm_title(found.title):
        return
    found_res = ctx.load("research", Research)
    qc, info = ctx.load("qc", QC), ctx.load("render", RenderInfo)
    src = sha(script)
    vis = ctx.load("visuals", Visuals) if ctx.fingerprint("visuals_src") == src else None
    state.upsert_history(HistoryEntry(
        date=found.day, topic=found_res.chosen.topic if found_res else script.topic, title=meta.title,
        format=script.format, hook=script.hook, video_id=found.video_id,
        clip_ids=vis.clip_ids if vis is not None else [], qc_score=qc.score if qc else 0,
        duration=round(info.duration, 2) if info and ctx.fingerprint("render_src") == src else 0))


def _weekly_report(run: Run, perf) -> None:
    """Laporan mingguan ke Telegram (SPEC 6.2) di run sungguhan pertama sejak hari laporan. Gagal = dicoba lagi di
    run berikutnya; tidak pernah menggagalkan run harian."""
    today = timeutil.today_local(run.cfg.schedule.timezone)
    if not analyst.weekly_due(run.cfg, today):
        return
    if perf is None or not perf.day:
        # performa belum terbaca (galat sesaat, YouTube belum/tidak lagi terhubung): minggu ini tidak ditandai
        run.log("laporan mingguan: performa YouTube belum terbaca; dicoba lagi di run berikutnya")
        return
    if not perf.videos:
        run.log("laporan mingguan: belum ada video buatan Nero di channel; dilewati minggu ini")
        if not run.dry_run:
            analyst.mark_weekly(run.cfg, today, skipped="belum ada video")
        return
    items = analyst.weekly_items(perf)
    if run.dry_run:
        # isi laporan (statistik YouTube) tidak ditulis ke run.log: log run tidak ikut terhapus saat izin dicabut
        run.log(f"laporan mingguan (uji coba): tidak dikirim ({len(perf.videos)} video; isinya: python -m "
                "features.channel weekly-report)")
        return
    if not run.cfg.notify.telegram:
        run.log("laporan mingguan: kiriman Telegram dimatikan (notify.telegram = false)")
        return
    if not telegram.configured(run.settings):
        run.log("laporan mingguan: PERINGATAN token bot / chat ID Telegram belum diisi; tidak dikirim")
        return
    try:
        telegram.send_items(items, settings=run.settings, cancel=run.cancel, log=run.log)
    except telegram.TelegramError as e:
        run.log(f"laporan mingguan: PERINGATAN belum terkirim ({e}); dicoba lagi di run berikutnya")
        return
    analyst.mark_weekly(run.cfg, today)
    run.log("laporan mingguan: terkirim ke Telegram")


def step_analyst(run: Run, force: bool) -> bool:
    """SPEC 6.2, jalan paling awal: performa video 28 hari terakhir, learnings.md, dan laporan mingguan. Tidak pernah
    menggagalkan run (galat = peringatan; pelajaran sebelumnya tetap dipakai)."""
    today = timeutil.today_local(run.cfg.schedule.timezone)
    try:
        with analyst.guard():
            perf, did = analyst.run(run.cfg, run.settings, today, force=force, log=run.log, progress=run.progress,
                                    on_found=_record_found)
            _weekly_report(run, perf)
    except analyst.AnalystBusy as e:
        run.log(f"analis: {e} Dilewati.")
        return False
    except (OSError, ValueError) as e:
        run.log(f"analis: PERINGATAN {e}; dilewati")
        return False
    return did


def step_state_update(run: Run, force: bool) -> bool:
    """SPEC langkah 12: riwayat = video yang ada di YouTube. Upload lewat Nero sudah mencatatnya; langkah ini
    melengkapinya bila sempat terlewat. Video yang diunggah sendiri dicatat analis begitu dikenali di channel."""
    if run.dry_run:
        run.log("riwayat: uji coba, riwayat tidak diubah")
        return False
    yt = run.ctx.load("youtube", YouTubeResult)
    if yt is None or not yt.video_id:
        run.log("riwayat: video tanggal ini belum ada di YouTube (yang diunggah sendiri dicatat saat analis "
                "menemukannya)")
        return False
    _ensure_history(run, yt)
    return False


STEP_FUNCS = {"analyst": step_analyst, "research": step_research, "script": step_script, "qc": step_qc,
              "seo": step_seo, "tts": step_tts, "visuals": step_visuals, "render": step_render,
              "publish_youtube": step_publish_youtube, "manual_pack": step_manual_pack, "notify": step_notify,
              "state_update": step_state_update}


# --------------------------------------------------------------------------
# Run lama (sebelum ada sidik masukan)
# --------------------------------------------------------------------------
def _adopt_legacy(run: Run) -> None:
    """Folder run yang dibuat sebelum ada inputs.json: hasil yang ada dianggap cocok dengan masukan sekarang
    (sekali saja), supaya rencana yang sudah diperiksa tidak ditulis ulang diam-diam."""
    ctx = run.ctx
    if os.path.exists(ctx.path(FINGERPRINTS)) or not os.path.exists(ctx.path("research.json")):
        return
    res = ctx.load("research", Research)
    if res is None:
        return
    fps = {"research": fp_research(run.cfg)}
    script = ctx.load("script", Script)
    if script is not None:
        fps["script"] = fp_script(res, run.cfg)
        if ctx.load("qc", QC) is not None:
            fps["qc"] = fp_qc(script, res, run.cfg)
        if ctx.load("metadata", Metadata) is not None:
            fps["seo"] = fp_seo(script, res, run.cfg)
            fps["seo_src"] = sha(script)
        words = ctx.load("words", Words)
        if words is not None and os.path.isfile(ctx.path("voice.wav")):
            fps["tts"] = fp_tts(script, run.cfg)
            fps["tts_src"] = sha(script)
            vis = ctx.load("visuals", Visuals)
            if vis is not None:
                fps["visuals"] = fp_visuals(script, words, run.cfg)
                fps["visuals_src"] = sha(script)
                if ctx.load("render", RenderInfo) is not None:
                    fps["render"] = fp_render(words, vis, run.cfg, ctx.day)
                    fps["render_src"] = sha(script)
                    fps["render_media"] = sha(words, vis)
    for step, value in fps.items():
        ctx.set_fingerprint(step, value)
    run.log("run lama: hasil yang sudah ada dipakai apa adanya (" + ", ".join(fps) + ")")


# --------------------------------------------------------------------------
# Run harian
# --------------------------------------------------------------------------
def resolve_steps(only: str | None) -> list[str]:
    if not only:
        return list(STEPS)
    if only in ALIASES:
        return list(ALIASES[only])
    if only not in STEPS:
        raise ConfigError(f"Langkah '{only}' tidak dikenal. Pilihan: {', '.join(ALIASES)}, {', '.join(STEPS)}")
    return [only]


def prepare_config(genre: str | None = None, settings: dict | None = None) -> ChannelConfig:
    cfg = config.load_config(settings)
    if genre:
        try:
            cfg = genres.apply(cfg, genre)
        except KeyError as e:
            raise ConfigError(str(e.args[0])) from e
    if not cfg.channel.niche.strip():
        raise ConfigError("Genre/niche channel belum dipilih. Pilih genre di halaman Nero, atau uji coba dengan "
                          "--genre (lihat: python -m features.channel genres).")
    if cfg.tts.engine != "edge":
        raise ConfigError("Mesin suara 'piper' belum tersedia (suara Indonesia Piper belum jelas lisensinya). "
                          "Pakai 'edge'.")
    return cfg


def _run_steps(run: Run, steps: list[str], force: list[str], cancel: threading.Event | None) -> None:
    ran: set[str] = set()
    for step in STEPS:
        if step not in steps:
            continue
        if step not in IMPLEMENTED:
            run.log(f"langkah {step} belum tersedia (menyusul di fase berikutnya), dilewati")
            continue
        run.step = step
        media.check_cancel(cancel)
        run.progress(0.0)
        # langkah rencana ikut diulang bila langkah yang hasilnya ia pakai baru dijalankan; langkah media
        # memakai sidik masukannya (tidak ada suara/gambar AI yang dibuat ulang bila masukannya sama)
        forced = step in force or (step in PLAN_STEPS and any(d in ran for d in DEPENDS.get(step, ())))
        did = STEP_FUNCS[step](run, forced)
        # genre khusus run ini ikut menentukan sidik hasil; dicatat setelah langkah rencana/media selesai dengannya
        if step in (*PLAN_STEPS, *MEDIA_STEPS) and run.ctx.fingerprint("genre") != run.genre:
            run.ctx.set_fingerprint("genre", run.genre)
        run.progress(1.0)
        (run.ran if did else run.skipped).append(step)
        if did:
            ran.add(step)
    media.check_cancel(cancel)                   # dibatalkan saat langkah terakhir: bukan "selesai"


def run_daily(day: date | None = None, *, dry_run: bool = False, force: list[str] = (), only: str | None = None,
              genre: str | None = None, echo=print, cancel: threading.Event | None = None,
              on_progress: Callable[[str, float], None] | None = None) -> int:
    """Jalankan pipeline untuk satu tanggal. Hasil: kode keluar (0 = berhasil)."""
    settings = common.load_settings()
    try:
        cfg = prepare_config(genre, settings)
        steps = resolve_steps(only)
        bad = [f for f in force if f not in STEPS]
        if bad:
            raise ConfigError(f"--force {', '.join(bad)}: langkah tidak dikenal")
    except ConfigError as e:
        echo(f"[channel] {e}")
        return EXIT_CONFIG
    if not cfg.enabled and not dry_run:
        echo("[channel] Channel Otomatis masih nonaktif. Uji coba dengan --dry-run, atau nyalakan di pengaturan.")
        return EXIT_CONFIG
    today = timeutil.today_local(cfg.schedule.timezone)
    day = day or today
    ctx = RunContext(day, echo=echo)
    run = Run(ctx, cfg, settings, dry_run, cancel=cancel, on_progress=on_progress, genre=genre or "",
              alerts=not dry_run)
    try:
        ctx.acquire()
    except RunLocked as e:
        echo(f"[channel] {e}")
        return EXIT_LOCKED
    run.step = "-"

    def body():
        run.log(f"mulai run {day.isoformat()} ({'uji coba' if dry_run else 'sungguhan'}), niche: {cfg.channel.niche}")
        freed = state.prune_runs(min(day, today), keep=(day.isoformat(), today.isoformat()))
        if freed:
            run.log(f"bersih-bersih: {freed} berkas sementara dari run lama dihapus")
        _adopt_legacy(run)
        _run_steps(run, steps, force, cancel)
        run.log(f"selesai. dijalankan: {', '.join(run.ran) or '-'}; dipakai ulang: {', '.join(run.skipped) or '-'}")

    return _guarded(run, body)


def _guarded(run: Run, body: Callable[[], None]) -> int:
    """Jalankan isi run yang sudah memegang kunci: pembatalan & kegagalan dicatat dengan cara yang sama, kunci
    selalu dilepas."""
    try:
        with cancelmod.cancel_scope(run.cancel):     # panggilan AI, riset & upload ikut berhenti saat dibatalkan
            body()
        return EXIT_OK
    except media.Cancelled:
        run.log(f"DIBATALKAN di langkah {run.step}")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001  semua kegagalan langkah dilaporkan dengan cara yang sama
        run.log(f"GAGAL di langkah {getattr(e, 'step', None) or run.step}: {e}")
        with open(run.ctx.path("run.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        _alert(run, getattr(e, "step", None) or run.step, str(e))
        return EXIT_FAILED
    finally:
        run.ctx.release()


def upload_test(day: date, *, echo=print, cancel: threading.Event | None = None,
                on_progress: Callable[[str, float], None] | None = None) -> int:
    """Unggah uji atas perintah pengguna (tombol di halaman atau CLI): video run `day` diunggah sebagai private
    tanpa jadwal. Boleh walau channel masih nonaktif atau youtube.api_upload = false, karena tidak ada yang
    tayang tanpa pengguna. Syarat QC & validasi render tetap berlaku, dan tetap hanya sekali per run."""
    settings = common.load_settings()
    ctx = RunContext(day, echo=echo)
    try:
        cfg = run_config(ctx, settings)
    except ConfigError as e:
        echo(f"[channel] {e}")
        return EXIT_CONFIG
    if not os.path.isfile(ctx.path("render.json")):
        echo(f"[channel] Run {day.isoformat()} belum punya video. Buat videonya dulu.")
        return EXIT_CONFIG
    run = Run(ctx, cfg, settings, dry_run=False, cancel=cancel, on_progress=on_progress)
    try:
        ctx.acquire()
    except RunLocked as e:
        echo(f"[channel] {e}")
        return EXIT_LOCKED
    run.step = "publish_youtube"

    def body():
        run.progress(0.0)
        old = ctx.load("youtube", YouTubeResult)
        if old is not None and old.video_id:
            run.log(f"lewati youtube: video tanggal ini sudah diunggah ({old.url}); tidak diunggah ulang")
            _ensure_history(run, old)
            did = "-; dipakai ulang: publish_youtube"
        else:
            run.log(f"mulai unggah uji {day.isoformat()} (private)")
            _refresh_description(run)
            _publish(run, test=True)
            did = "publish_youtube; dipakai ulang: -"
        run.progress(1.0)
        if markers:
            run.log(f"selesai. dijalankan: {did} (unggah uji)")     # menghapus tanda gagal upload sebelumnya
        else:
            run.log("youtube (unggah uji): selesai")              # kegagalan langkah lain tetap tampil

    from .runs import failure
    # penanda run hanya dipakai bila tidak menutupi kegagalan langkah lain (mis. kiriman Telegram harian)
    markers = (failure(ctx) or {}).get("step") in (None, "publish_youtube")
    if markers:
        return _guarded(run, body)
    try:
        with cancelmod.cancel_scope(cancel):
            body()
        return EXIT_OK
    except media.Cancelled:
        run.log("youtube (unggah uji): dibatalkan")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001  dilaporkan ke halaman/CLI tanpa menimpa tanda gagal run
        run.log(f"youtube (unggah uji) gagal: {e}")
        return EXIT_FAILED
    finally:
        ctx.release()


def recheck_upload(day: date) -> YouTubeResult | None:
    """Cek ulang status video yang sudah diunggah (1 unit kuota) dan coba lagi playlist yang gagal."""
    ctx = RunContext(day, echo=None)
    if not os.path.isfile(ctx.path("youtube.json")):
        return None
    settings = common.load_settings()
    cfg = config.load_config(settings)
    ctx.acquire()                       # RunLocked bila run tanggal ini sedang berjalan
    try:
        old = ctx.load("youtube", YouTubeResult)     # dibaca setelah mengunci: tidak menimpa tulisan run lain
        if old is None or not old.video_id:
            return None
        here = youtube.account().get("channel_id", "")
        if old.channel_id and not here:
            here = youtube._save_account(**youtube.fetch_channel(settings=settings),
                                         checked_at=youtube._now_iso()).get("channel_id", "")
        if old.channel_id and here and old.channel_id != here:
            raise youtube.YouTubeError(f"Video ini ada di channel lain ({old.channel_title or old.channel_id}). "
                                       "Hubungkan channel itu untuk mengecek statusnya.")
        # privasi yang diubah pengguna di YouTube Studio (mis. ditayangkan) bukan masalah
        res = youtube.verify(ctx, old, settings=settings, strict=False)
        if cfg.youtube.playlist_id.strip() and not res.playlist_added:
            res = youtube.add_playlist_once(ctx, res, cfg.youtube.playlist_id.strip(), settings=settings)
        _ensure_history(Run(ctx, cfg, settings, dry_run=False), res)
        from .runs import failure
        if (failure(ctx) or {}).get("step") == "publish_youtube":
            # upload yang tadinya gagal di tahap akhir ternyata ada: tanda gagal di halaman dihapus
            ctx.log("selesai. dijalankan: -; dipakai ulang: publish_youtube (cek status)")
        return res
    finally:
        ctx.release()


def run_config(ctx: RunContext, settings: dict | None = None) -> ChannelConfig:
    """Pengaturan yang dipakai run ini: pengaturan tersimpan + genre khusus run (--genre) bila ada."""
    gid = ctx.fingerprint("genre") or None
    try:
        return prepare_config(gid, settings)
    except ConfigError:
        if gid is None:
            raise
        raise ConfigError(f"Run ini dibuat dengan genre '{gid}' yang tidak dikenal lagi.") from None


def upload_block(day: date) -> dict:
    """Untuk halaman, tanpa jaringan: {"block": alasan belum bisa diunggah ('' = siap), "rebuild": bisa diperbaiki
    dengan membuat ulang video run itu, "genre": genre khusus run (--genre) untuk tombol buat ulang}. Pemeriksaannya
    sama dengan saat upload."""
    ctx = RunContext(day, echo=None)
    genre = ctx.fingerprint("genre")
    out = {"block": "", "rebuild": False, "replan": False, "genre": genre}
    old = ctx.load("youtube", YouTubeResult)
    if old is not None and old.video_id:
        return {**out, "block": "Sudah diunggah."}
    settings = common.load_settings()
    try:
        run = Run(ctx, run_config(ctx, settings), settings, dry_run=True)
        _upload_ready(run, "publish_youtube")
    except UploadBlocked as e:
        res = ctx.load("research", Research)
        # membuat ulang akan riset/menulis naskah baru (pengaturan channel berubah): halaman minta konfirmasi
        replan = res is None or ctx.fingerprint("research") != fp_research(run.cfg) or \
            ctx.fingerprint("script") != fp_script(res, run.cfg)
        return {**out, "block": str(e), "rebuild": e.rebuild, "replan": e.rebuild and replan}
    except (ConfigError, StepError) as e:
        return {**out, "block": str(e)}
    return out


def notify_test(day: date, *, echo=print, cancel: threading.Event | None = None,
                on_progress: Callable[[str, float], None] | None = None) -> int:
    """Kirim paket run `day` ke Telegram atas perintah pengguna (tombol di halaman atau CLI), walau channel masih
    nonaktif. Selalu mengirim ulang semuanya; tidak mengubah catatan kiriman harian."""
    settings = common.load_settings()
    ctx = RunContext(day, echo=echo)
    try:
        try:
            cfg = run_config(ctx, settings)            # genre khusus run ini (--genre) ikut menentukan sidik QC
        except ConfigError:
            cfg = config.load_config(settings)
    except ConfigError as e:
        echo(f"[channel] {e}")
        return EXIT_CONFIG
    if not os.path.isfile(ctx.path("script.json")):
        echo(f"[channel] Run {day.isoformat()} belum punya naskah.")
        return EXIT_CONFIG
    if not telegram.configured(settings):
        echo("[channel] Token bot / chat ID Telegram belum diisi.")
        return EXIT_CONFIG
    run = Run(ctx, cfg, settings, dry_run=False, cancel=cancel, on_progress=on_progress)
    try:
        ctx.acquire()
    except RunLocked as e:
        echo(f"[channel] {e}")
        return EXIT_LOCKED
    run.step = "notify"
    # Kiriman atas perintah pengguna tidak memakai penanda run ("GAGAL di langkah"/"selesai. dijalankan"), supaya
    # status run harian di halaman tidak berubah; hasilnya dilaporkan lewat pekerjaan halaman / CLI.
    from .runs import failure
    try:
        with cancelmod.cancel_scope(cancel):
            run.progress(0.0)
            items = _notify_items(run)
            run.log(f"telegram (kirim uji): mulai, {len(items)} pesan")
            telegram.send_items(items, settings=settings, cancel=cancel, progress=run.progress, log=run.log)
            run.progress(1.0)
        run.log("telegram (kirim uji): selesai")
        if (failure(ctx) or {}).get("step") == "notify":
            # kiriman harian yang tadinya gagal kini sudah sampai: dicatat terkirim (run berikutnya tidak mengirim
            # ulang) dan tanda gagalnya dihapus
            run.ctx.save("telegram", TelegramSent(
                source=sha(_need(run, "script", Script, "notify")), sent=sorted(i.kind for i in items),
                sent_at=timeutil.now_local(cfg.schedule.timezone).isoformat(timespec="seconds")))
            ctx.log("selesai. dijalankan: -; dipakai ulang: notify (kirim uji ke Telegram)")
        return EXIT_OK
    except media.Cancelled:
        run.log("telegram (kirim uji): dibatalkan")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001  dilaporkan ke halaman/CLI tanpa menandai run gagal
        run.log(f"telegram (kirim uji) gagal: {e}")
        return EXIT_FAILED
    finally:
        ctx.release()


# --------------------------------------------------------------------------
# Analis atas perintah pengguna (tombol halaman / CLI), tanpa folder run
# --------------------------------------------------------------------------
ANALYST_LOG_MAX = 256 * 1024


class _AnalystLog:
    """Log analis di luar run harian: ke halaman/CLI dan data/channel/analyst.log. Peringatan terakhir diingat untuk
    pesan gagal di halaman."""

    def __init__(self, echo):
        self.echo, self.warning = echo, ""
        self.path = state.data_path("analyst.log")

    def __call__(self, msg: str) -> None:
        from datetime import datetime
        line = f"{datetime.now():%H:%M:%S} {msg}"
        if self.echo:
            self.echo(f"[channel] {line}")
        if "PERINGATAN" in msg:
            self.warning = msg.split("PERINGATAN", 1)[1].strip()
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path) and os.path.getsize(self.path) > ANALYST_LOG_MAX:
                keep = state.read_text(self.path)[-ANALYST_LOG_MAX // 2:]
                state.write_text_atomic(self.path, keep.split("\n", 1)[-1])
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except OSError:
            pass


def _analyst_config(settings: dict, echo) -> ChannelConfig | None:
    try:
        return config.load_config(settings)
    except ConfigError as e:
        echo(f"[channel] {e}")
        return None


def analyze_now(*, echo=print, cancel: threading.Event | None = None,
                on_progress: Callable[[str, float], None] | None = None, force: bool = True) -> int:
    """Perbarui analisis performa sekarang (boleh walau channel nonaktif: hanya membaca YouTube). Laporan mingguan
    tidak dikirim dari sini."""
    settings = common.load_settings()
    cfg = _analyst_config(settings, echo)
    if cfg is None:
        return EXIT_CONFIG
    log = _AnalystLog(echo)
    today = timeutil.today_local(cfg.schedule.timezone)
    prog = (lambda f: on_progress("analyst", f)) if on_progress else None
    try:
        with cancelmod.cancel_scope(cancel), analyst.guard():
            if prog:
                prog(0.0)
            log(f"analis: mulai ({today.isoformat()})")
            perf, did = analyst.run(cfg, settings, today, force=force, log=log, progress=prog,
                                    on_found=_record_found)
    except analyst.AnalystBusy as e:
        log(f"analis gagal: {e}")
        return EXIT_LOCKED
    except media.Cancelled:
        log("analis: dibatalkan")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001  dilaporkan ke halaman/CLI
        log(f"analis gagal: {e}")
        return EXIT_FAILED
    if force and not did:
        log(f"analis gagal: {log.warning or 'performa belum bisa dibaca'}")
        return EXIT_FAILED
    if prog:
        prog(1.0)
    log("analis: selesai" + (f" ({perf.learnings_state})" if perf else ""))
    return EXIT_OK


def weekly_report_now(*, echo=print, cancel: threading.Event | None = None,
                      on_progress: Callable[[str, float], None] | None = None) -> int:
    """Kirim laporan mingguan sekarang atas perintah pengguna (boleh walau channel nonaktif). Bila laporan minggu
    ini belum terkirim, kiriman ini dicatat sebagai laporan minggu ini (run harian tidak mengirim ulang)."""
    settings = common.load_settings()
    cfg = _analyst_config(settings, echo)
    if cfg is None:
        return EXIT_CONFIG
    if not telegram.configured(settings):
        echo("[channel] Token bot / chat ID Telegram belum diisi.")
        return EXIT_CONFIG
    log = _AnalystLog(echo)
    today = timeutil.today_local(cfg.schedule.timezone)
    prog = (lambda f: on_progress("weekly_report", f)) if on_progress else None
    try:
        with cancelmod.cancel_scope(cancel), analyst.guard():
            if prog:
                prog(0.0)
            perf, _ = analyst.run(cfg, settings, today, log=log,
                                  progress=(lambda f: prog(0.8 * f)) if prog else None, on_found=_record_found)
            if perf is None or not perf.videos:
                if perf is None:             # galat sesaat saat membaca YouTube: alasannya di peringatan analis
                    why = log.warning or "performa YouTube belum bisa dibaca"
                else:
                    why = perf.notes[0] if perf.notes else "belum ada video buatan Nero dengan data YouTube"
                log(f"laporan mingguan gagal: {why}")
                return EXIT_FAILED
            telegram.send_items(analyst.weekly_items(perf), settings=settings, cancel=cancel, log=log)
            if analyst.weekly_due(cfg, today):
                analyst.mark_weekly(cfg, today)
    except analyst.AnalystBusy as e:
        log(f"laporan mingguan gagal: {e}")
        return EXIT_LOCKED
    except media.Cancelled:
        log("laporan mingguan: dibatalkan")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001  dilaporkan ke halaman/CLI
        log(f"laporan mingguan gagal: {e}")
        return EXIT_FAILED
    if prog:
        prog(1.0)
    log("laporan mingguan: terkirim ke Telegram")
    return EXIT_OK
