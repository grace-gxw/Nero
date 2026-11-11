"""
Nero - Google Gemini (paket gratis) sebagai AI yang lebih kuat untuk fitur yang membutuhkannya.

Gemini bisa MENONTON video (bukan hanya melihat beberapa gambar), jadi ia juga bisa membaca animasi
subtitle dan zoom. Butuh API key gratis dari Google AI Studio (https://aistudio.google.com/api-keys),
dibuat sendiri oleh pengguna dan disimpan hanya di data/settings.json (tidak pernah dikirim ke halaman).

Catatan privasi: di paket gratis, isi yang dikirim boleh dipakai Google untuk meningkatkan produknya.
Memakai Interactions API (https://ai.google.dev/gemini-api/docs) lewat HTTP biasa, tanpa paket tambahan.
"""
from __future__ import annotations

import base64
import json
import re
import time

import requests

from local_ai import LocalAIError, _parse_json

API_URL = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"
DEFAULT_MODEL = "gemini-3.8-flash"        # Flash terbaru yang gratis di paket gratis (Nov 2026)
KEY_PAGE = "https://aistudio.google.com/api-keys"
INLINE_LIMIT = 18 * 1024 * 1024          # permintaan dengan data langsung maksimal ±20 MB


def _retry_delay(r: requests.Response, err: dict) -> float | None:
    """Detik tunggu yang diminta Gemini: header Retry-After, atau RetryInfo.retryDelay ("30s") di isi galat."""
    try:
        return float(r.headers.get("Retry-After") or "")
    except ValueError:
        pass
    for d in err.get("details") or []:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str((d or {}).get("retryDelay") or ""))
        if m:
            return float(m.group(1))
    return None


def _error(r: requests.Response, model: str) -> LocalAIError:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    msg = str(err.get("message") or r.text)[:240]
    code, status = r.status_code, str(err.get("status") or "")
    kw = {"status": code, "retry_after": _retry_delay(r, err if isinstance(err, dict) else {})}
    if code == 400 and ("API key" in msg or "API_KEY" in json.dumps(err)):
        return LocalAIError("API key Gemini tidak valid. Periksa lagi di Pengaturan Nero → AI untuk meniru gaya.",
                            **kw)
    if code == 403:
        return LocalAIError("API key Gemini ditolak (diblokir, belum diverifikasi, atau proyeknya tidak boleh "
                            f"memakai paket gratis). Buat kunci baru di {KEY_PAGE}. ({msg})", **kw)
    if code == 404:
        return LocalAIError(f"Model Gemini '{model}' tidak ditemukan. Periksa nama model di Pengaturan Nero.", **kw)
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return LocalAIError("Batas pemakaian gratis Gemini tercapai (per menit atau per hari). "
                            "Coba lagi nanti; kuota harian diatur ulang tengah malam waktu Pasifik.",
                            **{**kw, "status": 429})
    if code in (500, 503, 504):
        return LocalAIError(f"Server Gemini sedang sibuk ({code}). Coba lagi sebentar lagi.", **kw)
    return LocalAIError(f"Gemini menolak permintaan ({code}): {msg}", **kw)


def _output_text(data: dict) -> str:
    """Teks jawaban dari sebuah Interaction (langkah model_output), atau dari bentuk 'outputs' versi SDK."""
    parts = []
    for step in data.get("steps") or []:
        if step.get("type") == "model_output":
            parts += [c.get("text", "") for c in step.get("content") or [] if c.get("type") == "text"]
    if not parts:
        parts = [o.get("text", "") for o in data.get("outputs") or [] if o.get("type") == "text"]
    return "".join(parts)


def interact(inputs: list[dict], *, key: str, model: str = DEFAULT_MODEL, schema: dict | None = None,
             thinking: str = "low", timeout: float = 240, alive=None, usage: dict | None = None) -> str:
    """Kirim satu permintaan; hasil: teks jawaban. inputs: [{type: text|image|video, ...}].
    usage: bila diberikan, diisi jumlah token {"input": n, "output": n} dari jawaban Gemini."""
    if not key:
        raise LocalAIError(f"Belum ada API key Gemini. Buat gratis di {KEY_PAGE} lalu tempel di Pengaturan Nero.")
    body = {"model": model, "input": inputs, "generation_config": {"thinking_level": thinking}}
    if schema:
        body["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
    headers = {"x-goog-api-key": key, "Content-Type": "application/json", "Api-Revision": API_REVISION}
    last = None
    for attempt in range(2):
        if alive:
            alive()
        try:
            r = requests.post(f"{API_URL}/interactions", json=body, headers=headers, timeout=(10, timeout))
        except requests.ConnectionError as e:
            raise LocalAIError("Tidak bisa terhubung ke Gemini. Periksa koneksi internet.", retryable=True) from e
        except requests.Timeout as e:
            # tidak dicoba ulang otomatis: server yang tidak menjawab biasanya sedang penuh, lebih baik pindah model
            raise LocalAIError("Gemini terlalu lama menjawab. Coba lagi.") from e
        if r.ok:
            data = r.json()
            if usage is not None:
                u = data.get("usage") or {}
                usage.update(input=u.get("total_input_tokens"), output=u.get("total_output_tokens"))
            if data.get("status") not in (None, "completed"):
                raise LocalAIError(f"Gemini tidak menyelesaikan jawaban (status {data.get('status')}).")
            text = _output_text(data)
            if not text.strip():
                raise LocalAIError("Jawaban Gemini kosong.")
            return text
        last = _error(r, model)
        if r.status_code not in (500, 503) or attempt:
            break
        time.sleep(3)   # server sibuk sesaat: coba sekali lagi
    raise last


def chat_json(prompt: str, schema: dict, *, key: str, model: str = DEFAULT_MODEL, images: list[bytes] = (),
              video: bytes | None = None, video_fps: float = 4.0, system: str = "", alive=None) -> dict:
    """Pertanyaan + gambar JPEG + (opsional) video MP4 kecil, jawaban JSON sesuai skema."""
    inputs = []
    if system:
        inputs.append({"type": "text", "text": system})
    if video:
        if len(video) > INLINE_LIMIT:
            raise LocalAIError("Video contoh terlalu besar untuk dikirim ke Gemini.")
        inputs.append({"type": "video", "data": base64.b64encode(video).decode("ascii"), "mime_type": "video/mp4",
                       "processing": {"type": "static", "fps": video_fps}})
    inputs += [{"type": "image", "data": base64.b64encode(x).decode("ascii"), "mime_type": "image/jpeg"}
               for x in images]
    inputs.append({"type": "text", "text": prompt})
    return _parse_json(interact(inputs, key=key, model=model, schema=schema, alive=alive))


def check(key: str, model: str = DEFAULT_MODEL) -> dict:
    """Uji API key dengan pertanyaan sangat kecil. Hasil: {ok, error}."""
    try:
        interact([{"type": "text", "text": "Reply with the single word: ok"}], key=key, model=model, timeout=60)
        return {"ok": True, "error": ""}
    except LocalAIError as e:
        return {"ok": False, "error": str(e)}
