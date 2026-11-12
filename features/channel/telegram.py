"""
Nero · Channel Otomatis - notifikasi Telegram lewat Bot API (SPEC 6.11), memakai `requests`.

Dicek di dokumentasi resmi (core.telegram.org, Bot API 10.4, 12 Nov 2026):
- sendMessage: teks 1–4096 karakter setelah entity diproses (dihitung di sini dalam UTF-16, dengan margin).
  parse_mode HTML: hanya tag tertentu (b, i, u, s, a, code, pre, blockquote, …); karakter < > & di luar tag wajib
  di-escape. link_preview_options menggantikan disable_web_page_preview.
- sendVideo: unggah multipart maks. 50 MB, caption 0–1024 karakter.
- Galat: {"ok": false, "description", "error_code", "parameters": {"retry_after"}}. 429 = tunggu retry_after.
  Batas kirim ±1 pesan/detik per chat.
- Bot tidak bisa memulai chat: pengguna harus membuka bot dan menekan Start dulu. Chat ID dibaca lewat getUpdates
  (update disimpan Telegram maks. 24 jam; tidak bisa dipakai bila webhook aktif).

Token bot ada di URL permintaan, jadi pesan galat TIDAK PERNAH memuat URL atau teks galat requests (yang bisa
memuat URL); hanya nama jenis galatnya.
"""
from __future__ import annotations

import html
import os
import random
import re
from collections.abc import Callable
from dataclasses import dataclass

import requests

from . import cancel as cancelmod
from . import config

API_URL = "https://api.telegram.org/bot{token}/{method}"
TEXT_LIMIT = 4000              # resmi 4096 (setelah entity diproses); diberi margin
CAPTION_LIMIT = 1000           # resmi 1024
VIDEO_LIMIT = 50_000_000       # resmi "50 MB"
TIMEOUT = 30
VIDEO_TIMEOUT = (30, 600)
ATTEMPTS = 4
SEND_GAP = 1.1                 # ±1 pesan per detik per chat (FAQ Bot API)
MAX_WAIT = 60                  # tunggu retry_after paling lama ini; lebih dari itu = gagal
_sleep = cancelmod.sleep


class TelegramError(RuntimeError):
    """Galat Telegram. Pesannya aman ditampilkan (tidak memuat token)."""


def configured(settings: dict | None = None) -> bool:
    return bool(config.secret("telegram_token", settings) and config.secret("telegram_chat_id", settings))


def _token(settings: dict | None = None) -> str:
    tok = config.secret("telegram_token", settings).strip()
    if not tok:
        raise TelegramError("Token bot Telegram belum diisi (Pengaturan channel → Telegram).")
    if re.fullmatch(r"bot\d+:\S+", tok):
        tok = tok[3:]                           # tertempel bersama awalan "bot" dari contoh URL
    return tok


def _chat(settings: dict | None = None) -> str:
    chat = config.secret("telegram_chat_id", settings).strip()
    if not chat:
        raise TelegramError("Chat ID Telegram belum diisi. Tekan \"Cari chat ID\" di Pengaturan channel.")
    return chat


def _explain(status: int, desc: str) -> str:
    d = desc.lower()
    if "chat not found" in d:
        return "Chat ID Telegram salah, atau Anda belum menekan Start di bot. Tekan \"Cari chat ID\" lagi."
    if status in (401, 404) or "unauthorized" in d or d.strip() == "not found":
        # token yang salah bentuk (terpotong, dsb.) dibalas 404 Not Found
        return "Token bot Telegram salah. Salin ulang token dari @BotFather (tanpa awalan \"bot\")."
    if status == 403 or "blocked" in d or "can't initiate" in d:
        return "Bot belum boleh mengirim ke chat ini: buka bot di Telegram lalu tekan Start (atau buka blokirnya)."
    if status == 413 or "too large" in d:
        return "Berkas terlalu besar untuk bot Telegram (maks. 50 MB)."
    if "webhook" in d:
        return "Bot ini memakai webhook, jadi chat ID tidak bisa dibaca. Hapus webhook-nya atau buat bot baru."
    return f"Telegram menolak permintaan (HTTP {status}): {desc[:200] or 'tanpa keterangan'}"


