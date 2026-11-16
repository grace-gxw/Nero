"use client";

import { Loader2Icon, PlusIcon, Trash2Icon, UploadIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { CommitInput, SimpleSelect, type Option } from "@/components/fields";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { api, post } from "@/lib/api";
import { cn } from "@/lib/utils";

import { API } from "./api";
import {
  CAPTION_COLORS,
  FONTS,
  STYLE_FIELDS,
  colorHex,
  describe,
  fontFamily,
  labelOf,
  type StyleField,
} from "./options";
import { thumbUrl, useStyles } from "./style-store";
import { useStore } from "./store";
import type { Style, StyleSource } from "./types";

/** Gambar contoh 9:16 sebuah gaya (dibuat server dari frame video terbaru). */
export function StyleThumb({ id, k, className }: { id: string; k?: string; className?: string }) {
  const [loaded, setLoaded] = useState<string | null>(null);
  return (
    <div className={cn("relative aspect-[9/16] overflow-hidden rounded-lg bg-muted", className)}>
      {k && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={thumbUrl(id, k)}
          alt=""
          loading="lazy"
          onLoad={() => setLoaded(k)}
          className={cn("size-full object-cover transition-opacity", loaded === k ? "opacity-100" : "opacity-0")}
        />
      )}
      {loaded !== k && <div className="absolute inset-0 animate-pulse bg-muted" />}
    </div>
  );
}

/** Satu frame contoh dari video terbaru (atau latar contoh) dengan gaya ini, diperbarui saat gaya berubah. */
export function StylePreview({ style, className }: { style: Style; className?: string }) {
  const { state } = useStore();
  const [img, setImg] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const source = state.projects.find((p) => p.status === "siap" && p.source);
  const key = JSON.stringify(style);

  useEffect(() => {
    let stale = false;
    let url: string | null = null;
    const t = window.setTimeout(async () => {
      setLoading(true);
      try {
        const r = await fetch(`${API}/preview-style`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ style: JSON.parse(key) }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          throw new Error((j as { error?: string }).error || "Pratinjau gagal");
        }
        const blob = await r.blob();
        if (stale) return;
        url = URL.createObjectURL(blob);
        setImg(url);
        setError("");
      } catch (e) {
        if (!stale) setError((e as Error).message);
      } finally {
        if (!stale) setLoading(false);
      }
    }, 250);
    return () => {
      stale = true;
      window.clearTimeout(t);
      // gambar lama dilepas setelah gambar baru tampil
      if (url) window.setTimeout(() => URL.revokeObjectURL(url!), 2000);
    };
  }, [key]);

  return (
    <div className={cn("flex flex-col items-center gap-2", className)}>
      <div className="relative aspect-[9/16] w-full overflow-hidden rounded-xl border bg-black">
        {img && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={img}
            alt="Pratinjau gaya video"
            className={cn("size-full object-cover transition-opacity", loading && "opacity-60")}
          />
        )}
        {loading && !img && <div className="absolute inset-0 animate-pulse bg-muted/30" />}
      </div>
      <span className="text-center text-[11px] text-muted-foreground">
        {error ||
          (loading
            ? "Membuat pratinjau..."
            : source
              ? `Frame dari "${source.title}"`
              : "Latar contoh. Tambahkan video untuk melihat dengan video Anda.")}
      </span>
    </div>
  );
}

/** Kontrol yang tidak berlaku saat fiturnya dimatikan, mis. warna subtitle saat subtitle mati. */
const DEPENDS: Partial<Record<keyof Style, (s: Style) => boolean>> = {
  title_bg: (s) => s.title_on,
  title_text: (s) => s.title_on,
  title_secs: (s) => s.title_on,
  title_size: (s) => s.title_on,
  title_pos: (s) => s.title_on,
  progress_pos: (s) => s.progress_bar,
  progress_color: (s) => s.progress_bar,
  progress_h: (s) => s.progress_bar,
  zoom_scale: (s) => s.zoom === "hentak",
  kinetic_lines: (s) => s.captions && s.caption_mode === "kinetik",
  caption_words: (s) => s.captions && s.caption_mode !== "kinetik",
  caption_stroke_color: (s) => s.captions && (s.caption_stroke > 0 || s.caption_stroke_ratio > 0),
  broll_fit: (s) => s.broll,
  broll_after: (s) => s.broll,
  broll_len: (s) => s.broll,
  broll_every: (s) => s.broll,
};

