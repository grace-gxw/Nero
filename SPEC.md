# SPEC — Sistem Channel Video Otomatis Multi-Agen (versi gratis)

> **Cara pakai — proyek baru:** simpan file ini sebagai `SPEC.md` di folder kosong, buka Claude Code di folder itu, lalu kirim:
> `Baca SPEC.md sampai habis, lalu kerjakan Fase 0. Jangan lanjut ke fase berikutnya sebelum saya bilang OK.`
>
> **Cara pakai — membangun di atas sistem yang sudah ada:** simpan file ini sebagai `SPEC.md` di root repo yang sudah ada, buka Claude Code di repo itu, lalu kirim:
> `Ini repo sistem saya yang sudah berjalan. Baca SPEC.md sampai habis, lalu kerjakan Mode Integrasi (bagian 14) Fase I-0: audit read-only dan tulis INTEGRATION_PLAN.md. Jangan mengubah kode apa pun sebelum saya menyetujui rencananya.`

> **Catatan untuk repo Nero:** SPEC ini dipakai dalam Mode Integrasi. Keputusan yang menyesuaikan SPEC dengan
> sistem Nero (Python 3.14, `data/settings.json`, `features/channel/`, jadwal di Nero, dll.) ada di
> `INTEGRATION_PLAN.md` dan berlaku di atas bagian 2–4 dan 9 di bawah.

---

## 0. Peran kamu dan cara kerja

Kamu adalah senior Python engineer yang membangun sistem ini bersama saya. Aturan kerja:

1. **Bahasa:** berkomunikasi dengan saya dalam Bahasa Indonesia. Kode, nama variabel, dan komentar kode dalam bahasa Inggris. README dan panduan setup dalam Bahasa Indonesia.
2. **Bertahap:** kerjakan per fase (lihat bagian 12). Di akhir setiap fase: jalankan test, tunjukkan hasil/demo, ringkas apa yang sudah jadi, sebutkan apa yang harus saya lakukan (misalnya membuat API key), lalu **berhenti dan tunggu OK saya**.
3. **Jangan menebak API:** library berikut sering berubah — `google-genai`, `edge-tts`, `moviepy`, `google-api-python-client`, `faster-whisper`. Sebelum memakai fungsi/parameter tertentu, cek versi yang ter-install (`pip show`, `help()`, atau source code di site-packages) dan dokumentasi resminya. Nama model LLM juga jangan di-hardcode; ambil dari `config.yaml`.
4. **Sederhana:** jangan pakai framework agen (LangChain, CrewAI, AutoGen, dll). Setiap "agen" adalah modul Python biasa: satu file prompt + satu skema Pydantic + satu fungsi `run()`.
5. **Aman:** jangan pernah commit secret (`.env`, `client_secret*.json`, token). Buat `.gitignore` sejak awal.
6. **Gratis:** semua komponen default harus gratis. Provider berbayar (Anthropic/OpenAI) boleh ada sebagai opsi, tapi **nonaktif secara default**.
7. **Idempoten:** setiap langkah pipeline bisa diulang tanpa efek ganda. Yang paling penting: **satu run tidak boleh meng-upload video dua kali.**

---

## 1. Tujuan sistem

Setiap hari, tanpa campur tangan saya (kecuali review singkat), sistem harus:

1. Belajar dari performa video sebelumnya.
2. Riset topik yang relevan dan segar di niche channel.
3. Menulis skrip video pendek (Shorts/Reels/TikTok, vertikal, 45–58 detik).
4. Memeriksa fakta dan kualitas skrip (QC), lalu menulis ulang bila gagal.
5. Membuat metadata SEO (judul, deskripsi, tag, hashtag).
6. Membuat voice-over (TTS) beserta timing per kata.
7. Mencari dan membuat klip video yang cocok untuk tiap segmen: stock footage, gambar AI yang dianimasikan, atau motion graphic buatan sendiri.
8. Merender video 1080×1920 dengan subtitle besar yang di-burn-in.
9. Meng-upload ke YouTube sebagai private dengan jadwal tayang (mode review).
10. Mengirim notifikasi Telegram berisi preview video, link YouTube Studio, skrip, dan paket caption untuk TikTok/Instagram (diposting manual oleh saya).
11. Menyimpan riwayat dan data performa untuk putaran berikutnya.

Semua berjalan otomatis setiap hari lewat **GitHub Actions** dengan biaya Rp0.

---

## 2. Tech stack (wajib gratis)

| Bagian | Pilihan utama | Cadangan |
|---|---|---|
| Bahasa | Python 3.12 | — |
| CLI | `typer` | — |
| Config | `pydantic` + `pydantic-settings` + `PyYAML` | — |
| LLM | Gemini API via `google-genai` (model Flash gratis) | Groq (OpenAI-compatible, via `openai` SDK dengan `base_url`) |
| Riset | RSS (`feedparser`), YouTube Data API `search.list`/`videos.list`, Wikipedia REST API | `pytrends` (tidak resmi, default off) |
| TTS | `edge-tts` (suara `id-ID-ArdiNeural` / `id-ID-GadisNeural`) | Piper TTS lokal |
| Timing kata | word boundary dari `edge-tts` (jika versi ter-install mendukung) | `faster-whisper` (model `base`, CPU, bahasa `id`) |
| Visual: stock | Pexels Video API (`orientation=portrait`) | Pixabay Video API |
| Visual: gambar AI | Cloudflare Workers AI, model `@cf/black-forest-labs/flux-1-schnell` (gratis 10.000 neurons/hari, reset 00.00 UTC; sekitar 170 gambar/hari pada pengaturan default) → dianimasikan dengan ffmpeg `zoompan` (efek Ken Burns) | Pindah ke stock atau motion graphic |
| Visual: motion graphic | Dibuat sendiri dengan `Pillow` + ffmpeg (kartu teks, angka besar, daftar poin, perbandingan) | Background polos + teks |
| Render | `ffmpeg` via `subprocess` + subtitle format ASS (libass) | — |
| Thumbnail (opsional) | `Pillow` | — |
| Upload | YouTube Data API v3 (`google-api-python-client`, resumable upload) | — |
| Analitik | YouTube Data API `videos.list` + YouTube Analytics API | — |
| Notifikasi | Telegram Bot API (`httpx`) | — |
| Fuzzy dedupe | `rapidfuzz` | — |
| Test | `pytest`, `respx`/`unittest.mock` | — |
| Scheduler | GitHub Actions (cron) | jalan lokal via cron/Task Scheduler |
| Penyimpanan state | file JSON di folder `state/`, di-commit balik ke repo oleh workflow | — |

