import { useMemo, useRef, useState } from "react";
import type { JobSnapshot, LibraryItem } from "../types";
import { exportAllUrl } from "../api";
import { formatTc } from "../utils";
import {
  ChevronDownIcon,
  ClockIcon,
  DownloadIcon,
  FilmIcon,
  GripIcon,
  LoaderIcon,
  PackageIcon,
  RefreshIcon,
  ScissorsIcon,
  SearchIcon,
  TrashIcon,
  XIcon,
} from "./Icons";

interface Props {
  job: JobSnapshot;
  busy: boolean;
  /** 提交新的片段 id 顺序；父组件负责调用后端 */
  onCompose: (segmentIds: string[]) => void;
  /** 导出全部片段（含候选库） */
  onExportAll: () => void;
}

/** 按关键词筛选片段。
 *
 * 规则：用空格分隔多个关键词，需要全部命中（AND），匹配范围是文案和步骤名，
 * 这样既能搜“中裤 松弛”，也能直接搜类型的“赞美”“场景”。
 */
function filterItems(items: LibraryItem[], query: string): LibraryItem[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return items;
  return items.filter((item) => {
    const haystack = `${item.text} ${item.step}`.toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

/** 片段搜索框：替换面板与候选库共用 */
function SegmentSearch({
  value,
  onChange,
  placeholder,
  matched,
  total,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  matched: number;
  total: number;
}) {
  const active = value.trim().length > 0;
  return (
    <div className="flex items-center gap-1.5">
      <div className="relative flex-1">
        <SearchIcon
          size={13}
          className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          type="search"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className="w-full rounded-lg border border-slate-200 bg-white py-1 pl-7 pr-6 text-[11px] text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100"
        />
        {active ? (
          <button
            type="button"
            onClick={() => onChange("")}
            title="清空搜索"
            className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-300 hover:text-slate-500"
          >
            <XIcon size={11} />
          </button>
        ) : null}
      </div>
      <span className="shrink-0 text-[10px] text-slate-400">
        {active ? `${matched}/${total}` : `${total} 个`}
      </span>
    </div>
  );
}

/** 一行片段的小卡片，用于成片列表与片段库 */
function SegmentRow({
  item,
  index,
  total,
  busy,
  onMoveUp,
  onMoveDown,
  onRemove,
  onReplace,
  replaceCandidates,
  showOrigin,
  onAppend,
  dragHandle,
  dragging,
  dragOver,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  item: LibraryItem;
  index?: number;
  total?: number;
  busy: boolean;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
  onRemove?: () => void;
  onReplace?: (newId: string) => void;
  replaceCandidates?: LibraryItem[];
  showOrigin?: boolean;
  onAppend?: () => void;
  /** 是否显示拖拽把手（仅成片列表行启用） */
  dragHandle?: boolean;
  dragging?: boolean;
  dragOver?: boolean;
  onDragStart?: (event: React.DragEvent) => void;
  onDragEnd?: () => void;
  onDragOver?: (event: React.DragEvent) => void;
  onDragLeave?: () => void;
  onDrop?: (event: React.DragEvent) => void;
}) {
  const [replacing, setReplacing] = useState(false);
  // 替换面板的关键词：只影响这里列出的候选，不影响成片数据
  const [replaceQuery, setReplaceQuery] = useState("");
  const rowRef = useRef<HTMLDivElement>(null);

  const visibleCandidates = useMemo(
    () => filterItems(replaceCandidates ?? [], replaceQuery),
    [replaceCandidates, replaceQuery],
  );

  // 拖动时把整行作为拖拽幽灵图，比只拖把手好看
  const handleDragStart = (event: React.DragEvent) => {
    event.dataTransfer.effectAllowed = "move";
    // 部分浏览器需要 setData 才会触发拖动
    event.dataTransfer.setData("text/plain", item.id);
    if (rowRef.current) {
      event.dataTransfer.setDragImage(rowRef.current, 20, 20);
    }
    onDragStart?.(event);
  };

  return (
    <div
      ref={rowRef}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`rounded-xl border bg-white px-3 py-2.5 transition ${
        dragging
          ? "border-indigo-300 opacity-50"
          : dragOver
            ? "border-indigo-400 ring-2 ring-indigo-200"
            : "border-slate-200"
      }`}
    >
      <div className="flex items-start gap-2">
        {dragHandle ? (
          <div
            draggable={!busy}
            onDragStart={handleDragStart}
            onDragEnd={onDragEnd}
            title="拖动排序"
            className={`-ml-1 flex h-7 w-5 shrink-0 items-center justify-center rounded text-slate-300 ${
              busy ? "cursor-not-allowed" : "cursor-grab hover:text-indigo-400 active:cursor-grabbing"
            }`}
          >
            <GripIcon size={16} />
          </div>
        ) : null}
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600">
          {index ?? "＋"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="badge bg-indigo-50 text-indigo-600">{item.step}</span>
            {showOrigin ? (
              <span
                className={`badge ${
                  item.origin === "final"
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-sky-50 text-sky-600"
                }`}
              >
                {item.origin === "final" ? "成片段" : "候选"}
              </span>
            ) : null}
            <span className="mono flex items-center gap-1 text-[11px] text-slate-400">
              <ClockIcon size={11} />
              {formatTc(item.start)} → {formatTc(item.end)} ·{" "}
              {item.duration.toFixed(1)}s
            </span>
          </div>
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-700">
            {item.text}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {onMoveUp ? (
            <button
              type="button"
              className="btn btn-ghost px-1.5 py-1 text-slate-400"
              disabled={busy || index === 1}
              onClick={onMoveUp}
              title="上移"
            >
              ↑
            </button>
          ) : null}
          {onMoveDown ? (
            <button
              type="button"
              className="btn btn-ghost px-1.5 py-1 text-slate-400"
              disabled={busy || index === total}
              onClick={onMoveDown}
              title="下移"
            >
              ↓
            </button>
          ) : null}
          {onReplace ? (
            <button
              type="button"
              className="btn btn-ghost px-1.5 py-1 text-[11px] text-indigo-500"
              disabled={busy || !replaceCandidates?.length}
              onClick={() => {
                setReplacing((value) => !value);
                setReplaceQuery("");
              }}
              title="用候选片段替换"
            >
              替换
            </button>
          ) : null}
          {onRemove ? (
            <button
              type="button"
              className="btn btn-ghost px-1.5 py-1 text-rose-400"
              disabled={busy}
              onClick={onRemove}
              title="删除"
            >
              <TrashIcon size={13} />
            </button>
          ) : null}
          {onAppend ? (
            <button
              type="button"
              className="btn btn-ghost px-2 py-1 text-[11px] text-indigo-500"
              disabled={busy}
              onClick={onAppend}
              title="添加到最后"
            >
              <RefreshIcon size={12} /> 添加
            </button>
          ) : null}
        </div>
      </div>

      {/* 替换下拉：从候选片段里挑一个替换当前行，支持关键词搜索 */}
      {replacing && onReplace ? (
        <div className="mt-2 space-y-1.5 rounded-lg border border-indigo-100 bg-indigo-50/40 p-2">
          <p className="text-[11px] text-indigo-600">选择一个候选片段替换当前片段：</p>
          {replaceCandidates && replaceCandidates.length ? (
            <>
              <SegmentSearch
                value={replaceQuery}
                onChange={setReplaceQuery}
                placeholder="搜文案或类型，多个词用空格分隔"
                matched={visibleCandidates.length}
                total={replaceCandidates.length}
              />
              {visibleCandidates.length ? (
                <div className="max-h-48 space-y-1.5 overflow-auto">
                  {visibleCandidates.map((candidate) => (
                    <button
                      key={candidate.id}
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        onReplace(candidate.id);
                        setReplacing(false);
                        setReplaceQuery("");
                      }}
                      className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-left text-[11px] transition hover:border-indigo-300 hover:bg-indigo-50"
                    >
                      <span className="badge bg-slate-100 text-slate-500">
                        {candidate.step}
                      </span>
                      <span className="mono ml-1.5 text-slate-400">
                        {candidate.duration.toFixed(1)}s
                      </span>
                      <p className="mt-0.5 line-clamp-2 text-slate-600">
                        {candidate.text}
                      </p>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-slate-200 bg-white px-2 py-3 text-center text-[11px] text-slate-400">
                  没有匹配“{replaceQuery.trim()}”的候选片段
                </p>
              )}
            </>
          ) : (
            <p className="text-[11px] text-slate-400">
              片段库中没有可替换的候选片段。
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}

export default function CompositionEditor({
  job,
  busy,
  onCompose,
  onExportAll,
}: Props) {
  const library = job.library ?? [];
  const composition = job.composition ?? [];
  const exportInfo = job.export;
  const [showLibrary, setShowLibrary] = useState(true);
  // 候选库关键词：只过滤展示，不影响成片与已选数据
  const [libraryQuery, setLibraryQuery] = useState("");
  // 拖拽排序状态：记录正在拖的行与悬停目标行
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  const byId = useMemo(() => {
    const map = new Map<string, LibraryItem>();
    for (const item of library) map.set(item.id, item);
    return map;
  }, [library]);

  const compositionItems = composition
    .map((id) => byId.get(id))
    .filter((item): item is LibraryItem => Boolean(item));

  // 未被成片使用的候选片段
  const usedIds = new Set(composition);
  const unusedItems = library.filter((item) => !usedIds.has(item.id));
  // 候选库的搜索结果
  const visibleLibrary = filterItems(unusedItems, libraryQuery);

  const totalDuration = compositionItems.reduce(
    (sum, item) => sum + item.duration,
    0,
  );

  if (!library.length || !composition.length) {
    return null;
  }

  /** 工具：交换两个位置 */
  const swap = (from: number, to: number) => {
    if (to < 0 || to >= composition.length) return;
    const next = [...composition];
    [next[from], next[to]] = [next[to], next[from]];
    onCompose(next);
  };

  /** 拖拽结束：把 from 位置的片段插到 to 位置（保持其余顺序） */
  const moveItem = (from: number, to: number) => {
    if (from === to || to < 0 || to >= composition.length) return;
    const next = [...composition];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onCompose(next);
  };

  const removeAt = (position: number) => {
    if (composition.length <= 1) return;
    const next = composition.filter((_, i) => i !== position);
    onCompose(next);
  };

  const replaceAt = (position: number, newId: string) => {
    const next = [...composition];
    next[position] = newId;
    onCompose(next);
  };

  const append = (id: string) => {
    onCompose([...composition, id]);
  };

  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <ScissorsIcon size={17} className="text-indigo-500" />
          <div>
            <h2 className="text-base font-semibold text-slate-900">成片编排</h2>
            <p className="mt-0.5 text-[11px] text-slate-500">
              拖动左侧把手可排序，也可上移/下移/删除，或从候选库替换
            </p>
          </div>
        </div>
        <span className="badge bg-indigo-50 text-indigo-600">
          <ClockIcon size={12} />
          {formatTc(totalDuration)} · {composition.length} 段
        </span>
      </div>

      <div className="card-body space-y-4">
        {/* 导出全部片段（含候选库） */}
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-800">
                <PackageIcon size={15} className="text-slate-400" />
                导出全部片段（含候选库）
              </p>
              <p className="mt-0.5 text-[11px] text-slate-500">
                把成片片段和候选库里的每一段都剪成独立 mp4，打包下载，附一份清单
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {exportInfo?.ready ? (
                <a
                  className="btn btn-secondary"
                  href={exportAllUrl(job.id)}
                  download
                >
                  <DownloadIcon size={16} />
                  下载全部片段包
                </a>
              ) : null}
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || exportInfo?.status === "running" || !library.length}
                onClick={onExportAll}
              >
                {exportInfo?.status === "running" ? (
                  <LoaderIcon size={16} />
                ) : (
                  <PackageIcon size={16} />
                )}
                {exportInfo?.status === "running"
                  ? "正在导出…"
                  : exportInfo?.ready
                    ? "重新导出"
                    : "开始导出"}
              </button>
            </div>
          </div>

          {exportInfo?.status === "running" ? (
            <div className="mt-2">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                <div
                  className="h-full rounded-full bg-indigo-500 transition-all"
                  style={{ width: `${Math.round((exportInfo.progress || 0) * 100)}%` }}
                />
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                {exportInfo.message || "正在导出…"}（
                {Math.round((exportInfo.progress || 0) * 100)}%）
              </p>
            </div>
          ) : null}

          {exportInfo?.status === "ready" ? (
            <p className="mt-1.5 text-[11px] text-emerald-600">
              ✓ {exportInfo.message || `已导出 ${exportInfo.count} 个片段`}
            </p>
          ) : null}

          {exportInfo?.status === "failed" ? (
            <p className="mt-1.5 text-[11px] text-rose-500">
              导出失败：{exportInfo.error || "未知错误"}
            </p>
          ) : null}
        </div>
        {/* 成片当前顺序 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-800">当前成片</h3>
            <span className="text-[11px] text-slate-400">
              共 {unusedItems.length} 个候选片段可替换
            </span>
          </div>
          <div className="space-y-2">
            {compositionItems.map((item, position) => (
              <SegmentRow
                key={`${item.id}-${position}`}
                item={item}
                index={position + 1}
                total={composition.length}
                busy={busy}
                dragHandle
                dragging={dragFrom === position}
                dragOver={dragOver === position && dragFrom !== position}
                onDragStart={() => setDragFrom(position)}
                onDragEnd={() => {
                  setDragFrom(null);
                  setDragOver(null);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (dragFrom !== null && dragFrom !== position) {
                    setDragOver(position);
                  }
                }}
                onDragLeave={() => {
                  setDragOver((current) => (current === position ? null : current));
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (dragFrom !== null) {
                    moveItem(dragFrom, position);
                  }
                  setDragFrom(null);
                  setDragOver(null);
                }}
                onMoveUp={() => swap(position, position - 1)}
                onMoveDown={() => swap(position, position + 1)}
                onRemove={() => removeAt(position)}
                onReplace={(newId) => replaceAt(position, newId)}
                replaceCandidates={unusedItems}
              />
            ))}
          </div>
        </div>

        {/* 候选片段库 */}
        <div className="space-y-2">
          <button
            type="button"
            className="flex w-full items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-left transition hover:bg-slate-100"
            onClick={() => setShowLibrary((value) => !value)}
          >
            <span className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <FilmIcon size={15} className="text-slate-400" />
              候选片段库（{unusedItems.length}）
            </span>
            <span
              className={`text-slate-400 transition ${
                showLibrary ? "rotate-180" : ""
              }`}
            >
              <ChevronDownIcon size={16} />
            </span>
          </button>

          {showLibrary ? (
            unusedItems.length ? (
              <div className="space-y-2">
                <SegmentSearch
                  value={libraryQuery}
                  onChange={setLibraryQuery}
                  placeholder="搜候选片段文案或类型，多个词用空格分隔"
                  matched={visibleLibrary.length}
                  total={unusedItems.length}
                />
                {visibleLibrary.length ? (
                  visibleLibrary.map((item) => (
                    <SegmentRow
                      key={item.id}
                      item={item}
                      busy={busy}
                      showOrigin
                      onAppend={() => append(item.id)}
                    />
                  ))
                ) : (
                  <p className="rounded-xl border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-slate-400">
                    没有匹配“{libraryQuery.trim()}”的候选片段
                  </p>
                )}
              </div>
            ) : (
              <p className="rounded-xl border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-slate-400">
                所有候选片段都已在成片中，或模型未提供额外候选片段。
              </p>
            )
          ) : null}
        </div>

        <p className="text-[11px] leading-relaxed text-slate-400">
          提示：拖动左侧把手可调整顺序；替换是「用候选片段换掉当前位置的片段」，不改变其他片段；
          搜索框支持多关键词（空格分隔，需全部命中），可搜文案或类型（如“场景”“赞美”）；
          顺序以本列表为准，与原始 SRT 顺序无关。
        </p>
      </div>
    </section>
  );
}