export function isDisabled(field: StyleField, style: Style): boolean {
  if (field.type === "broll") return false;
  const dep = DEPENDS[field.key] ?? (field.key.startsWith("caption_") ? (s: Style) => s.captions : undefined);
  return Boolean(dep && !dep(style));
}

export function StyleControl({
  field,
  style,
  onChange,
}: {
  field: StyleField;
  style: Style;
  onChange: (key: keyof Style, value: unknown) => void;
}) {
  const value = style[field.key];
  const set = (v: unknown) => onChange(field.key, v);
  const off = isDisabled(field, style);
  const wide = field.type === "font" || field.type === "color" || field.type === "broll";

  if (field.type === "bool") {
    return (
      <Label className={cn("cursor-pointer self-end py-1.5 font-normal", off && "pointer-events-none opacity-45")}>
        <Checkbox checked={Boolean(value)} onCheckedChange={(v) => set(v === true)} disabled={off} />
        {field.label}
      </Label>
    );
  }
  return (
    <div className={cn("flex flex-col gap-1.5", wide && "col-span-full", off && "pointer-events-none opacity-45")}>
      <Label className="flex items-baseline justify-between text-xs font-normal text-muted-foreground">
        {field.label}
        {field.type === "range" && (
          <span className="font-mono text-[11px] text-foreground tabular-nums">{describe(field.key, value)}</span>
        )}
      </Label>
      {field.type === "select" && (
        <SimpleSelect value={value as string | number} onChange={set} options={field.options} aria-label={field.label} />
      )}
      {field.type === "range" && <RangeControl field={field} value={Number(value)} onCommit={set} />}
      {field.type === "text" && (
        <CommitInput value={String(value ?? "")} onCommit={set} placeholder={field.placeholder} />
      )}
      {field.type === "color" && (
        <ColorPicker
          value={String(value ?? "")}
          onChange={set}
          label={field.label}
          palette={field.palette ?? CAPTION_COLORS}
          extras={field.extras}
        />
      )}
      {field.type === "font" && <FontPicker value={String(value)} onChange={set} upper={style.caption_upper} />}
      {field.type === "broll" && <BrollLibrary />}
      {field.hint && <p className="text-[11px] text-muted-foreground">{field.hint}</p>}
    </div>
  );
}

const HEX_RE = /^#[0-9a-f]{6}$/;
const SWATCH = "size-7 flex-none rounded-full border-2 border-transparent ring-1 ring-border transition-transform hover:scale-110";
const SWATCH_ON = "border-foreground shadow-[inset_0_0_0_2px_var(--background)]";

