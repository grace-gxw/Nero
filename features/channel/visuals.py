"""
Nero · Channel Otomatis - visual per segmen (SPEC.md 6.8).

Setiap segmen naskah menjadi satu klip clips/seg_{id}.mp4: 1080×1920, 30 fps, tanpa audio, dengan jumlah frame
persis sepanjang segmennya (dari words.json). Render tidak perlu tahu jenis visualnya.

Rentang waktu: visual segmen pertama juga menutupi hook, dan visual segmen terakhir menutupi CTA serta ekor video.

Rantai cadangan (SPEC 11): ai_image → stock → motion_graphic → latar polos; stock → motion_graphic → polos;
motion_graphic → polos. Setiap cadangan yang dipakai dicatat alasannya di visuals.json.
"""
from __future__ import annotations

import itertools
import os
import random
import re
import threading
from dataclasses import dataclass, field

from . import ai_images, config, graphics, media, stock
from .config import ChannelConfig
from .media import FPS, H, W
from .schemas import Script, Segment, SegmentVisual, Visuals, Words

KB_UPSCALE = 4          # gambar diperbesar 4× sebelum zoompan supaya gerak tidak bergetar
KB_ZOOM = 0.12          # zoom 1.00 → 1.12
LONG_SEGMENT = 6.0      # segmen gambar AI lebih panjang dari ini memakai 2 gambar + crossfade
XFADE = 0.5             # detik crossfade antar-gambar AI
MOTIONS = ("zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in_top", "pan_down")


class VisualError(RuntimeError):
    """Satu jenis visual gagal untuk satu segmen (cadangan berikutnya dicoba)."""


@dataclass
class Span:
    segment: Segment
    start: float
    end: float
    f0: int
    f1: int

    @property
    def frames(self) -> int:
        return self.f1 - self.f0

    @property
    def seconds(self) -> float:
        return self.frames / FPS


def total_seconds(words: Words) -> float:
    from .voice import TAIL_SECONDS
    return words.duration + TAIL_SECONDS


def spans(script: Script, words: Words) -> list[Span]:
    """Rentang frame tiap segmen. Batas segmen = saat bagian narasinya mulai (setelah jeda)."""
    timing = {t.segment_id: t for t in words.segments}
    starts = []
    for i, seg in enumerate(script.segments):
        if seg.id not in timing:
            raise VisualError(f"timing segmen {seg.id} tidak ada di words.json; jalankan ulang langkah tts.")
        starts.append(0.0 if i == 0 else timing[seg.id].start)
    frames = [media.frame_at(s) for s in starts] + [media.frame_at(total_seconds(words))]
    out = []
    for i, seg in enumerate(script.segments):
        f0, f1 = frames[i], frames[i + 1]
        if f1 <= f0:
            raise VisualError(f"segmen {seg.id} tidak punya durasi.")
        out.append(Span(seg, f0 / FPS, f1 / FPS, f0, f1))
    return out


def spans_frames(vis: Visuals, words: Words) -> list[int]:
    """Jumlah frame tiap segmen menurut words.json (urutan segmen di visuals.json), untuk cek kecocokan."""
    timing = {t.segment_id: t for t in words.segments}
    ids = [v.segment_id for v in vis.segments]
    if any(i not in timing for i in ids[1:]):
        return []
    frames = [0] + [media.frame_at(timing[i].start) for i in ids[1:]] + [media.frame_at(total_seconds(words))]
    return [b - a for a, b in itertools.pairwise(frames)]


# --------------------------------------------------------------------------
# Teks layar
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]+", " ", text.lower()).split())


def similar(a: str, b: str) -> bool:
    """Dua teks berisi hal yang sama: semua kata yang satu ada di yang lain ('Angin bergolak' vs 'Angin matahari
    bergolak'). 'Wilayah heliosheath' vs 'Masih di heliosheath' tidak sama: ada kata baru."""
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def show_osd(seg: Segment, used_text: str = "") -> bool:
    """on_screen_text tampil, kecuali isinya sudah tampil sebagai grafik/teks besar (INTEGRATION_PLAN §7)."""
    text = seg.on_screen_text.strip()
    return bool(text) and not (used_text and similar(text, used_text))


# --------------------------------------------------------------------------
# Ken Burns (zoompan)
# --------------------------------------------------------------------------
def _smooth(p: str) -> str:
    return f"(({p})*({p})*(3-2*({p})))"


