# Nero — Panduan Singkat

Nero adalah aplikasi pribadi berisi kumpulan alat untuk membantu menghasilkan uang dari internet. Semua berjalan di komputer Anda sendiri; data dan hasilnya tidak dikirim ke mana pun (kecuali ke Claude bila Anda mengisi API key).

Fitur yang tersedia sekarang:

| Fitur | Kegunaan |
|---|---|
| **Clip Maker** | Ubah video panjang (dari link atau file) menjadi video pendek vertikal 9:16 siap upload ke TikTok, Instagram Reels, dan YouTube Shorts. |

Fitur lain akan ditambahkan di versi berikutnya dan otomatis muncul di menu.

## Instalasi (sekali saja)

1. Ekstrak folder `Nero` ke tempat yang mudah, misalnya `D:\Nero`.
2. Klik dua kali **install.bat**. Ia akan menginstal Python, FFmpeg, Deno (dibutuhkan untuk unduhan YouTube), paket yang diperlukan, dan model transkrip (sekitar 500 MB). Bila komputer punya kartu grafis NVIDIA, pustaka CUDA (sekitar 1 GB) ikut dipasang supaya transkrip jauh lebih cepat.
   - Jika muncul pesan "Python selesai diinstal", tutup jendelanya lalu jalankan **install.bat** sekali lagi.
3. Selesai. Ikon **Nero** muncul di Desktop dan Start Menu. Butuh koneksi internet dan ruang disk kurang lebih 3 GB.

## Memakai Nero

1. Klik dua kali ikon **Nero** di Desktop (atau cari "Nero" di Start Menu). Browser akan terbuka di `http://127.0.0.1:7860`. Tidak ada jendela hitam: Nero berjalan diam-diam sampai dimatikan.
   - Bila Nero sudah berjalan, ikon cukup membuka halamannya lagi (misalnya setelah tab browser tertutup).
   - Ikon belum ada (mis. pindah dari versi lama)? Jalankan **run.bat** sekali, buka **Pengaturan**, lalu klik **Buat ikon di Desktop**.
   - **run.bat** tetap bisa dipakai bila ingin melihat pesan Nero di jendela hitam. Saat dibuka lewat ikon, pesan yang sama ditulis ke `data\nero.log`.
2. Halaman pertama adalah **Beranda**: ringkasan semua fitur (berapa tugas sedang berjalan, berapa hasil yang sudah jadi). Pilih fitur dari kartu di Beranda atau dari menu di kiri (di layar kecil: menu di atas).
3. Di bawah menu ada:
   - **Folder hasil**: membuka folder `output`. Setiap fitur punya subfoldernya sendiri, mis. `output\clip`.
   - **Pengaturan**: pengaturan umum yang dipakai semua fitur (Claude API key, model AI, cookies browser), ikon Nero di Desktop, dan **Jalankan otomatis saat Windows menyala** (Nero langsung siap di latar belakang tanpa membuka browser; buka halamannya lewat ikon Nero).
   - **Matikan Nero**: menghentikan Nero. Menutup tab browser saja tidak mematikan Nero.
   - Tanda **Terhubung** / **Terputus** (bila terputus, Nero sedang mati: buka lagi lewat ikon Nero) dan apakah **AI Claude** aktif.

### Pindah dari ClipMaker versi lama

Cukup timpa berkas aplikasinya (folder `data` dan `output` jangan dihapus). Saat pertama dibuka, Nero otomatis memindahkan proyek lama ke `data\clip\projects` dan hasil render lama ke `output\clip`; pengaturan dan API key tetap terbawa. Bila ada berkas yang sedang dibuka program lain, berkas itu dilewati dan dipindah saat Nero dibuka berikutnya (jendela run.bat atau `data\nero.log` memberi tahu).

Folder aplikasi boleh diganti namanya (mis. `ClipMaker` menjadi `Nero`); tidak perlu instal ulang. Setelah folder dipindah atau diganti namanya, jalankan **run.bat** sekali: ikon Nero di Desktop otomatis diarahkan ke lokasi baru.

## Clip Maker

Buka **Clip Maker** dari menu.

1. **Masukkan video**: tempel satu atau banyak link (satu per baris), atau tarik file video dari komputer. Tentukan jumlah clip, **bahasa** yang diucapkan di video, dan **Gaya video ini** (biarkan "Gaya aktif", atau pilih gaya lain khusus untuk video ini), lalu tekan **Proses**.
2. Aplikasi akan mengunduh, membuat transkrip, dan mencari momen paling menarik. Setiap momen diberi **skor** dan **alasan**. Proses yang sedang berjalan bisa dihentikan dengan **✕ Batal**.
3. Periksa daftar clip:
   - Ubah judul/hook (teks yang tampil di atas video).
   - Geser waktu mulai/selesai, atau pakai tombol −1 / +1 detik.
   - **Pratinjau** untuk menonton bagian aslinya.
   - **Transkrip** untuk memilih kalimat sendiri lalu menjadikannya clip.
   - **Subtitle** untuk mengoreksi kata yang salah dengar (nama orang, istilah, bahasa gaul). Waktu tiap kata tetap dipakai, jadi sebaiknya hanya mengganti kata, jangan menambah/menghapus banyak. Koreksi tersimpan di transkrip proyek, jadi ikut berlaku untuk clip lain di bagian yang sama. Clip yang sudah dirender diberi tanda **Subtitle diubah, render ulang**.
   - Hapus centang clip yang tidak ingin dirender.
4. Pilih gaya (lihat **Gaya video** di bawah), lalu tekan **Render**. Analisis video lain tetap bisa berjalan bersamaan dengan render. Gaya satu video bisa diganti kapan saja lewat pilihan gaya di baris tombol proyeknya; clip yang sudah dirender perlu dirender ulang.
5. Hasil muncul di daftar dan tersimpan di folder `output\clip` (tombol **Folder hasil** di atas halaman Clip Maker). Untuk setiap clip ada:
   - `.mp4` video 1080×1920 siap upload
   - `.srt` subtitle terpisah
   - `_caption.txt` caption postingan + hashtag (tombol **Caption** juga menyalinnya)

Centang **Langsung render otomatis** bila ingin semua momen langsung dijadikan video tanpa diperiksa dulu.

**Pengaturan clip** (tombol di atas halaman Clip Maker) berisi model transkrip, bahasa video bawaan, jumlah clip bawaan, dan durasi minimum/maksimum clip.

### Referensi & instruksi (timeline riset, clip contoh, prompt)

Klik **Referensi & instruksi** di kartu "Masukkan video" (atau **Cari ulang + referensi** pada proyek yang sudah ada) untuk mengarahkan hasil. Tulis satu referensi per baris:

```
12:30 - 13:45 Kenapa bisnis pertamaku bangkrut?
1:02:10 - 1:03:00 tips mengatur uang
45:20 momen lucu
https://www.tiktok.com/@akun/video/...
https://youtu.be/IDVIDEO?t=754
Fokus pada momen tentang "investasi". Judul berupa pertanyaan. Jangan ambil bagian sponsor.
```

