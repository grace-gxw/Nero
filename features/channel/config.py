"""
Nero · Channel Otomatis - pengaturan fitur dan kunci API.

Pengaturan disimpan di bagian "channel" dari data/settings.json (lihat common.py). Isinya sama dengan
config.yaml di SPEC.md bagian 3. Model Pydantic di sini adalah satu-satunya sumber nilai bawaan dan aturan
validasinya.

Kunci API disimpan di channel.keys dan tidak pernah dikirim ke halaman. Pengecualian: kunci Gemini (gemini_key)
dan Claude (api_key) memakai kunci umum Nero yang sudah ada. Environment variable di ENV_KEYS selalu menimpa
nilai tersimpan, supaya CLI dan CI bisa berjalan tanpa settings.json.
"""
from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

import common

ID = "channel"

FORMATS = ("myth_vs_fact", "list_3", "story", "question", "explainer")
VISUAL_TYPES = ("stock", "ai_image", "motion_graphic")
PROVIDERS = ("gemini", "gemini_lite", "groq", "ollama")
VOICES = {"id-ID-ArdiNeural": "Pria (Ardi)", "id-ID-GadisNeural": "Wanita (Gadis)"}
# Kecepatan bicara terukur (detik per kata, rate +5%, tanpa jeda), uji nyata edge-tts 12 Nov 2026 pada naskah 111 kata.
# Gadis ±31% lebih lambat, jadi jumlah kata yang pas untuk 45–58 detik berbeda per suara.
VOICE_SECONDS_PER_WORD = {"id-ID-ArdiNeural": 0.357, "id-ID-GadisNeural": 0.469}
VIDEO_TAIL_SECONDS = 0.7      # video berlanjut sebentar setelah suara selesai
NARRATION_GAPS = 7            # jeda antar-bagian narasi (hook + ±6 segmen + CTA)
MAX_TTS_RATE = 15             # SPEC 6.7: rate suara dinaikkan paling banyak sampai +15%
RENDER_SLACK_SECONDS = 3      # SPEC 6.9: durasi video boleh di luar target_seconds sejauh ±3 dtk
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


def _pair(v, name):
    if v[0] > v[1]:
        raise ValueError(f"{name}: angka pertama harus lebih kecil atau sama dengan angka kedua")
    return v


class ChannelInfo(_Section):
    name: str = ""
    niche: str = ""          # genre/niche dipilih pengguna di halaman Nero; kosong = belum siap berjalan
    audience: str = ""
    language: str = "id"
    region: str = "ID"
    tone: str = "santai, cerdas, tidak lebay, tidak clickbait menyesatkan"
    banned_topics: list[str] = Field(default_factory=lambda: [
        "politik praktis", "SARA", "saran medis", "rekomendasi investasi spesifik", "tragedi/bencana terkini"])


class VideoCfg(_Section):
    target_seconds: tuple[int, int] = (45, 58)
    # None = dihitung otomatis dari suara, rate, dan target_seconds (lihat words_range()); isi untuk mengunci
    words_range: tuple[int, int] | None = None
    fps: int = 30
    resolution: tuple[int, int] = (1080, 1920)
    formats_rotation: list[Literal[FORMATS]] = Field(default_factory=lambda: list(FORMATS), min_length=3)
    music_dir: str = ""              # folder musik bebas royalti milik pengguna; kosong = tanpa musik
    music_volume_db: float = Field(-22, ge=-60, le=0)   # 0 = musik sekeras suara narasi
    caption_style: str = "preset:klasik"   # gaya subtitle dari Clip Maker: preset:<id> atau id "Gaya saya"

    @field_validator("target_seconds")
    @classmethod
    def _seconds(cls, v):
        return _pair(v, "target_seconds")

    @field_validator("words_range")
    @classmethod
    def _words(cls, v):
        return v if v is None else _pair(v, "words_range")


