# INTEGRATION_PLAN — Channel Video Otomatis di atas Nero

> Hasil Fase I-0 (audit read-only), 11 November 2026. Selain file ini, tidak ada file yang dibuat, diubah,
> atau dihapus. `SPEC.md` belum disimpan ke repo karena aturan 14.1.1; isinya akan disimpan di langkah
> pertama Fase I-1 setelah rencana ini disetujui.

---

## 1. Ringkasan sistem lama

| Hal | Kondisi sekarang |
|---|---|
| Apa | **Nero**: aplikasi desktop-lokal untuk Windows 11 (server Flask di `http://127.0.0.1:7860`). Isinya beberapa fitur untuk mencari uang dari internet. Fitur pertama: **Clip Maker** (video panjang → clip vertikal 1080×1920 bersubtitle). |
| Bahasa & runtime | Python **3.14.10** (`.venv`, `install.bat` mewajibkan 3.14; 3.15 belum bisa karena onnxruntime). Tampilan: Next.js 16 + React 19 + shadcn/ui, di-build ke `web/`. |
| Cara jalan | Ikon Nero di Desktop (`pythonw`, tanpa jendela; log ke `data/nero.log`), atau `run.bat`. Bisa **buka otomatis saat Windows menyala** (`desktop.set_autostart`). |
| Struktur | `app.py` (server, keamanan, pengaturan umum), `common.py` (folder, `settings.json`, berkas), `features/<id>/` (satu folder per fitur: `ID`, `NAME`, `SETTINGS`, `bp`, `start()`, `summary()`), `frontend/src/features/<id>/` + `frontend/src/features/index.ts`. AI bersama: `gemini_ai.py`, `local_ai.py`. |
| Mesin video | `features/clip/engine.py` (2.349 baris): `ffmpeg_exe/run_ffmpeg/probe`, transkrip `faster-whisper` (GPU lalu CPU), `CaptionRenderer` (subtitle 1–4 kata, kapital, stroke, shadow, sorotan warna/kotak/karaoke, animasi), `FrameComposer` (tata letak, zoom, filter warna, judul, progress bar), `render_clip` (NVENC, kembali ke libx264 bila gagal, loudnorm). 11 preset gaya + pustaka "Gaya saya". |
| FFmpeg | `tools/ffmpeg.exe` 9.0.2 (gyan.dev). Filter yang dibutuhkan SPEC tersedia: `zoompan`, `xfade`, `amix`, `afade`, `loudnorm`, `drawtext`, `ass`/`subtitles`. Encoder `h264_nvenc` dan `libx264` ada. **Tidak ada `ffprobe`**; `engine.probe()` membaca keluaran `ffmpeg -i`. |
| Config & secret | Satu file `data/settings.json` (di-gitignore): kunci umum + satu bagian per fitur (`{"clip": {...}}`). Secret (`api_key`, `gemini_key`, `local_ai_key`) disimpan di sana, diisi lewat halaman Pengaturan, **tidak pernah dikirim ke halaman**. Tidak ada `.env`. |
| Penyimpanan data | Tanpa database. JSON di `data/<fitur>/`, hasil di `output/<fitur>/`. Tulis atomik (`.tmp` lalu `os.replace`). `NERO_DATA`/`NERO_OUTPUT` bisa mengalihkan folder (dipakai untuk Nero kedua uji coba). |
| AI yang sudah ada | Gemini lewat Interactions API dengan HTTP biasa (model dari pengaturan, bawaan `gemini-3.8-flash`, JSON + skema). Ollama/LM Studio/server bergaya OpenAI lewat `local_ai.py`; `local_ai_key` disebut untuk Groq/OpenRouter. Claude (berbayar) opsional di Clip Maker. |
| Scheduler | **Tidak ada.** Pekerja hanya thread antrian di `start()` tiap fitur. Tidak ada cron, Task Scheduler, atau GitHub Actions (`.github/` tidak ada). |
| Notifikasi & YouTube | Tidak ada Telegram, OAuth Google, atau uploader. |
| Tes | `unittest` (bukan pytest): 59 tes, jalan dengan `.venv\Scripts\python -m unittest discover -s tests -t .`. **Hasil audit: 59/59 OK, ±2 menit.** Tes memakai folder sementara (`Sandbox`), jadi `data/`/`output/` milik pengguna tidak tersentuh. Tidak ada ruff/linter Python, tidak ada CI. |
| Dependency penting | flask, numpy, pillow, opencv, faster-whisper, yt-dlp, imageio-ffmpeg, requests, anthropic. `pydantic`, `PyYAML`, `Jinja2` sudah terpasang sebagai dependency turunan, tapi belum tercantum di `requirements*.txt`. |
| Git | Branch `main`, remote `origin` = `github.com/raphaelvittorio/Nero` (private). Commit terakhir `948e642`. Ada file belum dilacak `PROMPT_BANGUN_DARI_AWAL.md`, yang tidak akan saya sentuh. |
| Konvensi | Identifier bahasa Inggris; komentar, docstring, pesan galat, teks UI, dan nilai enum dalam **Bahasa Indonesia** (`antri`, `proses`, `selesai`, `gagal`). Tampilan hitam-putih (warna hanya untuk status). Subprocess memakai `CREATE_NO_WINDOW`. Tugas panjang bisa dibatalkan dan menampilkan progres. Abstraksi dibuat seperlunya. |