| Baris | Hasilnya |
|---|---|
| `mm:ss - mm:ss` atau `j:mm:ss - j:mm:ss` (boleh `12.30 - 13.45`, `s/d`, `sampai`) | **Timeline riset**: pasti dijadikan clip dengan waktu itu (dirapikan ke awal/akhir kalimat bila selisihnya ≤ 2 detik). Teks setelah waktu menjadi judul (mode lokal) atau catatan untuk AI. |
| `mm:ss catatan` | **Titik waktu**: dibuatkan clip yang dimulai sedikit sebelum titik itu. |
| Link clip pendek orang lain (TikTok/Reels/Shorts) | **Clip referensi**: audionya diunduh dan ditranskrip, lalu dicari bagian yang sama di video Anda. Bila ketemu, bagian itu dijadikan clip. Bila tidak ada (clip dari video lain), dipakai sebagai contoh gaya/topik. Maksimal 5 link, masing-masing maks 15 menit. |
| Link YouTube video ini dengan `?t=` | Dianggap titik waktu. |
| Teks lain | **Instruksi/prompt**. Dengan Claude API key, instruksi dibaca AI apa adanya (topik, gaya judul, bahasa caption, dll). Tanpa API key, kata-katanya dipakai sebagai kata kunci topik: `"frasa dalam kutip"` dicocokkan utuh, dan kata setelah *jangan/hindari/tanpa* diberi skor minus. |

Link di kolom ini hanya menentukan **momen** yang dipotong, bukan tampilan editannya. Untuk meniru tampilan, lihat bagian berikut.

Di bawah kolom muncul daftar **Dikenali** supaya Anda bisa memastikan setiap baris terbaca dengan benar. **Jumlah clip** adalah total: timeline dan titik Anda selalu masuk, sisanya diisi momen pilihan otomatis. Bila hanya ingin clip dari riset Anda, isi Jumlah clip sama dengan banyaknya timeline. Clip dari referensi diberi tanda **Timeline Anda**, **Titik riset Anda**, atau **Sama dengan clip referensi**.

### Gaya video

Kartu **2. Gaya video** berisi:

- **Gaya aktif**: dipakai untuk semua video, kecuali video yang diberi gaya sendiri. **Atur detail** membuka pengaturan lengkap dengan pratinjau langsung di sebelahnya; setiap perubahan tersimpan otomatis. **Simpan** menyimpan gaya aktif ke Gaya saya.
- **Tiru gaya dari clip**: baca gaya editing dari clip orang lain (lihat bagian berikut).
- **Gaya saya**: semua gaya yang pernah Anda baca dari clip atau simpan sendiri. **Siap pakai**: 11 gaya bawaan (Klasik Kuning, Tegas Hijau, Kotak Ungu, Karaoke Biru, Satu Kata Cepat, Minimalis Putih, Gaya Berita, Sinematik, Santai Lucu, Neon Pink, Hitam Putih). Klik gambar untuk memakainya; tombol **ⓘ** di pojok gambar untuk melihat detail, mengganti nama, membaca ulang, atau menghapus.

Watermark @akun, letaknya, dan perataan volume adalah milik Anda: tidak berubah saat gaya diganti.

Pilihan di **Atur detail**:

| Bagian | Pilihan |
|---|---|
| Subtitle | Font (Impact, Anton, Bebas Neue, Montserrat, Poppins, Arial Black, Segoe UI Black, Bahnschrift, Verdana, Georgia, Comic Sans; contohnya tampil dengan huruf aslinya), warna teks & warna sorotan (9 warna), **cara menyorot kata** (kata berwarna, kotak berwarna di belakang kata, karaoke, tanpa sorotan), kata per tampilan (1–4), **animasi muncul** (pop, memantul, naik perlahan, tanpa), **garis tepi** (tanpa/tipis/sedang/tebal), ukuran, posisi, HURUF BESAR, sorot kata kunci & angka, bayangan, kotak gelap di belakang subtitle |
| Judul | Ada/tidak, kotak putih/kuning/merah/hitam atau tulisan saja, lama tampil, **ukuran**, **posisi** |
| Video & efek | Tata letak (ikuti wajah, dua orang, video utuh + latar blur, **video utuh + latar hitam**, potong tengah), **zoom** (pelan maju-mundur, atau **zoom hentak** yang berganti dekat/jauh di akhir kalimat seperti jump cut), **filter warna** (cerah, hangat, dingin, sinematik, pudar, hitam putih), hapus jeda diam, progress bar di bawah atau **di atas** |
| Watermark & suara | Watermark @akun di bawah atau di atas, ratakan volume suara |

**Sorot kata kunci**: angka dan kata pemancing perhatian (rahasia, ternyata, juta, gagal, dll.) selalu berwarna sorotan dan sedikit lebih besar. **Hapus jeda**: diam lebih dari 0,3 atau 0,6 detik dibuang otomatis; audio tetap sinkron dan `.srt` ikut menyesuaikan.

### Tiru gaya dari clip referensi (AI lokal gratis)

Klik **Tiru gaya dari clip**, tempel link clip yang gaya editannya ingin ditiru (TikTok/Reels/Shorts) lalu tekan **Baca gaya**, atau **Pilih file**. Pembacaan berjalan di latar belakang (jendelanya boleh ditutup) dalam dua lapis:

1. **Pengukuran piksel** (selalu, tanpa AI): posisi & ukuran subtitle, jumlah kata per tampilan, HURUF BESAR, warna teks & sorotan, judul (ada/tidak, warna kotak, lama tampil), progress bar, tata letak (termasuk latar hitam), **zoom hentak / zoom pelan**, dan video hitam-putih.
2. **AI lokal gratis** (bila sudah disiapkan): melihat beberapa frame, potongan area subtitle, dan lembar contoh font Nero, lalu menilai **jenis font**, **cara menyorot kata** (warna/kotak/karaoke), garis tepi, bayangan, kotak gelap, gaya judul, nuansa warna, plus memberi **nama** dan **ringkasan** gayanya.

Angka hasil pengukuran selalu didahulukan; AI mengisi yang tidak bisa diukur. Di hasilnya, setiap pengaturan diberi tanda **UKUR** (diukur dari piksel) atau **AI**. Semua hasil bacaan **otomatis tersimpan di Gaya saya**, jadi bisa dipilih lagi kapan saja; tekan **Pakai gaya ini** untuk langsung memakainya. Gaya dari link bisa **dibaca ulang** (mis. setelah AI lokal disiapkan) lewat tombol **ⓘ** → **Baca ulang**.

**Pilih AI-nya** di **Pengaturan → AI gratis untuk membaca & meniru gaya editing**:

| | Google Gemini (disarankan) | AI lokal (Ollama) |
|---|---|---|
| Kepintaran | Model besar Google (Gemini 3.8 Flash) | Model kecil 2–4B |
| Cara melihat | **Menonton videonya** (20 detik pertama), jadi animasi subtitle & zoom ikut terbaca | Beberapa gambar diam |
| Butuh | Internet + API key gratis | Ollama + model di laptop |
| Privasi | Potongan clip kecil tanpa suara dikirim ke Google; di paket gratis boleh dipakai Google untuk meningkatkan produknya | Video tidak keluar dari laptop |
| Batas | Kuota gratis harian (cukup untuk pemakaian biasa) | Tidak ada |

Bila Gemini gagal (kuota habis, tanpa internet), Nero otomatis memakai AI lokal, lalu pengukuran piksel saja.

**Menyiapkan Gemini (sekali saja, gratis):**

1. Di Pengaturan pilih **Google Gemini**, lalu klik **Buat API key gratis** (membuka Google AI Studio).
2. Masuk dengan akun Google, tekan **Create API key**, lalu salin kuncinya.
3. Tempel di kolom **API key Gemini**, tekan **Cek** (harus muncul "✓ API key berfungsi"), lalu **Simpan**.

