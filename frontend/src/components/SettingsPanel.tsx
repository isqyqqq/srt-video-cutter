import type { JobSnapshot } from "../types";
import {
  AlertIcon,
  LoaderIcon,
  RefreshIcon,
  ScissorsIcon,
  SettingsIcon,
  TrashIcon,
  XIcon,
} from "./Icons";

export interface Settings {
  padBefore: number;
  padAfter: number;
  fadeMs: number;
  quality: "high" | "standard";
  mode: "precise" | "fast";
}

interface Props {
  job: JobSnapshot;
  settings: Settings;
  busy: boolean;
  onSettingsChange: (settings: Settings) => void;
  onReplan: () => void;
  onStart: () => void;
  onCancel: () => void;
  onDelete: () => void;
}

export default function SettingsPanel({
  job,
  settings,
  busy,
  onSettingsChange,
  onReplan,
  onStart,
  onCancel,
  onDelete,
}: Props) {
  const processing = job.status === "processing" || job.status === "queued";
  const disabled = busy || processing;
  const update = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    onSettingsChange({ ...settings, [key]: value });

  return (
    <section className="card">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <SettingsIcon size={17} className="text-slate-400" />
          <h2 className="text-base font-semibold text-slate-900">剪辑设置</h2>
        </div>
        <span className="badge bg-slate-100 text-slate-500">
          {processing ? "处理中" : "可调整"}
        </span>
      </div>

      <div className="card-body space-y-5">
        <div className="grid grid-cols-2 gap-3">
          <label>
            <span className="label">前余量（秒）</span>
            <input
              className="input mono"
              type="number"
              min={0}
              max={5}
              step={0.05}
              value={settings.padBefore}
              disabled={disabled}
              onChange={(event) =>
                update("padBefore", Math.max(0, Number(event.target.value) || 0))
              }
            />
          </label>
          <label>
            <span className="label">后余量（秒）</span>
            <input
              className="input mono"
              type="number"
              min={0}
              max={5}
              step={0.05}
              value={settings.padAfter}
              disabled={disabled}
              onChange={(event) =>
                update("padAfter", Math.max(0, Number(event.target.value) || 0))
              }
            />
          </label>
        </div>
        <p className="-mt-2 text-[11px] leading-relaxed text-slate-400">
          默认 0，严格按 SRT 时间戳；设置余量可避免切掉字头字尾。
        </p>

        <label>
          <span className="label">音频淡化</span>
          <select
            className="input"
            value={settings.fadeMs}
            disabled={disabled}
            onChange={(event) => update("fadeMs", Number(event.target.value))}
          >
            <option value={0}>关闭（保留原始声音）</option>
            <option value={15}>15ms（消除拼接咔哒声）</option>
            <option value={30}>30ms（更柔和）</option>
          </select>
        </label>

        <div>
          <span className="label">画质档位</span>
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                ["high", "高画质", "CRF 18 · 编码稍慢"],
                ["standard", "标准", "CRF 20 · 速度更快"],
              ] as const
            ).map(([value, title, hint]) => (
              <button
                key={value}
                type="button"
                disabled={disabled}
                onClick={() => update("quality", value)}
                className={`rounded-xl border px-3 py-2.5 text-left transition ${
                  settings.quality === value
                    ? "border-indigo-300 bg-indigo-50 ring-2 ring-indigo-100"
                    : "border-slate-200 bg-white hover:bg-slate-50"
                }`}
              >
                <p className="text-sm font-medium text-slate-800">{title}</p>
                <p className="mt-0.5 text-[11px] text-slate-400">{hint}</p>
              </button>
            ))}
          </div>
        </div>

        <div>
          <span className="label">精度模式</span>
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                ["precise", "精确模式", "帧级切点 · 默认"],
                ["fast", "极速模式", "关键帧切点 · 可能偏移"],
              ] as const
            ).map(([value, title, hint]) => (
              <button
                key={value}
                type="button"
                disabled={disabled}
                onClick={() => update("mode", value)}
                className={`rounded-xl border px-3 py-2.5 text-left transition ${
                  settings.mode === value
                    ? "border-indigo-300 bg-indigo-50 ring-2 ring-indigo-100"
                    : "border-slate-200 bg-white hover:bg-slate-50"
                }`}
              >
                <p className="text-sm font-medium text-slate-800">{title}</p>
                <p className="mt-0.5 text-[11px] text-slate-400">{hint}</p>
              </button>
            ))}
          </div>
        </div>

        {settings.mode === "fast" ? (
          <div className="flex gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-[11px] leading-relaxed text-amber-700">
            <AlertIcon size={14} className="mt-0.5 shrink-0" />
            <span>极速模式按关键帧切，切点最多可能偏移 1–2 秒，仅建议用于快速预览。</span>
          </div>
        ) : null}

        <div className="space-y-2 border-t border-slate-100 pt-4">
          <button
            type="button"
            className="btn btn-secondary w-full"
            onClick={onReplan}
            disabled={disabled}
          >
            {busy ? <LoaderIcon size={16} /> : <RefreshIcon size={16} />}
            重新计算切点
          </button>
          <button
            type="button"
            className="btn btn-primary w-full"
            onClick={onStart}
            disabled={disabled || !job.plan}
          >
            {processing ? <LoaderIcon size={16} /> : <ScissorsIcon size={16} />}
            {job.status === "completed" ? "按当前设置重新剪辑" : "开始剪辑"}
          </button>
          {processing ? (
            <button type="button" className="btn btn-danger w-full" onClick={onCancel}>
              <XIcon size={16} />
              取消任务
            </button>
          ) : null}
          {!processing ? (
            <button
              type="button"
              className="btn btn-ghost w-full text-xs"
              onClick={onDelete}
            >
              <TrashIcon size={14} />
              删除任务与全部产物
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
