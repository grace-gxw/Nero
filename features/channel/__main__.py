"""
CLI Channel Otomatis (jalankan dari folder Nero):

  .venv\\Scripts\\python -m features.channel llm-ping     uji semua provider AI di pengaturan
  .venv\\Scripts\\python -m features.channel config       tampilkan pengaturan (tanpa nilai kunci)
  .venv\\Scripts\\python -m features.channel genres       daftar genre siap pakai
  .venv\\Scripts\\python -m features.channel run-daily --dry-run --only plan [--genre fakta-sains]
  .venv\\Scripts\\python -m features.channel run-daily --dry-run --only video [--genre fakta-sains]
  .venv\\Scripts\\python -m features.channel youtube-auth        hubungkan YouTube (membuka browser)
  .venv\\Scripts\\python -m features.channel youtube-status      status koneksi & kuota hari ini
  .venv\\Scripts\\python -m features.channel youtube-upload --date YYYY-MM-DD   unggah uji (private)
  .venv\\Scripts\\python -m features.channel youtube-check --date YYYY-MM-DD    cek ulang status video
  .venv\\Scripts\\python -m features.channel youtube-disconnect  cabut izin & hapus token

--only: plan = riset s.d. SEO; media = suara, visual, render; video = plan + media; atau satu nama langkah.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from . import config, genres, llm, pipeline, youtube
from .runctx import STEPS, RunLocked
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


def cmd_youtube_auth(_args) -> int:
    import webbrowser
    flow = youtube.begin_connect(config.load_config())
    print("Buka alamat ini di browser (Nero mencoba membukanya otomatis), pilih akun/channel, lalu setujui:")
    print(flow.url, flush=True)
    webbrowser.open(flow.url)
    try:
        while not flow.done.wait(0.5):
            pass
    except KeyboardInterrupt:
        flow.cancel()
        print("Dibatalkan.")
        return 1
    if flow.status == "berhasil":
        print(f"YouTube terhubung: {flow.channel}")
        return 0
    print(f"YouTube belum terhubung: {flow.error}", file=sys.stderr)
    return 1


def cmd_youtube_status(_args) -> int:
    st = youtube.status()
    st.pop("flow", None)
    print(json.dumps(st, indent=2, ensure_ascii=False))
    return 0


def cmd_youtube_upload(args) -> int:
    if not args.yes:
        print("Dengan mengunggah, kamu menyatakan video ini mematuhi Persyaratan Layanan YouTube "
              f"({youtube.TOS_URL}) dan tidak melanggar hak cipta atau privasi orang lain.")
        if input("Unggah sebagai private? Ketik ya: ").strip().lower() != "ya":
            print("Batal.")
            return 1
    return pipeline.upload_test(date.fromisoformat(args.date))


def cmd_youtube_check(args) -> int:
    try:
        res = pipeline.recheck_upload(date.fromisoformat(args.date))
    except RunLocked as e:
        print(e, file=sys.stderr)
        return 3
    except youtube.YouTubeError as e:
        print(f"Gagal: {e}", file=sys.stderr)
        return 1
    if res is None:
        print("Video tanggal ini belum diunggah.", file=sys.stderr)
        return 1
    print(json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if res.status != "masalah" else 1


def cmd_youtube_disconnect(_args) -> int:
    revoked = youtube.disconnect()
    print("Token dihapus dari Nero." + (" Izin di Google juga dicabut." if revoked else
                                       " Izin di Google belum tentu tercabut; cek myaccount.google.com/permissions."))
    return 0


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
    sub.add_parser("youtube-auth", help="hubungkan YouTube lewat browser").set_defaults(func=cmd_youtube_auth)
    sub.add_parser("youtube-status", help="status koneksi YouTube & kuota hari ini").set_defaults(
        func=cmd_youtube_status)
    u = sub.add_parser("youtube-upload", help="unggah uji video satu run sebagai private")
    u.add_argument("--date", required=True, help="tanggal run YYYY-MM-DD")
    u.add_argument("--yes", action="store_true", help="lewati pertanyaan konfirmasi")
    u.set_defaults(func=cmd_youtube_upload)
    c = sub.add_parser("youtube-check", help="cek ulang status video yang sudah diunggah")
    c.add_argument("--date", required=True, help="tanggal run YYYY-MM-DD")
    c.set_defaults(func=cmd_youtube_check)
    sub.add_parser("youtube-disconnect", help="cabut izin YouTube & hapus token").set_defaults(
        func=cmd_youtube_disconnect)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, youtube.AuthError) as e:
        print(e, file=sys.stderr)
        return 2
    except youtube.YouTubeError as e:
        print(e, file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Gagal: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
