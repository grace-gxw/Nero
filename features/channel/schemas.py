"""
Nero · Channel Otomatis - kontrak data tiap langkah pipeline (SPEC.md bagian 5).

Model yang dijawab LLM (Research, Script, QC, Metadata, Ping) sengaja memakai nilai bawaan kosong
alih-alih None, supaya skema JSON-nya sederhana untuk Gemini/Groq.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    template: Literal[GRAPHIC_TEMPLATES] = "title_card"
    content: str = ""


class Segment(_Model):
    id: int = Field(ge=1)
    text: str = Field(min_length=1)
    visual_type: Literal[VISUAL_TYPES]
    visual_query: str = Field(min_length=2)      # bahasa Inggris, juga cadangan untuk jenis lain
    image_prompt: str = ""
    graphic: Graphic = Field(default_factory=Graphic)
    on_screen_text: str = ""

    @model_validator(mode="after")
    def _by_type(self):
        if self.visual_type == "ai_image" and not self.image_prompt.strip():
            raise ValueError(f"segmen {self.id}: image_prompt wajib diisi untuk ai_image")
        if self.visual_type == "motion_graphic" and not self.graphic.content.strip():
            raise ValueError(f"segmen {self.id}: graphic.content wajib diisi untuk motion_graphic")
        if count_words(self.on_screen_text) > 5:
            raise ValueError(f"segmen {self.id}: on_screen_text maksimal 5 kata")
        return self


class Claim(_Model):
    claim: str
    source_url: str


class Script(_Model):
    topic: str
    format: Literal[FORMATS]
    hook: str = Field(min_length=3)
    segments: list[Segment] = Field(min_length=1)
    cta: str = ""
    word_count: int = 0
    claims: list[Claim] = Field(default_factory=list)

    @field_validator("hook")
    @classmethod
    def _hook(cls, v):
        if count_words(v) > 12:
            raise ValueError("hook maksimal 12 kata")
        return v

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


class QC(_Model):
    passed: bool = Field(False, alias="pass")
    score: float = Field(ge=0, le=10)
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
