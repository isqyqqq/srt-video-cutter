# 片段库 + 成片编排 设计文档

- 版本：v1.2
- 日期：2026-10-28
- 状态：已实现

## 1. 需求

在原有「AI 生成切片 SRT → 按时间戳剪辑」的基础上，增加：

1. **候选片段库**：AI 生成成片时，同步产出所有满足剪辑要求的有用片段（比成片本身更宽）。
2. **成片编排**：用户可在候选片段库中替换成片里的某一段，也可调整成片顺序。

## 2. 核心设计

原有数据流：`generate_clip → segments(固定成片) → clip.srt → cues → plan → start → ffmpeg`。

关键洞察：`job.cues` 是一切的源头，`plan` 从 `cues` 计算切点。只要按「用户选定的片段集合与顺序」重建 `cues` 与 `clip.srt` 再重算 `plan`，整条剪辑链路即可自动复用，无需改 ffmpeg。

因此新增统一模型：

- `library`：候选片段库，每项带唯一 `id`。成片段用 `s1、s2…`，备用库段用 `c1、c2…`。
- `composition`：当前成片的片段 `id` 顺序数组。

所有编排操作都归约为「给一个 id 顺序数组」：

| 操作 | 对 composition 的变换 |
|---|---|
| 调整顺序 | 交换/移动 id 位置 |
| 删除片段 | 移除某个 id |
| 替换片段 | 用库 id 替换某个成片 id |
| 添加片段 | 在末尾/中间插入库 id |

## 3. 后端改动

### 3.1 数据结构（Job）

```python
library: list[dict]      # [{id,a,b,step,reason,start,end,duration,text,origin}]
composition: list[str]   # 成片片段 id 顺序
```

`origin ∈ {"final", "library"}`：final 为模型建议成片段，library 为备用候选段。

### 3.2 片段库生成

`clip.md` 提示词新增 `spare` 字段：在成片 `segments` 之外，再给 6~12 个「未进成片但有用」的候选片段。

`clip_writer.generate_clip`：
- 解析 `spare`，单独校验（区间合法、不与 segments 重叠、spare 间不重叠、单段 ≤12s），不合格项跳过并警告，不影响成片。
- `ClipResult` 新增 `spare: list[Segment]`。
- 模型没给 spare 时降级为空，前端仅无替换项，功能仍可用。

### 3.3 编排接口

`POST /api/jobs/{id}/composition  { segment_ids: [str, ...] }`

`JobManager.compose(job_id, segment_ids)`：
1. 校验每个 id 在 `library` 中，空数组报错。
2. 按顺序取片段 → 重建 `RawCue`（index 按 composition 顺序 1..N）→ 写 `clip.srt`。
3. 更新 `job.cues / job.composition / job.clip_meta / job.clip_text`，清旧产物。
4. 重新 `plan(0,0)`，前端拿到可直接开剪的状态。

> 因 plan 输出按 cue.index 排序，赋 index=1..N 即保证成片顺序 = composition 顺序。

### 3.4 兼容

- 手动重排模式：不生成 library/composition，前端不显示编排器。
- `reset-clip` 同时清空 library/composition。

## 4. 前端改动

- `types.ts`：`JobSnapshot` 增 `library: LibraryItem[]`、`composition: string[]`。
- `api.ts`：`composeJob(jobId, segmentIds)`。
- 新组件 `CompositionEditor`：
  - 上半「成片编排」：每行显示序号/文案/时长，支持上移、下移、删除、替换（下拉选库片段）。
  - 下半「候选片段库」：显示未被使用的库片段，支持"添加到最后"。
  - 每次操作算出新 id 数组，调用 `onCompose`。
- `App.tsx`：`handleCompose` 调接口并刷新 job。

## 5. 验收

- AI 生成后 `library` 含成片段与备用段，`composition` 为成片段 id 顺序。
- 替换/上移/下移/删除/添加后，切点表与成片按新顺序生成。
- 手动模式无编排器，旧流程不受影响。
- 现有测试全绿，新增 library 与 compose 用例。

## 6. 导出全部片段（含候选库）v1.3

### 6.1 与「下载片段」的区别

| 功能 | 内容 | 入口 |
|---|---|---|
| 下载片段（原有） | 只含**成片**切出来的 `clips/*.mp4` | `GET /clips.zip` |
| 导出全部片段（新） | **成片段 + 候选库段** 全部渲染 | `POST /export-all` |

原「下载片段」保持不动，两者并存。

### 6.2 后端

- `storage.JobPaths` 新增 `export_dir = <root>/export_all`、`export_zip = <root>/all_segments.zip`。
- `storage.create_clips_zip(..., extras=[manifest])`：额外文件放到压缩包根目录（清单不进 `clips/`）。
- `Job` 新增 `export_status / export_progress / export_message / export_error / export_count`，
  `to_dict()` 里输出 `export: {status, progress, message, error, count, ready}`。
- `JobManager.export_all(job_id)`：校验（非处理中、`library` 非空、没在跑），起后台线程。
- `JobManager._export_worker()`：遍历 `job.library` 全部项（成片 + 候选），
  用 `build_clip_command` 逐段渲染为
  `{序号}_{id}_{成片|候选}_{step}_{时长}s.mp4`，
  同时写 `all_segments.txt` 清单，最后一并打包成 `all_segments.zip`。
- `_clear_export()`：重新生成切片、`reset-clip` 时清掉导出产物与状态，避免新旧文件混淆。

### 6.3 接口

- `POST /api/jobs/{id}/export-all`：触发导出，立即返回任务快照（不阻塞）。
- `GET  /api/jobs/{id}/export-all.zip`：下载结果；未导出时 404 中文提示。
- `POST` 在片段库为空时返回 400「还没有候选片段库，请先生成切片」。

### 6.4 前端

- `CompositionEditor` 新增「导出」面板：进度条、开始/重新导出、下载按钮、失败提示。
- `App.tsx`：`export.status === "running"` 时每 1 秒轮询 `GET /api/jobs/{id}`，完成后自动停。

### 6.5 验收

- `pytest`：`test_export_all_includes_spare`、`test_export_all_requires_library`、
  `test_export_all_zip_404_before_export`、`test_regenerate_clears_export`。
- zip 内 mp4 数量 = `library` 项数，清单含「成片/候选」两类。
