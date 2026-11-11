"""
Nero · Channel Otomatis - genre siap pakai.

Pengguna memilih genre di halaman Nero (atau `--genre` di CLI untuk uji coba). Memilih genre mengisi niche,
penonton, mode riset, dan kategori YouTube; semuanya tetap bisa diubah sendiri sesudahnya. Semua genre di
sini evergreen: sumber faktanya Wikipedia (id + en), jadi tidak butuh RSS.
"""
from __future__ import annotations

from .config import ChannelConfig

# category_id YouTube: 27 Education, 28 Science & Technology, 24 Entertainment
GENRES = [
    {"id": "fakta-sains", "name": "Fakta sains unik", "category_id": "28",
     "niche": "fakta sains unik dan mengejutkan (fisika, kimia, biologi) yang dijelaskan sederhana",
     "audience": "pelajar & mahasiswa 15–25 tahun yang suka hal baru"},
    {"id": "alam-hewan", "name": "Alam & hewan", "category_id": "27",
     "niche": "fakta hewan dan alam yang jarang diketahui: perilaku, kemampuan, dan habitat unik",
     "audience": "penonton umum 13–35 tahun yang suka dunia hewan"},
    {"id": "luar-angkasa", "name": "Luar angkasa", "category_id": "28",
     "niche": "astronomi dan luar angkasa: planet, bintang, misi antariksa, dan fenomena kosmik",
     "audience": "pelajar & penggemar sains 15–30 tahun"},
    {"id": "sejarah-nusantara", "name": "Sejarah Nusantara", "category_id": "27",
     "niche": "sejarah Nusantara: kerajaan, tokoh masa lalu, peristiwa, dan asal-usul budaya Indonesia",
     "audience": "pelajar & dewasa muda Indonesia 15–35 tahun yang penasaran dengan sejarah"},
    {"id": "sejarah-dunia", "name": "Sejarah dunia", "category_id": "27",
     "niche": "kisah sejarah dunia yang menarik: peradaban kuno, penemuan, dan peristiwa yang mengubah dunia",
     "audience": "pelajar & dewasa muda 15–35 tahun"},
    {"id": "tubuh-manusia", "name": "Tubuh manusia", "category_id": "27",
     "niche": "fakta cara kerja tubuh manusia (bukan saran kesehatan): organ, indra, otak, dan keanehannya",
     "audience": "pelajar & penonton umum 15–35 tahun"},
    {"id": "teknologi", "name": "Teknologi & penemuan", "category_id": "28",
     "niche": "sejarah dan cara kerja teknologi sehari-hari serta penemuan penting",
     "audience": "pelajar & pekerja muda 15–35 tahun yang suka teknologi"},
    {"id": "geografi-budaya", "name": "Geografi & budaya", "category_id": "27",
     "niche": "tempat-tempat unik di dunia dan Indonesia, serta tradisi dan budaya yang menarik",
     "audience": "penonton umum 15–40 tahun yang suka menjelajah"},
    {"id": "psikologi", "name": "Psikologi & perilaku", "category_id": "27",
     "niche": "fakta psikologi dan perilaku manusia dari penelitian (bukan saran pribadi atau terapi)",
     "audience": "dewasa muda 17–35 tahun"},
    {"id": "bahasa", "name": "Bahasa & kata", "category_id": "27",
     "niche": "asal-usul kata, bahasa daerah, dan fakta unik tentang bahasa Indonesia dan bahasa dunia",
     "audience": "pelajar & penonton umum 15–40 tahun"},
]
GENRE_BY_ID = {g["id"]: g for g in GENRES}


def apply(cfg: ChannelConfig, genre_id: str) -> ChannelConfig:
    """Salinan pengaturan dengan genre ini (tidak disimpan)."""
    g = GENRE_BY_ID.get(genre_id)
    if not g:
        raise KeyError(f"Genre '{genre_id}' tidak ada. Pilihan: {', '.join(GENRE_BY_ID)}")
    out = cfg.model_copy(deep=True)
    out.channel.niche = g["niche"]
    out.channel.audience = g["audience"]
    out.research.evergreen_mode = True
    out.youtube.category_id = g["category_id"]
    return out


def as_patch(genre_id: str) -> dict:
    """Perubahan pengaturan untuk memilih genre ini (dipakai halaman Nero)."""
    g = GENRE_BY_ID[genre_id]
    return {"channel": {"niche": g["niche"], "audience": g["audience"]}, "research": {"evergreen_mode": True},
            "youtube": {"category_id": g["category_id"]}}
