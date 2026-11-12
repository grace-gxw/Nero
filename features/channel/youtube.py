"""
Nero · Channel Otomatis - YouTube Data API v3 lewat REST (SPEC.md 6.10): hubungkan akun (OAuth), upload
resumable, verifikasi, playlist, dan judul video populer sebagai sinyal minat untuk riset.

Tanpa google-api-python-client: cukup `requests` (konvensi Nero), jadi tidak ada dependensi baru. Dicek di
dokumentasi resmi 12 Nov 2026:
- OAuth aplikasi desktop: redirect loopback http://127.0.0.1:<port acak> tanpa path (contoh resmi Google) dan PKCE
  S256. Refresh token selalu diberikan untuk installed app. Scope yang benar-benar diizinkan wajib dicek, karena
  pengguna bisa tidak mencentang sebagian.
- Upload resumable: POST ...?uploadType=resumable -> header Location (session URI), lalu PUT per potongan
  (kelipatan 256 KB). 308 + header Range = lanjutkan dari byte berikutnya. 404/410 = sesi kedaluwarsa. Status sesi
  dicek dengan PUT kosong "Content-Range: bytes */TOTAL" (jangan menebak byte yang sudah sampai).
- Kuota (bucket sejak 1 Jun 2026): videos.insert dan search.list masing-masing 100 panggilan/hari; metode lain
  berbagi 10.000 unit/hari. Reset pukul 00.00 waktu Pasifik.
- privacyStatus bawaan YouTube sekarang "public", jadi selalu diisi eksplisit. Sejak 2 Nov 2026 video dari project
  yang belum diaudit tidak lagi dikunci private (revision history); verifikasi tetap memeriksa privasi hasilnya.
- playlistItems.insert butuh scope youtube.force-ssl; youtube.upload saja tidak cukup.

Rahasia: refresh token disimpan di channel.keys.yt_refresh_token dan tidak pernah dikirim ke halaman. Access token
hanya ada di memori. Info akun tanpa rahasia (nama channel, scope) ada di data/channel/youtube_account.json.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import http.server
import json
import os
import random
import re
import secrets
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import cancel as cancelmod
from . import config, state, timeutil
from .config import ChannelConfig
from .schemas import Metadata, YouTubeResult

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TOS_URL = "https://www.youtube.com/t/terms"

SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"    # untuk analis (Fase I-7)
SCOPE_MANAGE = "https://www.googleapis.com/auth/youtube.force-ssl"           # hanya bila playlist dipakai
REQUIRED_SCOPES = (SCOPE_UPLOAD, SCOPE_READONLY)
SCOPE_NAMES = {SCOPE_UPLOAD: "upload video", SCOPE_READONLY: "melihat channel",
               SCOPE_ANALYTICS: "melihat statistik", SCOPE_MANAGE: "mengelola playlist"}

TIMEOUT = 30
UPLOAD_TIMEOUT = (30, 300)          # (sambung, baca) per potongan
CHUNK_BYTES = 8 * 1024 * 1024       # kelipatan 256 KB (syarat upload per potongan)
UPLOAD_ATTEMPTS = 6                 # coba ulang untuk 5xx / koneksi putus, jeda bertambah
API_ATTEMPTS = 3
RETRY_STATUS = {500, 502, 503, 504}
# 403 dari alamat upload yang hanya sementara: sesi tetap disimpan untuk dicoba lagi
KEEP_SESSION_REASONS = {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded", "uploadLimitExceeded"}
VERIFY_WAIT = 300                   # detik menunggu YouTube selesai memproses setelah upload
VERIFY_INTERVAL = 15
AUTH_WAIT = 600                     # batas waktu menunggu persetujuan di halaman Google
SEARCH_PER_DAY = 3                  # R9: hemat kuota pencarian
TREND_DAYS = 7                      # SPEC 6.3: publishedAfter = 7 hari lalu
TREND_TITLES = 15
DUPLICATE_DAYS = 3
ACCOUNT_REFRESH_DAYS = 7            # info channel dibaca ulang berkala (data API tidak disimpan basi)
PACIFIC = ZoneInfo("America/Los_Angeles")
VIDEO_MIME = "video/mp4"
ACCOUNT_FILE = "youtube_account.json"
SESSION_FILE = "upload_session.json"
UPLOAD_LOCK = "upload.lock"                  # data/channel/upload.lock: PID proses yang sedang mengunggah
_sleep = cancelmod.sleep
_LOCK = threading.RLock()


class YouTubeError(RuntimeError):
    """Galat YouTube/Google. Pesannya aman ditampilkan (tidak memuat token)."""

    def __init__(self, msg: str, reason: str = "", status: int = 0):
        super().__init__(msg)
        self.reason, self.status = reason, status


class AuthError(YouTubeError):
    """Perlu (meng)hubungkan ulang YouTube atau memperbaiki client OAuth."""


class SessionExpired(YouTubeError):
    """Session URI upload sudah tidak berlaku (404/410)."""


class SessionUnusable(YouTubeError):
    """Session URI upload menolak izin sekarang (401/403 dari alamat upload, mis. sesi milik akun lain)."""


UPLOAD_BUSY = "Sedang mengunggah ke YouTube. Tunggu selesai dulu."


# --------------------------------------------------------------------------
# Pesan galat
# --------------------------------------------------------------------------
_API_OFF = "YouTube Data API v3 belum diaktifkan di project Google Cloud (APIs & Services → Library)."
REASONS = {
    "quotaExceeded": "Kuota YouTube API hari ini habis. Kuota pulih pukul 00.00 waktu Pasifik (14.00–15.00 WIB).",
    "uploadLimitExceeded": "Batas upload harian channel YouTube sudah tercapai. Coba lagi besok.",
    "youtubeSignupRequired": "Akun Google ini belum punya channel YouTube. Buat channel dulu, lalu hubungkan ulang.",
    "insufficientPermissions": "Izin YouTube kurang untuk tindakan ini. Hubungkan ulang YouTube dan centang semua "
                               "izin.",
    "forbiddenPrivacySetting": "YouTube menolak pengaturan privasi video.",
    "invalidPublishAt": "Waktu tayang (publishAt) ditolak YouTube.",
    "invalidTitle": "Judul video ditolak YouTube.",
    "invalidDescription": "Deskripsi video ditolak YouTube.",
    "invalidTags": "Tag video ditolak YouTube.",
    "invalidCategoryId": "Kategori video tidak dikenal YouTube. Periksa category_id di pengaturan.",
    "playlistNotFound": "Playlist tidak ditemukan. Periksa ID playlist di pengaturan.",
    "accessNotConfigured": _API_OFF,
    "SERVICE_DISABLED": _API_OFF,
}
TOKEN_ERRORS = {
    "invalid_grant": "Izin YouTube sudah tidak berlaku: dicabut, tidak dipakai 6 bulan, atau project Google Cloud "
                     "masih berstatus Testing sehingga izinnya habis tiap 7 hari. Hubungkan ulang YouTube.",
    "invalid_client": "Client ID atau client secret OAuth salah. Periksa lagi di Google Cloud (Google Auth Platform "
                      "→ Clients).",
    "unauthorized_client": "Client OAuth ini tidak bisa dipakai Nero. Pastikan jenisnya \"Desktop app\".",
    "deleted_client": "Client OAuth sudah dihapus di Google Cloud. Buat client \"Desktop app\" baru, lalu isi lagi ID "
                      "dan secret-nya.",
    "admin_policy_enforced": "Admin Google Workspace memblokir akses aplikasi ini ke YouTube.",
}
FAILURE_REASONS = {"codec": "codec video tidak didukung", "conversion": "konversi gagal", "emptyFile": "berkas kosong",
                   "invalidFile": "berkas tidak valid", "tooSmall": "berkas terlalu kecil",
                   "uploadAborted": "upload terputus"}
REJECTION_REASONS = {"claim": "ada klaim hak cipta", "copyright": "pelanggaran hak cipta",
                     "duplicate": "duplikat video lain", "inappropriate": "konten tidak pantas",
                     "legal": "alasan hukum", "length": "terlalu panjang",
                     "termsOfUse": "melanggar Persyaratan Layanan", "trademark": "merek dagang",
                     "uploaderAccountClosed": "akun ditutup", "uploaderAccountSuspended": "akun ditangguhkan"}


def _reason(r: requests.Response) -> tuple[str, str]:
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        return "", (r.text or "")[:200]
    if not isinstance(err, dict):
        return str(err or ""), ""
    found = [e.get("reason", "") for e in err.get("errors") or [] if isinstance(e, dict)]
    found += [d.get("reason", "") for d in err.get("details") or [] if isinstance(d, dict)]
    reason = next((x for x in found if x in REASONS), next((x for x in found if x), ""))
    return reason, str(err.get("message") or "")[:300]


def _api_error(r: requests.Response, what: str) -> YouTubeError:
    reason, msg = _reason(r)
    text = REASONS.get(reason)
    if not text:
        text = f"{what} gagal (HTTP {r.status_code}{', ' + reason if reason else ''})"
        text += f": {msg}" if msg else "."
    auth = r.status_code == 401 or reason in ("insufficientPermissions", "youtubeSignupRequired")
    return (AuthError if auth else YouTubeError)(text, reason, r.status_code)


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat()


def _parse_time(text) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text))     # Python 3.11+ menerima akhiran "Z"
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _wait(seconds: float, cancel=None) -> None:
    if cancel is not None:
        if cancel.wait(max(0.0, seconds)):
            raise cancelmod.Cancelled()
    else:
        _sleep(seconds)


def _check(cancel=None) -> None:
    cancelmod.check_cancel(cancel)
    cancelmod.check_current()


# --------------------------------------------------------------------------
# Pemakaian kuota (perkiraan, per hari Pasifik)
# --------------------------------------------------------------------------
def _pacific_day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(PACIFIC).date().isoformat()


def _usage_path() -> str:
    return state.data_path("usage", "youtube.json")


def usage(now: datetime | None = None) -> dict:
    """Pemakaian hari ini: {"units": n, "insert": n, "search": n}."""
    data = state.read_json(_usage_path(), {}) or {}
    day = data.get(_pacific_day(now)) if isinstance(data, dict) else None
    return dict(day) if isinstance(day, dict) else {}


def _count(kind: str, n: int = 1, now: datetime | None = None) -> None:
    with _LOCK:
        data = state.read_json(_usage_path(), {})
        data = data if isinstance(data, dict) else {}
        today = _pacific_day(now)
        oldest = (datetime.fromisoformat(today) - timedelta(days=7)).date().isoformat()
        data = {k: v for k, v in data.items() if k >= oldest and isinstance(v, dict)}
        day = data.setdefault(today, {})
        day[kind] = int(day.get(kind, 0) or 0) + n
        state.write_json_atomic(_usage_path(), data)


# --------------------------------------------------------------------------
# Akun & token
# --------------------------------------------------------------------------
def _account_path() -> str:
    return state.data_path(ACCOUNT_FILE)


def account() -> dict:
    """Info akun tanpa rahasia: channel_id, channel_title, custom_url, uploads, scopes, connected_at, problem."""
    data = state.read_json(_account_path(), {})
    return data if isinstance(data, dict) else {}


def _save_account(**changes) -> dict:
    with _LOCK:
        data = {**account(), **changes}
        state.write_json_atomic(_account_path(), data)
        return data


def client(settings: dict | None = None) -> tuple[str, str]:
    cid = config.secret("yt_client_id", settings).strip()
    sec = config.secret("yt_client_secret", settings).strip()
    if not cid or not sec:
        raise AuthError("Client ID dan client secret OAuth YouTube belum diisi (Pengaturan channel → YouTube).")
    return cid, sec


def connected(settings: dict | None = None) -> bool:
    return bool(config.secret("yt_refresh_token", settings))


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge S256). Verifier 64 karakter [A-Za-z0-9_-] (RFC 7636: 43–128)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def scopes_for(cfg: ChannelConfig) -> list[str]:
    out = [SCOPE_UPLOAD, SCOPE_READONLY, SCOPE_ANALYTICS]
    if cfg.youtube.playlist_id.strip():
        out.append(SCOPE_MANAGE)
    return out


def auth_url(client_id: str, redirect_uri: str, challenge: str, state_token: str, scopes: list[str]) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
         "code_challenge": challenge, "code_challenge_method": "S256", "state": state_token,
         # pilih akun/channel secara sadar, dan selalu dapat refresh token baru
         "prompt": "select_account consent"}
    return AUTH_URL + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def _token_post(data: dict, session=None) -> dict:
    try:
        r = (session or requests).post(TOKEN_URL, data=data, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise YouTubeError(f"Server Google tidak bisa dihubungi ({type(e).__name__}). Periksa koneksi internet.") \
            from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    if r.status_code == 200 and body.get("access_token"):
        return body
    err = str(body.get("error") or "")
    if err in TOKEN_ERRORS:
        raise AuthError(TOKEN_ERRORS[err], err, r.status_code)
    desc = str(body.get("error_description") or "")[:200]
    # status 0: galat dari server token, bukan dari alamat upload (tidak boleh dianggap "sesi upload rusak")
    raise YouTubeError(f"Google menolak permintaan token (HTTP {r.status_code}{', ' + err if err else ''})"
                       + (f": {desc}" if desc else "."), err, 0)


_tokens: dict[str, tuple[str, float]] = {}


def _token_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def access_token(settings: dict | None = None, *, session=None, fresh: bool = False) -> str:
    """Access token yang masih berlaku (diperbarui dari refresh token bila perlu)."""
    rt = config.secret("yt_refresh_token", settings)
    if not rt:
        raise AuthError("YouTube belum dihubungkan. Tekan \"Hubungkan YouTube\" di pengaturan channel.")
    key = _token_key(rt)
    with _LOCK:
        got = _tokens.get(key)
        if got and not fresh and got[1] - 60 > time.time():
            return got[0]
    cid, sec = client(settings)
    try:
        body = _token_post({"client_id": cid, "client_secret": sec, "refresh_token": rt,
                            "grant_type": "refresh_token"}, session)
    except AuthError as e:
        _save_account(problem=str(e))
        raise
    with _LOCK:
        _tokens[key] = (body["access_token"], time.time() + int(body.get("expires_in") or 3600))
    if account().get("problem"):
        _save_account(problem="")
    return body["access_token"]


def _api(method: str, url: str, *, what: str, units: int = 1, settings: dict | None = None, session=None,
         token: str | None = None, headers: dict | None = None, **kw) -> requests.Response:
    """Panggilan API dengan access token. 401 = token diperbarui sekali; 5xx/koneksi putus = coba ulang."""
    s = session or requests
    r = None
    refreshed = False
    for attempt in range(API_ATTEMPTS + 1):
        try:
            tok = token or access_token(settings, session=session, fresh=refreshed)
            r = s.request(method, url, headers={**(headers or {}), "Authorization": f"Bearer {tok}"},
                          timeout=TIMEOUT, **kw)
        except AuthError:
            raise
        except (requests.RequestException, YouTubeError) as e:   # YouTubeError = server token tak terjangkau
            if attempt >= API_ATTEMPTS:
                if isinstance(e, YouTubeError):
                    raise
                raise YouTubeError(f"{what} gagal: YouTube tidak bisa dihubungi ({type(e).__name__}).") from None
            _sleep(2 ** attempt)
            continue
        if r.status_code == 401 and not refreshed and token is None:
            refreshed = True
            continue
        if r.status_code in RETRY_STATUS and attempt < API_ATTEMPTS:
            _sleep(2 ** attempt)
            continue
        break
    if units:
        _count("units", units)
    if r.status_code >= 400:
        raise _api_error(r, what)
    return r


def fetch_channel(*, settings: dict | None = None, session=None, token: str | None = None) -> dict:
    """Channel milik akun yang terhubung (channels.list mine=true, 1 unit)."""
    r = _api("GET", f"{API_URL}/channels", what="Membaca channel", settings=settings, session=session, token=token,
             params={"part": "snippet,contentDetails", "mine": "true"})
    items = r.json().get("items") or []
    if not items:
        raise YouTubeError(REASONS["youtubeSignupRequired"], "youtubeSignupRequired", 200)
    ch = items[0]
    sn = ch.get("snippet") or {}
    return {"channel_id": ch.get("id", ""), "channel_title": sn.get("title", ""),
            "custom_url": sn.get("customUrl", ""),
            "uploads": ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", "")}


def finish_connect(code: str, verifier: str, redirect_uri: str, *, settings: dict | None = None,
                   session=None, should_save: Callable[[], bool] | None = None) -> dict:
    """Tukar kode dari Google menjadi token, periksa izin, baca channel-nya, lalu simpan refresh token (kecuali
    `should_save()` bilang proses ini sudah dibatalkan/diganti)."""
    cid, sec = client(settings)
    body = _token_post({"client_id": cid, "client_secret": sec, "code": code, "code_verifier": verifier,
                        "grant_type": "authorization_code", "redirect_uri": redirect_uri}, session)
    granted = set(str(body.get("scope") or "").split())
    missing = [SCOPE_NAMES[s] for s in REQUIRED_SCOPES if s not in granted]
    if missing:
        raise YouTubeError("Izin " + " dan ".join(missing) + " tidak dicentang di halaman Google. Hubungkan ulang "
                           "dan centang semua izin.")
    rt = str(body.get("refresh_token") or "")
    if not rt:
        raise YouTubeError("Google tidak memberikan refresh token. Hapus akses aplikasi ini di "
                           "myaccount.google.com/permissions, lalu hubungkan ulang.")
    info = fetch_channel(session=session, token=body["access_token"])   # gagal = token baru tidak disimpan
    if should_save is not None and not should_save():
        raise YouTubeError("Proses menghubungkan sudah dibatalkan; izin baru tidak disimpan.")
    if upload_active():
        raise YouTubeError(UPLOAD_BUSY + " Hubungkan ulang setelah unggahan selesai.")
    with _LOCK:
        _tokens[_token_key(rt)] = (body["access_token"], time.time() + int(body.get("expires_in") or 3600))
    config.save_keys({"yt_refresh_token": rt})
    now = _now_iso()
    return _save_account(**info, scopes=sorted(granted), connected_at=now, checked_at=now, problem="")


def disconnect(*, session=None) -> bool:
    """Hapus token dari Nero lalu cabut izinnya di Google (bila bisa). Hasil: True bila Google mengonfirmasi.
    Proses menghubungkan yang sedang berjalan dibatalkan dulu, supaya tidak menyimpan token sesudahnya."""
    if upload_active():
        raise YouTubeError(UPLOAD_BUSY)
    flow = _flow
    if flow is not None:
        flow.cancel()
        with flow._lock:             # tunggu penukaran kode yang sedang berjalan (bila ada) selesai
            pass
        if upload_active():
            raise YouTubeError(UPLOAD_BUSY)
    rt = config.secret("yt_refresh_token")
    config.save_keys(clear=["yt_refresh_token"])
    with _LOCK:
        _tokens.clear()
        state.remove_retry(_account_path())
    revoked = False
    if rt:
        with contextlib.suppress(requests.RequestException):
            r = (session or requests).post(REVOKE_URL, params={"token": rt}, timeout=TIMEOUT,
                                           headers={"Content-Type": "application/x-www-form-urlencoded"})
            revoked = r.status_code == 200
    return revoked


# --------------------------------------------------------------------------
# Menghubungkan lewat browser: listener loopback sementara
# --------------------------------------------------------------------------
_FRAGMENT_JS = ("<script>if(location.hash.length>1)location.replace('/?'+location.hash.slice(1));</script>")


def _page(title: str, text: str, script: str = "") -> str:
    return (
        "<!doctype html><html lang=\"id\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Nero · {html.escape(title)}</title><style>"
        "body{font-family:system-ui,sans-serif;background:#fff;color:#111;display:grid;place-items:center;"
        "min-height:100vh;margin:0;padding:16px;box-sizing:border-box}"
        "main{max-width:480px;padding:24px;border:1px solid #111;border-radius:12px}"
        "h1{font-size:20px;margin:0 0 8px}p{margin:0;line-height:1.5}"
        "@media (prefers-color-scheme: dark){body{background:#111;color:#eee}main{border-color:#eee}}"
        f"</style></head><body><main><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p></main>{script}"
        "</body></html>")


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True          # satu thread per koneksi: koneksi diam (mis. preconnect browser) tidak menahan
    block_on_close = False


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Nero"
    timeout = 10                   # koneksi yang tidak mengirim apa pun ditutup setelah 10 dtk

    def do_GET(self):  # nama dari http.server
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != "/":
            self.send_error(404)
            return
        code, page = self.server.flow.receive(dict(urllib.parse.parse_qsl(parts.query)))
        data = page.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class AuthFlow:
    """Satu proses "Hubungkan YouTube". Listener sementara di 127.0.0.1:<port acak> menunggu Google mengalihkan
    browser kembali dengan ?code=...&state=..., menukar kodenya, lalu berhenti sendiri."""

    def __init__(self, scopes: list[str], *, settings: dict | None = None, session=None, wait: float = AUTH_WAIT):
        cid, _ = client(settings)
        self.verifier, challenge = pkce_pair()
        self.state = secrets.token_urlsafe(24)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.flow = self
        self.server.timeout = 0.5
        self.redirect_uri = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.url = auth_url(cid, self.redirect_uri, challenge, self.state, scopes)
        self.status, self.error, self.channel = "menunggu", "", ""
        self.session, self.wait = session, wait
        self.started = time.time()
        self.done = threading.Event()
        self._lock = threading.Lock()     # jawaban Google diproses satu per satu (browser bisa mengulang permintaan)
        self.thread: threading.Thread | None = None

    def start(self) -> AuthFlow:
        self.thread = threading.Thread(target=self._serve, name="channel-youtube-auth", daemon=True)
        self.thread.start()
        return self

    def _serve(self) -> None:
        end = time.monotonic() + self.wait
        try:
            while not self.done.is_set() and time.monotonic() < end:
                self.server.handle_request()
        finally:
            self.server.server_close()
            with self._lock:         # penukaran kode yang sedang berjalan diselesaikan dulu
                if not self.done.is_set():
                    self._finish("kedaluwarsa", "Waktu menghubungkan habis. Tekan \"Hubungkan YouTube\" lagi.")

    def _finish(self, status: str, error: str) -> None:
        self.status, self.error = status, error
        self.done.set()

    def receive(self, q: dict) -> tuple[int, str]:
        with self._lock:
            return self._receive(q)

    def _receive(self, q: dict) -> tuple[int, str]:
        if self.done.is_set():
            return 410, _page("Sudah selesai", "Proses menghubungkan ini sudah selesai. Kembali ke Nero.")
        if not q:
            # jaga-jaga bila Google mengirim hasilnya di fragmen URL (#), yang tidak sampai ke server
            return 200, _page("Menunggu…", "Menunggu jawaban dari Google.", _FRAGMENT_JS)
        if not secrets.compare_digest(str(q.get("state", "")).encode(), self.state.encode()):
            return 400, _page("Permintaan tidak dikenal", "Tautan ini bukan dari proses menghubungkan yang sedang "
                                                         "berjalan. Tekan \"Hubungkan YouTube\" lagi dari Nero.")
        if q.get("error"):
            msg = ("Akses ditolak di halaman Google." if q["error"] == "access_denied"
                   else f"Google membatalkan proses ({str(q['error'])[:60]}).")
            self._finish("gagal", msg)
            return 200, _page("YouTube belum terhubung", msg + " Kembali ke Nero untuk mencoba lagi.")
        if not q.get("code"):
            return 400, _page("Permintaan tidak lengkap", "Kode dari Google tidak ada. Coba lagi dari Nero.")
        if upload_active():
            self._finish("gagal", UPLOAD_BUSY)
            return 200, _page("YouTube belum terhubung", UPLOAD_BUSY + " Ulangi Hubungkan setelah selesai.")
        try:
            info = finish_connect(q["code"], self.verifier, self.redirect_uri, session=self.session,
                                  should_save=lambda: not self.done.is_set())
        except YouTubeError as e:
            if not self.done.is_set():
                self._finish("gagal", str(e))
            return 200, _page("YouTube belum terhubung", str(e))
        except Exception as e:  # noqa: BLE001  galat tak terduga tetap dilaporkan ke halaman Nero
            if not self.done.is_set():
                self._finish("gagal", f"Galat tak terduga: {e}"[:300])
            return 500, _page("YouTube belum terhubung", "Terjadi galat tak terduga. Lihat pesan di Nero.")
        self.channel = info.get("channel_title", "")
        self._finish("berhasil", "")
        return 200, _page("YouTube terhubung", f"Channel: {self.channel}. Tab ini boleh ditutup, lalu kembali ke "
                                               "Nero.")

    def cancel(self) -> None:
        if not self.done.is_set():
            self._finish("batal", "Dibatalkan.")

    def snapshot(self) -> dict:
        waiting = not self.done.is_set()
        return {"status": self.status, "error": self.error, "channel": self.channel,
                "url": self.url if waiting else "", "started": self.started}


_flow: AuthFlow | None = None


def begin_connect(cfg: ChannelConfig, settings: dict | None = None, *, session=None) -> AuthFlow:
    """Mulai proses menghubungkan (proses lama yang belum selesai dibatalkan)."""
    global _flow
    if config.key_from_env("yt_refresh_token"):
        raise AuthError("Refresh token YouTube diatur lewat environment variable YT_REFRESH_TOKEN, jadi token dari "
                        "Nero tidak akan dipakai. Hapus env var itu dulu.")
    if upload_active():
        raise YouTubeError(UPLOAD_BUSY)
    with _LOCK:
        if _flow is not None:
            _flow.cancel()
        _flow = AuthFlow(scopes_for(cfg), settings=settings, session=session).start()
        return _flow


def current_flow() -> AuthFlow | None:
    return _flow


def status(settings: dict | None = None) -> dict:
    """Status untuk halaman & CLI (tanpa rahasia)."""
    acct = account()
    is_connected = connected(settings)
    scopes = set(acct.get("scopes") or []) if is_connected else set()
    try:
        client(settings)
        configured = True
    except AuthError:
        configured = False
    return {"configured": configured, "connected": is_connected,
            "from_env": config.key_from_env("yt_refresh_token"),
            "channel_title": acct.get("channel_title", "") if is_connected else "",
            "channel_id": acct.get("channel_id", "") if is_connected else "",
            "custom_url": acct.get("custom_url", "") if is_connected else "",
            "connected_at": acct.get("connected_at", "") if is_connected else "",
            "problem": acct.get("problem", "") if is_connected else "",
            "can_upload": SCOPE_UPLOAD in scopes, "can_playlist": SCOPE_MANAGE in scopes,
            "flow": _flow.snapshot() if _flow is not None else None, "usage": usage()}


# --------------------------------------------------------------------------
# Metadata video
# --------------------------------------------------------------------------
def _no_brackets(text: str) -> str:
    return re.sub(r"[<>]", "", text or "").strip()     # YouTube menolak < dan > di judul & deskripsi


def _fit_bytes(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    return text if len(data) <= limit else data[:limit].decode("utf-8", errors="ignore").rstrip()


def _tags_length(tags: list[str]) -> int:
    # aturan YouTube: koma ikut dihitung; tag berspasi dihitung seolah diberi tanda kutip (+2)
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def fit_tags(tags, limit: int = 500) -> list[str]:
    out: list[str] = []
    for raw in tags:
        t = re.sub(r"[<>,\"]", "", str(raw)).strip().lstrip("#").strip()
        if not t or t.lower() in {x.lower() for x in out} or _tags_length([*out, t]) > limit:
            continue
        out.append(t)
    return out


def video_body(meta: Metadata, cfg: ChannelConfig, *, privacy: str, publish_at: datetime | None) -> dict:
    """Resource video untuk videos.insert (snippet + status)."""
    title = _no_brackets(meta.title)[:100].strip()
    if not title:
        raise YouTubeError("Judul video kosong.")
    if privacy not in ("private", "public", "unlisted"):
        raise ValueError(f"privasi '{privacy}' tidak dikenal")
    snippet = {"title": title, "description": _fit_bytes(_no_brackets(meta.description), 5000),
               "tags": fit_tags([*meta.tags, *cfg.youtube.default_tags]),
               "categoryId": (meta.category_id or cfg.youtube.category_id).strip(),
               "defaultLanguage": meta.language or cfg.channel.language}
    status_ = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(cfg.youtube.made_for_kids),
               "containsSyntheticMedia": bool(cfg.youtube.contains_synthetic_media)}
    if publish_at is not None:
        if privacy != "private":
            raise ValueError("publishAt hanya boleh untuk video private")
        status_["publishAt"] = timeutil.to_rfc3339_utc(publish_at)
    return {"snippet": snippet, "status": status_}


def privacy_plan(cfg: ChannelConfig, *, needs_review: bool, test: bool,
                 now: datetime | None = None) -> tuple[str, datetime | None, str]:
    """(privasi, jadwal tayang lokal atau None, alasan) sesuai mode persetujuan (SPEC bagian 7)."""
    if test:
        return "private", None, "unggah uji: private tanpa jadwal"
    if needs_review:
        return "private", None, "perlu dicek manusia: private tanpa jadwal"
    mode = cfg.approval.mode
    if mode == "review_window":
        at = timeutil.publish_at(cfg.schedule.publish_time, cfg.schedule.timezone, now)
        return "private", at, f"mode review_window: private, tayang otomatis {timeutil.format_local(at)}"
    if mode == "auto":
        return "public", None, "mode auto: langsung publik"
    return "private", None, "mode manual: private tanpa jadwal"


# --------------------------------------------------------------------------
# Upload resumable
# --------------------------------------------------------------------------
def start_session(body: dict, size: int, *, settings: dict | None = None, session=None) -> str:
    """Mulai sesi upload; hasilnya session URI. Sesi tanpa data belum membuat video, jadi aman dicoba ulang."""
    r = _api("POST", UPLOAD_URL, what="Memulai upload", units=0, settings=settings, session=session,
             params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
             headers={"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size),
                      "X-Upload-Content-Type": VIDEO_MIME})
    _count("insert")
    uri = r.headers.get("Location", "")
    if not uri.startswith("https://"):
        raise YouTubeError("YouTube tidak memberikan alamat upload (header Location kosong).")
    return uri


def _put(uri: str, data: bytes, headers: dict, *, settings: dict | None, session) -> requests.Response:
    s = session or requests
    r = None
    for attempt in range(2):       # 401 = access token diperbarui sekali
        tok = access_token(settings, session=session, fresh=attempt > 0)
        r = s.put(uri, data=data, headers={**headers, "Authorization": f"Bearer {tok}"}, timeout=UPLOAD_TIMEOUT)
        if r.status_code != 401:
            break
    return r


def _interpret(r: requests.Response) -> tuple[bool, int, dict]:
    """(selesai?, byte berikutnya, resource video)."""
    if r.status_code in (200, 201):
        try:
            video = r.json()
        except ValueError:
            video = {}
        if not isinstance(video, dict) or not video.get("id"):
            raise YouTubeError("Upload selesai tetapi YouTube tidak mengirim ID video.")
        return True, 0, video
    if r.status_code == 308:
        m = re.fullmatch(r"bytes=0-(\d+)", (r.headers.get("Range") or "").strip())
        return False, int(m.group(1)) + 1 if m else 0, {}
    if r.status_code in (404, 410):
        raise SessionExpired("Sesi upload YouTube sudah kedaluwarsa.", "", r.status_code)
    err = _api_error(r, "Upload video")
    if r.status_code == 401 or (r.status_code == 403 and err.reason not in KEEP_SESSION_REASONS):
        raise SessionUnusable(str(err), err.reason, r.status_code)     # access token sudah diperbarui (_put)
    raise err


def send_file(uri: str, path: str, *, settings: dict | None = None, session=None, cancel=None,
              progress: Callable[[float], None] | None = None, log=print) -> dict:
    """Kirim berkas ke session URI per potongan; melanjutkan dari posisi yang dilaporkan YouTube."""
    size = os.path.getsize(path)
    offset: int | None = None        # None = posisi belum diketahui: tanyakan dulu ke YouTube
    failures = 0
    best = -1                        # byte terjauh yang sudah dikonfirmasi; coba ulang dihitung ulang hanya bila maju
    with open(path, "rb") as f:
        while True:
            _check(cancel)
            sent_from = offset
            problem = ""
            try:
                if offset is None:
                    r = _put(uri, b"", {"Content-Range": f"bytes */{size}"}, settings=settings, session=session)
                else:
                    end = min(offset + CHUNK_BYTES, size) - 1
                    f.seek(offset)
                    chunk = f.read(end - offset + 1)
                    r = _put(uri, chunk, {"Content-Type": VIDEO_MIME, "Content-Range": f"bytes {offset}-{end}/{size}"},
                             settings=settings, session=session)
            except AuthError:
                raise
            except requests.RequestException as e:
                r, problem = None, type(e).__name__
            except YouTubeError as e:                      # memperbarui access token gagal (mis. jaringan)
                r, problem = None, str(e)
            if r is not None and r.status_code not in RETRY_STATUS:
                done, nxt, video = _interpret(r)
                if done:
                    if progress:
                        progress(1.0)
                    return video
                if sent_from is not None and nxt <= sent_from:
                    problem = "potongan tidak diterima"
                else:
                    if nxt > best:
                        best, failures = nxt, 0
                    offset = nxt
                    if progress:
                        progress(nxt / size)
                    continue
            elif r is not None:
                problem = f"HTTP {r.status_code}"
            failures += 1
            if failures > UPLOAD_ATTEMPTS:
                raise YouTubeError(f"Upload terputus berkali-kali ({problem}). Jalankan lagi untuk melanjutkan dari "
                                   "bagian yang sudah terkirim.")
            delay = min(60.0, 2.0 ** failures) + random.random()
            log(f"youtube: upload terputus ({problem}), dicoba lagi dalam {delay:.0f} dtk")
            _wait(delay, cancel)
            offset = None


def find_recent_upload(titles, uploads: str, *, since: datetime | None = None, settings: dict | None = None,
                       session=None) -> tuple[str, str]:
    """(ID video, judulnya) di daftar upload channel yang judulnya sama dengan salah satu `titles` dan diunggah sejak
    `since` (bawaan: 3 hari terakhir); ("", "") bila tidak ada. Jaring pengaman bila catatan sesi upload hilang atau
    kedaluwarsa padahal videonya sudah sampai."""
    wanted = {str(t).strip() for t in ([titles] if isinstance(titles, str) else titles) if str(t).strip()}
    if not uploads or not wanted:
        return "", ""
    try:
        r = _api("GET", f"{API_URL}/playlistItems", what="Memeriksa upload channel", settings=settings,
                 session=session, params={"part": "snippet,contentDetails", "playlistId": uploads, "maxResults": 50})
    except YouTubeError as e:
        if e.status == 404:          # channel yang belum punya video: daftar upload-nya belum ada
            return "", ""
        raise
    since = since or datetime.now(UTC) - timedelta(days=DUPLICATE_DAYS)
    for item in r.json().get("items") or []:
        sn = item.get("snippet") or {}
        when = _parse_time(sn.get("publishedAt"))
        if str(sn.get("title") or "").strip() not in wanted or (when is not None and when < since):
            continue
        vid = (item.get("contentDetails") or {}).get("videoId") or (sn.get("resourceId") or {}).get("videoId")
        if vid:
            return str(vid), str(sn.get("title") or "").strip()
    return "", ""


# --------------------------------------------------------------------------
# Verifikasi & playlist
# --------------------------------------------------------------------------
def fetch_video(video_id: str, *, settings: dict | None = None, session=None) -> dict | None:
    r = _api("GET", f"{API_URL}/videos", what="Memeriksa video", settings=settings, session=session,
             params={"part": "status,processingDetails,snippet", "id": video_id})
    items = r.json().get("items") or []
    return items[0] if items else None


def assess(video: dict | None, privacy: str, publish_at: str, now: datetime | None = None,
           strict: bool = True) -> tuple[str, str]:
    """Bandingkan video di YouTube dengan yang diminta: ("siap" | "diproses" | "masalah", catatan).
    strict=False (cek ulang belakangan): privasi/jadwal yang diubah pengguna di YouTube Studio bukan masalah."""
    if not video:
        return "masalah", "Video tidak ditemukan di YouTube (sudah dihapus?)."
    st = video.get("status") or {}
    pd = video.get("processingDetails") or {}
    up = st.get("uploadStatus", "")
    if up == "failed":
        why = FAILURE_REASONS.get(st.get("failureReason", ""), st.get("failureReason") or "alasan tidak disebut")
        return "masalah", f"YouTube gagal memproses video: {why}."
    if up == "rejected":
        why = REJECTION_REASONS.get(st.get("rejectionReason", ""), st.get("rejectionReason") or "alasan tidak disebut")
        return "masalah", f"Video ditolak YouTube: {why}."
    if up == "deleted":
        return "masalah", "Video sudah dihapus di YouTube."
    proc = pd.get("processingStatus", "")
    if proc in ("failed", "terminated"):
        return "masalah", f"Pemrosesan video di YouTube gagal ({pd.get('processingFailureReason') or proc})."
    want_at = _parse_time(publish_at)
    got = st.get("privacyStatus", "")
    aired = want_at is not None and want_at <= (now or datetime.now(UTC)) and got == "public"
    if not strict:
        changed = f"Privasi diubah di YouTube Studio menjadi '{got}'." if got and got != privacy and not aired else ""
        if up == "processed" and proc in ("", "succeeded"):
            return "siap", changed
        return "diproses", " ".join(x for x in ("YouTube masih memproses video.", changed) if x)
    expect = privacy
    if want_at is not None and want_at <= (now or datetime.now(UTC)) and got == "public":
        expect = "public"                       # jadwal tayang sudah lewat: video memang sudah publik
    user_note = ""
    if expect == "private" and got in ("public", "unlisted"):
        # YouTube tidak pernah menayangkan video private sebelum jadwalnya (atau tanpa jadwal): ini perubahan oleh
        # pengguna di YouTube Studio, bukan masalah
        expect, user_note = got, f"Privasi diubah di YouTube Studio menjadi '{got}'."
    if got != expect:
        if expect != "private" and got == "private":
            return "masalah", ("YouTube menyimpan video sebagai private, tidak sesuai permintaan (bisa jadi dikunci "
                               "karena project Google Cloud belum diaudit). Cek di YouTube Studio.")
        return "masalah", f"Privasi video di YouTube '{got or '?'}', bukan '{expect}'."
    if want_at is not None and expect == "private":
        got_at = _parse_time(st.get("publishAt"))
        if got_at is None or abs((got_at - want_at).total_seconds()) > 60:
            return "masalah", "Jadwal tayang di YouTube tidak sesuai permintaan. Cek di YouTube Studio."
    if up == "processed" and proc in ("", "succeeded"):
        return "siap", user_note
    return "diproses", " ".join(x for x in ("YouTube masih memproses video.", user_note) if x)


def verify(ctx, res: YouTubeResult, *, wait: float = 0, interval: float | None = None, cancel=None,
           settings: dict | None = None, session=None, strict: bool = True) -> YouTubeResult:
    """Cek status video (videos.list, 1 unit per cek) sampai selesai diproses atau `wait` detik habis.
    Hasil disimpan ke youtube.json setiap kali dicek. strict: lihat assess(). Cek longgar hanya dipakai setelah
    video pernah lolos cek ketat (strict_ok); sebelum itu tetap ketat, supaya masalah privasi/jadwal tidak tertutup."""
    strict = strict or not res.strict_ok
    end = time.monotonic() + wait
    while True:
        st, note = assess(fetch_video(res.video_id, settings=settings, session=session), res.privacy,
                          res.publish_at, strict=strict)
        res = res.model_copy(update={"status": st, "verified": st == "siap", "checked_at": _now_iso(),
                                     # cek ketat yang tidak menemukan masalah = privasi & jadwal sudah sesuai
                                     "note": note, "strict_ok": res.strict_ok or (strict and st != "masalah")})
        ctx.save("youtube", res)
        if st != "diproses" or time.monotonic() >= end:
            return res
        _wait(VERIFY_INTERVAL if interval is None else interval, cancel)


def add_to_playlist(playlist_id: str, video_id: str, *, settings: dict | None = None, session=None) -> None:
    if SCOPE_MANAGE not in set(account().get("scopes") or []):
        raise YouTubeError("izin mengelola playlist belum diberikan. Hubungkan ulang YouTube setelah mengisi ID "
                           "playlist.")
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    try:
        _api("POST", f"{API_URL}/playlistItems", what="Menambah ke playlist", units=50, settings=settings,
             session=session, params={"part": "snippet"}, json=body)
    except YouTubeError as e:
        if e.reason != "videoAlreadyInPlaylist":
            raise


# --------------------------------------------------------------------------
# Satu video: upload (sekali saja) -> catat -> verifikasi -> playlist
# --------------------------------------------------------------------------
def _file_sig(path: str) -> list[int]:
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def _body_sig(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def probe(uri: str, size: int, *, settings: dict | None = None, session=None) -> dict | None:
    """Status sesi upload: resource video bila sudah selesai, None bila belum. SessionExpired bila kedaluwarsa,
    SessionUnusable bila sesi menolak izin sekarang. Galat jaringan/token = YouTubeError (sesi tetap disimpan)."""
    try:
        r = _put(uri, b"", {"Content-Range": f"bytes */{size}"}, settings=settings, session=session)
    except requests.RequestException as e:
        raise YouTubeError(f"Status upload sebelumnya tidak bisa dicek ({type(e).__name__}). Coba lagi.") from None
    if r.status_code in RETRY_STATUS:
        raise YouTubeError(f"Status upload sebelumnya tidak bisa dicek (HTTP {r.status_code}). Coba lagi.")
    done, _, video = _interpret(r)
    return video if done else None


# Catatan sesi (runs/<tgl>/upload_session.json) disimpan sampai video_id tercatat di youtube.json. Sesi yang sudah
# tidak bisa dilanjutkan tidak dihapus, hanya ditandai (state), supaya judul, waktu, dan asal naskahnya tetap dipakai
# untuk mencari video yang mungkin sudah sampai (cek duplikat) walau cek itu gagal sekali.
SESSION_LIVE, SESSION_EXPIRED, SESSION_ABANDONED = "aktif", "kedaluwarsa", "ditinggal"
SESSION_UNUSABLE = "tidak_bisa"      # sesi menolak izin sekarang: apakah upload-nya selesai tidak diketahui


def _mark_session(path: str, sess: dict, mark: str) -> dict:
    sess = {"created": _now_iso(), **sess, "state": mark, "uri": ""}
    state.write_json_atomic(path, sess)
    return sess


def _session_titles(sess: dict | None) -> list[str]:
    if not sess:
        return []
    out = [str(t) for t in (sess.get("titles") or []) if str(t).strip()]
    t = str(sess.get("title") or "")
    return [*out, t] if t.strip() and t not in out else out


def _actual(video_id: str, fallback: dict, *, settings: dict | None, session) -> dict:
    """Privasi, jadwal, dan judul yang benar-benar ada di YouTube (untuk video yang ditemukan, bukan diunggah)."""
    try:
        v = fetch_video(video_id, settings=settings, session=session)
    except (YouTubeError, ValueError):
        v = None
    if not v:
        return fallback
    st, sn = v.get("status") or {}, v.get("snippet") or {}
    at = _parse_time(st.get("publishAt"))
    return {"privacy": st.get("privacyStatus") or fallback["privacy"],
            "publish_at": timeutil.to_rfc3339_utc(at) if at else "",
            "title": sn.get("title") or fallback["title"]}


def _upload_lock_path() -> str:
    return state.data_path(UPLOAD_LOCK)


def upload_active() -> bool:
    """Ada upload yang sedang berjalan (proses ini atau proses lain, mis. CLI)."""
    from .runctx import lock_active
    return lock_active(_upload_lock_path())


@contextlib.contextmanager
def _upload_guard():
    """Satu upload sekaligus di komputer ini; Hubungkan/Putuskan menunggu sampai selesai."""
    path = _upload_lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not upload_active():
        with contextlib.suppress(OSError):
            os.remove(path)                           # sisa proses yang sudah mati
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise YouTubeError("Unggahan lain sedang berjalan. Tunggu selesai dulu.") from None
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            state.remove_retry(path)


def publish(ctx, video_path: str, body: dict, *, publish_at_wib: str = "", playlist_id: str = "",
            test: bool = False, source: str = "", on_uploaded: Callable[[YouTubeResult], None] | None = None,
            tz: str = "Asia/Jakarta", cancel=None, progress: Callable[[float], None] | None = None, log=print,
            settings: dict | None = None, session=None, verify_wait: float | None = None) -> YouTubeResult:
    """Upload video satu run ke YouTube. Tidak pernah mengunggah dua kali:
    - youtube.json yang sudah berisi video_id dipakai apa adanya;
    - upload yang terputus dilanjutkan dari sesinya, hanya bila berkas, isi permintaan (judul, privasi, jadwal), dan
      channel-nya masih sama. Bila berbeda, sesi lama dicek dulu: yang sudah selesai dipakai apa adanya, yang belum
      ditinggal (video baru ada setelah byte terakhir sampai);
    - sebelum membuat sesi baru, daftar upload channel dicek untuk video berjudul sama (judul sekarang atau judul
      sesi lama), sejak sehari sebelum sesi lama dibuat.
    Video yang ditemukan (bukan diunggah sekarang) dicatat dengan privasi, jadwal, dan judul aslinya di YouTube, dan
    dengan asal naskah dari sesinya. `on_uploaded(res)` dipanggil begitu video_id tercatat, sebelum menunggu YouTube
    memproses video. Batal atau galat saat menunggu tidak membatalkan video yang sudah terunggah."""
    old = ctx.load("youtube", YouTubeResult)
    if old is not None and old.video_id:
        return old
    with _upload_guard():
        old = ctx.load("youtube", YouTubeResult)
        if old is not None and old.video_id:
            return old
        res = _upload_once(ctx, video_path, body, publish_at_wib=publish_at_wib, test=test, source=source, tz=tz,
                           cancel=cancel, progress=progress, log=log, settings=settings, session=session)
        if on_uploaded is not None:
            try:
                on_uploaded(res)
            except Exception as e:  # noqa: BLE001  video sudah terunggah; catatan dilengkapi run/cek berikutnya
                log(f"youtube: PERINGATAN catatan unggahan belum tersimpan ({e}); dilengkapi saat cek berikutnya")
        try:
            res = verify(ctx, res, wait=VERIFY_WAIT if verify_wait is None else verify_wait, cancel=cancel,
                         settings=settings, session=session)
            if playlist_id:
                res = add_playlist_once(ctx, res, playlist_id, settings=settings, session=session)
        except cancelmod.Cancelled:
            log("youtube: menunggu status dihentikan; video sudah terunggah, cek statusnya nanti (Cek status)")
            res = ctx.load("youtube", YouTubeResult) or res
        except Exception as e:  # noqa: BLE001  video sudah terunggah: galat cek status tidak menggagalkan run
            res = (ctx.load("youtube", YouTubeResult) or res).model_copy(
                update={"note": f"Belum terverifikasi: {e}"[:300]})
            with contextlib.suppress(OSError):
                ctx.save("youtube", res)
        return res


def _upload_once(ctx, video_path: str, body: dict, *, publish_at_wib: str, test: bool, source: str, tz: str,
                 cancel, progress, log, settings: dict | None, session) -> YouTubeResult:
    """Bagian publish() yang menghasilkan video_id (upload, lanjutan, atau video yang ditemukan) lalu mencatatnya."""
    acct = account()
    checked = _parse_time(acct.get("checked_at"))
    stale = checked is None or datetime.now(UTC) - checked > timedelta(days=ACCOUNT_REFRESH_DAYS)
    if not acct.get("uploads") or stale:
        acct = _save_account(**fetch_channel(settings=settings, session=session), checked_at=_now_iso())
    if settings is not None and config.secret("yt_refresh_token", settings) != config.secret("yt_refresh_token"):
        # akun yang dipakai run ini (token saat run dimulai) tidak lagi sama dengan akun yang terhubung
        raise YouTubeError("YouTube dihubungkan ulang selama proses berjalan. Jalankan unggah lagi.")
    title = body["snippet"]["title"]
    sig = _file_sig(video_path)
    want = _body_sig(body)
    channel = acct.get("channel_id", "")
    now = _now_iso()
    sess_path = ctx.path(SESSION_FILE)
    sess = state.read_json(sess_path, None)
    sess = sess if isinstance(sess, dict) and (sess.get("uri") or sess.get("created")) else None
    if (sess is not None and sess.get("state") != SESSION_ABANDONED and sess.get("channel") and channel
            and sess["channel"] != channel):
        # upload sebelumnya mungkin sudah sampai di channel itu: jangan unggah lagi ke channel lain diam-diam
        raise YouTubeError(
            f"Upload sebelumnya untuk run ini dimulai di channel lain ({sess.get('channel_title') or sess['channel']}). "
            "Hubungkan channel itu lagi untuk menyelesaikan atau mengeceknya. Bila memang ingin mengunggah ke "
            f"channel ini, hapus berkas {SESSION_FILE} di folder run tanggal ini.")
    video = None
    origin = None                  # catatan sesi lama bila video yang dipakai berasal darinya
    adopted = ""
    if sess is not None and sess.get("state", SESSION_LIVE) == SESSION_LIVE and str(sess.get("uri") or ""):
        same = (sess.get("file") == sig and sess.get("body") == want
                and str(sess.get("channel") or channel) == channel)
        try:
            if same:
                log("youtube: melanjutkan upload yang terputus")
                video = send_file(sess["uri"], video_path, settings=settings, session=session, cancel=cancel,
                                  progress=progress, log=log)
            else:
                log("youtube: berkas, judul/privasi/jadwal, atau channel berubah sejak upload terputus; sesi lama "
                    "dicek dulu")
                size = sess["file"][0] if isinstance(sess.get("file"), list) and sess["file"] else sig[0]
                video = probe(sess["uri"], int(size), settings=settings, session=session)
                if video is not None:
                    origin = sess
                    adopted = "Upload sebelumnya ternyata sudah selesai dengan judul/pengaturan lama; dipakai apa adanya."
                    log(f"youtube: PERINGATAN {adopted}")
                else:
                    log("youtube: upload lama belum selesai, ditinggalkan; mulai upload baru")
                    sess = _mark_session(sess_path, sess, SESSION_ABANDONED)
        except SessionExpired:
            log("youtube: sesi upload lama sudah kedaluwarsa")
            sess = _mark_session(sess_path, sess, SESSION_EXPIRED)
        except SessionUnusable as e:
            log(f"youtube: sesi upload lama tidak bisa dipakai ({e}); mulai upload baru")
            sess = _mark_session(sess_path, sess, SESSION_UNUSABLE)
    if video is None:
        old_titles = _session_titles(sess)
        since = _parse_time(sess.get("created")) if sess else None
        week = datetime.now(UTC) - timedelta(days=DUPLICATE_DAYS)
        start = min(week, since - timedelta(days=1)) if since else week
        dup, matched = find_recent_upload([title, *old_titles], acct.get("uploads", ""), since=start,
                                          settings=settings, session=session)
        if dup:
            if matched != title and sess is not None:
                origin = sess
            adopted = "Video berjudul sama sudah ada di channel (upload sebelumnya); tidak diunggah ulang."
            log(f"youtube: video berjudul sama sudah ada di channel ({dup}); tidak diunggah ulang")
            video = {"id": dup}
        else:
            log(f"youtube: mengunggah {sig[0] / 1e6:.1f} MB ({body['status']['privacyStatus']})")
            uri = start_session(body, sig[0], settings=settings, session=session)
            # catatan baru membawa judul & waktu sesi lama, supaya cek duplikat berikutnya tetap mencakupnya
            sess = {"state": SESSION_LIVE, "uri": uri, "file": sig, "body": want, "channel": channel,
                    "channel_title": acct.get("channel_title", ""),
                    "created": (sess or {}).get("created") or now, "title": title,
                    "titles": [t for t in old_titles if t != title], "privacy": body["status"]["privacyStatus"],
                    "publish_at": body["status"].get("publishAt", ""), "source": source, "test": test}
            state.write_json_atomic(sess_path, sess)
            try:
                video = send_file(uri, video_path, settings=settings, session=session, cancel=cancel,
                                  progress=progress, log=log)
            except SessionExpired:
                _mark_session(sess_path, sess, SESSION_EXPIRED)
                raise YouTubeError("Sesi upload kedaluwarsa di tengah jalan. Jalankan lagi.") from None
            except SessionUnusable as e:
                _mark_session(sess_path, sess, SESSION_UNUSABLE)
                raise YouTubeError(str(e), e.reason, e.status) from None
    vid = str(video["id"])
    used = {"privacy": body["status"]["privacyStatus"], "publish_at": body["status"].get("publishAt", ""),
            "title": title}
    src, is_test = source, test
    if adopted:
        if origin is not None:
            used = {"privacy": str(origin.get("privacy") or used["privacy"]),
                    "publish_at": str(origin.get("publish_at") or ""), "title": str(origin.get("title") or title)}
            src, is_test = str(origin.get("source") or ""), bool(origin.get("test"))
        used = _actual(vid, used, settings=settings, session=session)
        at = _parse_time(used["publish_at"])
        publish_at_wib = timeutil.format_local(at.astimezone(ZoneInfo(tz))) if at else ""
    res = YouTubeResult(video_id=vid, url=f"https://www.youtube.com/watch?v={vid}",
                        studio_url=f"https://studio.youtube.com/video/{vid}/edit", title=used["title"],
                        privacy=used["privacy"], publish_at=used["publish_at"],
                        publish_at_wib=publish_at_wib if used["publish_at"] else "", status="diproses",
                        channel_id=channel,
                        channel_title=acct.get("channel_title", ""), uploaded_at=_now_iso(), test=is_test,
                        source=src, adopted=adopted)
    ctx.save("youtube", res)                 # SEGERA dicatat: penjaga upload ganda
    with contextlib.suppress(OSError):
        state.remove_retry(sess_path)        # baru dihapus setelah video_id tersimpan
    log(f"youtube: terunggah {res.url}" if not adopted else f"youtube: dipakai {res.url}")
    return res


def add_playlist_once(ctx, res: YouTubeResult, playlist_id: str, *, settings: dict | None = None,
                      session=None) -> YouTubeResult:
    """Tambahkan ke playlist bila belum; kegagalan dicatat (video tetap dianggap terunggah)."""
    if res.playlist_added:
        return res
    try:
        add_to_playlist(playlist_id, res.video_id, settings=settings, session=session)
        res = res.model_copy(update={"playlist_added": True, "playlist_error": ""})
    except YouTubeError as e:
        res = res.model_copy(update={"playlist_error": str(e)})
    ctx.save("youtube", res)
    return res


# --------------------------------------------------------------------------
# Sinyal tren untuk riset (SPEC 6.3)
# --------------------------------------------------------------------------
def _trends_path() -> str:
    return state.data_path("usage", "youtube_trends.json")


def trending_titles(cfg: ChannelConfig, *, now: datetime | None = None, settings: dict | None = None,
                    session=None, log=print) -> list[str]:
    """Judul video pendek yang banyak ditonton 7 hari terakhir untuk kata kunci di pengaturan (maks. 3 pencarian
    per hari). Hanya petunjuk minat penonton untuk prompt riset: bukan sumber fakta, tidak disimpan di hasil riset,
    tanpa statistik (Developer Policies III.E.4: data API tidak boleh disimpan lama atau diolah jadi metrik baru).
    Galat apa pun = daftar kosong; riset tetap berjalan."""
    queries = [q.strip() for q in cfg.research.youtube_search_queries if q.strip()][:SEARCH_PER_DAY]
    if not queries:
        return []
    if not connected(settings):
        log("riset: pencarian YouTube dilewati (YouTube belum dihubungkan)")
        return []
    now = now or datetime.now(UTC)
    day = _pacific_day(now)
    key = [queries, cfg.channel.region, cfg.channel.language]
    cache = state.read_json(_trends_path(), {})
    if isinstance(cache, dict) and cache.get("day") == day and cache.get("key") == key:
        return [str(t) for t in cache.get("titles") or []]
    titles: list[str] = []
    after = (now - timedelta(days=TREND_DAYS)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for q in queries:
        if int(usage(now).get("search", 0) or 0) >= SEARCH_PER_DAY:
            log(f"riset: batas {SEARCH_PER_DAY} pencarian YouTube hari ini sudah tercapai")
            break
        _count("search", now=now)
        try:
            r = _api("GET", f"{API_URL}/search", what="Pencarian YouTube", units=0, settings=settings,
                     session=session, params={"part": "snippet", "q": q, "type": "video", "order": "viewCount",
                                              "publishedAfter": after, "regionCode": cfg.channel.region,
                                              "relevanceLanguage": cfg.channel.language, "videoDuration": "short",
                                              "maxResults": 10, "safeSearch": "moderate"})
        except YouTubeError as e:
            log(f"riset: pencarian YouTube '{q}' gagal: {e}")
            break
        for item in r.json().get("items") or []:
            t = html.unescape(str((item.get("snippet") or {}).get("title") or "")).strip()
            if t and t not in titles:
                titles.append(t)
    titles = titles[:TREND_TITLES]
    state.write_json_atomic(_trends_path(), {"day": day, "key": key, "titles": titles})
    log(f"riset: {len(titles)} judul populer YouTube sebagai petunjuk minat")
    return titles