---

## 2. Peta komponen

Usulan lokasi baru: fitur Nero baru **`features/channel/`** (nama tampil "Channel Otomatis", alamat `/channel`).

| Komponen SPEC | Status | Lokasi di sistem lama | Catatan |
|---|---|---|---|
| Config (`config.yaml`) | ada → perlu diubah | `common.py`, `data/settings.json` | Bagian baru `"channel"` di `settings.json`, diedit dari halaman Nero. Perlu: (1) merge bertingkat, karena `_merge` sekarang hanya menggabung satu tingkat; (2) secret per fitur; (3) env var menimpa secret untuk CLI/CI. |
| Secret (`.env`) | ada → perlu diubah | `common.SECRET_KEYS` | Kunci baru: Groq, Pexels, Pixabay, Cloudflare (account id + token), YouTube (client id/secret/refresh token), Telegram (token + chat id). Disimpan di `settings.json`, tidak pernah dikirim ke halaman. Daftar nama env var didokumentasikan di `PANDUAN.md`. |
| Klien LLM + fallback | ada → perlu diubah | `gemini_ai.py`, `local_ai.py` | Pakai ulang, tanpa `google-genai`/`openai` SDK. Tambahan: parameter `temperature`, pembungkus `generate_json(prompt_name, variables, schema, temperature)` dengan validasi Pydantic, ulang sekali dengan pesan galat, backoff 429/5xx (`Retry-After`), lalu fallback ke Gemini → Groq (lewat jalur OpenAI-compatible `local_ai`, `https://api.groq.com/openai`) → Ollama (opsional). Log provider, model, token, dan durasi. |
| Skema Pydantic | belum ada → buat baru | — | `features/channel/schemas.py`. `pydantic` ditambahkan resmi ke requirements. |
| State (history/performance/learnings) | belum ada → buat baru | pola tulis atomik di `common.write_settings_file` | `data/channel/history.json`, `performance.json`, `learnings.md`. |
| Run harian + checkpoint | belum ada → buat baru | pola stage/progress/batal di `features/clip/api.py` | `data/channel/runs/YYYY-MM-DD/` berisi JSON per langkah. Hasil akhir (`final.mp4`, caption) disalin ke `output/channel/YYYY-MM-DD/` supaya tombol "Folder hasil" bekerja. Ada kunci (lock) per tanggal supaya tombol dan jadwal tidak berjalan bersamaan. |
| timeutil (WIB↔UTC) | belum ada → buat baru | — | `zoneinfo` di Windows butuh paket `tzdata` (tidak ada di lock), jadi `tzdata` ditambahkan. |
| Logging | ada → pakai ulang | `print` → `data/nero.log` (`desktop.log_to_file`) | Ditambah log per run (`run.log`) untuk 30 baris terakhir di alert. |
| CLI | belum ada → buat baru | — | `python -m features.channel run-daily [--date] [--dry-run] [--force STEP] [--only STEP]`, `llm-ping`. Memakai `argparse` dari stdlib. |
| Agen Analis | belum ada → buat baru | — | |
| Agen Riset | belum ada → buat baru | — | RSS (`feedparser`), Wikipedia REST (`requests`), YouTube `search.list` (memakai kredensial YouTube dari Fase I-5), dedupe `rapidfuzz`. |
| Agen Skrip, QC, SEO | belum ada → buat baru | — | `_make_title`/`_hashtags` di engine hanya heuristik clip, tidak dipakai. Prompt di `features/channel/prompts/*.md` (bisa diedit tanpa menyentuh kode). |
| TTS | belum ada → buat baru | — | `edge-tts` 7.3.1 (parameter word boundary dicek di source saat implementasi). Piper sebagai cadangan opsional. |
| Timing kata | ada → pakai ulang (cadangan) | `engine.extract_audio`, `engine.transcribe` | Whisper dengan `word_timestamps`, GPU lalu CPU otomatis. Dipakai bila word boundary edge-tts tidak tersedia. |
| Visual: stock | belum ada → buat baru | — | Pexels lalu Pixabay. **Tidak memakai yt-dlp/`download_video`** (lihat K12). |
| Visual: gambar AI + Ken Burns | belum ada → buat baru | `engine.run_ffmpeg` | Cloudflare Workers AI REST lewat `requests`; `zoompan`/`xfade` tersedia di `tools/ffmpeg.exe`. |
| Visual: motion graphic | sebagian ada → pakai ulang + baru | `engine._load_font`, `FONTS_DIR`, `CAPTION_FONTS`, `_title_image` | Font OFL yang sudah ada (Anton, Bebas Neue, Montserrat, Poppins). Template `big_number`, `bullet_list`, `versus`, `quote_card`, `title_card` dibuat baru. |
| Subtitle (ASS) | ada → pakai ulang | `engine.CaptionRenderer`, `presets.py`, pustaka gaya | Sudah memenuhi kebutuhan SPEC (2–4 kata, kapital, outline + shadow, karaoke/sorotan, posisi diatur). Tidak membuat ASS (lihat K5). `engine.write_srt` dipakai untuk menyimpan teks subtitle. |
| Render | ada → perlu diubah | `engine.render_clip`, `FrameComposer`, `video_encoder` | Langkah 1 (baru): ffmpeg menyambung klip segmen + `voice.mp3` + musik opsional → video dasar vertikal. Langkah 2 (pakai ulang): `render_clip` dengan tata letak `fill` → subtitle, judul, progress bar. `on_screen_text` per segmen dibakar saat membuat klip segmen, jadi engine tidak perlu diubah. Bila tetap perlu diubah, hanya parameter opsional dengan nilai bawaan lama. |
| Validasi render | ada → pakai ulang | `engine.probe` | Ukuran, fps, audio, durasi. `ffprobe` tidak dibutuhkan. |
| Publisher YouTube | belum ada → buat baru | — | `google-api-python-client` + `google-auth-oauthlib` (dry-run: tanpa downgrade paket lama). OAuth lewat tombol **"Hubungkan YouTube"** di Nero dengan redirect loopback ke server Nero sendiri. Skrip CLI `auth_youtube` tetap ada untuk keperluan CI. |
| Manual pack | belum ada → buat baru | — | Caption TikTok/IG. Pola tombol salin seperti caption clip di UI Clip Maker. |
| Telegram | belum ada → buat baru | — | `requests` (bukan `httpx`, supaya tidak menambah klien HTTP kedua). |
| Scheduler | belum ada → buat baru | `desktop.set_autostart`, `feature.start()` | Lihat K9 dan pertanyaan 4. |
| Halaman UI | ada → pakai ulang pola | `frontend/src/features/index.ts`, `src/features/clip/*` | Halaman `/channel`: status run hari ini per langkah, tombol "Jalankan sekarang (uji)", pratinjau video, skrip, metadata, caption siap salin, riwayat, pengaturan, kunci API, tombol "Hubungkan YouTube", sakelar aktif. Tampilan hitam-putih. |
| Tes | ada → pakai ulang | `tests/`, `tests/util.py`, pola `Sandbox` | `tests/test_channel_*.py` dengan `unittest` + `unittest.mock`, tanpa jaringan. |
| Flag fitur | belum ada → buat baru | — | `settings.channel.enabled = false` (bawaan). Saat `false`: jadwal tidak jalan dan upload/kirim Telegram tidak dilakukan. Uji coba (`--dry-run`) tetap bisa. |

