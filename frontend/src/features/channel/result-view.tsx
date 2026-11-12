"use client";

import {
  AlertTriangleIcon,
  ChevronDownIcon,
  ClapperboardIcon,
  ClipboardCopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FolderOpenIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useEffect, useState } from "react";

import { SimpleSelect } from "@/components/fields";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { api, openFolder } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  API,
  OUT_DIR,
  TONE_DOT,
  TONE_TEXT,
  copyText,
  dateLabel,
  downloadUrl,
  runStatus,
  seconds,
  toSummary,
} from "./api";
import { FORMAT_LABELS, ISSUE_LABELS, STEP_DOT, STEP_STATE_LABELS, VISUAL_LABELS } from "./options";
import { useChannel } from "./store";
import type { RunDetail, RunSummary, SegmentVisual } from "./types";

/** Bagian "3. Hasil": pilih tanggal, lalu lihat video, paket unggah, QC, dan naskah. */
export function ResultSection() {
  const { state, selected, setSelected, detailKick, reloadDetail } = useChannel();
  const runs = state?.runs ?? [];

  return (
    <section>
      <div className="mt-8 mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-base font-bold">3. Hasil uji coba</h2>
        <span className="text-xs text-muted-foreground">Tonton, periksa naskah, lalu salin judul & deskripsi.</span>
        <div className="flex-1" />
        {runs.length > 0 && selected && (
          <>
            <SimpleSelect
              size="sm"
              value={selected}
              onChange={(d) => setSelected(d)}
              options={runs.map((r) => [r.date, `${dateLabel(r.date)}${r.topic ? ` · ${r.topic}` : ""}`] as const)}
              className="w-auto max-w-72"
              aria-label="Pilih tanggal run"
            />
            <Button variant="outline" size="sm" onClick={reloadDetail} title="Muat ulang hasil">
              <RefreshCwIcon />
            </Button>
          </>
        )}
      </div>
      {runs.length === 0 || !selected ? (
        <p className="rounded-xl bg-card px-4 py-10 text-center text-sm text-muted-foreground ring-1 ring-foreground/10">
          Belum ada uji coba. Jalankan uji coba di atas; hasilnya muncul di sini.
        </p>
      ) : (
        <RunDetailView key={selected} day={selected} kick={detailKick} />
      )}
      {runs.length > 1 && <RunsList runs={runs} selected={selected} onSelect={setSelected} />}
    </section>
  );
}

function RunDetailView({ day, kick }: { day: string; kick: number }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api<RunDetail>(`${API}/runs/${day}`)
      .then((d) => alive && (setDetail(d), setFailed(false)))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [day, kick]);

  if (!detail) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        {failed ? "Hasil run tidak bisa dimuat." : "Memuat hasil..."}
      </p>
    );
  }
  const st = runStatus(toSummary(detail));
  return (
    <article className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-4 sm:px-5">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">
            {dateLabel(detail.date)}
            {detail.format && ` · ${FORMAT_LABELS[detail.format] ?? detail.format}`}
          </p>
          <h3 className="truncate text-base font-bold">{detail.title || detail.topic || "Belum ada topik"}</h3>
        </div>
        <span className={cn("flex items-center gap-1.5 text-sm", TONE_TEXT[st.tone])}>
          <span className={cn("size-2 rounded-full", TONE_DOT[st.tone])} /> {st.label}
        </span>
        {detail.qc && (
          <Badge variant="outline" className={detail.qc.pass ? "text-ok" : "text-destructive"}>
            QC {detail.qc.score.toLocaleString("id-ID")}/10
          </Badge>
        )}
        {detail.render && (
          <Badge variant="outline" className="text-muted-foreground">
            {seconds(detail.render.duration)}
          </Badge>
        )}
      </div>

      <StepsStrip detail={detail} />
      <Alerts detail={detail} />

      <div className="grid gap-5 p-4 sm:p-5 lg:grid-cols-[auto_1fr]">
        <VideoBox detail={detail} />
        <div className="flex min-w-0 flex-col gap-5">
          {detail.metadata && <UploadPack detail={detail} />}
          {detail.qc && <QCBox detail={detail} />}
        </div>
      </div>

      {detail.script && <ScriptBox detail={detail} />}
      {detail.research && <SourcesBox detail={detail} />}
      <LogBox lines={detail.log} />
    </article>
  );
}

