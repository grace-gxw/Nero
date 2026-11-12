"""
Nero · Channel Otomatis - agen analis (SPEC 6.2): performa video buatan Nero, learnings.md, dan laporan mingguan.

    perf, did = run(cfg, settings, today, force=False, log=print)

Alur (sekali sehari; run harian memanggilnya paling awal, tombol "Perbarui analisis" di halaman juga bisa):
1. Video buatan Nero 28 hari terakhir: riwayat (history.json), youtube.json tiap run, dan video yang diunggah sendiri
   oleh pengguna (judulnya di daftar upload channel sama persis dengan judul buatan Nero untuk run itu).
2. YouTube Data API videos.list: statistik sekarang, privasi, dan waktu tayang. Video yang hilang dilewati.
3. YouTube Analytics API (tertunda 48–72 jam, tanggal waktu Pasifik): total channel per hari untuk tren mingguan, dan
   per video angka 7 hari pertama sejak tayang, supaya video lama dan baru dibandingkan dengan rentang yang sama.
4. Bila ≥ 5 video punya data 7 hari pertama yang lengkap dan pengguna mengizinkan statistik dikirim ke AI
   (analyst.share_with_ai), AI merangkumnya jadi learnings.md (maks. 15 poin; tiap pola didukung ≥ 2 video, dicek
   kode). Selain itu learnings.md menjelaskan kenapa belum ada pelajaran ("data belum cukup").

Aturan YouTube API Services (Developer Policies, dicek 13 Nov 2026):
- Peringkat hanya memakai satu metrik mentah YouTube (engaged views 7 hari pertama); tidak ada skor gabungan.
- Statistik hanya disimpan untuk video di jendela analisis, diperbarui tiap analisis, dan dihapus bila izin YouTube
  dicabut, tidak berlaku, atau tidak terbukti berlaku selama 30 hari. Judul & teks lain dari YouTube tidak disimpan.
- Yang dikirim ke AI: nomor urut, label buatan Nero (judul, topik, format, hook, durasi), dan angka YouTube
  bertanggal; tanpa ID video/channel.

Analis tidak pernah menggagalkan run harian: galat dicatat sebagai peringatan dan pelajaran lama tetap dipakai.
"""
from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, llm, state, telegram, youtube
from .config import DAYS, ChannelConfig
from .runctx import RunContext, lock_active, sha
from .schemas import (
    AnalystDraft,
    ChannelWeek,
    Learning,
    Metadata,
    Performance,
    RenderInfo,
    Research,
    Script,
    VideoNumbers,
    VideoPerf,
    YouTubeResult,
)

WINDOW_DAYS = 28            # SPEC 6.2: video 28 hari terakhir
FIRST_DAYS = 7              # angka per video = 7 hari pertama sejak tayang
LATENCY_DAYS = 3            # data Analytics biasanya tertunda 48–72 jam
CHANNEL_DAYS = 28           # rentang total channel per hari (dua minggu + jeda data yang tertunda)
MIN_VIDEOS = 5              # SPEC 6.2: kurang dari 5 video = "data belum cukup"
MAX_POINTS = 15
MIN_EVIDENCE = 2            # satu pola didukung minimal 2 video
UPLOAD_SLACK_DAYS = 3       # video yang diunggah sendiri boleh tayang s.d. 3 hari sebelum tanggal run
AUTH_MAX_DAYS = 30          # izin YouTube wajib terbukti berlaku tiap 30 hari, atau data performa dihapus
TOP_N = 3
PACIFIC = ZoneInfo("America/Los_Angeles")
LOCK_FILE = "analyst.lock"

VIDEO_METRICS = ("engagedViews", "views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
                 "likes", "comments", "shares")
# averageViewPercentage bukan metrik inti (tidak dilindungi kebijakan deprecation): bila kombinasi ditolak, tanpa itu
CORE_VIDEO_METRICS = tuple(m for m in VIDEO_METRICS if m != "averageViewPercentage")
CHANNEL_METRICS = ("engagedViews", "views", "estimatedMinutesWatched", "subscribersGained", "subscribersLost")
FIELDS = {"engagedViews": "engaged_views", "views": "views", "estimatedMinutesWatched": "minutes_watched",
          "averageViewDuration": "avg_view_duration", "averageViewPercentage": "avg_view_pct", "likes": "likes",
          "comments": "comments", "shares": "shares", "subscribersGained": "subscribers_gained",
          "subscribersLost": "subscribers_lost"}
KIND_TITLES = {"berhasil": "Yang berhasil", "kurang": "Yang kurang berhasil", "saran": "Saran untuk video berikutnya"}
HEADING = "# Pelajaran performa channel"
# angka statistik di teks pelajaran (persen, ribuan, atau ≥ 3 digit): pelajaran berupa pola, bukan salinan angka
_STAT_NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s*%|\b\d{1,3}(?:[.,]\d{3})+\b|\b\d{3,}\b")


class AnalystBusy(RuntimeError):
    """Analis sedang berjalan di thread/proses lain."""


# --------------------------------------------------------------------------
# Pembantu
# --------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def pt_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(PACIFIC).date()