class ScheduleCfg(_Section):
    timezone: str = "Asia/Jakarta"
    publish_time: str = "18:00"
    # jam run harian dimulai (jadwal di dalam Nero, Fase I-8). Bawaan sama dengan cron SPEC bagian 9 (05.17 WIB):
    # cukup waktu untuk membuat video dan meninjaunya sebelum jam tayang. Bila Nero baru menyala setelah jam ini, run
    # hari itu dikejar.
    run_time: str = "05:17"
    videos_per_day: int = Field(1, ge=1, le=1)   # saat ini hanya 1 video per hari

    @field_validator("publish_time", "run_time")
    @classmethod
    def _hhmm(cls, v, info):
        m = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", v.strip())
        if not m:
            raise ValueError(f"{info.field_name} harus berbentuk JJ:MM, mis. 18:00")
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"zona waktu '{v}' tidak dikenal") from e
        return v


class ApprovalCfg(_Section):
    # manual: private tanpa jadwal | review_window: private + jadwal tayang | auto: langsung publik
    mode: Literal["manual", "review_window", "auto"] = "manual"


class LLMCfg(_Section):
    # urutan fallback. gemini_lite = model Gemini ringan dengan kunci yang sama, cadangan saat model utama
    # sedang penuh (paket gratis sering membalas 503 / tidak menjawab).
    providers: list[Literal[PROVIDERS]] = Field(default_factory=lambda: ["gemini", "groq", "gemini_lite"],
                                                min_length=1)
    gemini_model: str = ""           # kosong = model Gemini di Pengaturan Nero (gemini_model)
    gemini_lite_model: str = "gemini-3.5-flash-lite"   # dicek tersedia & cepat di paket gratis (Nov 2026)
    groq_model: str = "openai/gpt-oss-120b"   # model produksi Groq dengan json_schema strict (dicek Nov 2026)
    groq_url: str = "https://api.groq.com/openai"
    ollama_model: str = ""           # kosong = model AI lokal di Pengaturan Nero
    anthropic_model: str = ""        # opsional & berbayar, belum dipakai
    temperature_creative: float = Field(0.9, ge=0, le=2)   # dipakai Groq/Ollama
    temperature_strict: float = Field(0.2, ge=0, le=2)
    # Gemini Interactions API tidak memakai temperature, tetapi thinking_level (minimal|low|medium|high)
    gemini_thinking_creative: Literal["minimal", "low", "medium", "high"] = "low"
    gemini_thinking_strict: Literal["minimal", "low", "medium", "high"] = "medium"
    max_retries: int = Field(4, ge=1, le=8)
    timeout_seconds: int = Field(120, ge=20, le=900)


class AIImageCfg(_Section):
    provider: Literal["cloudflare"] = "cloudflare"
    # flux-2-klein-4b: murah & bisa langsung vertikal (width/height 256–1920). flux-1-schnell (usulan SPEC) tidak
    # bisa diatur ukurannya, jadi gambarnya harus dipotong dari persegi. Dicek di dokumentasi Cloudflare Nov 2026.
    model: str = "@cf/black-forest-labs/flux-2-klein-4b"
    width: int = Field(768, ge=256, le=1920)
    height: int = Field(1344, ge=256, le=1920)
    steps: int = Field(4, ge=1, le=8)            # hanya untuk model berformat JSON (flux-2 selalu 4 langkah)
    style_suffix: str = "cinematic lighting, high detail, vertical composition, no text"
    # Perkiraan biaya: petak 512×512 keluaran × neurons_per_tile + langkah × neurons_per_step (harga Workers AI,
    # Nov 2026). klein-4b: 26,05 per petak, tanpa biaya langkah (768×1344 = 6 petak ≈ 156 neurons).
    # flux-1-schnell: 4,8 per petak + 9,6 per langkah. neurons_per_image mengisi angka sendiri (None = otomatis).
    neurons_per_tile: float = Field(26.05, ge=0)
    neurons_per_step: float = Field(0, ge=0)
    neurons_per_image: float | None = Field(None, gt=0)
    daily_neurons: int = Field(10000, ge=0)      # kuota gratis harian Workers AI (reset 00.00 UTC)
    stop_ratio: float = Field(0.8, ge=0, le=1)   # berhenti membuat gambar AI di 80% kuota (SPEC 6.8b)


