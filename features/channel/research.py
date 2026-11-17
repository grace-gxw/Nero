"""
Nero · Channel Otomatis - agen riset: kumpulkan bahan, minta kandidat topik, buang duplikat/terlarang, pilih satu.

Bahan riset:
  - Wikipedia (id + en) lewat Action API (pencarian + TextExtracts dalam satu permintaan), untuk mode
    evergreen. Kata kunci pencariannya diusulkan LLM (prompts/research_queries.md) sesuai niche.
  - RSS dari pengaturan (item dalam lookback_hours terakhir).
  - YouTube search (tema yang sedang diminati) menyusul di Fase I-5 karena butuh kredensial YouTube.

Setiap kandidat wajib menunjuk nomor bahan riset sebagai sumber; URL sumber selalu diambil dari bahan itu
(bukan dari tulisan LLM), jadi sumber tidak bisa dikarang.
"""
from __future__ import annotations

import calendar
import html
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import unquote, urlsplit

import requests
from rapidfuzz import fuzz

from . import cancel, llm, memory
from .config import ChannelConfig
from .schemas import Candidate, DuplicateCheck, QueryPlan, Research, ResearchDraft, Source

HEADERS = {"User-Agent": "Nero/1.0 (aplikasi desktop lokal)"}
TIMEOUT = 15
DUP_RATIO = 80              # rapidfuzz token_set_ratio ≥ ini = topik dianggap sama dengan video lama
HISTORY_DAYS = 60
MAX_MATERIALS = 24
SNIPPET_CANDIDATE = 900     # karakter per bahan saat memilih topik
SNIPPET_CHOSEN = 4000       # karakter per sumber topik terpilih (bahan skrip & QC)
MIN_CANDIDATES = 3


class ResearchError(RuntimeError):
    """Riset tidak menghasilkan topik yang bisa dipakai."""


_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"^=+\s*(.*?)\s*=+$", re.MULTILINE)


