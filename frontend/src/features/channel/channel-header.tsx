"use client";

import { FolderOpenIcon, SettingsIcon, TvMinimalPlayIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { openFolder } from "@/lib/api";
import { cn } from "@/lib/utils";

import { OUT_DIR } from "./api";
import { useChannel } from "./store";

export function ChannelHeader() {
  const { state, setSettingsOpen } = useChannel();
  const enabled = state?.enabled ?? false;
  return (
    <header className="sticky top-0 z-10 border-b bg-background/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:px-6">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-foreground text-background">
          <TvMinimalPlayIcon className="size-4" />
        </span>
        <div className="min-w-0">
          <h1 className="text-lg leading-tight font-extrabold tracking-tight">Channel Otomatis</h1>
          <p className="hidden truncate text-xs text-muted-foreground sm:block">
            Video pendek harian: riset, naskah, cek fakta, suara, visual, render
          </p>
        </div>
        <Badge
          variant="outline"
          className={cn(enabled ? "text-ok" : "text-muted-foreground")}
          title={enabled ? "Channel Otomatis aktif" : "Nonaktif: hanya uji coba dari halaman ini"}
        >
          {enabled ? "Aktif" : "Nonaktif"}
        </Badge>
        <div className="flex-1" />
        <Button variant="outline" aria-label="Folder hasil Channel Otomatis" onClick={() => openFolder(OUT_DIR)}>
          <FolderOpenIcon />
          <span className="hidden sm:inline">Folder hasil</span>
        </Button>
        <Button variant="outline" aria-label="Pengaturan Channel Otomatis" onClick={() => setSettingsOpen(true)}>
          <SettingsIcon />
          <span className="hidden sm:inline">Pengaturan channel</span>
        </Button>
      </div>
    </header>
  );
}