---

## 3. Konfigurasi (`config.yaml`)

Semua perilaku diatur dari sini. Isi placeholder `ISI_...` saat Fase 0 berdasarkan jawaban saya.

```yaml
channel:
  name: "ISI_NAMA_CHANNEL"
  niche: "ISI_NICHE"                 # contoh: "fakta sains unik", "sejarah Nusantara"
  audience: "ISI_TARGET_PENONTON"    # contoh: "pelajar & mahasiswa 15–25 tahun"
  language: "id"
  region: "ID"
  tone: "santai, cerdas, tidak lebay, tidak clickbait menyesatkan"
  banned_topics: ["politik praktis", "SARA", "saran medis", "rekomendasi investasi spesifik", "tragedi/bencana terkini"]

video:
  target_seconds: [45, 58]
  words_range: [110, 135]            # sesuaikan setelah mengukur durasi TTS nyata
  fps: 30
  resolution: [1080, 1920]
  formats_rotation: ["myth_vs_fact", "list_3", "story", "question", "explainer"]
  music_dir: "assets/music"          # diisi manual dengan musik bebas royalti; kosong = tanpa musik
  music_volume_db: -22
  font_file: "assets/fonts/Montserrat-ExtraBold.ttf"   # font OFL, commit ke repo

schedule:
  timezone: "Asia/Jakarta"
  publish_time: "18:00"
  videos_per_day: 1

approval:
  mode: "review_window"              # review_window | manual | auto

llm:
  providers: ["gemini", "groq"]      # urutan fallback
  gemini_model: "ISI_SETELAH_CEK_DOKUMENTASI"
  groq_model: "ISI_SETELAH_CEK_DOKUMENTASI"
  anthropic_model: ""                # opsional, nonaktif
  temperature_creative: 0.9
  temperature_strict: 0.2
  max_retries: 4

visuals:
  allowed_types: ["stock", "ai_image", "motion_graphic"]
  max_ai_image_ratio: 0.6            # maksimal 60% segmen memakai gambar AI
  min_types_per_video: 2             # minimal 2 jenis visual dalam satu video
  ai_image:
    provider: "cloudflare"
    model: "@cf/black-forest-labs/flux-1-schnell"
    width: 768
    height: 1344                     # mendekati 9:16; di-upscale lalu di-crop ke 1080x1920
    steps: 4
    style_suffix: "cinematic lighting, high detail, vertical composition, no text"
  ken_burns: true                    # zoom/pan pelan agar gambar terasa hidup
  ai_video_provider: ""              # opsional & berbayar (text-to-video); kosong = nonaktif

tts:
  engine: "edge"                     # edge | piper
  voice: "id-ID-ArdiNeural"
  rate: "+5%"

research:
  rss_feeds: []                      # isi 3–10 feed relevan niche
  youtube_search_queries: []         # 1–3 query, hemat kuota search
  evergreen_mode: false              # true = topik tak harus berita terbaru; sumber utama Wikipedia
  lookback_hours: 72
  use_pytrends: false

youtube:
  category_id: "27"                  # 27 = Education, 28 = Science & Technology, 24 = Entertainment
  default_tags: []
  contains_synthetic_media: true
  playlist_id: ""
  made_for_kids: false

notify:
  telegram: true
  weekly_report_day: "monday"
```

Secret **tidak** boleh ada di `config.yaml`. Secret dibaca dari environment variable (lokal via `.env`, di CI via GitHub Secrets):

```
GEMINI_API_KEY=
GROQ_API_KEY=
PEXELS_API_KEY=
PIXABAY_API_KEY=
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=     # token dengan izin Workers AI
YT_CLIENT_ID=
YT_CLIENT_SECRET=
YT_REFRESH_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ANTHROPIC_API_KEY=        # opsional
```

---

## 4. Struktur repo

```
.
├── SPEC.md
├── CLAUDE.md                 # ringkasan konvensi proyek untuk sesi Claude Code berikutnya
├── README.md                 # panduan setup lengkap (Bahasa Indonesia)
├── config.yaml
├── .env.example
├── .gitignore
├── requirements.txt
├── main.py                   # CLI typer
├── prompts/                  # prompt tiap agen, bisa saya edit tanpa menyentuh kode
│   ├── research.md
│   ├── script.md
│   ├── qc.md
│   ├── seo.md
│   └── analyst.md
├── src/
│   ├── core/
│   │   ├── config.py
│   │   ├── llm.py            # klien LLM + fallback + JSON mode
│   │   ├── schemas.py        # semua model Pydantic
│   │   ├── state.py          # history, performance, learnings
│   │   ├── runctx.py         # folder run harian + checkpoint
│   │   ├── timeutil.py       # WIB <-> UTC, RFC3339
│   │   └── log.py
│   ├── agents/
│   │   ├── analyst.py
│   │   ├── research.py
│   │   ├── scriptwriter.py
│   │   ├── qc.py
│   │   └── seo.py
│   ├── media/
│   │   ├── tts.py
│   │   ├── timings.py
│   │   ├── visuals.py        # pemilih jenis visual + stock + cadangan
│   │   ├── ai_images.py      # Cloudflare Workers AI + animasi Ken Burns
│   │   ├── graphics.py       # template motion graphic (Pillow)
│   │   ├── captions.py       # pembuat file .ass
│   │   └── render.py
│   ├── publish/
│   │   ├── youtube.py
│   │   ├── manual_pack.py
│   │   └── telegram.py
│   └── pipeline.py           # orkestrator
├── scripts/
│   └── auth_youtube.py       # dijalankan sekali di laptop untuk mendapat refresh token
├── assets/
│   ├── fonts/
│   └── music/
├── state/
│   ├── history.json
│   ├── performance.json
│   └── learnings.md
├── runs/                     # di-.gitignore; output harian (diunggah sebagai artifact CI)
├── tests/
└── .github/workflows/daily.yml
```

