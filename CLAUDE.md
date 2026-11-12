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
  - `voice.py`: edge-tts per bagian narasi + jeda, timing dari WordBoundary (cadangan Whisper 16 kHz). Rentang kata otomatis per suara: `config.words_range()` (ideal, untuk prompt) dan `config.words_limits()` (batas keras yang dicek kode di QC).
  - `visuals.py`: satu klip per segmen dengan jumlah frame tepat. Rantai cadangan ai_image → stock → motion_graphic → polos. Pembantunya `stock.py` (Pexels/Pixabay, cache 24 jam), `ai_images.py` (Cloudflare, kuota per tanggal UTC), dan `graphics.py` (template Pillow).
  - `render.py`: base.mkv (klip + suara + musik), lalu `engine.render_clip` untuk subtitle, lalu validasi.
  - Tes tidak boleh memanggil jaringan atau `engine.transcribe`. FFmpeg sungguhan boleh, asal klipnya pendek.
- Halaman `/channel` (Fase I-4):
  - Rute ada di `api.py`.
  - `jobs.py` menjalankan satu uji coba (dry-run) per proses di thread latar dengan progres per langkah.
  - `runs.py` membaca folder run untuk halaman. Hasil yang usang disembunyikan lewat `<langkah>_src` = sidik naskah di `inputs.json`.
  - Frontend ada di `frontend/src/features/channel/`. Setelah mengubah frontend, jalankan `npm run build` (menyalin ke `web/`, ikut di-commit).
- YouTube (Fase I-5, `youtube.py`, REST lewat `requests`, tanpa pustaka Google):
  - OAuth client "Desktop app": listener sementara di `http://127.0.0.1:<port acak>` (tanpa path) + PKCE S256, scope yang diizinkan dicek. Refresh token di `channel.keys.yt_refresh_token`, hanya diisi/dihapus lewat Hubungkan/Putuskan (POST /settings mengabaikannya). Access token hanya di memori. Info akun tanpa rahasia: `data/channel/youtube_account.json`.
  - Upload resumable per 8 MB. Sesi disimpan di `runs/<tgl>/upload_session.json` (berkas, sidik isi permintaan, channel, judul, asal naskah). Sesi dilanjutkan hanya bila berkas, isi, dan channel sama; bila berbeda, sesi lama dicek dulu (selesai = dipakai apa adanya, belum = ditinggal).
  - Catatan sesi **tidak dihapus** sebelum `video_id` tersimpan; sesi yang kedaluwarsa/tidak terpakai hanya ditandai (`state`), supaya cek duplikat (judul sekarang + judul sesi, sejak sehari sebelum sesi dibuat) tetap bisa diulang bila gagal.
  - `youtube.json` ditulis begitu ada `video_id` (penjaga upload ganda), lalu `on_uploaded` mencatat riwayat. Video yang ditemukan (bukan diunggah saat itu) dicatat dengan privasi/jadwal/judul asli dari YouTube dan keterangan `adopted`.
  - Galat server token = `YouTubeError` status 0; hanya 401/403 dari alamat upload = `SessionUnusable`. Satu upload sekaligus lewat `data/channel/upload.lock`; Hubungkan/Putuskan ditolak selama itu.
  - Cek tetap ketat sampai sekali lolos (`strict_ok`); video private tanpa jadwal yang ditayangkan pengguna bukan masalah. Cek ulang (`recheck_upload`) menolak video di channel lain tanpa mengubah `youtube.json`.
  - Sesi dari channel lain tidak pernah ditinggal diam-diam (upload diminta menghubungkan channel itu). Channel baru tanpa video: daftar upload 404 = belum ada upload.
  - Langkah `publish_youtube` hanya untuk run sungguhan dengan `enabled` dan `youtube.api_upload` menyala; video `needs_review` tidak diunggah otomatis. `pipeline.upload_test()` = unggah uji private atas perintah pengguna (boleh walau nonaktif). Syarat keduanya ada di `_upload_ready` (halaman memakai yang sama lewat `upload_block`, plus tombol "Buat ulang video"): QC lulus untuk naskah & pengaturan sekarang, render valid, `tts_src`/`visuals_src`/`render_src`/`seo_src` = sidik naskah, dan `render_media` = sidik suara+visual sekarang (run lama: `fp_render`). Deskripsi disusun ulang dari visual video saat upload. Juga sidik `seo` = `fp_seo` sekarang (kategori/tag). Langkah yang dipakai ulang memperbarui `<langkah>_src` (`_mark_src`). Genre `--genre` dicatat di `inputs.json` setelah langkah rencana/media selesai, dan dipakai lagi lewat `run_config`. `state.upsert_history` membaca riwayat secara ketat (tidak menimpa berkas yang rusak).
  - Pencarian tren: maks. 3 `search.list` per hari, hanya judul, masuk prompt riset sebagai petunjuk minat (tidak disimpan di `research.json`; cache dihapus `prune_runs` setelah 2 hari).
  - Tes memakai `FakeGoogle` (patch `requests.post/put/request`); listener loopback diuji dengan socket 127.0.0.1 sungguhan.
