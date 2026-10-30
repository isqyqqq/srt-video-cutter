from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SRT_CUTTER_DATA", str(tmp_path / "data"))
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, video_path, srt_path):
    with video_path.open("rb") as video_file, srt_path.open("rb") as srt_file:
        return client.post(
            "/api/jobs",
            files={
                "video": ("source.mp4", video_file, "video/mp4"),
                "srt": ("source.srt", srt_file, "text/plain"),
            },
        )


def test_health(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["ffmpeg"] != "unknown"


def test_full_flow(client: TestClient, short_media):
    video, srt = short_media
    response = upload(client, video, srt)
    assert response.status_code == 200, response.text

    job = response.json()
    job_id = job["id"]
    assert job["srt"]["count"] == 2
    assert job["video"]["has_audio"] is True

    response = client.post(
        f"/api/jobs/{job_id}/plan",
        json={"pad_before": 0, "pad_after": 0},
    )
    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    assert len(plan["items"]) == 2
    assert plan["total_frames"] == 45
    assert plan["total_duration"] == pytest.approx(1.5, abs=1e-6)

    response = client.post(
        f"/api/jobs/{job_id}/start",
        json={"quality": "standard", "mode": "precise", "audio_fade_ms": 0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"

    completed = None
    for _ in range(120):
        snapshot = client.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in {"completed", "failed", "cancelled"}:
            completed = snapshot
            break
        time.sleep(0.5)

    assert completed is not None, "任务在 60 秒内没有结束"
    assert completed["status"] == "completed", completed.get("error")
    assert completed["progress"] == 1.0

    files = completed["files"]
    merged = next(item for item in files if item["kind"] == "merged")
    clips = [item for item in files if item["kind"] == "clip"]
    assert merged["name"] == "merged.mp4"
    assert len(clips) == 2
    assert any(item["kind"] == "zip" for item in files)
    assert completed["output_dir"].endswith(job_id)

    # 预览支持 Range，浏览器才能拖动进度条
    response = client.get(
        f"/api/jobs/{job_id}/preview/merged.mp4",
        headers={"Range": "bytes=0-99"},
    )
    assert response.status_code == 206
    assert response.headers["content-range"].startswith("bytes 0-99/")
    assert len(response.content) == 100

    response = client.get(f"/api/jobs/{job_id}/download/merged.mp4")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]

    response = client.get(f"/api/jobs/{job_id}/clips.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
    assert len([name for name in names if name.endswith(".mp4")]) == 2

    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_invalid_srt_returns_400(client: TestClient, short_media):
    video, _ = short_media
    bad_srt = video.parent / "bad.srt"
    bad_srt.write_text("这不是 SRT", encoding="utf-8")

    response = upload(client, video, bad_srt)
    assert response.status_code == 400
    assert "SRT" in response.json()["detail"]


def test_plan_validation_rejects_negative_padding(client: TestClient, short_media):
    video, srt = short_media
    job = upload(client, video, srt).json()
    response = client.post(
        f"/api/jobs/{job['id']}/plan",
        json={"pad_before": -1, "pad_after": 0},
    )
    assert response.status_code == 422
