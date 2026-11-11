"""
Nero · Channel Otomatis - render video akhir dan validasinya (SPEC.md 6.9).

Dua tahap (INTEGRATION_PLAN K6):
1. ffmpeg menyambung klip segmen (tanpa encode ulang) + voice.wav + musik opsional -> base.mkv.
2. engine.render_clip (mesin Clip Maker) menambahkan subtitle bergaya Clip Maker dan progress bar, menormalkan
   kenyaringan, lalu meng-encode final.mp4 (NVENC bila ada, jika tidak libx264).

Validasi (SPEC 6.9): 1080×1920, 30 fps, ada audio, durasi dalam target ±3 dtk. Tidak valid = pipeline gagal dan
video tidak boleh di-upload.
"""
from __future__ import annotations

import os
import random
import threading
from datetime import date

from features.clip import engine

from . import media
from .config import ChannelConfig
from .media import FPS, H, W
from .schemas import RenderInfo, Visuals, Words

MUSIC_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")
MUSIC_MIN_SECONDS = 2.0
MUSIC_FADE_IN = 0.8
MUSIC_FADE_OUT = 1.5
DURATION_SLACK = 3.0
LENGTH_TOLERANCE = 0.15     # selisih durasi video akhir vs rencana yang masih diterima (detik)
BASE_TIMEOUT = 300          # video dasar hanya menyalin video + mencampur audio
AUDIO_RATE = 48000
DEFAULT_STYLE_ID = "preset:klasik"


class RenderError(RuntimeError):
    """Render gagal (pesan aman ditampilkan)."""


# --------------------------------------------------------------------------
# Gaya subtitle
# --------------------------------------------------------------------------
def caption_style(cfg: ChannelConfig, log=print) -> dict:
    """Gaya subtitle dari pustaka Clip Maker (preset:<id> atau id "Gaya saya"), disesuaikan untuk Shorts:
    tanpa potong jeda, tanpa zoom (teks layar & label di atas tidak boleh terpotong), tanpa judul, dan posisi
    subtitle di 55–65% tinggi layar (SPEC 6.9)."""
    from features.clip import api as clip_api
    from features.clip import presets
    sid = cfg.video.caption_style or DEFAULT_STYLE_ID
    entry = clip_api.find_style(sid)
    if entry is None:
        log(f"render: gaya subtitle '{sid}' tidak ditemukan, memakai {DEFAULT_STYLE_ID}")
        entry = presets.PRESET_BY_ID[DEFAULT_STYLE_ID]
    style = {**presets.DEFAULT_STYLE, **(entry.get("style") or {})}
    style.update(cut_pauses=0, zoom="tanpa", layout="fill", captions=True, title_on=False, handle="",
                 color_bt709=True)    # klip antara BT.709; video akhir dikonversi & diberi tanda yang sama
    try:
        pos = float(style.get("caption_pos", 0.6))
    except (TypeError, ValueError):
        pos = 0.6
    style["caption_pos"] = min(0.65, max(0.55, pos))
    return style


# --------------------------------------------------------------------------
# Musik & video dasar
# --------------------------------------------------------------------------
def music_candidates(music_dir: str, day: date, log=print, limit: int = 2) -> list[str]:
    """Lagu dari folder musik pengguna, urutan tetap untuk tanggal yang sama. Berkas yang tidak berisi audio
    (rusak, kosong, belum terunduh dari cloud) dilewati."""
    if not music_dir or not os.path.isdir(music_dir):
        return []
    files = sorted(f for f in os.listdir(music_dir) if f.lower().endswith(MUSIC_EXT)
                   and os.path.isfile(os.path.join(music_dir, f)))
    if not files:
        return []
    start = random.Random(day.toordinal()).randrange(len(files))
    out = []
    for f in files[start:] + files[:start]:
        path = os.path.join(music_dir, f)
        if media.audio_seconds(path) >= MUSIC_MIN_SECONDS:
            out.append(path)
            if len(out) >= limit:
                break
        else:
            log(f"render: musik '{f}' dilewati (tidak terbaca atau terlalu pendek)")
    return out


