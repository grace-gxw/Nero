"use client";

import { ExternalLinkIcon, Loader2Icon, RefreshCwIcon, SendIcon, Trash2Icon } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, post } from "@/lib/api";
import { cn } from "@/lib/utils";

import { API, dateLabel, jobRunning } from "./api";
import { FORMAT_LABELS, WEEKDAYS } from "./options";
import { GLOBAL_MODES, useChannel } from "./store";
import type { AnalyticsData, ChannelWeek, Job, Performance, VideoPerf, VideoPerfStatus } from "./types";

const STATUS: Record<VideoPerfStatus, { label: string; tone: string }> = {
  lengkap: { label: "7 hari lengkap", tone: "text-ok" },
  sementara: { label: "sementara", tone: "text-muted-foreground" },
  belum_ada_data: { label: "belum ada data", tone: "text-muted-foreground" },
  belum_tayang: { label: "belum tayang", tone: "text-warn" },
  "": { label: "-", tone: "text-muted-foreground" },
};
const KINDS = [
  ["berhasil", "Yang berhasil"],
  ["kurang", "Yang kurang berhasil"],
  ["saran", "Saran untuk video berikutnya"],
] as const;
/** catatan analis yang sama dengan peringatan izin statistik (tidak ditampilkan dua kali) */
const SCOPE_NOTE = "Izin melihat statistik";

const num = (n: number | null | undefined) =>
  n === null || n === undefined ? "-" : Math.round(n).toLocaleString("id-ID");

/** "2026-11-13T07:01:00+00:00" -> "13 Nov 2026 14.01" (waktu komputer) */
function timeLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("id-ID", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** angka minggu ini, dengan persen naik/turun hanya bila kedua minggu sama-sama 7 hari berdata */
function change(cur: number | null, prev: number | null | undefined, comparable: boolean): string {
  if (cur === null) return "-";
  if (!comparable || prev === null || prev === undefined || prev <= 0) return num(cur);
  const pct = ((cur - prev) / prev) * 100;
  if (Math.abs(pct) < 0.5) return `${num(cur)} (sama dengan minggu sebelumnya)`;
  return `${num(cur)} (${pct > 0 ? "naik" : "turun"} ${Math.abs(pct).toFixed(0)}%)`;
}

/** Bagian "4. Analitik": performa video buatan Nero di YouTube, pelajaran (learnings.md), laporan mingguan. */
export function AnalyticsSection() {
  const { state, analyticsKick, reloadAnalytics, setSettingsOpen, trackJob, refresh, confirm } = useChannel();
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [failed, setFailed] = useState(false);
  const [sending, setSending] = useState(false);
  const job = state?.job;
  const busy = jobRunning(job);
  const mine = busy && job && GLOBAL_MODES.includes(job.mode) ? job : null;
  const tgReady = state?.telegram.configured ?? false;

  useEffect(() => {
    let alive = true;
    api<AnalyticsData>(`${API}/analytics`)
      .then((d) => alive && (setData(d), setFailed(false)))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [analyticsKick]);

  async function start(path: "run" | "report") {
    setSending(true);
    try {
      const r = await post<{ ok: boolean; job: Job }>(`${API}/analytics/${path}`);
      trackJob(r.job.id);
      refresh();
    } catch {}
    setSending(false);
  }

  async function clear() {
    const ok = await confirm({
      title: "Hapus data performa?",
      description:
        "Yang dihapus hanya data yang disimpan Nero: angka performa dan pelajaran (learnings.md). Video dan data di " +
        "YouTube tidak berubah. Data dibaca lagi di analisis berikutnya selama YouTube terhubung.",
      action: "Hapus",
      destructive: true,
    });
    if (!ok) return;
    try {
      await post(`${API}/analytics/clear`);
      toast("Data performa dihapus");
      reloadAnalytics();
    } catch {}
  }

  const perf = data?.performance ?? null;
  const day = WEEKDAYS.find(([id]) => id === data?.weekly.day)?.[1] ?? "Senin";
  const sentThisWeek = Boolean(data && data.weekly.sent_at && data.weekly.last_key === data.weekly.key);
  const locked = busy || sending || Boolean(data?.running);
  return (
    <section>
      <div className="mt-8 mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-base font-bold">4. Analitik</h2>
        <span className="text-xs text-muted-foreground">
          Performa video buatan Nero di YouTube ({data?.window_days ?? 28} hari terakhir) dan pelajaran untuk video
          berikutnya.
        </span>
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          disabled={locked || !data?.connected}
          onClick={() => start("run")}
          title={data && !data.connected ? "Hubungkan YouTube dulu di Pengaturan" : "Baca ulang performa dari YouTube"}
        >
          {mine?.mode === "analyst" ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />} Perbarui analisis
        </Button>
        {tgReady && (
          <Button
            variant="outline"
            size="sm"
            disabled={locked || !perf?.videos.length}
            onClick={() => start("report")}
            title={perf?.videos.length ? "Kirim laporan mingguan ke Telegram sekarang" : "Belum ada video untuk dilaporkan"}
          >
            {mine?.mode === "report" ? <Loader2Icon className="animate-spin" /> : <SendIcon />} Kirim laporan
          </Button>
        )}
      </div>
      {!data ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          {failed ? "Analitik tidak bisa dimuat." : "Memuat analitik..."}
        </p>
      ) : (
        <article className="flex flex-col gap-5 rounded-xl bg-card px-4 py-4 ring-1 ring-foreground/10 sm:px-5">
          {mine && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2Icon className="size-4 animate-spin" />
              {mine.mode === "report" ? "Menyiapkan & mengirim laporan mingguan..." : "Membaca performa dari YouTube..."}
              <span className="tabular-nums">{Math.round(mine.progress * 100)}%</span>
            </p>
          )}
          <StatusLine data={data} perf={perf} onSettings={() => setSettingsOpen(true)} />
          <LearningsBlock data={data} perf={perf} onSettings={() => setSettingsOpen(true)} />
          {perf && perf.this_week && perf.this_week.days > 0 && (
            <WeekLine wk={perf.this_week} prev={perf.prev_week} comparable={data.comparable} />
          )}
          {perf && perf.videos.length > 0 && <VideoTable perf={perf} top={data.top} bottom={data.bottom} />}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3 text-[11px] text-muted-foreground">
            <span className="min-w-0 flex-1">
              Laporan mingguan ke Telegram tiap {day} (bisa diubah di Pengaturan), dikirim run harian saat Channel
              Otomatis aktif.{" "}
              {sentThisWeek
                ? `Laporan minggu ini terkirim ${timeLabel(data.weekly.sent_at)}.`
                : data.weekly.due
                  ? "Laporan minggu ini belum terkirim."
                  : ""}{" "}
              Judul, topik, dan format adalah catatan Nero (bukan data YouTube); angkanya dari YouTube.
            </span>
            {(perf || data.learnings) && (
              <Button variant="ghost" size="sm" className="text-destructive" disabled={locked} onClick={clear}>
                <Trash2Icon /> Hapus data performa
              </Button>
            )}
          </div>
        </article>
      )}
    </section>
  );
}

