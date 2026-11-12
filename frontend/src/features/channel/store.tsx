"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api } from "@/lib/api";

import { API, jobRunning } from "./api";
import type { ChannelState, Settings } from "./types";

type ConfirmOptions = { title: string; description?: string; action?: string; destructive?: boolean };

interface Store {
  settings: Settings | null;
  loadSettings: () => Promise<void>;
  applySettings: (s: Settings) => void;
  state: ChannelState | null;
  refresh: () => void;
  /** tanggal run yang sedang dilihat di bagian Hasil */
  selected: string | null;
  setSelected: (d: string | null) => void;
  /** naik setiap kali isi run perlu dimuat ulang (mis. run baru selesai) */
  detailKick: number;
  reloadDetail: () => void;
  settingsOpen: boolean;
  setSettingsOpen: (open: boolean) => void;
  confirm: (opt: ConfirmOptions) => Promise<boolean>;
  /** pesan bila halaman gagal dimuat (pengaturan/status tidak terbaca) */
  loadError: string;
  /** tanggal yang videonya sedang dibuat ulang: pemutarnya disembunyikan supaya berkas tidak terkunci */
  holdDay: string | null;
  setHoldDay: (d: string | null) => void;
  /** catat run yang baru dimulai, supaya selesainya tetap diberitahukan walau sangat cepat */
  trackJob: (id: string) => void;
}

const Ctx = createContext<Store | null>(null);

export function useChannel(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useChannel harus dipakai di dalam ChannelProvider");
  return s;
}

export function ChannelProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [state, setState] = useState<ChannelState | null>(null);
  const [kick, setKick] = useState(0);
  const [picked, setSelected] = useState<string | null>(null);
  const [detailKick, setDetailKick] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [confirmState, setConfirmState] = useState<ConfirmOptions | null>(null);
  const confirmResolve = useRef<((ok: boolean) => void) | null>(null);
  const lastJob = useRef<{ id: string; status: string } | null>(null);
  const [loadError, setLoadError] = useState("");
  const [holdDay, setHoldDay] = useState<string | null>(null);
  const settingsRef = useRef<Settings | null>(null);

  const applySettings = useCallback((s: Settings) => {
    settingsRef.current = s;
    setSettings(s);
  }, []);
  const loadSettings = useCallback(() => api<Settings>(`${API}/settings`).then(applySettings), [applySettings]);

  // Status berkala: cepat saat run berjalan, lambat saat diam. Tanpa toast galat (fetch biasa). Pengaturan
  // dimuat di putaran yang sama sampai berhasil (mis. Nero baru menyala atau settings.json sempat rusak).
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const readJSON = async <T,>(url: string): Promise<{ data?: T; error?: string }> => {
      try {
        const r = await fetch(url);
        const j = await r.json().catch(() => ({}));
        return r.ok ? { data: j as T } : { error: (j as { error?: string }).error || `Galat ${r.status}` };
      } catch {
        return { error: "Tidak bisa terhubung ke Nero." };
      }
    };
    const tick = async () => {
      const errors: string[] = [];
      if (!settingsRef.current) {
        const s = await readJSON<Settings>(`${API}/settings`);
        if (s.data && alive) applySettings(s.data);
        if (s.error) errors.push(s.error);
      }
      const st = await readJSON<ChannelState>(`${API}/state`);
      if (!alive) return;
      if (st.data) setState(st.data);
      if (st.error) errors.push(st.error);
      setLoadError(errors[0] ?? "");
      timer = window.setTimeout(tick, jobRunning(st.data?.job) ? 1200 : errors.length ? 3000 : 5000);
    };
    tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [kick, applySettings]);

  // Run selesai/gagal: beri tahu, lalu tampilkan hasilnya.
  useEffect(() => {
    const job = state?.job;
    if (!job) return;
    const prev = lastJob.current;
    lastJob.current = { id: job.id, status: job.status };
    if (!prev || prev.id !== job.id || prev.status !== "proses" || job.status === "proses") return;
    const what =
      job.mode === "upload" ? "Unggah ke YouTube" : job.mode === "notify" ? "Kirim ke Telegram" : "Uji coba";
    if (job.status === "selesai" && job.warning) toast.warning(job.warning);
    else if (job.status === "selesai") toast.success(`${what} selesai`);
    else if (job.status === "batal") toast(`${what} dibatalkan`);
    else toast.error(job.error || `${what} gagal`);
    setHoldDay(null);
    setSelected(job.day);
    setDetailKick((k) => k + 1);
  }, [state?.job]);

  const trackJob = useCallback((id: string) => {
    lastJob.current = { id, status: "proses" };
  }, []);

  // belum memilih: tampilkan run terbaru
  const selected = picked ?? state?.runs[0]?.date ?? null;

  const refresh = useCallback(() => setKick((k) => k + 1), []);
  const reloadDetail = useCallback(() => setDetailKick((k) => k + 1), []);

  const confirm = useCallback((opt: ConfirmOptions) => {
    setConfirmState(opt);
    return new Promise<boolean>((resolve) => {
      confirmResolve.current = resolve;
    });
  }, []);
  const closeConfirm = (ok: boolean) => {
    confirmResolve.current?.(ok);
    confirmResolve.current = null;
    setConfirmState(null);
  };

  return (
    <Ctx.Provider
      value={{
        settings,
        loadSettings,
        applySettings,
        state,
        refresh,
        selected,
        setSelected,
        detailKick,
        reloadDetail,
        settingsOpen,
        setSettingsOpen,
        confirm,
        loadError,
        holdDay,
        setHoldDay,
        trackJob,
      }}
    >
      {children}
      <AlertDialog open={confirmState !== null} onOpenChange={(open) => !open && closeConfirm(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmState?.title}</AlertDialogTitle>
            {confirmState?.description && <AlertDialogDescription>{confirmState.description}</AlertDialogDescription>}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction
              variant={confirmState?.destructive ? "destructive" : "default"}
              onClick={() => closeConfirm(true)}
            >
              {confirmState?.action ?? "Lanjut"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Ctx.Provider>
  );
}
