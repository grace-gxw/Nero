"""
Nero · Channel Otomatis - klien LLM: prompt dari berkas, jawaban JSON tervalidasi, dan fallback provider.

    generate_json("script", {...}, Script, temperature=0.9) -> Script

Provider (urutan dari pengaturan llm.providers), semuanya memakai modul AI Nero yang sudah ada:
  gemini  gemini_ai.py (Interactions API). Tidak memakai temperature; suhu diterjemahkan ke thinking_level.
  gemini_lite  sama, dengan model ringan (llm.gemini_lite_model) sebagai cadangan saat model utama penuh
  groq    local_ai.openai_chat_json ke https://api.groq.com/openai (OpenAI-compatible, butuh kunci Groq)
  ollama  local_ai.chat_json ke AI lokal di Pengaturan Nero (opsional, tidak dipakai untuk QC)

Per provider: galat sementara (429/5xx/koneksi) dicoba lagi dengan jeda bertambah (Retry-After dihormati),
jawaban yang tidak lolos skema dicoba sekali lagi dengan pesan galatnya. Bila tetap gagal, pindah ke provider
berikutnya. Teks prompt hanya ada di folder prompts/, tidak di kode.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import jinja2
from pydantic import BaseModel, ValidationError

import common
import gemini_ai
import local_ai
from local_ai import LocalAIError

from . import cancel
from . import config as cfgmod
from .config import ChannelConfig

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
LOCAL_PROVIDERS = {"ollama"}
MAX_WAIT = 60.0          # jeda terlama antar-percobaan (detik), walau server meminta lebih
_sleep = cancel.sleep    # berhenti seketika bila run dibatalkan; diganti di tes

_env = jinja2.Environment(loader=jinja2.FileSystemLoader(PROMPTS_DIR), undefined=jinja2.StrictUndefined,
                          keep_trailing_newline=True, autoescape=False, trim_blocks=True, lstrip_blocks=True)


class LLMError(RuntimeError):
    """Semua provider gagal. calls berisi riwayat setiap percobaan."""

    def __init__(self, msg: str, calls: list[Call]):
        super().__init__(msg)
        self.calls = calls


@dataclass
class Call:
    provider: str
    model: str
    ok: bool = False
    seconds: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str = ""
    attempts: int = 0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Prompt & skema
# --------------------------------------------------------------------------
def render_prompt(name: str, variables: dict | None = None) -> str:
    return _env.get_template(f"{name}.md").render(**(variables or {})).strip()


def json_schema_for(schema: type[BaseModel]) -> dict:
    """Skema JSON sederhana dari model Pydantic: $ref dilebur, title/default dibuang, Optional jadi tipe biasa.
    Gemini hanya mendukung sebagian JSON Schema, jadi bentuk sederhana paling aman untuk semua provider."""
    raw = schema.model_json_schema(by_alias=True)
    defs = raw.get("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
        out = {}
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: walk(sub) for name, sub in v.items()}   # nama kolom (mis. "title") tetap
            elif k not in ("$defs", "title", "default", "examples"):
                out[k] = walk(v)
        if "anyOf" in out:
            rest = [x for x in out["anyOf"] if x.get("type") != "null"]
            if len(rest) == 1:
                out.pop("anyOf")
                out.update(rest[0])
        return out

    return walk(raw)


def _short(text: str, n: int) -> str:
    """Pesan galat satu baris untuk log (galat skema berisi beberapa baris)."""
    return " ".join((text or "gagal").split())[:n]


def _validation_message(e: ValidationError) -> str:
    lines = []
    for err in e.errors()[:8]:
        where = ".".join(str(x) for x in err.get("loc", ()))
        lines.append(f"- {where or '(akar)'}: {err.get('msg')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------
def _model_for(provider: str, cfg: ChannelConfig, s: dict) -> str:
    if provider == "gemini":
        return cfgmod.gemini_model(cfg, s)
    if provider == "gemini_lite":
        return cfg.llm.gemini_lite_model
    if provider == "groq":
        return cfg.llm.groq_model
    if provider == "ollama":
        return cfg.llm.ollama_model or str(s.get("local_ai_model") or "")
    return ""


def _ask(provider: str, model: str, prompt: str, schema: dict, temperature: float, cfg: ChannelConfig,
         s: dict, usage: dict) -> dict:
    strict = temperature < 0.5
    if provider in ("gemini", "gemini_lite"):
        key = cfgmod.secret("gemini", s)
        if not model:
            raise LocalAIError("Nama model Gemini kosong. Isi di Pengaturan Nero.")
        thinking = cfg.llm.gemini_thinking_strict if strict else cfg.llm.gemini_thinking_creative
        text = gemini_ai.interact([{"type": "text", "text": prompt}], key=key, model=model, schema=schema,
                                  thinking=thinking, timeout=cfg.llm.timeout_seconds, usage=usage)
        return local_ai._parse_json(text)
    if provider == "groq":
        key = cfgmod.secret("groq", s)
        if not key:
            raise LocalAIError("Belum ada API key Groq (buat gratis di console.groq.com).")
        try:
            return local_ai.openai_chat_json(cfg.llm.groq_url, model, prompt, schema, key=key,
                                             temperature=temperature, timeout=cfg.llm.timeout_seconds, usage=usage)
        except LocalAIError as e:
            # pesan local_ai ditulis untuk Ollama; jelaskan ulang untuk Groq
            if e.status == 404:
                msg = f"Model Groq '{model}' tidak tersedia. Lihat daftar model di console.groq.com/docs/models."
            elif e.status in (401, 403):
                msg = "API key Groq tidak valid atau ditolak."
            elif e.status == 429:
                msg = "Batas pemakaian gratis Groq tercapai."
            else:
                msg = str(e).replace("AI lokal", "Groq")
            raise LocalAIError(msg, status=e.status, retry_after=e.retry_after, retryable=e.retryable) from e
    if provider == "ollama":
        url = s.get("local_ai_url") or local_ai.DEFAULT_URL
        if not model:
            st = local_ai.status(url)
            if not st["ok"]:
                raise LocalAIError(st["error"])
            names = [m["name"] for m in st["models"] if not m.get("thinking")] or [m["name"] for m in st["models"]]
            if not names:
                raise LocalAIError("Belum ada model di AI lokal.")
            model = names[0]
        # jawaban skrip jauh lebih panjang dari pembacaan gaya: beri ruang token lebih besar
        return local_ai.chat_json(prompt, schema, url=url, model=model, temperature=temperature,
                                  timeout=cfg.llm.timeout_seconds, usage=usage,
                                  options={"num_ctx": 16384, "num_predict": 4096})
    raise LocalAIError(f"Provider '{provider}' tidak dikenal.")


def _wait_for(e: LocalAIError, attempt: int) -> float:
    if e.retry_after is not None:
        return min(MAX_WAIT, max(0.0, float(e.retry_after)))
    return min(MAX_WAIT, 2.0 * (2 ** attempt))


# --------------------------------------------------------------------------
# Antarmuka utama
# --------------------------------------------------------------------------
COOLDOWN_SECONDS = 600   # provider yang baru gagal (penuh/timeout/kuota) dilewati dulu selama ini
_cooldown: dict[tuple[str, str], float] = {}


def reset_cooldown() -> None:
    _cooldown.clear()


def _order(providers: list[str], cfg: ChannelConfig, s: dict) -> list[str]:
    """Provider yang sedang istirahat dipindah ke belakang (tetap dicoba bila yang lain juga gagal)."""
    now = time.time()
    fresh = [p for p in providers if _cooldown.get((p, _model_for(p, cfg, s)), 0) <= now]
    return fresh + [p for p in providers if p not in fresh]


def generate_json(prompt_name: str, variables: dict, schema: type[BaseModel], temperature: float, *,
                  cfg: ChannelConfig | None = None, settings: dict | None = None, allow_local: bool = True,
                  providers: list[str] | None = None, check=None, usable=None, log=None,
                  calls: list[Call] | None = None) -> BaseModel:
    """Isi prompts/<prompt_name>.md, minta jawaban JSON, validasi dengan `schema`, fallback antar-provider.

    allow_local=False: provider lokal (Ollama) tidak dipakai, mis. untuk QC yang harus teliti.
    check(hasil) -> [pesan]: pemeriksaan isi tambahan (mis. jumlah kata). Bila ada pesan, diminta perbaikan
    sekali; bila masih ada, hasilnya tetap dikembalikan (langkah berikutnya, mis. QC, yang memutuskan).
    usable(hasil) -> bool: jawaban yang masih punya masalah `check` tetapi sama sekali tidak bisa dipakai (mis. riset
    tanpa satu pun kandidat bersumber) tidak dikembalikan: provider itu dianggap gagal dan provider berikutnya
    dicoba."""
    s = settings if settings is not None else common.load_settings()
    cfg = cfg or cfgmod.load_config(s)
    log = log or (lambda m: print(f"[channel] {m}"))
    calls = calls if calls is not None else []
    schema_dict = json_schema_for(schema)
    base = render_prompt(prompt_name, variables) + "\n\n" + render_prompt(
        "_json_schema", {"schema": json.dumps(schema_dict, ensure_ascii=False)})
    order = [p for p in (providers or cfg.llm.providers) if allow_local or p not in LOCAL_PROVIDERS]
    if not order:
        raise LLMError("Tidak ada provider AI yang boleh dipakai untuk langkah ini.", calls)

    fallback = None             # (call, hasil, masalah): jawaban sesuai skema yang perbaikan isinya gagal
    for provider in _order(order, cfg, s):
        cancel.check_current()
        model = _model_for(provider, cfg, s)
        call = Call(provider, model)
        calls.append(call)
        prompt, fixed, checked, attempt = base, False, False, 0
        t0 = time.time()
        server_failed = False
        pending = None          # jawaban sesuai skema yang sedang diminta perbaikan isinya: (hasil, masalah)
        while attempt < cfg.llm.max_retries:
            cancel.check_current()
            attempt += 1
            call.attempts = attempt
            usage: dict = {}
            try:
                data = _ask(provider, model, prompt, schema_dict, temperature, cfg, s, usage)
            except LocalAIError as e:
                call.error = str(e)
                if e.retryable and attempt < cfg.llm.max_retries:
                    wait = _wait_for(e, attempt - 1)
                    call.notes.append(f"percobaan {attempt}: {e} (tunggu {wait:.0f} dtk)")
                    log(f"{provider}/{model}: {e} -> coba lagi dalam {wait:.0f} dtk")
                    _sleep(wait)
                    continue
                server_failed = True
                break
            finally:
                if usage:
                    call.tokens_in = (call.tokens_in or 0) + (usage.get("input") or 0)
                    call.tokens_out = (call.tokens_out or 0) + (usage.get("output") or 0)
            try:
                result = schema.model_validate(data)
            except ValidationError as e:
                call.error = "jawaban tidak sesuai skema:\n" + _validation_message(e)
                if fixed:
                    break
                fixed = True   # sekali saja: ulangi dengan daftar galat dimasukkan ke prompt
                call.notes.append("jawaban tidak sesuai skema, diulang sekali")
                prompt = base + "\n\n" + render_prompt("_validation_retry", {
                    "errors": _validation_message(e), "previous": json.dumps(data, ensure_ascii=False)[:4000]})
                attempt -= 1   # perbaikan skema tidak dihitung sebagai percobaan galat server
                continue
            problems = list(check(result) or []) if check else []
            if problems and not checked:
                checked = True
                # hanya jawaban yang masih bisa dipakai yang boleh menjadi cadangan terakhir
                pending = (result, problems) if usable is None or usable(result) else None
                call.notes.append("isi belum memenuhi aturan, diminta perbaikan: " + "; ".join(problems))
                log(f"LLM {prompt_name}: minta perbaikan ({'; '.join(problems)[:200]})")
                prompt = base + "\n\n" + render_prompt("_validation_retry", {
                    "errors": "\n".join(f"- {p}" for p in problems),
                    "previous": json.dumps(data, ensure_ascii=False)[:6000]})
                attempt -= 1
                continue
            if problems and usable is not None and not usable(result):
                # tetap tidak bisa dipakai setelah diminta perbaikan: coba provider berikutnya (tanpa cooldown,
                # servernya menjawab)
                call.error = "jawaban tidak bisa dipakai: " + "; ".join(problems)
                break
            if problems:
                call.notes.append("masih belum sempurna: " + "; ".join(problems))
                log(f"LLM {prompt_name}: diterima dengan catatan ({'; '.join(problems)[:200]})")
            call.ok, call.error = True, ""
            call.seconds = round(time.time() - t0, 2)
            _cooldown.pop((provider, model), None)
            log(f"LLM {prompt_name}: {provider}/{model} ok dalam {call.seconds} dtk, "
                f"token masuk {call.tokens_in}, keluar {call.tokens_out}")
            return result
        call.seconds = round(time.time() - t0, 2)
        if server_failed:
            _cooldown[(provider, model)] = time.time() + COOLDOWN_SECONDS
        if pending is not None and fallback is None:
            # permintaan perbaikan isi gagal (server penuh/timeout, atau jawaban perbaikan tidak sesuai skema). Provider
            # lain dicoba dulu (mis. QC yang lengkap); bila semuanya gagal, jawaban pertama yang sudah sesuai skema
            # dipakai dengan catatan, sama seperti perbaikan yang masih belum sempurna.
            fallback = (call, *pending)
            call.notes.append(f"perbaikan gagal ({_short(call.error, 160)}); jawaban pertama disimpan sebagai cadangan")
        log(f"LLM {prompt_name}: {provider}/{model} gagal ({_short(call.error, 400)})")
    if fallback is not None:
        call, result, problems = fallback
        call.ok, call.error = True, ""
        log(f"LLM {prompt_name}: provider lain juga gagal; jawaban pertama {call.provider}/{call.model} dipakai dengan "
            f"catatan ({'; '.join(problems)[:200]})")
        return result
    detail = "; ".join(f"{c.provider}: {_short(c.error, 160)}" for c in calls)
    raise LLMError(f"Semua provider AI gagal untuk '{prompt_name}'. {detail}", calls)
