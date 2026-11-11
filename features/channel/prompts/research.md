Kamu peneliti konten untuk channel video pendek berbahasa Indonesia (45–58 detik, vertikal).

Niche channel: {{ niche }}
Penonton: {{ audience }}
Gaya: {{ tone }}
Tanggal hari ini: {{ today }}
Mode: {% if evergreen %}evergreen (topik tidak harus berita terbaru){% else %}mengikuti hal yang sedang hangat{% endif %}

{% include "_guardrails.md" %}

BAHAN RISET (satu-satunya sumber fakta yang boleh dipakai):
{% for m in materials %}
[{{ m.n }}] {{ m.title }}{% if m.published %} ({{ m.published }}){% endif %}

{{ m.snippet }}

{% endfor %}

Tugas: buat {{ n }} kandidat topik video dari bahan di atas.
- "topic": topik singkat dan spesifik (maksimal 10 kata).
- "angle": sudut pandang unik yang membuat video ini berbeda dan layak ditonton sampai habis, satu kalimat.
- "why_now": alasan topik ini menarik sekarang, atau "evergreen".
- "source_ids": nomor bahan riset ([n]) yang mendukung fakta-fakta topik ini. Minimal satu. Hanya pakai
  nomor yang ada di daftar. Topik harus cukup didukung bahan untuk mengisi video 45–58 detik dengan 4–7
  fakta konkret.
- "scores" (0–10): relevance (cocok dengan niche & penonton), freshness (menarik/aktual sekarang),
  novelty (jarang diketahui & berbeda dari video lama), learnings_fit (sesuai pelajaran performa; 5 bila
  belum ada data). Beri nilai jujur, jangan semua tinggi.
- Jangan membuat kandidat yang sama dengan topik 60 hari terakhir:
{% if recent_topics %}{% for t in recent_topics %}  - {{ t }}
{% endfor %}{% else %}  (belum ada)
{% endif %}
{% if learnings %}

Pelajaran dari performa video sebelumnya:
{{ learnings }}
{% endif %}
