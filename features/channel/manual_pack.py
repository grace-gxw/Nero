"""
Nero · Channel Otomatis - paket unggah manual untuk TikTok dan Instagram Reels (SPEC 6.11).

TikTok dan Instagram SELALU diunggah sendiri oleh pengguna (SPEC bagian 7). Nero hanya menyiapkan caption dan
pengingat label AI. Aturannya dicek di sumber resmi pada 12 Nov 2026:
- TikTok: caption maks. 2.200 karakter (Newsroom & Content Posting API). SPEC meminta teks ≤150 karakter +
  3–5 hashtag. Label "AI-generated content" wajib untuk gambar/video AI yang realistis; narasi TTS generik
  (bukan suara orang terkenal) dikecualikan.
- Instagram: maks. 5 hashtag per post (Help Center), caption maks. 2.200 karakter (Graph API). Label AI wajib untuk
  reel dengan narasi suara AI yang realistis (contoh resmi Meta), jadi pengingat "Add AI label" selalu aktif.
- Tanpa #fyp/#Shorts dan tanpa menyebut platform lain.
Teks caption ada di prompts/_caption_*.md, bukan di kode.
"""
from __future__ import annotations

from . import llm
from .schemas import Captions, Metadata

TIKTOK_TEXT_MAX = 150          # SPEC 6.11: teks caption TikTok (di luar hashtag)
CAPTION_MAX = 2200             # batas caption TikTok & Instagram
HASHTAGS_MIN, HASHTAGS_MAX = 3, 5
# hashtag yang tidak dipakai: menyebut platform di bagian mana pun (#YTShorts, #FaktaShorts, #IGReels), diawali kata
# umum (#fypシ, #ViralHari), atau persis kata pendek ini
SKIP_PLATFORMS = ("shorts", "youtube", "tiktok", "reels", "instagram")
SKIP_PREFIXES = ("fyp", "foryou", "viral", "trending", "explore")
SKIP_EXACT = {"ig", "yt", "fy", "short", "reel", "xyzbca"}


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _hashtag(text: str) -> str:
    return "#" + "".join(ch for ch in str(text).lstrip("#") if ch.isalnum() or ch == "_")


def _skip(tag: str) -> bool:
    body = tag.lstrip("#").lower()
    return body in SKIP_EXACT or body.startswith(SKIP_PREFIXES) or any(p in body for p in SKIP_PLATFORMS)


def strip_hashtags(text: str) -> str:
    """Hapus #hashtag dari teks (mis. judul YouTube), supaya jumlah hashtag caption terkendali."""
    return " ".join(w for w in str(text).split() if not w.startswith("#"))


def pack_hashtags(meta: Metadata, topic: str = "") -> list[str]:
    """3–5 hashtag dari metadata (tanpa hashtag platform seperti #Shorts/#fyp), dilengkapi dari tag lalu dari kata
    topik bila kurang."""
    out: list[str] = []

    def add(t: str) -> None:
        t = _hashtag(t)
        if len(t) > 2 and not _skip(t) and t.lower() not in {x.lower() for x in out}:
            out.append(t)

    for t in meta.hashtags:
        add(t)
    for t in meta.tags:
        if len(out) >= HASHTAGS_MIN:
            break
        add("".join(w[:1].upper() + w[1:] for w in str(t).split()))      # "fakta hewan" -> #FaktaHewan
    for w in str(topic or meta.title).split():
        if len(out) >= HASHTAGS_MIN:
            break
        if len(w) >= 4:
            add(w[:1].upper() + w[1:])
    return out[:HASHTAGS_MAX]


def _fit(text: str, limit: int) -> str:
    """Potong di batas kata supaya panjangnya (UTF-16) ≤ limit."""
    if utf16_len(text) <= limit:
        return text
    words, out = text.split(), ""
    for w in words:
        nxt = f"{out} {w}".strip()
        if utf16_len(nxt + "…") > limit:
            break
        out = nxt
    return (out or text[: max(1, limit - 1)]).rstrip(" ,.;:") + "…"


def build(meta: Metadata, *, uses_ai: bool, topic: str = "") -> Captions:
    """Caption TikTok & Instagram dari metadata. uses_ai = video memakai gambar AI (label wajib di TikTok)."""
    tags = pack_hashtags(meta, topic)
    headline = strip_hashtags(meta.title) or " ".join(meta.title.split())
    cta = llm.render_prompt("_caption_cta")
    body_vars = {"headline": headline, "cta": cta, "ai": uses_ai, "hashtags": []}
    if utf16_len(llm.render_prompt("_caption_tiktok", body_vars)) > TIKTOK_TEXT_MAX:
        body_vars["cta"] = ""                               # ajakan dibuang dulu bila kepanjangan
    body = llm.render_prompt("_caption_tiktok", body_vars)
    if utf16_len(body) > TIKTOK_TEXT_MAX:
        extra = utf16_len(body) - utf16_len(headline)
        body_vars["headline"] = _fit(headline, TIKTOK_TEXT_MAX - extra)
    tiktok = llm.render_prompt("_caption_tiktok", {**body_vars, "hashtags": tags})

    summary = strip_hashtags(meta.summary or meta.description.split("\n\nSumber:", 1)[0])
    ig_vars = {"title": headline, "summary": summary, "ai": uses_ai, "hashtags": tags}
    instagram = llm.render_prompt("_caption_instagram", ig_vars)
    if utf16_len(instagram) > CAPTION_MAX:
        over = utf16_len(instagram) - CAPTION_MAX
        ig_vars["summary"] = _fit(summary, max(20, utf16_len(summary) - over))
        instagram = llm.render_prompt("_caption_instagram", ig_vars)
    return Captions(tiktok=tiktok, instagram=instagram, hashtags=tags, tiktok_ai_label=uses_ai,
                    instagram_ai_label=True)
