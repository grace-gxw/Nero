"""
Nero · Clip Maker - baca & tiru gaya editing dari clip referensi dengan AI lokal gratis (Ollama).

Dua lapis pembacaan:
  1. Pengukuran piksel (engine.analyze_reference_style): ukuran, posisi, jumlah kata, warna, judul,
     progress bar, tata letak, zoom. Angkanya tepat, jadi selalu didahulukan.
  2. AI yang bisa melihat gambar: hal yang sulit diukur, yaitu jenis font (dibandingkan dengan lembar
     contoh font Nero), cara menyorot kata (warna / kotak / karaoke), garis tepi, bayangan, kotak gelap,
     gaya judul, nuansa warna video, plus nama & ringkasan gaya. AI juga menjadi cadangan bila
     pengukuran tidak menemukan subtitle (mis. subtitle tanpa garis tepi).
Tanpa AI lokal, hasil pengukuran saja tetap dipakai.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import numpy as np

import gemini_ai
import local_ai

from . import engine as core

# urutan baris di lembar contoh font (nomor = posisi + 1); hanya font yang ada di komputer ini yang ikut
FONT_ORDER = ["impact", "anton", "bebas", "montserrat", "poppins", "arial", "segoe", "bahnschrift",
              "verdana", "georgia", "comic"]
COLORS_EN = {"white": "putih", "yellow": "kuning", "green": "hijau", "red": "merah", "blue": "biru",
             "pink": "pink", "orange": "oranye", "purple": "ungu", "teal": "toska"}
COLORS_ID = {v: k for k, v in COLORS_EN.items()}
COLOR_KEYS = ("caption_base", "caption_color", "progress_color", "title_text", "caption_stroke_color")
HIGHLIGHT = {"color": "warna", "box": "kotak", "karaoke": "karaoke", "none": "tanpa"}
OUTLINE = {"none": 0, "thin": 1, "normal": 2, "thick": 3}
TITLES = {"white_box": "putih", "yellow_box": "kuning", "black_box": "hitam", "red_box": "merah",
          "text_only": "tanpa"}
LAYOUTS = {"face_crop": "face", "two_people_split": "split", "full_video_blurred_background": "blur",
           "full_video_black_background": "hitam", "center_crop": "center", "original_vertical": "center"}
LOOKS = {"natural": "asli", "vivid": "cerah", "warm": "hangat", "cool": "dingin", "black_white": "hitamputih",
         "cinematic": "sinematik", "faded": "pudar"}
POSITIONS = {"top": 0.3, "upper_middle": 0.42, "middle": 0.5, "lower_middle": 0.62, "bottom": 0.75}
# hanya bila AI menonton videonya (Gemini): hal yang tidak terlihat dari gambar diam
ANIMS = {"pop": "pop", "bounce": "bounce", "slide_up": "naik", "none": "tanpa"}
ZOOMS = {"none": "tanpa", "slow": "halus", "punch": "hentak"}

# Petunjuk untuk model ditulis dalam bahasa Inggris: model kecil (2-8B) jauh lebih patuh dengan
# instruksi berbahasa Inggris. Nama & ringkasan tetap diminta dalam bahasa Indonesia.
SYSTEM = ("You are an expert short-form video editor (TikTok, Instagram Reels, YouTube Shorts). You look at frames "
          "of a vertical video and describe its editing style precisely. Describe only what you actually see. "
          "Answer with JSON only.")


def available_fonts() -> list[str]:
    out = []
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts") if os.name == "nt" else None
    for key in FONT_ORDER:
        file = core.CAPTION_FONTS[key][0]
        if os.path.isfile(os.path.join(core.FONTS_DIR, file)) or (win and os.path.isfile(os.path.join(win, file))):
            out.append(key)
    # font tambahan pengguna ikut dibandingkan, jadi tiruan bisa memakai font yang sama persis
    return out + [k for k, path in core.USER_FONTS.items() if os.path.isfile(path)]


def schema(n_fonts: int, video: bool = False) -> dict:
    """Bentuk jawaban AI. video=True menambah pertanyaan yang hanya bisa dijawab dengan menonton (animasi, zoom)."""
    s = lambda *vals: {"type": "string", "enum": list(vals)}  # noqa: E731
    props = {
        "has_subtitles": {"type": "boolean"},
        # tanpa pilihan "tidak ada": model kecil cenderung memilih jalan keluar itu daripada membandingkan
        "font_number": s(*[str(i) for i in range(1, n_fonts + 1)]),
        "uppercase": {"type": "boolean"},
        "words_per_screen": s("1", "2", "3", "4"),
        "subtitle_position": s(*POSITIONS),
        "text_color": s(*COLORS_EN),
        "highlight_style": s(*HIGHLIGHT),
        "highlight_color": s(*COLORS_EN, "none"),
        "outline": s(*OUTLINE),
        "shadow": {"type": "boolean"},
        "dark_box": {"type": "boolean"},
        "title_style": s("none", *TITLES),
        "layout": s(*LAYOUTS),
        "color_look": s(*LOOKS),
        "emoji": {"type": "boolean"},
        "caption_mode": s("standard", "kinetic"),
        "rough_texture": {"type": "boolean"},
        "broll": {"type": "boolean"},
        "name": {"type": "string"},
        "summary": {"type": "string"},
    }
    if video:
        props = {**{k: v for k, v in props.items() if k not in ("name", "summary")},
                 "subtitle_animation": s(*ANIMS), "zoom": s(*ZOOMS),
                 "name": props["name"], "summary": props["summary"]}
    return {"type": "object", "properties": props, "required": list(props)}


# --------------------------------------------------------------------------
# Gambar untuk AI
# --------------------------------------------------------------------------
def _grab_jpeg(path: str, t: float, vf: str) -> bytes | None:
    r = subprocess.run([core.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path,
                        "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **core._popen_kwargs())
    return r.stdout if r.returncode == 0 and len(r.stdout) > 500 else None


def pick_times(measure: dict, seconds: float, n: int = 3) -> list[float]:
    """Waktu frame yang dikirim ke AI: tersebar, diutamakan saat subtitle terlihat."""
    want = [seconds * k for k in (0.2, 0.5, 0.8)][:n]
    cap = measure.get("caption_times") or []
    if cap:
        want = [min(cap, key=lambda c: abs(c - w)) for w in want]
    return sorted(dict.fromkeys(round(max(0.3, w), 2) for w in want))


def frame_images(path: str, times: list[float]) -> list[bytes]:
    return [j for t in times if (j := _grab_jpeg(path, t, "scale=432:-2"))]


def contact_sheet(frames: list[bytes]) -> bytes | None:
    """Beberapa frame berdampingan dalam satu gambar. Ollama memperbesar/memperkecil SETIAP gambar ke
    ±1.000 token apa pun ukurannya, jadi satu gambar gabungan jauh lebih hemat konteks daripada tiga."""
    import cv2
    imgs = [im for j in frames if (im := cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)) is not None]
    if not imgs:
        return None
    h = min(im.shape[0] for im in imgs)
    cols = []
    for im in imgs:
        cols += [cv2.resize(im, (int(im.shape[1] * h / im.shape[0]), h)), np.full((h, 12, 3), 255, np.uint8)]
    ok, buf = cv2.imencode(".jpg", np.hstack(cols[:-1]), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else None


def caption_strip(path: str, band, times: list[float]) -> bytes | None:
    """Potongan area subtitle dari beberapa saat, ditumpuk ke bawah, supaya hurufnya terlihat jelas."""
    import cv2
    if not band:
        return None
    a, b = max(0.0, band[0] - 0.025), min(1.0, band[1] + 0.025)
    crops = []
    for t in times:
        j = _grab_jpeg(path, t, f"crop=iw:ih*{b - a:.4f}:0:ih*{a:.4f},scale=896:-2")
        if j:
            img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                crops.append(img)
    if not crops:
        return None
    w = crops[0].shape[1]
    sep = np.full((10, w, 3), 128, np.uint8)
    rows = []
    for c in crops:
        rows += [cv2.resize(c, (w, int(c.shape[0] * w / c.shape[1]))), sep]
    ok, buf = cv2.imencode(".jpg", np.vstack(rows[:-1]), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else None


_sheet_cache: dict[tuple, bytes] = {}
SHEET_TEXT = "Kata subtitle 2026"   # tulisan contoh di lembar font


def clean_name(v) -> str:
    """Nama gaya dari AI. Model kecil kadang menyalin tulisan di gambar (mis. contoh di lembar font): buang."""
    name = re.sub(r"\s+", " ", str(v or "")).strip(" .\"'")[:40]
    sheet = set(SHEET_TEXT.lower().split())
    return "" if not name or set(name.lower().split()) & sheet else name


_HL_WORD = {"kotak": "Kotak", "karaoke": "Karaoke", "tanpa": "Polos", "warna": "Sorot"}


def style_name(ai_name: str, style: dict) -> str:
    """Nama untuk Gaya saya. Nama dari AI dipakai bila memang menggambarkan tampilan (menyebut warna);
    model kecil kadang memberi nama acak, maka dibuat nama dari gayanya, mis. "Kotak Ungu Montserrat"."""
    colors = set(core.CAPTION_COLORS) | {"hitam", "emas", "neon"}
    if ai_name and set(ai_name.lower().split()) & colors:
        return ai_name
    if not style.get("captions", True):
        return ai_name
    hl = style.get("caption_highlight", "warna")
    color = str(style.get("caption_base" if hl == "tanpa" else "caption_color", "kuning"))
    color = "Warna" if color.startswith("#") else color.capitalize()
    key = str(style.get("caption_font") or "")
    font = _FONT_LABEL.get(key) or (key[5:].replace("-", " ").title() if key.startswith("user-") else "")
    first = "Kinetik" if style.get("caption_mode") == "kinetik" else _HL_WORD.get(hl, "Sorot")
    return " ".join(x for x in (first, color, font) if x)


_FONT_LABEL = {"impact": "Impact", "anton": "Anton", "bebas": "Bebas", "montserrat": "Montserrat",
               "poppins": "Poppins", "arial": "Arial Black", "segoe": "Segoe", "bahnschrift": "Bahnschrift",
               "verdana": "Verdana", "georgia": "Georgia", "comic": "Comic"}


def clean_summary(v) -> str:
    """Ringkasan dari AI: paling banyak dua kalimat berbeda (model kecil suka mengulang kalimat)."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", str(v or "").strip()):
        if s and s.lower() not in (x.lower() for x in out):
            out.append(s)
        if len(out) == 2:
            break
    return " ".join(out)[:400]


