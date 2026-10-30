"""任务目录与文件管理。"""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from pathlib import Path
import zipfile

from fastapi import UploadFile


class StorageError(RuntimeError):
    """磁盘写入失败（空间不足等），message 使用中文。"""


@dataclass
class JobPaths:
    root: Path
    input_dir: Path
    clips_dir: Path
    export_dir: Path
    preview_dir: Path
    merged: Path
    plan_json: Path
    result_json: Path
    log_file: Path
    list_file: Path
    clips_zip: Path
    export_zip: Path
    clip_srt: Path
    hooks_json: Path


def create_job_paths(root: Path) -> JobPaths:
    """创建任务目录结构。"""

    input_dir = root / "input"
    clips_dir = root / "clips"
    export_dir = root / "export_all"
    # 片段快预览的缓存目录（按需生成的 720p 小样，可随时清）
    preview_dir = root / "previews"
    input_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    return JobPaths(
        root=root,
        input_dir=input_dir,
        clips_dir=clips_dir,
        export_dir=export_dir,
        preview_dir=preview_dir,
        merged=root / "merged.mp4",
        plan_json=root / "plan.json",
        result_json=root / "result.json",
        log_file=root / "job.log",
        list_file=root / "concat.txt",
        clips_zip=root / "clips.zip",
        export_zip=root / "all_segments.zip",
        clip_srt=root / "clip.srt",
        hooks_json=root / "hooks.json",
    )


def save_upload(upload: UploadFile, destination: Path) -> int:
    """把上传文件分块写入磁盘，避免大文件占满内存。"""

    total = 0
    with destination.open("wb") as handle:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            total += len(chunk)
    return total


def resolve_inside(base: Path, relative: str) -> Path:
    """解析相对路径并确保不会跳出任务目录。"""

    base_resolved = base.resolve()
    target = (base_resolved / relative).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError("非法文件路径")
    return target


def create_clips_zip(
    job_root: Path,
    clips_dir: Path,
    destination: Path,
    extras: list[Path] | None = None,
) -> Path:
    """把 clips 目录打包成 zip。

    先写到临时文件再改名，避免磁盘写满时留下半截的 zip（下次下载会拿到残包）。
    磁盘空间不够时抛 StorageError，由接口层翻译成中文提示。
    extras 里的额外文件（如清单）会放到压缩包根目录。
    """

    clips = sorted(clips_dir.glob("*.mp4"))
    extra_files = [path for path in (extras or []) if path.is_file()]
    if not clips and not extra_files:
        raise StorageError("还没有生成片段，无法打包下载。")

    destination.parent.mkdir(parents=True, exist_ok=True)
    need = sum(path.stat().st_size for path in clips + extra_files)
    free = shutil.disk_usage(destination.parent).free
    # zip 压缩后一般更小，但给 1.1 倍余量，保证临时文件也写得下
    if free < need * 1.1:
        raise StorageError(
            f"磁盘空间不足：需要约 {human_size(int(need * 1.1))}，只剩 {human_size(free)}。"
            "请先清理 data/jobs 下的旧任务再试。"
        )

    temp = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for clip in clips:
                archive.write(clip, arcname=f"clips/{clip.name}")
            for extra in extra_files:
                archive.write(extra, arcname=extra.name)
        temp.replace(destination)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise StorageError(f"打包片段失败：{exc}") from exc
    return destination


def human_size(size: int) -> str:
    """把字节数转换成易读文本。"""

    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def prune_jobs(jobs_dir: Path, keep: int, protect: set[str] | None = None) -> list[str]:
    """只保留最新的 keep 个任务目录，其余删掉，返回被删的任务 id。

    每个任务都会存一份完整源视频（动辄 1.6G），不清理会很快把磁盘吃满。
    keep 小于 1 时按 1 处理；protect 中的 id 永不删（例如当前选中的任务）。
    """

    keep = max(1, keep)
    protect = protect or set()
    if not jobs_dir.is_dir():
        return []

    dirs = [path for path in jobs_dir.iterdir() if path.is_dir()]
    # 按修改时间从新到旧排序，最新的优先保留
    dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    removed: list[str] = []
    for position, path in enumerate(dirs):
        in_quota = position < keep
        if in_quota or path.name in protect:
            continue
        delete_job_dir(path)
        removed.append(path.name)
    return removed


def delete_job_dir(root: Path) -> None:
    """删除整个任务目录。"""

    shutil.rmtree(root, ignore_errors=True)