Usulan isi folder (datar, tanpa subfolder berlapis, mengikuti gaya `features/clip`):

```
features/channel/
  __init__.py      ID, NAME, SETTINGS, bp, start, summary
  __main__.py      CLI
  api.py           rute /api/channel/*, pekerja, jadwal harian
  pipeline.py      orkestrator 12 langkah + checkpoint + alert
  schemas.py  llm.py  state.py  timeutil.py
  research.py      riset + dedupe
  writer.py        agen skrip, QC, SEO
  analyst.py       analis + laporan mingguan
  voice.py         TTS + timing kata
  visuals.py       stock, gambar AI + Ken Burns, motion graphic, cadangan
  render.py        video dasar + render_clip + validasi
  youtube.py       OAuth, upload, verifikasi, playlist
  telegram.py      notifikasi, alert, manual pack
  prompts/         research.md script.md qc.md seo.md analyst.md
frontend/src/features/channel/ + frontend/src/app/channel/page.tsx
tests/test_channel_*.py
```

---

## 3. Konflik dan usulan resolusi

| # | SPEC | Sistem lama | Usulan |
|---|---|---|---|
| K1 | Python 3.12, komentar kode bahasa Inggris | Python 3.14, komentar/docstring bahasa Indonesia | Ikut sistem lama: 3.14, identifier Inggris, komentar/teks Indonesia. Dry-run: semua paket baru punya wheel cp314 untuk Windows dan Linux. |
| K2 | Struktur `src/core`, `src/agents`, … | Satu folder per fitur di `features/` | `features/channel/` seperti di bagian 2. |
| K3 | `config.yaml` + `.env` | `data/settings.json` lewat UI | Bagian `"channel"` di `settings.json`, isinya sama dengan `config.yaml` SPEC. Env var (`GEMINI_API_KEY`, `PEXELS_API_KEY`, dll.) menimpa secret untuk CLI/CI. Prompt tetap berupa file `.md` yang di-commit. |
| K4 | `google-genai` SDK + `openai` SDK untuk Groq | Gemini & OpenAI-compatible lewat HTTP (`requests`) | Pakai ulang modul yang ada, tanpa SDK baru. Aturan aman: **QC tidak boleh diputuskan oleh model lokal kecil**. Jika hanya Ollama yang tersedia, QC dianggap gagal dan tidak ada upload. |
| K5 | Subtitle ASS lewat libass | `CaptionRenderer` (Pillow) + preset + pustaka gaya | Pakai `CaptionRenderer`: tampilannya sama dengan Clip Maker, gayanya bisa dipilih dari preset atau "Gaya saya", dan pratinjau di UI sudah ada. File `captions.srt` tetap disimpan. |
| K6 | Satu filter graph ffmpeg | Frame disusun di Python lalu di-pipe ke ffmpeg | Dua tahap: ffmpeg untuk video dasar, lalu `render_clip` untuk subtitle. Encoder ikut engine (NVENC bila ada, jika tidak `libx264`). |
| K7 | `ffprobe` | Tidak ada `ffprobe`; ada `engine.probe` | Pakai `engine.probe`. |
| K8 | `state/` di-commit ke repo | `data/` di-gitignore | Lokal: `data/channel/`. Jika GitHub Actions dipilih: CI memakai `NERO_DATA` ke folder state, lalu commit ke **branch terpisah `channel-state`** supaya riwayat `main` tidak penuh commit harian. |
| K9 | Scheduler GitHub Actions | Tidak ada scheduler; Nero sudah bisa buka otomatis saat Windows menyala | **Rekomendasi: jadwal di dalam Nero dulu.** Saat Nero menyala, jika run hari ini belum ada dan masih cukup waktu sebelum jam tayang, run dimulai (catch-up). Kelebihannya: GPU laptop terpakai, tidak ada secret ganda, review bisa di halaman Nero. Kekurangannya: laptop harus menyala sekali sehari. GitHub Actions bisa ditambah di fase terakhir memakai CLI yang sama. Hanya satu dari keduanya yang boleh menjadi pemilik state (lihat pertanyaan 4). |
| K10 | pytest, respx, ruff | unittest, tanpa linter | Tes memakai `unittest` + `mock`. ruff hanya sebagai alat dev untuk `features/channel` dan tes baru, tidak masuk `requirements.txt`. |
| K11 | `typer` | Belum ada CLI | `argparse`, supaya tidak menambah dependency. |
| K12 | Larangan yt-dlp / konten kreator lain | Clip Maker memakai yt-dlp | Clip Maker tidak diubah. Modul channel **tidak boleh** mengimpor `yt_dlp` atau `download_video`, dan ini dijaga oleh sebuah tes. |
| K13 | `httpx` | `requests` dipakai di semua modul | `requests`. |
| K14 | Review hanya lewat Telegram + Studio | Ada UI lokal | Keduanya. Telegram untuk ponsel, halaman `/channel` untuk pratinjau di laptop. |

