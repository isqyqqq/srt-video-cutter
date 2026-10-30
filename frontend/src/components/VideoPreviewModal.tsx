import { useEffect } from "react";
import { previewUrl } from "../api";
import { XIcon } from "./Icons";

interface Props {
  jobId: string;
  path: string;
  title: string;
  onClose: () => void;
}

export default function VideoPreviewModal({ jobId, path, title, onClose }: Props) {
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
        <div className="flex max-h-[78vh] items-center justify-center bg-black">
          <video
            className="max-h-[78vh] w-full"
            src={previewUrl(jobId, path)}
            controls
            autoPlay
            playsInline
          />
        </div>
      </div>
    </div>
  );
}
