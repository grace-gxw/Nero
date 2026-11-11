"""
Nero · Channel Otomatis - orkestrator run harian (SPEC.md bagian 5).

    run_daily(day, dry_run=True, only="plan")

Setiap langkah membaca masukannya dari folder run (data/channel/runs/YYYY-MM-DD/) dan menulis hasilnya ke
sana. Hasil yang sudah ada & valid dipakai lagi (checkpoint). Bila satu langkah dijalankan ulang (karena
--force atau hasilnya belum ada), semua langkah sesudahnya ikut dijalankan ulang supaya tidak memakai
hasil lama yang sudah tidak cocok.

Fase I-2: langkah research, script, qc, seo. Langkah lain menyusul (lihat INTEGRATION_PLAN.md).
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date

import common

from . import config, genres, research, state, timeutil, writer
from .config import ChannelConfig, ConfigError
from .runctx import STEPS, RunContext, RunLocked
from .schemas import QC, Metadata, Research, Script

PLAN_STEPS = ("research", "script", "qc", "seo")
IMPLEMENTED = {"research", "script", "qc", "seo"}
ALIASES = {"plan": PLAN_STEPS}

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_LOCKED = 0, 1, 2, 3


class StepError(RuntimeError):
    def __init__(self, step: str, msg: str):
        super().__init__(msg)
        self.step = step


@dataclass
class Run:
    ctx: RunContext
    cfg: ChannelConfig
    settings: dict
    dry_run: bool
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.ctx.log(msg)


# --------------------------------------------------------------------------
# Langkah
# --------------------------------------------------------------------------
def _need(run: Run, name: str, schema, step: str):
    got = run.ctx.load(name, schema)
    if got is None:
        raise StepError(step, f"{name}.json belum ada atau rusak; jalankan langkah sebelumnya dulu.")
    return got


def step_research(run: Run, force: bool) -> bool:
    if run.ctx.cached("research", Research, force):
        return False
    res = research.run(run.cfg, run.ctx.day, settings=run.settings, learnings=state.load_learnings(),
                       log=run.log)
    run.ctx.save("research", res)
    return True


def _format(run: Run) -> str:
    return writer.pick_format(run.cfg.video.formats_rotation, state.last_formats(2))


def step_script(run: Run, force: bool) -> bool:
    if run.ctx.cached("script", Script, force):
        return False
    res = _need(run, "research", Research, "script")
    fmt = _format(run)
    run.log(f"skrip: format {fmt}")
    script = writer.write_script(run.cfg, res, fmt, settings=run.settings, learnings=state.load_learnings(),
                                 recent_hooks=state.last_hooks(5), log=run.log)
    run.ctx.save("script", script)
    return True


def step_qc(run: Run, force: bool) -> bool:
    cached = run.ctx.cached("qc", QC, force)
    if cached:
        if not cached.passed:
            raise StepError("qc", "QC sebelumnya gagal. Jalankan ulang dengan --force script untuk menulis ulang.")
        return False
    res = _need(run, "research", Research, "qc")
    script = _need(run, "script", Script, "qc")
    script, qc = writer.write_and_review(
        run.cfg, res, script.format, script=script, settings=run.settings, learnings=state.load_learnings(),
        recent_topics=state.recent_topics(research.HISTORY_DAYS, run.ctx.day), recent_hooks=state.last_hooks(5),
        on_script=lambda s: run.ctx.save("script", s), log=run.log)
    run.ctx.save("script", script)
    run.ctx.save("qc", qc)
    if not qc.passed:
        reasons = "; ".join(f"{i.type}: {i.detail}" for i in qc.issues)[:600]
        raise StepError("qc", f"QC gagal setelah {writer.MAX_REWRITES} kali tulis ulang (skor {qc.score:g}). "
                              f"Video tidak dibuat. Alasan: {reasons}")
    return True


def step_seo(run: Run, force: bool) -> bool:
    if run.ctx.cached("metadata", Metadata, force):
        return False
    res = _need(run, "research", Research, "seo")
    script = _need(run, "script", Script, "seo")
    qc = _need(run, "qc", QC, "seo")
    if not qc.passed:
        raise StepError("seo", "QC belum lulus.")
    run.ctx.save("metadata", writer.seo(run.cfg, script, res, settings=run.settings, log=run.log))
    return True


STEP_FUNCS = {"research": step_research, "script": step_script, "qc": step_qc, "seo": step_seo}


# --------------------------------------------------------------------------
# Run harian
# --------------------------------------------------------------------------
def resolve_steps(only: str | None) -> list[str]:
    if not only:
        return list(STEPS)
    if only in ALIASES:
        return list(ALIASES[only])
    if only not in STEPS:
        raise ConfigError(f"Langkah '{only}' tidak dikenal. Pilihan: plan, {', '.join(STEPS)}")
    return [only]


def prepare_config(genre: str | None = None, settings: dict | None = None) -> ChannelConfig:
    cfg = config.load_config(settings)
    if genre:
        try:
            cfg = genres.apply(cfg, genre)
        except KeyError as e:
            raise ConfigError(str(e.args[0])) from e
    if not cfg.channel.niche.strip():
        raise ConfigError("Genre/niche channel belum dipilih. Pilih genre di halaman Nero, atau uji coba dengan "
                          "--genre (lihat: python -m features.channel genres).")
    return cfg


def run_daily(day: date | None = None, *, dry_run: bool = False, force: list[str] = (), only: str | None = None,
              genre: str | None = None, echo=print) -> int:
    """Jalankan pipeline untuk satu tanggal. Hasil: kode keluar (0 = berhasil)."""
    settings = common.load_settings()
    try:
        cfg = prepare_config(genre, settings)
        steps = resolve_steps(only)
        bad = [f for f in force if f not in STEPS]
        if bad:
            raise ConfigError(f"--force {', '.join(bad)}: langkah tidak dikenal")
    except ConfigError as e:
        echo(f"[channel] {e}")
        return EXIT_CONFIG
    if not cfg.enabled and not dry_run:
        echo("[channel] Channel Otomatis masih nonaktif. Uji coba dengan --dry-run, atau nyalakan di pengaturan.")
        return EXIT_CONFIG
    day = day or timeutil.today_local(cfg.schedule.timezone)
    ctx = RunContext(day, echo=echo)
    run = Run(ctx, cfg, settings, dry_run)
    try:
        ctx.acquire()
    except RunLocked as e:
        echo(f"[channel] {e}")
        return EXIT_LOCKED
    current = "-"
    try:
        run.log(f"mulai run {day.isoformat()} ({'uji coba' if dry_run else 'sungguhan'}), niche: {cfg.channel.niche}")
        dirty = False
        for step in STEPS:
            if step not in steps:
                continue
            if step not in IMPLEMENTED:
                run.log(f"langkah {step} belum tersedia (menyusul di fase berikutnya); berhenti di sini")
                break
            current = step
            did = STEP_FUNCS[step](run, dirty or step in force)
            (run.ran if did else run.skipped).append(step)
            dirty = dirty or did
        run.log(f"selesai. dijalankan: {', '.join(run.ran) or '-'}; dipakai ulang: {', '.join(run.skipped) or '-'}")
        return EXIT_OK
    except Exception as e:  # noqa: BLE001  semua kegagalan langkah dilaporkan dengan cara yang sama
        run.log(f"GAGAL di langkah {getattr(e, 'step', None) or current}: {e}")
        with open(ctx.path("run.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        # alert Telegram (nama langkah + 30 baris log terakhir) ditambahkan di Fase I-6
        return EXIT_FAILED
    finally:
        ctx.release()
