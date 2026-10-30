/** 把秒格式化为 HH:MM:SS.mmm */
export function formatTc(seconds: number): string {
  const total = Math.max(0, Math.round(seconds * 1000));
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor((total % 3_600_000) / 60_000);
  const secs = Math.floor((total % 60_000) / 1000);
  const millis = total % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(
    2,
    "0",
  )}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

/** 把秒格式化为 1分23.4秒 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(2)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes} 分 ${rest.toFixed(1)} 秒`;
}

/** 把字节格式化为易读文本 */
export function formatBytes(size: number): string {
  let value = size;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (value < 1024 || unit === "GB") {
      return `${value.toFixed(1)} ${unit}`;
    }
    value /= 1024;
  }
  return `${value.toFixed(1)} GB`;
}

/** 状态对应的中文文案 */
export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    uploaded: "已上传",
    planned: "切点已生成",
    queued: "排队中",
    processing: "剪辑中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}
