"""HTTP API。"""

from __future__ import annotations

import mimetypes
from pathlib import Path
import re
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import llm
from .ffmpeg import ffmpeg_version
from .jobs import JobError, JobManager
from .storage import StorageError, create_clips_zip, resolve_inside

router = APIRouter(prefix="/api")


class PlanRequest(BaseModel):
    pad_before: float = Field(0.0, ge=0, le=5, description="前余量（秒）")
    pad_after: float = Field(0.0, ge=0, le=5, description="后余量（秒）")


class HooksRequest(BaseModel):
    """生成开头钩子的参数。"""

    count: int = Field(4, ge=3, le=6, description="期望的钩子数量")
    force: bool = Field(False, description="忽略已有钩子，重新生成")


class ClipSrtRequest(BaseModel):
    """按选中钩子生成切片 SRT。"""

    hook_id: str = Field(..., min_length=1, description="候选钩子 id，例如 h1")


class CompositionRequest(BaseModel):
    """按片段 id 顺序重排成片。"""

    segment_ids: list[str] = Field(..., min_length=1, description="成片片段的 id 顺序")


class StartRequest(BaseModel):
    quality: Literal["high", "standard"] = "high"
    mode: Literal["precise", "fast"] = "precise"
    audio_fade_ms: int = Field(0, ge=0, le=200)


def get_manager(request: Request) -> JobManager:
    """从应用状态中取出任务管理器。"""

    return request.app.state.manager


@router.get("/health")
def health(request: Request) -> dict:
    """健康检查，同时暴露 ffmpeg 版本。"""

    manager = get_manager(request)
    return {
        "status": "ok",
        "ffmpeg": ffmpeg_version(),
        "jobs": len(manager.jobs),
    }


@router.post("/jobs")
def create_job(
    request: Request,
    video: UploadFile = File(...),
    srt: UploadFile = File(...),
) -> dict:
    """上传视频和 SRT，创建任务。"""

    manager = get_manager(request)
    try:
        job = manager.create_job(video, srt)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return manager.snapshot(job.id)


@router.get("/jobs")
def list_jobs(request: Request) -> dict:
    """列出当前进程内的任务。"""

    return {"jobs": get_manager(request).list_jobs()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    """查询任务状态。"""

    manager = get_manager(request)
    try:
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/hooks")
def generate_hooks(
    job_id: str, payload: HooksRequest, request: Request
) -> dict:
    """让模型基于完整 SRT 生成多个开头钩子。"""

    manager = get_manager(request)
    try:
        manager.generate_hooks(job_id, count=payload.count, force=payload.force)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/clip-srt")
def generate_clip_srt(
    job_id: str, payload: ClipSrtRequest, request: Request
) -> dict:
    """按选中的开头钩子生成切片 SRT，并自动规划切点。"""

    manager = get_manager(request)
    try:
        manager.generate_clip_srt(job_id, payload.hook_id)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/composition")
def compose_job(
    job_id: str, payload: CompositionRequest, request: Request
) -> dict:
    """按片段 id 顺序调整成片（替换/排序/删除/添加）。"""

    manager = get_manager(request)
    try:
        manager.compose(job_id, payload.segment_ids)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/reset-clip")
def reset_clip(job_id: str, request: Request) -> dict:
    """放弃切片，恢复到上传的完整 SRT。"""

    manager = get_manager(request)
    try:
        manager.reset_clip(job_id)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/llm")
def llm_status() -> dict:
    """返回当前大模型配置摘要（不含密钥）。"""

    return llm.describe()


@router.post("/jobs/{job_id}/plan")
def plan_job(job_id: str, payload: PlanRequest, request: Request) -> dict:
    """重新计算切点。"""

    manager = get_manager(request)
    try:
        manager.plan(job_id, payload.pad_before, payload.pad_after)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/start")
def start_job(job_id: str, payload: StartRequest, request: Request) -> dict:
    """开始处理任务。"""

    manager = get_manager(request)
    try:
        manager.start(
            job_id,
            quality=payload.quality,
            mode=payload.mode,
            audio_fade_ms=payload.audio_fade_ms,
        )
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    """取消任务。"""

    manager = get_manager(request)
    try:
        manager.cancel(job_id)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request) -> dict:
    """删除任务和全部产物。"""

    manager = get_manager(request)
    try:
        manager.delete(job_id)
        return {"ok": True}
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _job_file(request: Request, job_id: str, relative: str) -> tuple[Path, Path]:
    """把相对路径解析成任务目录内的真实文件。"""

    manager = get_manager(request)
    try:
        job = manager.get_job(job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        target = resolve_inside(job.root, relative)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="非法文件路径") from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return job.root, target


