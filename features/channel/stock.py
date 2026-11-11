"""
Nero · Channel Otomatis - video stok dari Pexels lalu Pixabay (SPEC.md 6.8a).

Dicek di dokumentasi resmi (Nov 2026):
- Pexels: GET https://api.pexels.com/v1/videos/search, header "Authorization: <key>" (tanpa Bearer), parameter
  query/orientation/size/locale/page/per_page (maks. 80). video_files[] berisi id, quality (hd|sd|hls), file_type,
  width, height, fps (opsional), link. Entri hls (width/height null) dilewati. Batas 200 permintaan/jam.
- Pixabay: GET https://pixabay.com/api/videos/?key=..&q=.. (q maks. 100 karakter, per_page 3–200, safesearch).
  Tidak ada filter orientasi untuk video, jadi klip potret dipilih di kode. Rendisi large/medium/small/tiny
  (url, width, height, size); "large" bisa kosong. Galat berupa teks biasa. Hasil pencarian wajib di-cache 24 jam.
  Key ada di URL, jadi URL tidak pernah ditulis ke log atau pesan galat.

Klip yang dipakai 30 hari terakhir dan klip yang sudah dipakai di video yang sama tidak dipilih lagi.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass

import requests

from . import media

PEXELS_URL = "https://api.pexels.com/v1/videos/search"
PIXABAY_URL = "https://pixabay.com/api/videos/"
TIMEOUT = 20
CACHE_SECONDS = 24 * 3600
PER_PAGE = 15
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
HEADERS = {"User-Agent": "Nero/1.0 (aplikasi desktop lokal)"}
TARGET_SHORT = 1080      # sisi pendek ideal (lebar video potret)
MAX_SHORT = 1440         # di atas ini dianggap 4K/terlalu besar: hanya dipakai bila tidak ada pilihan lain
MIN_SHORT = 540
DOWNLOAD_TRIES = 3


class StockError(RuntimeError):
    """Gagal mendapat video stok (pesan aman ditampilkan; tidak memuat key)."""


@dataclass
class StockClip:
    source: str          # pexels | pixabay
    id: str
    page_url: str
    author: str
    author_url: str
    duration: float
    width: int
    height: int
    file_url: str

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"


# --------------------------------------------------------------------------
# Pilih berkas
# --------------------------------------------------------------------------
def pick_file(files: list[tuple[int, int, str]]) -> tuple[int, int, str] | None:
    """Dari [(lebar, tinggi, url)] pilih berkas potret berukuran sedang: sisi pendek ≥ 1080 sekecil mungkin
    (bukan 4K); bila tidak ada, yang terbesar di bawahnya (minimal 540)."""
    portrait = [f for f in files if f[0] and f[1] and f[2] and f[1] > f[0]]
    good = sorted((f for f in portrait if TARGET_SHORT <= f[0] <= MAX_SHORT), key=lambda f: f[0])
    if good:
        return good[0]
    lower = sorted((f for f in portrait if MIN_SHORT <= f[0] < TARGET_SHORT), key=lambda f: -f[0])
    if lower:
        return lower[0]
    bigger = sorted((f for f in portrait if f[0] > MAX_SHORT), key=lambda f: f[0])
    return bigger[0] if bigger else None


def parse_pexels(data: dict) -> list[StockClip]:
    out = []
    for v in data.get("videos") or []:
        files = [(int(f.get("width") or 0), int(f.get("height") or 0), str(f.get("link") or ""))
                 for f in v.get("video_files") or [] if f.get("quality") != "hls"]
        best = pick_file(files)
        if not best:
            continue
        user = v.get("user") or {}
        out.append(StockClip("pexels", str(v.get("id")), str(v.get("url") or ""), str(user.get("name") or ""),
                             str(user.get("url") or ""), float(v.get("duration") or 0), best[0], best[1], best[2]))
    return out


def parse_pixabay(data: dict) -> list[StockClip]:
    out = []
    for h in data.get("hits") or []:
        vids = h.get("videos") or {}
        files = [(int(r.get("width") or 0), int(r.get("height") or 0), str(r.get("url") or ""))
                 for r in (vids.get(k) or {} for k in ("large", "medium", "small", "tiny"))]
        best = pick_file(files)
        if not best:
            continue
        user, uid = str(h.get("user") or ""), h.get("user_id")
        author_url = f"https://pixabay.com/users/{user}-{uid}/" if user and uid else ""
        out.append(StockClip("pixabay", str(h.get("id")), str(h.get("pageURL") or ""), user, author_url,
                             float(h.get("duration") or 0), best[0], best[1], best[2]))
    return out


# --------------------------------------------------------------------------
# Pencarian (dengan cache 24 jam)
# --------------------------------------------------------------------------
def _cache_path(cache_dir: str, source: str, query: str) -> str:
    norm = " ".join(query.lower().split())
    digest = hashlib.sha1(f"{source}|{norm}".encode()).hexdigest()[:16]
    return os.path.join(cache_dir, "search", f"{source}_{digest}.json")


def _cached(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        if time.time() - float(got.get("time", 0)) < CACHE_SECONDS:
            return got.get("data")
    except (OSError, ValueError):
        pass
    return None


def _store(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"time": time.time(), "data": data}, f)
    os.replace(tmp, path)


def _http_error(source: str, r: requests.Response) -> StockError:
    if r.status_code == 429:
        return StockError(f"{source}: batas permintaan API tercapai, coba lagi nanti.")
    if r.status_code in (400, 401, 403):
        return StockError(f"{source}: key ditolak atau permintaan tidak valid (HTTP {r.status_code}).")
    return StockError(f"{source}: galat HTTP {r.status_code}.")


def search(source: str, query: str, key: str, *, cache_dir: str, session=None) -> list[StockClip]:
    path = _cache_path(cache_dir, source, query)
    data = _cached(path)
    if data is None:
        s = session or requests
        try:
            if source == "pexels":
                r = s.get(PEXELS_URL, headers={**HEADERS, "Authorization": key}, timeout=TIMEOUT,
                          params={"query": query, "orientation": "portrait", "per_page": PER_PAGE,
                                  "locale": "en-US"})
            else:
                r = s.get(PIXABAY_URL, headers=HEADERS, timeout=TIMEOUT,
                          params={"key": key, "q": query[:100], "lang": "en", "video_type": "film",
                                  "safesearch": "true", "per_page": 30})
        except requests.RequestException as e:
            raise StockError(f"{source}: tidak bisa terhubung ({type(e).__name__}).") from None
        if r.status_code != 200:
            raise _http_error(source, r)
        try:
            data = r.json()
        except ValueError:
            raise StockError(f"{source}: jawaban bukan JSON.") from None
        _store(path, data)
    return parse_pexels(data) if source == "pexels" else parse_pixabay(data)


def choose(clips: list[StockClip], need: float, exclude: set[str]) -> StockClip | None:
    """Klip pertama (urutan relevansi API) yang cukup panjang; bila tidak ada, yang terpanjang (akan diulang)."""
    usable = [c for c in clips if c.key not in exclude]
    long_enough = [c for c in usable if c.duration >= need]
    if long_enough:
        return long_enough[0]
    return max(usable, key=lambda c: c.duration) if usable else None


def simplify(query: str) -> list[str]:
    """Kata kunci yang makin sederhana: 'ocean waves aerial' -> ['ocean waves aerial', 'ocean waves', 'ocean']."""
    words = query.split()
    out = []
    for q in (" ".join(words), " ".join(words[:2]), " ".join(words[:1])):
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q)
    return out


def forget(path: str) -> None:
    """Buang klip unduhan dari cache (mis. rusak), supaya tidak dipakai lagi."""
    try:
        os.remove(path)
    except OSError:
        pass


def download(clip: StockClip, cache_dir: str, *, session=None, cancel: threading.Event | None = None) -> str:
    path = os.path.join(cache_dir, "stock", f"{clip.source}_{clip.id}_{clip.width}x{clip.height}.mp4")
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s = session or requests
    part = path + ".part"
    try:
        with s.get(clip.file_url, headers={**HEADERS, "Accept-Encoding": "identity"}, stream=True,
                   timeout=TIMEOUT) as r:
            if r.status_code != 200:
                raise StockError(f"{clip.source}: unduhan klip {clip.id} gagal (HTTP {r.status_code}).")
            headers = getattr(r, "headers", None) or {}
            try:
                expected = 0 if headers.get("Content-Encoding") else int(headers.get("Content-Length") or 0)
            except ValueError:
                expected = 0
            if expected > MAX_DOWNLOAD_BYTES:
                raise StockError(f"{clip.source}: klip {clip.id} terlalu besar.")
            size = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    media.check_cancel(cancel)
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise StockError(f"{clip.source}: klip {clip.id} terlalu besar.")
                    f.write(chunk)
        if size == 0 or (expected and size != expected):
            raise StockError(f"{clip.source}: unduhan klip {clip.id} tidak lengkap.")
        os.replace(part, path)
    except requests.RequestException as e:
        raise StockError(f"{clip.source}: unduhan klip {clip.id} terputus ({type(e).__name__}).") from None
    finally:
        if os.path.exists(part):
            os.remove(part)
    return path


def find(query: str, need: float, *, keys: dict[str, str], exclude: set[str], cache_dir: str, log=print,
         session=None, cancel=None) -> tuple[StockClip, str, list[str]]:
    """Cari & unduh satu klip: Pexels lalu Pixabay, lalu kata kunci yang disederhanakan.
    Hasil: (klip, berkas lokal, catatan kegagalan di jalan)."""
    notes: list[str] = []
    sources = [s for s in ("pexels", "pixabay") if keys.get(s)]
    if not sources:
        raise StockError("Key Pexels/Pixabay belum diisi.")
    for q in simplify(query):
        for src in sources:
            media.check_cancel(cancel)
            try:
                clips = search(src, q, keys[src], cache_dir=cache_dir, session=session)
            except StockError as e:
                notes.append(str(e))
                log(f"visual: {e}")
                continue
            for _ in range(DOWNLOAD_TRIES):     # unduhan gagal: coba kandidat berikutnya dari hasil yang sama
                clip = choose(clips, need, exclude)
                if clip is None:
                    notes.append(f"{src} '{q}': tidak ada klip potret yang belum dipakai")
                    break
                try:
                    path = download(clip, cache_dir, session=session, cancel=cancel)
                except StockError as e:
                    exclude.add(clip.key)
                    notes.append(str(e))
                    log(f"visual: {e}")
                    continue
                log(f"visual: stok {clip.key} ({clip.width}x{clip.height}, {clip.duration:.0f} dtk) untuk '{q}'")
                return clip, path, notes
    raise StockError("; ".join(notes) or "tidak ada klip stok")


def as_dict(clip: StockClip) -> dict:
    return asdict(clip)