class VisualsCfg(_Section):
    allowed_types: list[Literal[VISUAL_TYPES]] = Field(default_factory=lambda: list(VISUAL_TYPES), min_length=1)
    max_ai_image_ratio: float = Field(0.6, ge=0, le=1)
    min_types_per_video: int = Field(2, ge=1, le=3)
    ai_image: AIImageCfg = Field(default_factory=AIImageCfg)
    ken_burns: bool = True
    ai_video_provider: str = ""      # opsional & berbayar; kosong = nonaktif


class TTSCfg(_Section):
    engine: Literal["edge", "piper"] = "edge"
    voice: Literal[tuple(VOICES)] = "id-ID-ArdiNeural"
    rate: str = "+5%"
    pause_ms: int = Field(300, ge=0, le=1500)    # jeda antarbagian narasi (hook, segmen, CTA)

    @field_validator("rate")
    @classmethod
    def _rate(cls, v):
        if not re.fullmatch(r"[+-]\d{1,2}%", v.strip()):
            raise ValueError("rate harus berbentuk +5% atau -10%")
        return v.strip()


class ResearchCfg(_Section):
    rss_feeds: list[str] = Field(default_factory=list)
    # kata kunci pencarian YouTube (maks. 3 per hari) untuk melihat tema yang sedang diminati; kosong = tidak dipakai
    youtube_search_queries: list[str] = Field(default_factory=list, max_length=3)
    evergreen_mode: bool = False
    lookback_hours: int = Field(72, ge=6, le=24 * 14)
    use_pytrends: bool = False


class YouTubeCfg(_Section):
    # false = run harian tidak mengunggah; video diunggah sendiri memakai paket di halaman (keputusan pengguna
    # 11 Nov 2026, saat YouTube masih mengunci video dari project Google Cloud yang belum diaudit). Kunci itu dicabut
    # YouTube pada 2 Nov 2026 (revision history Data API), jadi menyalakannya tinggal keputusan pengguna setelah
    # unggah uji berhasil. Unggah uji (private, atas perintah pengguna) tidak bergantung pada nilai ini.
    api_upload: bool = False
    category_id: str = "27"          # 27 Education, 28 Science & Technology, 24 Entertainment
    default_tags: list[str] = Field(default_factory=list)
    contains_synthetic_media: bool = True
    playlist_id: str = ""
    made_for_kids: bool = False


class NotifyCfg(_Section):
    telegram: bool = True
    weekly_report_day: Literal[DAYS] = "monday"     # laporan mingguan analis (SPEC 6.2), hari menurut zona jadwal


class AnalystCfg(_Section):
    # Statistik channel dari YouTube (Authorized Data) hanya boleh diakses pengguna atau agen yang ia setujui
    # (YouTube Developer Policies III.E.3.b). Mengirimnya ke penyedia AI di llm.providers untuk menyusun learnings.md
    # butuh izin tegas pengguna; paket gratis Gemini memakai prompt untuk pengembangan produk dan bisa dibaca
    # peninjau manusia. false = statistik tetap dibaca untuk halaman & laporan mingguan, tanpa pelajaran otomatis.
    share_with_ai: bool = False