class Multipart:
    """Badan multipart/form-data yang dibaca bertahap (berkas tidak dimuat utuh ke memori). Batal diperiksa setiap
    potongan, jadi upload video bisa dihentikan di tengah jalan."""

    CHUNK = 256 * 1024

    def __init__(self, fields: dict, file_field: str, path: str, mime: str, cancel=None):
        self.boundary = "nero-" + os.urandom(12).hex()
        head = b"".join(
            f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode() + str(v).encode()
            + b"\r\n" for k, v in fields.items())
        head += (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        self._head, self._tail = head, f"\r\n--{self.boundary}--\r\n".encode()
        self._size = os.path.getsize(path)
        self._fh = open(path, "rb")     # noqa: SIM115  ditutup close() setelah permintaan
        self._pos = 0
        self.cancel = cancel
        self.content_type = f"multipart/form-data; boundary={self.boundary}"

    def __len__(self) -> int:
        return len(self._head) + self._size + len(self._tail)

    def __iter__(self):                  # requests memperlakukan objek ber-__iter__ + read() sebagai aliran
        while chunk := self.read(self.CHUNK):
            yield chunk

    def read(self, n: int = -1) -> bytes:
        cancelmod.check_cancel(self.cancel)
        cancelmod.check_current()
        n = self.CHUNK if n is None or n < 0 else min(n, self.CHUNK)
        out = b""
        while len(out) < n:
            head_end = len(self._head)
            file_end = head_end + self._size
            want = n - len(out)
            if self._pos < head_end:
                part = self._head[self._pos:self._pos + want]
            elif self._pos < file_end:
                part = self._fh.read(want)
                if not part:
                    raise OSError("Berkas video berubah saat diunggah.")
            elif self._pos < len(self):
                part = self._tail[self._pos - file_end:self._pos - file_end + want]
            else:
                break
            out += part
            self._pos += len(part)
        return out

    def close(self) -> None:
        self._fh.close()


def _call(method: str, *, token: str, data: dict | None = None, stream: Callable[[], Multipart] | None = None,
          timeout=TIMEOUT, cancel=None, session=None, resend_safe: bool = True) -> dict | list | bool:
    """Satu panggilan Bot API dengan coba ulang (koneksi putus, 5xx, 429). `stream` = fungsi yang membuat badan
    multipart baru untuk setiap percobaan. resend_safe=False (video): bila hasilnya tidak pasti (koneksi putus setelah
    mulai mengirim, 5xx), tidak dicoba ulang supaya video tidak terkirim dua kali."""
    s = session or requests
    url = API_URL.format(token=token, method=method)
    last = ""
    unsure = "Kiriman video terputus dan mungkin sudah sampai. Cek Telegram sebelum mengirim ulang."
    for attempt in range(ATTEMPTS):
        cancelmod.check_cancel(cancel)
        cancelmod.check_current()
        body_stream = stream() if stream else None
        try:
            if body_stream is not None:
                r = s.post(url, data=body_stream, headers={"Content-Type": body_stream.content_type},
                           timeout=timeout)
            else:
                r = s.post(url, data=data, timeout=timeout)
        except requests.ConnectTimeout:
            last, r = "Telegram tidak bisa dihubungi (ConnectTimeout). Periksa koneksi internet.", None
        except requests.RequestException as e:
            if not resend_safe:
                raise TelegramError(unsure) from None
            last = f"Telegram tidak bisa dihubungi ({type(e).__name__}). Periksa koneksi internet."
            r = None
        finally:
            if body_stream is not None:
                body_stream.close()
        if r is not None:
            try:
                body = r.json()
            except ValueError:
                body = {}
            body = body if isinstance(body, dict) else {}
            if body.get("ok"):
                return body.get("result")
            params = body.get("parameters") or {}
            desc = str(body.get("description") or "")
            if r.status_code == 429 or params.get("retry_after"):
                wait = float(params.get("retry_after") or 5)
                if wait > MAX_WAIT:
                    raise TelegramError(f"Telegram meminta menunggu {wait:.0f} detik (terlalu sering mengirim). "
                                        "Coba lagi nanti.")
                if attempt < ATTEMPTS - 1:
                    _wait(wait, cancel)
                    continue
                raise TelegramError("Telegram terus meminta menunggu (terlalu sering mengirim). Coba lagi nanti.")
            if r.status_code < 500:
                raise TelegramError(_explain(r.status_code, desc))
            if not resend_safe:
                raise TelegramError(unsure)
            last = f"Server Telegram sedang bermasalah (HTTP {r.status_code})."
        if attempt < ATTEMPTS - 1:
            _wait(min(30.0, 2.0 ** attempt) + random.random(), cancel)
    raise TelegramError(last or "Telegram tidak bisa dihubungi.")


def _wait(seconds: float, cancel=None) -> None:
    if cancel is not None:
        if cancel.wait(max(0.0, seconds)):
            raise cancelmod.Cancelled()
    else:
        _sleep(seconds)


# --------------------------------------------------------------------------
# Teks
# --------------------------------------------------------------------------
def escape(text) -> str:
    """Escape untuk parse_mode HTML (< > & dan tanda kutip)."""
    return html.escape(str(text), quote=True)


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_text(text: str, limit: int) -> list[str]:
    """Pecah teks polos per baris supaya tiap bagian ≤ limit (UTF-16). Baris yang sangat panjang dipotong."""
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while utf16_len(line) > limit:                       # baris tunggal yang terlalu panjang
            cut = limit
            while utf16_len(line[:cut]) > limit:
                cut -= 1
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:cut])
            line = line[cut:]
        nxt = f"{cur}\n{line}" if cur else line
        if utf16_len(nxt) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = nxt
    if cur.strip():
        parts.append(cur)
    return [p for p in parts if p.strip()] or [""]