def zoompan_filter(n_frames: int, motion: str) -> str:
    """Filter zoompan untuk satu gambar yang sudah diperbesar KB_UPSCALE× dari 1080×1920."""
    n = max(1, n_frames - 1)
    p = _smooth(f"min(on/{n},1)")
    z0, z1 = 1.0, 1 + KB_ZOOM
    ax, ay = "0.5", "0.5"
    if motion == "zoom_out":
        z = f"{z1}-{KB_ZOOM}*{p}"
    elif motion in ("pan_right", "pan_left", "pan_down"):
        z = f"{z1}"
        if motion == "pan_right":
            ax = p
        elif motion == "pan_left":
            ax = f"(1-{p})"
        else:
            ay = f"(0.15+0.7*{p})"
    else:
        z = f"{z0}+{KB_ZOOM}*{p}"
        if motion == "zoom_in_top":
            ay = "0.3"
    x = f"(iw-iw/zoom)*{ax}"
    y = f"(ih-ih/zoom)*{ay}"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={n_frames}:s={W}x{H}:fps={FPS}"


def ken_burns_args(images: list[str], n_frames: int, motion_index: int, out_path: str,
                   overlay_png: str | None = None) -> list[str]:
    """Argumen ffmpeg: 1 gambar → satu gerakan; 2 gambar → dua gerakan disambung crossfade."""
    kw, kh = W * KB_UPSCALE, H * KB_UPSCALE
    prep = f"scale={kw}:{kh}:force_original_aspect_ratio=increase:flags=bicubic,crop={kw}:{kh},setsar=1"
    args: list[str] = []
    for img in images:
        args += ["-i", img]
    parts = []
    if len(images) == 1:
        parts.append(f"[0:v]{prep},{zoompan_filter(n_frames, MOTIONS[motion_index % len(MOTIONS)])},"
                     f"setsar=1,{media.TO_BT709}[kb]")
    else:
        xf = round(XFADE * FPS)
        n1 = (n_frames + xf) // 2
        n2 = n_frames + xf - n1
        m1, m2 = MOTIONS[motion_index % len(MOTIONS)], MOTIONS[(motion_index + 1) % len(MOTIONS)]
        parts.append(f"[0:v]{prep},{zoompan_filter(n1, m1)},setsar=1,{media.TO_BT709}[a]")
        parts.append(f"[1:v]{prep},{zoompan_filter(n2, m2)},setsar=1,{media.TO_BT709}[b]")
        parts.append(f"[a][b]xfade=transition=fade:duration={XFADE}:offset={(n1 - xf) / FPS:.4f}[kb]")
    last = "kb"
    if overlay_png:
        args += ["-i", overlay_png]
        parts.append(f"[kb][{len(images)}:v]overlay=0:0:format=auto,format=yuv420p[ov]")
        last = "ov"
    parts[-1] = parts[-1].rsplit("[", 1)[0] + f",tpad=stop_mode=clone:stop=-1[{last}]"
    return [*args, "-filter_complex", ";".join(parts), "-map", f"[{last}]", *media.CLIP_CODEC,
            "-frames:v", str(n_frames), out_path]


def stock_args(src: str, clip_seconds: float, n_frames: int, out_path: str,
               overlay_png: str | None = None) -> list[str]:
    """Argumen ffmpeg: potong klip stok sepanjang segmen, isi layar 9:16, 30 fps, tanpa audio."""
    need = n_frames / FPS
    offset = min(1.0, (clip_seconds - need) / 2) if clip_seconds > need + 0.5 else 0.0
    loop = clip_seconds < need + offset + 1 / FPS      # klip kurang panjang: diulang, bukan frame terakhir dibekukan
    args = (["-stream_loop", "-1"] if loop else []) + ["-ss", f"{offset:.3f}", "-i", src]
    vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,"
          f"{media.TO_BT709}")
    if overlay_png:
        args += ["-i", overlay_png]
        graph = f"[0:v]{vf}[b];[b][1:v]overlay=0:0:format=auto,format=yuv420p,tpad=stop_mode=clone:stop=-1[v]"
    else:
        graph = f"[0:v]{vf},tpad=stop_mode=clone:stop=-1[v]"
    return [*args, "-filter_complex", graph, "-map", "[v]", "-an", *media.CLIP_CODEC,
            "-frames:v", str(n_frames), out_path]


# --------------------------------------------------------------------------
# Pembuat klip
# --------------------------------------------------------------------------
@dataclass
class Ctx:
    run_dir: str
    cfg: ChannelConfig
    keys: dict[str, str]
    cache_dir: str
    exclude: set[str]
    log: object = print
    cancel: threading.Event | None = None
    session: object = None
    rng: random.Random = field(default_factory=random.Random)
    ai_index: int = 0

    def path(self, *parts: str) -> str:
        return os.path.join(self.run_dir, *parts)