def words_range(cfg: ChannelConfig) -> tuple[int, int]:
    """Jumlah kata naskah. Otomatis: dari kecepatan suara terukur supaya narasi + jeda + ekor video masuk
    target_seconds, dengan sisa 1 dtk di bawah dan 3 dtk di atas (rate bisa dinaikkan bila kelebihan)."""
    if cfg.video.words_range:
        return tuple(cfg.video.words_range)
    pace = _pace(cfg, int(cfg.tts.rate.rstrip("%")))
    overhead = _overhead(cfg)
    lo_s, hi_s = cfg.video.target_seconds
    lo = max(20, int(-(-(lo_s + 1 - overhead) // pace)))
    hi = int((hi_s - 3 - overhead) // pace)
    window = max(8, round(0.1 * (lo + hi) / 2))   # target durasi yang sempit tidak boleh membuat rentang 1 kata
    if hi - lo < window:
        mid = (lo + hi) / 2
        lo = max(20, round(mid - window / 2))
        hi = max(round(mid + window / 2), lo + window)
    return lo, hi


def _pace(cfg: ChannelConfig, rate_pct: int) -> float:
    """Detik per kata untuk suara pengaturan pada rate tertentu (terukur pada +5%)."""
    base = VOICE_SECONDS_PER_WORD.get(cfg.tts.voice, max(VOICE_SECONDS_PER_WORD.values()))
    return base * 1.05 / (1 + rate_pct / 100)


def _overhead(cfg: ChannelConfig) -> float:
    return VIDEO_TAIL_SECONDS + NARRATION_GAPS * cfg.tts.pause_ms / 1000


def words_limits(cfg: ChannelConfig) -> tuple[int, int]:
    """Batas keras jumlah kata yang diperiksa kode (QC). Di luar ini video pasti gagal validasi: terlalu pendek
    walau dengan rate pengaturan, atau terlalu panjang walau rate sudah +15%. Prompt penulis tetap meminta rentang
    ideal (words_range), jadi naskah yang sedikit meleset tidak perlu ditulis ulang."""
    lo, hi = words_range(cfg)
    if cfg.video.words_range:
        return lo, hi
    rate = int(cfg.tts.rate.rstrip("%"))
    lo_s, hi_s = cfg.video.target_seconds
    lo_ok = int(-(-(lo_s - RENDER_SLACK_SECONDS + 0.5 - _overhead(cfg)) // _pace(cfg, rate)))
    hi_ok = int((hi_s - _overhead(cfg)) // _pace(cfg, max(rate, MAX_TTS_RATE)))
    return max(20, min(lo, lo_ok)), max(hi, hi_ok)


class ChannelConfig(_Section):
    enabled: bool = False            # flag utama: false = tidak ada jadwal, upload, atau kiriman Telegram
    channel: ChannelInfo = Field(default_factory=ChannelInfo)
    video: VideoCfg = Field(default_factory=VideoCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)
    approval: ApprovalCfg = Field(default_factory=ApprovalCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    visuals: VisualsCfg = Field(default_factory=VisualsCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    research: ResearchCfg = Field(default_factory=ResearchCfg)
    youtube: YouTubeCfg = Field(default_factory=YouTubeCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    analyst: AnalystCfg = Field(default_factory=AnalystCfg)

    @model_validator(mode="after")
    def _ai_disclosure(self):
        # SPEC bagian 8.8: gambar AI realistis wajib diungkap. Selama gambar AI boleh dipakai, pengungkapan
        # tidak boleh dimatikan.
        if "ai_image" in self.visuals.allowed_types and not self.youtube.contains_synthetic_media:
            raise ValueError("contains_synthetic_media harus true selama gambar AI dipakai")
        return self


# --------------------------------------------------------------------------
# Kunci API
# --------------------------------------------------------------------------
# nama kunci -> environment variable yang menimpanya
ENV_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
    "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
    "cloudflare_token": "CLOUDFLARE_API_TOKEN",
    "yt_client_id": "YT_CLIENT_ID",
    "yt_client_secret": "YT_CLIENT_SECRET",
    "yt_refresh_token": "YT_REFRESH_TOKEN",
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "anthropic": "ANTHROPIC_API_KEY",
}
# kunci yang sudah ada di pengaturan umum Nero (dipakai bersama fitur lain)
GLOBAL_KEYS = {"gemini": "gemini_key", "anthropic": "api_key"}
CHANNEL_KEYS = tuple(k for k in ENV_KEYS if k not in GLOBAL_KEYS)

SETTINGS = {**ChannelConfig().model_dump(mode="json"), "keys": {k: "" for k in CHANNEL_KEYS}}


class ConfigError(ValueError):
    """Pengaturan channel tidak valid; pesannya aman ditampilkan ke pengguna."""


def _ensure_registered() -> None:
    # app.py mendaftarkan semua fitur; CLI (python -m features.channel) tidak lewat app.py
    if ID not in common.registered_features():
        common.register_settings(ID, SETTINGS)


def _section(settings: dict | None = None) -> dict:
    _ensure_registered()
    s = settings if settings is not None else common.load_settings()
    sec = s.get(ID)
    return common._merge(SETTINGS, sec if isinstance(sec, dict) else {})


def _explain(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        where = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{where}: {err.get('msg')}" if where else str(err.get("msg")))
    return "Pengaturan channel tidak valid: " + "; ".join(parts)


def load_config(settings: dict | None = None) -> ChannelConfig:
    sec = {k: v for k, v in _section(settings).items() if k != "keys"}
    try:
        return ChannelConfig.model_validate(sec)
    except ValidationError as e:
        raise ConfigError(_explain(e)) from e


def save_config(patch: dict) -> ChannelConfig:
    """Gabungkan perubahan (boleh sebagian & bertingkat), validasi, lalu simpan. Kunci API tidak lewat sini."""
    patch = {k: v for k, v in (patch or {}).items() if k != "keys"}
    current = {k: v for k, v in _section().items() if k != "keys"}
    try:
        cfg = ChannelConfig.model_validate(common._merge(current, patch))
    except ValidationError as e:
        raise ConfigError(_explain(e)) from e
    data = cfg.model_dump(mode="json")
    # daftar diganti utuh (bukan digabung): simpan nilai hasil validasi apa adanya
    common.update_settings(data, ID)
    return cfg


def save_keys(values: dict | None = None, clear: list | tuple = ()) -> None:
    """Simpan kunci API. Nilai kosong = kunci lama tetap dipakai; nama di `clear` = hapus kunci itu."""
    keys = dict(_section()["keys"])
    glob = {}
    for name, v in (values or {}).items():
        if not isinstance(v, str) or not v.strip():
            continue
        if name in GLOBAL_KEYS:
            glob[GLOBAL_KEYS[name]] = v.strip()
        elif name in CHANNEL_KEYS:
            keys[name] = v.strip()
    for name in clear or ():
        if name in GLOBAL_KEYS:
            glob[GLOBAL_KEYS[name]] = ""
        elif name in CHANNEL_KEYS:
            keys[name] = ""
    common.update_settings({"keys": keys}, ID)
    if glob:
        common.update_settings(glob)


def secret(name: str, settings: dict | None = None) -> str:
    """Nilai kunci API: environment variable lebih dulu, lalu pengaturan tersimpan."""
    env = os.environ.get(ENV_KEYS.get(name, ""), "").strip()
    if env:
        return env
    s = settings if settings is not None else common.load_settings()
    if name in GLOBAL_KEYS:
        return str(s.get(GLOBAL_KEYS[name]) or "")
    return str((_section(s).get("keys") or {}).get(name) or "")


def key_from_env(name: str) -> bool:
    """Kunci ini diisi lewat environment variable (menimpa nilai tersimpan; tidak bisa dihapus dari halaman)."""
    return bool(os.environ.get(ENV_KEYS.get(name, ""), "").strip())


def key_status(settings: dict | None = None) -> dict[str, bool]:
    """{nama kunci: sudah diisi?}. Hanya ini yang boleh dikirim ke halaman."""
    s = settings if settings is not None else common.load_settings()
    return {name: bool(secret(name, s)) for name in ENV_KEYS}


def gemini_model(cfg: ChannelConfig, settings: dict | None = None) -> str:
    s = settings if settings is not None else common.load_settings()
    return cfg.llm.gemini_model or str(s.get("gemini_model") or "")


def public_settings(settings: dict | None = None) -> dict:
    """Pengaturan untuk halaman: tanpa nilai kunci API."""
    s = settings if settings is not None else common.load_settings()
    cfg = load_config(s)
    return {"config": cfg.model_dump(mode="json"), "keys": key_status(s),
            "keys_env": {name: key_from_env(name) for name in ENV_KEYS},
            "voices": VOICES, "formats": list(FORMATS), "gemini_model": gemini_model(cfg, s)}
