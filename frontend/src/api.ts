import type { Health, JobSnapshot, LlmStatus, StartOptions } from "./types";

/** 统一的错误提取，尽量把后端的中文原因显示给用户 */
async function parseError(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) {
      return data.detail.map((item: { msg?: string }) => item.msg ?? "").join("；");
    }
    return JSON.stringify(data);
  } catch {
    return `请求失败（HTTP ${response.status}）`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as T;
}

/** 用 XHR 上传，才能拿到真实的上传进度 */
export function uploadJob(
  video: File,
  srt: File,
  onProgress: (fraction: number) => void,
): Promise<JobSnapshot> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("video", video);
    form.append("srt", srt);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress(event.loaded / event.total);
      }
    };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText || "{}");
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data as JobSnapshot);
        } else {
          const detail =
            typeof data?.detail === "string"
              ? data.detail
              : `上传失败（HTTP ${xhr.status}）`;
          reject(new Error(detail));
        }
      } catch {
        reject(new Error(`上传失败（HTTP ${xhr.status}）`));
      }
    };
    xhr.onerror = () => reject(new Error("网络错误，上传失败"));
    xhr.send(form);
  });
}

export function planJob(
  jobId: string,
  padBefore: number,
  padAfter: number,
): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pad_before: padBefore, pad_after: padAfter }),
  });
}

/** 让模型基于完整 SRT 生成多个开头钩子 */
export function generateHooks(
  jobId: string,
  count = 4,
  force = false,
): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/hooks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count, force }),
  });
}

/** 按选中的钩子生成切片 SRT，并自动规划切点 */
export function generateClipSrt(
  jobId: string,
  hookId: string,
): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/clip-srt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hook_id: hookId }),
  });
}

/** 放弃切片，恢复到上传的完整 SRT */
export function resetClip(jobId: string): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/reset-clip`, {
    method: "POST",
  });
}

/** 按片段 id 顺序调整成片（替换/排序/删除/添加） */
export function composeJob(
  jobId: string,
  segmentIds: string[],
): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/composition`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_ids: segmentIds }),
  });
}

/** 当前大模型配置摘要（不含密钥） */
export function getLlmStatus(): Promise<LlmStatus> {
  return request<LlmStatus>("/api/llm");
}

export function startJob(
  jobId: string,
  options: StartOptions,
): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
}

export function getJob(jobId: string): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}`);
}

export function cancelJob(jobId: string): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}

export function deleteJob(jobId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/jobs/${jobId}`, { method: "DELETE" });
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function previewUrl(jobId: string, path: string): string {
  return `/api/jobs/${jobId}/preview/${encodePath(path)}`;
}

/** 片段快预览地址：后端按需渲染 720p 小样并缓存 */
export function segmentPreviewUrl(jobId: string, segmentId: string): string {
  return `/api/jobs/${jobId}/segment-preview/${encodeURIComponent(segmentId)}`;
}

export function downloadUrl(jobId: string, path: string): string {
  return `/api/jobs/${jobId}/download/${encodePath(path)}`;
}

export function zipUrl(jobId: string): string {
  return `/api/jobs/${jobId}/clips.zip`;
}

/** 触发“导出全部片段（含候选库）”，返回最新任务快照 */
export function exportAllSegments(jobId: string): Promise<JobSnapshot> {
  return request<JobSnapshot>(`/api/jobs/${jobId}/export-all`, {
    method: "POST",
  });
}

/** “导出全部片段（含候选库）”的下载地址 */
export function exportAllUrl(jobId: string): string {
  return `/api/jobs/${jobId}/export-all.zip`;
}