def pre_blocks(title: str, text: str) -> list[str]:
    """Teks panjang sebagai beberapa pesan <pre> (mudah disalin di ponsel), judul tebal di pesan pertama."""
    head = f"<b>{escape(title)}</b>\n"
    room = TEXT_LIMIT - utf16_len(head) - 20
    chunks = split_text(text, room // 2)                   # escape bisa memperpanjang teks; beri ruang
    out = []
    for i, c in enumerate(chunks):
        body = f"<pre>{escape(c)}</pre>"
        out.append((head if i == 0 else "") + body)
    return out


@dataclass
class Item:
    """Satu kiriman: kind (untuk mencatat yang sudah terkirim), teks HTML, atau video + caption HTML."""
    kind: str
    text: str = ""
    video: str = ""


# --------------------------------------------------------------------------
# Kirim
# --------------------------------------------------------------------------
def get_me(settings: dict | None = None, *, session=None) -> dict:
    """Profil bot (menguji token)."""
    res = _call("getMe", token=_token(settings), session=session)
    return res if isinstance(res, dict) else {}


def find_chats(settings: dict | None = None, *, session=None) -> list[dict]:
    """Chat yang baru mengirim pesan ke bot (24 jam terakhir): [{id, type, name}]. Pengguna menekan Start dulu."""
    res = _call("getUpdates", token=_token(settings), data={"limit": 100}, session=session)
    out: dict[str, dict] = {}
    for upd in res if isinstance(res, list) else []:
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or upd.get("my_chat_member")
        chat = (msg or {}).get("chat") or {}
        if "id" not in chat:
            continue
        name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
        if chat.get("username"):
            name = f"{name} (@{chat['username']})".strip()
        out[str(chat["id"])] = {"id": str(chat["id"]), "type": chat.get("type", ""), "name": name or "-"}
    return list(out.values())


def send_text(text: str, *, settings: dict | None = None, cancel=None, session=None) -> None:
    """Kirim satu pesan HTML. Bila Telegram menolak format HTML-nya, dikirim ulang sebagai teks polos."""
    token, chat = _token(settings), _chat(settings)
    data = {"chat_id": chat, "text": text, "parse_mode": "HTML",
            "link_preview_options": '{"is_disabled": true}'}
    try:
        _call("sendMessage", token=token, data=data, cancel=cancel, session=session)
    except TelegramError as e:
        if "parse" not in str(e).lower() and "entit" not in str(e).lower():
            raise
        plain = html.unescape(text.replace("<pre>", "").replace("</pre>", "").replace("<b>", "").replace("</b>", ""))
        _call("sendMessage", token=token, data={"chat_id": chat, "text": plain[:TEXT_LIMIT]}, cancel=cancel,
              session=session)


def send_video(path: str, caption: str, *, settings: dict | None = None, cancel=None, session=None) -> None:
    token, chat = _token(settings), _chat(settings)
    size = os.path.getsize(path)
    if size > VIDEO_LIMIT:
        raise TelegramError(f"Video {size / 1e6:.0f} MB, lebih dari batas bot Telegram (50 MB).")
    fields = {"chat_id": chat, "caption": caption, "parse_mode": "HTML", "supports_streaming": "true",
              "width": "1080", "height": "1920"}
    _call("sendVideo", token=token, stream=lambda: Multipart(fields, "video", path, "video/mp4", cancel),
          timeout=VIDEO_TIMEOUT, cancel=cancel, session=session, resend_safe=False)


def send_items(items: list[Item], *, settings: dict | None = None, done: set[str] | None = None,
               on_sent: Callable[[str], None] | None = None, cancel=None, progress=None, log=print,
               session=None) -> list[str]:
    """Kirim berurutan; kind yang ada di `done` dilewati (melanjutkan kiriman yang terputus). Hasil: kind yang
    terkirim sekarang. `on_sent(kind)` dipanggil setelah tiap kind selesai (untuk dicatat)."""
    done = set(done or ())
    sent: list[str] = []
    todo = [it for it in items if it.kind not in done]
    for i, it in enumerate(todo):
        if i:
            _wait(SEND_GAP, cancel)
        if it.video:
            try:
                send_video(it.video, it.text, settings=settings, cancel=cancel, session=session)
            except TelegramError as e:
                if "50 MB" not in str(e):
                    raise
                log(f"telegram: {e} Dikirim sebagai pesan.")
                send_text(it.text + "\n\n" + escape(f"{e} Videonya ada di Folder hasil Nero."), settings=settings,
                          cancel=cancel, session=session)
        else:
            send_text(it.text, settings=settings, cancel=cancel, session=session)
        sent.append(it.kind)
        if on_sent:
            on_sent(it.kind)
        if progress:
            progress((i + 1) / len(todo))
    return sent


def tail_text(lines: list[str], limit: int) -> str:
    """Baris log terakhir yang muat dalam `limit` karakter."""
    out: list[str] = []
    for line in reversed(lines):
        if utf16_len(escape("\n".join([line, *out]))) > limit:
            break
        out.insert(0, line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# Isi pesan
# --------------------------------------------------------------------------
FORMAT_NAMES = {"myth_vs_fact": "Mitos vs fakta", "list_3": "Tiga poin", "story": "Cerita", "question": "Pertanyaan",
                "explainer": "Penjelasan"}
PRIVACY_NAMES = {"private": "private", "public": "publik", "unlisted": "tidak publik"}


def script_text(script) -> str:
    """Naskah lengkap sebagai teks polos: hook, segmen, ajakan."""
    parts = [script.hook.strip(), *[s.text.strip() for s in script.segments], script.cta.strip()]
    return "\n\n".join(p for p in parts if p)


def compose_run(*, day: str, script, meta=None, qc=None, render=None, youtube=None, captions=None,
                video: str = "", approval: str = "manual", topic: str = "", youtube_stale: bool = False) -> list[Item]:
    """Paket harian (SPEC 6.11): ringkasan, video, caption TikTok, caption Instagram, naskah lengkap."""
    title = meta.title if meta is not None else script.topic
    lines = [f"<b>Channel Otomatis · {escape(day)}</b>", f"<b>{escape(title)}</b>", "",
             f"Topik: {escape(topic or script.topic)}",
             f"Format: {escape(FORMAT_NAMES.get(script.format, script.format))}"]
    if qc is not None:
        lines.append(f"QC: {qc.score:g}/10 ({'lulus' if qc.passed else 'tidak lulus'})")
    if render is not None:
        lines.append(f"Durasi: {render.duration:.1f} dtk".replace(".", ","))
        if render.needs_review:
            lines.append(f"<b>Perlu dicek sebelum diunggah:</b> {escape('; '.join(render.warnings))}")
    else:
        lines.append("Video: belum ada atau belum dibuat ulang untuk naskah ini")
    lines.append("")
    if youtube is not None and youtube.video_id:
        state_ = PRIVACY_NAMES.get(youtube.privacy, youtube.privacy)
        if youtube.publish_at_wib:
            state_ += f", tayang otomatis {youtube.publish_at_wib}"
        lines.append(f"YouTube: {escape(state_)} · <a href=\"{escape(youtube.studio_url)}\">buka di YouTube Studio</a>")
        if youtube.status == "masalah":
            lines.append(f"<b>PERINGATAN YouTube:</b> {escape(youtube.note)}")
        if approval == "review_window" and youtube.publish_at:
            lines.append("Kalau tidak cocok, ubah ke Private atau hapus di Studio sebelum jam tayang.")
    elif youtube_stale:
        lines.append("YouTube: video yang sudah diunggah untuk tanggal ini dibuat dari naskah sebelumnya (tidak "
                     "diunggah ulang). Cek di YouTube Studio.")
    else:
        lines.append("YouTube: belum diunggah. Unggah sendiri memakai judul & deskripsi di halaman Channel, "
                     "atau tekan Unggah uji (private) di Nero.")
    if captions is not None:
        lines += ["", "<b>Saat mengunggah ke TikTok & Instagram:</b>",
                  "TikTok: " + ("nyalakan \"AI-generated content\" (ada gambar AI yang realistis)."
                                if captions.tiktok_ai_label else "label AI tidak perlu (tanpa gambar AI)."),
                  "Instagram: " + ("nyalakan \"Add AI label\" (narasi memakai suara AI)."
                                   if captions.instagram_ai_label else "label AI tidak perlu.")]
    summary = "\n".join(lines)
    if utf16_len(summary) > TEXT_LIMIT:
        summary = summary[:TEXT_LIMIT]
    items = [Item("ringkasan", summary)]
    if video:
        cap = f"<b>{escape(title)}</b>"
        items.append(Item("video", cap if utf16_len(cap) <= CAPTION_LIMIT else escape(title[:300]), video))
    if captions is not None:
        for kind, label, text in (("tiktok", "Caption TikTok", captions.tiktok),
                                  ("instagram", "Caption Instagram", captions.instagram)):
            for i, block in enumerate(pre_blocks(label, text)):
                items.append(Item(kind if i == 0 else f"{kind}#{i + 1}", block))
    for i, block in enumerate(pre_blocks("Naskah", script_text(script))):
        items.append(Item("naskah" if i == 0 else f"naskah#{i + 1}", block))
    return items


def alert_items(*, day: str, step: str, error: str, log_lines: list[str]) -> list[Item]:
    """Peringatan kegagalan (SPEC 6.11): nama langkah + 30 baris log terakhir."""
    head = (f"<b>Channel Otomatis GAGAL · {escape(day)}</b>\nLangkah: {escape(step)}\n"
            f"{escape(str(error)[:800])}\n\n")
    tail = tail_text(log_lines[-30:], TEXT_LIMIT - utf16_len(head) - 20)
    return [Item("alert", head + (f"<pre>{escape(tail)}</pre>" if tail else ""))]


def plain(text: str) -> str:
    """Isi pesan HTML sebagai teks polos (untuk log uji coba)."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))