/** Pilihan warna palet, pilihan tambahan (mis. "Otomatis"), atau warna sendiri "#rrggbb" (hasil tiruan persis). */
function ColorPicker({
  value,
  onChange,
  label,
  palette,
  extras,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  palette: Record<string, string>;
  extras?: Option<string>[];
}) {
  const v = value.toLowerCase();
  const custom = HEX_RE.test(v) ? v : "";
  const [draft, setDraft] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  // pemilih warna bawaan browser mengirim warna terus selama digeser: simpan setelah berhenti sebentar
  const pick = (hex: string) => {
    setDraft(hex);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setDraft(null);
      onChange(hex);
    }, 350);
  };
  const shown = draft ?? custom;
  return (
    <div className="flex flex-wrap items-center gap-2 py-0.5">
      {extras?.map(([o, l]) => (
        <button
          key={o || "-"}
          type="button"
          aria-pressed={v === o}
          onClick={() => onChange(o)}
          className={cn(
            "h-7 rounded-full border px-2.5 text-xs transition-colors hover:bg-muted",
            v === o && "border-foreground bg-muted font-medium",
          )}
        >
          {l}
        </button>
      ))}
      {Object.entries(palette).map(([name, color]) => (
        <button
          key={name}
          type="button"
          title={name}
          aria-label={`${label}: ${name}`}
          aria-pressed={v === name}
          onClick={() => onChange(name)}
          style={{ background: color }}
          className={cn(SWATCH, v === name && SWATCH_ON)}
        />
      ))}
      <label
        title="Warna sendiri"
        className={cn(SWATCH, "relative cursor-pointer overflow-hidden", shown && SWATCH_ON)}
        style={{ background: shown || "conic-gradient(#f33,#fd0,#3f3,#0cf,#a5f,#f5c,#f33)" }}
      >
        <span className="sr-only">{label}: warna sendiri</span>
        <input
          type="color"
          value={shown || "#ffffff"}
          onChange={(e) => pick(e.target.value)}
          className="absolute inset-0 cursor-pointer opacity-0"
        />
      </label>
      <CommitInput
        value={shown}
        onCommit={(t) => {
          const hex = (t.trim().startsWith("#") ? t.trim() : `#${t.trim()}`).toLowerCase();
          if (HEX_RE.test(hex)) onChange(hex);
          else if (t.trim()) toast.error("Tulis warna sebagai #rrggbb, mis. #ff6a00.");
        }}
        placeholder="#rrggbb"
        maxLength={7}
        aria-label={`${label}: kode warna`}
        className="h-7 w-24 font-mono text-xs"
      />
    </div>
  );
}

function FontPicker({ value, onChange, upper }: { value: string; onChange: (v: string) => void; upper: boolean }) {
  const { userFonts, loadFonts } = useStyles();
  const { confirm } = useStore();
  const fileIn = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const sample = upper ? "AA KATA" : "Aa kata";
  const all = [
    ...FONTS.map((f) => ({ ...f, user: false })),
    ...userFonts.map((f) => ({ ...f, hint: "font sendiri", css: undefined, user: true })),
  ];

  async function upload(file: File) {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await post<{ ok: boolean; font: { key: string; label: string } }>(`${API}/fonts`, fd);
      await loadFonts();
      onChange(r.font.key);
      toast.success(`Font "${r.font.label}" ditambahkan dan dipakai`);
    } catch {
    } finally {
      setBusy(false);
      if (fileIn.current) fileIn.current.value = "";
    }
  }

  async function remove(key: string, label: string) {
    const ok = await confirm({
      title: `Hapus font "${label}"?`,
      description: "Gaya yang memakai font ini akan memakai Impact sampai diganti.",
      action: "Hapus",
      destructive: true,
    });
    if (!ok) return;
    try {
      await api(`${API}/fonts/${encodeURIComponent(key)}`, { method: "DELETE" });
      if (value === key) onChange("impact");
      await loadFonts();
    } catch {}
  }

  return (
    <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-4">
      {all.map((f) => (
        <div key={f.key} className="group relative">
          <button
            type="button"
            aria-pressed={value === f.key}
            title={`${f.label}: ${f.hint}`}
            onClick={() => onChange(f.key)}
            className={cn(
              "flex w-full flex-col items-center gap-0.5 rounded-lg border px-1.5 py-2 transition-colors hover:bg-muted",
              value === f.key && "border-foreground bg-muted",
            )}
          >
            <span className="max-w-full truncate text-lg leading-none" style={{ fontFamily: fontFamily(f.key), ...f.css }}>
              {sample}
            </span>
            <span className="w-full truncate text-center text-[10px] text-muted-foreground">{f.label}</span>
          </button>
          {f.user && (
            <button
              type="button"
              title={`Hapus font ${f.label}`}
              aria-label={`Hapus font ${f.label}`}
              onClick={() => remove(f.key, f.label)}
              className="absolute top-1 right-1 rounded-full bg-background/90 p-0.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 hover:text-destructive"
            >
              <XIcon className="size-3" />
            </button>
          )}
        </div>
      ))}
      <button
        type="button"
        disabled={busy}
        onClick={() => fileIn.current?.click()}
        title="Tambah font sendiri (.ttf / .otf), mis. font yang dipakai clip referensi"
        className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-1.5 py-2 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        {busy ? <Loader2Icon className="size-4 animate-spin" /> : <PlusIcon className="size-4" />}
        Font sendiri
      </button>
      <input
        ref={fileIn}
        type="file"
        accept=".ttf,.otf,font/ttf,font/otf"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload(f);
        }}
      />
    </div>
  );
}

