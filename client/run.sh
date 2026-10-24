#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$PROJECT_DIR/client"
PY="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "!! 未找到虚拟环境，请先执行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# 依次加载 .env（KEY=VALUE 每行一条），不覆盖已存在的环境变量。
# 先加载 client/.env（客户端配置），再加载项目根 .env（如 SILICONFLOW_API_KEY，
# 供本地语音处理 app.process_voice_file 使用）。
load_env() {
  local file="$1"
  [ -f "$file" ] || return 0
  echo "==> 从 $file 加载环境变量"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    [ -z "$key" ] && continue
    [ -z "$value" ] && continue
    if [ -z "${!key+x}" ]; then
      export "$key=$value"
    fi
  done < "$file"
}

load_env "$CLIENT_DIR/.env"
load_env "$PROJECT_DIR/.env"

export REMOTE_COMMAND=1
exec "$PY" "$CLIENT_DIR/asr_client.py"