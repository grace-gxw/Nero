"""
Nero · Channel Otomatis - gambar AI dari Cloudflare Workers AI (SPEC.md 6.8b).

POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}, header "Authorization: Bearer".
Dicek di dokumentasi resmi (Nov 2026):
- Model flux-2-* (mis. @cf/black-forest-labs/flux-2-klein-4b) wajib multipart/form-data: prompt, width & height
  (256–1920), seed; steps tetap 4. Model lain (mis. flux-1-schnell) memakai JSON {prompt, steps}; flux-1-schnell
  tidak bisa diatur ukurannya.
- Jawaban JSON {"success", "result": {"image": base64}, "errors": [{code, message}]}; beberapa model mengirim
  gambar mentah (Content-Type image/*).
- Kuota gratis 10.000 neurons/hari, reset 00.00 UTC. Kuota habis: HTTP 429 kode 3036 ("daily free allocation").
  Kode 3040 (kapasitas penuh sementara) boleh dicoba ulang.

Pemakaian neurons diperkirakan per gambar dari ukuran gambar (estimate_neurons) dan dicatat di
data/channel/usage/; gambar AI berhenti dibuat saat perkiraan mencapai batas aman (bawaan 80%) kuota harian.
"""
from __future__ import annotations

import base64
import io
import threading
import time
from datetime import UTC, datetime

import numpy as np
import requests
from PIL import Image

from . import media, state
from .config import AIImageCfg

CF_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
TIMEOUT = 90
ATTEMPTS = 3
QUOTA_CODES = {3036, 4006}
RETRY_CODES = {3040, 3007}
_sleep = time.sleep
_LOCK = threading.Lock()


class AIImageError(RuntimeError):
    """Gambar AI gagal dibuat (pesan aman ditampilkan; tidak memuat token)."""


class QuotaExhausted(AIImageError):
    """Kuota harian gratis Cloudflare habis/hampir habis."""