def _range_response(
    path: Path, request: Request, download: bool = False
) -> StreamingResponse:
    """支持 Range 的视频流响应，保证浏览器可以拖动进度条。"""

    size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range")

    start = 0
    end = size - 1
    status_code = 200
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Cache-Control": "no-cache",
    }

    if download:
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'

    if range_header:
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if not match:
            raise HTTPException(status_code=416, detail="Range 格式不正确")
        start_text, end_text = match.group(1), match.group(2)
        if not start_text and not end_text:
            raise HTTPException(status_code=416, detail="Range 格式不正确")
        if not start_text:
            # bytes=-500 表示最后 500 字节
            suffix = int(end_text)
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        if start >= size or end < start:
            raise HTTPException(
                status_code=416,
                detail="请求范围超出文件大小",
                headers={"Content-Range": f"bytes */{size}"},
            )
        end = min(end, size - 1)
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)

    def iterator():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iterator(), status_code=status_code, media_type=media_type, headers=headers
    )


@router.get("/jobs/{job_id}/preview/{file_path:path}")
def preview(job_id: str, file_path: str, request: Request) -> StreamingResponse:
    """预览视频（支持 Range）。"""

    _, target = _job_file(request, job_id, file_path)
    if target.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        raise HTTPException(status_code=400, detail="只支持预览视频文件")
    return _range_response(target, request, download=False)


@router.get("/jobs/{job_id}/segment-preview/{segment_id}")
def preview_segment(job_id: str, segment_id: str, request: Request) -> StreamingResponse:
    """片段快预览：按需渲染一个 720p 小样并返回（支持 Range）。

    候选片段在成片前还没有 mp4 文件，这里第一次请求时现场渲染，
    之后按「片段 id + 起止时间」缓存，再次请求直接读缓存。
    """

    manager = get_manager(request)
    try:
        target = manager.preview_segment(job_id, segment_id)
    except JobError as exc:
        # 片段不存在 / 渲染失败 / 磁盘不足，都给出中文原因
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _range_response(target, request, download=False)


@router.get("/jobs/{job_id}/download/{file_path:path}")
def download(job_id: str, file_path: str, request: Request) -> StreamingResponse:
    """下载文件。"""

    _, target = _job_file(request, job_id, file_path)
    return _range_response(target, request, download=True)


@router.get("/jobs/{job_id}/clips.zip")
def download_clips_zip(job_id: str, request: Request) -> StreamingResponse:
    """打包下载全部片段。"""

    manager = get_manager(request)
    try:
        job = manager.get_job(job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not job.paths.clips_dir.exists() or not any(job.paths.clips_dir.glob("*.mp4")):
        raise HTTPException(status_code=404, detail="还没有生成片段")
    try:
        create_clips_zip(job.root, job.paths.clips_dir, job.paths.clips_zip)
    except StorageError as exc:
        # 磁盘写满等存储问题：告诉老板真正原因，而不是报 500
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    return _range_response(job.paths.clips_zip, request, download=True)


@router.post("/jobs/{job_id}/export-all")
def export_all_segments(job_id: str, request: Request) -> dict:
    """导出全部片段（成片 + 候选库）：后台逐个渲染，前端轮询进度。"""

    manager = get_manager(request)
    try:
        manager.export_all(job_id)
        return manager.snapshot(job_id)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/export-all.zip")
def download_export_all(job_id: str, request: Request) -> StreamingResponse:
    """下载“导出全部片段”的打包结果（含候选库）。"""

    manager = get_manager(request)
    try:
        job = manager.get_job(job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not job.paths.export_zip.exists():
        raise HTTPException(
            status_code=404, detail="还没有导出结果，请先点击“导出全部片段（含候选库）”"
        )
    return _range_response(job.paths.export_zip, request, download=True)
