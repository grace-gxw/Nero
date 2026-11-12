/** Bentuk data dari /api/channel/* (lihat features/channel/api.py, runs.py, jobs.py, schemas.py). */

export type StepId = "research" | "script" | "qc" | "seo" | "tts" | "visuals" | "render" | "publish_youtube";
export type StepState = "antri" | "proses" | "selesai" | "dipakai" | "gagal" | "batal" | "lewat" | "belum";
export type RunMode = "plan" | "video" | "media" | "upload";
export type JobStatus = "proses" | "selesai" | "gagal" | "batal";
export type VisualType = "stock" | "ai_image" | "motion_graphic" | "plain";

export interface JobStep {
  id: StepId;
  label: string;
  state: StepState;
  progress?: number;
}

export interface Job {
  id: string;
  day: string;
  mode: RunMode;
  genre: string;
  status: JobStatus;
  error: string;
  /** selesai tetapi perlu perhatian (mis. YouTube menolak video yang diunggah) */
  warning: string;
  code: number | null;
  /** pembatalan diminta, menunggu langkah yang sedang berjalan berhenti */
  cancelling: boolean;
  started: number;
  finished: number;
  /** label langkah yang sedang berjalan */
  stage: string;
  /** 0..1 seluruh run */
  progress: number;
  steps: JobStep[];
  log: string[];
}

export interface RunSummary {
  date: string;
  topic: string;
  title: string;
  format: string;
  qc: { score: number; passed: boolean } | null;
  render: { valid: boolean; duration: number; needs_review: boolean } | null;
  /** ada video lama milik naskah sebelumnya (tidak ditampilkan) */
  stale_video: boolean;
  failed: { step: string; cancelled: boolean; message: string } | null;
  running: boolean;
  updated: number;
  /** URL pratinjau (kosong bila belum ada) */
  video: string;
  thumb: string;
  captions: string;
  /** sudah lolos validasi & disalin ke folder hasil */
  published: boolean;
  /** sudah diunggah ke YouTube */
  uploaded: { video_id: string; url: string; status: UploadStatus; verified: boolean } | null;
}

export type UploadStatus = "" | "diproses" | "siap" | "masalah";

/** isi youtube.json satu run */
export interface YouTubeResult {
  video_id: string;
  url: string;
  studio_url: string;
  privacy: string;
  publish_at: string;
  publish_at_wib: string;
  verified: boolean;
  status: UploadStatus;
  note: string;
  channel_title: string;
  uploaded_at: string;
  checked_at: string;
  playlist_added: boolean;
  playlist_error: string;
  test: boolean;
  source: string;
  title: string;
  channel_id: string;
  /** video ditemukan di channel (upload sebelumnya), bukan diunggah saat itu */
  adopted: string;
  strict_ok: boolean;
}

/** status koneksi YouTube (tanpa token) */
export interface YouTubeStatus {
  configured: boolean;
  connected: boolean;
  /** refresh token diisi lewat environment variable */
  from_env: boolean;
  channel_title: string;
  channel_id: string;
  custom_url: string;
  connected_at: string;
  /** mis. izin kedaluwarsa: perlu dihubungkan ulang */
  problem: string;
  can_upload: boolean;
  can_playlist: boolean;
  flow: { status: "menunggu" | "berhasil" | "gagal" | "batal" | "kedaluwarsa"; error: string; channel: string; url: string } | null;
  usage: { units?: number; insert?: number; search?: number };
}

export interface ReadyItem {
  id: string;
  label: string;
  ok: boolean;
  required: boolean;
}

export interface ChannelState {
  job: Job | null;
  runs: RunSummary[];
  today: string;
  enabled: boolean;
  niche: string;
  ready: ReadyItem[];
  youtube: YouTubeStatus;
}

export interface Segment {
  id: number;
  text: string;
  visual_type: Exclude<VisualType, "plain">;
  visual_query: string;
  image_prompt: string;
  graphic: { template: string; content: string };
  on_screen_text: string;
  label_illustration: boolean;
}

export interface Script {
  topic: string;
  format: string;
  hook: string;
  segments: Segment[];
  cta: string;
  word_count: number;
  claims: { claim: string; source_url: string }[];
}

