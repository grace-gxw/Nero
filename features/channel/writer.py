"""
Nero · Channel Otomatis - agen penulis skrip, QC, dan SEO.

  pick_format()       format berikutnya dalam rotasi, tidak sama dengan 2 video terakhir
  write_script()      skrip dari topik terpilih (prompts/script.md); bisa menulis ulang berdasar masalah QC
  review()            QC oleh LLM (prompts/qc.md) + pemeriksaan pasti oleh kode
  write_and_review()  tulis → QC → tulis ulang (maks. 2×)
  seo()               judul, ringkasan, tag, hashtag (prompts/seo.md) + deskripsi dari templat _description.md
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from . import config, llm
from .config import ChannelConfig
from .research import banned_hit
from .schemas import QC, Issue, Metadata, Research, Script, SeoDraft, count_words

HOOK_SIMILAR = 85        # token_set_ratio ≥ ini terhadap hook lama = terlalu mirip
HOOK_MAX_WORDS = 12
OSD_MAX_WORDS = 5        # on_screen_text
CODE_ISSUE_SCORE_CAP = 6.5   # masalah pasti dari kode (panjang, format, visual) memaksa tulis ulang
MAX_REWRITES = 2


class QCFailed(RuntimeError):
    def __init__(self, msg: str, qc: QC):
        super().__init__(msg)
        self.qc = qc


# --------------------------------------------------------------------------
# Format & pemeriksaan pasti
# --------------------------------------------------------------------------
def pick_format(rotation: list[str], recent_formats: list[str]) -> str:
    """Format berikutnya setelah format terakhir dalam rotasi, dan bukan salah satu dari 2 format terakhir."""
    rotation = list(rotation)
    blocked = set(recent_formats[-2:])
    allowed = [f for f in rotation if f not in blocked] or rotation
    if recent_formats and recent_formats[-1] in rotation:
        start = rotation.index(recent_formats[-1]) + 1
        for f in rotation[start:] + rotation[:start]:
            if f in allowed:
                return f
    return allowed[0]


def script_issues(script: Script, cfg: ChannelConfig, research: Research, fmt: str,
                  recent_hooks: list[str]) -> list[Issue]:
    """Masalah yang bisa dipastikan kode tanpa AI."""
    out: list[Issue] = []
    lo, hi = config.words_limits(cfg)
    if not lo <= script.word_count <= hi:
        fix = "tambah fakta/kalimat" if script.word_count < lo else "pendekkan kalimat atau buang satu segmen"
        out.append(Issue(type="length", detail=f"jumlah kata yang diucapkan {script.word_count}, harus {lo}–{hi}",
                         fix=fix))
    if script.format != fmt:
        out.append(Issue(type="repetition", detail=f"format harus {fmt} (bukan {script.format})",
                         fix=f"tulis dengan format {fmt}"))
    hook_words = count_words(script.hook)
    if hook_words > HOOK_MAX_WORDS:
        out.append(Issue(type="quality", detail=f"hook {hook_words} kata, maksimal {HOOK_MAX_WORDS}",
                         fix="padatkan hook"))
    long_osd = [s.id for s in script.segments if count_words(s.on_screen_text) > OSD_MAX_WORDS]
    if long_osd:
        out.append(Issue(type="quality", detail=f"on_screen_text segmen {long_osd} lebih dari {OSD_MAX_WORDS} kata",
                         fix="pendekkan teks layar"))
    types = [s.visual_type for s in script.segments]
    allowed = cfg.visuals.allowed_types
    bad_types = sorted({t for t in types if t not in allowed})
    if bad_types:
        out.append(Issue(type="quality", detail=f"jenis visual {', '.join(bad_types)} tidak diizinkan",
                         fix=f"pakai hanya {', '.join(allowed)}"))
    ai = types.count("ai_image")
    if types and ai / len(types) > cfg.visuals.max_ai_image_ratio:
        out.append(Issue(type="quality", detail=f"terlalu banyak segmen ai_image ({ai} dari {len(types)})",
                         fix=f"maksimal {int(cfg.visuals.max_ai_image_ratio * 100)}% segmen ai_image"))
    need = min(cfg.visuals.min_types_per_video, len(allowed))
    if len(set(types)) < need:
        out.append(Issue(type="quality", detail=f"hanya {len(set(types))} jenis visual",
                         fix=f"pakai minimal {need} jenis visual berbeda"))
    urls = {s.url for s in research.chosen.sources}
    if not script.claims:
        out.append(Issue(type="fact", detail="daftar claims kosong", fix="cantumkan setiap fakta beserta source_url"))
    unknown = [c.source_url for c in script.claims if c.source_url not in urls]
    if unknown:
        out.append(Issue(type="fact", detail="source_url bukan salah satu sumber riset: " + ", ".join(unknown[:3]),
                         fix="pakai hanya URL dari daftar sumber"))
    similar = next((h for h in recent_hooks if fuzz.token_set_ratio(h.lower(), script.hook.lower()) >= HOOK_SIMILAR),
                   None)
    if similar:
        out.append(Issue(type="repetition", detail=f"hook terlalu mirip hook lama: \"{similar}\"",
                         fix="ubah struktur hook (mis. pertanyaan, angka, pernyataan mengejutkan)"))
    spoken = " ".join(t for _, t in script.narration()) + " " + script.topic
    hit = banned_hit(spoken, cfg.channel.banned_topics)
    if hit:
        out.append(Issue(type="policy", detail=f"menyentuh topik terlarang: {hit}", fix="ganti topik/kalimat"))
    return out


# --------------------------------------------------------------------------
# Skrip
# --------------------------------------------------------------------------
def _sources(research: Research) -> list[dict]:
    return [{"title": s.title, "url": s.url, "snippet": s.snippet} for s in research.chosen.sources]


def write_script(cfg: ChannelConfig, research: Research, fmt: str, *, settings: dict | None = None,
                 learnings: str = "", recent_hooks: list[str] = (), issues: list[Issue] | None = None,
                 previous: Script | None = None, log=print) -> Script:
    c = research.chosen
    variables = {
        "niche": cfg.channel.niche, "audience": cfg.channel.audience, "tone": cfg.channel.tone,
        "banned_topics": cfg.channel.banned_topics, "format": fmt, "topic": c.topic, "angle": c.angle,
        "words_min": config.words_range(cfg)[0], "words_max": config.words_range(cfg)[1],
        "seconds_min": cfg.video.target_seconds[0], "seconds_max": cfg.video.target_seconds[1],
        "sources": _sources(research), "recent_hooks": list(recent_hooks), "learnings": learnings,
        "allowed_visuals": cfg.visuals.allowed_types, "max_ai_pct": int(cfg.visuals.max_ai_image_ratio * 100),
        "min_types": min(cfg.visuals.min_types_per_video, len(cfg.visuals.allowed_types)),
        "issues": [i.model_dump() for i in issues or []],
        "previous": previous.model_dump_json(indent=1) if previous else "",
    }

    def check(s: Script) -> list[str]:
        return [f"{i.detail} ({i.fix})" if i.fix else i.detail
                for i in script_issues(s, cfg, research, fmt, list(recent_hooks))]

    return llm.generate_json("script", variables, Script, cfg.llm.temperature_creative, cfg=cfg,
                             settings=settings, check=check, log=log)


# --------------------------------------------------------------------------
# QC
# --------------------------------------------------------------------------
EVIDENCE_MATCH = 85      # rapidfuzz partial_ratio kutipan bukti terhadap teks sumber
_DIGIT = re.compile(r"\d")
# angka yang menempel pada nama diri (Voyager 1, Apollo 11, COVID-19): bukan klaim jumlah
_NAMED_NUMBER = re.compile(r"\b(?:[A-Z][\w-]*\s+)+\d+[a-z]?\b|\b[A-Za-z]+-\d+\b")


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def evidence_found(evidence: str, sources) -> bool:
    """Kutipan bukti dari QC benar-benar ada di salah satu sumber (toleran beda spasi/huruf besar)."""
    ev = _norm(evidence)
    return len(ev) >= 12 and any(fuzz.partial_ratio(ev, _norm(s.snippet)) >= EVIDENCE_MATCH for s in sources)


def _where(segment_id: int) -> str:
    return {0: "hook", -1: "cta"}.get(segment_id, f"segmen {segment_id}")


def has_quantity(statement: str, research: Research) -> bool:
    """Pernyataan memuat angka yang bisa jadi klaim. Angka bagian nama diri yang tertulis di sumber/topik
    ("Benarkah Voyager 1 keluar dari tata surya?") tidak dihitung."""
    c = research.chosen
    known = " ".join([c.topic, *(f"{s.title} {s.snippet}" for s in c.sources)])
    known = " ".join(known.split())
    rest = statement
    for m in _NAMED_NUMBER.finditer(statement):
        parts = m.group(0).split()
        names = [" ".join(parts[i:]) for i in range(len(parts)) if len(parts) - i >= 2 or "-" in parts[i]]
        if any(n in known for n in names):
            rest = rest.replace(m.group(0), " ")
    return bool(_DIGIT.search(rest))


def check_issues(qc: QC, research: Research) -> list[Issue]:
    """Masalah fakta dari pemeriksaan per pernyataan. Kode tidak percaya begitu saja pada "supported": kutipan
    buktinya harus ditemukan di sumber."""
    out = []
    for c in qc.checks:
        where = _where(c.segment_id)
        if c.kind == "framing" or is_myth_statement(c.statement):
            # kalimat tanya/mitos ("Ada anggapan bahwa…", "Benarkah…?") tidak menyatakan isinya sebagai fakta,
            # walau model QC kadang menggolongkannya "fact"
            if has_quantity(c.statement, research):
                out.append(Issue(type="fact", detail=f"{where}: \"{c.statement}\" memuat angka tetapi tidak diperiksa "
                                                     "sebagai fakta", fix="beri bukti dari sumber atau hapus angkanya"))
            continue
        if not c.supported:
            out.append(Issue(type="fact", detail=f"{where}: \"{c.statement}\" tidak didukung sumber",
                             fix="hapus atau ganti dengan fakta yang tertulis di sumber"))
        elif not evidence_found(c.evidence, research.chosen.sources):
            out.append(Issue(type="fact", detail=f"{where}: bukti untuk \"{c.statement}\" tidak ditemukan di sumber",
                             fix="pakai hanya fakta yang tertulis di sumber, tanpa dilebih-lebihkan"))
    return out


# Kata mutlak / klaim tentang anggapan orang. Model mudah menambahkannya ("hanya 10 hari") padahal sumber tidak
# menyatakannya. Bila dipakai, bukti dari sumber untuk segmen itu harus memuat penanda yang setara.
ABSOLUTE_ID = ("hanya", "cuma", "satu-satunya", "selalu", "tidak pernah", "tak pernah", "pasti", "semua",
               "seluruh", "paling", "terbesar", "terkecil", "tertinggi", "terendah", "tercepat", "terlama",
               "tertua", "terpanjang", "terberat", "pertama", "banyak orang", "banyak yang", "kebanyakan",
               "mayoritas", "sebagian besar", "semua orang")
ABSOLUTE_EN = ("only", "sole", "always", "never", "all ", "every", "most", "largest", "smallest", "highest",
               "lowest", "fastest", "longest", "oldest", "heaviest", "first", "many people", "majority", "entire")


# Klaim tentang anggapan orang tetap butuh sumber, juga di dalam kalimat mitos/pertanyaan.
BELIEF_ID = ("banyak orang", "banyak yang", "kebanyakan", "mayoritas", "sebagian besar", "semua orang")
# Kalimat yang menyebut mitos/anggapan (format myth_vs_fact) atau bertanya tidak menyatakan isinya sebagai fakta.
MYTH_OPENERS = ("benarkah", "ada anggapan", "anggapan bahwa", "konon", "mitos")


def _markers(text: str, words) -> list[str]:
    low = f" {_norm(text)} "
    return [w for w in words if re.search(rf"(?<!\w){re.escape(w.strip())}(?!\w)", low)]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]


def _myth_sentence(sent: str) -> bool:
    s = _norm(sent)
    return s.endswith("?") or s.startswith(MYTH_OPENERS)


def is_myth_statement(text: str) -> bool:
    """Seluruh pernyataan berupa kalimat tanya/mitos, tanpa klaim tentang anggapan orang ("banyak orang")."""
    sents = _sentences(text)
    return bool(sents) and all(_myth_sentence(s) for s in sents) and not _markers(text, BELIEF_ID)


def asserted_markers(text: str) -> list[str]:
    """Kata mutlak yang dinyatakan sebagai fakta. Di kalimat tanya atau kalimat mitos ("Ada anggapan bahwa
    semua…"), kata mutlak adalah isi mitos yang sedang dibantah, jadi hanya klaim tentang anggapan orang dihitung."""
    used: list[str] = []
    for sent in _sentences(text):
        words = BELIEF_ID if _myth_sentence(sent) else ABSOLUTE_ID
        used += [w for w in _markers(sent, words) if w not in used]
    return used


def absolute_issues(qc: QC, script: Script) -> list[Issue]:
    out = []
    for seg in script.segments:
        used = asserted_markers(seg.text)
        if not used:
            continue
        evidence = " ".join(c.evidence for c in qc.checks if c.segment_id == seg.id and c.kind == "fact")
        if not _markers(evidence, ABSOLUTE_ID + ABSOLUTE_EN):
            out.append(Issue(type="fact", detail=f"segmen {seg.id} memakai \"{used[0]}\", tetapi sumber tidak "
                                                 "menyatakan hal mutlak/umum itu",
                             fix=f"hapus \"{used[0]}\" atau tulis persis seperti sumber"))
    return out


def coverage_problems(qc: QC, script: Script) -> list[str]:
    covered = {c.segment_id for c in qc.checks}
    missing = [s.id for s in script.segments if s.id not in covered]
    return [f"checks belum mencakup segmen {missing}; periksa setiap pernyataan di setiap segmen"] if missing else []


def review(cfg: ChannelConfig, script: Script, research: Research, fmt: str, *, settings: dict | None = None,
           recent_topics: list[str] = (), recent_hooks: list[str] = (), log=print) -> QC:
    variables = {
        "script": script.model_dump_json(indent=1), "sources": _sources(research),
        "banned_topics": cfg.channel.banned_topics, "recent_topics": list(recent_topics),
        "recent_hooks": list(recent_hooks), "words_min": config.words_limits(cfg)[0],
        "words_max": config.words_limits(cfg)[1], "word_count": script.word_count, "audience": cfg.channel.audience,
    }
    qc = llm.generate_json("qc", variables, QC, cfg.llm.temperature_strict, cfg=cfg, settings=settings,
                           allow_local=False, check=lambda q: coverage_problems(q, script), log=log)
    extra = check_issues(qc, research) + absolute_issues(qc, script) + coverage_issue(qc, script) + script_issues(
        script, cfg, research, fmt, list(recent_hooks))
    score = qc.score
    if any(i.type in ("length", "repetition", "quality") for i in extra):
        score = min(score, CODE_ISSUE_SCORE_CAP)
    return QC.model_validate({"score": score, "checks": [c.model_dump() for c in qc.checks],
                              "issues": [i.model_dump() for i in qc.issues + extra]})


def coverage_issue(qc: QC, script: Script) -> list[Issue]:
    """QC yang tidak memeriksa semua segmen tidak boleh meloloskan naskah."""
    return [Issue(type="fact", detail=p, fix="ulangi QC") for p in coverage_problems(qc, script)]


def write_and_review(cfg: ChannelConfig, research: Research, fmt: str, *, script: Script | None = None,
                     settings: dict | None = None, learnings: str = "", recent_topics: list[str] = (),
                     recent_hooks: list[str] = (), max_rewrites: int = MAX_REWRITES, on_script=None,
                     log=print) -> tuple[Script, QC]:
    """QC, lalu tulis ulang dengan daftar masalahnya bila gagal (maks. `max_rewrites` kali)."""
    if script is None:
        script = write_script(cfg, research, fmt, settings=settings, learnings=learnings,
                              recent_hooks=recent_hooks, log=log)
        if on_script:
            on_script(script)
    for round_ in range(max_rewrites + 1):
        qc = review(cfg, script, research, fmt, settings=settings, recent_topics=recent_topics,
                    recent_hooks=recent_hooks, log=log)
        log(f"QC putaran {round_ + 1}: skor {qc.score:g}, {'lulus' if qc.passed else 'gagal'}"
            + (f" ({'; '.join(i.type + ': ' + i.detail for i in qc.issues)[:300]})" if qc.issues else ""))
        if qc.passed or round_ == max_rewrites:
            break
        script = write_script(cfg, research, fmt, settings=settings, learnings=learnings,
                              recent_hooks=recent_hooks, issues=qc.issues, previous=script, log=log)
        if on_script:
            on_script(script)
    return script, qc


# --------------------------------------------------------------------------
# SEO
# --------------------------------------------------------------------------
def normalize_hashtags(tags: list[str], required: str = "#Shorts", limit: int = 5) -> list[str]:
    out = [required]
    for t in tags:
        t = "#" + "".join(ch for ch in t.lstrip("#") if ch.isalnum() or ch == "_")
        if len(t) > 1 and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:limit]