# --------------------------------------------------------------------------
# Kuota harian (perkiraan)
# --------------------------------------------------------------------------
def _today_utc(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()


def _usage_path() -> str:
    return state.data_path("usage", "cloudflare.json")


def neurons_used(now: datetime | None = None) -> float:
    data = state.read_json(_usage_path(), {})
    return float((data or {}).get(_today_utc(now), 0) or 0)


def add_neurons(n: float, now: datetime | None = None) -> float:
    with _LOCK:
        data = state.read_json(_usage_path(), {}) or {}
        day = _today_utc(now)
        data = {k: v for k, v in data.items() if k >= _today_utc(now)[:8]}   # simpan bulan berjalan saja
        data[day] = float(data.get(day, 0) or 0) + n
        state.write_json_atomic(_usage_path(), data)
        return data[day]


def mark_exhausted(cfg: AIImageCfg, now: datetime | None = None) -> None:
    used = neurons_used(now)
    if used < cfg.daily_neurons:
        add_neurons(cfg.daily_neurons - used, now)


def estimate_neurons(cfg: AIImageCfg) -> float:
    """Perkiraan neurons satu gambar: petak 512×512 keluaran (dibulatkan ke atas) dan langkah."""
    if cfg.neurons_per_image:
        return cfg.neurons_per_image
    tiles = -(-cfg.width // 512) * -(-cfg.height // 512)
    steps = 0 if _multipart(cfg.model) else cfg.steps
    return tiles * cfg.neurons_per_tile + steps * cfg.neurons_per_step


def quota_ok(cfg: AIImageCfg, images: int = 1, now: datetime | None = None) -> bool:
    return neurons_used(now) + estimate_neurons(cfg) * images <= cfg.daily_neurons * cfg.stop_ratio


# --------------------------------------------------------------------------
# Permintaan
# --------------------------------------------------------------------------
def _multipart(model: str) -> bool:
    return "/flux-2" in model


def full_prompt(prompt: str, cfg: AIImageCfg) -> str:
    text = ", ".join(x.strip() for x in (prompt, cfg.style_suffix) if x and x.strip())
    return text[:2048]


def _errors(r: requests.Response) -> list[tuple[int, str]]:
    try:
        data = r.json()
    except ValueError:
        return [(0, r.text[:200])]
    out = []
    for e in (data.get("errors") or []) if isinstance(data, dict) else []:
        try:
            out.append((int(e.get("code") or 0), str(e.get("message") or "")))
        except (TypeError, ValueError, AttributeError):
            out.append((0, str(e)[:200]))
    return out


def _decode(r: requests.Response) -> bytes:
    ctype = (r.headers.get("Content-Type") or "").lower()
    if ctype.startswith("image/"):
        return r.content
    try:
        data = r.json()
        return base64.b64decode(data["result"]["image"])
    except (ValueError, KeyError, TypeError) as e:
        raise AIImageError(f"jawaban Cloudflare tidak berisi gambar ({type(e).__name__}).") from None


def _check_image(raw: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:  # noqa: BLE001  berkas rusak dalam berbagai bentuk
        raise AIImageError(f"gambar dari Cloudflare rusak ({type(e).__name__}).") from None
    arr = np.asarray(img.convert("L").resize((64, 64)), dtype=np.float32)
    if arr.std() < 3:    # hitam/polos: biasanya ditahan filter keamanan
        raise AIImageError("gambar AI kosong/polos (kemungkinan ditahan filter keamanan).")
    return img.convert("RGB")


def generate(prompt: str, cfg: AIImageCfg, *, account: str, token: str, seed: int | None, session=None,
             log=print, cancel: threading.Event | None = None) -> Image.Image:
    """Satu gambar. QuotaExhausted bila kuota harian habis/hampir habis; AIImageError untuk galat lain."""
    if not account or not token:
        raise AIImageError("Account ID/token Cloudflare belum diisi.")
    if not quota_ok(cfg):
        raise QuotaExhausted(f"perkiraan pemakaian neurons hari ini sudah {neurons_used():.0f} dari "
                             f"{cfg.daily_neurons} (batas aman {cfg.stop_ratio:.0%}).")
    s = session or requests
    url = CF_URL.format(account=account, model=cfg.model)
    headers = {"Authorization": f"Bearer {token}"}
    text = full_prompt(prompt, cfg)
    last = ""
    for attempt in range(ATTEMPTS):
        media.check_cancel(cancel)
        try:
            if _multipart(cfg.model):
                fields = {"prompt": (None, text), "width": (None, str(cfg.width)), "height": (None, str(cfg.height))}
                if seed is not None:
                    fields["seed"] = (None, str(seed))
                r = s.post(url, headers=headers, files=fields, timeout=TIMEOUT)
            else:
                r = s.post(url, headers=headers, json={"prompt": text, "steps": cfg.steps}, timeout=TIMEOUT)
        except requests.RequestException as e:
            last = f"tidak bisa terhubung ({type(e).__name__})"
        else:
            if r.status_code == 200:
                img = _check_image(_decode(r))
                used = add_neurons(estimate_neurons(cfg))
                log(f"visual: gambar AI {img.width}x{img.height}, perkiraan neurons hari ini {used:.0f}")
                return img
            errs = _errors(r)
            codes = {c for c, _ in errs}
            msg = "; ".join(f"{c}: {m}" for c, m in errs)[:300] or f"HTTP {r.status_code}"
            if codes & QUOTA_CODES or "daily free allocation" in msg.lower():
                mark_exhausted(cfg)
                raise QuotaExhausted(f"kuota harian Cloudflare habis ({msg}).")
            if r.status_code in (401, 403):
                raise AIImageError(f"token/akun Cloudflare ditolak (HTTP {r.status_code}: {msg}).")
            if not (codes & RETRY_CODES or r.status_code == 429 or r.status_code >= 500):
                raise AIImageError(f"Cloudflare menolak permintaan (HTTP {r.status_code}: {msg}).")
            last = f"HTTP {r.status_code}: {msg}"
        log(f"visual: gambar AI gagal ({last}), percobaan {attempt + 1}/{ATTEMPTS}")
        if attempt + 1 < ATTEMPTS:
            _sleep(3 * (attempt + 1))
    raise AIImageError(f"gambar AI gagal {ATTEMPTS} kali: {last}")
