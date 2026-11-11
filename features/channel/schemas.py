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


# --------------------------------------------------------------------------
# Hasil media & publikasi (ditulis kode, bukan LLM)
# --------------------------------------------------------------------------
class WordTiming(_Model):
    word: str
    start: float
    end: float
    segment_id: int


class Words(_Model):
    words: list[WordTiming]
    duration: float
    source: Literal["edge", "whisper", "piper"] = "edge"


class SegmentVisual(_Model):
    segment_id: int
    type: Literal[VISUAL_TYPES + ("plain",)]
    requested: Literal[VISUAL_TYPES]
    file: str
    source: str = ""             # pexels | pixabay | cloudflare | nero
    source_id: str = ""
    url: str = ""
    author: str = ""
    prompt: str = ""
    seed: int | None = None
    fallbacks: list[str] = Field(default_factory=list)


class Visuals(_Model):
    segments: list[SegmentVisual]
    uses_ai_images: bool = False


class RenderInfo(_Model):
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    valid: bool
    problems: list[str] = Field(default_factory=list)


class YouTubeResult(_Model):
    video_id: str = ""
    url: str = ""
    studio_url: str = ""
    privacy: str = ""
    publish_at_wib: str = ""
    verified: bool = False
    note: str = ""


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