def _overlay_png(ctx: Ctx, seg_id: int, text: str, illustration: bool) -> str | None:
    img = graphics.overlay_layer(text, illustration)
    if img is None:
        return None
    path = ctx.path("clips", f"overlay_{seg_id}.png")
    img.save(path)
    return path


def make_stock(ctx: Ctx, span: Span, out: str) -> SegmentVisual:
    seg = span.segment
    osd = show_osd(seg)
    notes: list[str] = []
    for attempt in range(2):
        try:
            clip, src, found_notes = stock.find(seg.visual_query, span.seconds, keys=ctx.keys,
                                                exclude=ctx.exclude, cache_dir=ctx.cache_dir, log=ctx.log,
                                                session=ctx.session, cancel=ctx.cancel)
        except stock.StockError as e:
            raise VisualError("; ".join([*notes, str(e)])) from None
        notes += found_notes
        ctx.exclude.add(clip.key)            # tidak dipakai dua kali dalam satu video (juga bila rusak)
        ov = _overlay_png(ctx, seg.id, seg.on_screen_text if osd else "", False)
        try:
            real = media.video_seconds(src)  # durasi dari API dibulatkan ke detik; pakai durasi berkas asli
            if real <= 0:
                raise VisualError(f"klip {clip.key} tidak bisa dibaca")
            media.ffmpeg(stock_args(src, real, span.frames, out, ov), cancel=ctx.cancel)
            break
        except (VisualError, ValueError, RuntimeError) as e:
            notes.append(f"{clip.key}: {e}")
            ctx.log(f"visual: klip {clip.key} rusak/gagal diproses, dibuang dari cache")
            stock.forget(src)
            if attempt == 1:
                raise VisualError("; ".join(notes)) from None
        finally:
            if ov and os.path.exists(ov):
                os.remove(ov)
    return SegmentVisual(segment_id=seg.id, type="stock", requested=seg.visual_type, file="", source=clip.source,
                         source_id=clip.id, url=clip.page_url, author=clip.author, query=seg.visual_query,
                         on_screen_text=osd, fallbacks=[f"stok: {n}" for n in notes])


def make_ai(ctx: Ctx, span: Span, out: str) -> SegmentVisual:
    seg = span.segment
    prompt = seg.image_prompt.strip()
    if not prompt:
        raise VisualError("image_prompt kosong")
    cf = ctx.cfg.visuals.ai_image
    count = 2 if span.seconds > LONG_SEGMENT else 1
    if not ai_images.quota_ok(cf, count):
        raise VisualError("kuota gambar AI hari ini hampir habis (batas aman)")
    account, token = ctx.keys.get("cloudflare_account_id", ""), ctx.keys.get("cloudflare_token", "")
    multipart = "/flux-2" in cf.model
    seeds, files = [], []
    for k in range(count):
        seed = ctx.rng.randrange(1, 2**31 - 1) if multipart else None
        try:
            img = ai_images.generate(prompt, cf, account=account, token=token, seed=seed, session=ctx.session,
                                     log=ctx.log, cancel=ctx.cancel)
        except ai_images.AIImageError as e:
            if k == 0:
                raise VisualError(str(e)) from None
            ctx.log(f"visual: gambar AI kedua gagal ({e}); segmen memakai satu gambar")
            break
        rel = f"images/seg_{seg.id}{'' if k == 0 else '_b'}.png"
        os.makedirs(ctx.path("images"), exist_ok=True)
        img.save(ctx.path(rel))
        files.append(rel)
        seeds.append(seed)
    osd = show_osd(seg)
    ov = _overlay_png(ctx, seg.id, seg.on_screen_text if osd else "", seg.label_illustration)
    try:
        media.ffmpeg(ken_burns_args([ctx.path(f) for f in files], span.frames, ctx.ai_index, out, ov),
                     cancel=ctx.cancel)
    finally:
        if ov and os.path.exists(ov):
            os.remove(ov)
    ctx.ai_index += len(files)
    return SegmentVisual(segment_id=seg.id, type="ai_image", requested=seg.visual_type, file="",
                         source="cloudflare", source_id=cf.model, prompt=ai_images.full_prompt(prompt, cf),
                         seed=seeds[0], images=files, on_screen_text=osd, query=seg.visual_query)


def graphic_source(seg: Segment) -> tuple[str, str]:
    """(template, isi) untuk motion graphic: isi grafik dari naskah, atau teks layar sebagai kartu judul."""
    if seg.graphic.content.strip():
        return seg.graphic.template, seg.graphic.content.strip()
    if seg.on_screen_text.strip():
        return "title_card", seg.on_screen_text.strip()
    raise VisualError("tidak ada isi untuk grafik")