def normalize_tags(tags: list[str], defaults: list[str] = (), limit: int = 15) -> list[str]:
    out: list[str] = []
    for t in [*tags, *defaults]:
        t = " ".join(t.replace("#", " ").split())[:60]
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:limit]


def planned_visuals(script: Script) -> tuple[bool, bool]:
    """(ada klip stok, ada gambar AI) menurut rencana skrip. Setelah langkah visual, deskripsi disusun ulang
    dari hasil sebenarnya."""
    types = {s.visual_type for s in script.segments}
    return "stock" in types, "ai_image" in types


def claim_sources(script: Script) -> list[str]:
    return list(dict.fromkeys(c.source_url for c in script.claims if c.source_url))


def stock_credits(visuals) -> list[str]:
    """Kredit pembuat klip stok (pedoman API Pexels/Pixabay), satu baris per klip."""
    out = []
    for v in visuals.segments:
        if v.type != "stock" or not v.source_id:
            continue
        site = {"pexels": "Pexels", "pixabay": "Pixabay"}.get(v.source, v.source)
        line = f"{v.author or site} / {site}" + (f": {v.url}" if v.url else "")
        if line not in out:
            out.append(line)
    return out


def build_description(summary: str, script: Script, hashtags: list[str], *, uses_stock: bool,
                      uses_ai: bool, credits: list[str] = ()) -> str:
    return llm.render_prompt("_description", {"summary": summary.strip(), "sources": claim_sources(script),
                                              "uses_stock": uses_stock, "uses_ai": uses_ai, "hashtags": hashtags,
                                              "credits": list(credits)})


