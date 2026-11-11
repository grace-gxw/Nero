"""
CLI Channel Otomatis (jalankan dari folder Nero):

  .venv\\Scripts\\python -m features.channel llm-ping     uji semua provider AI di pengaturan
  .venv\\Scripts\\python -m features.channel config       tampilkan pengaturan (tanpa nilai kunci)
  .venv\\Scripts\\python -m features.channel genres       daftar genre siap pakai
  .venv\\Scripts\\python -m features.channel run-daily --dry-run --only plan [--genre fakta-sains]
  .venv\\Scripts\\python -m features.channel run-daily --dry-run --only video [--genre fakta-sains]

--only: plan = riset s.d. SEO; media = suara, visual, render; video = plan + media; atau satu nama langkah.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from . import config, genres, llm, pipeline
from .runctx import STEPS
from .schemas import Ping


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_config(_args) -> int:
    print(json.dumps(config.public_settings(), indent=2, ensure_ascii=False))
    return 0


def cmd_llm_ping(args) -> int:
    cfg = config.load_config()
    providers = args.provider or list(cfg.llm.providers)
    failed = 0
    for p in providers:
        print(f"...   {p:<11} menguji", flush=True)
        calls: list[llm.Call] = []
        try:
            got = llm.generate_json("ping", {}, Ping, cfg.llm.temperature_strict, cfg=cfg, providers=[p],
                                    calls=calls, log=lambda m: None)
            c = calls[-1]
            print(f"OK    {p:<11} {c.model:<28} {c.seconds:>6.2f} dtk  token {c.tokens_in}/{c.tokens_out}  "
                  f"\"{got.message}\"")
        except llm.LLMError:
            failed += 1
            c = calls[-1] if calls else llm.Call(p, "?")
            print(f"GAGAL {p:<11} {c.model or '(model kosong)':<28} {llm._short(c.error, 200)}")
    return 1 if failed == len(providers) else 0


def cmd_genres(_args) -> int:
    for g in genres.GENRES:
        print(f"{g['id']:<18} {g['name']:<24} {g['niche']}")
    return 0


def cmd_run_daily(args) -> int:
    day = date.fromisoformat(args.date) if args.date else None
    return pipeline.run_daily(day, dry_run=args.dry_run, force=args.force or [], only=args.only, genre=args.genre)


def main(argv=None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="python -m features.channel", description="Nero · Channel Otomatis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("llm-ping", help="uji koneksi ke semua provider AI")
    p.add_argument("--provider", action="append", choices=list(config.PROVIDERS),
                   help="uji provider ini saja (boleh diulang)")
    p.set_defaults(func=cmd_llm_ping)
    sub.add_parser("config", help="tampilkan pengaturan channel tanpa nilai kunci").set_defaults(func=cmd_config)
    sub.add_parser("genres", help="daftar genre siap pakai").set_defaults(func=cmd_genres)
    r = sub.add_parser("run-daily", help="jalankan pipeline harian")
    r.add_argument("--date", help="tanggal run YYYY-MM-DD (bawaan: hari ini, zona waktu pengaturan)")
    r.add_argument("--dry-run", action="store_true", help="uji coba: tanpa upload & tanpa kirim Telegram")
    r.add_argument("--force", action="append", choices=list(STEPS), metavar="STEP",
                   help="jalankan ulang langkah ini walau hasilnya sudah ada. Langkah yang memakai hasilnya ikut "
                        "diulang bila hasilnya berubah")
    r.add_argument("--only", choices=[*pipeline.ALIASES, *STEPS],
                   help="jalankan hanya langkah ini (plan = riset s.d. SEO, media = suara s.d. render, "
                        "video = plan + media)")
    r.add_argument("--genre", choices=[g["id"] for g in genres.GENRES],
                   help="pakai genre ini untuk run ini saja (tidak disimpan)")
    r.set_defaults(func=cmd_run_daily)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