Kunci hanya disimpan di komputer Anda (`data/settings.json`) dan tidak pernah dikirim ke halaman. Server lain yang meniru API OpenAI dan butuh kunci (mis. OpenRouter atau Groq) juga bisa dipakai lewat bagian AI lokal: isi **Alamat server** (mis. `https://openrouter.ai/api/v1`) dan **API key server**.

**Menyiapkan AI lokal (sekali saja, gratis):**

1. Unduh & pasang **Ollama** dari https://ollama.com/download, lalu buka aplikasinya (berjalan di latar belakang).
2. Di Nero buka **Pengaturan** (menu kiri bawah) → bagian **AI lokal gratis**, tekan **Cek lagi**, lalu **Unduh** salah satu model:
   - **qwen3-vl:4b-instruct** (± 3,3 GB): disarankan. Di RTX 3050 4 GB ± 35 detik per clip; nama & ringkasan gayanya paling rapi.
   - **qwen3-vl:2b-instruct** (± 1,9 GB): ± 3× lebih cepat (± 12 detik per clip) dengan hasil pengaturan yang hampir sama, tetapi nama & ringkasannya kurang bagus. Cocok untuk laptop tanpa GPU.
   - **qwen3-vl:8b-instruct** (± 6 GB): paling teliti, butuh GPU 8 GB atau lebih.

   Pakai versi berakhiran **-instruct**. Tag `qwen3-vl:4b` tanpa akhiran adalah versi *thinking* yang selalu "berpikir" ribuan kata dulu, sehingga satu clip bisa 5–10 menit; Nero menghentikannya dan memberi tahu bila model seperti itu terpilih.
3. Selesai. Tanda **AI lokal siap** muncul di kartu Gaya video. Pertama kali membaca bisa 1–3 menit selagi model dimuat; berikutnya lebih cepat.

Video tidak dikirim ke internet: AI berjalan di komputer Anda. Setelah membaca, model dilepas dari memori GPU dalam 1 menit supaya transkrip video tetap memakai GPU. Bila kedua model terpasang, **Otomatis** memakai yang 4B; pilih yang lain di **Model yang dipakai**. Server lain yang meniru API OpenAI (mis. LM Studio di `http://127.0.0.1:1234`) juga bisa dipakai: isi **Alamat server** di Pengaturan.

Batasannya: hanya untuk video vertikal 9:16, dan pengukuran paling akurat untuk subtitle ber-outline hitam (gaya TikTok umumnya). Musik, b-roll, stiker, dan emoji tidak ditiru. AI kecil kadang salah menebak font atau warna, jadi periksa pratinjaunya; semua pengaturan bisa diubah di **Atur detail**. Bila video aslinya banyak tulisan (slide, berita), hasil subtitle/judul bisa keliru.

Setelah semua clip selesai, tekan **🧹 Hapus video sumber** untuk menghemat disk (video asli bisa ratusan MB). Hasil render tetap aman, tetapi clip proyek itu tidak bisa dirender ulang.

### Kemampuan Clip Maker

- Deteksi momen menarik otomatis (hook kalimat pembuka, pertanyaan, kata kunci emosional, energi suara, kepadatan bicara)
- Subtitle otomatis gaya TikTok: 1–4 kata per tampilan, sorotan warna / kotak / karaoke, animasi muncul
- 11 gaya siap pakai, pustaka **Gaya saya**, dan tiru gaya dari clip referensi dengan AI lokal gratis
- Efek video: zoom hentak / zoom pelan, filter warna, latar blur atau hitam
- Crop pintar 9:16 mengikuti wajah pembicara dengan gerakan kamera halus
- Tata letak **Dua orang (atas/bawah)** untuk podcast/wawancara: tiap orang mendapat setengah layar (dipilih otomatis bila terdeteksi dua wajah berdampingan)
- Tata letak alternatif: video utuh dengan latar blur, atau potong tengah
- Transkrip di GPU NVIDIA bila tersedia (otomatis kembali ke CPU bila gagal); encode video juga memakai GPU (NVENC) bila bisa
- Render cepat: beberapa frame disusun bersamaan (clip 14 detik ±6 detik di laptop RTX 3050)
- Judul/hook di atas video, progress bar, watermark @akun
- Volume suara diratakan sesuai standar media sosial
- Proses banyak link sekaligus dengan antrian
- Caption + hashtag siap salin

## Mode AI (opsional, lebih pintar)

Ada dua jenis AI, keduanya opsional:

- **AI gratis untuk meniru gaya**: Google Gemini (online, API key gratis, paling pintar) atau AI lokal Ollama (offline). Di Clip Maker dipakai untuk membaca & meniru gaya editing dari clip referensi. Cara menyiapkannya ada di bagian *Tiru gaya dari clip referensi*.
- **Claude API key** (berbayar): dijelaskan di bawah.

Tanpa pengaturan apa pun, setiap fitur bekerja dengan algoritma lokal (gratis); di Clip Maker, momen dipilih otomatis di komputer Anda. Untuk hasil yang jauh lebih bagus (di Clip Maker: pemilihan momen, judul hook, dan caption), isi **Claude API key** di **Pengaturan** (menu kiri bawah). Kunci ini dipakai bersama oleh semua fitur. Kunci bisa dibuat di https://console.anthropic.com (berbayar per pemakaian; satu video biasanya hanya beberapa sen dolar). Kunci hanya disimpan di komputer Anda (`data/settings.json`).

## Channel Otomatis (dalam pengembangan)

Fitur kedua Nero: setiap hari membuat satu video pendek orisinal untuk channel Anda sendiri, mulai dari riset topik, skrip, pemeriksaan fakta, suara, visual, sampai render. Hasilnya berupa paket siap unggah, dan bisa diunggah ke YouTube sebagai private (lihat **Menghubungkan YouTube**). Rancangannya ada di `SPEC.md` dan `INTEGRATION_PLAN.md`. Fitur ini masih dibangun bertahap dan **nonaktif** sampai Anda menyalakannya.

### Halaman Channel Otomatis

Buka **Channel Otomatis** di menu kiri Nero.

1. **Uji coba.** Pilih genre bila belum, lalu pilih **Video lengkap** (riset sampai render, ±3–6 menit) atau **Rencana saja** (riset, naskah, cek fakta, judul; ±1–2 menit), lalu tekan **Jalankan uji coba**.
   - Progres tiap langkah tampil langsung. Langkah yang hasilnya masih cocok ditandai "dipakai ulang".
   - **Batal** menghentikan run. Bila sedang menunggu AI, pembatalan terjadi begitu jawaban berikutnya masuk.
   - Centang **Mulai dari riset lagi** untuk topik baru di hari yang sama.
   - Uji coba tidak pernah mengunggah atau mengirim apa pun.
