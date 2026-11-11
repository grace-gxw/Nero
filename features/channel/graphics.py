"""
Nero · Channel Otomatis - motion graphic buatan sendiri (SPEC.md 6.8c).

Frame digambar dengan Pillow, lalu di-encode ffmpeg (media.encode_frames). Template:
  big_number   angka besar yang menghitung naik
  bullet_list  poin muncul satu per satu ("a | b | c")
  versus       dua kolom perbandingan ("kiri | kanan")
  quote_card   kutipan/fakta singkat
  title_card   judul di tengah layar
Juga lapisan teks layar (`on_screen_text`) dan label "ILUSTRASI" yang ditempel di atas semua jenis visual.

Tata letak: grafik ada di bagian tengah-atas (17–52% tinggi layar), teks layar satu baris di atasnya. Subtitle berada di ±55–65% tinggi layar,
sedangkan 20% bawah dan sisi kanan bagian bawah tertutup tombol Shorts/TikTok.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from features.clip import engine

from .media import FPS, H, W


@dataclass(frozen=True)
class Theme:
    """Palet & font semua grafik channel (satu tempat supaya konsisten)."""
    bg_top: tuple = (12, 14, 24)
    bg_bottom: tuple = (34, 40, 62)
    glow: tuple = (70, 86, 140)
    text: tuple = (255, 255, 255)
    muted: tuple = (190, 198, 220)
    accent: tuple = (255, 206, 0)
    card: tuple = (255, 255, 255, 26)
    font_head: str = "montserrat"     # kunci engine.CAPTION_FONTS (font OFL di fonts/)
    font_number: str = "anton"


THEME = Theme()

# area grafik: x0, y0, x1, y1
AREA = (80, int(H * 0.17), W - 80, int(H * 0.52))
# lapisan teks layar & label ilustrasi
OSD_Y = int(H * 0.085)
LABEL_Y = int(H * 0.035)

TEMPLATES = ("big_number", "bullet_list", "versus", "quote_card", "title_card")


# --------------------------------------------------------------------------
# Dasar
# --------------------------------------------------------------------------
def ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease_back(x: float) -> float:
    """Sedikit melewati 1 lalu kembali (efek 'pop')."""
    x = min(1.0, max(0.0, x))
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def font(key: str, size: int):
    return _font(key, max(8, int(size)))


@lru_cache(maxsize=256)
def _font(key: str, size: int):
    return engine._font_by_key(key, size)


@lru_cache(maxsize=4)
def background(theme: Theme = THEME) -> Image.Image:
    """Gradien vertikal halus dengan cahaya lembut di kiri atas."""
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    top, bot = np.array(theme.bg_top, np.float32), np.array(theme.bg_bottom, np.float32)
    img = top * (1 - y)[..., None] + bot * y[..., None]
    img = np.broadcast_to(img, (H, W, 3)).copy()
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W * 0.2) / (W * 0.9)) ** 2 + ((yy - H * 0.22) / (H * 0.45)) ** 2)
    glow = np.clip(1 - d, 0, 1) ** 2 * 0.55
    img = img * (1 - glow[..., None]) + np.array(theme.glow, np.float32) * glow[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def blurred_background(img: Image.Image, theme: Theme = THEME) -> Image.Image:
    """Gambar (mis. stok/AI) yang diburamkan & digelapkan sebagai latar grafik."""
    img = img.convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((max(W, round(img.width * s)), max(H, round(img.height * s))), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((x, y, x + W, y + H)).filter(ImageFilter.GaussianBlur(28))
    return Image.blend(img, background(theme), 0.55)


def wrap(text: str, fnt, max_w: int) -> list[str]:
    d = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lines, cur = [], ""
    for w in text.split():
        test = f"{cur} {w}".strip()
        if not cur or d.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text: str, key: str, max_w: int, max_h: int, size: int, *, min_size: int = 36, max_lines: int = 4,
        gap: float = 0.16):
    """Ukuran font terbesar (≤ size) yang membuat teks muat. Hasil: (font, baris, tinggi baris)."""
    text = engine._clean_text(" ".join(str(text).split()))
    while True:
        f = font(key, size)
        lines = wrap(text, f, max_w)
        lh = int(size * (1 + gap))
        d = ImageDraw.Draw(Image.new("RGB", (4, 4)))
        widest = max((d.textlength(x, font=f) for x in lines), default=0)
        if (len(lines) <= max_lines and lh * len(lines) <= max_h and widest <= max_w) or size <= min_size:
            return f, lines[:max_lines], lh
        size = max(min_size, int(size * 0.9))


def text_block(lines: list[str], fnt, lh: int, fill, *, align: str = "center", shadow: bool = True,
               stroke: int = 0, stroke_fill=(0, 0, 0)) -> Image.Image:
    """Gambar RGBA berisi baris teks (sebesar isinya)."""
    d0 = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    widths = [d0.textlength(x, font=fnt) for x in lines] or [0]
    pad = 12 + stroke * 2
    w = int(max(widths)) + pad * 2
    h = lh * max(1, len(lines)) + pad * 2 + int(lh * 0.2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ds = ImageDraw.Draw(sh)
        for i, line in enumerate(lines):
            x = _x(align, widths[i], w, pad)
            ds.text((x + 4, pad + i * lh + 6), line, font=fnt, fill=(0, 0, 0, 150), stroke_width=stroke,
                    stroke_fill=(0, 0, 0, 150))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((_x(align, widths[i], w, pad), pad + i * lh), line, font=fnt, fill=fill, stroke_width=stroke,
               stroke_fill=stroke_fill)
    return img


def _x(align, line_w, box_w, pad):
    if align == "left":
        return pad
    return (box_w - line_w) / 2


def paste(frame: Image.Image, layer: Image.Image, cx: float, cy: float, *, alpha: float = 1.0,
          scale: float = 1.0, anchor: str = "center") -> None:
    """Tempel lapisan RGBA ke frame RGB (in place) dengan transparansi & skala; posisi = titik tengah (atau kiri)."""
    if alpha <= 0.003 or scale <= 0.01:
        return
    if abs(scale - 1) > 0.004:
        layer = layer.resize((max(1, round(layer.width * scale)), max(1, round(layer.height * scale))),
                             Image.BICUBIC)
    if alpha < 0.997:
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer = layer.copy()
        layer.putalpha(a)
    x = cx - layer.width / 2 if anchor == "center" else cx
    frame.paste(layer, (round(x), round(cy - layer.height / 2)), layer)


# --------------------------------------------------------------------------
# Angka
# --------------------------------------------------------------------------
@dataclass
class NumberSpec:
    prefix: str
    value: float
    decimals: int
    thousands: bool
    unit: str
    label: str
    animate: bool


_NUM = re.compile(r"(?P<sign>[-−+]?)(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)")
_UNIT_HINT = re.compile(r"[%°²³$€£¥]|^(km|m|cm|mm|kg|g|mg|ton|kal|kkal|jt|rb|x|kali)$", re.IGNORECASE)


def parse_number(content: str) -> NumberSpec | None:
    """'-272 °C' -> angka -272, satuan '°C'. '1.200 km per jam' -> 1200, 'km', label 'per jam'. Tanpa angka: None."""
    text = " ".join(str(content).split())
    m = _NUM.search(text)
    if not m:
        return None
    raw = m.group("num")
    thousands = bool(re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", raw))
    norm = raw.replace(".", "").replace(",", ".") if thousands else raw.replace(",", ".")
    if not thousands and "." in raw and re.fullmatch(r"\d+\.\d{3}", raw):   # 10.000 tanpa koma = ribuan
        thousands, norm = True, raw.replace(".", "")
    try:
        value = float(norm)
    except ValueError:
        return None
    decimals = len(norm.split(".")[1]) if "." in norm else 0
    if m.group("sign") in ("-", "−"):
        value = -value
    prefix = text[:m.start()].strip()
    rest = text[m.end():].strip()
    unit, label = "", rest
    if rest:
        first, _, after = rest.partition(" ")
        if _UNIT_HINT.search(first) or len(first) <= 2:
            unit, label = first, after.strip()
    # tahun (mis. 2007) tidak dihitung naik: terlihat aneh
    year = decimals == 0 and not thousands and 1000 <= abs(value) <= 2100 and not unit
    return NumberSpec(prefix, value, decimals, thousands or abs(value) >= 10000, unit, label, animate=not year)


def format_number(value: float, decimals: int, thousands: bool) -> str:
    """Format Indonesia: titik pemisah ribuan, koma desimal."""
    s = f"{abs(value):,.{decimals}f}" if thousands else f"{abs(value):.{decimals}f}"
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return ("-" if value < 0 else "") + s


# --------------------------------------------------------------------------
# Scene: fungsi frame(i) -> Image RGB
# --------------------------------------------------------------------------
class Scene:
    def __init__(self, n_frames: int, theme: Theme = THEME, bg: Image.Image | None = None,
                 overlay: Image.Image | None = None):
        self.n = max(1, n_frames)
        self.theme = theme
        self.bg = (bg or background(theme)).convert("RGB")
        self.overlay = overlay

    def t(self, i: int) -> float:
        return i / FPS

    def draw(self, frame: Image.Image, t: float) -> None:   # diisi template
        pass

    def frame(self, i: int) -> np.ndarray:
        img = self.bg.copy()
        self.draw(img, self.t(i))
        if self.overlay is not None:
            img.paste(self.overlay, (0, 0), self.overlay)
        return np.asarray(img)


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def _area() -> Box:
    return Box(*AREA)


class TitleCard(Scene):
    def __init__(self, text: str, n_frames: int, **kw):
        super().__init__(n_frames, **kw)
        a = _area()
        f, lines, lh = fit(text.upper(), self.theme.font_head, int(a.w), int(a.h * 0.8), 110, max_lines=4)
        self.layer = text_block(lines, f, lh, self.theme.text)
        self.cx, self.cy = a.cx, a.cy
        self.bar_w = min(a.w * 0.5, self.layer.width * 0.6)
        self.bar_y = a.cy + self.layer.height / 2 + 24

    def draw(self, frame, t):
        a = ease_out(t / 0.45)
        paste(frame, self.layer, self.cx, self.cy, alpha=a, scale=0.9 + 0.1 * ease_back(t / 0.5))
        w = self.bar_w * ease_out((t - 0.2) / 0.5)
        if w > 2:
            ImageDraw.Draw(frame, "RGBA").rounded_rectangle(
                (self.cx - w / 2, self.bar_y, self.cx + w / 2, self.bar_y + 12), radius=6, fill=self.theme.accent)


class BigNumber(Scene):
    COUNT_SECONDS = 1.3

    def __init__(self, content: str, n_frames: int, **kw):
        super().__init__(n_frames, **kw)
        spec = parse_number(content)
        if spec is None:
            raise ValueError("tidak ada angka")
        self.spec = spec
        a = _area()
        aw, ah = a.w, a.h
        self.cx, self.cy, self.aw = a.cx, a.cy, aw
        final = self._text(spec.value)
        size = 330
        while size > 120:   # ukuran angka tetap (dihitung dari nilai akhir) supaya tidak "melompat"
            f = font(self.theme.font_number, size)
            if ImageDraw.Draw(Image.new("RGB", (4, 4))).textlength(final, font=f) <= aw:
                break
            size = int(size * 0.92)
        self.num_font = f
        self.size = size
        self.label = None
        if spec.label or spec.prefix:
            text = " ".join(x for x in (spec.prefix, spec.label) if x).upper()
            lf, lines, lh = fit(text, self.theme.font_head, int(aw), int(ah * 0.32), 64, max_lines=2)
            self.label = text_block(lines, lf, lh, self.theme.muted)
        self._cache: dict[str, Image.Image] = {}

    def _text(self, v: float) -> str:
        s = self.spec
        num = format_number(v, s.decimals, s.thousands)
        return f"{num} {s.unit}".strip() if s.unit and not s.unit.startswith("%") else num + s.unit

    def _layer(self, text):
        if text not in self._cache:
            self._cache[text] = text_block([text], self.num_font, int(self.size * 1.08), self.theme.accent)
        return self._cache[text]

    def draw(self, frame, t):
        s = self.spec
        p = ease_out(t / self.COUNT_SECONDS) if s.animate else 1.0
        v = s.value * p
        if s.decimals == 0:
            v = math.floor(abs(v) + 1e-9) * (1 if s.value >= 0 else -1)
        layer = self._layer(self._text(v))
        ny = self.cy - (40 if self.label is not None else 0)
        paste(frame, layer, self.cx, ny, alpha=ease_out(t / 0.3), scale=0.85 + 0.15 * ease_back(t / 0.45))
        if self.label is not None:
            la = ease_out((t - 0.35) / 0.4)
            paste(frame, self.label, self.cx, ny + layer.height / 2 + self.label.height / 2 - 10 + 30 * (1 - la),
                  alpha=la)


class BulletList(Scene):
    def __init__(self, content: str, n_frames: int, **kw):
        super().__init__(n_frames, **kw)
        points = [p.strip(" -•\t") for p in str(content).split("|") if p.strip(" -•\t")][:4]
        if len(points) < 2:
            raise ValueError("bullet_list butuh minimal 2 poin")
        a = _area()
        aw, cy = a.w, a.cy
        slot = a.h / len(points)
        size = 84 if len(points) <= 3 else 70
        fonts = [fit(p, self.theme.font_head, int(aw - 90), int(slot * 0.8), size, max_lines=2) for p in points]
        size_min = min(int(f[2] / 1.16) for f in fonts)
        self.items = []
        for p in points:
            f, lines, lh = fit(p, self.theme.font_head, int(aw - 90), int(slot * 0.8), size_min, max_lines=2)
            self.items.append(text_block(lines, f, lh, self.theme.text, align="left"))
        total = sum(it.height for it in self.items) + 28 * (len(self.items) - 1)
        y = cy - total / 2
        self.pos = []
        for it in self.items:
            self.pos.append(y + it.height / 2)
            y += it.height + 28
        self.x0 = a.x0
        dur = self.n / FPS
        span = max(0.6, min(dur * 0.65, 0.9 * len(points)))
        self.starts = [0.15 + span * k / len(points) for k in range(len(points))]

    def draw(self, frame, t):
        d = ImageDraw.Draw(frame, "RGBA")
        for it, yc, st in zip(self.items, self.pos, self.starts, strict=True):
            a = ease_out((t - st) / 0.35)
            if a <= 0:
                continue
            dx = -50 * (1 - a)
            r = 15
            bx, by = self.x0 + 22 + dx, yc
            d.ellipse((bx - r, by - r, bx + r, by + r), fill=self.theme.accent + (int(255 * a),))
            paste(frame, it, self.x0 + 62 + dx, yc, alpha=a, anchor="left")


class Versus(Scene):
    def __init__(self, content: str, n_frames: int, **kw):
        super().__init__(n_frames, **kw)
        parts = [p.strip() for p in str(content).split("|") if p.strip()]
        if len(parts) < 2:
            parts = [p.strip() for p in re.split(r"\s+(?:vs\.?|versus|lawan)\s+", str(content), flags=re.IGNORECASE)
                     if p.strip()]
        if len(parts) < 2:
            raise ValueError("versus butuh 'kiri | kanan'")
        a = _area()
        self.cx, self.cy = a.cx, a.cy
        col_w = int((a.w - 110) / 2)
        self.col_w, self.col_h = col_w, int(a.h * 0.78)
        self.cols = []
        for p in parts[:2]:
            f, lines, lh = fit(p.upper(), self.theme.font_head, col_w - 40, int(self.col_h * 0.85), 72, max_lines=5)
            self.cols.append(text_block(lines, f, lh, self.theme.text))
        self.lx = a.x0 + col_w / 2
        self.rx = a.x1 - col_w / 2
        vs_f = font(self.theme.font_number, 96)
        self.vs = text_block(["VS"], vs_f, 100, (12, 14, 24), shadow=False)

    def _card(self, frame, cx, a):
        d = ImageDraw.Draw(frame, "RGBA")
        x0, x1 = cx - self.col_w / 2, cx + self.col_w / 2
        y0, y1 = self.cy - self.col_h / 2, self.cy + self.col_h / 2
        c = self.theme.card
        d.rounded_rectangle((x0, y0, x1, y1), radius=36, fill=c[:3] + (int(c[3] * a),),
                            outline=self.theme.muted + (int(90 * a),), width=3)

    def draw(self, frame, t):
        a1, a2 = ease_out(t / 0.45), ease_out((t - 0.25) / 0.45)
        for col, cx, a, sgn in ((self.cols[0], self.lx, a1, -1), (self.cols[1], self.rx, a2, 1)):
            if a <= 0:
                continue
            x = cx + sgn * 90 * (1 - a)
            self._card(frame, x, a)
            paste(frame, col, x, self.cy, alpha=a)
        pv = ease_back((t - 0.55) / 0.35)
        if pv > 0:
            r = 74 * pv
            ImageDraw.Draw(frame, "RGBA").ellipse((self.cx - r, self.cy - r, self.cx + r, self.cy + r),
                                                  fill=self.theme.accent)
            paste(frame, self.vs, self.cx, self.cy + 4, scale=pv)


class QuoteCard(Scene):
    def __init__(self, text: str, n_frames: int, **kw):
        super().__init__(n_frames, **kw)
        a = _area()
        text = str(text).strip().strip("\"'“”")
        f, lines, lh = fit(text, self.theme.font_head, int(a.w - 40), int(a.h * 0.7), 78, max_lines=5)
        self.layer = text_block(lines, f, lh, self.theme.text)
        self.mark = text_block(["“"], font(self.theme.font_number, 260), 260, self.theme.accent, shadow=False)
        self.cx, self.cy, self.x0 = a.cx, a.cy + 30, a.x0

    def draw(self, frame, t):
        a = ease_out(t / 0.5)
        top = self.cy - self.layer.height / 2
        paste(frame, self.mark, self.x0 + 60, top - 40, alpha=ease_out(t / 0.3),
              scale=0.7 + 0.3 * ease_back(t / 0.4))
        paste(frame, self.layer, self.cx, self.cy + 24 * (1 - a), alpha=a)


SCENES = {"big_number": BigNumber, "bullet_list": BulletList, "versus": Versus, "quote_card": QuoteCard,
          "title_card": TitleCard}


def make_scene(template: str, content: str, n_frames: int, *, overlay: Image.Image | None = None,
               bg: Image.Image | None = None, theme: Theme = THEME) -> Scene:
    """Scene untuk satu template. Isi yang tidak cocok dengan templatnya (mis. big_number tanpa angka) jatuh ke
    title_card, supaya grafik tetap jadi."""
    cls = SCENES.get(template, TitleCard)
    try:
        return cls(content, n_frames, theme=theme, overlay=overlay, bg=bg)
    except ValueError:
        return TitleCard(content.replace("|", " · "), n_frames, theme=theme, overlay=overlay, bg=bg)


def plain_scene(n_frames: int, text: str = "", *, overlay: Image.Image | None = None,
                theme: Theme = THEME) -> Scene:
    """Cadangan terakhir: latar polos, dengan teks besar bila ada."""
    if text.strip():
        return TitleCard(text, n_frames, theme=theme, overlay=overlay)
    return Scene(n_frames, theme=theme, overlay=overlay)


# --------------------------------------------------------------------------
# Lapisan atas: teks layar + label ilustrasi
# --------------------------------------------------------------------------
def overlay_layer(on_screen_text: str = "", illustration: bool = False, theme: Theme = THEME) -> Image.Image | None:
    """Gambar RGBA selebar layar berisi teks layar (atas, tengah) dan label 'ILUSTRASI' (kiri atas)."""
    text = engine._clean_text(" ".join(str(on_screen_text or "").split())).upper()
    if not text and not illustration:
        return None
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if text:
        f, lines, lh = fit(text, theme.font_head, W - 300, 100, 70, max_lines=1, min_size=34)
        block = text_block(lines, f, lh, (14, 14, 18), shadow=False)
        bw, bh = block.width + 40, block.height + 8
        x0, y0 = (W - bw) / 2, OSD_Y
        d.rounded_rectangle((x0 + 5, y0 + 7, x0 + bw + 5, y0 + bh + 7), radius=26, fill=(0, 0, 0, 110))
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=26, fill=(255, 255, 255, 245))
        img.alpha_composite(block, (int(x0 + 20), int(y0 + 4)))
    if illustration:
        f = font(theme.font_head, 34)
        lab = text_block(["ILUSTRASI"], f, 40, (255, 255, 255), shadow=False)
        x0, y0 = 60, LABEL_Y
        d.rounded_rectangle((x0, y0, x0 + lab.width + 16, y0 + lab.height - 4), radius=18, fill=(0, 0, 0, 160))
        img.alpha_composite(lab, (x0 + 8, y0 - 2))
    return img