function StatusLine({ data, perf, onSettings }: { data: AnalyticsData; perf: Performance | null; onSettings: () => void }) {
  if (!data.connected) {
    return (
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <p className="min-w-0 flex-1 text-muted-foreground">
          Hubungkan YouTube di Pengaturan supaya Nero bisa membaca performa video, termasuk video yang Anda unggah
          sendiri dengan judul buatan Nero. Aktifkan juga YouTube Analytics API di project Google Cloud yang sama.
        </p>
        <Button size="sm" variant="outline" onClick={onSettings}>
          Buka Pengaturan
        </Button>
      </div>
    );
  }
  // catatan izin statistik: ditampilkan sekali (baris di bawah), dan basi bila izinnya sudah diberikan lewat Hubungkan
  // ulang (scope OAuth dicatat dari jawaban Google); token env var memakai catatan dari analis apa adanya
  const scopeNote = (perf?.notes ?? []).some((n) => n.startsWith(SCOPE_NOTE));
  const staleScope = scopeNote && data.can_analytics && !data.from_env;
  const notes = (perf?.notes ?? []).filter((n) => data.from_env || !n.startsWith(SCOPE_NOTE));
  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <p className="text-muted-foreground">
        {perf?.updated_at ? `Diperbarui ${timeLabel(perf.updated_at)}` : "Belum dianalisis. Tekan Perbarui analisis."}
        {perf?.analytics && perf.data_through &&
          ` · data YouTube Analytics s.d. ${dateLabel(perf.data_through)} (waktu Pasifik, tertunda 48–72 jam)`}
        {perf && ` · ${perf.videos.length} video, ${perf.mature} dengan data 7 hari pertama lengkap`}
      </p>
      {!data.can_analytics && (
        <p className="text-warn">
          Izin melihat statistik belum diberikan. Hubungkan ulang YouTube dan centang izin statistik.
        </p>
      )}
      {staleScope && <p className="text-muted-foreground">Izin statistik sudah diberikan. Tekan Perbarui analisis.</p>}
      {notes.map((n) => (
        <p key={n} className="text-warn">
          {n}
        </p>
      ))}
    </div>
  );
}

