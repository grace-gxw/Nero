"use client";

import { ExternalLinkIcon, Loader2Icon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
import { KEY_PAGES, RATES, VISUAL_LABELS, WEEKDAYS } from "./options";
import { useChannel } from "./store";
import type { ChannelConfig, Settings, WeekDay, YouTubeStatus } from "./types";

const CUSTOM = "kustom";
type AllowedVisual = ChannelConfig["visuals"]["allowed_types"][number];
const VISUALS: AllowedVisual[] = ["stock", "ai_image", "motion_graphic"];

/** key channel yang diisi di sini (Gemini & Claude ada di Pengaturan Nero) */
const KEYS: KeyDef[] = [
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
const YT_KEYS: KeyDef[] = [
  {
    name: "yt_client_id",
    label: "Client ID OAuth (Desktop app)",
    page: "https://console.cloud.google.com/auth/clients",
    link: "Buka Google Cloud",
  },
  {
    name: "yt_client_secret",
    label: "Client secret OAuth",
    hint: "Hanya tampil sekali saat client dibuat; bila hilang, tambah secret baru.",
  },
];
const TIME_RE = /^([01]?\d|2[0-3])[:.][0-5]\d$/;
const TG_KEYS: KeyDef[] = [
  {
    name: "telegram_token",
    label: "Token bot Telegram",
    page: KEY_PAGES.telegram,
    link: "Buka @BotFather",
    hint: "Di @BotFather kirim /newbot, lalu salin tokennya.",
  },
  { name: "telegram_chat_id", label: "Chat ID", hint: "Isi lewat Cari chat ID di bawah." },
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
  const { setSettingsOpen, applySettings, refresh, reloadDetail, reloadAnalytics, confirm } = useChannel();
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
  const [apiUpload, setApiUpload] = useState(c.youtube.api_upload);
  const [approval, setApproval] = useState<ChannelConfig["approval"]["mode"]>(c.approval.mode);
  const [publishTime, setPublishTime] = useState(c.schedule.publish_time);
  const [runTime, setRunTime] = useState(c.schedule.run_time);
  const [playlist, setPlaylist] = useState(c.youtube.playlist_id);
  const [kids, setKids] = useState(c.youtube.made_for_kids);
  const [ytQueries, setYtQueries] = useState(c.research.youtube_search_queries.join(", "));
  const [notifyTg, setNotifyTg] = useState(c.notify.telegram);
  const [weeklyDay, setWeeklyDay] = useState<WeekDay>(c.notify.weekly_report_day);
  const [shareAi, setShareAi] = useState(c.analyst.share_with_ai);
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
    if (!TIME_RE.test(publishTime.trim())) return toast.error("Jam tayang berbentuk JJ:MM, mis. 18:00.");
    if (!TIME_RE.test(runTime.trim())) return toast.error("Jam run harian berbentuk JJ:MM, mis. 05:17.");
    const queries = ytQueries
      .split(",")
      .map((q) => q.trim())
      .filter(Boolean);
    if (queries.length > 3) return toast.error("Kata kunci tren YouTube paling banyak 3.");
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
          schedule: { publish_time: publishTime.trim(), run_time: runTime.trim() },
          approval: { mode: approval },
          youtube: { api_upload: apiUpload, playlist_id: playlist.trim(), made_for_kids: kids },
          research: { youtube_search_queries: queries },
          notify: { telegram: notifyTg, weekly_report_day: weeklyDay },
          analyst: { share_with_ai: shareAi },
        },
        keys: filled,
      });
      applySettings(s);
      setSettingsOpen(false);
      refresh();
      reloadDetail();          // kesiapan unggah bergantung pada pengaturan
      reloadAnalytics();
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
      reloadDetail();
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
          {KEYS.map((k) => (
            <KeyRow
              key={k.name}
              k={k}
              settings={settings}
              value={keys[k.name] ?? ""}
              onChange={(v) => setKeys((s) => ({ ...s, [k.name]: v }))}
              onClear={() => clearKey(k.name, k.label)}
            />
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">YouTube</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Butuh project Google Cloud dengan YouTube Data API v3 dan client OAuth jenis &quot;Desktop app&quot;
          (langkahnya di PANDUAN.md). Status aplikasi di Google harus <b>In production</b>; bila masih Testing, izin
          habis tiap 7 hari.
        </p>
        <div className="flex flex-col gap-3 rounded-xl border p-3">
          {YT_KEYS.map((k) => (
            <KeyRow
              key={k.name}
              k={k}
              settings={settings}
              value={keys[k.name] ?? ""}
              onChange={(v) => setKeys((s) => ({ ...s, [k.name]: v }))}
              onClear={() => clearKey(k.name, k.label)}
            />
          ))}
        </div>
        <YouTubeConnect unsaved={YT_KEYS.some((k) => (keys[k.name] ?? "").trim())} />
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="ch_approval">Mode tayang (upload otomatis)</Label>
            <SimpleSelect id="ch_approval" value={approval} onChange={setApproval} options={APPROVAL_OPTIONS} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_time">Jam tayang (WIB)</Label>
            <Input id="ch_time" value={publishTime} onChange={(e) => setPublishTime(e.target.value)} placeholder="18:00" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ch_playlist">ID playlist (opsional)</Label>
            <Input id="ch_playlist" value={playlist} onChange={(e) => setPlaylist(e.target.value)} placeholder="PL..." />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="ch_ytq">Kata kunci tren YouTube (opsional, maks. 3, pisahkan dengan koma)</Label>
            <Input
              id="ch_ytq"
              value={ytQueries}
              onChange={(e) => setYtQueries(e.target.value)}
              placeholder="fakta unik, sains"
            />
          </div>
        </div>
        <Label className="cursor-pointer font-normal">
          <Checkbox checked={kids} onCheckedChange={(v) => setKids(v === true)} />
          Video untuk anak-anak (Made for Kids)
        </Label>
        <Label className="cursor-pointer font-normal">
          <Checkbox checked={apiUpload} onCheckedChange={(v) => setApiUpload(v === true)} />
          Upload otomatis lewat API saat Channel Otomatis aktif
        </Label>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Mati = run harian hanya menyiapkan video dan paket unggah; Anda yang mengunggah. Tombol &quot;Unggah uji
          (private)&quot; di hasil run tetap bisa dipakai. Judul populer dari kata kunci tren hanya jadi petunjuk minat
          penonton untuk riset (bukan sumber fakta) dan tidak disimpan.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Telegram</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Paket harian (ringkasan, video, caption, naskah) dan peringatan bila run gagal dikirim ke ponsel Anda.
          Buat bot di @BotFather, buka bot itu lalu tekan <b>Start</b>, kemudian tekan <b>Cari chat ID</b>.
        </p>
        <div className="flex flex-col gap-3 rounded-xl border p-3">
          {TG_KEYS.map((k) => (
            <KeyRow
              key={k.name}
              k={k}
              settings={settings}
              value={keys[k.name] ?? ""}
              onChange={(v) => setKeys((s) => ({ ...s, [k.name]: v }))}
              onClear={() => clearKey(k.name, k.label)}
            />
          ))}
        </div>
        <TelegramTools
          settings={settings}
          tokenUnsaved={Boolean((keys.telegram_token ?? "").trim())}
          chatUnsaved={Boolean((keys.telegram_chat_id ?? "").trim())}
          onPick={(id) => setKeys((s) => ({ ...s, telegram_chat_id: id }))}
        />
        <Label className="cursor-pointer font-normal">
          <Checkbox checked={notifyTg} onCheckedChange={(v) => setNotifyTg(v === true)} />
          Kirim paket harian, laporan mingguan, & peringatan gagal ke Telegram (saat Channel Otomatis aktif)
        </Label>
      </section>

      <section className="flex flex-col gap-3">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Analitik</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Nero membaca performa video buatannya di channel yang terhubung (YouTube Data API &amp; YouTube Analytics
          API; aktifkan keduanya di Google Cloud). Angkanya disimpan di komputer ini, diperbarui tiap analisis, dan
          dihapus bila YouTube diputuskan.
        </p>
        <div className="flex flex-col gap-1.5 sm:max-w-60">
          <Label htmlFor="ch_weekly">Hari laporan mingguan</Label>
          <SimpleSelect id="ch_weekly" value={weeklyDay} onChange={setWeeklyDay} options={WEEKDAYS} />
        </div>
        <Label className="cursor-pointer items-start font-normal">
          <Checkbox className="mt-0.5" checked={shareAi} onCheckedChange={(v) => setShareAi(v === true)} />
          <span>
            Izinkan statistik video dikirim ke penyedia AI (urutan di pengaturan AI: Gemini, Groq, atau AI lokal) untuk
            menyusun pelajaran performa
          </span>
        </Label>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Yang dikirim: nomor urut, judul/topik/format/hook buatan Nero, dan angka YouTube 7 hari pertama per video,
          tanpa ID video atau channel. Paket gratis Gemini memakai isi prompt untuk mengembangkan produk Google dan
          bisa dibaca peninjau manusia. Mati = performa tetap tampil di halaman dan laporan mingguan, tanpa pelajaran
          otomatis.
        </p>
      </section>

      <section className="flex flex-col gap-2">
        <p className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">Otomatis</p>
        <div className="flex flex-col gap-1.5 sm:max-w-60">
          <Label htmlFor="ch_run">Jam run harian (WIB)</Label>
          <Input id="ch_run" value={runTime} onChange={(e) => setRunTime(e.target.value)} placeholder="05:17" />
        </div>
        <Label className="cursor-pointer font-normal">
          <Checkbox checked={enabled} onCheckedChange={(v) => setEnabled(v === true)} />
          Aktifkan Channel Otomatis
        </Label>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Saat aktif, Nero menjalankan run harian sungguhan setiap hari pada jam run: membuat video, mengunggah bila
          upload otomatis menyala, mengirim paket ke Telegram, dan laporan mingguan. Jadwal berjalan di dalam Nero,
          jadi laptop dan Nero harus menyala; bila Nero baru dibuka setelah jam run, run hari itu langsung dikejar.
          Bila jam run hari ini sudah lewat, run dimulai begitu Anda menyimpan. Uji coba, unggah uji, kirim ke
          Telegram, dan analisis dari halaman ini selalu bisa dipakai.
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

const APPROVAL_OPTIONS = [
  ["manual", "Manual: private tanpa jadwal, saya yang menayangkan"],
  ["review_window", "Jendela tinjau: private, tayang otomatis di jam tayang"],
  ["auto", "Otomatis: langsung publik (tidak disarankan)"],
] as const;

type KeyDef = { name: string; label: string; page?: string; hint?: string; link?: string };

function KeyRow({
  k,
  settings,
  value,
  onChange,
  onClear,
}: {
  k: KeyDef;
  settings: Settings;
  value: string;
  onChange: (v: string) => void;
  onClear: () => void;
}) {
  const has = settings.keys[k.name] ?? false;
  const fromEnv = settings.keys_env?.[k.name] ?? false;
  return (
    <div className="flex flex-col gap-1.5">
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
            {k.link ?? "Buat key"} <ExternalLinkIcon className="size-3" />
          </a>
        )}
      </div>
      <Input
        id={`key_${k.name}`}
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={
          fromEnv ? "Diisi lewat environment variable" : has ? "Tersimpan. Kosongkan untuk tetap memakai yang lama." : "Tempel di sini"
        }
        disabled={fromEnv}
      />
      <p className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span className={has ? "text-ok" : ""}>{fromEnv ? "✓ Dari environment variable" : has ? "✓ Tersimpan" : "Belum ada"}</span>
        {k.hint && <span>· {k.hint}</span>}
        {has && !fromEnv && (
          <button type="button" onClick={onClear} className="underline underline-offset-4">
            Hapus
          </button>
        )}
      </p>
    </div>
  );
}

async function readYouTube(): Promise<YouTubeStatus | null> {
  try {
    const r = await fetch(`${API}/youtube`);
    return r.ok ? ((await r.json()) as YouTubeStatus) : null;
  } catch {
    return null;
  }
}

/** Status koneksi + tombol Hubungkan / Putuskan. Menghubungkan membuka halaman Google di tab baru; Nero menunggu
 *  jawabannya di alamat 127.0.0.1 sementara. */
function YouTubeConnect({ unsaved }: { unsaved: boolean }) {
  const { state, refresh, reloadDetail, reloadAnalytics, confirm } = useChannel();
  const [own, setOwn] = useState<YouTubeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const yt = own ?? state?.youtube ?? null;
  const flow = yt?.flow ?? null;
  const waiting = flow?.status === "menunggu";
  const prevFlow = useRef(flow?.status);

  useEffect(() => {
    if (!waiting) return;
    const t = window.setInterval(() => {
      readYouTube().then((s) => s && setOwn(s));
    }, 1500);
    return () => window.clearInterval(t);
  }, [waiting]);

  useEffect(() => {
    const now = flow?.status;
    if (prevFlow.current === "menunggu" && now && now !== "menunggu") {
      if (now === "berhasil") toast.success(`YouTube terhubung: ${flow?.channel}`);
      else if (now !== "batal") toast.error(flow?.error || "YouTube belum terhubung");
      refresh();
      reloadDetail();
      reloadAnalytics();
    }
    prevFlow.current = now;
  }, [flow?.status, flow?.channel, flow?.error, refresh, reloadDetail, reloadAnalytics]);

  async function connect() {
    if (unsaved) {
      toast.error("Simpan dulu Client ID & secret, lalu buka Pengaturan lagi untuk menghubungkan.");
      return;
    }
    setBusy(true);
    try {
      const r = await post<{ ok: boolean; url: string; youtube: YouTubeStatus }>(`${API}/youtube/connect`);
      setOwn(r.youtube);
      window.open(r.url, "_blank", "noopener,noreferrer");
    } catch {}
    setBusy(false);
  }

  async function stop() {
    try {
      const r = await post<{ ok: boolean; youtube: YouTubeStatus }>(`${API}/youtube/connect/cancel`);
      setOwn(r.youtube);
    } catch {}
  }

  async function disconnect() {
    const ok = await confirm({
      title: "Putuskan YouTube?",
      description:
        "Izin Nero di akun Google dicabut dan token dihapus dari komputer ini. Data performa yang disimpan Nero " +
        "(angka & pelajaran) ikut dihapus. Video yang sudah diunggah tidak berubah.",
      action: "Putuskan",
      destructive: true,
    });
    if (!ok) return;
    try {
      const r = await post<{ ok: boolean; revoked: boolean; youtube: YouTubeStatus }>(`${API}/youtube/disconnect`);
      setOwn(r.youtube);
      toast(r.revoked ? "YouTube diputuskan" : "Token dihapus. Cek juga myaccount.google.com/permissions.");
      refresh();
      reloadDetail();
      reloadAnalytics();
    } catch {}
  }

  if (!yt) return null;
  const used = yt.usage ?? {};
  return (
    <div className="flex flex-col gap-2 rounded-xl border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 text-sm">
          {yt.connected ? (
            <>
              <span className={yt.problem ? "text-warn" : "text-ok"}>{yt.problem ? "Perlu dihubungkan ulang" : "✓ Terhubung"}</span>
              {yt.channel_title && (
                <>
                  {" "}
                  ke <b>{yt.channel_title}</b>
                  {yt.custom_url && <span className="text-muted-foreground"> ({yt.custom_url})</span>}
                </>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">Belum terhubung</span>
          )}
        </p>
        {waiting ? (
          <Button size="sm" variant="outline" onClick={stop}>
            Batal
          </Button>
        ) : (
          <Button size="sm" variant={yt.connected && !yt.problem ? "outline" : "default"} onClick={connect} disabled={busy || !yt.configured || yt.from_env}>
            {busy && <Loader2Icon className="animate-spin" />}
            {yt.connected ? "Hubungkan ulang" : "Hubungkan YouTube"}
          </Button>
        )}
        {yt.connected && !yt.from_env && !waiting && (
          <Button size="sm" variant="ghost" onClick={disconnect}>
            Putuskan
          </Button>
        )}
      </div>
      {waiting && flow?.url && (
        <p className="flex items-start gap-2 text-xs text-info">
          <Loader2Icon className="mt-0.5 size-3.5 flex-none animate-spin" />
          <span>
            Menunggu persetujuan di halaman Google (tab baru). Pilih akun/channel tujuan, lalu izinkan semua akses.{" "}
            <a href={flow.url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
              Buka lagi halaman Google
            </a>
          </span>
        </p>
      )}
      {yt.problem && <p className="text-xs text-warn">{yt.problem}</p>}
      {!yt.configured && <p className="text-xs text-muted-foreground">Isi dan simpan Client ID & secret di atas dulu.</p>}
      {yt.from_env && (
        <p className="text-xs text-muted-foreground">Token diatur lewat environment variable YT_REFRESH_TOKEN.</p>
      )}
      {yt.connected && !yt.can_playlist && (
        <p className="text-[11px] text-muted-foreground">
          Untuk menambah video ke playlist, isi ID playlist, simpan, lalu tekan Hubungkan ulang (perlu izin tambahan).
        </p>
      )}
      {yt.connected && (
        <p className="text-[11px] text-muted-foreground">
          Kuota hari ini: {used.insert ?? 0}/100 upload · {used.search ?? 0}/3 pencarian · {used.units ?? 0} unit lain
          (pulih 14.00–15.00 WIB).
        </p>
      )}
    </div>
  );
}

type ChatInfo = { id: string; type: string; name: string };

/** Cari chat ID (getUpdates) dan kirim pesan uji. Keduanya memakai token/chat ID yang sudah disimpan. */
function TelegramTools({
  settings,
  tokenUnsaved,
  chatUnsaved,
  onPick,
}: {
  settings: Settings;
  tokenUnsaved: boolean;
  chatUnsaved: boolean;
  onPick: (id: string) => void;
}) {
  const [chats, setChats] = useState<ChatInfo[] | null>(null);
  const [bot, setBot] = useState("");
  const [busy, setBusy] = useState(false);
  const hasToken = settings.keys.telegram_token ?? false;
  const hasChat = settings.keys.telegram_chat_id ?? false;

  async function find() {
    if (tokenUnsaved || !hasToken) {
      toast.error("Simpan dulu token bot, lalu buka Pengaturan lagi.");
      return;
    }
    setBusy(true);
    try {
      const r = await post<{ ok: boolean; bot: string; chats: ChatInfo[] }>(`${API}/telegram/chats`);
      setBot(r.bot);
      setChats(r.chats);
    } catch {}
    setBusy(false);
  }

  async function test() {
    if (tokenUnsaved || chatUnsaved || !hasToken || !hasChat) {
      toast.error("Simpan dulu token bot dan chat ID, lalu buka Pengaturan lagi.");
      return;
    }
    setBusy(true);
    try {
      await post(`${API}/telegram/test`);
      toast.success("Pesan uji terkirim. Cek Telegram Anda.");
    } catch {}
    setBusy(false);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={find} disabled={busy}>
          {busy && <Loader2Icon className="animate-spin" />} Cari chat ID
        </Button>
        <Button size="sm" variant="outline" onClick={test} disabled={busy}>
          Kirim pesan uji
        </Button>
      </div>
      {chats !== null &&
        (chats.length === 0 ? (
          <p className="text-xs text-warn">
            Belum ada chat{bot ? ` untuk @${bot}` : ""}. Buka bot di Telegram, tekan Start (atau kirim pesan apa saja),
            lalu tekan Cari chat ID lagi.
          </p>
        ) : (
          <ul className="flex flex-col gap-1 rounded-lg border p-2 text-xs">
            {chats.map((c) => (
              <li key={c.id} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">
                  {c.name} <span className="text-muted-foreground">({c.type}, {c.id})</span>
                </span>
                <Button size="xs" variant="ghost" onClick={() => (onPick(c.id), toast("Chat ID diisi. Tekan Simpan."))}>
                  Pakai
                </Button>
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}
