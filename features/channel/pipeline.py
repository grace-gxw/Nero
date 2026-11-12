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

Fase I-2: research, script, qc, seo. Fase I-3: tts, visuals, render. Langkah lain menyusul (lihat
INTEGRATION_PLAN.md) dan dilewati sampai tersedia.
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

from . import config, genres, media, research, state, timeutil, writer
from .config import ChannelConfig, ConfigError
from .runctx import DEPENDS, FINGERPRINTS, STEPS, RunContext, RunLocked, downstream, sha
from .schemas import QC, Issue, Metadata, RenderInfo, Research, Script, Visuals, Words

PLAN_STEPS = ("research", "script", "qc", "seo")
MEDIA_STEPS = ("tts", "visuals", "render")
IMPLEMENTED = {*PLAN_STEPS, *MEDIA_STEPS}
ALIASES = {"plan": PLAN_STEPS, "media": MEDIA_STEPS, "video": PLAN_STEPS + MEDIA_STEPS}
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


def _unpublish(run: Run) -> None:
    """Tarik salinan video di folder hasil (video lama tidak boleh tertinggal untuk diunggah)."""
    for name in PUBLISHED:
        path = os.path.join(_output_dir(run), name)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise StepError(run.step, f"{path} sedang dibuka program lain ({e.strerror}). Tutup berkas itu lalu "
                                      "jalankan ulang.") from None


def _begin(run: Run, step: str) -> None:
    """Langkah akan bekerja: tarik salinan video bila video bergantung pada langkah ini, lalu hapus hasil lama
    langkah ini (langkah yang gagal di tengah jalan tidak meninggalkan hasil lama yang tampak valid)."""
    if "render" in (step, *downstream(step)):
        _unpublish(run)
    run.ctx.invalidate(step)


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
    _begin(run, "research")
    res = research.run(run.cfg, run.ctx.day, settings=run.settings, learnings=state.load_learnings(),
                       log=run.log)
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
    script = writer.write_script(run.cfg, res, fmt, settings=run.settings, learnings=state.load_learnings(),
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
        run.cfg, res, script.format, script=script, settings=run.settings, learnings=state.load_learnings(),
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
        return False
    _begin(run, "seo")
    run.ctx.save("metadata", writer.seo(run.cfg, script, res, settings=run.settings, log=run.log))
    run.ctx.set_fingerprint("seo", fp)
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
    new = writer.write_script(run.cfg, res, script.format, settings=run.settings, learnings=state.load_learnings(),
                              recent_hooks=state.last_hooks(5), issues=[issue], previous=script, log=run.log)
    run.ctx.save("script", new)
    media.check_cancel(run.cancel)
    new, qc = writer.write_and_review(
        run.cfg, res, script.format, script=new, settings=run.settings, learnings=state.load_learnings(),
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
    _refresh_description(run)
    run.log("visual: selesai (" + ", ".join(f"{v.segment_id}:{v.type}" for v in vis.segments) + ")")
    return True


def _publish_local(run: Run) -> str:
    """Salin hasil ke output/channel/<tanggal>/ supaya tombol "Folder hasil" di Nero bekerja. Atomik dan
    idempoten: berkas yang sudah sama dilewati. Gagal menyalin tidak menggagalkan run (dicoba lagi run berikutnya)."""
    out = _output_dir(run)
    os.makedirs(out, exist_ok=True)
    for name in PUBLISHED:
        src, dst = run.ctx.path(name), os.path.join(out, name)
        if not os.path.isfile(src):
            continue
        st = os.stat(src)
        if os.path.isfile(dst) and os.path.getsize(dst) == st.st_size and os.path.getmtime(dst) >= st.st_mtime:
            continue
        tmp = dst + ".tmp"
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        except OSError as e:
            run.log(f"render: PERINGATAN {name} belum bisa disalin ke folder hasil ({e.strerror}); dicoba lagi nanti")
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
    _refresh_description(run)
    run.log(f"render: hasil disalin ke {_publish_local(run)}")
    if info.needs_review:
        run.log("render: PERLU DICEK MANUSIA sebelum diunggah: " + "; ".join(info.warnings))
    return True


STEP_FUNCS = {"research": step_research, "script": step_script, "qc": step_qc, "seo": step_seo,
              "tts": step_tts, "visuals": step_visuals, "render": step_render}


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
        words = ctx.load("words", Words)
        if words is not None and os.path.isfile(ctx.path("voice.wav")):
            fps["tts"] = fp_tts(script, run.cfg)
            vis = ctx.load("visuals", Visuals)
            if vis is not None:
                fps["visuals"] = fp_visuals(script, words, run.cfg)
                if ctx.load("render", RenderInfo) is not None:
                    fps["render"] = fp_render(words, vis, run.cfg, ctx.day)
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
    run = Run(ctx, cfg, settings, dry_run, cancel=cancel, on_progress=on_progress)
    try:
        ctx.acquire()
    except RunLocked as e:
        echo(f"[channel] {e}")
        return EXIT_LOCKED
    current = "-"
    try:
        run.log(f"mulai run {day.isoformat()} ({'uji coba' if dry_run else 'sungguhan'}), niche: {cfg.channel.niche}")
        freed = state.prune_runs(min(day, today), keep=(day.isoformat(), today.isoformat()))
        if freed:
            run.log(f"bersih-bersih: {freed} berkas sementara dari run lama dihapus")
        _adopt_legacy(run)
        ran: set[str] = set()
        for step in STEPS:
            if step not in steps:
                continue
            if step not in IMPLEMENTED:
                run.log(f"langkah {step} belum tersedia (menyusul di fase berikutnya), dilewati")
                continue
            current = run.step = step
            media.check_cancel(cancel)
            run.progress(0.0)
            # langkah rencana ikut diulang bila langkah yang hasilnya ia pakai baru dijalankan; langkah media
            # memakai sidik masukannya (tidak ada suara/gambar AI yang dibuat ulang bila masukannya sama)
            forced = step in force or (step in PLAN_STEPS and any(d in ran for d in DEPENDS.get(step, ())))
            did = STEP_FUNCS[step](run, forced)
            run.progress(1.0)
            (run.ran if did else run.skipped).append(step)
            if did:
                ran.add(step)
        run.log(f"selesai. dijalankan: {', '.join(run.ran) or '-'}; dipakai ulang: {', '.join(run.skipped) or '-'}")
        return EXIT_OK
    except media.Cancelled:
        run.log(f"DIBATALKAN di langkah {current}")
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001  semua kegagalan langkah dilaporkan dengan cara yang sama
        run.log(f"GAGAL di langkah {getattr(e, 'step', None) or current}: {e}")
        with open(ctx.path("run.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        # alert Telegram (nama langkah + 30 baris log terakhir) ditambahkan di Fase I-6
        return EXIT_FAILED
    finally:
        ctx.release()