- Paket manual & Telegram (Fase I-6):
  - `manual_pack.py`: caption TikTok (teks ≤150 + 3–5 hashtag) dan Instagram (≤2.200, ≤5 hashtag), dengan pengingat label AI (TikTok hanya bila ada gambar AI; Instagram selalu, karena narasi TTS). Teks caption ada di `prompts/_caption_*.md`. Langkah `manual_pack` ikut alias plan/media/video.
  - `telegram.py`: Bot API lewat `requests`. Pesan galat tidak pernah memuat URL (berisi token). Teks ≤4000 (UTF-16), caption video ≤1000, video ≤50 MB. 429 = tunggu `retry_after`. HTML yang ditolak dikirim ulang sebagai teks polos.
  - Langkah `notify` hanya ikut run lengkap. Uji coba hanya mencatat isinya. `telegram.json` mencatat bagian yang sudah terkirim per naskah, sehingga kiriman yang terputus dilanjutkan dan tidak dobel.
  - `notify_test()`: kirim paket atas perintah pengguna (boleh walau nonaktif). `_alert` di `_guarded` mengirim kegagalan hanya untuk run harian sungguhan (`Run.alerts`).
  - Tes memakai `FakeTelegram` (patch `telegram.requests`).
- Analis (Fase I-7, `analyst.py`, langkah `analyst` paling awal + `state_update`):
  - Tidak pernah menggagalkan run: galat YouTube/AI = `PERINGATAN`, data & pelajaran lama dipakai. Sekali sehari (`performance.json` `day`), kecuali `--force analyst` / tombol halaman. Satu analis sekaligus lewat `data/channel/analyst.lock`.
  - Video = milik Nero saja: `youtube.json` tiap run, riwayat, dan video yang diunggah sendiri bila judul di daftar upload sama persis (`norm_title`) dengan judul run itu (judul kembar dilewati). Yang diunggah sendiri dicatat ke riwayat lewat `pipeline._record_found`.
  - Angka per video = YouTube Analytics 7 hari pertama sejak tayang (tanggal Pasifik; data tertunda 48–72 jam, `data_through` dari baris harian channel). Satu panggilan per video (`filters=video==ID`, laporan dasar yang terdokumentasi); 400 = ulang tanpa `averageViewPercentage`. YouTube Analytics API harus diaktifkan terpisah (`ANALYTICS_OFF`).
  - Developer Policies: peringkat hanya dengan satu metrik mentah (engaged views 7 hari pertama), tanpa skor gabungan. Judul/teks dari YouTube tidak disimpan (label dari Nero). `state.clear_performance()` saat Putuskan, `invalid_grant`, YouTube tidak terhubung, atau `auth_verified_at` > 30 hari (`analyst.load`). Riwayat & `youtube.json` tidak ikut dihapus.
  - `learnings.md` selalu ditulis (juga "Data belum cukup"); prompt hanya menerima poinnya (`analyst.prompt_learnings`). AI dipanggil hanya bila ≥ 5 video lengkap DAN `analyst.share_with_ai` (bawaan false, izin tegas pengguna). Data ke AI tanpa ID video/channel. Tiap pola ≥ 2 video bukti, tanpa angka statistik (`_STAT_NUMBER`), dicek kode. Sidik data sama = AI tidak dipanggil ulang.
  - Laporan mingguan: `weekly_key` = hari laporan terakhir ≤ hari ini (dikejar bila terlewat); hanya run sungguhan (`not dry_run`, berarti `enabled`) + `notify.telegram`; dicatat di `weekly_report.json`, gagal = dicoba lagi. `weekly_report_now()` = kirim atas perintah pengguna (menandai minggu ini bila belum).
  - Halaman: kartu `analytics-card.tsx`, rute `/analytics`, `/analytics/run|report|clear`; pekerjaan `jobs` mode `analyst`/`report` (`GLOBAL_MODES`, tanpa folder run; log ke `data/channel/analyst.log`).
  - Tes memakai `FakeAnalytics` (turunan `FakeGoogle`) dan `FakeAI`.
- Pembatalan: `cancel.py` memasang event batal per thread (`cancel_scope`). Panggilan AI (`llm`) dan riset memeriksanya, jadi Batal juga menghentikan langkah rencana.
- Windows: berkas yang sedang dibaca/diputar tidak bisa diganti. Tulis dan hapus lewat `state.replace_retry`/`remove_retry`, dan baca dengan `state.read_text` (berkas langsung ditutup).
- CLI: `.venv\Scripts\python -m features.channel llm-ping | genres | run-daily --dry-run --only plan|media|video --genre <id> | youtube-auth | youtube-status | youtube-upload --date D | youtube-check --date D | youtube-disconnect | telegram-chats | telegram-test | telegram-send --date D | analyst [--cached] | weekly-report [--send] | analytics-clear`.
- Di heredoc Bash tool, backslash (`\n`) bisa berubah; tulis skrip/berkas dengan tool Write.
- Dilarang:
  - Memakai `yt_dlp` atau `download_video` di modul channel. Ada tes yang menjaga ini.
  - Upload ke TikTok/IG secara otomatis.
  - Upload ke YouTube sebelum QC dan validasi render lulus.
- `youtube.api_upload` bawaannya `false` (keputusan pengguna: paket unggah manual dulu). Alasan awalnya, kunci private untuk project yang belum diaudit, dicabut YouTube pada 2 Nov 2026. Jangan mengubah bawaannya tanpa keputusan pengguna.