2. **Kesiapan.** Menunjukkan key yang wajib (Gemini) dan yang opsional (Groq, Pexels/Pixabay, Cloudflare, folder musik). Tombol **Atur key & pengaturan** membuka pengaturan channel.
3. **Hasil uji coba.**
   - Video bisa ditonton dan diunduh.
   - **Paket unggah manual** berisi judul, deskripsi, tag, dan hashtag, masing-masing dengan tombol **Salin**.
   - Ada juga hasil pemeriksaan fakta (QC), naskah per segmen (jenis visual, sumber klip, cadangan yang dipakai), sumber riset, dan catatan proses.
   - Kotak **YouTube** menampilkan tombol **Unggah uji (private)** bila YouTube sudah terhubung, atau status video yang sudah diunggah.
   - Pilih tanggal lain di **Riwayat uji coba**.
   - Video yang lolos pemeriksaan disalin ke **Folder hasil** (`output\channel\<tanggal>\`).
4. **Analitik.** Performa video buatan Nero di YouTube, pelajaran untuk video berikutnya, dan laporan mingguan (lihat **Analitik & laporan mingguan**).

**Pengaturan channel** (tombol kanan atas): genre/niche & penonton, suara (Ardi/Gadis) dan kecepatannya, gaya subtitle dari pustaka Clip Maker, folder & volume musik, jenis visual yang boleh dipakai, key API, YouTube (hubungkan, mode tayang, jam tayang, playlist, kata kunci tren, upload otomatis), Telegram, Analitik (hari laporan mingguan, izin AI), jam run harian, dan sakelar **Aktifkan** (lihat **Jadwal harian**). Key Gemini diisi di **Pengaturan Nero** karena dipakai bersama Clip Maker.

Kunci API disimpan di `data/settings.json` dan diisi dari halaman Nero; nilainya tidak pernah ditampilkan lagi. Untuk baris perintah, setiap kunci juga bisa diisi lewat environment variable. Nilai dari environment variable menimpa yang tersimpan, dan halaman menandainya "dari environment variable":

| Kunci | Environment variable | Untuk |
|---|---|---|
| Gemini (sama dengan kunci Gemini di Pengaturan Nero) | `GEMINI_API_KEY` | AI penulis skrip (gratis) |
| Groq | `GROQ_API_KEY` | AI cadangan (gratis, console.groq.com) |
| Pexels, Pixabay | `PEXELS_API_KEY`, `PIXABAY_API_KEY` | video stok |
| Cloudflare Workers AI | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | gambar AI |
| YouTube | `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | upload & statistik. Refresh token diisi otomatis oleh **Hubungkan YouTube**; env var hanya untuk baris perintah/CI. |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | notifikasi ke ponsel |

Uji koneksi AI dari folder `Nero`:

```
.venv\Scripts\python -m features.channel llm-ping
```

Urutan AI cadangannya: Gemini (model di Pengaturan Nero) → Groq → Gemini ringan (`gemini-3.5-flash-lite`). Di paket gratis, model Gemini utama kadang penuh (tidak menjawab atau membalas 503); dalam kasus itu Nero otomatis memakai cadangan berikutnya.

Uji coba membuat rencana video (riset topik → skrip → pemeriksaan fakta & kualitas → judul/deskripsi/tag), tanpa membuat video:

```
.venv\Scripts\python -m features.channel genres
.venv\Scripts\python -m features.channel run-daily --dry-run --only plan --genre fakta-sains
```

Hasilnya ada di `data\channel\runs\<tanggal>\`: `research.json`, `script.json`, `qc.json`, `metadata.json`, dan `run.log`. Menjalankan ulang memakai hasil yang sudah ada. Untuk menulis ulang skrip (dan langkah sesudahnya), tambahkan `--force script`; untuk mencari topik lain, `--force research`.

Teks perintah untuk AI ada di `features\channel\prompts\`. Boleh diedit, misalnya untuk mengubah gaya penulisan. Berkas yang namanya diawali `_` dipakai bersama: `_guardrails.md` berisi aturan konten, `_description.md` templat deskripsi YouTube.

### Membuat video (uji coba)

```
.venv\Scripts\python -m features.channel run-daily --dry-run --only video --genre fakta-sains
```

`--only video` menjalankan rencana di atas lalu suara, visual, dan render. `--only media` hanya menjalankan suara sampai render, memakai rencana yang sudah ada. Hasil tambahan di folder run: `voice.mp3`, `words.json` (waktu tiap kata), `visuals.json` (sumber tiap visual, termasuk cadangan yang dipakai), `clips\`, `images\` (gambar AI), `final.mp4`, `captions.srt`, dan `render.json` (hasil pemeriksaan video). `final.mp4` juga disalin ke `output\channel\<tanggal>\`.

- **Suara:** edge-tts (gratis, tanpa key, butuh internet). Pilih suara Ardi (pria) atau Gadis (wanita) di pengaturan. Gadis berbicara lebih pelan, jadi naskahnya otomatis dibuat lebih pendek supaya video tetap 45–58 detik. Bila narasi kepanjangan, kecepatan suara dinaikkan sampai +15%; bila masih kepanjangan, AI diminta memendekkan naskah lalu naskah diperiksa ulang.
- **Visual:** video stok dari Pexels lalu Pixabay, gambar AI dari Cloudflare yang digerakkan pelan (efek Ken Burns), dan grafik buatan Nero (angka besar, daftar poin, perbandingan, kutipan, judul). Bila satu jenis gagal (key belum diisi, kuota habis, klip tidak ditemukan), Nero memakai cadangan: gambar AI → video stok → grafik → latar polos. Alasannya dicatat di `visuals.json`.
- **Key gratis:** Pexels di pexels.com/api, Pixabay di pixabay.com/api/docs (masuk dulu, key tampil di halaman itu), dan Cloudflare: daftar di dash.cloudflare.com → Workers AI → **Use REST API** → **Create a Workers AI API Token**. Salin token dan **Account ID** dari halaman yang sama. Kuota gratis gambar AI ±60 gambar/hari; Nero berhenti membuat gambar AI di 80% kuota (catatan pemakaian di `data\channel\usage\`).
- **Musik (opsional):** isi `video.music_dir` dengan folder musik bebas royalti milik Anda. Satu lagu dipilih per hari, dipelankan (bawaan -22 dB), diberi fade in/out.
- **Subtitle:** memakai gaya dari pustaka Clip Maker (`video.caption_style`, bawaan `preset:klasik`, atau id dari "Gaya saya").
- Video yang tidak lolos pemeriksaan (bukan 1080×1920, bukan 30 fps, tanpa audio, atau durasi di luar 45–58 detik ±3) membuat run gagal dan tidak akan di-upload.
- Video yang sah tetapi visualnya kurang bervariasi (misalnya semua grafik karena key stok/AI belum diisi) ditandai **perlu dicek** di `render.json` (`needs_review`). Video seperti ini tidak akan diunggah otomatis.
- Menjalankan ulang memakai hasil yang masih cocok. Bila skrip, suara, atau pengaturan berubah (misalnya suara diganti, topik terlarang ditambah), langkah yang terdampak dibuat ulang otomatis. Video lama di folder hasil ditarik begitu langkah sebelumnya dijalankan ulang. Untuk membuat ulang satu langkah: `--force tts`, `--force visuals` (misalnya setelah mengisi key Pexels/Cloudflare), atau `--force render` (misalnya setelah mengganti gaya subtitle).
- QC dengan model Gemini ringan masih bisa meloloskan nuansa yang keliru, misalnya kalimat usang di Wikipedia ("sekarang berada di…"). Tonton dan baca naskahnya sebelum mengunggah; key Groq membuat pemeriksaan lebih teliti.

### Menghubungkan YouTube

Nero bisa mengunggah video ke channel YouTube Anda lewat YouTube Data API (gratis). Upload dari run harian bawaannya **mati** (`youtube.api_upload = false`): Nero hanya menyiapkan video dan paket unggah, lalu Anda yang mengunggah. Tombol **Unggah uji (private)** di hasil run selalu bisa dipakai untuk mencoba jalurnya.

**1. Siapkan Google Cloud (sekali saja, ±15 menit)**

1. Buka [console.cloud.google.com](https://console.cloud.google.com), masuk dengan akun Google pemilik channel, lalu buat project baru (misalnya "Nero Channel").
2. **APIs & Services → Library**: cari **YouTube Data API v3**, tekan **Enable**. Cari juga **YouTube Analytics API** dan tekan **Enable**. API ini terpisah, dan dipakai untuk statistik video di kartu Analitik.
3. Buka **Google Auth Platform → Overview → Get started**:
   - App name bebas, tetapi **jangan memakai kata "YouTube"**. Isi email dukungan.
   - Audience: **External**. Isi email kontak, setujui kebijakannya, lalu **Create**.
4. **Data Access → Add or remove scopes**: centang `youtube.upload`, `youtube.readonly`, dan `yt-analytics.readonly`. Bila memakai playlist, tambahkan `youtube.force-ssl`. Tekan **Update**, lalu **Save**. Kalau scope tidak muncul, pastikan API di langkah 2 sudah aktif.
5. **Audience → Publish app**, lalu konfirmasi sampai statusnya **In production**.
   - Jangan biarkan di **Testing**: izin dari project Testing kedaluwarsa tiap 7 hari, jadi Anda harus menghubungkan ulang setiap minggu.
   - Aplikasi tidak perlu diverifikasi Google karena hanya Anda yang memakainya. Akibatnya, saat menghubungkan muncul peringatan "Google belum memverifikasi aplikasi ini". Klik **Lanjutan / Advanced**, lalu lanjutkan ke aplikasi Anda.
6. **Clients → Create client**, Application type **Desktop app**, nama bebas (misalnya "Nero"), lalu **Create**.
   - **Salin Client ID dan Client secret saat itu juga** (atau unduh JSON-nya). Secret hanya ditampilkan sekali. Bila hilang, tambahkan secret baru di halaman client yang sama.
   - Client yang tidak dipakai 6 bulan dihapus otomatis oleh Google; upload rutin menjaganya tetap aktif.

**2. Hubungkan di Nero**

1. **Pengaturan channel → YouTube**: tempel Client ID dan Client secret, lalu **Simpan**.
2. Buka Pengaturan lagi, tekan **Hubungkan YouTube**. Halaman Google terbuka di tab baru. Pilih akun dan channel tujuan (termasuk Brand Account bila channel Anda memakainya), lalu izinkan semua akses.
3. Setelah halaman Google menampilkan "YouTube terhubung", kembali ke Nero. Pengaturan menampilkan **✓ Terhubung ke &lt;nama channel&gt;**.

Cara kerjanya: Nero membuka "penerima" sementara di `http://127.0.0.1:<port acak>` di komputer Anda sendiri (bukan internet) untuk menerima jawaban Google. Penerima ini berhenti sendiri setelah selesai atau setelah 10 menit. Token izin disimpan di `data/settings.json` dan tidak pernah ditampilkan. **Putuskan** mencabut izin di akun Google dan menghapus tokennya. Bisa juga lewat baris perintah: `python -m features.channel youtube-auth`.

**3. Unggah uji**

Di **Hasil uji coba**, pilih run yang videonya lolos pemeriksaan, lalu tekan **Unggah uji (private)**.
- Video diunggah sebagai **private**: hanya Anda yang bisa melihatnya. Video baru tayang bila Anda sendiri yang mengubahnya di YouTube Studio.
- Sebelum mengunggah, Anda diminta menyatakan video mematuhi [Persyaratan Layanan YouTube](https://www.youtube.com/t/terms).
- Setelah terunggah, kotak YouTube menampilkan status ("Masih diproses" → "Siap di YouTube"), tautan YouTube Studio, dan tombol **Cek status**.
- Setiap run hanya diunggah **satu kali**. Menekan tombol lagi, menjalankan ulang run, atau `--force` tidak mengunggah ulang.
- Upload yang terputus (internet mati, Nero ditutup) dilanjutkan dari bagian yang sudah terkirim saat dicoba lagi. Bila catatan sesinya hilang, Nero memeriksa dulu apakah video berjudul sama sudah ada di channel.
- Video yang gagal QC, gagal validasi render, dibuat dari naskah sebelumnya, atau belum dibuat ulang setelah suara/visual berubah tidak bisa diunggah. Kotak YouTube menuliskan alasannya, dan bila bisa diperbaiki ada tombol **Buat ulang video** untuk tanggal itu (hasil yang masih cocok dipakai ulang).
- Bila Anda menayangkan video uji di YouTube Studio, **Cek status** tidak menganggapnya masalah.
- Bila membuat ulang berarti riset dan naskah baru (misalnya genre atau penonton diganti), Nero meminta konfirmasi dulu.
- Upload yang terputus terikat ke channel tempat ia dimulai. Bila Anda menghubungkan channel lain, Nero meminta channel lama dihubungkan lagi dulu, supaya video yang sama tidak terunggah ke dua channel.
- Video yang diunggah masuk riwayat (`data\channel\history.json`), supaya topiknya tidak diulang 60 hari.

**4. Upload otomatis (nanti)**

Menyalakan **Upload otomatis lewat API** membuat run harian mengunggah sendiri, tetapi hanya bila Channel Otomatis **aktif** (lihat **Jadwal harian**). Mode tayangnya:

| Mode | Hasil di YouTube |
|---|---|
| Manual (bawaan) | Private tanpa jadwal; Anda yang menayangkan. |
| Jendela tinjau | Private dengan jadwal tayang di **jam tayang** (bawaan 18.00 WIB; dikirim ke YouTube dalam UTC). Bila jam itu sudah lewat atau tinggal <15 menit, dijadwalkan besok. Batalkan di Studio sebelum jam tayang bila tidak cocok. |
| Otomatis | Langsung publik (tidak disarankan). |

Video yang ditandai **perlu dicek** tidak pernah diunggah otomatis. Setelah upload, Nero memeriksa status video (`videos.list`). Bila privasi atau jadwalnya tidak sesuai (misalnya YouTube mengunci video sebagai private), hasilnya ditandai **Ada masalah**.

Lain-lain:
- **Playlist (opsional):** isi ID playlist (diawali `PL`), simpan, lalu **Hubungkan ulang**. Izin playlist (`youtube.force-ssl`) hanya diminta bila ID playlist diisi.
- **Kata kunci tren (opsional, maks. 3):** riset mencari judul Shorts yang banyak ditonton 7 hari terakhir. Judul itu hanya dipakai sebagai petunjuk minat penonton, bukan sumber fakta, dan tidak disimpan di hasil riset. Cache-nya dihapus setelah 2 hari.
- **Kuota gratis per hari:** 100 upload, 100 pencarian (Nero memakai maks. 3), dan 10.000 unit untuk panggilan lain. Kuota pulih pukul 00.00 waktu Pasifik (14.00–15.00 WIB). Pemakaian tampil di Pengaturan → YouTube.
- **Video AI:** bila pengaturan `contains_synthetic_media` aktif (bawaan, wajib selama gambar AI dipakai), video ditandai "konten sintetis" di YouTube.

**Audit YouTube API.** Sampai Oktober 2026, video yang diunggah lewat API dari project yang belum diaudit dikunci private oleh YouTube. Sejak **2 November 2026** aturan itu dicabut (YouTube Data API revision history), dan kuota bawaan 100 upload per hari jauh di atas kebutuhan 1 video per hari. Jadi audit **tidak diperlukan** untuk Nero. Unggah uji tetap berguna untuk memastikan: buka videonya di YouTube Studio dan cek tidak ada label terkunci.

Bila suatu saat Anda tetap ingin mengajukan audit (misalnya untuk kuota tambahan), formulirnya ada di support.google.com/youtube/contact/yt_api_form. Syaratnya:
- situs publik HTTPS yang menjelaskan alat ini dan memuat kebijakan privasi (alamat 127.0.0.1 tidak memenuhi syarat);
- rekaman layar alur hubungkan → unggah → verifikasi;
- nomor project Google Cloud.

Draf isian (bahasa Inggris, sesuaikan):
- *Describe your work related to YouTube:* "Personal tool for my own YouTube channel. It researches a topic from public sources (Wikipedia, RSS), writes and fact-checks an original script, renders a short vertical video with my own voice-over/graphics and licensed stock footage, then uploads it to my channel as private for my review."
- *API client:* "Nero, a desktop application that runs only on my own computer; single user (the channel owner); no public users."
- *How the client uses API data:* "videos.insert to upload my own videos (private or scheduled), videos.list to verify processing and privacy status and to read my own videos' statistics, YouTube Analytics reports.query for my own channel's statistics, playlistItems.insert/list for my own uploads and playlist, search.list (max 3 calls/day) to see which topics are popular. No API data is shown to other people or sold. My channel statistics are stored only on my computer, refreshed daily, and deleted when I disconnect; other API data is not stored longer than 30 days."
- *Quota justification:* "One upload per day; the default quota is sufficient."

### TikTok, Instagram, dan Telegram

**Paket unggah manual.** TikTok dan Instagram selalu Anda unggah sendiri; Nero tidak pernah mengunggah ke sana. Untuk setiap run, Nero menyiapkan:
- **Caption TikTok**: judul + ajakan singkat (≤150 karakter) dan 3–5 hashtag.
- **Caption Instagram Reels**: judul, ringkasan, ajakan, dan maksimal 5 hashtag. Sejak akhir 2025 Instagram hanya menerima 5 hashtag per post.
- **Pengingat label AI**:
  - TikTok: nyalakan "AI-generated content" bila video memakai gambar AI yang realistis. Narasi TTS generik tidak perlu label.
  - Instagram: nyalakan "Add AI label" karena narasinya memakai suara AI. Contoh resmi Meta persis kasus ini.

Caption tampil di hasil run (tombol **Salin**) dan tersimpan sebagai `tiktok_caption.txt` dan `instagram_caption.txt` di Folder hasil. Teksnya bisa diubah di `features\channel\prompts\_caption_*.md`. Unggah selalu berkas MP4 dari Nero, bukan video yang diunduh dari platform lain: video ber-watermark platform lain tidak direkomendasikan TikTok dan Instagram.

**Telegram (opsional).** Paket harian dan peringatan bila run gagal bisa dikirim ke ponsel Anda.
1. Di Telegram, buka **@BotFather**, kirim `/newbot`, isi nama, lalu username yang berakhiran `bot`. Salin token yang diberikan; token ini rahasia.
2. Buka bot baru Anda dan tekan **Start**. Bot tidak bisa mengirim pesan sebelum Anda memulai chat.
3. Di Nero: **Pengaturan channel → Telegram**, tempel token, lalu **Simpan**. Buka Pengaturan lagi, tekan **Cari chat ID**, pilih chat Anda (**Pakai**), lalu **Simpan**.
4. Tekan **Kirim pesan uji**. Untuk mencoba paket lengkap, buka hasil run lalu tekan **Kirim ke Telegram**.

Isi paket harian, berurutan:
- ringkasan: topik, judul, skor QC, durasi, status YouTube + tautan Studio, dan pengingat label AI;
- video (maks. 50 MB, batas bot Telegram);
- caption TikTok;
- caption Instagram;
- naskah lengkap.

Caption dan naskah dikirim sebagai blok teks yang mudah disalin dari ponsel. Bila run harian gagal, Nero mengirim nama langkah yang gagal dan 30 baris log terakhir.

Kiriman otomatis hanya berjalan bila Channel Otomatis **aktif** dan "Kirim paket harian & peringatan gagal ke Telegram" dicentang. Uji coba (`--dry-run`) hanya menuliskan isi ringkasannya di log. Kiriman yang terputus dilanjutkan dari bagian yang belum terkirim, dan paket yang sama tidak dikirim dua kali. Baris perintahnya: `telegram-chats`, `telegram-test`, dan `telegram-send --date YYYY-MM-DD`.

### Analitik & laporan mingguan

Kartu **4. Analitik** di halaman Channel menampilkan performa video buatan Nero di channel yang terhubung, 28 hari terakhir. Datanya dibaca sekali sehari oleh run harian (langkah pertama), atau kapan saja lewat **Perbarui analisis**. Syaratnya: YouTube terhubung, dan **YouTube Analytics API** aktif di project Google Cloud (lihat **Menghubungkan YouTube**, langkah 2).

**Video yang dianalisis** hanya video buatan Nero:
- yang diunggah lewat Nero;
- yang Anda unggah sendiri, asalkan **judulnya sama persis** dengan judul buatan Nero untuk run itu. Tanda `<` dan `>` diabaikan.

Video yang diunggah sendiri juga dicatat ke riwayat, supaya topiknya tidak diulang dan formatnya bergiliran. Bila dua run atau dua video berjudul sama, Nero tidak memasangkannya supaya tidak salah.

**Angka yang dipakai:**
- **Engaged 7 hari:** engaged views selama 7 hari pertama sejak video tayang, menurut YouTube Analytics. Dengan rentang yang sama, video lama dan baru bisa dibandingkan. Engaged views tidak menghitung putar ulang. Angka inilah yang dipakai untuk peringkat teratas/terbawah.
- **Ditonton:** rata-rata detik (dan persen durasi) yang ditonton dalam 7 hari pertama.
- **Views sekarang:** angka publik YouTube saat ini, termasuk putar ulang dan autoplay.
- Data YouTube Analytics tertunda 48–72 jam dan memakai tanggal waktu Pasifik. Karena itu video baru tampil "belum ada data" atau "sementara" dulu, dan baru "7 hari lengkap" sekitar 10 hari setelah tayang.

**Pelajaran (`data\channel\learnings.md`)** dipakai saat riset topik dan menulis naskah berikutnya. Isinya maksimal 15 poin: yang berhasil, yang kurang berhasil, dan saran. Ada dua syarat:
- Minimal **5 video** yang data 7 hari pertamanya lengkap. Sebelum itu, berkasnya berisi "Data belum cukup" dan Nero tidak menebak pola.
- **Izin Anda** untuk mengirim statistik ke AI (**Pengaturan channel → Analitik**, bawaan mati).
  - Yang dikirim: nomor urut, judul/topik/format/hook buatan Nero, dan angka YouTube 7 hari pertama. ID video dan channel tidak dikirim.
  - Paket gratis Gemini memakai isi prompt untuk mengembangkan produk Google, dan isinya bisa dibaca peninjau manusia. Pertimbangkan itu sebelum menyalakan izin.
  - Setiap pola harus didukung minimal 2 video; poin yang tidak memenuhi syarat dibuang.

**Laporan mingguan** dikirim ke Telegram sekali seminggu pada hari pilihan Anda (bawaan Senin). Isinya:
- tren seluruh channel: 7 hari terakhir dibanding minggu sebelumnya;
- 3 video teratas dan 3 terbawah menurut engaged views 7 hari pertama;
- 3 poin pelajaran.

Laporan dikirim oleh run harian sungguhan saat Channel Otomatis aktif. Bila hari itu terlewat (laptop mati), laporan dikirim di run berikutnya minggu itu. **Kirim laporan** di kartu Analitik mengirimnya kapan saja.

**Privasi & data YouTube.**
- Nero memakai YouTube API Services. Dengan memakainya, Anda terikat pada [Persyaratan Layanan YouTube](https://www.youtube.com/t/terms) dan [Kebijakan Privasi Google](https://policies.google.com/privacy).
- Statistik channel Anda disimpan hanya di komputer ini (`data\channel\performance.json`), untuk video di jendela analisis, dan diperbarui tiap analisis. Judul dan teks lain dari YouTube tidak disimpan; judul yang tampil adalah catatan Nero.
- Statistik dikirim ke luar hanya ke:
  - Telegram milik Anda (laporan mingguan);
  - penyedia AI, bila Anda mengizinkannya.
- **Hapus data performa** di kartu Analitik (atau `analytics-clear`) menghapus angka dan pelajaran. Video dan data di YouTube tidak berubah. Catatan tanggal laporan mingguan tetap disimpan supaya laporan yang sama tidak terkirim dua kali.
- **Putuskan** YouTube juga langsung menghapus data itu. Data yang izinnya tidak lagi berlaku (dicabut, atau tidak terbukti berlaku selama 30 hari) dihapus otomatis.
- Izin Nero bisa dicabut kapan saja di [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

Baris perintah:

```
.venv\Scripts\python -m features.channel analyst
.venv\Scripts\python -m features.channel weekly-report
.venv\Scripts\python -m features.channel weekly-report --send
.venv\Scripts\python -m features.channel analytics-clear
```

`analyst` memperbarui analisis dan menampilkan `learnings.md`. `weekly-report` menampilkan isi laporan; tambahkan `--send` untuk mengirimnya ke Telegram.

### Jadwal harian

Saat **Channel Otomatis aktif** (Pengaturan channel → Otomatis → **Aktifkan**), Nero menjalankan *run harian sungguhan* setiap hari pada **jam run harian** (bawaan 05.17 WIB). Run itu:
- membuat video;
- mengunggahnya ke YouTube bila **Upload otomatis** menyala;
- menyiapkan caption TikTok/Instagram;
- mengirim paket ke Telegram;
- mengirim laporan mingguan pada harinya.

Jadwal berjalan **di dalam Nero** di laptop ini (keputusan no. 4 di `INTEGRATION_PLAN.md`), jadi:
- **Laptop dan Nero harus menyala.** Nyalakan **Buka otomatis saat Windows menyala** di Pengaturan Nero. Nero lalu berjalan di latar setiap kali Anda login. Kartu **2. Kesiapan** memberi peringatan bila pilihan ini mati, termasuk bila Nero dimatikan di Windows (Task Manager → Startup apps). Menyalakannya lagi dari Pengaturan Nero juga mengaktifkannya kembali di Windows.
- **Run dikejar.** Bila laptop mati atau tidur pada jam run, run hari itu dimulai begitu Nero menyala atau laptop bangun, kapan pun di hari yang sama. Nero menunggu ±2 menit dulu supaya internet tersambung. Hari yang sudah lewat tidak dikejar.
- **Laptop tidak tidur di tengah run.** Selama run berjalan, Nero meminta Windows tidak tidur otomatis; layar tetap boleh mati. Ini tidak mencegah tidur karena laptop ditutup atau tombol power. Bila itu terjadi, run dilanjutkan dari langkah terakhir yang selesai di percobaan berikutnya.
- **Gagal = dicoba ulang.** Run yang gagal (mis. AI gratis sedang penuh) dicoba lagi 1 jam lalu 2 jam kemudian, maksimal 3 kali per hari. Setiap kegagalan dikirim ke Telegram.
  - Pengaturan yang belum lengkap (mis. genre belum dipilih) tidak dicoba ulang; diberitahukan sekali.
  - Run yang Anda batalkan sendiri tidak dicoba ulang otomatis.
- **Satu run per hari.** Uji coba yang sedang berjalan ditunggu dulu.
  - Video yang sudah diunggah tidak pernah diunggah lagi.
  - Bila jam tayang mode *Jendela tinjau* sudah dipakai video lain (mis. run yang terlambat digeser ke besok), video berikutnya diunggah private tanpa jadwal supaya tidak tayang bersamaan.

Kartu **2. Kesiapan** menampilkan keadaan run hari ini, run berikutnya, dan tombol **Jalankan run harian sekarang** (run sungguhan tanpa menunggu jam run). Saat Anda menyalakan **Aktifkan** setelah jam run hari ini lewat, run hari ini langsung dikejar. Status jadwal juga bisa dilihat dari baris perintah:

```
.venv\Scripts\python -m features.channel schedule-status
```

Bila ingin laptop bangun sendiri untuk run harian: Windows menyediakan *wake timer*, tetapi di laptop ini wake timer mati saat memakai baterai. Nero tidak mengubah pengaturan daya Windows.

## Tips & masalah umum

| Masalah | Solusi |
|---|---|
| Unduhan YouTube gagal / "confirm you're not a bot" | Di **Pengaturan** (menu kiri bawah), isi "Ambil cookies dari browser" dengan browser tempat Anda login YouTube (chrome/edge/firefox). Tutup browser itu saat mengunduh. |
| Unduhan tiba-tiba gagal padahal dulu bisa | Jalankan **update.bat** (situs video sering berubah). |
| Ikon Nero diklik tetapi halaman tidak terbuka | Jalankan **run.bat** untuk melihat pesannya, atau buka `data\nero.log`. |
| Transkrip lambat | Lihat baris di bawah tombol Proses: bila tertulis "memakai CPU" padahal ada kartu NVIDIA, jalankan **install.bat** lagi. Atau di **Pengaturan clip** ganti model transkrip ke **base**. Dengan GPU, pakai **large-v3-turbo**: hampir seakurat large-v3 tetapi jauh lebih cepat. |
| Subtitle kurang tepat / malah diterjemahkan | Pastikan pilihan **Bahasa** saat menambahkan video sesuai bahasa yang diucapkan (atau pilih Deteksi otomatis), dan pakai model **medium**. |
| Wajah tidak diikuti | Pilih tata letak **Ikuti wajah**, **Dua orang (atas/bawah)** untuk podcast, atau **Video utuh + latar blur** untuk video dengan banyak orang. |
| "AI lokal belum berjalan" saat tiru gaya | Buka aplikasi Ollama (ikonnya ada di Start Menu), lalu di **Pengaturan** tekan **Cek lagi**. Gaya tetap bisa dibaca tanpa AI, hanya font & cara menyorot kata yang tidak ditiru. |
| "Batas pemakaian gratis Gemini tercapai" | Kuota gratis harian/menit habis. Nero otomatis memakai AI lokal; kuota harian kembali tengah malam waktu Pasifik (± pukul 14.00–15.00 WIB). |
| "API key Gemini tidak valid / ditolak" | Buat kunci baru di Google AI Studio, tempel di Pengaturan, tekan **Cek**. |
| AI lokal lambat / "memori tidak cukup" | Pilih model yang lebih kecil (**qwen3-vl:2b-instruct**) di **Pengaturan → AI lokal gratis**. |
| "Izin YouTube sudah tidak berlaku" | Tekan **Hubungkan ulang** di Pengaturan channel → YouTube. Bila terjadi tiap minggu, status aplikasi di Google Cloud masih **Testing**: buka Google Auth Platform → Audience → **Publish app**. |
| "redirect_uri_mismatch" / "invalid_client" saat menghubungkan YouTube | Pastikan jenis client OAuth-nya **Desktop app** (bukan Web application), lalu salin ulang Client ID & secret. |
| Telegram: "Bot belum boleh mengirim ke chat ini" / "chat not found" | Buka bot Anda di Telegram dan tekan **Start**, lalu **Cari chat ID** lagi dan simpan. |
| Analitik: "YouTube Analytics API belum diaktifkan" | Di Google Cloud: **APIs & Services → Library → YouTube Analytics API → Enable** (project yang sama dengan client OAuth), lalu **Perbarui analisis**. |
| Analitik: video yang saya unggah sendiri tidak muncul | Judul di YouTube harus sama persis dengan judul buatan Nero untuk run itu. Ubah judulnya di YouTube Studio, atau pakai **Unggah uji** dari Nero. |
| Analitik: "Izin melihat statistik belum diberikan" | **Hubungkan ulang** YouTube dan centang semua izin, termasuk statistik. |
| Run harian tidak berjalan | Pastikan Channel Otomatis **aktif**, Nero terbuka (nyalakan **Buka otomatis saat Windows menyala** di Pengaturan Nero), dan laptop menyala setelah jam run. Lihat keadaan run hari ini di kartu **2. Kesiapan** atau `schedule-status`. |
| Run harian "gagal" berulang | Buka hasil run tanggal itu untuk alasan dan log. Bila AI gratis sedang penuh, isi key Groq sebagai cadangan. Setelah 3 percobaan, tekan **Jalankan run harian sekarang** untuk mencoba lagi. |
| Telegram: "Cari chat ID" kosong | Kirim pesan apa saja ke bot (Telegram hanya menyimpannya 24 jam), lalu tekan lagi. |
| "Model selalu berpikir panjang" | Model yang terpilih adalah versi *thinking*. Unduh versi **-instruct** lalu pilih di Pengaturan. |

Gunakan aplikasi ini untuk video milik Anda sendiri atau yang Anda punya izin untuk mengunggah ulang.

## Struktur folder

```
Nero/
  install.bat, run.bat, update.bat
  requirements*.txt daftar paket (lock = versi yang sudah teruji, gpu = pustaka CUDA)
  app.py            server web lokal: keamanan, tampilan, pengaturan umum, memuat semua fitur
  common.py         bagian bersama semua fitur (lokasi folder, pengaturan, hapus berkas)
  local_ai.py       AI lokal gratis (Ollama / LM Studio / server bergaya OpenAI), dipakai bersama semua fitur
  gemini_ai.py      Google Gemini (paket gratis, bisa menonton video), dipakai bersama semua fitur
  desktop.py        ikon Nero di Desktop & Start Menu, buka otomatis, berjalan tanpa jendela hitam
  features/         satu folder per fitur
    clip/           Clip Maker: api.py (rute, antrian, pustaka gaya), engine.py (unduh, transkrip, momen,
                    render, ukur gaya), style_ai.py (baca gaya dengan AI lokal), presets.py (gaya siap pakai)
    channel/        Channel Otomatis (dalam pengembangan): config.py (pengaturan & kunci), llm.py (AI + cadangan),
                    research.py, writer.py (skrip, QC, SEO), voice.py (suara), visuals.py + stock.py + ai_images.py
                    + graphics.py (visual), render.py, pipeline.py, schemas.py, state.py, runctx.py,
                    prompts/ (teks perintah AI, boleh diedit)
  web/              tampilan aplikasi siap pakai (hasil build frontend/, jangan diubah langsung)
  frontend/         kode sumber tampilan: Next.js + TypeScript + Tailwind CSS + shadcn/ui
  models/           model deteksi wajah
  fonts/            font subtitle gratis (lisensi SIL OFL, berkas OFL-*.txt)
  tests/            tes otomatis
  output/           HASIL ANDA, satu subfolder per fitur (output/clip = video clip)
  data/             pengaturan (settings.json), catatan nero.log, dan data setiap fitur (data/clip = proyek & transkrip)
```

## Mengubah tampilan (khusus pengembang)

Pemakai biasa tidak perlu Node.js: tampilan yang sudah jadi ada di folder `web/`. Node.js 20.9 atau lebih baru hanya dibutuhkan bila ingin mengubah tampilan.

1. Sekali saja: buka terminal di folder `frontend` lalu jalankan `npm install`.
2. Jalankan backend dengan izin untuk server pengembangan, lalu server tampilan:
   ```
   set NERO_DEV_ORIGINS=localhost:3000
   run.bat
   ```
   Di terminal lain, dari folder `frontend`: `npm run dev`, lalu buka http://localhost:3000. Perubahan kode langsung terlihat; permintaan `/api` dan `/out` diteruskan ke app.py di port 7860.
3. Setelah selesai: `npm run build` di folder `frontend`. Hasilnya otomatis disalin ke `web/`, lalu `run.bat` memakai tampilan baru.

Untuk mencoba tanpa menyentuh data Anda, jalankan Nero kedua dengan folder data terpisah: variabel `NERO_DATA` dan `NERO_OUTPUT` (mis. `data-uji\data` dan `data-uji\output`, folder `data-uji/` tidak ikut git) dan `PORT=7861`. Konfigurasi `nero-uji` & `nero-ui-uji` di `.claude/launch.json` sudah mengaturnya.

### Menambah fitur baru

Setiap fitur berdiri sendiri, jadi fitur baru tidak mengganggu yang lama:

1. **Backend**: buat folder `features/<id>/` (contoh: `features/clip/`). Isinya menyediakan `ID`, `NAME`, `SETTINGS`, `bp` (Flask Blueprint), `start()`, dan `summary()`; rinciannya ada di `features/__init__.py`. Tambahkan fiturnya ke daftar `FEATURES` di berkas itu. Rute otomatis berada di bawah `/api/<id>`, data di `data/<id>/` (`common.data_dir`), hasil di `output/<id>/` (`common.output_dir`), dan pengaturan khusus di bagian `"<id>"` pada `data/settings.json`. API key Claude dibaca lewat `common.feature_settings("<id>")["api_key"]`.
2. **Tampilan**: taruh kodenya di `frontend/src/features/<id>/`, buat halaman `frontend/src/app/<id>/page.tsx`, lalu tambahkan satu entri di `frontend/src/features/index.ts`. Menu samping dan kartu di Beranda langsung memuatnya (angka di kartu diambil dari `summary()`).

### Tes otomatis

Dari folder `Nero`:

```
.venv\Scripts\python -m unittest discover -s tests -t .
```

Tes memakai transkrip, video, dan folder buatan (tidak menyentuh isi folder `data/` atau `output/` Anda), termasuk memindahkan data ClipMaker versi lama, me-render beberapa clip bergaya diketahui lalu membacanya kembali dengan "Tiru gaya", dan Ollama palsu untuk menguji jalur AI lokal tanpa internet. Butuh sekitar 1,5 menit.