def pick_music(music_dir: str, day: date, log=print) -> str | None:
    found = music_candidates(music_dir, day, log, limit=1)
    return found[0] if found else None


def concat_list(run_dir: str, visuals: Visuals) -> str:
    path = os.path.join(run_dir, "clips", "concat.txt")
    lines = []
    for v in visuals.segments:
        full = os.path.join(run_dir, v.file)
        if not os.path.isfile(full):
            raise RenderError(f"Klip {v.file} tidak ada; jalankan ulang langkah visuals.")
        lines.append("file '" + full.replace("\\", "/").replace("'", "'\\''") + "'")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def audio_graph(total: float, music: bool, music_db: float) -> str:
    """Filter audio: suara diisi hening sampai akhir video; musik (bila ada) diulang, dipelankan, fade in/out."""
    voice = (f"[1:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,apad,atrim=0:{total:.4f},"
             "asetpts=N/SR/TB")
    if not music:
        return voice + "[a]"
    fade_out = max(0.0, total - MUSIC_FADE_OUT)
    return (f"{voice}[vo];"
            f"[2:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,volume={music_db}dB,atrim=0:{total:.4f},"
            f"asetpts=N/SR/TB,afade=t=in:st=0:d={MUSIC_FADE_IN},afade=t=out:st={fade_out:.4f}:d={MUSIC_FADE_OUT}[mu];"
            "[vo][mu]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")


