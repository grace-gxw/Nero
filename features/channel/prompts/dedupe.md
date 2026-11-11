Periksa apakah kandidat topik video berikut pada dasarnya SAMA dengan video yang sudah pernah dibuat
(isi/fakta utamanya sama walau kata-katanya berbeda). Topik yang hanya bersinggungan atau membahas sisi
lain yang jelas berbeda TIDAK dihitung sama.

Kandidat:
{% for c in candidates %}{{ loop.index }}. {{ c }}
{% endfor %}

Video yang sudah dibuat (60 hari terakhir):
{% for t in recent_topics %}- {{ t }}
{% endfor %}

Isi "duplicate_ids" dengan nomor kandidat yang sama dengan video lama. Kosongkan bila tidak ada.
