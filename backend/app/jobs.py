"""任务管理：上传、规划、单任务队列、进度、取消。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import errno
import json
import os
from pathlib import Path
import queue
import threading
import traceback
import uuid

from fastapi import UploadFile

from .ffmpeg import (
    CancelledError,
    FFmpegError,
    VideoInfo,
    build_clip_command,
    build_fast_clip_command,
    build_fast_concat_command,
    build_merged_command,
    build_preview_command,
    probe_video,
    run_ffmpeg,
)
from . import clip_writer, llm
from .clip_writer import ClipResult, ClipWriterError, Hook, Segment
from .planner import PlanError, PlanResult, build_plan
from .srt_parser import RawCue, SrtParseError, parse_srt_bytes, parse_srt_text
from .storage import (
    JobPaths,
    create_clips_zip,
    create_job_paths,
    delete_job_dir,
    prune_jobs,
    save_upload,
)


class JobError(RuntimeError):
    """任务状态或参数不合法。"""


def _env_keep_jobs(default: int = 3) -> int:
    """从环境变量读取“保留多少个历史任务”（每个都带一份完整源视频）。"""

    raw = os.environ.get("SRT_CUTTER_KEEP_JOBS", "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _storage_hint(exc: OSError) -> str:
    """把磁盘写入错误（尤其是空间不足）翻译成老板能看懂的话。"""

    if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return (
            "磁盘空间不足，无法保存文件。请清理 data/jobs 下的旧任务，"
            "或设置 SRT_CUTTER_KEEP_JOBS 让程序自动保留更少的历史任务。"
        )
    return f"保存文件失败：{exc}"


@dataclass
class Job:
    id: str
    root: Path
    paths: JobPaths
    video_filename: str
    srt_filename: str
    video_path: Path
    srt_path: Path
    video_info: VideoInfo
    srt_encoding: str
    source_cues: list[RawCue]
    cues: list[RawCue]
    status: str = "uploaded"
    progress: float = 0.0
    message: str = "已上传，等待解析"
    error: str | None = None
    plan: PlanResult | None = None
    options: dict = field(default_factory=dict)
    hooks: list[dict] = field(default_factory=list)
    hook_warnings: list[str] = field(default_factory=list)
    selected_hook_id: str | None = None
    clip_meta: dict | None = None
    clip_text: str | None = None
    # 候选片段库：每项带唯一 id（s* 为成片段，c* 为备用段）
    library: list[dict] = field(default_factory=list)
    # 当前成片的片段 id 顺序
    composition: list[str] = field(default_factory=list)
    # “导出全部片段（含候选库）”的后台任务状态
    export_status: str = "idle"  # idle / running / ready / failed
    export_progress: float = 0.0
    export_message: str = ""
    export_error: str | None = None
    export_count: int = 0
    generation_status: str = "idle"
    generation_error: str | None = None
    logs: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def output_dir(self) -> str:
        return str(self.root)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 6),
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "video": {
                "filename": self.video_filename,
                "size": self.video_info.size,
                **self.video_info.to_dict(),
            },
            "srt": {
                "filename": self.srt_filename,
                "encoding": self.srt_encoding,
                "count": len(self.source_cues),
                "active_count": len(self.cues),
            },
            "hooks": self.hooks,
            "hook_warnings": self.hook_warnings,
            "selected_hook_id": self.selected_hook_id,
            "clip_srt": self._clip_srt_dict(),
            "library": self.library,
            "composition": self.composition,
            "export": {
                "status": self.export_status,
                "progress": round(self.export_progress, 6),
                "message": self.export_message,
                "error": self.export_error,
                "count": self.export_count,
                "ready": self.paths.export_zip.exists(),
            },
            "generation": {
                "status": self.generation_status,
                "error": self.generation_error,
            },
            "cues": [
                {
                    "index": cue.index,
                    "source_index": cue.source_index,
                    "srt_start": round(cue.start, 6),
                    "srt_end": round(cue.end, 6),
                    "duration": round(cue.end - cue.start, 6),
                    "text": cue.text,
                }
                for cue in self.cues
            ],
            "plan": self.plan.to_dict() if self.plan else None,
            "options": self.options,
            "output_dir": self.output_dir,
            "files": self._list_files(),
            "logs": self.logs[-200:],
        }

    def _clip_srt_dict(self) -> dict | None:
        """切片 SRT 的摘要信息，未生成时返回 None。"""

        if not self.clip_meta or self.clip_text is None:
            return None
        return {
            "path": "clip.srt",
            "count": len(self.cues),
            "hook_id": self.selected_hook_id,
            "text": self.clip_text,
            **self.clip_meta,
        }

    def _list_files(self) -> list[dict]:
        files: list[dict] = []
        if self.paths.clip_srt.exists():
            files.append(
                {
                    "name": "clip.srt",
                    "path": "clip.srt",
                    "kind": "clip_srt",
                    "size": self.paths.clip_srt.stat().st_size,
                }
            )
        if self.paths.merged.exists():
            files.append(
                {
                    "name": "merged.mp4",
                    "path": "merged.mp4",
                    "kind": "merged",
                    "size": self.paths.merged.stat().st_size,
                }
            )
        if self.paths.clips_dir.exists():
            for clip in sorted(self.paths.clips_dir.glob("*.mp4")):
                files.append(
                    {
                        "name": clip.name,
                        "path": f"clips/{clip.name}",
                        "kind": "clip",
                        "size": clip.stat().st_size,
                    }
                )
        if self.paths.clips_zip.exists():
            files.append(
                {
                    "name": "clips.zip",
                    "path": "clips.zip",
                    "kind": "zip",
                    "size": self.paths.clips_zip.stat().st_size,
                }
            )
        return files


class JobManager:
    """单任务队列：同一时间只跑一个 ffmpeg 任务，避免抢占 CPU。"""

    def __init__(self, data_dir: Path, chat_fn: clip_writer.ChatFn | None = None):
        self.data_dir = data_dir
        # 允许测试注入假模型；默认走真实大模型
        self.chat_fn = chat_fn or llm.chat
        self.generation_lock = threading.Lock()
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.queue: queue.Queue[str] = queue.Queue()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    # ---------------- 上传与规划 ----------------

    def create_job(self, video: UploadFile, srt: UploadFile) -> Job:
        """保存上传文件，解析 SRT，读取视频信息。"""

        job_id = uuid.uuid4().hex[:12]
        root = self.jobs_dir / job_id
        paths = create_job_paths(root)

        video_suffix = Path(video.filename or "source.mp4").suffix or ".mp4"
        video_path = paths.input_dir / f"source{video_suffix}"
        srt_path = paths.input_dir / "source.srt"

        try:
            save_upload(video, video_path)
            srt_data = srt.file.read()
            srt_path.write_bytes(srt_data)
            cues, encoding = parse_srt_bytes(srt_data)
            video_info = probe_video(video_path)
        except (SrtParseError, FFmpegError, OSError) as exc:
            delete_job_dir(root)
            if isinstance(exc, OSError):
                raise JobError(_storage_hint(exc)) from exc
            raise JobError(str(exc)) from exc

        job = Job(
            id=job_id,
            root=root,
            paths=paths,
            video_filename=video.filename or video_path.name,
            srt_filename=srt.filename or srt_path.name,
            video_path=video_path,
            srt_path=srt_path,
            video_info=video_info,
            srt_encoding=encoding,
            source_cues=cues,
            cues=cues,
        )
        job.message = f"已解析 {len(cues)} 条字幕，等待生成开头钩子"

        with self.lock:
            self.jobs[job_id] = job
        self._log(
            job,
            f"上传完成：视频 {job.video_filename}（{video_info.duration:.3f}s，"
            f"{video_info.fps:.3f}fps），SRT {len(cues)} 条（{encoding}）",
        )
        self._prune_old_jobs(protect=job_id)
        return job

    def _prune_old_jobs(self, protect: str | None = None) -> None:
        """只保留最近几个任务，避免旧任务里的整份源视频把磁盘吃满。

        任务不做持久化恢复（重启后不会重新载入），所以旧目录纯属占地方。
        清理失败不影响当前任务，只记一条日志。
        """

        keep = _env_keep_jobs()
        protect_ids = {protect} if protect else set()
        try:
            removed = prune_jobs(self.jobs_dir, keep=keep, protect=protect_ids)
        except OSError:
            return
        if not removed:
            return

        with self.lock:
            for job_id in removed:
                self.jobs.pop(job_id, None)
        if protect and protect in self.jobs:
            self._log(
                self.jobs[protect],
                f"已自动清理 {len(removed)} 个旧任务（保留最近 {keep} 个）",
            )

    # ---------------- 提示词生成切片 SRT ----------------

    def generate_hooks(self, job_id: str, count: int = 4, force: bool = False) -> list[dict]:
        """让模型挑出多个开头钩子，结果写入 hooks.json。"""

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，无法生成开头钩子")
        if job.hooks and not force:
            return job.hooks

        if not self.generation_lock.acquire(blocking=False):
            raise JobError("正在生成中，请稍候再试")

        try:
            with self.lock:
                job.generation_status = "generating_hooks"
                job.generation_error = None
                job.message = "正在生成开头钩子…"
            self._log(job, f"开始生成开头钩子：字幕 {len(job.source_cues)} 条")

            hooks, warnings = clip_writer.generate_hooks(
                job.source_cues, count=count, chat_fn=self.chat_fn
            )
            payload = {
                "job_id": job.id,
                "model": llm.describe(),
                "hooks": [hook.to_dict() for hook in hooks],
                "warnings": warnings,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            job.paths.hooks_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            with self.lock:
                job.hooks = payload["hooks"]
                job.hook_warnings = warnings
                job.generation_status = "hooks_ready"
                job.generation_error = None
                job.message = f"已生成 {len(hooks)} 个开头钩子，请选择一个"
            self._log(job, f"开头钩子生成完成：{len(hooks)} 个")
            return job.hooks

        except (ClipWriterError, llm.LlmError) as exc:
            with self.lock:
                job.generation_status = "failed"
                job.generation_error = str(exc)
                job.message = "开头钩子生成失败"
            self._log(job, f"开头钩子生成失败：{exc}")
            raise JobError(str(exc)) from exc
        except Exception as exc:  # 兜底：避免状态卡在 generating_hooks
            with self.lock:
                job.generation_status = "failed"
                job.generation_error = f"生成失败：{exc}"
                job.message = "开头钩子生成失败"
            self._log(job, traceback.format_exc())
            raise JobError(f"生成失败：{exc}") from exc
        finally:
            self.generation_lock.release()

    def generate_clip_srt(self, job_id: str, hook_id: str) -> ClipResult:
        """按选中的钩子生成切片 SRT，并自动重新规划切点。"""

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，无法生成切片")

        selected = next((item for item in job.hooks if item.get("id") == hook_id), None)
        if selected is None:
            raise JobError("请先选择一个有效的开头钩子")

        if not self.generation_lock.acquire(blocking=False):
            raise JobError("正在生成中，请稍候再试")

        try:
            with self.lock:
                job.generation_status = "generating_clip"
                job.generation_error = None
                job.selected_hook_id = hook_id
                job.message = "正在生成切片 SRT…"
            self._log(job, f"开始生成切片 SRT：钩子 {hook_id}（第 {selected['a']}~{selected['b']} 条）")

            hook = Hook(
                id=str(selected["id"]),
                type=str(selected.get("type") or "其他"),
                a=int(selected["a"]),
                b=int(selected["b"]),
                start=float(selected.get("start") or 0.0),
                end=float(selected.get("end") or 0.0),
                text=str(selected.get("text") or ""),
                reason=str(selected.get("reason") or ""),
            )
            result = clip_writer.generate_clip(
                job.source_cues, hook, chat_fn=self.chat_fn
            )

            job.paths.clip_srt.write_text(result.srt_text, encoding="utf-8")
            clip_cues = parse_srt_text(result.srt_text)

            # 构建候选片段库：成片段用 s*，备用段用 c*，每项带唯一 id
            library: list[dict] = []
            for position, seg in enumerate(result.segments, start=1):
                item = seg.to_dict()
                item["id"] = f"s{position}"
                item["origin"] = "final"
                library.append(item)
            for position, seg in enumerate(result.spare, start=1):
                item = seg.to_dict()
                item["id"] = f"c{position}"
                item["origin"] = "library"
                library.append(item)
            composition = [item["id"] for item in library if item["origin"] == "final"]

            # 重新生成时清掉上一次的成片产物与导出产物，避免新旧文件混在一起
            for clip in job.paths.clips_dir.glob("*.mp4"):
                clip.unlink(missing_ok=True)
            for file_path in (job.paths.merged, job.paths.clips_zip, job.paths.result_json):
                file_path.unlink(missing_ok=True)
            self._clear_export(job)
            self._clear_previews(job)

            with self.lock:
                job.cues = clip_cues
                job.clip_meta = result.to_dict()
                job.clip_text = result.srt_text
                job.library = library
                job.composition = composition
                job.generation_status = "clip_ready"
                job.generation_error = None
                job.plan = None
                job.message = (
                    f"切片 SRT 生成完成：{len(clip_cues)} 条，"
                    f"约 {result.duration:.1f}s（片段库 {len(library)} 个）"
                )
            self._log(
                job,
                f"切片 SRT 生成完成：{len(clip_cues)} 条，总时长 {result.duration:.3f}s，"
                f"片段库 {len(library)} 个（备用 {len(result.spare)} 个）",
            )
            for warning in result.warnings:
                self._log(job, f"切片提示：{warning}")

            # 自动按 0 余量规划，前端后续可再调整余量重算。
            # 规划失败时切片 SRT 仍然有效，不能把整个生成标记为失败。
            try:
                self.plan(job_id, 0.0, 0.0)
            except JobError:
                with self.lock:
                    job.generation_status = "clip_ready"
                    job.message = "切片 SRT 已生成，但切点规划失败，请检查时间戳"
                raise
            return result

        except (ClipWriterError, llm.LlmError) as exc:
            with self.lock:
                job.generation_status = "failed"
                job.generation_error = str(exc)
                job.message = "切片 SRT 生成失败"
            self._log(job, f"切片 SRT 生成失败：{exc}")
            raise JobError(str(exc)) from exc
        except Exception as exc:  # 兜底：避免状态卡在 generating_clip
            with self.lock:
                job.generation_status = "failed"
                job.generation_error = f"生成失败：{exc}"
                job.message = "切片 SRT 生成失败"
            self._log(job, traceback.format_exc())
            raise JobError(f"生成失败：{exc}") from exc
        finally:
            self.generation_lock.release()

    def compose(self, job_id: str, segment_ids: list[str]) -> Job:
        """按用户给定的片段 id 顺序重建成片，并重新规划切点。

        所有编排操作（替换/排序/删除/添加）都归约为一个 id 数组：
        按顺序从片段库取出片段，重建 cues 与 clip.srt，再重算 plan。
        """

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，无法调整成片")
        if not job.library:
            raise JobError("还没有生成片段库，请先生成切片")
        if not segment_ids:
            raise JobError("成片至少需要保留一个片段")

        by_id = {item["id"]: item for item in job.library}
        missing = [sid for sid in segment_ids if sid not in by_id]
        if missing:
            raise JobError(f"片段 id 不存在：{', '.join(missing)}")

        # 按用户顺序重建片段，再生成 SRT 文本与 cues。
        # 注意：cue.index 按成片顺序 1..N 重编，plan 会按 index 输出成片顺序。
        ordered: list[Segment] = []
        for item in segment_ids:
            raw = by_id[item]
            ordered.append(
                Segment(
                    a=int(raw["a"]),
                    b=int(raw["b"]),
                    step=str(raw.get("step") or "价值"),
                    reason=str(raw.get("reason") or ""),
                    start=float(raw["start"]),
                    end=float(raw["end"]),
                    text=str(raw["text"]),
                )
            )

        srt_text = clip_writer.build_srt_text(job.source_cues, ordered)
        job.paths.clip_srt.write_text(srt_text, encoding="utf-8")
        clip_cues = parse_srt_text(srt_text)

        # 成片变了，清掉旧产物，避免新旧文件混在一起
        for clip in job.paths.clips_dir.glob("*.mp4"):
            clip.unlink(missing_ok=True)
        for file_path in (job.paths.merged, job.paths.clips_zip, job.paths.result_json):
            file_path.unlink(missing_ok=True)

        total = sum(seg.duration for seg in ordered)
        with self.lock:
            job.cues = clip_cues
            job.composition = list(segment_ids)
            job.clip_text = srt_text
            job.clip_meta = {
                "hook": job.clip_meta.get("hook") if job.clip_meta else None,
                "segments": [seg.to_dict() for seg in ordered],
                "memory_point": (job.clip_meta or {}).get("memory_point", ""),
                "duration": round(total, 3),
                "warnings": (job.clip_meta or {}).get("warnings", []),
                "spare": (job.clip_meta or {}).get("spare", []),
            }
            job.plan = None
            job.status = "planned" if job.status == "completed" else job.status
            job.generation_status = "clip_ready"
            job.message = f"成片已调整：{len(ordered)} 段，约 {total:.1f}s"

        self._log(job, f"成片编排更新：{len(ordered)} 段，总时长 {total:.3f}s")

        # 重新规划切点，拿到可直接开剪的状态
        try:
            self.plan(job_id, 0.0, 0.0)
        except JobError:
            with self.lock:
                job.message = "成片已调整，但切点规划失败，请检查时间戳"
            raise
        return job

    def reset_clip(self, job_id: str) -> Job:
        """放弃切片，恢复到上传的完整 SRT。"""

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，无法恢复完整字幕")

        with self.lock:
            job.cues = job.source_cues
            job.clip_meta = None
            job.clip_text = None
            job.library = []
            job.composition = []
            job.selected_hook_id = None
            job.plan = None
            job.status = "uploaded"
            job.message = "已恢复完整字幕，可重新选择开头钩子"
            job.generation_status = "hooks_ready" if job.hooks else "idle"
            job.generation_error = None
        job.paths.clip_srt.unlink(missing_ok=True)
        for clip in job.paths.clips_dir.glob("*.mp4"):
            clip.unlink(missing_ok=True)
        for file_path in (job.paths.merged, job.paths.clips_zip, job.paths.result_json):
            file_path.unlink(missing_ok=True)
        self._clear_export(job)
        self._clear_previews(job)
        self._log(job, "已恢复完整字幕，清除切片 SRT 和旧成片")
        return job

    def plan(self, job_id: str, pad_before: float, pad_after: float) -> PlanResult:
        """重新计算切点，并写入 plan.json。"""

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，无法重新规划")

        try:
            plan = build_plan(
                job.cues,
                job.video_info.duration,
                job.video_info.fps,
                pad_before=pad_before,
                pad_after=pad_after,
            )
        except PlanError as exc:
            raise JobError("；".join(exc.errors)) from exc

        if job.video_info.is_vfr:
            plan.warnings.append(
                "检测到可变帧率（VFR）视频，切点已按平均帧率对齐，时间精度可能有 1 帧误差"
            )

        with self.lock:
            job.plan = plan
            job.status = "planned"
            job.message = (
                f"切点生成完成：{len(plan.items)} 个片段，"
                f"总时长 {plan.total_duration:.3f}s"
            )
            job.error = None
        self._write_plan(job)
        self._log(
            job,
            f"切点规划完成：{len(plan.items)} 段，总时长 {plan.total_duration:.3f}s，"
            f"余量前 {pad_before:.3f}s / 后 {pad_after:.3f}s",
        )
        return plan

    def _clear_previews(self, job: Job) -> None:
        """清掉片段快预览的缓存小样（重新生成切片 / 恢复完整字幕时调用）。"""

        if job.paths.preview_dir.exists():
            for old in job.paths.preview_dir.glob("*.mp4"):
                old.unlink(missing_ok=True)

    def _clear_export(self, job: Job) -> None:
        """清掉“导出全部片段”的旧产物与状态（重新生成切片时调用）。"""

        job.paths.export_zip.unlink(missing_ok=True)
        (job.paths.root / "all_segments.txt").unlink(missing_ok=True)
        if job.paths.export_dir.exists():
            for old in job.paths.export_dir.glob("*.mp4"):
                old.unlink(missing_ok=True)
        with self.lock:
            job.export_status = "idle"
            job.export_progress = 0.0
            job.export_message = ""
            job.export_error = None
            job.export_count = 0

    # ---------------- 导出全部片段（含候选库） ----------------

    def export_all(self, job_id: str) -> Job:
        """把“片段库里的每一个片段”（成片段 + 候选片段）都渲染成 mp4 并打包下载。

        与“下载全部片段”（只含成片）不同：这里包含 spare 候选库，方便老板自己挑素材。
        渲染在后台线程做，接口立即返回，前端轮询 export 状态。
        """

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，无法导出片段")
        if not job.library:
            raise JobError("还没有候选片段库，请先生成切片")
        if job.export_status == "running":
            return job

        with self.lock:
            job.export_status = "running"
            job.export_progress = 0.0
            job.export_error = None
            job.export_count = 0
            job.export_message = "正在准备导出……"
            items = list(job.library)

        thread = threading.Thread(
            target=self._export_worker, args=(job, items), daemon=True
        )
        thread.start()
        return job

    def _export_worker(self, job: Job, items: list[dict]) -> None:
        """后台把片段库里每一项都渲染成 mp4，并打包成 all_segments.zip。"""

        quality = job.options.get("quality", "high")
        export_dir = job.paths.export_dir
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            # 清掉上一次的导出产物
            for old in export_dir.glob("*.mp4"):
                old.unlink(missing_ok=True)
            manifest = job.paths.root / "all_segments.txt"
            lines: list[str] = [
                "编号\t来源\t类型\t开始\t结束\t时长\t文案",
            ]

            total = max(1, len(items))
            done_seconds = 0.0
            total_seconds = max(
                0.001, sum(float(item.get("duration") or 0.0) for item in items)
            )

            for position, item in enumerate(items, start=1):
                if job.cancel_event.is_set():
                    raise CancelledError("任务已取消")

                start = float(item.get("start") or 0.0)
                end = float(item.get("end") or 0.0)
                duration = max(0.0, end - start)
                if duration <= 0:
                    continue

                origin = "成片" if item.get("origin") == "final" else "候选"
                item_id = str(item.get("id") or f"item{position}")
                step = str(item.get("step") or "")
                name = f"{position:03d}_{item_id}_{origin}_{step}_{duration:.1f}s.mp4".replace(
                    "/", "-"
                )
                output = export_dir / name

                with self.lock:
                    job.export_message = f"正在渲染 {position}/{total}（{origin}·{step}）"
                    job.export_progress = min(
                        0.98, (done_seconds / total_seconds) * 0.92
                    )

                command = build_clip_command(
                    job.video_path,
                    start,
                    duration,
                    output,
                    job.video_info,
                    quality=quality,
                    audio_fade_ms=0,
                )

                def on_progress(fraction: float, base: float = done_seconds, length: float = duration) -> None:
                    with self.lock:
                        job.export_progress = min(
                            0.98,
                            ((base + fraction * length) / total_seconds) * 0.92,
                        )

                run_ffmpeg(
                    command,
                    duration=duration,
                    on_progress=on_progress,
                    on_log=lambda line: None,
                    cancel_event=job.cancel_event,
                    label=f"导出 {position}/{total}",
                )
                done_seconds += duration

                text = str(item.get("text") or "").replace("\n", " ")
                lines.append(
                    f"{item_id}\t{origin}\t{step}\t{start:.3f}\t{end:.3f}\t{duration:.3f}\t{text}"
                )

            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.lock:
                job.export_message = "正在打包……"
                job.export_progress = 0.99

            create_clips_zip(
                job.root, export_dir, job.paths.export_zip, extras=[manifest]
            )

            with self.lock:
                job.export_status = "ready"
                job.export_progress = 1.0
                job.export_count = len(list(export_dir.glob("*.mp4")))
                job.export_message = f"已导出 {job.export_count} 个片段（含候选库）"
            self._log(job, f"导出全部片段完成：{job.export_count} 个（含候选库）")

        except CancelledError:
            with self.lock:
                job.export_status = "failed"
                job.export_error = "导出已取消"
                job.export_message = "导出已取消"
        except Exception as exc:  # 后台线程不能把异常抛出去
            with self.lock:
                job.export_status = "failed"
                job.export_error = str(exc)
                job.export_message = "导出失败"
            self._log(job, f"导出全部片段失败：{exc}")

    # ---------------- 启动与队列 ----------------

    def start(
        self,
        job_id: str,
        quality: str = "high",
        mode: str = "precise",
        audio_fade_ms: int = 0,
    ) -> Job:
        """把任务加入队列。"""

        job = self.get_job(job_id)
        if quality not in {"high", "standard"}:
            raise JobError("画质档位不合法")
        if mode not in {"precise", "fast"}:
            raise JobError("精度模式不合法")
        if audio_fade_ms < 0 or audio_fade_ms > 200:
            raise JobError("音频淡化时长必须在 0–200ms 之间")
        if job.status in {"queued", "processing"}:
            raise JobError("任务已经在队列中或正在处理")

        if job.plan is None:
            self.plan(job_id, 0.0, 0.0)

        # 重新运行前清理旧产物，避免新旧文件混在一起
        for clip in job.paths.clips_dir.glob("*.mp4"):
            clip.unlink(missing_ok=True)
        for file_path in (job.paths.merged, job.paths.clips_zip, job.paths.result_json):
            file_path.unlink(missing_ok=True)

        with self.lock:
            job.options = {
                "quality": quality,
                "mode": mode,
                "audio_fade_ms": audio_fade_ms,
            }
            job.status = "queued"
            job.progress = 0.0
            job.error = None
            job.cancel_event.clear()
            job.message = "已加入队列，等待处理"
            job.logs.clear()
        self._log(
            job,
            f"任务入队：画质 {quality}，模式 {mode}，音频淡化 {audio_fade_ms}ms",
        )
        self._write_plan(job)
        self.queue.put(job_id)
        return job

    # ---------------- 查询与控制 ----------------

    def get_job(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise JobError("任务不存在")
        return job

    def snapshot(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        with self.lock:
            return job.to_dict()

    def list_jobs(self) -> list[dict]:
        with self.lock:
            jobs = sorted(
                self.jobs.values(), key=lambda item: item.created_at, reverse=True
            )
            return [job.to_dict() for job in jobs]

    def cancel(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        job.cancel_event.set()
        with self.lock:
            if job.status == "queued":
                job.status = "cancelled"
                job.message = "已取消（尚未开始）"
            elif job.status == "processing":
                job.message = "正在取消…"
        self._log(job, "收到取消请求")
        return job

    def delete(self, job_id: str) -> None:
        job = self.get_job(job_id)
        job.cancel_event.set()
        with self.lock:
            self.jobs.pop(job_id, None)
        delete_job_dir(job.root)

    # ---------------- 后台处理 ----------------

    def _worker_loop(self) -> None:
        while True:
            job_id = self.queue.get()
            try:
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                self._process(job)
            except Exception:
                # 后台线程不能因为单个任务异常而退出
                continue

    def _process(self, job: Job) -> None:
        plan = job.plan
        if plan is None:
            self._fail(job, "缺少切点规划，无法开始")
            return

        quality = job.options.get("quality", "high")
        mode = job.options.get("mode", "precise")
        audio_fade_ms = int(job.options.get("audio_fade_ms", 0))
        items = [item.to_dict() for item in plan.items]
        total_clip_duration = sum(item["duration"] for item in items)
        total_work = (
            total_clip_duration * 2 if mode == "precise" else total_clip_duration
        )
        total_work = max(total_work, 0.001)

        with self.lock:
            job.status = "processing"
            job.progress = 0.0
            job.error = None
            job.message = f"准备生成 {len(items)} 个片段"

        completed = 0.0

        try:
            for position, item in enumerate(items, start=1):
                if job.cancel_event.is_set():
                    raise CancelledError("任务已取消")

                duration = float(item["duration"])
                output = job.paths.clips_dir / item["output_name"]
                with self.lock:
                    job.message = f"正在生成片段 {position}/{len(items)}"

                if mode == "fast":
                    command = build_fast_clip_command(
                        job.video_path,
                        float(item["start"]),
                        duration,
                        output,
                        job.video_info,
                    )
                else:
                    command = build_clip_command(
                        job.video_path,
                        float(item["start"]),
                        duration,
                        output,
                        job.video_info,
                        quality=quality,
                        audio_fade_ms=audio_fade_ms,
                    )

                def on_progress(fraction: float, base=completed, length=duration) -> None:
                    self._set_progress(job, (base + fraction * length) / total_work)

                run_ffmpeg(
                    command,
                    duration=duration,
                    on_progress=on_progress,
                    on_log=lambda line: self._log(job, line),
                    cancel_event=job.cancel_event,
                    label=f"片段 {position}/{len(items)}",
                )
                completed += duration
                self._log(
                    job,
                    f"片段 {position}/{len(items)} 完成：{item['output_name']}",
                )

            if job.cancel_event.is_set():
                raise CancelledError("任务已取消")

            with self.lock:
                job.message = "正在合成成片"

            if mode == "fast":
                list_lines = [
                    f"file '{job.paths.clips_dir / item['output_name']}'"
                    for item in items
                ]
                job.paths.list_file.write_text("\n".join(list_lines), encoding="utf-8")
                command = build_fast_concat_command(
                    job.paths.list_file, job.paths.merged
                )
            else:
                command = build_merged_command(
                    job.video_path,
                    items,
                    job.paths.merged,
                    job.video_info,
                    quality=quality,
                    audio_fade_ms=audio_fade_ms,
                )

            def merged_progress(fraction: float) -> None:
                if mode == "fast":
                    self._set_progress(job, 0.95 + fraction * 0.05)
                else:
                    self._set_progress(
                        job,
                        (completed + fraction * total_clip_duration) / total_work,
                    )

            run_ffmpeg(
                command,
                duration=total_clip_duration,
                on_progress=merged_progress,
                on_log=lambda line: self._log(job, line),
                cancel_event=job.cancel_event,
                label="成片",
            )

            create_clips_zip(job.root, job.paths.clips_dir, job.paths.clips_zip)

            with self.lock:
                job.status = "completed"
                job.progress = 1.0
                job.message = (
                    f"完成：{len(items)} 个片段，成片 {job.paths.merged.name}"
                )
            self._write_result(job)
            self._log(job, "全部完成")

        except CancelledError:
            with self.lock:
                job.status = "cancelled"
                job.message = "任务已取消"
            self._log(job, "任务已取消，保留已生成的片段")
        except Exception as exc:
            self._fail(job, str(exc))
            self._log(job, traceback.format_exc())

    def _fail(self, job: Job, message: str) -> None:
        with self.lock:
            job.status = "failed"
            job.error = message
            job.message = "处理失败"
        self._log(job, f"处理失败：{message}")

    def _set_progress(self, job: Job, fraction: float) -> None:
        with self.lock:
            job.progress = max(job.progress, min(0.999, max(0.0, fraction)))

    def _log(self, job: Job, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        with self.lock:
            job.logs.append(line)
            if len(job.logs) > 500:
                del job.logs[:250]
        try:
            with job.paths.log_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def _write_plan(self, job: Job) -> None:
        payload = {
            "job_id": job.id,
            "video": job.video_info.to_dict(),
            "srt": {
                "filename": job.srt_filename,
                "encoding": job.srt_encoding,
                "source_count": len(job.source_cues),
                "active_count": len(job.cues),
            },
            "generation": {
                "status": job.generation_status,
                "error": job.generation_error,
                "selected_hook_id": job.selected_hook_id,
                "clip_srt": job._clip_srt_dict(),
            },
            "plan": job.plan.to_dict() if job.plan else None,
            "options": job.options,
        }
        job.paths.plan_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _write_result(self, job: Job) -> None:
        payload = {
            "job_id": job.id,
            "status": job.status,
            "video": job.video_info.to_dict(),
            "plan": job.plan.to_dict() if job.plan else None,
            "options": job.options,
            "files": job._list_files(),
            "output_dir": job.output_dir,
        }
        job.paths.result_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------------- 片段快预览（成片前先看画面） ----------------

    def preview_segment(self, job_id: str, segment_id: str) -> Path:
        """按需渲染某一小段，返回预览小样的文件路径。

        候选片段在成片前还没有 mp4，老板想先看看画面，所以这里临时渲染一个
        720p 轻量小样（veryfast + crf28），并按「片段 id + 起止时间」缓存：
        同一个片段第二次点击直接命中缓存，不会再跑一遍 ffmpeg。

        缓存放在 `<任务>/previews/` 下，重新生成切片 / 恢复完整字幕时整体清空。
        """

        job = self.get_job(job_id)
        if job.status in {"queued", "processing"}:
            raise JobError("任务正在处理中，暂时无法预览片段")

        segment = next((item for item in job.library if item["id"] == segment_id), None)
        if segment is None:
            raise JobError(f"片段 {segment_id} 不存在，请刷新页面后重试")

        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        duration = max(0.0, end - start)
        if duration <= 0:
            raise JobError("该片段时长为 0，无法预览")

        # 文件名带上起止时间：片段被「替换」后 id 不变、时间会变，不会命中旧缓存
        cache_name = f"{segment_id}_{start:.3f}_{duration:.3f}.mp4".replace("/", "-")
        target = job.paths.preview_dir / cache_name
        if target.exists() and target.stat().st_size > 0:
            return target

        job.paths.preview_dir.mkdir(parents=True, exist_ok=True)
        command = build_preview_command(
            job.video_path, start, duration, target, job.video_info
        )
        try:
            run_ffmpeg(
                command,
                duration=duration,
                on_log=lambda line: None,
                cancel_event=None,
                label=f"预览 {segment_id}",
            )
        except (FFmpegError, CancelledError) as exc:
            target.unlink(missing_ok=True)
            raise JobError(f"片段预览渲染失败：{exc}") from exc
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise JobError(_storage_hint(exc)) from exc

        return target