---

## 5. Alur pipeline dan kontrak data

Perintah utama: `python main.py run-daily [--date YYYY-MM-DD] [--dry-run] [--force STEP] [--only STEP]`

Setiap langkah menulis output ke `runs/YYYY-MM-DD/`. Jika file output sudah ada dan valid terhadap skemanya, langkah itu **di-skip** (checkpoint), kecuali `--force`. `--dry-run` menjalankan semuanya **kecuali** upload YouTube dan pengiriman Telegram (output Telegram dicetak ke konsol).

| # | Langkah | Input | Output |
|---|---|---|---|
| 1 | `analyst` | `state/history.json`, YouTube API | `state/performance.json`, `state/learnings.md` |
| 2 | `research` | config, RSS, YouTube search, Wikipedia, learnings, history | `research.json` |
| 3 | `script` | `research.json`, learnings, history (format & hook terakhir) | `script.json` |
| 4 | `qc` | `script.json`, `research.json` | `qc.json` (loop tulis ulang maksimal 2×) |
| 5 | `seo` | `script.json`, `research.json` | `metadata.json` |
| 6 | `tts` | `script.json` | `voice.mp3`, `words.json` |
| 7 | `visuals` | `script.json`, `words.json` | `images/*.png` (gambar AI), `clips/*.mp4` (semua jenis visual sudah jadi klip), `visuals.json` |
| 8 | `render` | semua di atas | `final.mp4`, `captions.ass`, `render.json` (hasil ffprobe) |
| 9 | `publish_youtube` | `final.mp4`, `metadata.json` | `youtube.json` |
| 10 | `manual_pack` | `metadata.json` | `tiktok_caption.txt`, `instagram_caption.txt` |
| 11 | `notify` | semua | pesan Telegram |
| 12 | `state_update` | semua | `state/history.json` diperbarui |

Jika langkah mana pun gagal: log error lengkap, kirim alert Telegram (nama langkah + 30 baris log terakhir), hentikan pipeline dengan exit code ≠ 0. **Jangan pernah meng-upload jika QC gagal.**

### Skema data utama (implementasikan sebagai model Pydantic)

**`research.json`**
```json
{
  "date": "2026-11-12",
  "candidates": [
    {
      "topic": "string",
      "angle": "sudut pandang unik yang membuat video ini berbeda",
      "why_now": "string atau 'evergreen'",
      "sources": [{"title": "string", "url": "string", "published": "ISO date|null", "snippet": "string"}],
      "scores": {"relevance": 0, "freshness": 0, "novelty": 0, "learnings_fit": 0}
    }
  ],
  "chosen_index": 0,
  "rejected_as_duplicate": ["topik yang mirip riwayat"]
}
```

**`script.json`**
```json
{
  "topic": "string",
  "format": "myth_vs_fact | list_3 | story | question | explainer",
  "hook": "≤ 12 kata, 2–3 detik pertama, memancing rasa ingin tahu tanpa menipu",
  "segments": [
    {
      "id": 1,
      "text": "kalimat narasi",
      "visual_type": "stock | ai_image | motion_graphic",
      "visual_query": "english stock footage keywords (untuk stock, dan cadangan untuk jenis lain)",
      "image_prompt": "english prompt untuk gambar AI (wajib jika visual_type = ai_image)",
      "graphic": {"template": "big_number | bullet_list | versus | quote_card | title_card", "content": "teks/angka singkat"},
      "on_screen_text": "opsional ≤ 5 kata"
    }
  ],
  "cta": "ajakan singkat dan natural",
  "word_count": 0,
  "claims": [{"claim": "string", "source_url": "string"}]
}
```

**`qc.json`**
```json
{
  "pass": true,
  "score": 0,
  "issues": [{"type": "fact | policy | quality | repetition | length", "detail": "string", "fix": "string"}]
}
```
Lulus jika `score ≥ 7` **dan** tidak ada issue bertipe `fact` atau `policy`.

**`metadata.json`**
```json
{
  "title": "≤ 70 karakter",
  "description": "deskripsi + daftar sumber + kredit footage + hashtag",
  "tags": ["..."],
  "hashtags": ["#Shorts", "..."],
  "category_id": "27",
  "language": "id"
}
```

---

## 6. Spesifikasi tiap komponen

### 6.1 Klien LLM (`src/core/llm.py`)
- Interface: `generate_json(prompt_name: str, variables: dict, schema: type[BaseModel], temperature: float) -> BaseModel`.
- Prompt dimuat dari `prompts/{prompt_name}.md` dan diisi dengan Jinja2.
- Gemini: pakai JSON mode (`response_mime_type="application/json"`, plus `response_schema` jika didukung versi SDK). Groq: `response_format={"type": "json_object"}`, dengan skema dicantumkan di prompt.
- Validasi dengan Pydantic. Jika gagal, ulangi **sekali** dengan pesan error validasi dimasukkan ke prompt.
- Error 429/5xx: exponential backoff (hormati `Retry-After`), lalu fallback ke provider berikutnya sesuai `llm.providers`.
- Catat provider, model, jumlah token, dan durasi setiap panggilan ke log.
- Buat perintah `python main.py llm-ping` untuk mengetes semua provider.