def base_args(list_path: str, voice_wav: str, music: str | None, total_frames: int, music_db: float,
              out: str) -> list[str]:
    total = total_frames / FPS
    args = ["-f", "concat", "-safe", "0", "-i", list_path, "-i", voice_wav]
    if music:
        args += ["-stream_loop", "-1", "-i", music]
    return [*args, "-filter_complex", audio_graph(total, bool(music), music_db), "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "pcm_s16le", "-frames:v", str(total_frames), "-t", f"{total:.4f}", out]


# --------------------------------------------------------------------------
# Validasi
# --------------------------------------------------------------------------
def needs_review(cfg: ChannelConfig, visuals: Visuals) -> list[str]:
    """Alasan video perlu dicek manusia: variasi visual kurang dari pengaturan, atau banyak latar polos."""
    out = []
    types = {v.type for v in visuals.segments}
    real = sorted(types - {"plain"})
    need = min(cfg.visuals.min_types_per_video, len(cfg.visuals.allowed_types))
    if len(real) < need:
        out.append(f"hanya {len(real)} jenis visual ({', '.join(sorted(types))}); minimal {need}")
    plain = sum(v.type == "plain" for v in visuals.segments)
    if plain * 3 > len(visuals.segments):
        out.append(f"{plain} dari {len(visuals.segments)} segmen hanya latar polos")
    return out


def validate(path: str, cfg: ChannelConfig, visuals: Visuals | None = None) -> RenderInfo:
    if not os.path.isfile(path):
        return RenderInfo(width=0, height=0, fps=0, duration=0, has_audio=False, valid=False,
                          problems=["final.mp4 tidak ada"])
    info = engine.probe(path)
    lo, hi = cfg.video.target_seconds
    problems = []
    if (info.width, info.height) != (W, H):
        problems.append(f"ukuran {info.width}x{info.height}, seharusnya {W}x{H}")
    if abs(info.fps - FPS) > 0.05:
        problems.append(f"{info.fps:g} fps, seharusnya {FPS}")
    if not info.has_audio:
        problems.append("tidak ada audio")
    if not lo - DURATION_SLACK <= info.duration <= hi + DURATION_SLACK:
        problems.append(f"durasi {info.duration:.1f} dtk di luar {lo}–{hi} dtk (±{DURATION_SLACK:g})")
    warnings, types = [], []
    if visuals is not None:
        expected = visuals.total_frames / FPS
        if abs(info.duration - expected) > LENGTH_TOLERANCE:   # klip/suara terpotong di tengah jalan
            problems.append(f"durasi {info.duration:.2f} dtk tidak sama dengan rencana {expected:.2f} dtk")
        types = sorted({v.type for v in visuals.segments})
        warnings = needs_review(cfg, visuals)
    return RenderInfo(width=info.width, height=info.height, fps=info.fps, duration=round(info.duration, 3),
                      has_audio=info.has_audio, valid=not problems, problems=problems, warnings=warnings,
                      needs_review=bool(warnings), visual_types=types,
                      size_mb=round(os.path.getsize(path) / 1e6, 2))


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def render(run_dir: str, words: Words, visuals: Visuals, cfg: ChannelConfig, day: date, *, log=print,
           cancel: threading.Event | None = None, progress=None) -> RenderInfo:
    voice = os.path.join(run_dir, "voice.wav")
    if not os.path.isfile(voice):
        raise RenderError("voice.wav tidak ada; jalankan ulang langkah tts.")
    if abs(media.wav_duration(voice) - words.duration) > 0.05:
        raise RenderError("voice.wav tidak cocok dengan words.json; jalankan ulang langkah tts.")
    n = visuals.total_frames
    from .visuals import spans_frames
    if n <= 0 or [v.frames for v in visuals.segments] != spans_frames(visuals, words):
        raise RenderError("Klip visual tidak cocok dengan suara; jalankan ulang langkah visuals.")
    base = os.path.join(run_dir, "base.mkv")
    final = os.path.join(run_dir, "final.mp4")
    concat = concat_list(run_dir, visuals)
    music, music_warning, last_error = None, "", ""
    try:
        # lagu pertama; bila gagal dibaca ffmpeg, lagu berikutnya; terakhir tanpa musik
        for cand in [*music_candidates(cfg.video.music_dir, day, log), None]:
            log(f"render: video dasar {n / FPS:.1f} dtk, musik: {os.path.basename(cand) if cand else '-'}")
            try:
                media.ffmpeg(base_args(concat, voice, cand, n, cfg.video.music_volume_db, base), cancel=cancel,
                             timeout=BASE_TIMEOUT)
            except RuntimeError as e:
                last_error = str(e)
                if cand is None:
                    raise RenderError(f"Video dasar gagal dibuat: {e}") from None
                music_warning = f"musik '{os.path.basename(cand)}' tidak bisa dipakai"
                log(f"render: {music_warning} ({last_error[:160]})")
                continue
            music = cand
            break
    except BaseException:
        _cleanup(base, concat)
        raise
    if music_warning and not music:
        music_warning += "; video dibuat tanpa musik"
    if progress:
        progress(0.1)
    style = caption_style(cfg, log)
    words_abs = [{"start": w.start, "end": w.end, "word": w.word} for w in words.words]

    def prog(p: float) -> None:
        media.check_cancel(cancel)       # Cancelled bukan RuntimeError: engine tidak beralih ke libx264
        if progress:
            progress(0.1 + 0.85 * p)

    log("render: subtitle + encode akhir (mesin Clip Maker)")
    try:
        # (n + 0.5) / FPS: engine menghitung int(durasi × 30) frame, jadi tidak boleh kurang sedikit karena float
        engine.render_clip(base, 0.0, (n + 0.5) / FPS, final, words_abs, style, title="", progress=prog)
    except BaseException:
        _cleanup(final + ".tmp.mp4", final + ".log", base)
        raise
    finally:
        _cleanup(concat)
    engine.write_srt(words_abs, 0.0, n / FPS + 1, os.path.join(run_dir, "captions.srt"))
    info = validate(final, cfg, visuals)
    info.music = os.path.basename(music) if music else ""
    if music_warning:
        info.warnings.append(music_warning)
    info.encoder = engine.video_encoder()
    _cleanup(base)
    if progress:
        progress(1.0)
    log(f"render: {info.width}x{info.height}, {info.fps:g} fps, {info.duration:.1f} dtk, {info.size_mb} MB, "
        f"{'VALID' if info.valid else 'TIDAK VALID: ' + '; '.join(info.problems)}")
    for w in info.warnings:
        log(f"render: PERINGATAN {w}")
    return info
