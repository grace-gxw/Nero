"use client";

import { ExternalLinkIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { SimpleSelect } from "@/components/fields";
import { useNero } from "@/components/nero/store";
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
import { post } from "@/lib/api";

import { API } from "./api";
import { KEY_PAGES, RATES, VISUAL_LABELS } from "./options";
import { useChannel } from "./store";
import type { ChannelConfig, Settings } from "./types";

const CUSTOM = "kustom";
type AllowedVisual = ChannelConfig["visuals"]["allowed_types"][number];
const VISUALS: AllowedVisual[] = ["stock", "ai_image", "motion_graphic"];

/** key channel yang diisi di sini (Gemini & Claude ada di Pengaturan Nero) */
const KEYS: { name: string; label: string; page?: string; hint?: string }[] = [
  { name: "groq", label: "Groq (cadangan AI, gratis)", page: KEY_PAGES.groq },
  { name: "pexels", label: "Pexels (video stok)", page: KEY_PAGES.pexels },
  { name: "pixabay", label: "Pixabay (video stok)", page: KEY_PAGES.pixabay, hint: "Masuk dulu; key tampil di halaman dokumentasi." },
  { name: "cloudflare_account_id", label: "Cloudflare Account ID (gambar AI)", page: KEY_PAGES.cloudflare },
  {
    name: "cloudflare_token",
    label: "Cloudflare API token (gambar AI)",
    page: KEY_PAGES.cloudflare,
    hint: "Workers AI → Use REST API → Create a Workers AI API Token.",
  },
];

export function ChannelSettingsDialog() {
  const { settings, settingsOpen, setSettingsOpen, loadSettings } = useChannel();
  // muat ulang saat dibuka: status key (mis. Gemini yang baru diisi di Pengaturan Nero) selalu terbaru
  useEffect(() => {
    if (settingsOpen) loadSettings().catch(() => {});
  }, [settingsOpen, loadSettings]);
  return (
    <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Pengaturan Channel Otomatis</DialogTitle>
          <DialogDescription>Key disimpan di komputer ini saja dan tidak pernah ditampilkan lagi.</DialogDescription>
        </DialogHeader>
        {settings && settingsOpen && <SettingsForm settings={settings} />}
      </DialogContent>
    </Dialog>
  );
}

function SettingsForm({ settings }: { settings: Settings }) {
  const { setSettingsOpen, applySettings, refresh, confirm } = useChannel();
  const nero = useNero();
  const c = settings.config;
  const current = settings.genres.find((g) => g.niche === c.channel.niche);
  const initialGenre = current?.id ?? CUSTOM;
  const [genre, setGenre] = useState(initialGenre);
  const [name, setName] = useState(c.channel.name);
  const [niche, setNiche] = useState(c.channel.niche);
  const [audience, setAudience] = useState(c.channel.audience);
  const [voice, setVoice] = useState(c.tts.voice);
  const [rate, setRate] = useState(c.tts.rate);
  const [style, setStyle] = useState(c.video.caption_style);
  const [musicDir, setMusicDir] = useState(c.video.music_dir);
  const [musicDb, setMusicDb] = useState(String(c.video.music_volume_db));
  const [allowed, setAllowed] = useState<AllowedVisual[]>(c.visuals.allowed_types);
  const [enabled, setEnabled] = useState(c.enabled);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  function pickGenre(id: string) {
    setGenre(id);
    const g = settings.genres.find((x) => x.id === id);
    if (g) {
      setNiche(g.niche);
      setAudience(g.audience);
    }
  }

  async function save() {
    const db = Number(musicDb.replace(",", "."));
    if (!niche.trim()) return toast.error("Isi genre/niche channel.");
    if (!musicDb.trim()) return toast.error("Isi volume musik (bawaan -22 dB).");
    if (!Number.isFinite(db) || db > 0 || db < -60) return toast.error("Volume musik antara -60 dan 0 dB.");
    if (allowed.length === 0) return toast.error("Pilih minimal satu jenis visual.");
    const filled = Object.fromEntries(Object.entries(keys).filter(([, v]) => v.trim()));
    setSaving(true);
    try {
      const s = await post<Settings & { ok: boolean }>(`${API}/settings`, {
        // genre dikirim hanya bila diganti, supaya pengaturan lain yang diubah sendiri tidak tertimpa
        ...(genre !== CUSTOM && genre !== initialGenre ? { genre } : {}),
        config: {
          enabled,
          channel: { name: name.trim(), niche: niche.trim(), audience: audience.trim() },
          tts: { voice, rate },
          video: { caption_style: style, music_dir: musicDir.trim(), music_volume_db: db },
          visuals: { allowed_types: allowed },
        },
        keys: filled,
      });
      applySettings(s);
      setSettingsOpen(false);
      refresh();
      toast.success("Pengaturan disimpan");
    } catch {}
    setSaving(false);
  }

  async function clearKey(name: string, label: string) {
    const ok = await confirm({
      title: `Hapus key ${label}?`,
      description: "Key yang tersimpan di komputer ini dihapus. Fitur yang memakainya akan memakai cadangan.",
      action: "Hapus",
      destructive: true,
    });
    if (!ok) return;
    try {
      const s = await post<Settings & { ok: boolean }>(`${API}/settings`, { clear_keys: [name] });
      applySettings(s);
      refresh();
      toast("Key dihapus");
    } catch {}
  }

  const genreOptions = [
    ...settings.genres.map((g) => [g.id, g.name] as const),
    [CUSTOM, "Kustom (tulis sendiri)"] as const,
  ];
  const styleOptions = settings.styles.map((s) => [s.id, s.preset ? s.name : `Gaya saya: ${s.name}`] as const);
  const voiceOptions = Object.entries(settings.voices).map(([id, label]) => [id, label] as const);

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Channel</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_genre">Genre</Label>
            <SimpleSelect id="ch_genre" value={genre} onChange={pickGenre} options={genreOptions} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_name">Nama channel</Label>
            <Input id="ch_name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Opsional" />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="ch_niche">Niche (bahan riset & gaya naskah)</Label>
            <Input
              id="ch_niche"
              value={niche}
              onChange={(e) => {
                setNiche(e.target.value);
                setGenre(CUSTOM);
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="ch_aud">Penonton</Label>
            <Input id="ch_aud" value={audience} onChange={(e) => setAudience(e.target.value)} />
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Suara & video</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_voice">Suara</Label>
            <SimpleSelect id="ch_voice" value={voice} onChange={setVoice} options={voiceOptions} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_rate">Kecepatan bicara</Label>
            <SimpleSelect id="ch_rate" value={rate} onChange={setRate} options={RATES} />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="ch_style">Gaya subtitle (dari pustaka Clip Maker)</Label>
            <SimpleSelect id="ch_style" value={style} onChange={setStyle} options={styleOptions} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_music">Folder musik (opsional)</Label>
            <Input
              id="ch_music"
              value={musicDir}
              onChange={(e) => setMusicDir(e.target.value)}
              placeholder="C:\Musik\bebas-royalti"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_db">Volume musik (dB)</Label>
            <Input id="ch_db" inputMode="decimal" value={musicDb} onChange={(e) => setMusicDb(e.target.value)} />
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Isi folder musik hanya dengan musik bebas royalti milik Anda sendiri. Satu lagu dipilih per hari. Naskah
          otomatis dibuat lebih pendek untuk suara yang lebih pelan supaya video tetap 45–58 detik.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Jenis visual</p>
        <div className="flex flex-wrap gap-4">
          {VISUALS.map((v) => (
            <Label key={v} className="cursor-pointer font-normal">
              <Checkbox
                checked={allowed.includes(v)}
                onCheckedChange={(on) => setAllowed((a) => (on === true ? [...a, v] : a.filter((x) => x !== v)))}
              />
              {VISUAL_LABELS[v]}
            </Label>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Key API</p>
        <p className="text-xs text-muted-foreground">
          Key Gemini (wajib) diatur di{" "}
          <button
            type="button"
            onClick={() => {
              if (Object.values(keys).some((v) => v.trim())) {
                toast.error("Simpan dulu key yang sudah ditempel, lalu buka Pengaturan Nero.");
                return;
              }
              setSettingsOpen(false);
              nero.setSettingsOpen(true);
            }}
            className="font-medium text-foreground underline underline-offset-4"
          >
            Pengaturan Nero
          </button>
          : {settings.keys.gemini ? <span className="text-ok">✓ tersimpan</span> : <span className="text-warn">belum ada</span>}.
        </p>
        <div className="flex flex-col gap-3 rounded-xl border p-3">
          {KEYS.map((k) => {
            const has = settings.keys[k.name] ?? false;
            const fromEnv = settings.keys_env?.[k.name] ?? false;
            return (
              <div key={k.name} className="flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Label htmlFor={`key_${k.name}`} className="min-w-0 flex-1">
                    {k.label}
                  </Label>
                  {k.page && (
                    <a
                      href={k.page}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-xs font-medium underline underline-offset-4"
                    >
                      Buat key <ExternalLinkIcon className="size-3" />
                    </a>
                  )}
                </div>
                <Input
                  id={`key_${k.name}`}
                  type="password"
                  autoComplete="off"
                  value={keys[k.name] ?? ""}
                  onChange={(e) => setKeys((s) => ({ ...s, [k.name]: e.target.value }))}
                  placeholder={
                    fromEnv
                      ? "Diisi lewat environment variable"
                      : has
                        ? "Tersimpan. Kosongkan untuk tetap memakai yang lama."
                        : "Tempel key di sini"
                  }
                  disabled={fromEnv}
                />
                <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span className={has ? "text-ok" : ""}>
                    {fromEnv ? "✓ Dari environment variable" : has ? "✓ Tersimpan" : "Belum ada"}
                  </span>
                  {k.hint && <span>· {k.hint}</span>}
                  {has && !fromEnv && (
                    <button type="button" onClick={() => clearKey(k.name, k.label)} className="underline underline-offset-4">
                      Hapus
                    </button>
                  )}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Otomatis</p>
        <Label className="cursor-pointer font-normal">
          <Checkbox checked={enabled} onCheckedChange={(v) => setEnabled(v === true)} />
          Aktifkan Channel Otomatis
        </Label>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Jadwal harian, upload YouTube, dan kiriman Telegram belum tersedia, jadi sakelar ini belum menjalankan apa
          pun. Uji coba dari halaman ini selalu bisa dipakai.
        </p>
      </section>

      <DialogFooter>
        <Button variant="outline" onClick={() => setSettingsOpen(false)}>
          Batal
        </Button>
        <Button onClick={save} disabled={saving}>
          Simpan
        </Button>
      </DialogFooter>
    </div>
  );
}