### 6.2 Agen Analis (`analyst`) — jalan paling awal
- Ambil video 28 hari terakhir dari `history.json`. Untuk tiap video: `videos.list` (statistics: views, likes, comments).
- Coba juga YouTube Analytics API (`views`, `averageViewDuration`, `averageViewPercentage`, `subscribersGained`) per video. Data ini terlambat 2–3 hari, jadi bungkus dengan try/except dan jangan sampai menggagalkan pipeline.
- Simpan ke `performance.json`.
- LLM (`prompts/analyst.md`, temperature rendah) merangkum menjadi `learnings.md`, maksimal 15 poin: topik/format/hook yang berhasil, yang gagal, dan saran konkret. Jika data kurang dari 5 video, tulis "data belum cukup" dan jangan mengarang pola.
- Setiap `weekly_report_day`, kirim laporan mingguan ke Telegram: top 3 dan bottom 3 video, plus tren.

### 6.3 Agen Riset (`research`)
- Kumpulkan bahan mentah:
  - RSS feed dari config (item dalam `lookback_hours` terakhir).
  - YouTube `search.list` maksimal 3 panggilan per hari (`regionCode=ID`, `relevanceLanguage=id`, `order=viewCount`, `publishedAfter=7 hari lalu`), dilanjutkan `videos.list` untuk statistiknya. Gunanya hanya untuk melihat tema yang sedang diminati. **Dilarang menyalin isi video orang lain.**
  - Mode evergreen: ambil ringkasan artikel dari Wikipedia REST API (id.wikipedia.org dan en.wikipedia.org) sebagai sumber fakta.
- LLM (`prompts/research.md`) menghasilkan 5 kandidat topik. Setiap kandidat wajib punya minimal 1 sumber dari bahan mentah; kandidat tanpa sumber dibuang.
- Dedupe terhadap riwayat 60 hari: `rapidfuzz` (token_set_ratio ≥ 80 dianggap duplikat), lalu pengecekan semantik oleh LLM.
- Pilih kandidat dengan skor total tertinggi. Topik di `banned_topics` otomatis ditolak.

### 6.4 Agen Penulis Skrip (`script`)
Prompt (`prompts/script.md`) harus menuntut:
- Format diambil dari `formats_rotation`, dan **tidak boleh sama dengan 2 video terakhir**. Struktur hook juga harus bervariasi (cek 5 hook terakhir di history).
- Jumlah kata dalam `words_range`. Bahasa Indonesia lisan yang natural, kalimat pendek, tanpa basa-basi pembuka seperti "Halo guys".
- Hook di 2–3 detik pertama. Setiap segmen membawa satu informasi konkret. Penutup berisi payoff ditambah CTA singkat.
- Setiap klaim faktual dicantumkan di `claims` beserta `source_url` dari `research.json`. Dilarang menambah fakta yang tidak ada di sumber.
- Pilih `visual_type` per segmen sesuai isinya:
  - `stock` untuk hal nyata yang umum (alam, kota, orang beraktivitas, benda sehari-hari).
  - `ai_image` untuk hal yang sulit dicari di stock: ilustrasi konsep, rekonstruksi suasana sejarah, makhluk/objek imajinatif, sudut pandang tidak biasa.
  - `motion_graphic` untuk angka, perbandingan, daftar poin, atau kutipan.
  - Patuhi `max_ai_image_ratio` dan `min_types_per_video` dari config.
- `visual_query` selalu diisi dalam bahasa Inggris, 2–4 kata, konkret dan mudah dicari di stock footage (misalnya "ocean waves aerial", bukan "konsep kebebasan"). Field ini juga dipakai sebagai cadangan jika jenis visual lain gagal.
- `image_prompt` dalam bahasa Inggris, deskriptif: subjek, latar, pencahayaan, sudut kamera. Dilarang: orang nyata yang bisa dikenali, logo/merek, karakter berhak cipta, teks di dalam gambar, dan gambar realistis yang bisa disangka foto asli dari peristiwa nyata.
- Learnings dari analis disisipkan ke prompt.

### 6.5 Agen QC (`qc`)
- Temperature rendah. Cocokkan setiap klaim dengan snippet sumber. Klaim yang tidak didukung sumber = issue `fact`.
- Cek kebijakan: `banned_topics`, saran medis/keuangan/hukum, klaim menyesatkan, menyebut atau meniru orang nyata secara merugikan, konten yang tidak pantas → issue `policy`. Periksa juga setiap `image_prompt` terhadap aturan gambar AI di bagian 8.
- Cek kualitas: hook kuat, tidak repetitif dibanding history, alur jelas, panjang sesuai.
- Jika gagal: kirim `issues` ke agen skrip untuk ditulis ulang, maksimal 2 kali. Jika tetap gagal, pipeline berhenti dan kirim alert Telegram berisi alasannya.

### 6.6 Agen SEO (`seo`)
- Judul ≤ 70 karakter, jujur (sesuai isi), memancing rasa ingin tahu, bahasa Indonesia.
- Deskripsi berisi: 2–3 kalimat ringkasan, bagian "Sumber:" (URL dari `claims`), kredit "Video: Pexels/Pixabay" (jika ada klip stock), keterangan "Sebagian visual dibuat dengan AI" (jika `uses_ai_images`), dan 3–5 hashtag termasuk `#Shorts`.
- 8–15 tag. Kategori dan bahasa diambil dari config.