def font_sheet(fonts: list[str], upper: bool | None) -> bytes:
    """Lembar contoh font bernomor. Membandingkan jauh lebih mudah bagi AI kecil daripada menebak nama font."""
    import cv2
    from PIL import Image, ImageDraw
    key = (tuple(fonts), upper)
    if key in _sheet_cache:
        return _sheet_cache[key]
    text = SHEET_TEXT.upper() if upper is not False else SHEET_TEXT
    # Ollama mengubah setiap gambar ke ±1.000 token apa pun ukurannya; ukuran ini cukup jelas & cepat dibuat
    row_h, W = 80, 768
    img = Image.new("RGB", (W, row_h * len(fonts)), (92, 92, 92))
    d = ImageDraw.Draw(img)
    label = core._load_font(32)
    for i, k in enumerate(fonts):
        y = i * row_h
        if i:
            d.line((0, y, W, y), fill=(140, 140, 140), width=2)
        d.rectangle((0, y, 60, y + row_h), fill=(20, 20, 20))
        d.text((30, y + row_h / 2), str(i + 1), font=label, fill=(255, 255, 255), anchor="mm")
        f = core._font_by_key(k, 54)
        d.text((78, y + row_h / 2), text, font=f, fill=(255, 255, 255), anchor="lm",
               stroke_width=4, stroke_fill=(0, 0, 0))
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
    _sheet_cache[key] = buf.tobytes()
    return _sheet_cache[key]