def clean_text(text: str) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = _HEADING.sub(r"\1.", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Wikipedia (Action API: satu permintaan per pencarian, ringkasan artikel ikut terambil)
# --------------------------------------------------------------------------
REQUEST_GAP = 0.6        # jeda antar-permintaan (detik): Wikipedia membalas 429 bila terlalu rapat
_sleep = cancel.sleep    # berhenti seketika bila run dibatalkan; diganti di tes
_PAGE_PROPS = {"prop": "extracts|info|pageprops", "explaintext": 1, "inprop": "url", "ppprop": "disambiguation",
               "redirects": 1}


def _wiki_get(session, lang: str, params: dict) -> dict:
    http = session or requests
    for attempt in range(4):
        cancel.check_current()
        _sleep(REQUEST_GAP)
        r = http.get(f"https://{lang}.wikipedia.org/w/api.php", headers=HEADERS, timeout=TIMEOUT,
                     params={"action": "query", **params, "format": "json", "formatversion": 2})
        if r.status_code == 429 and attempt < 3:
            try:
                wait = float(r.headers.get("Retry-After") or "")
            except ValueError:
                wait = 2.0 * 2 ** attempt
            _sleep(min(30.0, wait))
            continue
        r.raise_for_status()
        return r.json()
    return {}


def _page_source(lang: str, p: dict, chars: int) -> Source | None:
    """Sumber dari satu halaman hasil API; None untuk halaman yang tidak ada, disambiguasi, atau terlalu pendek."""
    if p.get("missing") or "disambiguation" in (p.get("pageprops") or {}):
        return None
    text = clean_text(p.get("extract") or "")[:chars]
    if len(text) < 80 or not p.get("fullurl"):
        return None
    return Source(title=f"{p.get('title', '')} (Wikipedia {lang})", url=p["fullurl"],
                  published=str(p.get("touched") or "")[:10], snippet=text)


def wiki_search(lang: str, query: str, limit: int = 2, chars: int = SNIPPET_CANDIDATE, session=None) -> list[Source]:
    """Artikel teratas untuk pencarian ini beserta paragraf pembukanya."""
    data = _wiki_get(session, lang, {"generator": "search", "gsrsearch": query, "gsrlimit": limit,
                                     "gsrnamespace": 0, "exintro": 1, "exlimit": "max", **_PAGE_PROPS})
    pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
    return [s for p in pages if (s := _page_source(lang, p, chars))]


def wiki_extract(lang: str, title: str, chars: int = SNIPPET_CHOSEN, session=None) -> Source | None:
    """Teks artikel yang lebih panjang (bukan hanya pembuka) untuk topik terpilih. TextExtracts membatasi
    exchars ±1.200 karakter, jadi teks penuh diambil lalu dipotong di sini."""
    data = _wiki_get(session, lang, {"titles": title, **_PAGE_PROPS})
    pages = (data.get("query") or {}).get("pages") or []
    return _page_source(lang, pages[0], chars) if pages else None


def wikipedia_materials(queries, per_query: int = 2, chars: int = SNIPPET_CANDIDATE, log=print) -> list[Source]:
    out: list[Source] = []
    seen: set[str] = set()
    with requests.Session() as session:
        for q in queries:
            wanted = [("id", q.id, per_query)] + ([("en", q.en, 1)] if q.en.strip() else [])
            for lang, text, limit in wanted:
                try:
                    found = wiki_search(lang, text, limit, chars, session)
                except (requests.RequestException, ValueError) as e:
                    log(f"riset: pencarian Wikipedia {lang} '{text}' gagal ({e})")
                    continue
                for src in found:
                    if src.url not in seen:
                        seen.add(src.url)
                        out.append(src)
                if len(out) >= MAX_MATERIALS:
                    return out[:MAX_MATERIALS]
    return out


def _wiki_ref(url: str) -> tuple[str, str] | None:
    """(bahasa, judul) dari alamat artikel Wikipedia."""
    parts = urlsplit(url)
    if not parts.netloc.endswith(".wikipedia.org") or not parts.path.startswith("/wiki/"):
        return None
    return parts.netloc.split(".")[0], unquote(parts.path[len("/wiki/"):]).replace("_", " ")


def enrich(candidate: Candidate, log=print) -> Candidate:
    """Sumber topik terpilih diambil lebih panjang, supaya skrip & QC punya cukup fakta untuk dicocokkan."""
    out = candidate.model_copy(deep=True)
    for i, src in enumerate(out.sources):
        ref = _wiki_ref(src.url)
        if not ref:
            continue
        try:
            longer = wiki_extract(ref[0], ref[1], SNIPPET_CHOSEN)
        except (requests.RequestException, ValueError) as e:
            log(f"riset: sumber '{src.title}' tidak bisa diperpanjang ({e})")
            continue
        if longer and len(longer.snippet) > len(src.snippet):
            out.sources[i] = src.model_copy(update={"snippet": longer.snippet})
    return out


# --------------------------------------------------------------------------
# RSS
# --------------------------------------------------------------------------
def rss_materials(feeds: list[str], lookback_hours: int, now: datetime | None = None, log=print) -> list[Source]:
    import feedparser

    now = now or datetime.now(UTC)
    since = now - timedelta(hours=lookback_hours)
    out: list[Source] = []
    for url in feeds:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            log(f"riset: RSS {url} gagal ({e})")
            continue
        for entry in feedparser.parse(r.content).entries:
            stamp = entry.get("published_parsed") or entry.get("updated_parsed")
            if not stamp or not entry.get("link"):
                continue
            when = datetime.fromtimestamp(calendar.timegm(stamp), UTC)
            if when < since:
                continue
            out.append(Source(title=clean_text(entry.get("title", ""))[:200], url=entry["link"],
                              published=when.date().isoformat(),
                              snippet=clean_text(entry.get("summary", ""))[:SNIPPET_CANDIDATE]))
    return out


# --------------------------------------------------------------------------
# Memilih topik
# --------------------------------------------------------------------------
def duplicate_of(topic: str, past_topics: list[str], ratio: int = DUP_RATIO) -> str | None:
    for past in past_topics:
        if fuzz.token_set_ratio(topic.lower(), past.lower()) >= ratio:
            return past
    return None


def banned_hit(text: str, banned: list[str]) -> str | None:
    low = text.lower()
    for term in banned:
        if term.strip() and re.search(rf"(?<!\w){re.escape(term.lower().strip())}(?!\w)", low):
            return term
    return None


def build_candidates(draft: ResearchDraft, materials: list[Source]) -> list[Candidate]:
    """Kandidat dengan sumber dari bahan riset (nomor 1..n). Kandidat tanpa sumber yang sah dibuang."""
    out = []
    for d in draft.candidates:
        ids = [i for i in dict.fromkeys(d.source_ids) if 1 <= i <= len(materials)]
        if not ids:
            continue
        out.append(Candidate(topic=d.topic.strip(), angle=d.angle.strip(), why_now=d.why_now or "evergreen",
                             sources=[materials[i - 1] for i in ids], scores=d.scores))
    return out


def choose(candidates: list[Candidate]) -> int:
    return max(range(len(candidates)), key=lambda i: (candidates[i].scores.total, -i))


def _numbered(materials: list[Source]) -> list[dict]:
    return [{"n": i + 1, "title": m.title, "url": m.url, "published": m.published,
             "snippet": m.snippet[:SNIPPET_CANDIDATE]} for i, m in enumerate(materials)]


def run(cfg: ChannelConfig, today: date, *, settings: dict | None = None, learnings: str = "",
        avoid_topics: list[str] = (), log=print) -> Research:
    """avoid_topics: topik yang juga harus dihindari (mis. topik hari ini sebelum riset diulang)."""
    recent = [*memory.recent_topics(today, HISTORY_DAYS), *[t for t in avoid_topics if t]]
    from . import youtube  # sinyal minat dari YouTube (opsional, hanya bila kata kunci diisi & YouTube terhubung)
    trends = youtube.trending_titles(cfg, settings=settings, log=log)
    common_vars = {"niche": cfg.channel.niche, "audience": cfg.channel.audience, "tone": cfg.channel.tone,
                   "recent_topics": recent, "learnings": learnings, "banned_topics": cfg.channel.banned_topics,
                   "today": today.isoformat(), "evergreen": cfg.research.evergreen_mode, "trends": trends}
    ask = {"cfg": cfg, "settings": settings, "log": log}

    materials: list[Source] = []
    if cfg.research.rss_feeds:
        materials += rss_materials(cfg.research.rss_feeds, cfg.research.lookback_hours, log=log)
        log(f"riset: {len(materials)} item RSS baru")
    if cfg.research.evergreen_mode or not materials:
        plan = llm.generate_json("research_queries", {**common_vars, "n": 6}, QueryPlan,
                                 cfg.llm.temperature_creative, **ask)
        log("riset: kata kunci " + ", ".join(q.id for q in plan.queries))
        materials += wikipedia_materials(plan.queries, log=log)
    materials = materials[:MAX_MATERIALS]
    if not materials:
        raise ResearchError("Tidak ada bahan riset yang bisa dibaca (Wikipedia/RSS). Periksa koneksi internet.")
    log(f"riset: {len(materials)} bahan terkumpul")

    def check(draft: ResearchDraft) -> list[str]:
        good = build_candidates(draft, materials)
        if len(good) < MIN_CANDIDATES:
            return [(f"hanya {len(good)} kandidat yang menunjuk nomor bahan riset yang sah (1–{len(materials)}); "
                     f"buat {MIN_CANDIDATES}–5 kandidat dan isi source_ids dengan nomor bahan")]
        return []

    def usable(draft: ResearchDraft) -> bool:
        # tanpa satu pun kandidat bersumber riset pasti gagal: AI berikutnya dicoba (15 Nov 2026: Gemini ringan
        # yang sedang penuh dua kali menjawab tanpa nomor bahan yang sah, padahal Groq tersedia)
        return bool(build_candidates(draft, materials))

    draft = llm.generate_json("research", {**common_vars, "materials": _numbered(materials), "n": 5},
                              ResearchDraft, cfg.llm.temperature_creative, check=check, usable=usable, **ask)
    candidates = build_candidates(draft, materials)
    rejected, keep = [], []
    for c in candidates:
        hit = banned_hit(f"{c.topic} {c.angle}", cfg.channel.banned_topics)
        dup = None if hit else duplicate_of(c.topic, recent)
        if hit:
            rejected.append(f"{c.topic} (topik terlarang: {hit})")
        elif dup:
            rejected.append(f"{c.topic} (mirip video lama: {dup})")
        else:
            keep.append(c)
    if keep and recent:
        same = llm.generate_json("dedupe", {"candidates": [c.topic for c in keep], "recent_topics": recent},
                                 DuplicateCheck, cfg.llm.temperature_strict, **ask)
        drop = {i for i in same.duplicate_ids if 1 <= i <= len(keep)}
        rejected += [f"{keep[i - 1].topic} (sama maknanya dengan video lama)" for i in sorted(drop)]
        keep = [c for i, c in enumerate(keep, 1) if i not in drop]
    if not keep:
        raise ResearchError("Semua kandidat topik ditolak: " + "; ".join(rejected or ["tidak ada yang bersumber"]))
    idx = choose(keep)
    keep[idx] = enrich(keep[idx], log)
    log(f"riset: terpilih '{keep[idx].topic}' (skor {keep[idx].scores.total:g}), {len(rejected)} ditolak")
    return Research(date=today.isoformat(), candidates=keep, chosen_index=idx, rejected_as_duplicate=rejected)
