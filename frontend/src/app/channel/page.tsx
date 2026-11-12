import type { Metadata } from "next";

import { ChannelApp } from "@/features/channel/channel-app";

export const metadata: Metadata = { title: "Channel Otomatis" };

export default function Page() {
  return <ChannelApp />;
}
