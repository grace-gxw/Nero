# CLAUDE.md — Nero

Panduan singkat untuk sesi Claude Code berikutnya. Detail untuk pengguna ada di `PANDUAN.md`.

## Gambaran
- Nero adalah aplikasi desktop-lokal untuk Windows 11: server Flask di `http://127.0.0.1:7860` (`app.py`) dan tampilan Next.js (`frontend/`, hasil build disalin ke `web/`).
- Satu folder per fitur di `features/<id>/`. Kontraknya: `ID`, `NAME`, `SETTINGS`, `bp`, `start()`, `summary()` (lihat `features/__init__.py`). Setiap fitur juga perlu entri di `frontend/src/features/index.ts`.
- Fitur yang ada sekarang:
  - `clip` (Clip Maker): video panjang → clip vertikal. Mesinnya ada di `features/clip/engine.py`.
  - `channel` (Channel Otomatis): masih dalam pengembangan, lihat di bawah.

## Konvensi (wajib)
- Python **3.14** di `.venv`. Jangan pindah ke 3.15: onnxruntime belum punya wheel cp315. Pustaka GPU tetap CUDA 12.
- Identifier dalam bahasa Inggris. Komentar, docstring, pesan galat, teks UI, dan nilai enum dalam **Bahasa Indonesia** (`antri`, `proses`, `selesai`, `gagal`).
- Tanpa database: JSON di `data/<fitur>/`, hasil di `output/<fitur>/`. Tulis atomik (`.tmp` lalu `os.replace`). `data/` dan `output/` di-gitignore.
- Pengaturan ada di `data/settings.json`: kunci umum + satu bagian per fitur. Akses lewat `common.load_settings()`, `common.feature_settings()`, dan `common.update_settings()`. Secret **tidak pernah** dikirim ke halaman; yang dikirim hanya `has_<nama>`.
- HTTP memakai `requests`. Subprocess di Windows memakai `CREATE_NO_WINDOW` (`engine._popen_kwargs()`). FFmpeg diambil dari `engine.ffmpeg_exe()`.
- Tugas panjang harus bisa dibatalkan dan menampilkan progres (0..1) serta label tahap.
- Tampilan hitam-putih; warna hanya untuk status ok/peringatan/galat.
- Abstraksi seperlunya, tanpa framework agen.

## Tes
```
.venv\Scripts\python -m unittest discover -s tests -t .
```
- Tes memakai `unittest` + `unittest.mock`, tanpa jaringan.
- Folder data dibuat sementara lewat pola `Sandbox` di `tests/test_platform.py`. Jangan menyentuh `data/` milik pengguna.
- Seluruh tes lama harus tetap hijau.

## Channel Otomatis (`features/channel/`, branch `feat/auto-channel`)
- Spesifikasi: `SPEC.md`. Keputusan integrasi yang mengalahkan SPEC (stack, struktur, penyimpanan, jadwal): `INTEGRATION_PLAN.md`. Kerjakan **satu fase per waktu** dan tunggu OK pengguna di akhir tiap fase.
- Flag `settings.channel.enabled` bawaannya `false`. Selama `false`, tidak ada jadwal, upload, atau kiriman Telegram; uji coba (`--dry-run`) tetap boleh.
- Pengaturan channel ada di bagian `"channel"` dari `settings.json`, divalidasi oleh Pydantic (`features/channel/config.py`). Secret disimpan di `channel.keys`. Env var (`GEMINI_API_KEY`, `GROQ_API_KEY`, `PEXELS_API_KEY`, …; lihat `config.ENV_KEYS`) menimpa nilai tersimpan.
- LLM: `features/channel/llm.generate_json()`.
  - Memakai ulang `gemini_ai.py` (Interactions API) dan `local_ai.py` (Groq lewat jalur OpenAI-compatible, plus Ollama).
  - Nama model selalu diambil dari pengaturan, tidak di-hardcode.
  - QC tidak boleh diputuskan oleh model lokal (`allow_local=False`).
- Prompt agen ada di `features/channel/prompts/*.md` (Jinja2, `StrictUndefined`).
  - `_guardrails.md` berisi aturan SPEC bagian 8 dan di-include semua agen.
  - `_description.md` adalah templat deskripsi YouTube.
  - Teks prompt tidak boleh ditulis di kode Python.
- Pipeline: `pipeline.run_daily()`.
  - Setiap langkah menulis `data/channel/runs/<tanggal>/<langkah>.json`. Hasil lama dipakai ulang hanya bila valid dan sidik masukannya (isi + pengaturan relevan, `pipeline.fp_*`, disimpan di `inputs.json`) cocok.
  - Langkah yang mulai bekerja menghapus hasil lamanya sendiri (`RunContext.invalidate`). Bila video bergantung padanya, salinan di `output/channel/<tanggal>/` juga ditarik.
  - Langkah media (tts, visuals, render) mulai bekerja hanya kalau QC lulus untuk skrip dan pengaturan sekarang (`_passed_qc`).
  - `--force` di langkah rencana ikut mengulang langkah yang memakai hasilnya (`runctx.DEPENDS`). Langkah media diulang bila sidiknya berubah.
  - Langkah yang belum dibuat ada di luar `pipeline.IMPLEMENTED` dan dilewati.
  - `run_daily(cancel=Event, on_progress=fn(step, 0..1))`. `media.Cancelled` sengaja bukan `RuntimeError`: `engine.render_clip` menangkap `RuntimeError` untuk pindah permanen dari NVENC ke libx264.
- Agen:
  - `research.py`: Wikipedia/RSS. URL sumber selalu diambil dari bahan riset, bukan dari tulisan LLM.
  - `writer.py`: rotasi format, skrip, QC + tulis ulang maks. 2×, SEO.
  - Masalah yang bisa dipastikan kode (panjang, format, visual, URL klaim) ditambahkan ke QC.
- Media (Fase I-3):
  - `voice.py`: edge-tts per bagian narasi + jeda, timing dari WordBoundary (cadangan Whisper 16 kHz). Rentang kata otomatis per suara: `config.words_range()`.
  - `visuals.py`: satu klip per segmen dengan jumlah frame tepat. Rantai cadangan ai_image → stock → motion_graphic → polos. Pembantunya `stock.py` (Pexels/Pixabay, cache 24 jam), `ai_images.py` (Cloudflare, kuota per tanggal UTC), dan `graphics.py` (template Pillow).
  - `render.py`: base.mkv (klip + suara + musik), lalu `engine.render_clip` untuk subtitle, lalu validasi.
  - Tes tidak boleh memanggil jaringan atau `engine.transcribe`. FFmpeg sungguhan boleh, asal klipnya pendek.
- CLI: `.venv\Scripts\python -m features.channel llm-ping | genres | run-daily --dry-run --only plan|media|video --genre <id>`.
- Di heredoc Bash tool, backslash (`\n`) bisa berubah; tulis skrip/berkas dengan tool Write.
- Dilarang:
  - Memakai `yt_dlp` atau `download_video` di modul channel. Ada tes yang menjaga ini.
  - Upload ke TikTok/IG secara otomatis.
  - Upload ke YouTube sebelum QC dan validasi render lulus.
- YouTube mengunci video yang di-upload lewat API dari project yang belum diaudit. Karena itu `youtube.api_upload` bawaannya `false`, dan Nero menyiapkan paket unggah manual.