# --------------------------------------------------------------------------
# Pertanyaan untuk AI
# --------------------------------------------------------------------------
_LAYOUT_EN = {"face": "the original wide video is cropped to follow the speaker's face",
              "split": "two people stacked (top half / bottom half)",
              "blur": "the full wide video in the middle with a blurred background above and below",
              "hitam": "the full wide video in the middle with black bars above and below",
              "center": "a centered crop, or the original video was already vertical"}


def _color_en(v) -> str:
    v = str(v or "putih")
    return COLORS_ID.get(v, f"the color {v}" if v.startswith("#") else "white")


def facts(style: dict, measure: dict) -> str:
    """Hasil pengukuran piksel dalam kalimat, supaya AI tidak menebak ulang hal yang sudah pasti."""
    out = []
    if style.get("captions"):
        words = style.get("caption_words")
        case = {True: "UPPERCASE", False: "normal case"}.get(style.get("caption_upper"), "case unclear")
        if style.get("caption_mode") == "kinetik":
            out.append(f"- Kinetic text found around {round(style.get('caption_pos', 0.5) * 100)}% from the top: "
                       f"words stack into several lines of different sizes, {case}.")
        else:
            out.append(f"- Spoken-word subtitles found around {round(style.get('caption_pos', 0.68) * 100)}% from "
                       f"the top, at most {words} word(s) at a time, {case}.")
        if style.get("caption_base") == style.get("caption_color"):
            out.append(f"- Subtitle letters are mostly {_color_en(style.get('caption_base'))}.")
        else:
            out.append(f"- Subtitle text is mostly {_color_en(style.get('caption_base'))}; "
                       f"{_color_en(style.get('caption_color'))} is used for emphasis.")
    else:
        out.append("- The pixel scan did NOT find outlined subtitles (they may be missing, or have no outline).")
    if style.get("title_on"):
        secs = style.get("title_secs")
        out.append("- A fixed headline is shown near the top " + ("for the whole video." if not secs else
                                                                    f"for the first {secs} seconds."))
    else:
        out.append("- No fixed headline was found at the top.")
    if style.get("layout"):
        out.append(f"- Layout: {_LAYOUT_EN[style['layout']]}.")
    if style.get("broll"):
        out.append("- The edit cuts away from the speaker to other footage on a black background.")
    if "progress_bar" in style:
        out.append(f"- Progress bar: {'yes' if style.get('progress_bar') else 'no'}.")
    m = measure.get("motion") or {}
    if m:
        out.append(f"- Camera: {m.get('jumps_per_min', 0)} sudden zoom jumps and {m.get('cuts_per_min', 0)} scene "
                   "cuts per minute.")
    return "\n".join(out)


