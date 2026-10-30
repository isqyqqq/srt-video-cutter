export type JobStatus =
  | "uploaded"
  | "planned"
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled";

export interface VideoInfo {
  filename: string;
  size: number;
  duration: number;
  fps: number;
  width: number;
  height: number;
  display_width: number;
  display_height: number;
  rotation: number;
  codec: string;
  has_audio: boolean;
  audio_codec: string | null;
  sample_rate: number;
  channels: number;
  nb_frames: number;
  is_vfr: boolean;
  bit_rate: number;
}

export interface SrtInfo {
  filename: string;
  encoding: string;
  count: number;
  /** 当前参与剪辑的字幕条数（生成切片后 = 切片条数） */
  active_count: number;
}

export type HookType =
  | "价格锚"
  | "价格乌龙"
  | "痛点"
  | "反差"
  | "场景"
  | "故事"
  | "成分"
  | "其他";

export interface HookCandidate {
  id: string;
  type: HookType;
  a: number;
  b: number;
  start: number;
  end: number;
  duration: number;
  text: string;
  reason: string;
}

export interface ClipSegment {
  a: number;
  b: number;
  step: string;
  reason: string;
  start: number;
  end: number;
  duration: number;
  text: string;
}

export interface ClipSrtInfo {
  path: string;
  count: number;
  hook_id: string | null;
  text: string;
  duration: number;
  memory_point: string;
  warnings: string[];
  hook: HookCandidate;
  segments: ClipSegment[];
}

/** 候选片段库中的一项：成片段(s*)或备用段(c*) */
export interface LibraryItem {
  id: string;
  a: number;
  b: number;
  step: string;
  reason: string;
  start: number;
  end: number;
  duration: number;
  text: string;
  origin: "final" | "library";
}

export type GenerationStatus =
  | "idle"
  | "generating_hooks"
  | "hooks_ready"
  | "generating_clip"
  | "clip_ready"
  | "failed";

export interface GenerationInfo {
  status: GenerationStatus;
  error: string | null;
}

export interface Cue {
  index: number;
  source_index: number | null;
  srt_start: number;
  srt_end: number;
  duration: number;
  text: string;
}

export interface PlanItem {
  index: number;
  source_index: number | null;
  text: string;
  srt_start: number;
  srt_end: number;
  start: number;
  end: number;
  frame_start: number;
  frame_end: number;
  duration: number;
  output_name: string;
}

export interface Plan {
  items: PlanItem[];
  warnings: string[];
  total_duration: number;
  total_frames: number;
  fps: number;
  video_duration: number;
  pad_before: number;
  pad_after: number;
}

export type ExportStatus = "idle" | "running" | "ready" | "failed";

/** “导出全部片段（含候选库）”的后台状态 */
export interface ExportInfo {
  status: ExportStatus;
  progress: number;
  message: string;
  error: string | null;
  count: number;
  ready: boolean;
}

export interface FileEntry {
  name: string;
  path: string;
  kind: "merged" | "clip" | "zip" | "clip_srt";
  size: number;
}

export interface JobSnapshot {
  id: string;
  status: JobStatus;
  progress: number;
  message: string;
  error: string | null;
  created_at: string;
  video: VideoInfo;
  srt: SrtInfo;
  cues: Cue[];
  hooks: HookCandidate[];
  hook_warnings: string[];
  selected_hook_id: string | null;
  clip_srt: ClipSrtInfo | null;
  library: LibraryItem[];
  composition: string[];
  export: ExportInfo;
  generation: GenerationInfo;
  plan: Plan | null;
  options: {
    quality?: string;
    mode?: string;
    audio_fade_ms?: number;
  };
  output_dir: string;
  files: FileEntry[];
  logs: string[];
}

export interface Health {
  status: string;
  ffmpeg: string;
  jobs: number;
}

export interface LlmStatus {
  configured: boolean;
  provider: string;
  model: string | null;
  base_url: string | null;
  error: string | null;
}

export interface StartOptions {
  quality: "high" | "standard";
  mode: "precise" | "fast";
  audio_fade_ms: number;
}