function StepsStrip({ detail }: { detail: RunDetail }) {
  return (
    <ol className="flex flex-wrap gap-x-4 gap-y-1.5 border-b bg-muted/60 px-4 py-2.5 text-xs sm:px-5">
      {detail.steps.map((s) => (
        <li key={s.id} className="flex items-center gap-1.5" title={STEP_STATE_LABELS[s.state]}>
          <span className={cn("size-2 rounded-full", STEP_DOT[s.state])} />
          <span className={cn(s.state === "belum" && "text-muted-foreground")}>{s.label}</span>
          {s.state === "gagal" || s.state === "batal" ? (
            <span className="text-destructive">({STEP_STATE_LABELS[s.state]})</span>
          ) : (
            <span className="sr-only">: {STEP_STATE_LABELS[s.state]}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

function Alerts({ detail }: { detail: RunDetail }) {
  const msgs: { tone: "bad" | "warn"; text: string }[] = [];
  if (detail.failed && !detail.failed.cancelled)
    msgs.push({ tone: "bad", text: detail.failed.message || `Gagal di langkah ${detail.failed.step}.` });
  if (detail.render && !detail.render.valid)
    msgs.push({ tone: "bad", text: `Video tidak lolos pemeriksaan: ${detail.render.problems.join("; ")}` });
  if (detail.render?.needs_review)
    msgs.push({ tone: "warn", text: `Perlu dicek sebelum diunggah: ${detail.render.warnings.join("; ")}` });
  for (const w of detail.voice?.warnings ?? []) msgs.push({ tone: "warn", text: w });
  if (msgs.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5 border-b px-4 py-3 sm:px-5">
      {msgs.map((m) => (
        <p
          key={m.text}
          className={cn("flex items-start gap-2 text-sm break-words", m.tone === "bad" ? "text-destructive" : "text-warn")}
        >
          <AlertTriangleIcon className="mt-0.5 size-4 flex-none" /> {m.text}
        </p>
      ))}
    </div>
  );
}

const CATEGORY_NAMES: Record<string, string> = {
  "27": "Pendidikan",
  "28": "Sains & Teknologi",
  "24": "Hiburan",
  "22": "Orang & Blog",
};

function VideoBox({ detail }: { detail: RunDetail }) {
  const { state, holdDay } = useChannel();
  const rebuilding = holdDay === detail.date || (state?.job?.status === "proses" && state.job.day === detail.date);
  if (rebuilding) {
    return (
      <div className="grid aspect-[9/16] w-full place-items-center rounded-lg bg-muted text-center text-xs text-muted-foreground sm:w-64">
        <span className="flex flex-col items-center gap-2 px-4">
          <ClapperboardIcon className="size-5" />
          Uji coba tanggal ini sedang berjalan. Video tampil lagi setelah selesai.
        </span>
      </div>
    );
  }
  if (!detail.video) {
    return (
      <div className="grid aspect-[9/16] w-full place-items-center rounded-lg bg-muted text-center text-xs text-muted-foreground sm:w-64">
        <span className="flex flex-col items-center gap-2 px-4">
          <ClapperboardIcon className="size-5" />
          {detail.stale_video
            ? "Video lama milik naskah sebelumnya. Jalankan uji coba 'Video lengkap' untuk naskah ini."
            : detail.title
              ? "Video belum dibuat. Jalankan uji coba 'Video lengkap'."
              : "Belum ada video."}
        </span>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <video
        key={detail.video}
        src={detail.video}
        controls
        playsInline
        preload="metadata"
        className="mx-auto aspect-[9/16] h-[min(70vh,34rem)] w-auto max-w-full rounded-lg bg-black"
      />
      <div className="flex flex-wrap justify-center gap-2">
        <a href={downloadUrl(detail.video)} className={buttonVariants({ size: "sm" })}>
          <DownloadIcon /> Unduh
        </a>
        {detail.published && (
          <Button variant="outline" size="sm" onClick={() => openFolder(`${OUT_DIR}/${detail.date}`)}>
            <FolderOpenIcon /> Folder
          </Button>
        )}
      </div>
      {!detail.published && (
        <p className="max-w-64 text-center text-[11px] text-muted-foreground">
          Belum disalin ke folder hasil karena belum lolos pemeriksaan.
        </p>
      )}
    </div>
  );
}

function CopyRow({ label, value, done }: { label: string; value: string; done: string }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{label}</span>
        <div className="flex-1" />
        <Button variant="ghost" size="xs" onClick={() => copyText(value, done)} disabled={!value}>
          <ClipboardCopyIcon /> Salin
        </Button>
      </div>
      <p className="text-sm break-words whitespace-pre-wrap">{value || "-"}</p>
    </div>
  );
}

function UploadPack({ detail }: { detail: RunDetail }) {
  const m = detail.metadata!;
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-3 rounded-xl border p-3">
      <p className="text-sm font-semibold">Paket unggah manual</p>
      <CopyRow label="Judul" value={m.title} done="Judul disalin" />
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Deskripsi</span>
          <div className="flex-1" />
          <Button variant="ghost" size="xs" onClick={() => copyText(m.description, "Deskripsi disalin")}>
            <ClipboardCopyIcon /> Salin
          </Button>
        </div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className={cn("text-left text-sm break-words whitespace-pre-wrap", !open && "line-clamp-4")}
          title={open ? "Ringkas" : "Tampilkan semua"}
        >
          {m.description}
        </button>
      </div>
      <CopyRow label="Tag" value={m.tags.join(", ")} done="Tag disalin" />
      <CopyRow label="Hashtag" value={m.hashtags.join(" ")} done="Hashtag disalin" />
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Kategori YouTube: {CATEGORY_NAMES[m.category_id] ?? m.category_id ?? "-"}.{" "}
        {detail.visuals?.uses_ai_images ? (
          <b className="text-warn">Wajib: centang &quot;Konten yang diubah/sintetis&quot; saat mengunggah.</b>
        ) : (
          "Video ini tidak memakai gambar AI."
        )}
      </p>
    </div>
  );
}

function QCBox({ detail }: { detail: RunDetail }) {
  const qc = detail.qc!;
  const supported = qc.checks.filter((c) => c.kind === "fact" && c.supported).length;
  const facts = qc.checks.filter((c) => c.kind === "fact").length;
  return (
    <div className="flex flex-col gap-2 rounded-xl border p-3">
      <div className="flex items-center gap-2">
        <p className="text-sm font-semibold">Pemeriksaan fakta & kualitas</p>
        <div className="flex-1" />
        <span className={cn("text-xs", qc.pass ? "text-ok" : "text-destructive")}>
          {qc.pass ? "Lulus" : "Tidak lulus"} · skor {qc.score.toLocaleString("id-ID")}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        {supported} dari {facts} pernyataan fakta didukung kutipan sumber.
      </p>
      {qc.issues.length > 0 ? (
        <ul className="flex flex-col gap-1.5 text-xs">
          {qc.issues.map((i, k) => (
            <li key={k} className="flex flex-col gap-0.5">
              <span>
                <Badge
                  variant="outline"
                  className={cn("mr-1.5", i.type === "fact" || i.type === "policy" ? "text-destructive" : "text-warn")}
                >
                  {ISSUE_LABELS[i.type] ?? i.type}
                </Badge>
                {i.detail}
              </span>
              {i.fix && <span className="text-muted-foreground">Saran: {i.fix}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">Tidak ada catatan.</p>
      )}
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        QC otomatis bisa meloloskan nuansa yang keliru (mis. kalimat usang di sumber). Baca naskahnya sebelum unggah.
      </p>
    </div>
  );
}

/** "ai_image: key belum diisi" -> "Gambar AI: key belum diisi" */
function fallbackText(f: string): string {
  return f.replace(/^(stok|stock|ai_image|motion_graphic|plain):\s*/, (_, t: string) => {
    const key = (t === "stok" ? "stock" : t) as keyof typeof VISUAL_LABELS;
    return `${VISUAL_LABELS[key]}: `;
  });
}

function visualFor(detail: RunDetail, segId: number): SegmentVisual | undefined {
  return detail.visuals?.segments.find((v) => v.segment_id === segId);
}

function ScriptBox({ detail }: { detail: RunDetail }) {
  const s = detail.script!;
  const issuesBySeg = (id: number) =>
    (detail.qc?.checks ?? []).filter((c) => c.segment_id === id && c.kind === "fact" && !c.supported);
  return (
    <div className="border-t px-4 py-4 sm:px-5">
      <div className="mb-3 flex items-center gap-2">
        <p className="text-sm font-semibold">Naskah</p>
        <span className="text-xs text-muted-foreground">
          {s.word_count} kata
          {detail.voice && ` · ${seconds(detail.voice.duration)} suara ${detail.voice.voice.replace("id-ID-", "").replace("Neural", "")} ${detail.voice.rate}`}
        </span>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="xs"
          onClick={() =>
            copyText([s.hook, ...s.segments.map((g) => g.text), s.cta].filter(Boolean).join("\n\n"), "Naskah disalin")
          }
        >
          <ClipboardCopyIcon /> Salin naskah
        </Button>
      </div>
      <ol className="flex flex-col divide-y rounded-xl border text-sm">
        <li className="flex gap-3 px-3 py-2.5">
          <span className="w-14 flex-none text-xs text-muted-foreground">Hook</span>
          <span className="font-medium">{s.hook}</span>
        </li>
        {s.segments.map((g) => {
          const v = visualFor(detail, g.id);
          const bad = issuesBySeg(g.id);
          return (
            <li key={g.id} className="flex gap-3 px-3 py-2.5">
              <span className="w-14 flex-none text-xs text-muted-foreground">#{g.id}</span>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <span className={cn(bad.length > 0 && "text-destructive")}>{g.text}</span>
                <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  <Badge variant="secondary" className="h-auto text-[11px]">
                    {VISUAL_LABELS[v?.type ?? g.visual_type]}
                  </Badge>
                  {v && v.type !== v.requested && <span className="text-warn">rencana: {VISUAL_LABELS[v.requested]}</span>}
                  {v?.source === "pexels" || v?.source === "pixabay" ? (
                    <a href={v.url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
                      {v.author || v.source} / {v.source}
                    </a>
                  ) : null}
                  {g.on_screen_text && <span>teks layar: “{g.on_screen_text}”</span>}
                </span>
                {v && v.fallbacks.length > 0 && (
                  <span className="text-[11px] text-muted-foreground">
                    Cadangan dipakai karena: {v.fallbacks.map(fallbackText).join("; ")}
                  </span>
                )}
              </div>
            </li>
          );
        })}
        {s.cta && (
          <li className="flex gap-3 px-3 py-2.5">
            <span className="w-14 flex-none text-xs text-muted-foreground">Ajakan</span>
            <span>{s.cta}</span>
          </li>
        )}
      </ol>
    </div>
  );
}

function SourcesBox({ detail }: { detail: RunDetail }) {
  const r = detail.research!;
  return (
    <div className="border-t px-4 py-4 sm:px-5">
      <p className="mb-2 text-sm font-semibold">Riset</p>
      <p className="text-sm">
        <span className="font-medium">{r.topic}</span>
        <span className="text-muted-foreground"> · {r.angle}</span>
      </p>
      <ul className="mt-2 flex flex-col gap-1 text-xs">
        {r.sources.map((s) => (
          <li key={s.url}>
            <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline underline-offset-4">
              {s.title} <ExternalLinkIcon className="size-3" />
            </a>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-muted-foreground">
        {r.candidates} kandidat topik dinilai
        {r.rejected.length > 0 && `, ${r.rejected.length} ditolak karena mirip video lama`}.
      </p>
    </div>
  );
}

function LogBox({ lines }: { lines: string[] }) {
  const [open, setOpen] = useState(false);
  if (lines.length === 0) return null;
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border-t">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-4 py-3 text-left text-xs text-muted-foreground hover:text-foreground sm:px-5">
        Catatan proses run ini
        <ChevronDownIcon className={cn("ml-auto size-4 transition-transform", open && "rotate-180")} />
      </CollapsibleTrigger>
      <CollapsibleContent className="px-4 pb-4 sm:px-5">
        <pre className="max-h-72 overflow-auto rounded-lg bg-muted/60 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-muted-foreground">
          {lines.join("\n")}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}

function RunsList({
  runs,
  selected,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string | null;
  onSelect: (d: string) => void;
}) {
  return (
    <div className="mt-6">
      <p className="mb-2 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Riwayat uji coba</p>
      <ul className="divide-y overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
        {runs.map((r) => {
          const st = runStatus(r);
          return (
            <li key={r.date}>
              <button
                type="button"
                onClick={() => onSelect(r.date)}
                className={cn(
                  "flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-muted/60 sm:px-5",
                  selected === r.date && "bg-muted/60",
                )}
              >
                <span className="w-32 flex-none text-xs text-muted-foreground">{dateLabel(r.date)}</span>
                <span className="min-w-0 flex-1 truncate">{r.title || r.topic || "-"}</span>
                <span className={cn("flex items-center gap-1.5 text-xs", TONE_TEXT[st.tone])}>
                  <span className={cn("size-1.5 rounded-full", TONE_DOT[st.tone])} />
                  <span className="sr-only sm:not-sr-only">{st.label}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