### 6.7 TTS (`src/media/tts.py`, `timings.py`)
- `edge-tts` dengan voice dan rate dari config. Gabungkan seluruh teks (hook + segmen + CTA) dengan jeda alami antar segmen.
- Timing per kata: gunakan word boundary dari `edge-tts` jika versi ter-install mendukungnya (cek parameter yang benar di source code-nya). Jika tidak, jalankan `faster-whisper` (`base`, `language="id"`, `word_timestamps=True`) pada `voice.mp3`.
- `words.json` berisi `[{"word", "start", "end", "segment_id"}]`.
- Ukur durasi audio. Jika di atas `target_seconds` maksimum, naikkan rate maksimal +15%. Jika masih kelebihan, minta agen skrip memendekkan teks. Jika di bawah minimum, beri peringatan saja.
- Cadangan Piper: aktif jika `tts.engine: piper` atau `edge-tts` gagal 3 kali.

### 6.8 Visual (`src/media/visuals.py`, `ai_images.py`, `graphics.py`)
Durasi setiap segmen dihitung dari `words.json`. Output setiap jenis visual adalah klip `clips/seg_{id}.mp4` berukuran 1080×1920, 30 fps, tanpa audio, dengan durasi sama dengan segmennya. Dengan begitu render (6.9) tidak perlu tahu jenis visualnya.

**a. `stock`**
- Cari di Pexels (`orientation=portrait`). Pilih klip dengan durasi ≥ durasi segmen dan tinggi ≥ 1080 jika ada. Unduh file ukuran sedang, jangan yang 4K.
- Jangan pakai ulang klip yang dipakai 30 hari terakhir (simpan ID klip di history). Klip yang sama juga tidak boleh muncul dua kali dalam satu video.
- Jika Pexels kosong, coba Pixabay. Jika keduanya kosong, coba ulang dengan query yang disederhanakan (1–2 kata).

**b. `ai_image` (membuat klip dari gambar AI)**
- Panggil Cloudflare Workers AI REST API (`POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}`) dengan `image_prompt` + `style_suffix` dari config. Cek format respons di dokumentasi terbaru; biasanya gambar dikembalikan sebagai base64.
- Ukuran dari config (mendekati 9:16, kelipatan yang didukung model). Simpan ke `images/seg_{id}.png`. Simpan prompt dan seed untuk reproduksi.
- Ubah gambar menjadi klip dengan ffmpeg `zoompan`. Untuk menghindari getaran, upscale dulu ke resolusi besar, lalu zoom pelan 1.0→1.12 atau pan pelan. Arah gerak dipilih bergantian antar segmen supaya tidak monoton.
- Panjang segmen > 6 detik: buat 2 gambar berbeda dan gabungkan dengan crossfade singkat.
- Jika kuota harian habis (error dari API) atau gagal 3 kali: pakai cadangan `stock` dengan `visual_query`, lalu `motion_graphic`.
- Catat jumlah neurons yang terpakai (perkiraan) di log. Hentikan pembuatan gambar AI jika mendekati 80% kuota gratis harian.

**c. `motion_graphic` (membuat klip grafis sendiri)**
- Render frame dengan `Pillow` memakai template:
  - `big_number`: angka besar dengan animasi hitung naik.
  - `bullet_list`: poin muncul satu per satu.
  - `versus`: dua kolom perbandingan.
  - `quote_card`: kutipan atau fakta singkat.
  - `title_card`: judul di tengah layar.
- Font dan palet warna konsisten (definisikan di config, atau di satu modul tema).
- Gabungkan frame menjadi klip dengan ffmpeg. Animasi sederhana (fade, slide, scale) cukup dibuat dengan menghitung frame di Python.
- Background berupa warna solid atau gradien halus dari palet tema, atau gambar AI/stock yang diburamkan.

**d. Cadangan terakhir dan pencatatan**
- Jika semua cara gagal, gunakan background warna polos dengan `on_screen_text` besar.
- Simpan detail setiap segmen di `visuals.json`: jenis visual akhir, sumber, ID, URL, pembuat, prompt AI, dan cadangan yang dipakai. Cache unduhan di `runs/.cache/`.
- Jika ada segmen `ai_image` yang berhasil, set `uses_ai_images: true`. Agen SEO menambahkan keterangan "Sebagian visual dibuat dengan AI" di deskripsi.

### 6.9 Render (`src/media/render.py`, `captions.py`)
- Pakai ffmpeg langsung via `subprocess` (lebih cepat dan stabil daripada MoviePy). Logika filter graph dibungkus fungsi yang bisa dites.
- Setiap klip: potong sesuai durasi segmen, lalu `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920`, `fps=30`, tanpa audio asli.
- Gabungkan semua klip, tempelkan `voice.mp3`. Musik latar opsional: pilih acak dari `music_dir`, volume `music_volume_db`, fade in/out, dan dipotong sesuai durasi.
- Subtitle ASS:
  - 2–4 kata per tampilan, font dari config, ukuran besar (±80–90 px pada 1080 lebar).
  - Huruf kapital, outline tebal plus shadow.
  - Kata yang sedang diucapkan diberi warna highlight (efek karaoke).
  - Posisi di tengah, sekitar 55–65% tinggi layar. Hindari 20% bawah dan sisi kanan karena tertutup UI Shorts/TikTok.
- `on_screen_text` (jika ada) tampil di bagian atas selama segmennya.
- Encode: `libx264 -preset veryfast -crf 23 -pix_fmt yuv420p`, `aac 128k`, `-movflags +faststart`.
- Validasi dengan `ffprobe`: 1080×1920, 30 fps, ada stream audio, durasi dalam `target_seconds` ±3 detik. Simpan hasilnya di `render.json`. Jika tidak valid, pipeline gagal.