---

## 4. Risiko

| # | Risiko | Pencegahan |
|---|---|---|
| R1 | **Paling besar.** Menurut dokumentasi resmi `videos.insert`, video yang di-upload lewat API dari project Google Cloud yang belum diaudit (dibuat setelah 28 Juli 2020) dikunci sebagai private. Akibatnya `publishAt` tidak akan membuatnya tayang, dan menurut beberapa sumber kuncinya tidak bisa dibuka. | Ajukan audit YouTube API (formulir gratis, bisa lama) sejak Fase I-5. Sampai lolos, mode bawaan `manual`: Nero menyiapkan video + judul/deskripsi/tag siap tempel dan **pengguna meng-upload sendiri** di YouTube Studio/app, sama seperti TikTok/IG. Upload via API diaktifkan setelah audit lolos. Langkah verifikasi `videos.list` menandai video yang terkunci. Biaya kuota upload akan dicek di tabel kuota resmi (sumber pihak ketiga tidak sepakat). |
| R2 | Mengubah `common.py` (merge bertingkat, secret per fitur) bisa mengubah cara pengaturan Clip Maker dibaca | Untuk kamus satu tingkat perilakunya dibuat identik. Ditambah tes khusus. 59 tes lama dijalankan sebelum dan sesudah tiap fase. |
| R3 | Perubahan pada `engine.py` merusak render Clip Maker | Sebisa mungkin tidak mengubah engine (overlay dibakar di klip segmen). Jika terpaksa, hanya parameter opsional dengan nilai bawaan lama. `test_render_gaya` dan `test_subtitle` harus tetap hijau. |
| R4 | Nero jadi lambat dibuka karena impor berat (google, aiohttp) | Impor dilakukan di dalam fungsi (lazy). Thread jadwal tidak melakukan apa-apa saat `enabled=false`. |
| R5 | GPU 4 GB dipakai bersamaan oleh Clip Maker, Whisper, dan Ollama | Pipeline channel punya satu pekerja sendiri. Timing utama dari edge-tts (tanpa Whisper); cadangan Whisper memakai model `base`. Ollama tidak dipakai secara bawaan. |
| R6 | Secret bocor ke halaman, log, atau git | Ada tes bahwa `/api/channel/settings` tidak pernah mengembalikan nilai secret. Secret tidak ditulis ke log. `data/` tetap di-gitignore. Di CI memakai GitHub Secrets. |
| R7 | Sekitar 30 paket baru | Dry-run di `.venv`: tidak ada paket lama yang di-downgrade (protobuf tetap 7.36.2), dan semua punya wheel cp314 untuk win_amd64 dan manylinux. `requirements-lock.txt` hanya ditambah. Alur `install.bat` tidak berubah. |
| R8 | `edge-tts` memakai endpoint Microsoft tidak resmi yang bisa berubah | Timing cadangan dari Whisper, Piper opsional (ketersediaan suara Indonesia dicek dulu), dan alert Telegram bila gagal. |
| R9 | Kuota gratis: Gemini (dan isi paket gratis boleh dipakai Google), Groq, Cloudflare 10.000 neurons/hari, Pexels, YouTube 10.000 unit/hari | Pemakaian dicatat di log. Gambar AI berhenti di 80% kuota. YouTube search maksimal 3 panggilan/hari. Fallback berantai. |
| R10 | Jalur Gemini sungguhan belum pernah dites dengan key asli (baru server palsu) | Bukti pertama Fase I-1 adalah `llm-ping` dengan key asli. |
| R11 | Kebijakan *inauthentic content* bisa berdampak ke seluruh channel | Guardrail bagian 8 masuk ke prompt dan kode. Saran: pakai channel baru (pertanyaan 6). |
| R12 | Upload ganda atau dua run bersamaan (tombol + jadwal) | Lock per tanggal, guard `video_id` di `youtube.json`, checkpoint per langkah. |
| R13 | Laptop mati/tidur saat jadwal (jika jadwal di Nero) | Catch-up saat Nero menyala. Jika video tidak bisa siap sebelum batas waktu, tayang digeser ke besok di jam yang sama (aturan SPEC 6.10). |
| R14 | Font Windows (Impact, Segoe, Georgia) tidak ada di runner Linux (jika CI dipakai) | Gaya bawaan channel memakai font OFL di `fonts/`. |

