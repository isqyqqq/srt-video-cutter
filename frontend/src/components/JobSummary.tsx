import type { JobSnapshot } from "../types";
import { formatDuration, formatTc } from "../utils";
import { AlertIcon, CaptionsIcon, FilmIcon, ClockIcon } from "./Icons";

export default function JobSummary({ job }: { job: JobSnapshot }) {
  const video = job.video;
  return (
    <section className="grid gap-4 md:grid-cols-2">
      <div className="card">
        <div className="card-body flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-600">
            <FilmIcon size={22} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-slate-900">
              {video.filename}
            </p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              <span className="flex items-center gap-1">
                <ClockIcon size={13} /> {formatDuration(video.duration)}
              </span>
              <span>
                {video.display_width} × {video.display_height}
              </span>
              <span>{video.fps.toFixed(2)} fps</span>
              <span>{video.codec.toUpperCase()}</span>
              <span>{video.has_audio ? `音频 ${video.audio_codec ?? ""}` : "无音轨"}</span>
            </div>
            {video.is_vfr ? (
              <p className="mt-2 flex items-center gap-1 text-xs text-amber-600">
                <AlertIcon size={13} /> 可变帧率视频，切点可能有 1 帧误差
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-body flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-sky-50 text-sky-600">
            <CaptionsIcon size={22} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-slate-900">
              {job.srt.filename}
            </p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              <span>{job.srt.count} 条完整字幕</span>
              <span>编码 {job.srt.encoding.toUpperCase()}</span>
              {job.clip_srt ? (
                <span className="text-emerald-600">
                  切片 {job.clip_srt.count} 条 · {job.clip_srt.duration.toFixed(1)}s
                </span>
              ) : null}
              {job.plan ? (
                <span className="mono">输出 {formatTc(job.plan.total_duration)}</span>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
