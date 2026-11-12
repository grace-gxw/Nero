import { toast } from "sonner";

import type { Job, RunDetail, RunSummary } from "./types";

export const API = "/api/channel";
/** output/<OUT_DIR>: salinan video yang lolos validasi, per tanggal */
export const OUT_DIR = "channel";

export const jobRunning = (j: Job | null | undefined) => j?.status === "proses";

/** "2026-11-12" -> "Kam, 12 Nov 2026" */
export function dateLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("id-ID", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/** detik -> "45,7 dtk" */
export const seconds = (s: number) => `${s.toLocaleString("id-ID", { maximumFractionDigits: 1 })} dtk`;

/** Tambahkan ?dl=1 / &dl=1 supaya berkas diunduh, bukan diputar. */
export const downloadUrl = (url: string) => (url ? `${url}${url.includes("?") ? "&" : "?"}dl=1` : "");

export function copyText(text: string, done = "Disalin") {
  navigator.clipboard.writeText(text).then(
    () => toast.success(done),
    () => window.prompt("Salin teks ini:", text),
  );
}

/** Ringkasan dari isi run lengkap (untuk runStatus). */
export function toSummary(d: RunDetail): RunSummary {
  return {
    ...d,
    qc: d.qc ? { score: d.qc.score, passed: d.qc.pass } : null,
    render: d.render ? { valid: d.render.valid, duration: d.render.duration, needs_review: d.render.needs_review } : null,
  };
}

/** Status satu run untuk lencana di daftar & kepala hasil. */
export function runStatus(r: RunSummary): { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" } {
  if (r.running) return { label: "Sedang berjalan", tone: "info" };
  if (r.failed?.cancelled) return { label: "Dibatalkan", tone: "muted" };
  if (r.failed) return { label: "Gagal", tone: "bad" };
  if (r.qc && !r.qc.passed) return { label: "QC gagal", tone: "bad" };
  if (r.render?.valid && r.render.needs_review) return { label: "Video siap · perlu dicek", tone: "warn" };
  if (r.render?.valid) return { label: "Video siap", tone: "ok" };
  if (r.render) return { label: "Video tidak valid", tone: "bad" };
  if (r.title) return { label: "Rencana siap", tone: "info" };
  return { label: "Belum selesai", tone: "muted" };
}

export const TONE_TEXT = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-destructive",
  info: "text-info",
  muted: "text-muted-foreground",
} as const;

export const TONE_DOT = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-destructive",
  info: "bg-info",
  muted: "bg-muted-foreground",
} as const;
