import type { Plan } from "../types";
import { formatTc } from "../utils";
import { AlertIcon, CaptionsIcon, ClockIcon } from "./Icons";

export default function PlanTable({ plan }: { plan: Plan | null }) {
  if (!plan) {
    return (
      <section className="card">
        <div className="card-body flex flex-col items-center justify-center gap-3 py-14 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-100 text-slate-400">
            <CaptionsIcon size={26} />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-700">还没有切点</p>
            <p className="mt-1 text-xs text-slate-400">上传成功后会自动生成切点预览</p>
          </div>
        </div>
      </section>
    );
  }

  const hasPadding = plan.pad_before > 0 || plan.pad_after > 0;

  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div>
          <h2 className="text-base font-semibold text-slate-900">切点预览</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            顺序完全按照 SRT 条目；时间戳对应原视频
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="badge bg-slate-100 text-slate-600">
            {plan.items.length} 个片段
          </span>
          <span className="badge bg-indigo-50 text-indigo-600">
            <ClockIcon size={12} />
            {formatTc(plan.total_duration)} · {plan.total_frames} 帧
          </span>
          <span className="badge bg-white text-slate-500 ring-1 ring-slate-200">
            {hasPadding
              ? `余量 前${plan.pad_before.toFixed(2)}s / 后${plan.pad_after.toFixed(2)}s`
              : "无余量 · 严格按 SRT"}
          </span>
        </div>
      </div>

      {plan.warnings.length > 0 ? (
        <div className="border-b border-amber-100 bg-amber-50/70 px-5 py-3">
          <div className="flex gap-2 text-xs leading-relaxed text-amber-700">
            <AlertIcon size={15} className="mt-0.5 shrink-0" />
            <div className="space-y-1">
              {plan.warnings.map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      <div className="max-h-[460px] overflow-auto">
        <table className="w-full min-w-[680px] border-collapse text-left text-sm">
          <thead className="sticky top-0 z-10 bg-slate-50/95 backdrop-blur">
            <tr className="text-xs uppercase tracking-wide text-slate-400">
              <th className="px-4 py-3 font-medium">#</th>
              <th className="px-2.5 py-3 font-medium">SRT 原时间</th>
              <th className="px-2.5 py-3 font-medium">实际切点</th>
              <th className="px-2.5 py-3 text-right font-medium">时长</th>
              <th className="px-2.5 py-3 text-right font-medium">帧</th>
              <th className="px-4 py-3 font-medium">字幕文案</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {plan.items.map((item) => (
              <tr key={item.index} className="transition hover:bg-indigo-50/30">
                <td className="px-4 py-3">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600">
                    {item.index}
                  </span>
                </td>
                <td className="mono whitespace-nowrap px-2.5 py-3 text-xs text-slate-500">
                  {formatTc(item.srt_start)} → {formatTc(item.srt_end)}
                </td>
                <td className="mono whitespace-nowrap px-2.5 py-3 text-xs font-medium text-slate-800">
                  {formatTc(item.start)} → {formatTc(item.end)}
                </td>
                <td className="mono whitespace-nowrap px-2.5 py-3 text-right text-xs text-slate-500">
                  {item.duration.toFixed(2)}s
                </td>
                <td className="mono whitespace-nowrap px-2.5 py-3 text-right text-xs text-slate-400">
                  {item.frame_start}–{item.frame_end}
                </td>
                <td
                  className="max-w-[240px] truncate px-4 py-3 text-xs text-slate-600"
                  title={item.text}
                >
                  {item.text || <span className="text-slate-300">（无文案）</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
