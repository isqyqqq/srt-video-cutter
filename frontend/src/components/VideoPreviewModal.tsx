import { useEffect, useState } from "react";
import { XIcon } from "./Icons";

interface Props {
  /** 已经拼好的视频地址（完整预览或片段预览） */
  src: string;
  title: string;
  onClose: () => void;
}

export default function VideoPreviewModal({ src, title, onClose }: Props) {
  // 片段预览需要后端现场渲染，先展示加载提示，避免黑屏让人以为坏了
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl overflow-hidden rounded-2xl bg-slate-950 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4 border-b border-white/10 px-5 py-3">
          <p className="truncate text-sm font-medium text-white">{title}</p>
          <button
            type="button"
            className="rounded-lg p-1.5 text-slate-300 transition hover:bg-white/10 hover:text-white"
            onClick={onClose}
            aria-label="关闭"
          >
            <XIcon size={18} />
          </button>
        </div>
        <div className="relative flex max-h-[78vh] items-center justify-center bg-black">
          <video
            className="max-h-[78vh] w-full"
            src={src}
            controls
            autoPlay
            playsInline
            onCanPlay={() => setLoading(false)}
            onError={() =>
              setFailed("预览加载失败，可能还在渲染或片段已被删除，请关闭后重试")
            }
          />
          {loading && !failed ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/80 text-xs text-slate-300">
              <span className="h-6 w-6 animate-spin rounded-full border-2 border-slate-600 border-t-indigo-400" />
              正在渲染预览片段…
            </div>
          ) : null}
          {failed ? (
            <div className="absolute inset-0 flex items-center justify-center bg-black/85 px-6 text-center text-xs text-rose-300">
              {failed}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
