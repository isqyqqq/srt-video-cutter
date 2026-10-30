"""接口级测试：上传 → 生成钩子 → 选钩子生成切片 SRT。"""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import clip_writer
from app.jobs import JobManager
from app.main import create_app


def _fake_chat(messages: list[dict]) -> str:
    """根据提示词内容返回钩子或切片 JSON，避免测试依赖真实模型。"""

    prompt = messages[-1]["content"]
    if "开头钩子" in prompt:
        payload = {
            "hooks": [
                {"a": 1, "b": 2, "type": "价格锚", "reason": "价格锚点"},
                {"a": 2, "b": 3, "type": "痛点", "reason": "痛点"},
                {"a": 3, "b": 3, "type": "反差", "reason": "反差"},
            ]
        }
    else:
        payload = {
            "memory_point": "测试记忆点",
            "segments": [
                {"a": 1, "b": 2, "step": "钩子", "reason": "开场"},
                {"a": 3, "b": 3, "step": "结尾", "reason": "收尾"},
            ],
            # 候选片段库：第 4 条单独作为可替换片段（与成片不重叠）
            "spare": [
                {"a": 4, "b": 4, "step": "价值", "reason": "可替换"},
            ],
        }
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture()
def ordered_srt(tmp_path):
    """一份顺序正常的四条字幕，方便验证时间戳区间与备用片段。"""

    path = tmp_path / "ordered.srt"
    path.write_text(
        "1\n00:00:00,500 --> 00:00:01,200\n第一段\n\n"
        "2\n00:00:01,300 --> 00:00:02,000\n第二段\n\n"
        "3\n00:00:02,100 --> 00:00:02,900\n第三段\n\n"
        "4\n00:00:03,000 --> 00:00:03,500\n第四段\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SRT_CUTTER_DATA", str(tmp_path / "data"))
    # 测试视频只有 4 秒，放宽时长上限，避免为了测试造一段长视频
    monkeypatch.setattr(clip_writer, "MIN_DURATION_HARD", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_HARD", 30.0)
    monkeypatch.setattr(clip_writer, "MIN_DURATION_RECOMMENDED", 0.1)
    monkeypatch.setattr(clip_writer, "MAX_DURATION_RECOMMENDED", 30.0)

    app = create_app(lambda data_dir: JobManager(data_dir, chat_fn=_fake_chat))
    with TestClient(app) as test_client:
        yield test_client


def _upload(client: TestClient, video_path, srt_path):
    with video_path.open("rb") as video_file, srt_path.open("rb") as srt_file:
        return client.post(
            "/api/jobs",
            files={
                "video": ("source.mp4", video_file, "video/mp4"),
                "srt": ("source.srt", srt_file, "text/plain"),
            },
        )


def test_hooks_then_clip_srt(client, short_media, ordered_srt):
    video, _ = short_media
    srt = ordered_srt
    response = _upload(client, video, srt)
    assert response.status_code == 200, response.text
    job = response.json()
    job_id = job["id"]
    assert job["generation"]["status"] == "idle"
    assert job["clip_srt"] is None

    response = client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert len(snapshot["hooks"]) == 3
    assert snapshot["generation"]["status"] == "hooks_ready"
    assert snapshot["hooks"][0]["text"] == "第一段第二段"

    response = client.post(
        f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"}
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["generation"]["status"] == "clip_ready"
    assert snapshot["clip_srt"]["count"] == 2
    assert snapshot["selected_hook_id"] == "h1"
    assert snapshot["plan"] is not None
    assert snapshot["srt"]["count"] == 4  # 完整 SRT 条数不变
    assert snapshot["srt"]["active_count"] == 2

    # 切片 SRT 的条数、时间戳都沿用原片
    clip_text = snapshot["clip_srt"]["text"]
    assert "00:00:00,500 --> 00:00:02,000" in clip_text
    assert "00:00:02,100 --> 00:00:02,900" in clip_text

    download = client.get(f"/api/jobs/{job_id}/download/clip.srt")
    assert download.status_code == 200
    assert "第一段" in download.text

    files = [item for item in snapshot["files"] if item["kind"] == "clip_srt"]
    assert files and files[0]["path"] == "clip.srt"

    # 恢复完整字幕：切点和切片文件都要清掉
    response = client.post(f"/api/jobs/{job_id}/reset-clip")
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["clip_srt"] is None
    assert snapshot["plan"] is None
    assert snapshot["status"] == "uploaded"
    assert snapshot["srt"]["active_count"] == snapshot["srt"]["count"]


def test_invalid_hook_id(client, short_media, ordered_srt):
    video, _ = short_media
    srt = ordered_srt
    job_id = _upload(client, video, srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})

    response = client.post(
        f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "不存在"}
    )
    assert response.status_code == 400
    assert "开头钩子" in response.json()["detail"]