---

## 5. Urutan fase yang disesuaikan

Semua di branch `feat/auto-channel`, dengan commit kecil per langkah. Di akhir tiap fase: 59 tes lama + tes baru hijau, ada demo, dan `enabled` tetap `false`.

| Fase | Isi | Bukti selesai | Yang perlu kamu siapkan |
|---|---|---|---|
| **I-1 Fondasi & LLM** | Simpan `SPEC.md`, buat `CLAUDE.md` (belum ada) dan branch. Kerangka `features/channel` terdaftar (nonaktif). Bagian pengaturan + secret per fitur + env override + merge bertingkat. `schemas`, `state`, `timeutil` (+`tzdata`), `llm.generate_json` dengan fallback, CLI `llm-ping`. Tambah `pydantic`, `PyYAML`, `Jinja2`, `tzdata` ke requirements. | `python -m features.channel llm-ping` sukses untuk Gemini (dan Groq bila ada key). Tes WIB→UTC, skema, fallback 429, checkpoint hijau. | API key Gemini (AI Studio), opsional key Groq |
| **I-2 Otak konten** | Riset (RSS, Wikipedia, dedupe), skrip (rotasi format, variasi hook), QC + tulis ulang ≤2×, SEO, 5 file prompt | `run-daily --dry-run --only plan` menghasilkan `research.json`, `script.json`, `qc.json`, `metadata.json`, plus 1 contoh skrip untuk niche kamu | Daftar 3–10 RSS feed (atau saya usulkan) |
| **I-3 Media & render** | TTS + timing kata, visual (stock, gambar AI + Ken Burns, motion graphic, rantai cadangan), video dasar + `render_clip`, musik opsional, validasi | `final.mp4` lokal lolos validasi (1080×1920, 30 fps, ada audio, durasi 45–58 dtk ±3) dengan ≥2 jenis visual. Kamu cek videonya. | Key Pexels, Pixabay, Cloudflare (Account ID + token Workers AI). Opsional: musik bebas royalti |
| **I-4 Halaman Nero** | Halaman `/channel`: jalankan uji, progres per langkah, pratinjau, skrip, metadata, caption siap salin, riwayat, pengaturan, kunci, sakelar aktif. Kartu di Beranda. `npm run build` → `web/` | Kamu bisa menjalankan uji coba dan melihat hasilnya dari Nero tanpa terminal | — |
| **I-5 YouTube** | "Hubungkan YouTube" (OAuth loopback ke Nero) + skrip CLI, upload resumable dengan guard idempoten, `publishAt` WIB→UTC, verifikasi, playlist, YouTube search untuk riset. Paket "unggah manual" sebagai jalur sementara (R1) | 1 video uji ter-upload sebagai private (mode `manual`) dan terverifikasi. Pengajuan audit dimulai. | Project Google Cloud + OAuth client "Desktop app", consent screen **In production**. Formulir audit (saya bantu isi kontennya) |
| **I-6 Notifikasi** | Telegram (ringkasan, video, caption, skrip), manual pack, alert kegagalan dengan 30 baris log | Kamu menerima pesan lengkap di Telegram | Bot dari @BotFather + chat_id |
| **I-7 Analitik** | Analis (`videos.list` + Analytics API), `learnings.md`, laporan mingguan | `learnings.md` terbentuk (boleh "data belum cukup") | — |
| **I-8 Jadwal harian** | Sesuai jawaban pertanyaan 4: jadwal + catch-up di Nero, dan/atau `.github/workflows/daily.yml` (Python 3.14, state ke branch `channel-state`). `PANDUAN.md` diperbarui. | Run terjadwal berjalan sendiri (atau `workflow_dispatch` dry-run hijau + artifact berisi video) | GitHub Secrets (jika CI) |
| **I-akhir Aktivasi** | Dry-run ujung ke ujung, lalu kamu menyalakan `enabled` | 3 hari berturut-turut berjalan tanpa mengubah kode | — |