def _pt_day(text: str) -> date | None:
    dt = youtube._parse_time(text)
    return dt.astimezone(PACIFIC).date() if dt else None


def _num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


def norm_title(text: str) -> str:
    """Judul untuk dicocokkan: tanpa < > (dibuang saat diunggah), spasi dirapikan, huruf kecil."""
    return re.sub(r"\s+", " ", re.sub(r"[<>]", "", text or "")).strip().casefold()


def fmt_int(n: float | None) -> str:
    return "-" if n is None else f"{round(n):,}".replace(",", ".")


def fmt_day(d: str) -> str:
    try:
        x = date.fromisoformat(d)
    except ValueError:
        return d
    months = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
    return f"{x.day} {months[x.month - 1]} {x.year}"


def _lock_path() -> str:
    return state.data_path(LOCK_FILE)


def running() -> bool:
    return lock_active(_lock_path())


@contextlib.contextmanager
def guard():
    """Satu analis sekaligus di komputer ini (tombol halaman, run harian, CLI)."""
    path = _lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not running():
        with contextlib.suppress(OSError):
            os.remove(path)                           # sisa proses yang sudah mati
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AnalystBusy("Analisis performa sedang berjalan. Tunggu selesai dulu.") from None
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            state.remove_retry(path)


# --------------------------------------------------------------------------
# performance.json
# --------------------------------------------------------------------------
def load() -> Performance | None:
    """Hasil analis terakhir. Data yang izinnya tidak terbukti berlaku selama 30 hari dihapus (Developer Policies
    III.E.4.b), begitu juga data yang tersisa saat YouTube tidak lagi terhubung."""
    raw = state.load_performance()
    if not raw:
        return None
    try:
        perf = Performance.model_validate(raw)
    except ValueError:
        return None
    checked = youtube._parse_time(perf.auth_verified_at)
    here = str(youtube.account().get("channel_id") or "")
    if checked is None or datetime.now(UTC) - checked > timedelta(days=AUTH_MAX_DAYS) or not youtube.connected() \
            or (perf.channel_id and here and perf.channel_id != here):
        # izin tidak terbukti 30 hari, YouTube diputuskan, atau dihubungkan ke channel lain: data channel itu dihapus
        state.clear_performance()
        return None
    return perf


def _save(perf: Performance) -> None:
    state.save_performance(perf.model_dump(mode="json"))


# --------------------------------------------------------------------------
# Video buatan Nero
# --------------------------------------------------------------------------
@dataclass
class Found:
    video_id: str
    day: str
    source: str             # api | manual
    title: str = ""         # judul video itu (catatan Nero): label run dipakai hanya bila judul run-nya masih sama


def _run_days(since: date, until: date) -> list[date]:
    root = state.data_path("runs")
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        try:
            d = date.fromisoformat(name)
        except ValueError:
            continue
        if since <= d <= until and os.path.isdir(os.path.join(root, name)):
            out.append(d)
    return sorted(out)


def known_videos(today: date, channel_id: str) -> dict[str, Found]:
    """Video Nero yang ID-nya sudah tercatat (riwayat & youtube.json run), dari jendela 28 hari."""
    since = today - timedelta(days=WINDOW_DAYS)
    out: dict[str, Found] = {}
    other: set[str] = set()                   # video di channel lain (dihubungkan ulang ke channel berbeda)
    for d in _run_days(since, today + timedelta(days=7)):
        yt = RunContext(d, echo=None).load("youtube", YouTubeResult)
        if yt is None or not yt.video_id:
            continue
        if yt.channel_id and channel_id and yt.channel_id != channel_id:
            other.add(yt.video_id)
            continue
        out.setdefault(yt.video_id, Found(yt.video_id, d.isoformat(), "api", yt.title))
    for e in state.load_history():
        if e.video_id and e.date >= since.isoformat() and e.video_id not in out and e.video_id not in other:
            out[e.video_id] = Found(e.video_id, e.date, "manual", e.title)
    return out


def find_manual(today: date, known: dict[str, Found], uploads: list[dict], log,
                exclude: set[str] | frozenset = frozenset()) -> list[Found]:
    """Video yang diunggah sendiri: judul di daftar upload sama persis dengan judul buatan Nero untuk satu run (yang
    belum punya video yang masih ada di YouTube). Judul kembar (dua run atau dua video) dilewati supaya tidak salah
    pasang. `known` = video tercatat yang masih ada; `exclude` = semua ID yang sudah tercatat."""
    since = today - timedelta(days=WINDOW_DAYS)
    have_days = {f.day for f in known.values()}
    exclude = set(exclude) | set(known)
    by_title: dict[str, list[date]] = {}
    for d in _run_days(since, today + timedelta(days=7)):
        if d.isoformat() in have_days:
            continue
        if os.path.exists(RunContext(d, echo=None).path(youtube.SESSION_FILE)):
            # upload lewat Nero yang belum tercatat (terputus sebelum youtube.json): diselesaikan pemulihan upload
            # (Unggah uji / run berikutnya), bukan dianggap video yang diunggah sendiri
            log(f"analis: upload Nero untuk run {d.isoformat()} belum tercatat; tidak dipasangkan sebagai unggahan "
                "sendiri")
            continue
        meta = RunContext(d, echo=None).load("metadata", Metadata)
        if meta is not None and norm_title(meta.title):
            by_title.setdefault(norm_title(meta.title), []).append(d)
    matches: dict[str, list[dict]] = {}
    for up in uploads:
        t = norm_title(up.get("title", ""))
        if up.get("video_id") and up["video_id"] not in exclude and t in by_title:
            matches.setdefault(t, []).append(up)
    out = []
    for t, ups in matches.items():
        days = by_title[t]
        if len(days) > 1 or len({u["video_id"] for u in ups}) > 1:
            log(f"analis: judul '{t[:60]}' cocok dengan lebih dari satu run/video; tidak dipasangkan")
            continue
        when = youtube._parse_time(ups[0].get("published"))
        if when is not None and when.date() < days[0] - timedelta(days=UPLOAD_SLACK_DAYS):
            continue                     # diunggah jauh sebelum run itu dibuat: bukan video run ini
        meta = RunContext(days[0], echo=None).load("metadata", Metadata)
        out.append(Found(ups[0]["video_id"], days[0].isoformat(), "manual", meta.title if meta else ""))
    return out