### 6.10 Publisher YouTube (`src/publish/youtube.py`)
- Kredensial dibuat dari `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, dan `YT_REFRESH_TOKEN` (`google.oauth2.credentials.Credentials` dengan `token_uri`). Scopes: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`.
- **Guard idempoten:** jika `youtube.json` sudah berisi `video_id`, jangan upload lagi.
- Resumable upload (`MediaFileUpload(resumable=True, chunksize=8MB)`), retry dengan backoff untuk error 5xx dan koneksi (maksimal 5 kali).
- Body:
  - `snippet`: title, description, tags, categoryId, defaultLanguage, defaultAudioLanguage.
  - `status`: `privacyStatus: "private"`, `selfDeclaredMadeForKids` sesuai config, `containsSyntheticMedia` sesuai config.
  - Mode `review_window`: tambahkan `publishAt` = hari ini pukul `publish_time` WIB, dikonversi ke UTC format RFC3339. Jika waktu itu sudah lewat, jadwalkan besok di jam yang sama.
  - Mode `manual`: private tanpa `publishAt`.
  - Mode `auto`: `privacyStatus: "public"`.
- Setelah upload: panggil `videos.list` untuk memverifikasi status dan `publishAt`. Jika tidak sesuai harapan (misalnya video terkunci private oleh YouTube), tandai di `youtube.json` dan kirim peringatan Telegram.
- Jika `playlist_id` diisi, tambahkan video ke playlist itu.
- `youtube.json` berisi `{video_id, url, studio_url, privacy, publish_at_wib, verified}`.

`scripts/auth_youtube.py`: dijalankan sekali di laptop. Memakai `InstalledAppFlow` dengan client OAuth tipe "Desktop app", lalu mencetak refresh token untuk disalin ke GitHub Secrets.

### 6.11 Manual pack dan Telegram
- `manual_pack`: buat caption TikTok (≤ 150 karakter + 3–5 hashtag) dan caption Instagram Reels (lebih panjang + hashtag) dari `metadata.json`.
- `telegram.py` (`httpx`, Bot API, `parse_mode=HTML`). Kirim pesan berurutan:
  1. Ringkasan: topik, judul, format, skor QC, durasi, jadwal tayang WIB, link YouTube Studio. Untuk mode `review_window`, tambahkan kalimat: "Kalau tidak cocok, ubah ke Private/hapus di Studio sebelum jam tayang."
  2. `sendVideo` berisi `final.mp4` (jika ≤ 50 MB; jika lebih, kirim pesan bahwa file ada di artifact GitHub Actions).
  3. Caption TikTok dan Instagram, masing-masing sebagai pesan terpisah supaya mudah disalin.
  4. Skrip lengkap (dalam blok `<pre>`).
- Fungsi `alert(step, error, log_tail)` untuk kegagalan.

### 6.12 State (`src/core/state.py`)
- `history.json`: satu entri per video berisi `{date, topic, title, format, hook, video_id, clip_ids, qc_score, duration}`.
- Semua tulisan ke file state bersifat atomik (tulis ke file sementara, lalu rename).

---

## 7. Mode persetujuan

| Mode | Perilaku | Kapan dipakai |
|---|---|---|
| `review_window` (default) | Upload private + `publishAt` jam tayang. Saya punya waktu dari pagi sampai jam tayang untuk membatalkan. | Pemakaian harian normal |
| `manual` | Upload private tanpa jadwal; saya yang menekan publish | Minggu-minggu awal / niche sensitif |
| `auto` | Langsung public | Tidak disarankan |

TikTok dan Instagram **selalu** manual: sistem hanya menyiapkan file video dan caption, lalu mengirimnya ke Telegram.

---

## 8. Aturan konten (guardrails) — wajib masuk ke prompt dan kode

1. **Orisinal, bukan produksi massal.** Rotasi format, variasi struktur hook, dan setiap video harus membawa insight konkret. Ini penting supaya tidak terkena kebijakan YouTube soal *inauthentic content* (konten massal/berulang tidak bisa dimonetisasi, dan sanksinya bisa berlaku untuk seluruh channel).
2. **Fakta harus bersumber.** Klaim tanpa sumber ditolak QC. Sumber dicantumkan di deskripsi.
3. **Tidak ada materi berhak cipta.** Visual hanya dari Pexels/Pixabay, gambar AI yang dibuat sistem sendiri, atau motion graphic buatan sendiri. Musik hanya dari `assets/music` yang saya isi sendiri. Dilarang mengunduh atau memakai ulang video YouTube/TikTok orang lain, lirik lagu, atau logo merek.
4. **Aturan gambar AI.** Dilarang membuat:
   - Gambar orang nyata yang bisa dikenali.
   - Logo atau merek.
   - Karakter berhak cipta.
   - Gambar realistis yang bisa disangka foto/rekaman asli peristiwa nyata (misalnya bencana, tokoh, atau kejadian berita).
   Jika gambar AI dipakai untuk menggambarkan peristiwa sejarah atau berita, beri label "ilustrasi" di layar.
5. **Tidak meniru orang nyata.** Tidak ada suara/wajah tokoh nyata, tidak ada kutipan palsu.
6. **Tidak ada nasihat medis, keuangan, atau hukum** yang disajikan sebagai saran personal. Hindari `banned_topics`.
7. **Judul jujur.** Boleh bikin penasaran, tidak boleh menjanjikan hal yang tidak ada di video.
8. **Pengungkapan AI.** Isi `containsSyntheticMedia` sesuai config. Jika `uses_ai_images` bernilai true dan gambarnya realistis, wajib `true`.
9. **Ada manusia di dalam loop** secara default (mode `review_window`).

---

## 9. GitHub Actions (`.github/workflows/daily.yml`)

- Trigger:
  - `schedule: - cron: "17 22 * * *"` (05.17 WIB; menit tidak bulat supaya tidak ikut antrean jam :00).
  - `workflow_dispatch` dengan input `dry_run` (boolean) dan `date` (opsional).