def make_graphic(ctx: Ctx, span: Span, out: str) -> SegmentVisual:
    seg = span.segment
    template, content = graphic_source(seg)
    osd = show_osd(seg, content)
    overlay = graphics.overlay_layer(seg.on_screen_text if osd else "", False)
    scene = graphics.make_scene(template, content, span.frames, overlay=overlay)
    media.encode_frames(media.frames_of(scene.frame, span.frames), span.frames, out, cancel=ctx.cancel)
    return SegmentVisual(segment_id=seg.id, type="motion_graphic", requested=seg.visual_type, file="",
                         source="nero", source_id=template, prompt=content, on_screen_text=osd,
                         query=seg.visual_query)


def make_plain(ctx: Ctx, span: Span, out: str) -> SegmentVisual:
    seg = span.segment
    scene = graphics.plain_scene(span.frames, seg.on_screen_text)
    media.encode_frames(media.frames_of(scene.frame, span.frames), span.frames, out, cancel=ctx.cancel)
    return SegmentVisual(segment_id=seg.id, type="plain", requested=seg.visual_type, file="", source="nero",
                         prompt=seg.on_screen_text.strip(), query=seg.visual_query)


MAKERS = {"stock": make_stock, "ai_image": make_ai, "motion_graphic": make_graphic, "plain": make_plain}
CHAINS = {"ai_image": ("ai_image", "stock", "motion_graphic", "plain"),
          "stock": ("stock", "motion_graphic", "plain"),
          "motion_graphic": ("motion_graphic", "plain")}


def chain_for(seg: Segment, allowed: list[str]) -> list[str]:
    out = [t for t in CHAINS[seg.visual_type] if t != seg.visual_type or t in allowed]
    return out if out[-1] == "plain" else [*out, "plain"]


def build_segment(ctx: Ctx, span: Span) -> SegmentVisual:
    seg = span.segment
    final = ctx.path("clips", f"seg_{seg.id}.mp4")
    out = final + ".tmp.mp4"                 # klip baru ditulis ke sini, lalu menggantikan klip lama (atomik)
    notes: list[str] = []
    if seg.visual_type not in ctx.cfg.visuals.allowed_types:
        notes.append(f"{seg.visual_type}: tidak diizinkan di pengaturan")
    for kind in chain_for(seg, ctx.cfg.visuals.allowed_types):
        media.check_cancel(ctx.cancel)
        try:
            got = MAKERS[kind](ctx, span, out)
        except BaseException as e:
            if os.path.exists(out):
                os.remove(out)
            if not isinstance(e, Exception) or isinstance(e, media.Cancelled):
                raise
            notes.append(f"{kind}: {e}")
            ctx.log(f"visual: segmen {seg.id} {kind} gagal ({e}); coba cadangan berikutnya")
            continue
        os.replace(out, final)
        got.file = f"clips/seg_{seg.id}.mp4"
        got.start, got.end, got.frames = round(span.start, 3), round(span.end, 3), span.frames
        got.fallbacks = notes + got.fallbacks
        return got
    raise VisualError(f"segmen {seg.id}: semua jenis visual gagal ({'; '.join(notes)})")


def build(script: Script, words: Words, cfg: ChannelConfig, run_dir: str, *, settings: dict | None = None,
          exclude: set[str] = frozenset(), log=print, cancel: threading.Event | None = None, progress=None,
          session=None, seed: int | None = None) -> Visuals:
    os.makedirs(os.path.join(run_dir, "clips"), exist_ok=True)
    keys = {k: config.secret(k, settings) for k in ("pexels", "pixabay", "cloudflare_account_id",
                                                     "cloudflare_token")}
    ctx = Ctx(run_dir=run_dir, cfg=cfg, keys=keys, cache_dir=os.path.join(os.path.dirname(run_dir), ".cache"),
              exclude=set(exclude), log=log, cancel=cancel, session=session, rng=random.Random(seed))
    plan = spans(script, words)
    out = []
    for i, span in enumerate(plan):
        seg = span.segment
        log(f"visual: segmen {seg.id} ({seg.visual_type}, {span.seconds:.1f} dtk)")
        out.append(build_segment(ctx, span))
        if progress:
            progress((i + 1) / len(plan))
    types = [v.type for v in out]
    return Visuals(segments=out, uses_ai_images="ai_image" in types, uses_stock="stock" in types, fps=FPS,
                   total_frames=plan[-1].f1)
