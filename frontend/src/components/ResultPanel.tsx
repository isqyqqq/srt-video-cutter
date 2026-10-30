import { useState } from "react";
import type { JobSnapshot } from "../types";
import { downloadUrl, previewUrl, zipUrl } from "../api";
import { formatBytes, formatTc } from "../utils";
import {
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  FolderIcon,
  PackageIcon,
  PlayIcon,
} from "./Icons";

interface Props {
  job: JobSnapshot;
  onPreview: (path: string, title: string) => void;
}

export default function ResultPanel({ job, onPreview }: Props) {
  const [copied, setCopied] = useState(false);
  const merged = job.files.find((file) => file.kind === "merged");
  const clips = job.files.filter((file) => file.kind === "clip");
  const planByName = new Map(
    (job.plan?.items ?? []).map((item) => [item.output_name, item]),
  );

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(job.output_dir);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  if (job.status !== "completed" || !merged) {
    return null;
  }

  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <CheckIcon size={18} className="text-emerald-500" />
          <div>
            <h2 className="text-base font-semibold text-slate-900">剪辑完成</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              成片按 SRT 顺序拼接，片段与 SRT 条目一一对应
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <a className="btn btn-primary" href={downloadUrl(job.id, merged.path)}>
            <DownloadIcon size={16} />
            下载成片
          </a>
          <a className="btn btn-secondary" href={zipUrl(job.id)}>
            <PackageIcon size={16} />
            下载全部片段
          </a>
        </div>
      </div>

      <div className="card-body space-y-6">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,360px)_1fr]">
          <div>
            <div className="overflow-hidden rounded-2xl bg-slate-950 shadow-lg">
              <video
                key={merged.path}
                className="max-h-[520px] w-full"
                controls
                playsInline
                preload="metadata"
                src={previewUrl(job.id, merged.path)}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
              <span className="mono">{merged.name}</span>
              <span>{formatBytes(merged.size)}</span>
            </div>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-800">
                成片信息
              </h3>
              <span className="badge bg-emerald-50 text-emerald-600">
                {job.plan?.items.length ?? clips.length} 个片段
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-xl bg-slate-50 px-3 py-2.5">
                <dt className="text-slate-400">成片时长</dt>
                <dd className="mono mt-1 text-sm font-medium text-slate-800">
                  {formatTc(job.plan?.total_duration ?? 0)}
                </dd>
              </div>
              <div className="rounded-xl bg-slate-50 px-3 py-2.5">
                <dt className="text-slate-400">总帧数</dt>
                <dd className="mono mt-1 text-sm font-medium text-slate-800">
                  {job.plan?.total_frames ?? 0}
                </dd>
              </div>
              <div className="rounded-xl bg-slate-50 px-3 py-2.5">
                <dt className="text-slate-400">画质</dt>
                <dd className="mt-1 text-sm font-medium text-slate-800">
                  {job.options.quality === "standard" ? "标准" : "高画质"}
                </dd>
              </div>
              <div className="rounded-xl bg-slate-50 px-3 py-2.5">
                <dt className="text-slate-400">模式</dt>
                <dd className="mt-1 text-sm font-medium text-slate-800">
                  {job.options.mode === "fast" ? "极速" : "精确"}
                </dd>
              </div>
            </dl>

            <div className="rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2.5">
              <p className="text-[11px] text-slate-400">输出目录</p>
              <div className="mt-1 flex items-start gap-2">
                <code className="min-w-0 flex-1 break-all font-mono text-[11px] leading-relaxed text-slate-600">
                  {job.output_dir}
                </code>
                <button
                  type="button"
                  className="btn btn-ghost shrink-0 px-2 py-1"
                  onClick={copyPath}
                  title="复制路径"
                >
                  {copied ? (
                    <CheckIcon size={14} className="text-emerald-500" />
                  ) : (
                    <CopyIcon size={14} />
                  )}
                </button>
              </div>
            </div>

            <div className="flex items-center gap-2 rounded-xl bg-indigo-50/60 px-3 py-2.5 text-[11px] leading-relaxed text-indigo-700">
              <FolderIcon size={15} className="shrink-0" />
              每个片段都可以单独下载，也可以打包下载后直接发布。
            </div>
          </div>
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-800">全部片段</h3>
            <span className="text-xs text-slate-400">{clips.length} 个文件</span>
          </div>
          <div className="max-h-[420px] overflow-auto rounded-xl border border-slate-100">
            <table className="w-full min-w-[680px] border-collapse text-left text-sm">
              <thead className="sticky top-0 bg-slate-50/95 backdrop-blur">
                <tr className="text-xs text-slate-400">
                  <th className="px-4 py-2.5 font-medium">#</th>
                  <th className="px-3 py-2.5 font-medium">文件</th>
                  <th className="px-3 py-2.5 font-medium">原片时间</th>
                  <th className="px-3 py-2.5 text-right font-medium">时长</th>
                  <th className="px-3 py-2.5 text-right font-medium">大小</th>
                  <th className="px-4 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {clips.map((clip) => {
                  const item = planByName.get(clip.name);
                  return (
                    <tr key={clip.path} className="hover:bg-slate-50/60">
                      <td className="mono px-4 py-2.5 text-xs text-slate-400">
                        {item?.index ?? "-"}
                      </td>
                      <td className="max-w-[260px] truncate px-3 py-2.5 font-mono text-[11px] text-slate-600">
                        {clip.name}
                      </td>
                      <td className="mono whitespace-nowrap px-3 py-2.5 text-[11px] text-slate-500">
                        {item
                          ? `${formatTc(item.start)} → ${formatTc(item.end)}`
                          : "-"}
                      </td>
                      <td className="mono px-3 py-2.5 text-right text-xs text-slate-500">
                        {item ? `${item.duration.toFixed(2)}s` : "-"}
                      </td>
                      <td className="mono px-3 py-2.5 text-right text-xs text-slate-400">
                        {formatBytes(clip.size)}
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            className="btn btn-ghost px-2 py-1"
                            title="预览"
                            onClick={() =>
                              onPreview(
                                clip.path,
                                item ? `片段 ${item.index} · ${item.text}` : clip.name,
                              )
                            }
                          >
                            <PlayIcon size={14} />
                          </button>
                          <a
                            className="btn btn-ghost px-2 py-1"
                            title="下载"
                            href={downloadUrl(job.id, clip.path)}
                          >
                            <DownloadIcon size={14} />
                          </a>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