---

## 6. Pertanyaan untuk kamu

1. **Niche, penonton, nama channel.** Channel ini tentang apa, untuk siapa (contoh: "fakta sains unik, pelajar 15–25 tahun"), dan apa namanya? Topiknya lebih ke *evergreen* (sumber utama Wikipedia) atau mengikuti berita terbaru (RSS)?
2. **Tone & suara.** Gaya bahasa yang kamu mau (bawaan SPEC: "santai, cerdas, tidak lebay")? Suara pria `id-ID-ArdiNeural` atau wanita `id-ID-GadisNeural`?
3. **Jam tayang & mode persetujuan.** Jam tayang (bawaan 18.00 WIB)? Mode awal: saya sarankan `manual` sampai audit YouTube lolos, lalu `review_window`.
4. **Di mana jadwal harian berjalan?** (a) di Nero pada laptop: buka otomatis saat Windows menyala + catch-up (**saran saya**); (b) GitHub Actions (jalan walau laptop mati, render pakai CPU, state di branch `channel-state`); (c) Nero dulu, GitHub Actions ditambah di Fase I-8.
5. **Upload sebelum audit.** Karena R1, setujukah kamu bahwa sebelum audit YouTube lolos, Nero hanya menyiapkan video + paket unggah manual (kamu yang upload ke YouTube, TikTok, dan IG), dan upload otomatis baru dinyalakan setelah audit lolos?
6. **Channel YouTube.** Channel baru khusus untuk sistem ini (saran saya, supaya risiko kebijakan tidak menyentuh channel lain) atau channel yang sudah ada?

---

## 7. Keputusan (rencana disetujui 11 November 2026)

| # | Jawaban | Akibatnya di implementasi |
|---|---|---|
| 1 | "Semua genre, nanti saya yang pilih" | `channel.niche`, `audience`, dan `tone` adalah pengaturan, kosong saat awal. Genre dipilih di halaman Nero (Fase I-4); daftar genre siap pakai disiapkan di Fase I-2. Pipeline menolak berjalan selama genre belum dipilih. |
| 2 | Suara: kedua opsi disediakan | `tts.voice` bisa dipilih: `id-ID-ArdiNeural` (pria) atau `id-ID-GadisNeural` (wanita). Bawaan: Ardi. |
| 3 | Tayang 18.00 WIB; upload manual dulu | `schedule.publish_time = "18:00"`. `youtube.api_upload = false` sampai audit lolos. `approval.mode` bawaan `manual`. |
| 4 | Jadwal di Nero pada laptop | Jadwal + catch-up di dalam Nero (Fase I-8). GitHub Actions tidak dibuat (bisa ditambah nanti). State di `data/channel/`. |
| 5 | Paket unggah manual sebelum audit | Nero menyiapkan video + judul/deskripsi/tag siap tempel. Upload API dibuat di Fase I-5, tetapi tetap mati sampai audit lolos. |
| 6 | Channel baru | Analis hanya membaca video buatan sistem ini (dari `history.json`). |

Catatan dokumentasi yang dicek di Fase I-1:
- Gemini Interactions API: `generation_config` berisi `thinking_level` (`minimal|low|medium|high`), `max_output_tokens`, dan `seed`; `temperature` tidak tercantum, jadi suhu hanya dikirim ke Groq/Ollama. Token dibaca dari `usage.total_input_tokens` dan `usage.total_output_tokens`.
- Groq: base `https://api.groq.com/openai/v1`. Model produksi yang mendukung `json_schema` strict: `openai/gpt-oss-120b` dan `openai/gpt-oss-20b`. Error 429 membawa header `retry-after` (detik).
- `zoneinfo` di Windows gagal tanpa paket `tzdata` (sudah dibuktikan di `.venv`).

Temuan Fase I-2 (uji nyata 11 Nov 2026):
- Wikipedia membalas 429 bila permintaan terlalu rapat. Solusinya satu permintaan per kata kunci (`generator=search` +
  TextExtracts `exintro`), jeda 0,6 dtk, dan ulang dengan Retry-After. TextExtracts membatasi `exchars` ±1.200 karakter,
  jadi teks panjang diambil penuh lalu dipotong di kode.
