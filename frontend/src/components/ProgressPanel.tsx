import { useEffect, useRef } from "react";
import type { JobSnapshot } from "../types";
import { statusLabel } from "../utils";
import { AlertIcon, CheckIcon, LoaderIcon, XIcon } from "./Icons";

const STATUS_STYLES: Record<string, string> = {
  queued: "bg-amber-50 text-amber-600",
  processing: "bg-indigo-50 text-indigo-600",
  completed: "bg-emerald-50 text-emerald-600",
  failed: "bg-rose-50 text-rose-600",
  cancelled: "bg-slate-100 text-slate-500",
};

export default function ProgressPanel({ job }: { job: JobSnapshot }) {
  const logRef = useRef<HTMLPreElement>(null);
  const processing = job.status === "processing" || job.status === "queued";

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [job.logs.length]);

  if (job.status === "uploaded" || job.status === "planned") return null;

  const percent = Math.round(job.progress * 100);
  const style = STATUS_STYLES[job.status] ?? "bg-slate-100 text-slate-500";

  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div className="flex items-center gap-2">
          {processing ? (
            <LoaderIcon size={17} className="text-indigo-500" />
          ) : job.status === "completed" ? (
            <CheckIcon size={17} className="text-emerald-500" />
          ) : job.status === "failed" ? (
            <AlertIcon size={17} className="text-rose-500" />
          ) : (
            <XIcon size={17} className="text-slate-400" />
          )}
          <h2 className="text-base font-semibold text-slate-900">处理进度</h2>
        </div>
        <span className={`badge ${style}`}>{statusLabel(job.status)}</span>
      </div>

      <div className="card-body space-y-4">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate text-slate-600">{job.message}</span>
            <span className="mono shrink-0 text-slate-500">{percent}%</span>
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                job.status === "failed"
                  ? "bg-rose-400"
                  : job.status === "cancelled"
                    ? "bg-slate-300"
                    : "bg-gradient-to-r from-indigo-500 to-sky-400"
              }`}
              style={{ width: `${Math.max(2, percent)}%` }}
            />
          </div>
        </div>

        {job.error ? (
          <div className="rounded-xl bg-rose-50 px-4 py-3 text-xs leading-relaxed text-rose-700">
            {job.error}
          </div>
        ) : null}

        <details className="group rounded-xl border border-slate-100 bg-slate-50/60">
          <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-xs font-medium text-slate-600">
            <span>查看处理日志（{job.logs.length} 行）</span>
            <span className="text-slate-400 transition group-open:rotate-180">⌄</span>
          </summary>
          <pre
            ref={logRef}
            className="max-h-64 overflow-auto border-t border-slate-100 px-4 py-3 font-mono text-[11px] leading-relaxed text-slate-500"
          >
            {job.logs.length > 0 ? job.logs.join("\n") : "暂无日志"}
          </pre>
        </details>
      </div>
    </section>
  );
}
