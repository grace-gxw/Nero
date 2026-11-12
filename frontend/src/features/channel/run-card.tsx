"use client";

import { AlertTriangleIcon, ChevronDownIcon, Loader2Icon, PlayIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SimpleSelect } from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { post } from "@/lib/api";
import { cn } from "@/lib/utils";

import { API, dateLabel, jobRunning } from "./api";
import { MODES, STEP_DOT, STEP_STATE_LABELS } from "./options";
import { MODE_NAMES, useChannel } from "./store";
import type { Job, RunMode, Settings } from "./types";

/** Kartu "1. Uji coba": pilih genre (bila belum), jalankan, dan lihat progres per langkah. */
export function RunCard() {
  const { settings, state, refresh, reloadDetail, applySettings, confirm, setHoldDay, trackJob } = useChannel();
  const [mode, setMode] = useState<RunMode>("video");
  const [fresh, setFresh] = useState(false);
  const [sending, setSending] = useState(false);
  const job = state?.job ?? null;
  const running = jobRunning(job);
  const niche = state?.niche ?? settings?.config.channel.niche ?? "";
  const missing = (state?.ready ?? []).filter((r) => r.required && !r.ok && r.id !== "genre");
  const todayRun = state?.runs.find((r) => r.date === state.today);
  const elsewhere = !running && Boolean(todayRun?.running);   // run hari ini dipegang proses lain (CLI/jadwal)

  async function start() {
    if (fresh && todayRun) {
      const ok = await confirm({
        title: "Mulai dari riset lagi?",
        description: `Hasil uji coba ${dateLabel(todayRun.date)} (topik, naskah, video) akan diganti dengan yang baru.`,
        action: "Mulai ulang",
        destructive: true,
      });
      if (!ok) return;
    }
    // sembunyikan pemutar video hari ini dulu: di Windows berkas yang sedang diputar tidak bisa diganti
    if (state) setHoldDay(state.today);
    setSending(true);
    try {
      const r = await post<{ ok: boolean; job: Job }>(`${API}/run`, { mode, force: fresh ? ["research"] : [] });
      trackJob(r.job.id);
      toast("Uji coba dimulai");
      setFresh(false);
    } catch {
      setHoldDay(null);
    }
    setSending(false);
    refresh();
  }

  async function cancel() {
    try {
      const r = await post(`${API}/run/cancel`);
      toast(r.count ? "Membatalkan..." : "Tidak ada yang perlu dibatalkan");
    } catch {}
    refresh();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">1. Uji coba</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!niche.trim() && settings ? (
          <GenrePicker settings={settings} onSaved={(s) => (applySettings(s), refresh(), reloadDetail())} />
        ) : (
          <p className="text-sm">
            <span className="text-muted-foreground">Genre: </span>
            <span className="font-medium">{genreName(settings, niche)}</span>
          </p>
        )}

        <div className="flex flex-col gap-2">
          <div className="flex w-fit gap-0.5 rounded-lg bg-muted p-0.5 text-xs" role="radiogroup" aria-label="Jenis uji coba">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={mode === m.id}
                onClick={() => setMode(m.id)}
                className={cn(
                  "rounded-md px-2.5 py-1 font-medium text-muted-foreground transition-colors",
                  mode === m.id && "bg-background text-foreground shadow-sm",
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">{MODES.find((m) => m.id === mode)?.hint}</p>
        </div>

        {todayRun && (
          <Label className="cursor-pointer font-normal">
            <Checkbox checked={fresh} onCheckedChange={(v) => setFresh(v === true)} disabled={running} />
            Mulai dari riset lagi (topik baru). Tanpa ini, hasil hari ini yang masih cocok dipakai ulang.
          </Label>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            size="lg"
            className="px-5 font-bold"
            disabled={running || sending || elsewhere || !niche.trim() || missing.length > 0}
            onClick={start}
          >
            {sending ? <Loader2Icon className="animate-spin" /> : <PlayIcon />} Jalankan uji coba
          </Button>
          <p className="flex-1 text-xs leading-relaxed text-muted-foreground">
            Uji coba tidak mengunggah dan tidak mengirim apa pun.
            {elsewhere && <span className="text-info"> Run hari ini sedang berjalan di proses lain.</span>}
            {missing.length > 0 && (
              <span className="text-warn"> Lengkapi dulu: {missing.map((m) => m.label).join(", ")}.</span>
            )}
          </p>
        </div>

        {job && <JobView job={job} onCancel={cancel} />}
      </CardContent>
    </Card>
  );
}

function genreName(settings: Settings | null, niche: string): string {
  const g = settings?.genres.find((x) => x.niche === niche);
  return g ? g.name : niche;
}

function GenrePicker({ settings, onSaved }: { settings: Settings; onSaved: (s: Settings) => void }) {
  const [gid, setGid] = useState(settings.genres[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const g = settings.genres.find((x) => x.id === gid);

  async function save() {
    setSaving(true);
    try {
      const s = await post<Settings & { ok: boolean }>(`${API}/settings`, { genre: gid });
      onSaved(s);
      toast.success("Genre disimpan");
    } catch {}
    setSaving(false);
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-warn/40 p-3">
      <p className="text-sm font-medium">Pilih genre channel dulu</p>
      <div className="flex flex-wrap gap-2">
        <SimpleSelect
          value={gid}
          onChange={setGid}
          options={settings.genres.map((x) => [x.id, x.name] as const)}
          className="min-w-0 flex-1 sm:max-w-64"
          aria-label="Genre channel"
        />
        <Button onClick={save} disabled={saving || !gid}>
          Simpan genre
        </Button>
      </div>
      {g && (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {g.niche}. Penonton: {g.audience}. Bisa diubah kapan saja di Pengaturan channel.
        </p>
      )}
    </div>
  );
}

function JobView({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const [logOpen, setLogOpen] = useState(false);
  const running = job.status === "proses";
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-xl border p-4",
        job.status === "gagal" && "border-destructive/40",
        job.status === "selesai" && "border-ok/40",
      )}
    >
      <div className="flex items-center gap-2 text-sm">
        {running && <Loader2Icon className="size-4 animate-spin" />}
        <span className="min-w-0 flex-1">
          {MODE_NAMES[job.mode] && <span className="text-muted-foreground">{MODE_NAMES[job.mode]} · </span>}
          {running && job.cancelling
            ? "Membatalkan... (menunggu langkah ini berhenti)"
            : running
            ? job.stage || "Menyiapkan..."
            : job.status === "selesai"
              ? `Selesai · ${dateLabel(job.day)}`
              : job.status === "batal"
                ? "Dibatalkan"
                : "Gagal"}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">{Math.round(job.progress * 100)}%</span>
        {running && (
          <Button
            size="sm"
            variant="outline"
            onClick={onCancel}
            title={
              job.mode === "upload"
                ? "Hentikan unggah (bisa dilanjutkan nanti)"
                : job.mode === "notify" || job.mode === "report"
                  ? "Hentikan kiriman ke Telegram"
                  : job.mode === "analyst"
                    ? "Hentikan analisis"
                    : "Hentikan uji coba"
            }
            disabled={job.cancelling}
          >
            <XIcon /> Batal
          </Button>
        )}
      </div>
      {running && <Progress value={job.progress * 100} />}
      <ol className="grid gap-1.5 text-xs sm:grid-cols-2">
        {job.steps.map((s) => (
          <li key={s.id} className="flex items-center gap-2">
            <span className={cn("size-2 flex-none rounded-full", STEP_DOT[s.state])} />
            <span className={cn("min-w-0 flex-1 truncate", s.state === "antri" && "text-muted-foreground")}>
              {s.label}
            </span>
            <span className="text-muted-foreground tabular-nums">
              {s.state === "proses" && s.progress ? `${Math.round(s.progress * 100)}%` : STEP_STATE_LABELS[s.state]}
            </span>
          </li>
        ))}
      </ol>
      {job.error && job.status !== "batal" && (
        <p className="flex items-start gap-2 text-sm break-words text-destructive">
          <AlertTriangleIcon className="mt-0.5 size-4 flex-none" /> {job.error}
        </p>
      )}
      <Collapsible open={logOpen} onOpenChange={setLogOpen}>
        <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
          Catatan proses
          <ChevronDownIcon className={cn("size-3.5 transition-transform", logOpen && "rotate-180")} />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-muted/60 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
            {job.log.join("\n") || "Belum ada catatan."}
          </pre>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
