import type { Option } from "@/components/fields";

import type { RunMode, StepState, VisualType, WeekDay } from "./types";

export const MODES: { id: RunMode; label: string; hint: string }[] = [
  { id: "video", label: "Video lengkap", hint: "Riset sampai render. ±3–6 menit." },
  { id: "plan", label: "Rencana saja", hint: "Riset, naskah, QC, judul. Tanpa video, ±1–2 menit." },
];

export const RATES: Option<string>[] = [
  ["-10%", "Lebih pelan (-10%)"],
  ["-5%", "Agak pelan (-5%)"],
  ["+0%", "Normal"],
  ["+5%", "Agak cepat (+5%, bawaan)"],
  ["+10%", "Cepat (+10%)"],
  ["+15%", "Paling cepat (+15%)"],
];

export const VISUAL_LABELS: Record<VisualType, string> = {
  stock: "Video stok",
  ai_image: "Gambar AI",
  motion_graphic: "Grafik",
  plain: "Latar polos",
};

export const STEP_STATE_LABELS: Record<StepState, string> = {
  antri: "menunggu",
  proses: "berjalan",
  selesai: "selesai",
  dipakai: "dipakai ulang",
  gagal: "gagal",
  batal: "dibatalkan",
  lewat: "tidak dijalankan",
  belum: "belum",
};

export const STEP_DOT: Record<StepState, string> = {
  antri: "bg-muted-foreground/40",
  proses: "bg-info animate-[pulse-dot_1s_infinite]",
  selesai: "bg-ok",
  dipakai: "bg-ok/60",
  gagal: "bg-destructive",
  batal: "bg-muted-foreground",
  lewat: "bg-muted-foreground/40",
  belum: "bg-muted-foreground/40",
};

export const ISSUE_LABELS: Record<string, string> = {
  fact: "Fakta",
  policy: "Kebijakan",
  quality: "Kualitas",
  repetition: "Pengulangan",
  length: "Panjang",
};

export const FORMAT_LABELS: Record<string, string> = {
  myth_vs_fact: "Mitos vs fakta",
  list_3: "Tiga poin",
  story: "Cerita",
  question: "Pertanyaan",
  explainer: "Penjelasan",
};

export const WEEKDAYS: Option<WeekDay>[] = [
  ["monday", "Senin"],
  ["tuesday", "Selasa"],
  ["wednesday", "Rabu"],
  ["thursday", "Kamis"],
  ["friday", "Jumat"],
  ["saturday", "Sabtu"],
  ["sunday", "Minggu"],
];

/** Halaman pembuatan key (dibuka di browser). */
export const KEY_PAGES: Record<string, string> = {
  groq: "https://console.groq.com/keys",
  pexels: "https://www.pexels.com/api/",
  pixabay: "https://pixabay.com/api/docs/",
  cloudflare: "https://dash.cloudflare.com/?to=/:account/ai/workers-ai",
  telegram: "https://t.me/BotFather",
};