def nero_labels(found: Found) -> dict:
    """Label buatan Nero (bukan data YouTube): dari riwayat bila lengkap, selain itu dari folder run, tetapi hanya
    bila judul run itu masih judul video ini (naskah yang ditulis ulang setelah diunggah bukan isi video)."""
    entry = next((e for e in state.load_history() if e.video_id == found.video_id), None)
    out = {"title": found.title, "topic": "", "format": "", "hook": "", "duration": 0.0}
    if entry is not None:
        out.update(title=entry.title or found.title, topic=entry.topic, format=entry.format, hook=entry.hook,
                   duration=entry.duration)
    if out["format"]:
        return out
    ctx = RunContext(date.fromisoformat(found.day), echo=None)
    script, meta = ctx.load("script", Script), ctx.load("metadata", Metadata)
    if script is None or meta is None or (out["title"] and norm_title(meta.title) != norm_title(out["title"])):
        return out
    info, res = ctx.load("render", RenderInfo), ctx.load("research", Research)
    out.update(title=meta.title, topic=res.chosen.topic if res else script.topic, format=script.format,
               hook=script.hook, duration=round(info.duration, 1) if info else 0.0)
    return out


# --------------------------------------------------------------------------
# YouTube Analytics
# --------------------------------------------------------------------------
def _numbers(row: dict | None) -> VideoNumbers | None:
    if not row:
        return None
    return VideoNumbers(**{FIELDS[k]: _num(v) for k, v in row.items() if k in FIELDS and FIELDS[k] in
                           VideoNumbers.model_fields})


def _week(rows: list[dict], start: date, end: date) -> ChannelWeek:
    picked = [r for r in rows if start.isoformat() <= str(r.get("day", "")) <= end.isoformat()]
    wk = ChannelWeek(start=start.isoformat(), end=end.isoformat(), days=len(picked))
    if picked:
        for metric in CHANNEL_METRICS:
            vals = [_num(r.get(metric)) for r in picked]
            vals = [v for v in vals if v is not None]
            setattr(wk, FIELDS[metric], sum(vals) if vals else None)
    return wk


class _Analytics:
    """Panggilan Analytics satu analisis: metrik per video turun ke metrik inti bila kombinasi ditolak (400)."""

    def __init__(self, settings: dict | None, log):
        self.settings, self.log = settings, log
        self.metrics = VIDEO_METRICS

    def channel(self, today_pt: date) -> tuple[list[dict], date, date]:
        """(baris harian, hari terakhir berdata, awal rentang yang diminta)."""
        start = today_pt - timedelta(days=CHANNEL_DAYS)
        rows = youtube.analytics_report({"startDate": start.isoformat(),
                                         "endDate": today_pt.isoformat(), "metrics": ",".join(CHANNEL_METRICS),
                                         "dimensions": "day", "sort": "day"}, settings=self.settings)
        days = []
        for r in rows:
            with contextlib.suppress(ValueError):
                days.append(date.fromisoformat(str(r.get("day", ""))))
        # rows hanya sampai hari terakhir yang semua metriknya sudah selesai diproses; tanpa baris sama sekali
        # (channel baru/sepi): anggap tertunda 3 hari
        through = max(days) if days else today_pt - timedelta(days=LATENCY_DAYS)
        return rows, min(through, today_pt - timedelta(days=1)), start

    def video(self, video_id: str, start: date, end: date) -> VideoNumbers | None:
        params = {"startDate": start.isoformat(), "endDate": end.isoformat(), "filters": f"video=={video_id}"}
        try:
            rows = youtube.analytics_report({**params, "metrics": ",".join(self.metrics)}, settings=self.settings)
        except youtube.YouTubeError as e:
            if e.status != 400 or self.metrics == CORE_VIDEO_METRICS:
                raise
            self.log("analis: kombinasi metrik ditolak YouTube Analytics; dicoba tanpa persentase tonton")
            self.metrics = CORE_VIDEO_METRICS
            rows = youtube.analytics_report({**params, "metrics": ",".join(self.metrics)}, settings=self.settings)
        return _numbers(rows[0] if rows else None)