- Server gratis Gemini sedang penuh: `gemini-3.8-flash` habis waktu, lalu kena 429 (kuota). Demo selesai memakai
  `gemini-3.5-flash-lite`.
- QC oleh model ringan terlalu longgar. Karena itu QC wajib mengisi `checks`: satu pemeriksaan per pernyataan dengan
  kutipan bukti persis. Kode lalu memastikan (a) kutipan itu ada di sumber, (b) semua segmen diperiksa, dan (c) kata
  mutlak ("hanya", "paling", "banyak yang mengira", …) punya padanan di bukti. Nuansa makna tetap bisa lolos, jadi
  manusia tetap memeriksa (mode `manual`).
- Catatan untuk Fase I-3: `on_screen_text` kadang sama dengan isi grafik; render tidak boleh menampilkan keduanya.

Temuan Fase I-3 (dokumentasi resmi & uji nyata 12 Nov 2026):
- edge-tts 7.3.1: boundary bawaan `SentenceBoundary`, jadi harus diminta `WordBoundary` (offset/durasi dalam satuan
  100 ns). Teks WordBoundary tanpa tanda baca; angka dan satuan bisa jadi satu kata ("September 2007", "-272 °C"),
  sehingga dicocokkan kembali ke token naskah. SSML/`<break>` tidak didukung, jadi tiap bagian narasi disuarakan
  terpisah lalu disambung dengan jeda. `stream_sync()` bisa menggantung bila ada galat, jadi dipakai `stream()`
  async. Di laptop ini koneksi IPv6 ke layanan Microsoft kadang langsung putus (WinError 64); IPv4 stabil, jadi IPv4
  dicoba lebih dulu.
- Kecepatan bicara terukur (rate +5%): Ardi 0,357 dtk/kata, Gadis 0,469 dtk/kata (±31% lebih lambat). Naskah 111 kata
  hanya 42 dtk dengan Ardi tetapi 54 dtk dengan Gadis. `video.words_range` kini bawaannya otomatis per suara
  (Ardi ±122–146 kata, Gadis ±93–111 kata) supaya narasi masuk 45–58 dtk; nilai tetap bisa dikunci di pengaturan.
- Cloudflare Workers AI: `flux-1-schnell` (usulan SPEC) tidak menerima width/height, jadi gambar vertikal harus
  dipotong dari persegi. Bawaan diganti ke `@cf/black-forest-labs/flux-2-klein-4b` (multipart, width/height 256–1920,
  seed; ±156 neurons per gambar 768×1344, ±60 gambar/hari dalam kuota gratis). Kuota habis = HTTP 429 kode 3036
  (tidak dicoba ulang); kode 3040 = kapasitas penuh sementara (dicoba ulang). Pemakaian dicatat per tanggal UTC dan
  gambar AI berhenti di 80% kuota.
- Pexels: `Authorization: <key>` tanpa Bearer, `orientation=portrait`; entri `hls` tanpa ukuran dilewati. Pixabay:
  tidak ada filter orientasi untuk video (dipilih di kode), hasil pencarian wajib di-cache 24 jam, key ada di URL
  sehingga URL tidak pernah ditulis ke log. Kredit pembuat klip ditambahkan ke deskripsi.
- Piper: satu-satunya suara Indonesia (`id_ID-news_tts-medium`) lisensinya tidak jelas, jadi cadangan Piper belum
  dipasang. Bila edge-tts gagal 3× per bagian, langkah TTS gagal dan pipeline berhenti (tanpa upload).
- `engine.render_clip` dipakai apa adanya: video dasar vertikal selalu memakai tata letak `fill` tanpa pelacakan
  wajah. Pembatalan tidak boleh berupa `RuntimeError`, karena engine akan pindah permanen dari NVENC ke libx264.
  Gaya subtitle diambil dari pustaka Clip Maker (`video.caption_style`), dengan potong jeda, zoom, dan judul
  dimatikan, serta posisi subtitle dijaga di 55–65% tinggi layar.
- Warna: klip stok (BT.709, kadang rentang penuh) dan grafik/gambar AI (BT.601 tanpa tanda) yang disambung tanpa
  encode ulang dibaca dengan aturan warna klip pertama, sehingga warnanya bergeser (kuning 230,230,0 → 231,215,0).
  Semua klip antara kini dikonversi dan diberi tanda BT.709 rentang TV. Encode akhir memakai opsi gaya baru
  `color_bt709` di `engine.render_clip` (bawaan mati, jadi Clip Maker tidak berubah).
