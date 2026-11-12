"use client";

import { RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

import { ChannelHeader } from "./channel-header";
import { ReadyCard } from "./ready-card";
import { ResultSection } from "./result-view";
import { RunCard } from "./run-card";
import { ChannelSettingsDialog } from "./settings-dialog";
import { ChannelProvider, useChannel } from "./store";

/** Halaman fitur Channel Otomatis (/channel). */
export function ChannelApp() {
  return (
    <ChannelProvider>
      <ChannelHeader />
      <Main />
      <ChannelSettingsDialog />
    </ChannelProvider>
  );
}

function Main() {
  const { settings, state, loadError, refresh } = useChannel();
  if (!settings || !state) {
    return (
      <div className="flex flex-col items-center gap-3 px-4 py-24 text-center">
        <p className="text-muted-foreground">{loadError ? "Channel Otomatis belum bisa dimuat." : "Memuat Channel Otomatis..."}</p>
        {loadError && (
          <>
            <p className="max-w-xl text-sm break-words text-destructive">{loadError}</p>
            <Button variant="outline" size="sm" onClick={refresh}>
              <RotateCwIcon /> Coba lagi
            </Button>
          </>
        )}
      </div>
    );
  }
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
      <div className="grid items-start gap-5 lg:grid-cols-[1.35fr_1fr]">
        <RunCard />
        <ReadyCard />
      </div>
      <ResultSection />
    </main>
  );
}