def prompt(style: dict, measure: dict, inputs: list[str], n_fonts: int, video: bool = False,
           n_refs: int = 1) -> str:
    """inputs: keterangan tiap gambar sesuai urutannya (diberi nomor di sini). video=True: video ditonton dulu."""
    lines = []
    if video:
        lines.append(f"- The video{'s' if n_refs > 1 else ''}: the first seconds of "
                     f"{'each reference clip' if n_refs > 1 else 'the clip'} (watch how the subtitles appear and "
                     "how the camera moves).")
    lines += [f"- Image {k}: {desc}" for k, desc in enumerate(inputs, 1)]
    lines.append(f"- Image {len(inputs) + 1}: a font sample sheet with {n_fonts} numbered rows. Every row writes "
                 "the same words in a different font.")
    many = (f"There are {n_refs} references that share ONE editing style; describe that common style.\n\n"
            if n_refs > 1 else "")
    return f"""Analyze the editing style of this short video so it can be copied exactly.

{many}{chr(10).join(lines)}

Measurements already made by pixel analysis (trust them):
{facts(style, measure)}

Fill every field:
- has_subtitles: true if there are captions of the spoken words that change as the person talks. A headline that stays the same is NOT a subtitle.
- font_number: the row of the font sheet whose letters look MOST like the subtitle letters. Look at the subtitle close-up, then at each row of the font sheet, and compare letter width (narrow/condensed vs wide), stroke thickness, roundness and serifs. Always pick the closest row.
- uppercase: true if subtitles are written in CAPITAL letters.
- words_per_screen: the largest number of subtitle words usually visible at once.
- subtitle_position: where the subtitles sit vertically.
- text_color: the color of most subtitle letters.
- highlight_style: how the currently spoken word is emphasized. "color" = one word has a different letter color; "box" = one word has a solid colored rectangle behind it; "karaoke" = the words already spoken are colored from left to right; "none" = all words look the same.
- highlight_color: the color of that emphasized word or box ("none" if highlight_style is "none").
- outline: thickness of the dark outline around subtitle letters.
- shadow: true if the letters have a drop shadow.
- dark_box: true if the whole subtitle sits on a dark semi-transparent rectangle.
- title_style: the fixed headline near the top: its box color, "text_only" if it has no box, "none" if there is no headline.
- layout: how the footage fills the vertical frame.
- color_look: the overall color grading of the footage (not the text).
- emoji: true if emoji or stickers appear next to the subtitles.
- caption_mode: "kinetic" if words pile up one by one into a stacked block of several lines with clearly different letter sizes (big key words, small connector words, placed left/right/center); "standard" if 1-4 words show on one or two even lines and are replaced as the person talks.
- rough_texture: true if the letters look rough, worn or grunge (speckles, scratches), not clean.
- broll: true if the edit cuts away from the speaker to other footage (movie, anime, stock clips) while the voice continues.{VIDEO_FIELDS if video else ""}
- name: a short catchy name for this style in Indonesian, 2-4 words, describing its look (for example "Podcast Kuning Tegas" or "Kotak Ungu Santai"). Do not copy any text written in the images.
- summary: 1-2 short sentences in Indonesian that explain this editing style to a beginner. Describe the look, not what the people say."""


