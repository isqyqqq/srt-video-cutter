#!/usr/bin/env bash
set -euo pipefail

# 一键启动：检查依赖、构建前端、清理旧服务、启动本地服务
cd "$(dirname "$0")"

PORT="${PORT:-8765}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# 停掉占用指定端口的旧服务，避免「address already in use」
# 只针对处于 LISTEN 状态的服务端进程，不会误杀连上来的浏览器等客户端。
# 先发 TERM 让 uvicorn 优雅退出，最多等 5 秒；仍不退再 KILL。
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return 0
  fi
  echo "[0/4] 端口 ${port} 被占用（PID: $(echo "$pids" | tr '\n' ' ')），正在停止旧服务..."
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i
  for ((i = 0; i < 50; i++)); do
    if ! lsof -ti "tcp:${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "[0/4] 旧服务已停止"
      return 0
    fi
    sleep 0.1
  done
  echo "[0/4] 旧服务未响应 TERM，强制结束"
  # shellcheck disable=SC2086
  kill -9 $pids 2>/dev/null || true
  sleep 0.3
  if lsof -ti "tcp:${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[0/4] 无法停止占用端口 ${port} 的进程（可能需要更高权限）。"
    echo "       请手动执行：kill -9 $(echo "$pids" | tr '\n' ' ')"
    exit 1
  fi
}

if [ ! -d ".venv" ]; then
  echo "[1/4] 创建 Python 虚拟环境..."
  "$PYTHON_BIN" -m venv .venv
fi
if [ ! -f ".venv/.deps-installed" ]; then
  echo "[1/4] 安装 Python 依赖..."
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r backend/requirements.txt
  touch .venv/.deps-installed
else
  echo "[1/4] Python 依赖已就绪"
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "[2/4] 安装前端依赖..."
  (cd frontend && npm install --no-audit --no-fund)
else
  echo "[2/4] 前端依赖已就绪"
fi

echo "[3/4] 构建前端..."
(cd frontend && npm run build)

# 先摘掉可能存在的常驻 launchd 任务，否则它会和本脚本抢同一个端口，
# 表现就是反复报「address already in use」（日志里能看到成百上千条）。
stop_launchd_service() {
  local label="com.isqyq.srtcutter"
  if launchctl print "gui/$(id -u)/${label}" >/dev/null 2>&1; then
    echo "[0/4] 发现常驻服务 ${label}，先卸载，避免抢占端口"
    launchctl remove "${label}" >/dev/null 2>&1 || true
    sleep 0.5
  fi
}

stop_launchd_service

# 启动前先释放端口，保证每次都能干净启动
kill_port "$PORT"

echo "[4/4] 启动服务：http://127.0.0.1:${PORT}"
if command -v open >/dev/null 2>&1; then
  (sleep 1.5 && open "http://127.0.0.1:${PORT}") >/dev/null 2>&1 &
fi

exec .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT"
