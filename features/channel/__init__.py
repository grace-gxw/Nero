"""
Channel Otomatis: setiap hari membuat satu video pendek orisinal untuk channel sendiri (riset → skrip → QC →
suara → visual → render), lalu menyiapkan paket unggah. Masih dalam pengembangan; lihat SPEC.md dan
INTEGRATION_PLAN.md.

  config.py    pengaturan (bagian "channel" di data/settings.json) & kunci API
  schemas.py   kontrak data tiap langkah
  llm.py       jawaban JSON dari Gemini/Groq/Ollama dengan fallback
  state.py     riwayat, performa, learnings (data/channel/)
  runctx.py    folder run harian, checkpoint, log, kunci run
  timeutil.py  jam tayang WIB ↔ UTC
  api.py       rute /api/channel/*
  __main__.py  CLI: python -m features.channel ...
"""
from .api import ID, SETTINGS, bp, start, summary

NAME = "Channel Otomatis"

__all__ = ["ID", "NAME", "SETTINGS", "bp", "start", "summary"]
