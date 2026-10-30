import { useRef, useState } from "react";
import {
  CaptionsIcon,
  FilmIcon,
  LoaderIcon,
  UploadIcon,
} from "./Icons";
import { formatBytes } from "../utils";

/**
 * 上传模式：
 * - ai：完整视频 + 完整 SRT，交给 AI 选开头钩子再生成切片 SRT
 * - manual：完整视频 + 已排好的 SRT，直接按时间戳截取拼接（老功能，不调用 AI）
 */
export type UploadMode = "ai" | "manual";

interface Props {
  video: File | null;
  srt: File | null;
  busy: boolean;
  uploadProgress: number;
  mode: UploadMode;
  onModeChange: (mode: UploadMode) => void;
  onVideoChange: (file: File | null) => void;
  onSrtChange: (file: File | null) => void;
  onUpload: () => void;
}

/** 模式选项卡片：让老板一眼看清两种用法的区别 */
function ModeCard({
  active,
  title,
  desc,
  disabled,
  onClick,
}: {
  active: boolean;
  title: string;
  desc: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-2xl border p-3 text-left transition ${
        active
          ? "border-indigo-300 bg-indigo-50/60 ring-1 ring-indigo-200"
          : "border-slate-200 bg-white hover:border-indigo-200 hover:bg-slate-50"
      } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`h-4 w-4 shrink-0 rounded-full border-2 ${
            active ? "border-indigo-500 bg-indigo-500" : "border-slate-300"
          }`}
        />
        <span className="text-sm font-semibold text-slate-800">{title}</span>
      </div>
      <p className="mt-1.5 pl-6 text-xs leading-relaxed text-slate-500">{desc}</p>
    </button>
  );
}

function Dropzone({
  title,
  hint,
  accept,
  file,
  icon,
  onFile,
  disabled,
}: {
  title: string;
  hint: string;
  accept: string;
  file: File | null;
  icon: React.ReactNode;
  onFile: (file: File | null) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const pick = (incoming: File | undefined) => {
    if (incoming) onFile(incoming);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          inputRef.current?.click();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (!disabled) pick(event.dataTransfer.files[0]);
      }}
      className={`group flex min-h-[190px] cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition ${
        dragging
          ? "border-indigo-400 bg-indigo-50"
          : file
            ? "border-emerald-300 bg-emerald-50/50"
            : "border-slate-200 bg-slate-50/70 hover:border-indigo-300 hover:bg-indigo-50/40"
      } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        disabled={disabled}
        onChange={(event) => {
          pick(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <div
        className={`flex h-12 w-12 items-center justify-center rounded-2xl ${
          file ? "bg-emerald-100 text-emerald-600" : "bg-white text-indigo-500 shadow-sm"
        }`}
      >
        {icon}
      </div>
      <div>
        <p className="text-sm font-semibold text-slate-800">{title}</p>
        <p className="mt-1 text-xs text-slate-500">{hint}</p>
      </div>
      {file ? (
        <div className="max-w-full rounded-xl bg-white px-3 py-2 text-left shadow-sm">
          <p className="truncate text-sm font-medium text-slate-700">{file.name}</p>
          <p className="mt-0.5 text-xs text-slate-400">{formatBytes(file.size)}</p>
        </div>
      ) : (
        <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-indigo-600 shadow-sm">
          点击或拖入文件
        </span>
      )}
    </div>
  );
}

export default function UploadPanel({
  video,
  srt,
  busy,
  uploadProgress,
  mode,
  onModeChange,
  onVideoChange,
  onSrtChange,
  onUpload,
}: Props) {
  const isAi = mode === "ai";
  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div>
          <h2 className="text-base font-semibold text-slate-900">上传素材</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {isAi
              ? "上传完整视频 + 完整 SRT，下一步由 AI 生成开头钩子和切片"
              : "上传完整视频 + 已排好顺序的 SRT，按时间戳直接剪辑，不调用 AI"}
          </p>
        </div>
        <span className="badge bg-indigo-50 text-indigo-600">本地处理 · 不上传云端</span>
      </div>

      <div className="card-body space-y-5">
        {/* 模式选择：AI 智能切片与手动重排两种用法都保留，由老板自己决定 */}
        <div className="grid gap-3 sm:grid-cols-2">
          <ModeCard
            active={isAi}
            title="AI 智能切片"
            desc="完整视频 + 完整直播 SRT，AI 挑开头钩子并重排成 46~60 秒切片"
            disabled={busy}
            onClick={() => onModeChange("ai")}
          />
          <ModeCard
            active={!isAi}
            title="手动重排剪辑"
            desc="完整视频 + 已经排好顺序的 SRT，按时间戳直接截取拼接，不调用 AI"
            disabled={busy}
            onClick={() => onModeChange("manual")}
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <Dropzone
            title="原始完整视频"
            hint="支持 mp4 / mov / mkv 等 ffmpeg 可解码格式"
            accept="video/*,.mp4,.mov,.mkv,.avi,.flv,.webm"
            file={video}
            icon={<FilmIcon size={24} />}
            onFile={onVideoChange}
            disabled={busy}
          />
          <Dropzone
            title={isAi ? "完整直播字幕 SRT" : "已排好的字幕 SRT"}
            hint={
              isAi
                ? "上传原始完整 SRT，AI 会按提示词重排成切片"
                : "上传已经重排好顺序的 SRT，工具按时间戳直接截取拼接"
            }
            accept=".srt,text/plain"
            file={srt}
            icon={<CaptionsIcon size={24} />}
            onFile={onSrtChange}
            disabled={busy}
          />
        </div>

        {busy && uploadProgress > 0 && uploadProgress < 1 ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span className="flex items-center gap-1.5">
                <LoaderIcon size={14} /> 正在上传素材…
              </span>
              <span className="mono">{Math.round(uploadProgress * 100)}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-indigo-500 transition-all"
                style={{ width: `${Math.max(2, uploadProgress * 100)}%` }}
              />
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs leading-relaxed text-slate-400">
            成片与全部片段会同时生成；原视频不会被修改。
          </p>
          <button
            type="button"
            className="btn btn-primary min-w-[170px]"
            onClick={onUpload}
            disabled={busy || !video || !srt}
          >
            {busy ? <LoaderIcon size={16} /> : <UploadIcon size={16} />}
            {busy
              ? "正在处理…"
              : isAi
                ? "上传并生成开头钩子"
                : "上传并直接生成切点"}
          </button>
        </div>
      </div>
    </section>
  );
}
