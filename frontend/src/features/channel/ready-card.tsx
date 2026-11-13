"use client";

import { Loader2Icon, PlayIcon } from "lucide-react";
import { useState } from "react";

import { useNero } from "@/components/nero/store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";

import { API, jobRunning } from "./api";
import { useChannel } from "./store";
import type { Job, ScheduleStatus } from "./types";

/** Kartu "2. Kesiapan": apa yang sudah diisi (wajib vs opsional) dan jadwal harian. */
export function ReadyCard() {
  const { state, setSettingsOpen } = useChannel();
  const nero = useNero();
  const items = state?.ready ?? [];
  const optionalMissing = items.filter((r) => !r.required && !r.ok).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">2. Kesiapan</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <ul className="grid gap-2 text-sm">
          {items.map((r) => (
            <li key={r.id} className="flex items-start gap-2">
              <span className={cn("mt-1.5 size-2 flex-none rounded-full", r.ok ? "bg-ok" : r.required ? "bg-warn" : "bg-muted-foreground/40")} />
              <span className={cn("min-w-0 flex-1", !r.ok && "text-muted-foreground")}>{r.label}</span>
              <span className={cn("text-xs", r.ok ? "text-ok" : r.required ? "text-warn" : "text-muted-foreground")}>
                {r.ok ? "siap" : r.required ? "wajib" : "opsional"}
              </span>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)}>
            Atur key &amp; pengaturan
          </Button>
          {items.some((r) => r.id === "gemini" && !r.ok) && (
            <Button size="sm" variant="outline" onClick={() => nero.setSettingsOpen(true)}>
              Isi key Gemini
            </Button>
          )}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {optionalMissing > 0
            ? "Tanpa key video stok dan gambar AI, visual memakai grafik buatan Nero dan videonya ditandai perlu dicek."
            : "Semua sumber visual siap."}{" "}
          Video diunggah sendiri memakai paket unggah, atau diunggah uji (private) lewat YouTube setelah dihubungkan
          di Pengaturan. Video yang diunggah sendiri dengan judul buatan Nero ikut dianalisis.
        </p>
        {state?.schedule && <SchedulePanel s={state.schedule} />}
      </CardContent>
    </Card>
  );
}

const STATE_TEXT: Record<ScheduleStatus["today"]["state"], string> = {
  menunggu: "belum dijalankan",
  berjalan: "sedang berjalan",
  selesai: "selesai",
  gagal: "gagal",
  dilewati: "dilewati",
  dibatalkan: "dibatalkan",
  menyerah: "gagal",
  jam_mundur: "ditahan",
};

/** "2026-11-13T05:17:00+07:00" -> "hari ini 05.17" atau "Sabtu 05.17", di zona waktu jadwal (bukan zona browser) */
function whenLabel(iso: string, today: string, tz: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const time = d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", timeZone: tz });
  return iso.slice(0, 10) === today
    ? `hari ini ${time}`
    : `${d.toLocaleDateString("id-ID", { weekday: "long", timeZone: tz })} ${time}`;
}

/** Jadwal harian: keadaan run hari ini, run berikutnya, peringatan buka otomatis, tombol jalankan sekarang. */
function SchedulePanel({ s }: { s: ScheduleStatus }) {
  const { state, trackJob, refresh, confirm } = useChannel();
  const nero = useNero();
  const [sending, setSending] = useState(false);
  const busy = jobRunning(state?.job);
  const t = s.today;
  const runTime = s.run_time.replace(":", ".");

  async function runNow() {
    const ok = await confirm({
      title: "Jalankan run harian sekarang?",
      description:
        "Run sungguhan untuk hari ini: membuat video, mengunggah bila upload otomatis menyala, dan mengirim paket " +
        "ke Telegram. Hasil yang sudah ada hari ini dipakai ulang.",
      action: "Jalankan",
    });
    if (!ok) return;
    setSending(true);
    try {
      const r = await post<{ ok: boolean; job: Job | null }>(`${API}/schedule/run`);
      if (r.job) trackJob(r.job.id);
      refresh();
    } catch {}
    setSending(false);
  }

  if (!s.enabled) {
    return (
      <div className="rounded-lg bg-muted/50 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">Jadwal harian: nonaktif.</span> Saat Channel Otomatis aktif,
        run harian berjalan sendiri tiap hari pukul {runTime} WIB selama Nero terbuka.
      </div>
    );
  }
  const bad = ["gagal", "menyerah", "dilewati", "jam_mundur"].includes(t.state);
  const canRun = !["berjalan", "selesai", "jam_mundur"].includes(t.state);
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-muted/50 px-3 py-2.5 text-xs leading-relaxed">
      <p role="status">
        <span className="font-medium">Jadwal harian: aktif, tiap hari pukul {runTime} WIB.</span>{" "}
        <span className={cn(bad ? "text-warn" : t.state === "selesai" ? "text-ok" : "text-muted-foreground")}>
          Run hari ini {STATE_TEXT[t.state] ?? t.state}
          {t.attempts > 1 && ` (percobaan ${t.attempts} dari ${s.max_attempts})`}
          {t.state === "gagal" && t.next_retry && `; dicoba lagi ${whenLabel(t.next_retry, t.day, s.timezone)}`}.
        </span>
      </p>
      {t.note && <p className="text-muted-foreground">{t.note}</p>}
      {s.next_run && t.state !== "berjalan" && (
        <p className="text-muted-foreground">Run berikutnya: {whenLabel(s.next_run, t.day, s.timezone)}.</p>
      )}
      {s.autostart === false && (
        <p className="text-warn">
          Nero tidak dibuka otomatis saat Windows menyala, jadi run harian terlewat bila Nero belum dibuka.{" "}
          <button
            type="button"
            className="font-medium underline underline-offset-4"
            onClick={() => nero.setSettingsOpen(true)}
          >
            Nyalakan di Pengaturan Nero
          </button>
          .
        </p>
      )}
      {canRun && (
        <Button size="sm" variant="outline" className="w-fit" disabled={busy || sending} onClick={runNow}>
          {sending ? <Loader2Icon className="animate-spin" /> : <PlayIcon />} Jalankan run harian sekarang
        </Button>
      )}
    </div>
  );
}