function LearningsBlock({ data, perf, onSettings }: { data: AnalyticsData; perf: Performance | null; onSettings: () => void }) {
  // izin AI dimatikan: pelajaran lama tidak ditampilkan (dan tidak dipakai di prompt)
  const points = data.share_with_ai ? (perf?.learnings ?? []) : [];
  if (points.length > 0) {
    return (
      <div className="grid gap-4 sm:grid-cols-3">
        {KINDS.map(([kind, title]) => {
          const list = points.filter((p) => p.kind === kind);
          if (!list.length) return null;
          return (
            <div key={kind} className="flex flex-col gap-1.5">
              <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{title}</p>
              <ul className="grid gap-1.5 text-sm">
                {list.map((p) => (
                  <li key={p.text} className="leading-snug">
                    {p.text}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
        <p className="text-[11px] text-muted-foreground sm:col-span-3">
          Disusun AI dari angka YouTube{perf?.learnings_at ? ` (${timeLabel(perf.learnings_at)})` : ""}. Pelajaran ini
          dipakai saat riset topik dan menulis naskah berikutnya.
          {perf?.learnings_state === "gagal" && " Rangkuman terbaru gagal dibuat; ini pelajaran sebelumnya."}
        </p>
      </div>
    );
  }
  const enough = (perf?.mature ?? 0) >= data.min_videos;
  let why: string;
  let askPermission = false;
  if (perf && enough && !data.share_with_ai) {
    why =
      "Pelajaran otomatis belum dibuat: izin mengirim statistik video ke AI belum diberikan (Pengaturan channel → " +
      "Analitik).";
    askPermission = true;
  } else if (perf?.learnings_state === "izin_ai" && data.share_with_ai) {
    why = "Izin AI sudah diberikan. Tekan Perbarui analisis untuk menyusun pelajaran.";
  } else {
    // isi learnings.md menjelaskan alasannya (data belum cukup, belum terhubung, AI gagal, ...)
    why = data.learnings
      .split("\n")
      .filter((l) => l.trim() && !l.startsWith("#") && !l.startsWith("- "))
      .join(" ");
  }
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-muted/50 px-3 py-2.5 text-sm">
      <p className="min-w-0 flex-1">
        <span className="font-medium">Pelajaran: </span>
        {why || `Belum ada. Butuh minimal ${data.min_videos} video dengan data 7 hari pertama yang lengkap.`}
      </p>
      {askPermission && (
        <Button size="sm" variant="outline" onClick={onSettings}>
          Atur izin AI
        </Button>
      )}
    </div>
  );
}

function WeekLine({ wk, prev, comparable }: { wk: ChannelWeek; prev: ChannelWeek | null; comparable: boolean }) {
  return (
    <p className="text-sm">
      <span className="font-medium">
        Seluruh channel, {dateLabel(wk.start)}–{dateLabel(wk.end)}
        {wk.days < 7 && ` (${wk.days} hari berdata)`}:
      </span>{" "}
      engaged views {change(wk.engaged_views, prev?.engaged_views, comparable)} · views{" "}
      {change(wk.views, prev?.views, comparable)} · pelanggan +{num(wk.subscribers_gained ?? 0)} / −
      {num(wk.subscribers_lost ?? 0)}
    </p>
  );
}

function VideoTable({ perf, top, bottom }: { perf: Performance; top: string[]; bottom: string[] }) {
  const rows = [...perf.videos].sort((a, b) => b.day.localeCompare(a.day));
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:-mx-5 sm:px-5">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="text-[11px] tracking-wider text-muted-foreground uppercase">
          <tr className="border-b">
            <th className="py-2 pr-3 font-semibold">Tanggal</th>
            <th className="py-2 pr-3 font-semibold">Video</th>
            <th className="py-2 pr-3 font-semibold">Data</th>
            <th className="py-2 pr-3 text-right font-semibold" title="Engaged views 7 hari pertama sejak tayang">
              Engaged 7 hari
            </th>
            <th className="py-2 pr-3 text-right font-semibold" title="Rata-rata ditonton (7 hari pertama)">
              Ditonton
            </th>
            <th className="py-2 text-right font-semibold" title="Views publik sekarang, termasuk putar ulang & autoplay">
              Views sekarang
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => (
            <VideoRow
              key={v.video_id}
              v={v}
              rank={top.includes(v.video_id) ? "teratas" : bottom.includes(v.video_id) ? "terbawah" : ""}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VideoRow({ v, rank }: { v: VideoPerf; rank: string }) {
  const st = STATUS[v.status] ?? STATUS[""];
  const n = v.first_week;
  return (
    <tr className="border-b last:border-0">
      <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">{dateLabel(v.day)}</td>
      <td className="max-w-72 py-2 pr-3">
        <a
          href={`https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex max-w-full items-center gap-1 font-medium hover:underline"
        >
          <span className="truncate">{v.title || v.topic || v.video_id}</span>
          <ExternalLinkIcon className="size-3 flex-none" />
        </a>
        <p className="truncate text-xs text-muted-foreground">
          {v.format ? FORMAT_LABELS[v.format] ?? v.format : "format tidak tercatat"}
          {v.source === "manual" && " · diunggah sendiri"}
          {rank && <span className={rank === "teratas" ? "text-ok" : "text-warn"}> · {rank}</span>}
        </p>
      </td>
      <td className={cn("py-2 pr-3 text-xs whitespace-nowrap", st.tone)} title={v.note || undefined}>
        {st.label}
      </td>
      <td className="py-2 pr-3 text-right tabular-nums">{num(n?.engaged_views)}</td>
      <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">
        {n?.avg_view_duration != null ? `${Math.round(n.avg_view_duration)} dtk` : "-"}
        {n?.avg_view_pct != null && <span className="text-muted-foreground"> · {Math.round(n.avg_view_pct)}%</span>}
      </td>
      <td className="py-2 text-right tabular-nums">{num(v.public_views)}</td>
    </tr>
  );
}
