import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelJob,
  composeJob,
  deleteJob,
  exportAllSegments,
  generateClipSrt,
  generateHooks,
  getHealth,
  getJob,
  getLlmStatus,
  planJob,
  previewUrl,
  resetClip,
  segmentPreviewUrl,
  startJob,
  uploadJob,
} from "./api";
import type { Health, JobSnapshot, LlmStatus } from "./types";
import UploadPanel, { type UploadMode } from "./components/UploadPanel";
import JobSummary from "./components/JobSummary";
import HookSelector from "./components/HookSelector";
import CompositionEditor from "./components/CompositionEditor";
import PlanTable from "./components/PlanTable";
import SettingsPanel, { type Settings } from "./components/SettingsPanel";
import ProgressPanel from "./components/ProgressPanel";
import ResultPanel from "./components/ResultPanel";
import VideoPreviewModal from "./components/VideoPreviewModal";
import {
  AlertIcon,
  CheckIcon,
  FilmIcon,
  RefreshIcon,
  ScissorsIcon,
  XIcon,
} from "./components/Icons";

const DEFAULT_SETTINGS: Settings = {
  padBefore: 0,
  padAfter: 0,
  fadeMs: 0,
  quality: "high",
  mode: "precise",
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function App() {
  const [video, setVideo] = useState<File | null>(null);
  const [srt, setSrt] = useState<File | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [activeHookId, setActiveHookId] = useState<string | null>(null);
  const [manualMode, setManualMode] = useState(false);
  // 上传模式：AI 智能切片 / 手动重排剪辑，默认走 AI
  const [uploadMode, setUploadMode] = useState<UploadMode>("ai");
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  // 预览弹窗：src 直接给视频地址，兼容「完整文件预览」和「片段现场渲染预览」
  const [preview, setPreview] = useState<{ src: string; title: string } | null>(
    null,
  );
  const noticeTimer = useRef<number | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    getLlmStatus()
      .then(setLlm)
      .catch(() => setLlm(null));
  }, []);

  // 处理中每秒轮询一次；状态变化后自动重建定时器
  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "processing")) {
      return;
    }
    const jobId = job.id;
    const timer = window.setInterval(() => {
      getJob(jobId)
        .then(setJob)
        .catch((pollError) => setError(errorText(pollError)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  // 导出全部片段在后台渲染：跑的时候每秒轮询一次，拿到进度和完成状态
  useEffect(() => {
    if (!job || job.export?.status !== "running") return;
    const jobId = job.id;
    const timer = window.setInterval(() => {
      getJob(jobId)
        .then(setJob)
        .catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.export?.status]);

  const showNotice = useCallback((message: string) => {
    setNotice(message);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 2600);
  }, []);

  const handleUpload = async () => {
    if (!video || !srt || busy) return;
    setBusy(true);
    setError(null);
    setUploadProgress(0);
    setManualMode(false);
    let created: JobSnapshot | null = null;
    try {
      created = await uploadJob(video, srt, setUploadProgress);
      setJob(created);
      setUploadProgress(0);

      // 手动重排模式：不调用 AI，直接用上传的 SRT 生成切点
      if (uploadMode === "manual") {
        const planned = await planJob(
          created.id,
          settings.padBefore,
          settings.padAfter,
        );
        setJob(planned);
        setManualMode(true);
        showNotice("已按上传的 SRT 直接生成切点，可直接开始剪辑");
        return;
      }

      // AI 模式：上传成功后立刻生成候选开头，失败也不影响后续直接使用原 SRT
      const withHooks = await generateHooks(created.id, 4, false);
      setJob(withHooks);
      setActiveHookId(withHooks.hooks[0]?.id ?? null);
      showNotice(`已生成 ${withHooks.hooks.length} 个开头钩子，请选一个`);
    } catch (uploadError) {
      setError(errorText(uploadError));
      if (created) {
        try {
          setJob(await getJob(created.id));
        } catch {
          // 拉取失败时保留上传结果，错误提示已经展示
        }
      }
    } finally {
      setBusy(false);
      setUploadProgress(0);
    }
  };

  const handleGenerateHooks = async (force: boolean) => {
    if (!job || busy) return;
    const jobId = job.id;
    setBusy(true);
    setError(null);
    try {
      const next = await generateHooks(jobId, 4, force);
      setJob(next);
      setActiveHookId((current) =>
        current && next.hooks.some((hook) => hook.id === current)
          ? current
          : (next.hooks[0]?.id ?? null),
      );
      showNotice(`已生成 ${next.hooks.length} 个开头钩子`);
    } catch (hookError) {
      setError(errorText(hookError));
      try {
        setJob(await getJob(jobId));
      } catch {
        // 忽略二次失败，保留当前状态
      }
    } finally {
      setBusy(false);
    }
  };

  const handleGenerateClip = async (hookId: string) => {
    if (!job || busy) return;
    const jobId = job.id;
    setBusy(true);
    setError(null);
    try {
      const next = await generateClipSrt(jobId, hookId);
      setJob(next);
      setManualMode(false);
      showNotice(
        `切片 SRT 已生成：${next.clip_srt?.count ?? 0} 条，约 ${(
          next.clip_srt?.duration ?? 0
        ).toFixed(1)} 秒`,
      );
    } catch (clipError) {
      setError(errorText(clipError));
      try {
        setJob(await getJob(jobId));
      } catch {
        // 忽略二次失败
      }
    } finally {
      setBusy(false);
    }
  };

  const handleResetClip = async () => {
    if (!job || busy) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await resetClip(job.id));
      setActiveHookId(null);
      showNotice("已恢复完整字幕，可以重新选择开头");
    } catch (resetError) {
      setError(errorText(resetError));
    } finally {
      setBusy(false);
    }
  };

  const handleUseOriginal = async () => {
    if (!job || busy) return;
    setBusy(true);
    setError(null);
    try {
      const planned = await planJob(
        job.id,
        settings.padBefore,
        settings.padAfter,
      );
      setJob(planned);
      setManualMode(true);
      showNotice("已跳过 AI，直接按上传的完整 SRT 生成切点");
    } catch (planError) {
      setError(errorText(planError));
    } finally {
      setBusy(false);
    }
  };

  const handleCompose = async (segmentIds: string[]) => {
    if (!job || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await composeJob(job.id, segmentIds);
      setJob(next);
      showNotice("成片已按新的片段顺序更新");
    } catch (composeError) {
      setError(errorText(composeError));
      try {
        setJob(await getJob(job.id));
      } catch {
        // 忽略二次失败
      }
    } finally {
      setBusy(false);
    }
  };

  // “导出全部片段（含候选库）”：触发后台渲染，完成后轮询到 ready
  const handleExportAll = async () => {
    if (!job) return;
    setError(null);
    try {
      const next = await exportAllSegments(job.id);
      setJob(next);
      showNotice("已开始导出全部片段（含候选库）…");
    } catch (exportError) {
      setError(errorText(exportError));
    }
  };

  const handleReplan = async () => {
    if (!job || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await planJob(job.id, settings.padBefore, settings.padAfter);
      setJob(next);
      showNotice("切点已按当前设置重新计算");
    } catch (planError) {
      setError(errorText(planError));
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async () => {
    if (!job || busy) return;
    setBusy(true);
    setError(null);
    try {
      let current = job;
      const paddingChanged =
        !current.plan ||
        current.plan.pad_before !== settings.padBefore ||
        current.plan.pad_after !== settings.padAfter;
      if (paddingChanged) {
        current = await planJob(
          current.id,
          settings.padBefore,
          settings.padAfter,
        );
        setJob(current);
      }
      const next = await startJob(current.id, {
        quality: settings.quality,
        mode: settings.mode,
        audio_fade_ms: settings.fadeMs,
      });
      setJob(next);
      showNotice("任务已加入队列");
    } catch (startError) {
      setError(errorText(startError));
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!job) return;
    try {
      setJob(await cancelJob(job.id));
      showNotice("已发送取消请求");
    } catch (cancelError) {
      setError(errorText(cancelError));
    }
  };

  const handleDelete = async () => {
    if (!job) return;
    const confirmed = window.confirm(
      "确定删除这个任务和全部产物吗？删除后无法恢复。",
    );
    if (!confirmed) return;
    try {
      await deleteJob(job.id);
      setJob(null);
      setVideo(null);
      setSrt(null);
      setSettings(DEFAULT_SETTINGS);
      setActiveHookId(null);
      setManualMode(false);
      setUploadMode("ai");
      showNotice("任务已删除");
    } catch (deleteError) {
      setError(errorText(deleteError));
    }
  };

  const processing = job?.status === "queued" || job?.status === "processing";
  const showEditor = Boolean(job?.clip_srt) || manualMode;

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200/80 bg-white/80 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-sky-400 text-white shadow-lg shadow-indigo-500/20">
              <ScissorsIcon size={20} />
            </div>
            <div>
              <h1 className="text-base font-semibold tracking-tight text-slate-900">
                SRT 视频重排剪辑器
              </h1>
              <p className="text-[11px] text-slate-500">
                AI 选钩子 · 按原片时间戳剪辑 · 本地处理
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-[11px]">
            <span
              className={`badge ${
                health
                  ? "bg-emerald-50 text-emerald-600"
                  : "bg-slate-100 text-slate-500"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  health ? "bg-emerald-500" : "bg-slate-400"
                }`}
              />
              {health ? `FFmpeg ${health.ffmpeg}` : "服务检查中"}
            </span>
            {job ? (
              <span className="badge bg-slate-100 text-slate-500">
                <FilmIcon size={12} />
                {job.video.display_width} × {job.video.display_height}
              </span>
            ) : null}
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6 sm:px-6 lg:px-8">
        {error ? (
          <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <AlertIcon size={17} className="mt-0.5 shrink-0" />
            <p className="min-w-0 flex-1 leading-relaxed">{error}</p>
            <button
              type="button"
              className="rounded-lg p-1 text-rose-400 transition hover:bg-rose-100 hover:text-rose-600"
              onClick={() => setError(null)}
              aria-label="关闭错误提示"
            >
              <XIcon size={15} />
            </button>
          </div>
        ) : null}

        {notice ? (
          <div className="flex items-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-700">
            <CheckIcon size={16} />
            {notice}
          </div>
        ) : null}

        {!job ? (
          <div className="space-y-5">
            <UploadPanel
              video={video}
              srt={srt}
              busy={busy}
              uploadProgress={uploadProgress}
              mode={uploadMode}
              onModeChange={setUploadMode}
              onVideoChange={setVideo}
              onSrtChange={setSrt}
              onUpload={handleUpload}
            />
            <section className="grid gap-4 md:grid-cols-3">
              {[
                {
                  icon: <ScissorsIcon size={18} />,
                  title: "只按时间戳",
                  text: "不看字幕文案，SRT 的时间戳就是原视频里的切点。",
                },
                {
                  icon: <FilmIcon size={18} />,
                  title: "顺序完全照 SRT",
                  text: "SRT 排成什么顺序，成片就按什么顺序拼接。",
                },
                {
                  icon: <RefreshIcon size={18} />,
                  title: "成片 + 全部片段",
                  text: "同时输出合并成片和每个独立片段，片段可直接发布。",
                },
              ].map((item) => (
                <div key={item.title} className="card">
                  <div className="card-body">
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                      {item.icon}
                    </div>
                    <h3 className="mt-3 text-sm font-semibold text-slate-800">
                      {item.title}
                    </h3>
                    <p className="mt-1 text-xs leading-relaxed text-slate-500">
                      {item.text}
                    </p>
                  </div>
                </div>
              ))}
            </section>
          </div>
        ) : (
          <>
            <JobSummary job={job} />
            <HookSelector
              job={job}
              busy={busy}
              activeHookId={activeHookId}
              llm={llm}
              onActiveHookChange={setActiveHookId}
              onGenerateHooks={handleGenerateHooks}
              onGenerateClip={handleGenerateClip}
              onResetClip={handleResetClip}
              onUseOriginal={handleUseOriginal}
            />
            <CompositionEditor
              job={job}
              busy={busy}
              onCompose={handleCompose}
              onExportAll={handleExportAll}
              onPreviewSegment={(segment) =>
                setPreview({
                  src: segmentPreviewUrl(job.id, segment.id),
                  title: `片段预览 · ${segment.step} · ${segment.duration.toFixed(1)}s`,
                })
              }
            />
            {!showEditor ? (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-white/60 px-5 py-8 text-center text-sm text-slate-500">
                选好开头钩子并生成切片 SRT 后，这里会出现逐段切点表。
              </div>
            ) : null}
            <div
              className={`${
                showEditor ? "grid" : "hidden"
              } items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]`}
            >
              <div className="min-w-0 space-y-5">
                <PlanTable plan={job.plan} />
                <ProgressPanel job={job} />
                <ResultPanel
                  job={job}
                  onPreview={(path, title) =>
                    setPreview({ src: previewUrl(job.id, path), title })
                  }
                />
              </div>
              <aside className="space-y-5 lg:sticky lg:top-5">
                <SettingsPanel
                  job={job}
                  settings={settings}
                  busy={busy}
                  onSettingsChange={setSettings}
                  onReplan={handleReplan}
                  onStart={handleStart}
                  onCancel={handleCancel}
                  onDelete={handleDelete}
                />
                <div className="rounded-2xl border border-slate-200/80 bg-white/70 px-4 py-3 text-[11px] leading-relaxed text-slate-500">
                  <p className="font-medium text-slate-600">提示</p>
                  <p className="mt-1">
                    默认严格按 SRT 时间戳，只做整帧对齐。需要避免切掉字头字尾时，
                    可把前后余量改成 0.10 / 0.15 秒。
                  </p>
                  {processing ? (
                    <p className="mt-2 text-indigo-600">
                      处理期间请不要关闭终端，否则任务会中断。
                    </p>
                  ) : null}
                </div>
              </aside>
            </div>
          </>
        )}
      </main>

      <footer className="border-t border-slate-200/70 py-6 text-center text-[11px] text-slate-400">
        所有处理都在本机完成 · 原视频永远不会被修改
      </footer>

      {preview ? (
        <VideoPreviewModal
          src={preview.src}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      ) : null}
    </div>
  );
}
