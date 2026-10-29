#!/usr/bin/env bash
# Offline development only. No Dhan credentials, market socket, or account refresh.
set -euo pipefail
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'This offline launcher requires macOS.' >&2
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$root"
umask 077

for port in 8765 8767 5173; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null; then
    echo "Port $port is already in use; no duplicate service started." >&2
    exit 1
  fi
done

python="$root/.venv/bin/python"
gateway="$root/rust/target/debug/telemetry_local"
vite="$root/web/node_modules/vite/bin/vite.js"
[[ -x "$python" && -x "$gateway" && -f "$vite" ]] || {
  echo 'Build the locked Python, Rust, and web dependencies before offline startup.' >&2
  exit 1
}
command -v node >/dev/null || { echo 'Node.js 24 is required.' >&2; exit 1; }
command -v openssl >/dev/null || { echo 'OpenSSL is required.' >&2; exit 1; }

# Keep Mac development records separate from the Windows Dhan archive.
private="$HOME/Library/Application Support/AlphaGrid/offline-dev"
if [[ -L "$private" ]]; then
  echo 'Private development directory cannot be a symlink.' >&2
  exit 1
fi
if [[ ! -e "$private" ]]; then
  mkdir -m 700 -p "$private"
fi
[[ -d "$private" && ! -L "$private" && "$(stat -f %u "$private")" == "$(id -u)" ]] || {
  echo 'Private development directory owner invalid.' >&2
  exit 1
}
dir_mode="$(stat -f %Lp "$private")"
(( (8#$dir_mode & 8#077) == 0 )) || {
  echo 'Private development directory has broad permissions.' >&2
  exit 1
}
for path in "$private/paper-approvals.qp" "$private/settings.json"; do
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -f "$path" && ! -L "$path" && "$(stat -f %u "$path")" == "$(id -u)" ]] || {
      echo 'Private development file owner or type invalid.' >&2
      exit 1
    }
    file_mode="$(stat -f %Lp "$path")"
    (( (8#$file_mode & 8#077) == 0 )) || {
      echo 'Private development file has broad permissions.' >&2
      exit 1
    }
  fi
done

session_secret="$(openssl rand -hex 32)"
bootstrap="$(openssl rand -hex 32)"
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The account service gets offline cache mode only; the Mac has no migrated archive.
env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
  -u ALPHA_GRID_ONLINE_MODE -u ALPHA_GRID_ENABLE_DHAN_QUOTE \
  ALPHA_GRID_ACCOUNT_OFFLINE=1 ALPHA_GRID_ACCOUNT_SYNC=0 \
  ALPHA_GRID_TRADING_SETTINGS="$private/settings.json" \
  ALPHA_GRID_UI_SESSION_SECRET="$session_secret" \
  ALPHA_GRID_UI_BOOTSTRAP_TOKEN="$bootstrap" \
  node "$root/web/server/trading-service.mjs" &
pids+=("$!")

env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
  -u ALPHA_GRID_ONLINE_MODE -u ALPHA_GRID_ENABLE_DHAN_QUOTE \
  ALPHA_GRID_UI_SESSION_SECRET="$session_secret" ALPHA_GRID_PYTHON="$python" \
  PYTHONPATH="$root/src" \
  "$gateway" --security-id 2885 --approval-ledger "$private/paper-approvals.qp" &
pids+=("$!")

(cd "$root/web" && env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET \
  -u DHAN_CLIENT_ID -u ALPHA_GRID_UI_SESSION_SECRET -u ALPHA_GRID_UI_BOOTSTRAP_TOKEN \
  node "$vite" --host 127.0.0.1 --port 5173 --strictPort) &
pids+=("$!")

for attempt in {1..40}; do
  if curl --silent --fail http://127.0.0.1:5173/ >/dev/null && \
     curl --silent --fail http://127.0.0.1:8765/api/health >/dev/null && \
     curl --silent --fail http://127.0.0.1:8767/trade/status >/dev/null; then
    echo 'Alpha Grid offline development: DATA_ONLY / NO TRADE.'
    echo 'Press Ctrl+C to stop these three services.'
    open "http://127.0.0.1:5173/#bootstrap=$bootstrap"
    bootstrap=''
    while true; do
      for pid in "${pids[@]}"; do
        kill -0 "$pid" 2>/dev/null || {
          echo 'Offline service stopped.' >&2
          exit 1
        }
      done
      sleep 1
    done
  fi
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null || { echo 'Offline service exited during startup.' >&2; exit 1; }
  done
  sleep 0.25
done
echo 'Offline services did not become ready.' >&2
exit 1
