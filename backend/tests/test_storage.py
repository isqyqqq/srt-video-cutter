"""存储层测试：旧任务自动清理、打包失败的中文提示。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import storage


def _make_job(jobs_dir: Path, job_id: str, *, mtime: float, size: int = 16) -> Path:
    """造一个假任务目录，带可控的修改时间和一个占位文件。"""

    root = jobs_dir / job_id
    (root / "clips").mkdir(parents=True)
    (root / "input").mkdir(parents=True)
    (root / "input" / "source.mov").write_bytes(b"x" * size)
    os.utime(root, (mtime, mtime))
    return root


def test_prune_jobs_keeps_newest(tmp_path: Path):
    """只保留最新的 keep 个，其余删掉。"""

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    for index in range(5):
        _make_job(jobs_dir, f"job{index}", mtime=1_000 + index)

    removed = storage.prune_jobs(jobs_dir, keep=2)

    remaining = sorted(path.name for path in jobs_dir.iterdir())
    assert remaining == ["job3", "job4"]
    assert sorted(removed) == ["job0", "job1", "job2"]


def test_prune_jobs_protects_active_job(tmp_path: Path):
    """被保护的任务即使很旧也不能删。"""

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    for index in range(4):
        _make_job(jobs_dir, f"job{index}", mtime=1_000 + index)

    removed = storage.prune_jobs(jobs_dir, keep=1, protect={"job0"})

    remaining = sorted(path.name for path in jobs_dir.iterdir())
    assert "job0" in remaining
    assert "job3" in remaining
    assert set(removed) == {"job1", "job2"}


def test_prune_jobs_keep_at_least_one(tmp_path: Path):
    """keep 传入 0 也至少保留一个，避免把当前任务也删掉。"""

    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    _make_job(jobs_dir, "a", mtime=1_000)
    _make_job(jobs_dir, "b", mtime=2_000)

    storage.prune_jobs(jobs_dir, keep=0)

    assert len(list(jobs_dir.iterdir())) == 1


def test_prune_jobs_missing_dir_is_noop(tmp_path: Path):
    assert storage.prune_jobs(tmp_path / "nope", keep=3) == []


def test_create_clips_zip_refuses_when_disk_full(tmp_path: Path, monkeypatch):
    """磁盘空间不足时抛中文 StorageError，而不是写出半截 zip。"""

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    (clips_dir / "001.mp4").write_bytes(b"x" * 1024)

    class _Usage:
        free = 10  # 只剩 10 字节，肯定不够

    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _path: _Usage)

    with pytest.raises(storage.StorageError) as excinfo:
        storage.create_clips_zip(tmp_path, clips_dir, tmp_path / "clips.zip")

    assert "磁盘空间不足" in str(excinfo.value)
    assert not (tmp_path / "clips.zip").exists()
    assert not (tmp_path / "clips.zip.tmp").exists()


def test_create_clips_zip_writes_archive(tmp_path: Path):
    """正常情况能打包，且包含所有片段。"""

    import zipfile

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    (clips_dir / "001.mp4").write_bytes(b"a" * 100)
    (clips_dir / "002.mp4").write_bytes(b"b" * 100)

    target = storage.create_clips_zip(tmp_path, clips_dir, tmp_path / "clips.zip")

    assert target.exists()
    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == ["clips/001.mp4", "clips/002.mp4"]


def test_create_clips_zip_requires_clips(tmp_path: Path):
    """没有片段时给明确提示。"""

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()

    with pytest.raises(storage.StorageError) as excinfo:
        storage.create_clips_zip(tmp_path, clips_dir, tmp_path / "clips.zip")

    assert "还没有生成片段" in str(excinfo.value)


def test_keep_jobs_env(monkeypatch):
    """SRT_CUTTER_KEEP_JOBS 可覆盖保留数量，非法值回退默认。"""

    from app import jobs

    monkeypatch.setenv("SRT_CUTTER_KEEP_JOBS", "5")
    assert jobs._env_keep_jobs() == 5
    monkeypatch.setenv("SRT_CUTTER_KEEP_JOBS", "abc")
    assert jobs._env_keep_jobs(default=3) == 3
    monkeypatch.setenv("SRT_CUTTER_KEEP_JOBS", "0")
    assert jobs._env_keep_jobs() == 1
    monkeypatch.delenv("SRT_CUTTER_KEEP_JOBS")
    assert jobs._env_keep_jobs() == 3
