"""
CLI Channel Otomatis (jalankan dari folder Nero):

  .venv\\Scripts\\python -m features.channel llm-ping     uji semua provider AI di pengaturan
  .venv\\Scripts\\python -m features.channel config       tampilkan pengaturan (tanpa nilai kunci)

Perintah run-daily ditambahkan di Fase I-2.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config, llm
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
            print(f"GAGAL {p:<11} {c.model or '(model kosong)':<28} {c.error.splitlines()[0] if c.error else ''}")
    return 1 if failed == len(providers) else 0


def main(argv=None) -> int:
    _utf8_console()
    ap = argparse.ArgumentParser(prog="python -m features.channel", description="Nero · Channel Otomatis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("llm-ping", help="uji koneksi ke semua provider AI")
    p.add_argument("--provider", action="append", choices=list(config.PROVIDERS),
                   help="uji provider ini saja (boleh diulang)")
    p.set_defaults(func=cmd_llm_ping)
    sub.add_parser("config", help="tampilkan pengaturan channel tanpa nilai kunci").set_defaults(func=cmd_config)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