VIDEO_FIELDS = """
- subtitle_animation: how each new group of subtitle words appears. "pop" = quickly grows a little into place; "bounce" = grows bigger than normal, then settles back; "slide_up" = rises from below while fading in; "none" = appears instantly without motion.
- zoom: camera zoom used by the editor. "punch" = sudden jumps between a normal and a closer framing (like jump cuts); "slow" = smooth slow zoom in/out; "none" = no editing zoom."""


# --------------------------------------------------------------------------
# Gabungkan pengukuran + AI
# --------------------------------------------------------------------------
def _look_ok(look: str, color: dict) -> bool:
    """Nuansa warna dari AI hanya dipakai bila angka warnanya mendukung (AI kecil sering menebak)."""
    sat, warm, bright = color.get("sat", 60), color.get("warm", 0), color.get("bright", 120)
    # kulit & lampu ruangan memang hangat, jadi "hangat" baru dipercaya bila jelas sekali
    return {"asli": True, "cerah": sat >= 95, "hangat": warm >= 26, "dingin": warm <= -2,
            "hitamputih": sat < 28, "sinematik": sat < 120, "pudar": sat < 80 and bright > 70}.get(look, False)


def merge(measured: dict, ai: dict | None, measure: dict, fonts: list[str]) -> tuple[dict, dict]:
    """Hasil: (gaya, sumber per kunci: 'ukur' = pengukuran piksel, 'ai' = penilaian AI)."""
    style = dict(measured)
    src = {k: "ukur" for k in style}
    if not ai:
        return style, src

    def put(k, v):
        if v is not None:
            style[k], src[k] = v, "ai"

    try:
        n = int(ai.get("font_number") or 0)
    except (TypeError, ValueError):
        n = 0
    font = fonts[n - 1] if 1 <= n <= len(fonts) else None
    hl = HIGHLIGHT.get(ai.get("highlight_style"))
    hl_color = COLORS_EN.get(ai.get("highlight_color"))

    def caption_look():
        put("caption_font", font)
        if "caption_highlight" not in measured:   # kotak sorot terukur dari piksel lebih pasti daripada AI
            put("caption_highlight", hl)
        put("caption_stroke", OUTLINE.get(ai.get("outline")))
        put("caption_shadow", bool(ai.get("shadow")))
        put("caption_box", bool(ai.get("dark_box")))

    if measured.get("captions"):
        caption_look()
        # pengukuran hanya menemukan huruf terang yang menempel piksel gelap: pasti ada garis tepi,
        # bayangan, atau kotak gelap. Bila AI bilang tidak ada semuanya, anggap garis tepi tipis.
        if style.get("caption_stroke") == 0 and not style.get("caption_shadow") and not style.get("caption_box"):
            put("caption_stroke", 1)
        if "caption_upper" not in style:
            put("caption_upper", bool(ai.get("uppercase")))
        # kotak sorot sering tidak terbaca sebagai warna huruf oleh pengukuran
        if hl in ("kotak", "karaoke") and hl_color and style.get("caption_color") == style.get("caption_base"):
            put("caption_color", hl_color)
    elif ai.get("has_subtitles"):
        # pengukuran tidak menemukan subtitle (mis. tanpa garis tepi): pakai pembacaan AI seluruhnya
        put("captions", True)
        caption_look()
        put("caption_upper", bool(ai.get("uppercase")))
        put("caption_pos", POSITIONS.get(ai.get("subtitle_position")))
        try:
            put("caption_words", max(1, min(4, int(ai.get("words_per_screen") or 3))))
        except (TypeError, ValueError):
            pass
        put("caption_base", COLORS_EN.get(ai.get("text_color")))
        put("caption_color", hl_color or COLORS_EN.get(ai.get("text_color")))

    title = ai.get("title_style")
    if style.get("title_on") and "title_bg" not in style and title in TITLES:
        put("title_bg", TITLES[title])

    look = LOOKS.get(ai.get("color_look"))
    if "filter" not in style and look and _look_ok(look, measure.get("color") or {}):
        put("filter", look)
    # teks kinetik: AI dipercaya bila pengukuran tidak membacanya atau sinyal pikselnya juga mendukung
    mode = {"kinetic": "kinetik", "standard": "biasa"}.get(ai.get("caption_mode"))
    if mode and style.get("captions"):
        if "caption_mode" not in measured or (mode == "kinetik" and (measure.get("kinetic") or 0) >= 0.2):
            put("caption_mode", mode)
    if "caption_texture" not in measured and isinstance(ai.get("rough_texture"), bool):
        put("caption_texture", ai["rough_texture"])
    if "broll" not in measured and ai.get("broll"):
        put("broll", True)
        put("broll_fit", "hitam")
    # dari menonton video (Gemini): animasi muncul subtitle; zoom hanya bila gerak kamera tidak terukur
    anim = ANIMS.get(ai.get("subtitle_animation"))
    if anim and style.get("captions"):
        put("caption_anim", anim)
    zoom = ZOOMS.get(ai.get("zoom"))
    if zoom and "zoom" not in style:
        put("zoom", zoom)

    layout = LAYOUTS.get(ai.get("layout"))
    # "potong tengah" adalah tebakan cadangan pengukuran (mis. latar blur tidak terbaca karena zoom,
    # atau wajah tidak terdeteksi di video gelap), jadi penilaian AI boleh menggantinya
    if "layout" not in style or (style["layout"] == "center" and layout in ("blur", "hitam", "face")):
        put("layout", layout)
    return style, src