# --------------------------------------------------------------------------
# Analisis
# --------------------------------------------------------------------------
NO_ANALYTICS_SCOPE = ("Izin melihat statistik (YouTube Analytics) belum diberikan. Hubungkan ulang YouTube dan centang "
                      "izin statistik.")
NO_ANALYTICS_SCOPE_ENV = ("Izin melihat statistik (YouTube Analytics) tidak ada di token YT_REFRESH_TOKEN. Buat refresh "
                          "token baru dengan izin yt-analytics.readonly.")


def _scope_note() -> str:
    """Catatan izin statistik: token dari environment variable tidak bisa dihubungkan ulang dari halaman."""
    return NO_ANALYTICS_SCOPE_ENV if config.key_from_env("yt_refresh_token") else NO_ANALYTICS_SCOPE
TRANSIENT_REASONS = {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded", "backendError"}


def _transient(e: youtube.YouTubeError) -> bool:
    """Galat sementara (koneksi, server, kuota): dicoba lagi di analisis berikutnya, bukan masalah pengaturan."""
    return e.status == 0 or e.status == 429 or e.status >= 500 or e.reason in TRANSIENT_REASONS


def _alive(resources: dict[str, dict], channel: str) -> dict[str, dict]:
    """Hasil videos.list yang milik channel terhubung (video di channel lain tidak dianalisis di sini)."""
    return {vid: item for vid, item in resources.items()
            if not channel or not (item.get("snippet") or {}).get("channelId")
            or item["snippet"]["channelId"] == channel}


def _cleared(log, reason: str) -> Performance:
    """Izin YouTube tidak ada/tidak berlaku: data performa dihapus, learnings.md menjelaskan alasannya."""
    had = os.path.exists(state.data_path("performance.json"))
    state.clear_performance()
    if had:
        log(f"analis: data performa YouTube dihapus ({reason})")
    perf = Performance(day="", learnings_state="belum_terhubung", notes=[reason])
    write_learnings(perf, reason=reason)
    return perf


def run(cfg: ChannelConfig, settings: dict | None, today: date, *, force: bool = False, log=print,
        progress: Callable[[float], None] | None = None,
        on_found: Callable[[Found], None] | None = None) -> tuple[Performance | None, bool]:
    """Analisis performa (sekali sehari kecuali force). Hasil: (performa, benar-benar dijalankan). Galat YouTube/AI
    dicatat, tidak dilempar; pembatalan tetap dilempar."""
    progress = progress or (lambda f: None)
    if not youtube.connected(settings):
        return _cleared(log, "YouTube belum dihubungkan, jadi performa video belum bisa dibaca"), True
    old = load()
    if old is not None and old.day == today.isoformat() and not force:
        log("lewati analyst: performa sudah dianalisis hari ini")
        if (old.learnings_state == "izin_ai") == cfg.analyst.share_with_ai:
            # izin AI diubah setelah analisis hari ini: pelajaran disusun/ditarik tanpa membaca YouTube lagi
            _learn(cfg, settings, old, old, log=log)
            _save(old)
            write_learnings(old)
        return old, False
    try:
        youtube.access_token(settings)              # izin masih berlaku (Developer Policies: dicek berkala)
    except youtube.AuthError as e:
        return _cleared(log, f"izin YouTube tidak berlaku: {e}"), True
    except youtube.YouTubeError as e:
        log(f"analis: PERINGATAN YouTube tidak bisa dihubungi ({e}); data performa sebelumnya dipakai")
        if old is None and not state.load_learnings():
            write_learnings(Performance(), reason="YouTube tidak bisa dihubungi saat analisis terakhir")
        return old, False
    try:
        perf = _analyze(cfg, settings, today, old, log=log, progress=progress, on_found=on_found)
    except youtube.AuthError as e:
        return _cleared(log, f"izin YouTube tidak berlaku: {e}"), True
    except youtube.YouTubeError as e:
        log(f"analis: PERINGATAN performa belum bisa dibaca ({e}); data sebelumnya dipakai")
        return old, False
    _learn(cfg, settings, perf, old, log=log)
    _save(perf)
    write_learnings(perf)
    progress(1.0)
    log(f"analis: {len(perf.videos)} video dibaca, {perf.mature} dengan data 7 hari pertama lengkap"
        + (f"; data YouTube Analytics s.d. {perf.data_through} (waktu Pasifik)" if perf.analytics else ""))
    return perf, True


def _analyze(cfg: ChannelConfig, settings: dict | None, today: date, old: Performance | None, *, log, progress,
             on_found) -> Performance:
    acct = youtube.account()
    checked = youtube._parse_time(acct.get("checked_at"))
    if not acct.get("uploads") or checked is None or \
            datetime.now(UTC) - checked > timedelta(days=youtube.ACCOUNT_REFRESH_DAYS):
        acct = youtube._save_account(**youtube.fetch_channel(settings=settings), checked_at=youtube._now_iso())
    channel = acct.get("channel_id", "")
    perf = Performance(day=today.isoformat(), updated_at=_now_iso(), auth_verified_at=_now_iso(), channel_id=channel)
    known = known_videos(today, channel)
    # video tercatat yang masih ada di YouTube (yang dihapus tidak menghalangi mengenali unggahan ulang run itu)
    resources = _alive(youtube.fetch_videos(list(known), settings=settings) if known else {}, channel)
    alive = {vid: f for vid, f in known.items() if vid in resources}
    for vid, f in sorted(known.items(), key=lambda kv: kv[1].day):
        if vid not in alive:
            log(f"analis: video run {f.day} tidak ada lagi di YouTube (dihapus?) atau ada di channel lain; dilewati")
    since = datetime.combine(today - timedelta(days=WINDOW_DAYS + UPLOAD_SLACK_DAYS), datetime.min.time(), UTC)
    uploads = youtube.list_uploads(acct.get("uploads", ""), since=since, settings=settings) \
        if acct.get("uploads") else []
    found = find_manual(today, alive, uploads, log, exclude=set(known))
    if found:
        resources.update(_alive(youtube.fetch_videos([f.video_id for f in found], settings=settings), channel))
    for f in found:
        if f.video_id not in resources:
            continue
        alive[f.video_id] = f
        log(f"analis: video yang diunggah sendiri dikenali untuk run {f.day} (judul sama)")
        if on_found is not None:
            try:
                on_found(f)
            except (OSError, ValueError) as e:
                log(f"analis: PERINGATAN riwayat untuk run {f.day} belum bisa dicatat ({e})")
    progress(0.3)
    for vid, f in sorted(alive.items(), key=lambda kv: kv[1].day):
        item = resources[vid]
        sn, st, stats = item.get("snippet") or {}, item.get("status") or {}, item.get("statistics") or {}
        perf.videos.append(VideoPerf(video_id=vid, day=f.day, source=f.source, **nero_labels(f),
                                     published_at=str(sn.get("publishedAt") or ""),
                                     privacy=str(st.get("privacyStatus") or ""),
                                     public_views=_int(stats.get("viewCount")),
                                     public_likes=_int(stats.get("likeCount")),
                                     public_comments=_int(stats.get("commentCount"))))
    progress(0.4)
    if not youtube.can_analytics():
        perf.notes.append(_scope_note())
    else:
        try:
            _fill_analytics(perf, settings, log, progress)
            perf.analytics = True
        except youtube.YouTubeError as e:
            if _transient(e):
                raise             # server/kuota/koneksi: data & pelajaran sebelumnya tetap dipakai, dicoba lagi nanti
            if isinstance(e, youtube.AuthError) and e.reason != "insufficientPermissions":
                raise
            perf.notes.append(_scope_note() if e.reason == "insufficientPermissions" else str(e))
            log(f"analis: PERINGATAN YouTube Analytics: {e}")
    for v in perf.videos:
        if v.privacy and v.privacy != "public":
            v.status, v.note = "belum_tayang", f"Video masih {telegram.PRIVACY_NAMES.get(v.privacy, v.privacy)}."
        elif not v.status:
            v.status = "belum_ada_data"
    perf.mature = sum(1 for v in perf.videos if v.status == "lengkap")
    return perf


def _fill_analytics(perf: Performance, settings: dict | None, log, progress) -> None:
    api = _Analytics(settings, log)
    today_pt = pt_today()
    rows, through, asked = api.channel(today_pt)
    perf.data_through = through.isoformat()
    perf.this_week = _week(rows, through - timedelta(days=6), through)
    # minggu pembanding hanya bila seluruhnya ada di rentang yang diminta (tidak membandingkan 7 hari dengan 5 hari)
    prev_start = through - timedelta(days=13)
    perf.prev_week = _week(rows, prev_start, through - timedelta(days=7)) if prev_start >= asked else None
    public = [v for v in perf.videos if v.privacy == "public"]
    for i, v in enumerate(public):
        progress(0.4 + 0.5 * i / max(1, len(public)))
        start = _pt_day(v.published_at)
        if start is None:
            continue
        full_end = start + timedelta(days=FIRST_DAYS - 1)
        v.window_start, v.window_end = start.isoformat(), full_end.isoformat()
        if start > through:
            v.status, v.note = "belum_ada_data", "Data YouTube Analytics belum ada (tertunda 48–72 jam)."
            continue
        v.first_week = api.video(v.video_id, start, min(full_end, through))
        v.complete = full_end <= through
        if v.first_week is None:
            v.status, v.note = "belum_ada_data", "YouTube Analytics belum punya data untuk video ini."
        elif v.complete:
            v.status = "lengkap"
        else:
            v.status, v.note = "sementara", f"7 hari pertama baru terisi s.d. {through.isoformat()}."


# --------------------------------------------------------------------------
# Pelajaran (learnings.md)
# --------------------------------------------------------------------------
def mature_videos(perf: Performance) -> list[VideoPerf]:
    return sorted((v for v in perf.videos if v.status == "lengkap" and v.first_week is not None),
                  key=lambda v: v.day)


def _metrics_line(v: VideoPerf) -> str:
    n = v.first_week or VideoNumbers()
    parts = [f"engaged views {fmt_int(n.engaged_views)}", f"views {fmt_int(n.views)}"]
    if n.avg_view_duration is not None:
        pct = f" ({n.avg_view_pct:.0f}% durasi video)" if n.avg_view_pct is not None else ""
        parts.append(f"rata-rata ditonton {n.avg_view_duration:.0f} dtk{pct}")
    for label, val in (("suka", n.likes), ("komentar", n.comments), ("dibagikan", n.shares)):
        if val is not None:
            parts.append(f"{label} {fmt_int(val)}")
    return f"7 hari pertama ({v.window_start} s.d. {v.window_end}, waktu Pasifik): " + ", ".join(parts)


def _averages(videos: list[VideoPerf]) -> str:
    def avg(attr):
        vals = [getattr(v.first_week, attr) for v in videos if getattr(v.first_week, attr) is not None]
        return sum(vals) / len(vals) if vals else None
    parts = [f"engaged views {fmt_int(avg('engaged_views'))}", f"views {fmt_int(avg('views'))}"]
    d, p = avg("avg_view_duration"), avg("avg_view_pct")
    if d is not None:
        parts.append(f"rata-rata ditonton {d:.0f} dtk" + (f" ({p:.0f}% durasi video)" if p is not None else ""))
    return ", ".join(parts)


def ai_payload(perf: Performance) -> tuple[dict, dict[int, str]]:
    """Variabel prompts/analyst.md dan peta nomor → ID video. Tanpa ID video/channel (data seminimal mungkin)."""
    videos = mature_videos(perf)
    numbered = {i + 1: v.video_id for i, v in enumerate(videos)}
    # tanggal tayang (waktu Pasifik, dari YouTube), bukan tanggal run: video bisa diunggah beberapa hari kemudian
    rows = [{"n": i + 1, "date": v.window_start or v.day,
             "format": telegram.FORMAT_NAMES.get(v.format, v.format or "-"),
             "duration": f"{v.duration:.0f} dtk" if v.duration else "-", "title": v.title or "-",
             "topic": v.topic or "-", "hook": v.hook or "-", "metrics": _metrics_line(v)}
            for i, v in enumerate(videos)]
    days = sorted(r["date"] for r in rows)
    period = f"tayang {fmt_day(days[0])}–{fmt_day(days[-1])}" if days else ""
    return {"videos": rows, "period": period, "averages": _averages(videos), "max_points": MAX_POINTS}, numbered


def _point_problems(draft: AnalystDraft, valid: set[int]) -> list[str]:
    out = []
    if len(draft.points) > MAX_POINTS:
        out.append(f"Maksimal {MAX_POINTS} poin; sekarang {len(draft.points)}.")
    for i, p in enumerate(draft.points, 1):
        bad = sorted(set(p.videos) - valid)
        if bad:
            out.append(f"Poin {i}: nomor video {bad} tidak ada di data.")
        need = MIN_EVIDENCE if p.kind in ("berhasil", "kurang") else 1
        if len(set(p.videos) & valid) < need:
            out.append(f"Poin {i} ({p.kind}): sebutkan minimal {need} nomor video pendukung.")
        if _STAT_NUMBER.search(p.text):
            out.append(f"Poin {i}: jangan menulis angka statistik; tulis polanya saja.")
    return out


def _learn(cfg: ChannelConfig, settings: dict | None, perf: Performance, old: Performance | None, *, log) -> None:
    videos = mature_videos(perf)
    if len(videos) < MIN_VIDEOS:
        perf.learnings, perf.learnings_state, perf.learnings_fp = [], "data_belum_cukup", ""
        return
    if not cfg.analyst.share_with_ai:
        perf.learnings, perf.learnings_state, perf.learnings_fp = [], "izin_ai", ""
        log("analis: pelajaran otomatis belum dibuat: izin mengirim statistik ke AI belum diberikan "
            "(Pengaturan channel → Analitik)")
        return
    variables, numbered = ai_payload(perf)
    fp = sha(variables, cfg.channel.niche, cfg.channel.audience, cfg.channel.banned_topics)
    if old is not None and old.learnings_state == "siap" and old.learnings_fp == fp:
        perf.learnings, perf.learnings_state, perf.learnings_fp, perf.learnings_at = \
            old.learnings, "siap", fp, old.learnings_at
        log("lewati analyst (AI): data performa sama dengan rangkuman sebelumnya")
        return
    valid = set(numbered)
    try:
        draft = llm.generate_json(
            "analyst", {**variables, "niche": cfg.channel.niche, "audience": cfg.channel.audience,
                        "banned_topics": cfg.channel.banned_topics, "min_evidence": MIN_EVIDENCE},
            AnalystDraft, cfg.llm.temperature_strict, cfg=cfg, settings=settings, log=log,
            check=lambda d: _point_problems(d, valid))
    except llm.LLMError as e:
        log(f"analis: PERINGATAN AI gagal merangkum ({e}); pelajaran sebelumnya tetap dipakai")
        if old is not None and old.learnings:
            perf.learnings, perf.learnings_fp, perf.learnings_at = old.learnings, old.learnings_fp, old.learnings_at
        perf.learnings_state = "gagal"
        return
    points, seen = [], set()
    for p in draft.points:
        need = MIN_EVIDENCE if p.kind in ("berhasil", "kurang") else 1
        refs = [n for n in dict.fromkeys(p.videos) if n in valid]
        text = " ".join(p.text.split())
        if len(refs) < need or _STAT_NUMBER.search(text) or text.casefold() in seen:
            continue                              # pola tanpa bukti cukup / berisi angka: dibuang, tidak ditebak
        seen.add(text.casefold())
        points.append(Learning(kind=p.kind, text=text, video_ids=[numbered[n] for n in refs]))
    perf.learnings = points[:MAX_POINTS]
    perf.learnings_state, perf.learnings_fp, perf.learnings_at = "siap", fp, _now_iso()
    log(f"analis: {len(perf.learnings)} pelajaran disusun AI (dari {len(videos)} video)")


def write_learnings(perf: Performance, reason: str = "") -> None:
    """learnings.md untuk prompt riset & naskah. Selalu ditulis, juga saat belum ada pelajaran (alasannya)."""
    lines = [HEADING, ""]
    st = perf.learnings_state
    if st in ("siap", "gagal") and perf.learnings:
        videos = mature_videos(perf)
        lines.append(f"Disusun {fmt_day((perf.learnings_at or perf.updated_at)[:10])} dari {len(videos)} video "
                     f"dengan data 7 hari pertama yang lengkap (YouTube Analytics s.d. {perf.data_through}, waktu "
                     "Pasifik). Judul, topik, format, dan hook dari Nero; pola dari angka YouTube.")
        for kind, title in KIND_TITLES.items():
            pts = [p.text for p in perf.learnings if p.kind == kind]
            if pts:
                lines += ["", f"## {title}", *[f"- {t}" for t in pts]]
    elif st == "siap":
        lines.append("Belum ada pola yang jelas: performa video-video terakhir mirip satu sama lain.")
    elif st == "izin_ai":
        lines.append("Pelajaran otomatis belum dibuat: izin mengirim statistik video ke AI belum diberikan "
                     "(Pengaturan channel → Analitik). Performa video tetap dibaca untuk halaman dan laporan mingguan.")
    elif st == "gagal":
        lines.append("Pelajaran belum bisa disusun: AI gagal merangkum data performa. Dicoba lagi di analisis "
                     "berikutnya.")
    elif st == "data_belum_cukup":
        lines.append(f"Data belum cukup: baru {perf.mature} video dengan data 7 hari pertama yang lengkap (butuh "
                     f"minimal {MIN_VIDEOS}). Pola belum disimpulkan supaya tidak menebak.")
    else:
        lines.append(f"Data belum cukup: {reason or 'performa video belum dibaca'}.")
    state.save_learnings("\n".join(lines))


def prompt_learnings(cfg: ChannelConfig) -> str:
    """Isi learnings.md untuk prompt AI: hanya bila berisi pelajaran (poin), izin AI masih menyala, dan datanya masih
    sah (load: terhubung, channel sama, izin terbukti ≤ 30 hari). Tanpa judul berkas."""
    if not cfg.analyst.share_with_ai or load() is None:
        return ""
    text = state.load_learnings()
    if not any(line.startswith("- ") for line in text.splitlines()):
        return ""
    return "\n".join(line for line in text.splitlines() if line.strip() and line.strip() != HEADING).strip()


# --------------------------------------------------------------------------
# Laporan mingguan (SPEC 6.2)
# --------------------------------------------------------------------------
def weekly_key(cfg: ChannelConfig, today: date) -> str:
    """Tanggal hari laporan terakhir (≤ hari ini): satu laporan per minggu, dikejar bila hari itu terlewat."""
    idx = DAYS.index(cfg.notify.weekly_report_day)
    return (today - timedelta(days=(today.weekday() - idx) % 7)).isoformat()


def weekly_due(cfg: ChannelConfig, today: date) -> bool:
    """Laporan minggu ini belum dikirim/ditangani. Dibandingkan sebagai tanggal (bukan sekadar beda), supaya mengganti
    hari laporan tidak membuat laporan yang baru terkirim dianggap terlewat."""
    return str(state.load_weekly().get("key") or "") < weekly_key(cfg, today)


def mark_weekly(cfg: ChannelConfig, today: date, *, skipped: str = "") -> None:
    state.save_weekly({"key": weekly_key(cfg, today), "sent_at": "" if skipped else _now_iso(),
                       "skipped": skipped})


def ranking(perf: Performance) -> tuple[list[VideoPerf], list[VideoPerf]]:
    """Top & bottom menurut SATU metrik mentah YouTube (engaged views 7 hari pertama), hanya video yang datanya
    lengkap. Bottom diambil dari sisa (tidak tumpang tindih dengan top)."""
    ok = sorted(mature_videos(perf), key=lambda v: (-(v.first_week.engaged_views or 0), v.day))
    top = ok[:TOP_N]
    rest = ok[len(top):]
    return top, list(reversed(rest[-TOP_N:])) if rest else []


def comparable(wk: ChannelWeek | None, pw: ChannelWeek | None) -> bool:
    """Dua minggu hanya dibandingkan (persen naik/turun) bila jumlah hari berdatanya sama."""
    return wk is not None and pw is not None and wk.days == pw.days == 7


def _change(cur: float | None, prev: float | None) -> str:
    if cur is None:
        return "-"
    if prev is None or prev <= 0:
        return fmt_int(cur)
    pct = (cur - prev) / prev * 100
    word = "naik" if pct >= 0.5 else "turun" if pct <= -0.5 else "sama"
    return f"{fmt_int(cur)} ({word} {abs(pct):.0f}% dari minggu sebelumnya)" if word != "sama" else \
        f"{fmt_int(cur)} (sama dengan minggu sebelumnya)"


def _video_line(i: int, v: VideoPerf) -> str:
    n = v.first_week or VideoNumbers()
    pct = f" · ditonton rata-rata {n.avg_view_pct:.0f}%" if n.avg_view_pct is not None else ""
    title = telegram.escape(v.title or v.topic or v.day)
    return (f"{i}. <a href=\"https://www.youtube.com/watch?v={telegram.escape(v.video_id)}\">{title}</a> "
            f"({telegram.escape(fmt_day(v.day))}) — {fmt_int(n.engaged_views)} engaged views{pct}")


def weekly_text(perf: Performance) -> str:
    """Isi laporan mingguan (HTML Telegram): tren seluruh channel, top 3, bottom 3."""
    e = telegram.escape
    lines = ["<b>Laporan mingguan · Channel Otomatis</b>"]
    if perf.analytics and perf.data_through:
        lines.append(e(f"Data YouTube Analytics s.d. {fmt_day(perf.data_through)} (waktu Pasifik; tertunda 48–72 jam)."))
    wk = perf.this_week
    pw = perf.prev_week if comparable(perf.this_week, perf.prev_week) else None
    if wk is not None and wk.days:
        days = "" if wk.days == 7 else f" ({wk.days} hari berdata)"
        lines += ["", e(f"Tren seluruh channel, {fmt_day(wk.start)}–{fmt_day(wk.end)}{days}:"),
                  e(f"Engaged views: {_change(wk.engaged_views, pw.engaged_views if pw else None)}"),
                  e(f"Views: {_change(wk.views, pw.views if pw else None)}"),
                  e(f"Menit ditonton: {_change(wk.minutes_watched, pw.minutes_watched if pw else None)}")]
        if wk.subscribers_gained is not None or wk.subscribers_lost is not None:
            lines.append(e(f"Pelanggan: +{fmt_int(wk.subscribers_gained or 0)} / −{fmt_int(wk.subscribers_lost or 0)}"))
    elif not perf.analytics:
        lines += ["", e("Tren belum ada: " + (perf.notes[0] if perf.notes else "YouTube Analytics belum terbaca."))]
    top, bottom = ranking(perf)
    if top:
        lines += ["", "<b>Teratas</b> (engaged views 7 hari pertama, video 28 hari terakhir)",
                  *[_video_line(i, v) for i, v in enumerate(top, 1)]]
    if bottom:
        lines += ["", "<b>Terbawah</b>", *[_video_line(i, v) for i, v in enumerate(bottom, 1)]]
    if not top:
        lines += ["", e("Belum ada video dengan data 7 hari pertama yang lengkap.")]
    pending = [v for v in perf.videos if v.status in ("sementara", "belum_ada_data")]
    if pending:
        lines.append(e(f"Video yang datanya belum lengkap: {len(pending)}."))
    public = [v.public_views for v in perf.videos if v.public_views is not None]
    if public:
        lines.append(e(f"Views publik saat ini (semua video di atas, termasuk putar ulang): {fmt_int(sum(public))}."))
    if perf.learnings:
        lines += ["", "<b>Pelajaran</b>", *[f"• {e(p.text)}" for p in perf.learnings[:3]]]
    lines += ["", e("Judul & tanggal dari Nero; angka dari YouTube.")]
    text = "\n".join(lines)
    return text if telegram.utf16_len(text) <= telegram.TEXT_LIMIT else text[:telegram.TEXT_LIMIT]


def weekly_items(perf: Performance) -> list[telegram.Item]:
    return [telegram.Item("laporan_mingguan", weekly_text(perf))]


# --------------------------------------------------------------------------
# Halaman
# --------------------------------------------------------------------------
def page_data(cfg: ChannelConfig, today: date) -> dict:
    perf = load()
    top, bottom = ranking(perf) if perf else ([], [])
    weekly = state.load_weekly()
    return {
        "performance": perf.model_dump(mode="json") if perf else None,
        "learnings": state.load_learnings(),
        "top": [v.video_id for v in top], "bottom": [v.video_id for v in bottom],
        "weekly": {"day": cfg.notify.weekly_report_day, "key": weekly_key(cfg, today),
                   "last_key": str(weekly.get("key") or ""), "sent_at": str(weekly.get("sent_at") or ""),
                   "due": weekly_due(cfg, today)},
        "share_with_ai": cfg.analyst.share_with_ai, "connected": youtube.connected(),
        "can_analytics": youtube.can_analytics(), "from_env": config.key_from_env("yt_refresh_token"),
        "running": running(), "comparable": comparable(perf.this_week, perf.prev_week) if perf else False,
        "min_videos": MIN_VIDEOS, "window_days": WINDOW_DAYS,
    }