def test_compose_reorder_and_replace(client, short_media, ordered_srt):
    """片段库 + 成片编排：替换、重排、删除后切点跟随。"""

    video, _ = short_media
    srt = ordered_srt
    job_id = _upload(client, video, srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    snapshot = client.post(
        f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"}
    ).json()

    # 生成后应有片段库与成片编排，且成片 id 为 s1,s2
    library = snapshot["library"]
    assert any(item["origin"] == "final" for item in library)
    assert any(item["origin"] == "library" for item in library)
    assert snapshot["composition"] == ["s1", "s2"]

    # 调整顺序：倒序
    response = client.post(
        f"/api/jobs/{job_id}/composition", json={"segment_ids": ["s2", "s1"]}
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["composition"] == ["s2", "s1"]
    # 切点表顺序应跟随编排顺序（第一条是 s2 对应的第 3 条字幕）
    assert snapshot["plan"]["items"][0]["srt_start"] == 2.1

    # 替换：用备用片段 c1（第 2 条）替换成片第 2 位
    response = client.post(
        f"/api/jobs/{job_id}/composition", json={"segment_ids": ["s2", "c1"]}
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["composition"] == ["s2", "c1"]
    assert snapshot["plan"] is not None

    # 删除：只保留一段
    response = client.post(
        f"/api/jobs/{job_id}/composition", json={"segment_ids": ["s1"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["composition"] == ["s1"]


def test_compose_rejects_empty_and_unknown(client, short_media, ordered_srt):
    video, _ = short_media
    srt = ordered_srt
    job_id = _upload(client, video, srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})

    # 未知 id
    response = client.post(
        f"/api/jobs/{job_id}/composition", json={"segment_ids": ["不存在"]}
    )
    assert response.status_code == 400
    assert "片段 id" in response.json()["detail"]


def test_llm_endpoint(client):
    response = client.get("/api/llm")
    assert response.status_code == 200
    assert "provider" in response.json()


def _wait_export(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    """轮询等待后台导出线程结束，返回最终快照。"""

    deadline = time.time() + timeout
    snapshot = client.get(f"/api/jobs/{job_id}").json()
    while time.time() < deadline:
        if snapshot["export"]["status"] != "running":
            return snapshot
        time.sleep(0.2)
        snapshot = client.get(f"/api/jobs/{job_id}").json()
    raise AssertionError("导出在 60 秒内没有结束")


def test_export_all_includes_spare(client, short_media, ordered_srt):
    """导出全部片段：成片 + 候选库都要渲染进包里，并附带文本清单。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    snapshot = client.post(
        f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"}
    ).json()

    # 生成后应至少有 2 个成片段 + 1 个候选段
    library = snapshot["library"]
    expected = len(library)
    assert any(item["origin"] == "library" for item in library)

    # 触发导出：接口立即返回 running 状态
    response = client.post(f"/api/jobs/{job_id}/export-all")
    assert response.status_code == 200, response.text
    assert response.json()["export"]["status"] in {"running", "ready"}

    snapshot = _wait_export(client, job_id)
    assert snapshot["export"]["status"] == "ready", snapshot["export"]
    assert snapshot["export"]["count"] == expected
    assert snapshot["export"]["ready"] is True

    # 下载 zip：应含全部 mp4 + 清单，且清单里区分“成片/候选”
    response = client.get(f"/api/jobs/{job_id}/export-all.zip")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        manifest = archive.read("all_segments.txt").decode("utf-8")
    clips = [name for name in names if name.startswith("clips/") and name.endswith(".mp4")]
    assert len(clips) == expected
    assert "候选" in manifest and "成片" in manifest
    assert "编号\t来源" in manifest


def test_export_all_requires_library(client, short_media, ordered_srt):
    """还没生成片段库时，导出应返回 400 而不是 500。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]

    response = client.post(f"/api/jobs/{job_id}/export-all")
    assert response.status_code == 400
    assert "候选片段库" in response.json()["detail"]


def test_export_all_zip_404_before_export(client, short_media, ordered_srt):
    """未导出时下载 zip 应返回 404，并给出中文提示。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})

    response = client.get(f"/api/jobs/{job_id}/export-all.zip")
    assert response.status_code == 404
    assert "导出" in response.json()["detail"]


def test_regenerate_clears_export(client, short_media, ordered_srt):
    """重新生成切片后，旧的导出产物与状态要被清掉。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})
    client.post(f"/api/jobs/{job_id}/export-all")
    snapshot = _wait_export(client, job_id)
    assert snapshot["export"]["ready"] is True

    # 重新生成：导出状态归零、zip 消失
    snapshot = client.post(
        f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"}
    ).json()
    assert snapshot["export"]["status"] == "idle"
    assert snapshot["export"]["ready"] is False
    assert client.get(f"/api/jobs/{job_id}/export-all.zip").status_code == 404


def test_compose_insert_in_middle(client, short_media, ordered_srt):
    """在成片中间插入候选片段：只把该段插到指定位置，其余顺序不变。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})

    # 原成片是 [s1, s2]，把候选 c1 插到第 1 条后面 → [s1, c1, s2]
    response = client.post(
        f"/api/jobs/{job_id}/composition",
        json={"segment_ids": ["s1", "c1", "s2"]},
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["composition"] == ["s1", "c1", "s2"]
    # 切点表顺序跟随编排：第 2 条应是候选段（第 4 条字幕，2.9~3.5 秒）
    assert snapshot["plan"]["items"][1]["srt_start"] == 3.0
    assert len(snapshot["plan"]["items"]) == 3
    # 插入后 c1 已进成片，不应再出现在候选库里
    assert snapshot["library"][0]["id"] == "s1"


def test_segment_preview_renders_and_caches(client, short_media, ordered_srt):
    """片段快预览：第一次现场渲染 720p 小样，第二次直接命中缓存。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})

    # 用候选片段 c1 预览：成片前它还没有 mp4，必须现场渲染
    response = client.get(f"/api/jobs/{job_id}/segment-preview/c1")
    assert response.status_code == 200, response.text
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) > 0

    manager = client.app.state.manager
    job = manager.get_job(job_id)
    cached = list(job.paths.preview_dir.glob("c1_*.mp4"))
    assert len(cached) == 1
    first_mtime = cached[0].stat().st_mtime_ns

    # 第二次请求：应命中缓存，不再重新渲染（文件修改时间不变）
    response = client.get(f"/api/jobs/{job_id}/segment-preview/c1")
    assert response.status_code == 200
    assert cached[0].stat().st_mtime_ns == first_mtime


def test_segment_preview_unknown_id(client, short_media, ordered_srt):
    """预览不存在的片段：返回 400 + 中文原因。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})

    response = client.get(f"/api/jobs/{job_id}/segment-preview/不存在")
    assert response.status_code == 400
    assert "不存在" in response.json()["detail"]


def test_regenerate_clears_previews(client, short_media, ordered_srt):
    """重新生成切片后，预览缓存要清掉，避免播到旧时间点的画面。"""

    video, _ = short_media
    job_id = _upload(client, video, ordered_srt).json()["id"]
    client.post(f"/api/jobs/{job_id}/hooks", json={"count": 3})
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})
    client.get(f"/api/jobs/{job_id}/segment-preview/c1")

    manager = client.app.state.manager
    job = manager.get_job(job_id)
    assert len(list(job.paths.preview_dir.glob("*.mp4"))) == 1

    # 重新生成切片：预览缓存应被清空
    client.post(f"/api/jobs/{job_id}/clip-srt", json={"hook_id": "h1"})
    assert len(list(job.paths.preview_dir.glob("*.mp4"))) == 0