- Review adversarial (2 putaran, 8+2 agen) menemukan masalah checkpoint: hasil lama (QC, suara, video, salinan di
  folder hasil) bisa terpakai lagi setelah skrip/pengaturan berubah atau langkah gagal di tengah jalan. Solusinya
  sidik masukan per langkah (`inputs.json`, termasuk pengaturan yang relevan), penghapusan hasil lama saat langkah
  mulai bekerja, dan penarikan salinan video di folder hasil. Langkah media hanya diulang bila sidiknya berubah,
  jadi `--force seo/qc` tidak membuat ulang suara dan gambar AI.
- Durasi: narasi yang pasti terlalu pendek untuk validasi render (< target minimal − 3 dtk) kini ditangani di langkah
  TTS (naskah dipanjangkan sekali, mirip pemendekan), sebelum kuota visual terpakai. Di atas itu tetap peringatan
  (SPEC 6.7). Video yang sah tetapi variasi visualnya kurang ditandai `needs_review` (tidak boleh upload otomatis).
- Uji nyata 12 Nov (topik Voyager 1) menemukan dua salah-tangkap QC Fase I-2. (1) Angka di nama diri ("Voyager 1")
  dianggap klaim jumlah; kini angka yang menempel pada nama yang tertulis di sumber diabaikan. (2) Kata mutlak di
  kalimat mitos ("Ada anggapan bahwa … seluruh …") dianggap klaim; kini di kalimat tanya/mitos hanya klaim tentang
  anggapan orang ("banyak orang") yang diperiksa.
- Wikipedia Indonesia bisa usang: artikel Voyager 1 masih menulis "sekarang berada di heliosheath" dan "masih di
  sabuk Kuiper", padahal artikel yang sama dan Wikipedia Inggris mencatat Voyager 1 sudah memasuki ruang
  antarbintang sejak 2012. Prompt skrip & QC kini meminta pertentangan semacam ini ditandai, tetapi QC dengan
  `gemini-3.5-flash-lite` menggolongkannya sebagai "quality" sehingga naskah tetap lolos. Pemeriksaan manusia
  (mode `manual`) dan model yang lebih kuat (Groq) tetap diperlukan.
- Gemini ringan selalu menulis ±110–120 kata walau diminta 122–146. Karena itu ada dua rentang. Prompt meminta
  rentang ideal (`config.words_range`). Pemeriksaan kode di QC hanya menolak naskah di luar batas keras
  (`config.words_limits`, Ardi ±112–169, Gadis ±85–128), yaitu jumlah kata yang pasti menghasilkan video di luar
  target ±3 dtk walau rate disesuaikan.
- Kunci run milik proses yang sudah mati (Nero ditutup paksa) langsung dilepas lewat pemeriksaan PID, tidak
  menunggu 3 jam.

Temuan Fase I-4 (review adversarial 4 agen, 12 Nov 2026):
- Setelah riset/naskah diulang dengan "Rencana saja", video, suara, dan metadata lama milik naskah sebelumnya
  masih tampil sebagai "Video siap" di samping judul baru. Kini pipeline mencatat naskah asal tiap hasil
  (`<langkah>_src` di inputs.json), dan halaman hanya menampilkan hasil yang cocok dengan naskah sekarang.
- Windows: halaman yang memantau run membaca berkas JSON setiap 1–5 detik. Penulisan/penghapusan berkas oleh
  pipeline bisa bentrok sesaat (PermissionError), sehingga run gagal acak. Kini penulisan atomik dan penghapusan
  mencoba ulang sebentar, dan pembaca langsung menutup berkas. Video yang sedang diputar browser menahan berkasnya
  ±15 dtk setelah dijeda, jadi penghapusan video dicoba sampai 20 dtk, dan halaman menyembunyikan pemutar video
  tanggal yang sedang dibuat ulang.
- Batal tadinya hanya dicek di antara langkah, sehingga riset/naskah/QC bisa jalan bermenit-menit setelah Batal.
  Kini event batal dipasang per thread (`cancel.py`) dan dicek sebelum setiap panggilan AI/permintaan Wikipedia.
  Jeda coba ulang juga langsung berhenti. Batal di langkah terakhir tidak lagi dilaporkan "selesai".
- Simpan pengaturan kini atomik: genre + isian lain divalidasi utuh sebelum disimpan. Genre hanya dikirim bila
  diganti, supaya pengaturan lain tidak tertimpa. Volume musik dibatasi -60..0 dB. Key dari environment variable
  ditandai dan tidak bisa "dihapus" dari halaman.
- Uji nyata dari halaman (build produksi, 12 Nov) menghasilkan tiga temuan:
  - "Mulai dari riset lagi" memilih topik yang sama, karena topik hari itu belum masuk riwayat. Kini topik riset
    sebelumnya ikut dihindari.
  - QC ringan menggolongkan kalimat mitos ("Ada anggapan bahwa…") sebagai fakta tanpa bukti. Kini kode
    memperlakukannya sebagai framing (`writer.is_myth_statement`), dan prompt QC menjelaskan hal yang sama.
  - Gemini ringan konsisten menulis ±100–115 kata. Naskah di bawah batas keras ditulis ulang oleh QC, jadi uji coba
    dengan model ringan lebih sering gagal QC. Key Groq tetap disarankan.