GEMINI_SECONDS = 20   # bagian awal clip yang ditonton Gemini (cukup untuk melihat pola subtitle & zoom)
GEMINI_FPS = 4.0      # frame per detik yang dilihat Gemini: cukup untuk menangkap animasi muncul subtitle


def video_proxy(path: str, seconds: float, limit: float = 0) -> bytes | None:
    """Salinan kecil clip untuk dikirim ke Gemini: 480 px, tanpa suara, paling lama GEMINI_SECONDS."""
    import tempfile
    out = os.path.join(tempfile.gettempdir(), f"nero_gaya_{os.getpid()}_{id(path)}.mp4")
    try:
        core.run_ffmpeg(["-i", path, "-t", f"{min(seconds, limit or GEMINI_SECONDS):.1f}", "-vf", "scale=480:-2,fps=15", "-an",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", out])
        with open(out, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


MAX_REFS = 5   # referensi (video + kumpulan gambar) per pembacaan


def image_jpegs(paths: list[str], max_side: int = 768) -> list[bytes]:
    """Gambar referensi sebagai JPEG kecil untuk AI."""
    import cv2
    out = []
    for p in paths:
        im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            continue
        k = min(1.0, max_side / max(im.shape[:2]))
        if k < 1:
            im = cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok:
            out.append(buf.tobytes())
    return out


def image_strip(paths: list[str], band) -> bytes | None:
    """Potongan area subtitle dari gambar referensi (seperti caption_strip untuk video)."""
    import cv2
    if not band:
        return None
    a, b = max(0.0, band[0] - 0.025), min(1.0, band[1] + 0.025)
    crops = []
    for p in paths[:3]:
        im = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        if im is not None:
            h = im.shape[0]
            crop = im[int(a * h):max(int(a * h) + 2, int(b * h))]
            crops.append(cv2.resize(crop, (896, max(2, int(crop.shape[0] * 896 / crop.shape[1])))))
    if not crops:
        return None
    sep = np.full((10, 896, 3), 128, np.uint8)
    rows = []
    for c in crops:
        rows += [c, sep]
    ok, buf = cv2.imencode(".jpg", np.vstack(rows[:-1]), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else None


def combine(results: list[dict]) -> tuple[dict, list[str]]:
    """Gabungkan pembacaan beberapa referensi menjadi satu gaya: angka = nilai tengah (median), pilihan = suara
    terbanyak, warna = median RGB. Kunci yang terbaca di kurang dari separuh referensi tidak dipakai."""
    from collections import Counter
    styles = [r["style"] for r in results]
    n = len(styles)
    out, differ = {}, []
    for k in dict.fromkeys(k for st in styles for k in st):
        vals = [st[k] for st in styles if k in st]
        if len(vals) * 2 < n:
            continue
        if len({json.dumps(v) for v in vals}) > 1:
            differ.append(k)
        if k in COLOR_KEYS or (k == "title_bg" and any(str(v).startswith("#") for v in vals)):
            rgbs = [c for v in vals if (c := core.color_rgb(v, None)) is not None]
            if k == "title_bg":
                rgbs += [core._TITLE_PALETTE[v] for v in vals if v in core._TITLE_PALETTE and v not in core.CAPTION_COLORS]
            if rgbs:
                pal, tol = (core._TITLE_PALETTE, 60) if k == "title_bg" else (core.CAPTION_COLORS, 45)
                out[k] = core._rgb_name(np.median(np.array(rgbs, float), axis=0), pal, tol)
                continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            m = float(np.median(vals))
            out[k] = int(round(m)) if all(isinstance(v, int) for v in vals) else round(m, 3)
        else:
            out[k] = json.loads(Counter(json.dumps(v) for v in vals).most_common(1)[0][0])
    notes = [f"Gaya digabung dari {n} referensi: angka diambil nilai tengahnya, pilihan diambil yang paling sering."]
    if differ:
        notes.append("Berbeda antar-referensi: " + ", ".join(differ[:8]) + (" ..." if len(differ) > 8 else "") + ".")
    return out, notes


def analyze(paths, *, images: list[str] = (), ai: dict | None = None, use_ai: bool = True,
            stage=lambda label, x: None, alive=lambda: None) -> dict:
    """Baca gaya dari satu atau beberapa clip referensi dan/atau gambar (screenshot).
    ai: pengaturan umum Nero (ai_provider, gemini_key, gemini_model, local_ai_url, local_ai_model, local_ai_key).
    Gemini dicoba lebih dulu bila dipilih (menonton sampai 3 clip sekaligus); bila gagal, AI lokal; bila itu pun
    tidak ada, pengukuran piksel saja. Hasil: {style, sources, notes, seconds, refs, ai | None}."""
    ai = ai or {}
    paths = [paths] if isinstance(paths, str) else [p for p in (paths or []) if p]
    images = [p for p in (images or []) if p]
    if not paths and not images:
        raise RuntimeError("Tidak ada clip atau gambar referensi.")
    n_refs = len(paths) + (1 if images else 0)
    results = []
    for i, p in enumerate(paths, 1):
        label = "Mengukur subtitle, judul, tata letak & zoom..." if n_refs == 1 else \
            f"Mengukur clip {i}/{len(paths)}: subtitle, judul, tata letak & zoom..."
        stage(label, 0.2 + 0.2 * (i - 1) / n_refs)
        results.append(core.analyze_reference_style(p))
        alive()
    if images:
        stage(f"Mengukur {len(images)} gambar referensi...", 0.38)
        results.append(core.analyze_reference_images(images))
        alive()
    if len(results) > 1:
        style, notes = combine(results)
    else:
        style, notes = dict(results[0]["style"]), []
    for r in results:
        notes += [x for x in r["notes"] if x not in notes]
    # potongan subtitle & angka warna diambil dari referensi pertama yang subtitlenya terbaca
    k_first = next((k for k, r in enumerate(results) if r["measure"].get("caption_band")), 0)
    measure = results[k_first]["measure"]
    first_path = paths[k_first] if k_first < len(paths) else None
    out = {"style": style, "sources": {k: "ukur" for k in style}, "notes": notes,
           "seconds": round(sum(r["seconds"] for r in results), 1), "refs": n_refs, "ai": None}
    if not use_ai:
        notes.append("Dibaca tanpa AI: jenis font & cara menyorot kata tidak ditiru.")
        return out

    stage("Menyiapkan gambar untuk AI...", 0.42)
    strip = None
    if style.get("captions") and measure.get("caption_band"):
        strip = caption_strip(first_path, measure["caption_band"], pick_times(measure, results[k_first]["seconds"])) \
            if first_path else image_strip(images, measure["caption_band"])
    fonts = available_fonts()
    sheet = font_sheet(fonts, style.get("caption_upper"))
    shots = image_jpegs(images[:4]) if images else []
    alive()

    answer, used = None, None
    if ai.get("ai_provider") == "gemini":
        model = ai.get("gemini_model") or gemini_ai.DEFAULT_MODEL
        stage(f"Gemini ({model}) sedang menonton {'clip' if n_refs == 1 else f'{n_refs} referensi'}...", 0.5)
        try:
            secs = GEMINI_SECONDS if len(paths) <= 1 else 12   # beberapa clip: tiap clip lebih pendek
            videos = [v for p, r in zip(paths[:3], results) if (v := video_proxy(p, r["seconds"], secs))]
            if not videos and not shots:
                raise local_ai.LocalAIError("Clip tidak bisa disiapkan untuk Gemini.")
            alive()
            inputs = [f"screenshot {k} of the reference edit" for k in range(1, len(shots) + 1)]
            if strip:
                inputs.append("close-up strips of the subtitle area from several moments (stacked).")
            answer = gemini_ai.chat_json(
                prompt(style, measure, inputs, len(fonts), video=bool(videos), n_refs=n_refs),
                schema(len(fonts), video=bool(videos)), key=ai.get("gemini_key", ""), model=model, videos=videos,
                video_fps=GEMINI_FPS, images=shots + ([strip] if strip else []) + [sheet], system=SYSTEM,
                alive=alive)
            used = {"provider": "gemini", "model": model}
        except local_ai.LocalAIError as e:
            notes.append(f"{e} Dicoba dengan AI lokal.")
    alive()

    if answer is None:
        st = local_ai.status(ai.get("local_ai_url"), ai.get("local_ai_key"))
        model = local_ai.pick_model(st, ai.get("local_ai_model")) if st["ok"] else None
        if not st["ok"] or not model:
            why = st["error"] if not st["ok"] else "Belum ada model AI yang bisa melihat gambar di Ollama."
            notes.append(f"{why} Gaya dibaca tanpa AI: jenis font & cara menyorot kata tidak ditiru.")
            out["ai_missing"] = True
            return out
        if first_path or paths:
            src = first_path or paths[0]
            frames = frame_images(src, pick_times(measure if first_path else {}, results[0]["seconds"]))
            together = contact_sheet(frames)
            desc = (f"{len(frames)} full frames from the video side by side (separated by white bars), taken at "
                    "different moments from left to right." if len(frames) > 1 else "a full frame from the video.")
        else:
            together = contact_sheet(image_jpegs(images[:3], 540))
            desc = "screenshots of the reference edit side by side (separated by white bars)."
        images_ai = ([together] if together else []) + ([strip] if strip else []) + [sheet]
        inputs = ([desc] if together else []) + (["close-up strips of the subtitle area (stacked)."] if strip else [])
        label = f"AI lokal ({model}) sedang melihat clip... Pertama kali bisa 1-3 menit."
        stage(label, 0.55)
        try:
            answer = local_ai.chat_json(prompt(style, measure, inputs, len(fonts), n_refs=n_refs), schema(len(fonts)),
                                        images_ai, url=ai.get("local_ai_url"), model=model,
                                        key=ai.get("local_ai_key"), system=SYSTEM, alive=alive,
                                        on_token=lambda n: stage(label, min(0.95, 0.55 + n / 900)))
            used = {"provider": "lokal", "model": model}
        except local_ai.LocalAIError as e:
            notes.append(f"{e} Gaya dibaca tanpa AI.")
            return out
    alive()
    merged, sources = merge(style, answer, measure, fonts)
    out.update(style=merged, sources=sources,
               ai={**used, "name": clean_name(answer.get("name")), "summary": clean_summary(answer.get("summary"))})
    if answer.get("emoji"):
        notes.append("AI melihat emoji/stiker di samping subtitle; Nero belum bisa menambahkannya.")
    if answer.get("has_subtitles") and not merged.get("captions"):
        notes.append("AI melihat subtitle tetapi tidak bisa diukur; periksa pratinjau.")
    return out
