import type { HookCandidate, JobSnapshot, LlmStatus } from "../types";
import { downloadUrl } from "../api";
import { formatDuration, formatTc } from "../utils";
import {
  AlertIcon,
  CaptionsIcon,
  CheckIcon,
  DownloadIcon,
  LoaderIcon,
  RefreshIcon,
  ScissorsIcon,
} from "./Icons";

interface Props {
  job: JobSnapshot;
  busy: boolean;
  activeHookId: string | null;
  llm: LlmStatus | null;
  onActiveHookChange: (hookId: string) => void;
  onGenerateHooks: (force: boolean) => void;
  onGenerateClip: (hookId: string) => void;
  onResetClip: () => void;
  onUseOriginal: () => void;
}

const TYPE_STYLES: Record<string, string> = {
  价格锚: "bg-rose-50 text-rose-600",
  价格乌龙: "bg-rose-50 text-rose-600",
  痛点: "bg-amber-50 text-amber-600",
  反差: "bg-indigo-50 text-indigo-600",
  场景: "bg-sky-50 text-sky-600",
  故事: "bg-emerald-50 text-emerald-600",
  成分: "bg-slate-100 text-slate-600",
  其他: "bg-slate-100 text-slate-600",
};

function HookRow({
  hook,
  selected,
  disabled,
  onSelect,
}: {
  hook: HookCandidate;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className={`w-full rounded-xl border p-3 text-left transition ${
        selected
          ? "border-indigo-300 bg-indigo-50/60 ring-1 ring-indigo-200"
          : "border-slate-200 bg-white hover:border-indigo-200 hover:bg-slate-50"
      } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`h-4 w-4 shrink-0 rounded-full border-2 ${
            selected ? "border-indigo-500 bg-indigo-500" : "border-slate-300"
          }`}
        >
          {selected ? (
            <CheckIcon size={10} className="mx-auto text-white" />
          ) : null}
        </span>
        <span
          className={`badge ${TYPE_STYLES[hook.type] ?? TYPE_STYLES["其他"]}`}
        >
          {hook.type}
        </span>
        <span className="mono text-[11px] text-slate-400">
          {formatTc(hook.start).slice(3)} → {formatTc(hook.end).slice(3)} ·{" "}
          {hook.duration.toFixed(1)}s
        </span>
      </div>
      <p className="mt-2 text-sm font-medium leading-relaxed text-slate-800">
        “{hook.text}”
      </p>
      {hook.reason ? (
        <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
          {hook.reason}
        </p>
      ) : null}
    </button>
  );
}

export default function HookSelector({
  job,
  busy,
  activeHookId,
  llm,
  onActiveHookChange,
  onGenerateHooks,
  onGenerateClip,
  onResetClip,
  onUseOriginal,
}: Props) {
  const hooks = job.hooks ?? [];
  const clip = job.clip_srt;
  const status = job.generation?.status ?? "idle";
  const generating = status === "generating_hooks" || status === "generating_clip";
  const disabled = busy || generating || job.status === "processing";

  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <ScissorsIcon size={17} className="text-indigo-500" />
          <div>
            <h2 className="text-base font-semibold text-slate-900">
              AI 开头钩子
            </h2>
            <p className="mt-0.5 text-[11px] text-slate-500">
              先选一个开头，再按「五步结构」生成 55~60 秒切片 SRT
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {llm ? (
            <span
              className={`badge ${
                llm.configured
                  ? "bg-emerald-50 text-emerald-600"
                  : "bg-amber-50 text-amber-600"
              }`}
            >
              {llm.configured
                ? `${llm.provider} · ${llm.model ?? "默认模型"}`
                : "模型未配置"}
            </span>
          ) : null}
          {clip ? (
            <span className="badge bg-emerald-50 text-emerald-600">
              切片已生成
            </span>
          ) : null}
        </div>
      </div>

      <div className="card-body space-y-4">
        {job.generation?.error ? (
          <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-relaxed text-rose-700">
            <AlertIcon size={15} className="mt-0.5 shrink-0" />
            <p className="min-w-0 flex-1">{job.generation.error}</p>
          </div>
        ) : null}

        {generating ? (
          <div className="flex items-center gap-3 rounded-xl border border-indigo-100 bg-indigo-50/60 px-4 py-3 text-sm text-indigo-700">
            <LoaderIcon size={18} className="animate-spin" />
            <div>
              <p className="font-medium">
                {status === "generating_hooks"
                  ? "正在生成开头钩子…"
                  : "正在生成切片 SRT…"}
              </p>
              <p className="mt-0.5 text-[11px] text-indigo-500">
                模型思考通常需要 30~120 秒，请勿关闭页面。
              </p>
            </div>
          </div>
        ) : null}

        {clip ? (
          <div className="space-y-3 rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-emerald-800">
                  切片 SRT：{clip.count} 条 · 约 {clip.duration.toFixed(1)} 秒
                </p>
                <p className="mt-0.5 text-[11px] text-emerald-700">
                  核心记忆点：{clip.memory_point}
                </p>
              </div>
              <div className="flex gap-2">
                <a
                  className="btn btn-secondary !py-2"
                  href={downloadUrl(job.id, "clip.srt")}
                >
                  <DownloadIcon size={15} /> 下载 clip.srt
                </a>
                <button
                  type="button"
                  className="btn btn-ghost !py-2"
                  disabled={disabled}
                  onClick={onResetClip}
                >
                  <RefreshIcon size={15} /> 换一个开头
                </button>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {clip.segments.map((segment, index) => (
                <span
                  key={`${segment.a}-${segment.b}-${index}`}
                  className="badge bg-white text-slate-600 shadow-sm"
                >
                  {segment.step} {segment.duration.toFixed(1)}s
                </span>
              ))}
            </div>
            {clip.warnings.length ? (
              <ul className="space-y-1 text-[11px] leading-relaxed text-amber-700">
                {clip.warnings.map((warning) => (
                  <li key={warning}>· {warning}</li>
                ))}
              </ul>
            ) : null}
            <p className="text-[11px] text-emerald-700">
              下方切点表已经按切片 SRT 排好，可直接开始剪辑；时间戳全部沿用原视频。
            </p>
          </div>
        ) : null}

        {!clip && hooks.length ? (
          <>
            <div className="space-y-2">
              {hooks.map((hook) => (
                <HookRow
                  key={hook.id}
                  hook={hook}
                  selected={hook.id === activeHookId}
                  disabled={disabled}
                  onSelect={() => onActiveHookChange(hook.id)}
                />
              ))}
            </div>
            {job.hook_warnings?.length ? (
              <ul className="space-y-1 text-[11px] text-amber-600">
                {job.hook_warnings.map((warning) => (
                  <li key={warning}>· {warning}</li>
                ))}
              </ul>
            ) : null}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-[11px] text-slate-400">
                默认选第 1 个；生成后仍可在切点表里微调前后余量。
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={disabled}
                  onClick={() => onGenerateHooks(true)}
                >
                  <RefreshIcon size={15} /> 重新生成钩子
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={disabled || !activeHookId}
                  onClick={() => activeHookId && onGenerateClip(activeHookId)}
                >
                  <CaptionsIcon size={16} /> 用这个开头生成切片 SRT
                </button>
              </div>
            </div>
          </>
        ) : null}

        {!clip && !hooks.length && !generating ? (
          job.plan ? (
            // 手动重排模式：已按上传的 SRT 生成切点，无需 AI
            <div className="space-y-3 rounded-xl border border-sky-200 bg-sky-50/50 p-4">
              <p className="text-sm font-semibold text-sky-800">
                手动重排模式：已按上传的 SRT 生成 {job.plan.items.length} 个片段
              </p>
              <p className="text-[11px] leading-relaxed text-sky-700">
                直接按 SRT 时间戳截取拼接，未调用 AI。下方切点表可直接开始剪辑；
                也可以改用 AI 重新挑开头钩子。
              </p>
              <button
                type="button"
                className="btn btn-ghost !py-2"
                disabled={disabled}
                onClick={() => onGenerateHooks(false)}
              >
                <ScissorsIcon size={16} /> 改用 AI 生成开头钩子
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm leading-relaxed text-slate-500">
                还没有生成开头钩子。模型会从完整字幕里挑 3~5 个可用开头，
                你选一个之后再重排成切片。
              </p>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={disabled}
                  onClick={onUseOriginal}
                >
                  跳过 AI，直接用原 SRT
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={disabled}
                  onClick={() => onGenerateHooks(false)}
                >
                  <ScissorsIcon size={16} /> 生成开头钩子
                </button>
              </div>
            </div>
          )
        ) : null}

        {clip ? (
          <p className="text-[11px] text-slate-400">
            成片时长约 {formatDuration(clip.duration)}，符合 46~60 秒区间；共{" "}
            {clip.segments.length} 段。
          </p>
        ) : null}
      </div>
    </section>
  );
}