- `concurrency: { group: daily-pipeline, cancel-in-progress: false }`, `permissions: contents: write`, `timeout-minutes: 40`.
- Langkah:
  1. Checkout repo.
  2. `actions/setup-python` (3.12, cache pip).
  3. `sudo apt-get install -y ffmpeg`.
  4. `pip install -r requirements.txt`.
  5. Cache model faster-whisper dengan `actions/cache` (jika dipakai).
  6. `python main.py run-daily` (tambah `--dry-run` sesuai input).
  7. Upload `runs/<tanggal>/` sebagai artifact (`retention-days: 7`), juga saat gagal (`if: always()`).
  8. Commit perubahan `state/` dengan pesan `chore(state): update YYYY-MM-DD [skip ci]`, lalu push.
- Secret diambil dari `${{ secrets.* }}` sebagai env.
- Catatan untuk README: jadwal cron GitHub bisa terlambat beberapa menit sampai jam. Scheduled workflow juga bisa dinonaktifkan otomatis jika repo lama tidak ada aktivitas; commit state harian membantu menjaga repo tetap aktif. Sarankan repo **private**: kuota gratis 2.000 menit/bulan cukup untuk video harian.

---

## 10. README (Bahasa Indonesia, langkah demi langkah)

README wajib berisi:
1. Gambaran sistem dan diagram alur sederhana.
2. Cara mendapatkan setiap key:
   - **Gemini:** buat API key di Google AI Studio.
   - **Groq:** buat API key di console.groq.com.
   - **Pexels** dan **Pixabay:** daftar akun, lalu ambil API key.
   - **Cloudflare Workers AI:** daftar akun gratis, salin Account ID, lalu buat API token dengan izin Workers AI.
   - **Telegram:** buat bot lewat @BotFather, lalu cara mendapatkan `chat_id`.
   - **YouTube:** buat project di Google Cloud, aktifkan YouTube Data API v3 dan YouTube Analytics API, atur OAuth consent screen tipe External, buat OAuth client tipe Desktop app, lalu jalankan `scripts/auth_youtube.py`.
3. **PENTING:** ubah publishing status OAuth consent screen ke **"In production"**. Jika dibiarkan di "Testing", refresh token kedaluwarsa setelah 7 hari dan upload akan berhenti. Aplikasi production yang belum diverifikasi akan menampilkan layar peringatan "Google hasn't verified this app". Untuk pemakaian pribadi itu wajar: klik Advanced → lanjutkan.
4. Cara mengisi GitHub Secrets.
5. Cara menjalankan lokal: `python main.py run-daily --dry-run`.
6. Cara mengganti niche, suara, atau jam tayang (cukup edit `config.yaml`), dan cara mengedit prompt di `prompts/`.
7. Troubleshooting:
   - Error 429.
   - Refresh token invalid.
   - Video terkunci private.
   - Klip Pexels tidak ditemukan.
   - edge-tts gagal.
   - Workflow tidak jalan.

---

## 11. Testing dan kriteria selesai

- Unit test (tanpa jaringan, LLM dan HTTP di-mock):
  - Konversi WIB → UTC RFC3339, termasuk kasus jam tayang yang sudah lewat.
  - Chunking subtitle dan pembuatan file ASS.
  - Dedupe topik.
  - Validasi semua skema.
  - Checkpoint skip/force.
  - Guard upload idempoten.
  - Rotasi format (tidak sama dengan 2 video terakhir).
  - Pemilihan jenis visual mematuhi `max_ai_image_ratio` dan `min_types_per_video`.
  - Rantai cadangan visual (`ai_image` → `stock` → `motion_graphic` → background polos) saat API gagal atau kuota habis.
  - Pembuat filter `zoompan` dan template motion graphic menghasilkan klip 1080×1920 dengan durasi tepat.
  - Fallback provider LLM saat 429.
- Test integrasi: `python main.py run-daily --dry-run` menghasilkan `final.mp4` yang lolos validasi ffprobe.
- `ruff` bersih. Semua test hijau sebelum setiap fase dinyatakan selesai.

**Sistem dianggap selesai jika:** workflow GitHub Actions berjalan dari cron, video terunggah ke YouTube sebagai private terjadwal, Telegram menerima ringkasan + video + caption, dan `state/` ter-update otomatis — tiga hari berturut-turut tanpa intervensi kode.

---

## 12. Fase pengerjaan

| Fase | Isi | Bukti selesai |
|---|---|---|
| **0. Setup** | Tanyakan ke saya sekaligus (maksimal 6 pertanyaan): niche, target penonton, gaya bahasa/tone, suara pria/wanita, jam tayang, mode persetujuan. Lalu isi `config.yaml`, buat struktur repo, `.gitignore`, `.env.example`, `CLAUDE.md`, kerangka README, `requirements.txt`. | Tampilkan tree repo dan `config.yaml` |
| **1. Core** | `config.py`, `llm.py` (Gemini + Groq + fallback), `schemas.py`, `state.py`, `runctx.py`, `timeutil.py`, logging, CLI dasar | `python main.py llm-ping` sukses, unit test hijau |
| **2. Otak konten** | Agen research, script, QC, SEO beserta file prompt-nya | `python main.py run-daily --dry-run --only plan` menghasilkan JSON; tunjukkan 1 contoh skrip untuk niche saya |
| **3. Media** | TTS, timing kata, visual (stock + gambar AI animasi + motion graphic), subtitle ASS, render | `final.mp4` lokal lolos ffprobe dan memakai minimal 2 jenis visual; saya cek videonya |
| **4. YouTube** | `auth_youtube.py`, publisher, verifikasi | 1 video uji ter-upload sebagai private (mode `manual`) |
| **5. Notifikasi** | Telegram, manual pack, alert error | Saya menerima pesan lengkap di Telegram |
| **6. Analitik** | Analis, `learnings.md`, laporan mingguan | `learnings.md` terbentuk (boleh "data belum cukup") |
| **7. Otomasi** | Workflow GitHub Actions, README final, uji `workflow_dispatch` dry-run di CI | Run CI hijau + artifact berisi video |

