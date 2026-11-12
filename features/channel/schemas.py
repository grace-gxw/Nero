"""
Nero · Channel Otomatis - kontrak data tiap langkah pipeline (SPEC.md bagian 5).

Model yang dijawab LLM (Research, Script, QC, Metadata, Ping) sengaja memakai nilai bawaan kosong
alih-alih None, supaya skema JSON-nya sederhana untuk Gemini/Groq.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import FORMATS, VISUAL_TYPES

GRAPHIC_TEMPLATES = ("big_number", "bullet_list", "versus", "quote_card", "title_card")
ISSUE_TYPES = ("fact", "policy", "quality", "repetition", "length")
QC_MIN_SCORE = 7


def count_words(text: str) -> int:
    return len([w for w in (text or "").split() if any(c.isalnum() for c in w)])


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------
# research.json
# --------------------------------------------------------------------------
class Source(_Model):
    title: str
    url: str
    published: str = ""          # tanggal ISO bila diketahui
    snippet: str = ""


class Scores(_Model):
    relevance: float = Field(0, ge=0, le=10)
    freshness: float = Field(0, ge=0, le=10)
    novelty: float = Field(0, ge=0, le=10)
    learnings_fit: float = Field(0, ge=0, le=10)

    @property
    def total(self) -> float:
        return self.relevance + self.freshness + self.novelty + self.learnings_fit


class Candidate(_Model):
    topic: str
    angle: str
    why_now: str = "evergreen"
    sources: list[Source] = Field(default_factory=list)
    scores: Scores = Field(default_factory=Scores)


class Research(_Model):
    date: str
    candidates: list[Candidate] = Field(min_length=1)
    chosen_index: int = 0
    rejected_as_duplicate: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _index(self):
        if not 0 <= self.chosen_index < len(self.candidates):
            raise ValueError("chosen_index di luar daftar kandidat")
        return self

    @property
    def chosen(self) -> Candidate:
        return self.candidates[self.chosen_index]


# --------------------------------------------------------------------------
# script.json
# --------------------------------------------------------------------------
class Graphic(_Model):
    template: Literal[GRAPHIC_TEMPLATES]
    content: str


class Segment(_Model):
    """Kolom image_prompt, graphic, on_screen_text, dan label_illustration wajib ada di skema JSON (boleh kosong),
    supaya AI selalu mengisinya; model AI kecil cenderung melewati kolom yang tidak wajib. Data lama/tes tanpa
    kolom itu tetap diterima (diisi nilai kosong)."""
    id: int = Field(ge=1)
    text: str = Field(min_length=1)
    visual_type: Literal[VISUAL_TYPES]
    visual_query: str = Field(min_length=2)      # bahasa Inggris, juga cadangan untuk jenis lain
    image_prompt: str
    graphic: Graphic
    on_screen_text: str
    # true bila gambar AI menggambarkan peristiwa/tokoh/tempat nyata (sejarah/berita): diberi label "ilustrasi"
    label_illustration: bool

    @model_validator(mode="before")
    @classmethod
    def _fill_and_repair(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for k, v in (("image_prompt", ""), ("on_screen_text", ""), ("label_illustration", False)):
            if d.get(k) is None:
                d[k] = v
        g = d.get("graphic") if isinstance(d.get("graphic"), dict) else {}
        template = g.get("template") if g.get("template") in GRAPHIC_TEMPLATES else "title_card"
        g = {"template": template, "content": str(g.get("content") or "")}
        # Isi visual yang wajib tetapi kosong: grafik memakai teks layar bila ada; selain itu segmen memakai
        # video stok (visual_query selalu ada). Lebih baik daripada menolak seluruh naskah.
        if d.get("visual_type") == "motion_graphic" and not g["content"].strip():
            if str(d["on_screen_text"]).strip():
                g["content"] = str(d["on_screen_text"])
            else:
                d["visual_type"] = "stock"
        if d.get("visual_type") == "ai_image" and not str(d["image_prompt"]).strip():
            d["visual_type"] = "stock"
        d["graphic"] = g
        return d


class Claim(_Model):
    claim: str
    source_url: str


class Script(_Model):
    topic: str
    format: Literal[FORMATS]
    hook: str = Field(min_length=3)
    segments: list[Segment] = Field(min_length=1)
    cta: str                     # wajib di skema JSON (model kecil sering melewatinya saat menulis ulang)
    word_count: int = 0
    claims: list[Claim]          # wajib di skema JSON; data lama tanpa kolom ini diisi []

    @model_validator(mode="before")
    @classmethod
    def _fill(cls, data):
        if isinstance(data, dict):
            data = {**data, "cta": data.get("cta") or "", "claims": data.get("claims") or []}
        return data

    # Batas isi (hook ≤ 12 kata, teks layar ≤ 5 kata, jumlah kata) diperiksa writer.script_issues: diminta
    # diperbaiki & dinilai QC, bukan membuat jawaban AI ditolak mentah-mentah.

    @model_validator(mode="after")
    def _ids_and_count(self):
        ids = [s.id for s in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError("id segmen tidak boleh kembar")
        self.word_count = self.spoken_words()    # dihitung sendiri, tidak percaya angka dari LLM
        return self

    def narration(self) -> list[tuple[int, str]]:
        """Teks yang diucapkan, urut: (segment_id, teks). Hook = 0, CTA = -1."""
        out = [(0, self.hook)] + [(s.id, s.text) for s in self.segments]
        return out + ([(-1, self.cta)] if self.cta.strip() else [])

    def spoken_words(self) -> int:
        return sum(count_words(t) for _, t in self.narration())


# --------------------------------------------------------------------------
# qc.json
# --------------------------------------------------------------------------
class Issue(_Model):
    type: Literal[ISSUE_TYPES]
    detail: str
    fix: str = ""


class StatementCheck(_Model):
    """Satu pernyataan di naskah yang dicocokkan QC dengan sumber."""
    segment_id: int                      # 0 = hook, -1 = cta, selain itu id segmen
    statement: str
    kind: Literal["fact", "framing"]     # framing = pembuka/ajakan tanpa klaim fakta (mis. pertanyaan hook)
    supported: bool
    evidence: str                        # kutipan PERSIS dari sumber yang mendukung; kosong bila tidak ada


class QC(_Model):
    passed: bool = Field(False, alias="pass")
    score: float = Field(ge=0, le=10)
    checks: list[StatementCheck] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rule(self):
        # Aturan SPEC 5: lulus jika skor ≥ 7 DAN tidak ada masalah fakta/kebijakan. Nilai "pass" dari LLM diabaikan.
        self.passed = self.score >= QC_MIN_SCORE and not any(i.type in ("fact", "policy") for i in self.issues)
        return self


# --------------------------------------------------------------------------
# metadata.json
# --------------------------------------------------------------------------
class Metadata(_Model):
    title: str = Field(min_length=3, max_length=70)
    description: str
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    category_id: str = ""
    language: str = "id"
    summary: str = ""            # ringkasan dari agen SEO; deskripsi disusun ulang setelah visual jadi


# --------------------------------------------------------------------------
# Hasil media & publikasi (ditulis kode, bukan LLM)
# --------------------------------------------------------------------------
class WordTiming(_Model):
    word: str
    start: float
    end: float
    segment_id: int


class SegmentTiming(_Model):
    """Rentang suara satu bagian narasi (hook = 0, segmen, CTA = -1) di voice.wav, termasuk jeda sebelumnya."""
    segment_id: int
    start: float          # awal bagian ini (visual berganti di sini)
    speech_start: float   # kata pertama mulai diucapkan
    end: float            # suara bagian ini selesai


class Words(_Model):
    words: list[WordTiming]
    duration: float                       # panjang voice.wav (detik)
    source: Literal["edge", "whisper", "piper"] = "edge"
    segments: list[SegmentTiming] = Field(default_factory=list)
    voice: str = ""
    rate: str = ""
    warnings: list[str] = Field(default_factory=list)


class SegmentVisual(_Model):
    segment_id: int
    type: Literal[VISUAL_TYPES + ("plain",)]
    requested: Literal[VISUAL_TYPES]
    file: str                    # relatif terhadap folder run, mis. clips/seg_1.mp4
    start: float = 0             # detik di video
    end: float = 0
    frames: int = 0
    source: str = ""             # pexels | pixabay | cloudflare | nero
    source_id: str = ""
    url: str = ""
    author: str = ""
    query: str = ""
    prompt: str = ""
    seed: int | None = None
    images: list[str] = Field(default_factory=list)    # gambar AI (relatif), bisa 2 untuk segmen panjang
    on_screen_text: bool = False
    fallbacks: list[str] = Field(default_factory=list)  # alasan tiap cadangan yang dipakai


class Visuals(_Model):
    segments: list[SegmentVisual]
    uses_ai_images: bool = False
    uses_stock: bool = False
    fps: int = 30
    total_frames: int = 0

    @property
    def clip_ids(self) -> list[str]:
        """ID klip stok (mis. pexels:123) untuk riwayat: tidak dipakai ulang 30 hari."""
        return [f"{s.source}:{s.source_id}" for s in self.segments if s.type == "stock" and s.source_id]


class RenderInfo(_Model):
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    valid: bool
    problems: list[str] = Field(default_factory=list)    # membuat video tidak valid (tidak boleh di-upload)
    warnings: list[str] = Field(default_factory=list)    # dicatat, tidak menggagalkan
    # video sah tetapi variasi visualnya kurang (banyak cadangan): wajib dicek manusia, tidak boleh upload otomatis
    needs_review: bool = False
    visual_types: list[str] = Field(default_factory=list)
    music: str = ""
    encoder: str = ""
    size_mb: float = 0


class YouTubeResult(_Model):
    video_id: str = ""
    url: str = ""
    studio_url: str = ""
    privacy: str = ""                # privasi yang diminta saat upload
    publish_at: str = ""             # jadwal tayang UTC (RFC 3339) bila dijadwalkan
    publish_at_wib: str = ""
    verified: bool = False
    status: str = ""                 # hasil cek terakhir: diproses | siap | masalah
    note: str = ""
    channel_title: str = ""
    uploaded_at: str = ""
    checked_at: str = ""
    playlist_added: bool = False
    playlist_error: str = ""
    test: bool = False               # diunggah lewat tombol "Unggah uji" (selalu private)
    source: str = ""                 # sidik naskah video yang diunggah (halaman: naskah sudah berganti?)
    title: str = ""                  # judul video di YouTube
    channel_id: str = ""             # channel tujuan (cek ulang hanya dari channel yang sama)
    adopted: str = ""                # video ditemukan (upload sebelumnya), bukan diunggah saat itu: keterangannya
    strict_ok: bool = False          # pernah lolos cek ketat (privasi & jadwal sesuai permintaan)


class Captions(_Model):
    """captions.json: paket unggah manual TikTok & Instagram (manual_pack.py)."""
    tiktok: str
    instagram: str
    hashtags: list[str] = Field(default_factory=list)
    tiktok_ai_label: bool = False     # nyalakan "AI-generated content" saat mengunggah (ada gambar AI realistis)
    instagram_ai_label: bool = True   # nyalakan "Add AI label" (narasi suara AI yang realistis)


class TelegramSent(_Model):
    """telegram.json: bagian paket yang sudah terkirim untuk naskah `source` (kiriman tidak diulang)."""
    source: str = ""
    sent: list[str] = Field(default_factory=list)
    sent_at: str = ""


class HistoryEntry(_Model):
    date: str
    topic: str
    title: str = ""
    format: str = ""
    hook: str = ""
    video_id: str = ""
    clip_ids: list[str] = Field(default_factory=list)
    qc_score: float = 0
    duration: float = 0


class Ping(_Model):
    ok: bool
    message: str = ""


# --------------------------------------------------------------------------
# Draf yang dijawab LLM, lalu dirapikan kode menjadi model di atas
# --------------------------------------------------------------------------
class SearchQuery(_Model):
    id: str = Field(min_length=2)        # kata kunci pencarian Wikipedia bahasa Indonesia
    en: str = ""                         # padanan bahasa Inggris (Wikipedia en biasanya lebih lengkap)


class QueryPlan(_Model):
    queries: list[SearchQuery] = Field(min_length=2, max_length=10)


class CandidateDraft(_Model):
    topic: str
    angle: str
    why_now: str = "evergreen"
    source_ids: list[int] = Field(default_factory=list)    # nomor bahan riset yang mendukung topik ini
    scores: Scores = Field(default_factory=Scores)


class ResearchDraft(_Model):
    candidates: list[CandidateDraft] = Field(min_length=1, max_length=8)


class DuplicateCheck(_Model):
    duplicate_ids: list[int] = Field(default_factory=list)  # nomor kandidat yang sama dengan video lama


class SeoDraft(_Model):
    title: str = Field(min_length=3, max_length=70)
    summary: str = Field(min_length=10)                     # 2–3 kalimat ringkasan untuk deskripsi
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