/** Pustaka klip b-roll: klip yang disisipkan di antara pembicara saat "Sisipkan klip b-roll" menyala. */
function BrollLibrary() {
  const { broll, loadBroll } = useStyles();
  const { confirm } = useStore();
  const fileIn = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadBroll();
  }, [loadBroll]);

  async function upload(files: FileList) {
    setBusy(true);
    try {
      const fd = new FormData();
      for (const f of Array.from(files)) fd.append("files", f);
      const r = await post<{ ok: boolean; added: string[] }>(`${API}/broll`, fd);
      await loadBroll();
      toast.success(`${r.added.length} klip ditambahkan ke pustaka b-roll`);
    } catch {
    } finally {
      setBusy(false);
      if (fileIn.current) fileIn.current.value = "";
    }
  }

  async function remove(name: string) {
    const ok = await confirm({
      title: `Hapus "${name}" dari pustaka b-roll?`,
      description: "Video yang sudah dirender tidak berubah.",
      action: "Hapus",
      destructive: true,
    });
    if (!ok) return;
    try {
      await api(`${API}/broll/${encodeURIComponent(name)}`, { method: "DELETE" });
      await loadBroll();
    } catch {}
  }

  return (
    <div className="flex flex-col gap-2">
      {broll === null ? (
        <div className="h-16 animate-pulse rounded-lg bg-muted" />
      ) : broll.length === 0 ? (
        <p className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
          Belum ada klip. Tambahkan klip pendek (anime, cuplikan film, pemandangan, dsb.) yang boleh Anda pakai.
          Nero memilih klip secara acak untuk tiap sisipan.
        </p>
      ) : (
        <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {broll.map((b) => (
            <li key={b.name} className="group relative overflow-hidden rounded-lg border bg-muted">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`${API}/broll/${encodeURIComponent(b.name)}/thumb`}
                alt=""
                loading="lazy"
                className="aspect-video w-full object-cover"
              />
              <div className="flex items-center gap-1 px-1.5 py-1 text-[10px]">
                <span className="min-w-0 flex-1 truncate" title={b.name}>
                  {b.name}
                </span>
                <span className="flex-none text-muted-foreground tabular-nums">{Math.round(b.seconds)} dtk</span>
              </div>
              <button
                type="button"
                title={`Hapus ${b.name}`}
                aria-label={`Hapus ${b.name}`}
                onClick={() => remove(b.name)}
                className="absolute top-1 right-1 rounded-full bg-background/90 p-0.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 hover:text-destructive"
              >
                <Trash2Icon className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => fileIn.current?.click()}>
          {busy ? <Loader2Icon className="animate-spin" /> : <UploadIcon />} Tambah klip b-roll
        </Button>
        <input
          ref={fileIn}
          type="file"
          accept="video/*"
          multiple
          hidden
          onChange={(e) => e.target.files?.length && upload(e.target.files)}
        />
      </div>
    </div>
  );
}

function RangeControl({
  field,
  value,
  onCommit,
}: {
  field: Extract<StyleField, { type: "range" }>;
  value: number;
  onCommit: (v: number) => void;
}) {
  const [drag, setDrag] = useState<number | null>(null);
  const first = (v: number | readonly number[]) => (Array.isArray(v) ? v[0] : (v as number));
  return (
    <Slider
      className="py-2"
      min={field.min}
      max={field.max}
      step={field.step}
      value={[drag ?? value]}
      onValueChange={(v) => setDrag(first(v))}
      onValueCommitted={(v) => {
        setDrag(null);
        onCommit(first(v));
      }}
      aria-label={field.label}
    />
  );
}

const COLOR_FIELDS = new Set<keyof Style>(STYLE_FIELDS.filter((f) => f.type === "color").map((f) => f.key));

/** Daftar pengaturan yang terbaca dari clip referensi, dengan asalnya (diukur / dinilai AI). */
export function DetectedList({
  style,
  keys,
  sources,
}: {
  style: Partial<Style>;
  keys: (keyof Style)[];
  sources?: Partial<Record<keyof Style, StyleSource>>;
}) {
  const order = STYLE_FIELDS.map((f) => f.key).filter((k) => keys.includes(k));
  if (!order.length) return <p className="text-xs text-muted-foreground">Tidak ada gaya yang bisa dibaca dari clip ini.</p>;
  return (
    <ul className="grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
      {order.map((k) => (
        <li key={k} className="flex min-w-0 items-center gap-1.5">
          {COLOR_FIELDS.has(k) && colorHex(style[k]) && (
            <span className="size-3 flex-none rounded-full border" style={{ background: colorHex(style[k]) }} />
          )}
          <span className="flex-none text-muted-foreground">{labelOf(k)}:</span>
          <span
            className="truncate font-medium"
            style={k === "caption_font" ? { fontFamily: fontFamily(String(style[k])) } : undefined}
          >
            {describe(k, style[k])}
          </span>
          {sources?.[k] && (
            <span
              className={cn(
                "ml-auto flex-none rounded px-1 py-px text-[9px] font-semibold tracking-wide uppercase",
                sources[k] === "ai" ? "bg-foreground text-background" : "border text-muted-foreground",
              )}
              title={sources[k] === "ai" ? "Dinilai AI (Gemini / AI lokal)" : "Diukur dari piksel video"}
            >
              {sources[k] === "ai" ? "AI" : "ukur"}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Simpan gaya aktif sekarang ke "Gaya saya". */
export function SaveStyleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">{open && <SaveStyleForm onClose={onClose} />}</DialogContent>
    </Dialog>
  );
}

function SaveStyleForm({ onClose }: { onClose: () => void }) {
  const { loadSettings } = useStore();
  const { loadLibrary, find } = useStyles();
  const { settings } = useStore();
  const base = find(settings?.active_style_id);
  const [name, setName] = useState(base ? `${base.name} (ubahan)` : "");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const r = await post<{ ok: boolean; style: { name: string } }>(`${API}/styles`, { name: name.trim() || "Gaya saya" });
      await Promise.all([loadSettings(), loadLibrary()]);
      toast.success(`Disimpan sebagai "${r.style.name}"`);
      onClose();
    } catch {
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Simpan ke Gaya saya</DialogTitle>
        <DialogDescription>Gaya aktif sekarang disimpan supaya bisa dipilih lagi kapan saja.</DialogDescription>
      </DialogHeader>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="style_name">Nama gaya</Label>
        <Input
          id="style_name"
          autoFocus
          maxLength={40}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && save()}
          placeholder="mis. Podcast Kuning"
        />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Batal
        </Button>
        <Button onClick={save} disabled={busy}>
          Simpan
        </Button>
      </DialogFooter>
    </>
  );
}

/** Pilih gaya untuk satu video: ikut gaya aktif, atau salah satu gaya di pustaka. */
export function StyleSelect({
  value,
  onChange,
  className,
  size,
  ...rest
}: {
  value: string | null | undefined;
  onChange: (id: string | null) => void;
  className?: string;
  size?: "sm" | "default";
  "aria-label"?: string;
}) {
  const { library, activeName } = useStyles();
  const opts: Option<string>[] = [
    ["aktif", `Gaya aktif: ${activeName}`],
    ...(library?.styles ?? []).map((s): Option<string> => [s.id, s.name]),
    ...(library?.presets ?? []).map((s): Option<string> => [s.id, `${s.name} · siap pakai`]),
  ];
  const v = value && opts.some(([id]) => id === value) ? value : "aktif";
  return (
    <SimpleSelect
      value={v}
      onChange={(id) => onChange(id === "aktif" ? null : id)}
      options={opts}
      className={className}
      size={size}
      {...rest}
    />
  );
}