---

## 13. Hal yang DILARANG

- Memakai layanan berbayar sebagai default.
- Upload ke TikTok/Instagram lewat otomasi browser atau API tidak resmi (melanggar ToS, berisiko akun diblokir).
- Mengunduh atau memakai ulang konten kreator lain (yt-dlp dan sejenisnya).
- Commit secret atau token dalam bentuk apa pun.
- Upload jika QC gagal atau validasi render gagal.
- Meng-hardcode nama model, ID kategori, jam tayang, atau teks prompt di dalam kode Python.
- Melanjutkan ke fase berikutnya tanpa OK dari saya.

---

## 14. Mode Integrasi — membangun di atas sistem yang sudah ada

Bagian ini berlaku jika `SPEC.md` ditaruh di repo yang sudah berisi kode. Jika ada yang bertentangan dengan bagian lain, **bagian ini yang menang**. Bagian 0 (cara kerja), 8 (guardrails), dan 13 (larangan) tetap berlaku penuh.

### 14.1 Prinsip
1. **Audit dulu, read-only.** Sebelum Fase I-0 disetujui, jangan membuat, mengubah, atau menghapus file apa pun selain `INTEGRATION_PLAN.md`.
2. **Konvensi sistem lama menang.** Bahasa pemrograman, framework, struktur folder, gaya kode, tool lint/test, cara menyimpan config dan secret yang sudah ada tetap dipakai. Stack di bagian 2 dan struktur di bagian 4 hanyalah rekomendasi untuk komponen yang benar-benar belum ada.
   - Sistem lama berbasis Node.js/TypeScript (atau bahasa lain): komponen baru ditulis dalam bahasa itu. Pengecualian: modul Python terpisah yang dipanggil sebagai CLI, jika itu jauh lebih masuk akal (misalnya untuk render atau faster-whisper). Opsi ini harus diusulkan dan ditanyakan dulu.
   - Sistem lama berbasis n8n/Make/Zapier: komponen baru dibuat sebagai workflow/node di sana, atau sebagai service kecil yang dipanggil lewat HTTP/webhook.
3. **Pakai ulang, jangan duplikasi.** Jika sudah ada klien LLM, uploader YouTube, scheduler, logging, notifikasi, atau penyimpanan data, gunakan yang ada. Perluas jika perlu, jangan membuat versi kedua.
4. **Jangan merusak yang sudah jalan.**
   - Kerja di branch baru (`feat/auto-channel`), commit kecil per langkah, dengan pesan commit yang jelas.
   - Hal berikut harus disetujui saya dulu: menghapus atau me-rename file, mengubah skema database, mengubah interface/endpoint publik, dan meng-upgrade dependency mayor.
   - Jalankan test yang sudah ada **sebelum dan sesudah** setiap fase. Test yang tadinya hijau harus tetap hijau.
   - Semua fitur baru dibungkus flag config (misalnya `auto_channel.enabled: false` secara default) sampai saya mengaktifkannya.
5. **State dan data.** Jika sistem lama sudah punya database atau penyimpanan, simpan history, performance, dan learnings di sana (tabel/collection baru lewat migrasi yang saya setujui), bukan di folder `state/`.
6. **Scheduler.** Jika sudah ada scheduler (cron, Celery beat, n8n schedule, GitHub Actions, dll), tambahkan job harian di sana. Jangan membuat scheduler kedua.
7. **Secret.** Ikuti mekanisme secret yang sudah ada. Secret baru ditambahkan ke tempat yang sama dan didokumentasikan di `.env.example` (atau padanannya).
8. **CLAUDE.md.** Jika sudah ada, tambahkan bagian baru tentang modul auto-channel. Jangan menimpa isi yang lama.

### 14.2 Isi `INTEGRATION_PLAN.md` (hasil Fase I-0)
1. **Ringkasan sistem lama:** bahasa dan framework, cara menjalankan, struktur folder utama, dependency penting, config/secret, scheduler, integrasi eksternal yang sudah ada, kondisi test dan CI, branch dan status git.
2. **Peta komponen:** tabel setiap komponen di bagian 5–6 SPEC (LLM client, analyst, research, script, QC, SEO, TTS, timing, visuals, render, YouTube publisher, manual pack, Telegram, state, scheduler). Kolomnya:
   - Status: `ada → pakai ulang`, `ada → perlu diubah`, atau `belum ada → buat baru`.
   - Lokasi file di sistem lama.
   - Catatan.
3. **Konflik dan usulan resolusi:** tempat sistem lama berbeda dari SPEC (stack, struktur, penyimpanan, scheduler, alur review), beserta rekomendasi untuk masing-masing.
4. **Risiko:** bagian yang bisa rusak akibat integrasi dan cara mencegahnya.
5. **Urutan fase yang disesuaikan:** fase 1–7 dari bagian 12, hanya untuk komponen berstatus `perlu diubah` atau `buat baru`. Setiap fase punya bukti selesai yang jelas.
6. **Pertanyaan untuk saya** (maksimal 6): hal yang belum bisa disimpulkan dari kode, misalnya niche, jam tayang, mode persetujuan, atau pilihan saat ada konflik.

### 14.3 Fase Mode Integrasi
| Fase | Isi | Bukti selesai |
|---|---|---|
| **I-0. Audit** | Baca kode, jalankan test yang ada (tanpa mengubah apa pun), tulis `INTEGRATION_PLAN.md` | Saya menyetujui rencana |
| **I-1 … I-n** | Kerjakan sesuai urutan di rencana yang sudah disetujui, satu fase per waktu | Test lama dan baru hijau, demo per fase, flag masih `false` |
| **I-akhir. Aktivasi** | Uji coba dry-run end-to-end, lalu saya mengaktifkan flag | 3 hari berturut-turut berjalan normal tanpa intervensi kode |