export interface QCIssue {
  type: "fact" | "policy" | "quality" | "repetition" | "length";
  detail: string;
  fix: string;
}

export interface QC {
  pass: boolean;
  score: number;
  checks: { segment_id: number; statement: string; kind: string; supported: boolean; evidence: string }[];
  issues: QCIssue[];
}

export interface Metadata {
  title: string;
  description: string;
  tags: string[];
  hashtags: string[];
  category_id: string;
  language: string;
  summary: string;
}

export interface SegmentVisual {
  segment_id: number;
  type: VisualType;
  requested: Exclude<VisualType, "plain">;
  file: string;
  start: number;
  end: number;
  frames: number;
  source: string;
  source_id: string;
  url: string;
  author: string;
  query: string;
  prompt: string;
  images: string[];
  fallbacks: string[];
}

export interface Visuals {
  segments: SegmentVisual[];
  uses_ai_images: boolean;
  uses_stock: boolean;
  total_frames: number;
}

export interface RenderInfo {
  width: number;
  height: number;
  fps: number;
  duration: number;
  has_audio: boolean;
  valid: boolean;
  problems: string[];
  warnings: string[];
  needs_review: boolean;
  visual_types: string[];
  music: string;
  encoder: string;
  size_mb: number;
}

/** isi satu run; qc & render di sini versi lengkap (bukan ringkasan) */
export interface RunDetail extends Omit<RunSummary, "qc" | "render"> {
  research: {
    topic: string;
    angle: string;
    why_now: string;
    sources: { title: string; url: string }[];
    candidates: number;
    rejected: string[];
  } | null;
  script: Script | null;
  qc: QC | null;
  metadata: Metadata | null;
  visuals: Visuals | null;
  render: RenderInfo | null;
  voice: { duration: number; voice: string; rate: string; source: string; warnings: string[] } | null;
  steps: JobStep[];
  log: string[];
  youtube: YouTubeResult | null;
  /** video di YouTube dibuat dari naskah sebelumnya (run diulang setelah diunggah) */
  youtube_stale: boolean;
  /** QC lulus, video & judul milik naskah sekarang, belum diunggah */
  upload_ready: boolean;
  /** alasan belum bisa diunggah (kosong bila siap atau tidak ada video) */
  upload_block: string;
  /** alasan itu bisa diperbaiki dengan membuat ulang video run ini */
  upload_rebuild: boolean;
  /** membuat ulang akan riset/menulis naskah baru (pengaturan channel berubah) */
  upload_replan: boolean;
  /** genre khusus run ini (--genre), dipakai lagi saat membuat ulang */
  run_genre: string;
}

export interface Genre {
  id: string;
  name: string;
  niche: string;
  audience: string;
  category_id: string;
}

export interface CaptionStyle {
  id: string;
  name: string;
  preset: boolean;
}

export interface ChannelConfig {
  enabled: boolean;
  channel: { name: string; niche: string; audience: string; tone: string; banned_topics: string[] };
  video: { target_seconds: [number, number]; music_dir: string; music_volume_db: number; caption_style: string };
  tts: { engine: string; voice: string; rate: string; pause_ms: number };
  visuals: { allowed_types: Exclude<VisualType, "plain">[] };
  schedule: { timezone: string; publish_time: string };
  approval: { mode: "manual" | "review_window" | "auto" };
  youtube: {
    api_upload: boolean;
    category_id: string;
    playlist_id: string;
    made_for_kids: boolean;
    contains_synthetic_media: boolean;
  };
  research: { youtube_search_queries: string[] };
}

export interface Settings {
  config: ChannelConfig;
  /** nama kunci -> sudah diisi? (nilai kunci tidak pernah dikirim) */
  keys: Record<string, boolean>;
  /** nama kunci -> diisi lewat environment variable (tidak bisa dihapus dari halaman) */
  keys_env: Record<string, boolean>;
  voices: Record<string, string>;
  formats: string[];
  gemini_model: string;
  genres: Genre[];
  styles: CaptionStyle[];
  steps: Record<StepId, string>;
}