def seo(cfg: ChannelConfig, script: Script, research: Research, *, settings: dict | None = None,
        uses_stock: bool | None = None, uses_ai: bool | None = None, log=print) -> Metadata:
    plan_stock, plan_ai = planned_visuals(script)
    uses_stock = plan_stock if uses_stock is None else uses_stock
    uses_ai = plan_ai if uses_ai is None else uses_ai
    variables = {"script": script.model_dump_json(indent=1), "topic": research.chosen.topic,
                 "audience": cfg.channel.audience, "niche": cfg.channel.niche,
                 "banned_topics": cfg.channel.banned_topics}

    def check(d: SeoDraft) -> list[str]:
        out = []
        n = len(normalize_tags(d.tags))
        if not 8 <= n <= 15:
            out.append(f"tags berisi {n}, harus 8–15 tag berbeda")
        if len(normalize_hashtags(d.hashtags)) < 3:
            out.append("hashtags harus 3–5, termasuk #Shorts")
        return out

    d = llm.generate_json("seo", variables, SeoDraft, cfg.llm.temperature_strict, cfg=cfg, settings=settings,
                          check=check, log=log)
    tags = normalize_tags(d.tags, cfg.youtube.default_tags)
    hashtags = normalize_hashtags(d.hashtags)
    if len(hashtags) < 3:   # AI tidak memberi cukup hashtag: lengkapi dari tag
        hashtags = normalize_hashtags(d.hashtags + ["".join(w.capitalize() for w in t.split()) for t in tags])
    return Metadata(title=d.title.strip(), tags=tags, summary=d.summary.strip(),
                    hashtags=hashtags, category_id=cfg.youtube.category_id, language=cfg.channel.language,
                    description=build_description(d.summary, script, hashtags, uses_stock=uses_stock,
                                                  uses_ai=uses_ai))
