"use client";

import { useNero } from "@/components/nero/store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import { useChannel } from "./store";

/** Kartu "2. Kesiapan": apa yang sudah diisi, wajib vs opsional. */
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
      </CardContent>
    </Card>
  );
}
