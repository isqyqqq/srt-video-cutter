#!/usr/bin/env bash
set -euo pipefail

# 开发模式：后端 8765，前端 5173
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

# 停掉占用指定端口的旧服务（只针对 LISTEN 的服务端进程）
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return 0
  fi
  echo "端口 ${port} 被占用（PID: $(echo "$pids" | tr '\n' ' ')），正在停止旧服务..."
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i
  for ((i = 0; i < 50; i++)); do
    if ! lsof -ti "tcp:${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  # shellcheck disable=SC2086
  kill -9 $pids 2>/dev/null || true
}

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi

# 先摘掉可能存在的常驻 launchd 任务（否则它会一直占着 8765 反复重置）
if launchctl print "gui/$(id -u)/com.isqyq.srtcutter" >/dev/null 2>&1; then
  echo "发现常驻服务 com.isqyq.srtcutter，先卸载"
  launchctl remove com.isqyq.srtcutter >/dev/null 2>&1 || true
  sleep 0.5
fi

# 启动前先释放端口
kill_port 8765
kill_port 5173

.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --reload &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT

cd frontend
if [ ! -d "node_modules" ]; then
  npm install --no-audit --no-fund
fi
npm run dev
