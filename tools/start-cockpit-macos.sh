#!/usr/bin/env bash
# Local DATA_ONLY cockpit; dated feed approval and preflight gate live capture.
set -euo pipefail
export PATH="/opt/homebrew/opt/node@24/bin:$PATH"
root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$root"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --no-browser ) ]]; then
  echo 'Usage: bash tools/start-cockpit-macos.sh [--no-browser]' >&2
  exit 2
fi
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'This offline launcher requires macOS.' >&2
  exit 1
fi

umask 077

python="$root/.venv/bin/python"
gateway="$root/rust/target/debug/telemetry_local"
if [[ ! -x "$gateway" ]]; then
  gateway="$root/rust/target/release/telemetry_local"
fi
depth_binary="$root/rust/target/debug/quant-depth"
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

ready_local() {
  curl --silent --fail --max-time 2 http://127.0.0.1:5173/ >/dev/null &&
  curl --silent --fail --max-time 2 http://127.0.0.1:8767/trade/status >/dev/null
}
is_launcher_pid() {
  [[ "$1" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$1" 2>/dev/null || return 1
  local command_line
  command_line="$(ps -ww -p "$1" -o command= 2>/dev/null || true)"
  [[ "$command_line" == *"start-cockpit-macos.sh"* ]]
}

launcher_lock="$private/launcher.lock"
if [[ -L "$launcher_lock" ]]; then
  echo 'Launcher lock symlink invalid.' >&2
  exit 1
fi
if [[ -d "$launcher_lock" ]]; then
  existing_pid="$(cat "$launcher_lock/pid" 2>/dev/null || true)"
  if is_launcher_pid "$existing_pid"; then
    for attempt in {1..120}; do
      if ready_local; then
        echo 'Alpha Grid is already running; reattached to the supervised local services.'
        if [[ "$#" -eq 0 || "$1" != --no-browser ]]; then open "http://127.0.0.1:5173/"; fi
        exit 0
      fi
      sleep 0.5
    done
    : > "$private/stop-requested"
    for attempt in {1..40}; do
      if ! kill -0 "$existing_pid" 2>/dev/null; then break; fi
      sleep 0.25
    done
    if kill -0 "$existing_pid" 2>/dev/null; then kill -TERM "$existing_pid"; fi
    for attempt in {1..20}; do
      if ! kill -0 "$existing_pid" 2>/dev/null; then break; fi
      sleep 0.25
    done
  fi
fi
if ! mkdir -m 700 "$launcher_lock" 2>/dev/null; then
  existing_pid="$(cat "$launcher_lock/pid" 2>/dev/null || true)"
  if is_launcher_pid "$existing_pid"; then
    for attempt in {1..120}; do
      if ready_local; then
        echo 'Alpha Grid is already running; reattached to the supervised local services.'
        if [[ "$#" -eq 0 || "$1" != --no-browser ]]; then open "http://127.0.0.1:5173/"; fi
        exit 0
      fi
      sleep 0.5
    done
    echo 'Existing Alpha Grid launcher did not become ready.' >&2
    exit 1
  fi
  [[ -d "$launcher_lock" && ! -L "$launcher_lock" ]] || exit 1
  rm -f "$launcher_lock/pid"
  rmdir "$launcher_lock" 2>/dev/null || exit 1
  mkdir -m 700 "$launcher_lock" 2>/dev/null || exit 1
fi
printf '%s\n' "$$" > "$launcher_lock/pid"
abort_startup() {
  rm -f "$launcher_lock/pid"
  rmdir "$launcher_lock" 2>/dev/null || true
  exit 1
}

# Recover only recognizable Alpha Grid orphans from a crashed launcher.
for port in 8765 8767 5173; do
  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    command_line="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
    case "$port:$command_line" in
      8765:*"$root/rust/target/"*"telemetry_local"*|\
      8767:*"$root/web/server/trading-service.mjs"*|\
      5173:*"$root/web/node_modules/vite/"*) kill -TERM "$pid" 2>/dev/null || true ;;
      *) echo "Port $port belongs to another application; cannot replace it." >&2; abort_startup ;;
    esac
  done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
done
while read -r pid command_line; do
  if [[ "$command_line" == *"$private"* ]] &&
     [[ "$command_line" == *"quant.apps.live_simulation"* || "$command_line" == *"quant.apps.local_training_worker"* ]]; then
    kill -TERM "$pid" 2>/dev/null || true
  fi
done < <(ps -axww -o pid= -o command=)
for attempt in {1..40}; do
  occupied=0
  for port in 8765 8767 5173; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null; then occupied=1; fi
  done
  if (( occupied == 0 )); then break; fi
  sleep 0.25
done
(( occupied == 0 )) || { echo 'Previous local listener did not stop.' >&2; abort_startup; }

session_secret="$(openssl rand -hex 32)"
pids=()
depth_pid=0
gateway_stop="$private/gateway-stop-requested"
cleanup() {
  : > "$private/stop-requested"
  : > "$gateway_stop"
  # Give the gateway a chance to close and sync its QJ01 writer.
  for attempt in {1..20}; do
    if [[ ${#pids[@]} -lt 2 ]] || ! kill -0 "${pids[1]}" 2>/dev/null; then break; fi
    sleep 0.1
  done
  if (( depth_pid > 0 )); then
    for attempt in {1..50}; do
      if ! kill -0 "$depth_pid" 2>/dev/null; then break; fi
      sleep 0.1
    done
  fi
  for pid in "${pids[@]}"; do if (( pid > 0 )); then kill "$pid" 2>/dev/null || true; fi; done
  if (( depth_pid > 0 )); then kill "$depth_pid" 2>/dev/null || true; fi
  for pid in "${pids[@]}"; do if (( pid > 0 )); then wait "$pid" 2>/dev/null || true; fi; done
  if (( depth_pid > 0 )); then wait "$depth_pid" 2>/dev/null || true; fi
  rm -f "$launcher_lock/pid"
  rmdir "$launcher_lock" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ ! -L "$private/stop-requested" ]] || { echo 'Stop marker symlink invalid.' >&2; exit 1; }
[[ ! -L "$gateway_stop" ]] || { echo 'Gateway stop marker symlink invalid.' >&2; exit 1; }
rm -f "$private/stop-requested"
rm -f "$gateway_stop"

scope_plan() {
  # The daily budget limits NEW connections; never stop an active socket solely
  # because its successful connection consumed the final attempt.
  if [[ "${1:-}" != live ]] && ! "$python" - "$private" <<'PY'
import json, os, sys, tempfile, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
private = Path(sys.argv[1])
day = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
log = private / f"raw-capture-events-{day}.jsonl"
if log.exists():
    try:
        events = [json.loads(line) for line in log.read_text().splitlines() if line]
        # Offline revalidation emits PREFLIGHT_PASSED repeatedly for one live
        # socket; count only sessions that actually began network connection.
        attempts = {row["feed_session_id"] for row in events
                    if row.get("schema_version") == "alpha-grid-capture-event-v1"
                    and row.get("event", {}).get("detail") == "RESOLVING_DNS"}
        stops = [row.get("event", {}).get("detail") for row in events
                 if str(row.get("event", {}).get("detail", "")).startswith("STOPPED:")]
        reason = ("UPGRADE_HTTP_400_DAILY_ATTEMPTS_EXHAUSTED"
                  if stops and stops[-1] == "STOPPED:UPGRADE_HTTP_400"
                  else "DAILY_FEED_ATTEMPTS_EXHAUSTED") if len(attempts) >= 12 else None
    except (OSError, ValueError, KeyError, TypeError):
        reason = "FEED_ATTEMPT_LOG_INVALID"
    if reason:
        path = private / "feed-gate-status.json"
        fd, name = tempfile.mkstemp(prefix="feed-gate-", dir=private)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"schema_version": "alpha-grid-feed-gate-v1", "state": "BLOCKED",
                           "reason": reason, "checked_at_ns": time.time_ns()}, stream)
                stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        raise SystemExit(1)
PY
  then return 1
  fi
  PYTHONPATH="$root/src" "$python" -m quant.apps.live_feed_scope \
    --root "$root" --private-dir "$private" --auto-local-scope \
    --status-file "$private/feed-gate-status.json"
}

display_scope() {
  PYTHONPATH="$root/src" "$python" -m quant.apps.live_feed_scope \
    --root "$root" --private-dir "$private" --display-only
}

launch_gateway() {
  rm -f "$gateway_stop"
  if [[ -n "${1:-}" ]]; then
    gateway_expiry="${1##*|}"
    IFS='|' read -r _ _ _ _ _ _ _ _ gateway_standard_mode depth_flag gateway_capture_mode _ <<< "$1"
    env -u DHAN_API_KEY -u DHAN_API_SECRET \
      ALPHA_GRID_UI_SESSION_SECRET="$session_secret" ALPHA_GRID_PYTHON="$python" \
      PYTHONPATH="$root/src" "$python" -m quant.apps.live_feed_credentials \
      --root "$root" --private-dir "$private" --gateway "$gateway" &
    gateway_mode=live
    if [[ "$depth_flag" == 1 ]]; then
      env -u DHAN_API_KEY -u DHAN_API_SECRET \
        PYTHONPATH="$root/src" "$python" -m quant.apps.live_feed_credentials \
        --root "$root" --private-dir "$private" --depth-binary "$depth_binary" &
      depth_pid=$!
    else
      depth_pid=0
    fi
  else
    gateway_expiry=0
    gateway_capture_mode=""
    gateway_standard_mode=""
    depth_pid=0
    offline_ids="$(PYTHONPATH="$root/src" "$python" -c 'import json,sys; print(",".join(x["security_id"] for x in json.load(open(sys.argv[1]))["instruments"]))' "$root/docs/cross-stock-universe-2026-11-02.json")"
    if [[ -n "$offline_ids" ]]; then
      env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
        -u ALPHA_GRID_ONLINE_MODE -u ALPHA_GRID_ENABLE_DHAN_QUOTE \
        ALPHA_GRID_UI_SESSION_SECRET="$session_secret" ALPHA_GRID_PYTHON="$python" \
        PYTHONPATH="$root/src" \
        "$gateway" --security-ids "$offline_ids" \
        --universe "$root/docs/cross-stock-universe-2026-11-02.json" \
        --approval-ledger "$private/paper-approvals.qp" \
        --stop-file "$gateway_stop" &
    else
      env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
        -u ALPHA_GRID_ONLINE_MODE -u ALPHA_GRID_ENABLE_DHAN_QUOTE \
        ALPHA_GRID_UI_SESSION_SECRET="$session_secret" ALPHA_GRID_PYTHON="$python" \
        PYTHONPATH="$root/src" \
        "$gateway" --security-id 2885 --approval-ledger "$private/paper-approvals.qp" \
        --stop-file "$gateway_stop" &
    fi
    gateway_mode=offline
  fi
  gateway_pid=$!
  pids[1]="$gateway_pid"
}

stop_gateway() {
  : > "$gateway_stop"
  for attempt in {1..50}; do
    if ! kill -0 "$gateway_pid" 2>/dev/null; then break; fi
    sleep 0.1
  done
  if kill -0 "$gateway_pid" 2>/dev/null; then
    kill "$gateway_pid" 2>/dev/null || true
  fi
  wait "$gateway_pid" 2>/dev/null || true
  if (( depth_pid > 0 )); then
    for attempt in {1..50}; do
      if ! kill -0 "$depth_pid" 2>/dev/null; then break; fi
      sleep 0.1
    done
    if kill -0 "$depth_pid" 2>/dev/null; then kill "$depth_pid" 2>/dev/null || true; fi
    wait "$depth_pid" 2>/dev/null || true
    depth_pid=0
  fi
}

start_node() {
  # Account reads are limited to funds, holdings and positions; no order writes.
  env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
    -u ALPHA_GRID_ONLINE_MODE -u ALPHA_GRID_ENABLE_DHAN_QUOTE \
    ALPHA_GRID_ACCOUNT_OFFLINE=0 ALPHA_GRID_ACCOUNT_SYNC=1 \
    ALPHA_GRID_ACCOUNT_READ_ONLY_LIMITED=1 \
    ALPHA_GRID_PYTHON="$python" PYTHONPATH="$root/src" \
    ALPHA_GRID_TRADING_SETTINGS="$private/settings.json" \
    ALPHA_GRID_SIMULATION_DIR="$private" \
    ALPHA_GRID_UNIVERSE_PATH="$root/docs/cross-stock-universe-2026-11-02.json" \
    ALPHA_GRID_DEPTH_AVAILABLE="$(test -x "$depth_binary" && echo 1 || echo 0)" \
    ALPHA_GRID_UI_SESSION_SECRET="$session_secret" \
    node "$root/web/server/trading-service.mjs" &
  pids[0]=$!
}

start_simulation() {
  env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
    ALPHA_GRID_UI_SESSION_SECRET="$session_secret" PYTHONPATH="$root/src" \
    "$python" -m quant.apps.live_simulation --portfolio-v2 --private-dir "$private" &
  pids[2]=$!
}

start_training() {
  env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET -u DHAN_CLIENT_ID \
    PYTHONPATH="$root/src" \
    "$python" -m quant.apps.local_training_worker --private-dir "$private" &
  pids[3]=$!
}

start_vite() {
  (cd "$root/web" && exec env -u DHAN_ACCESS_TOKEN -u DHAN_API_KEY -u DHAN_API_SECRET \
    -u DHAN_CLIENT_ID -u ALPHA_GRID_UI_SESSION_SECRET -u ALPHA_GRID_UI_BOOTSTRAP_TOKEN \
    node "$vite" --host 127.0.0.1 --port 5173 --strictPort) &
  pids[4]=$!
}

start_service() {
  case "$1" in
    0) start_node ;; 2) start_simulation ;; 3) start_training ;; 4) start_vite ;;
  esac
}

record_service_failure() {
  "$python" - "$private/service-events.jsonl" "$1" "$2" <<'PY'
import json, os, stat, sys, time
path, service, count = sys.argv[1:]
flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(path, flags, 0o600)
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("private service event log invalid")
    row = {"schema_version": "alpha-grid-service-event-v1", "at_ns": time.time_ns(),
           "service": service, "consecutive_failures": int(count)}
    os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

service_failures=(0 0 0 0 0)
service_names=(node gateway simulation training vite)
supervise_services() {
  local index pid
  for index in 0 2 3 4; do
    pid="${pids[index]:-0}"
    if (( pid > 0 )) && ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      service_failures[index]=$((service_failures[index] + 1))
      record_service_failure "${service_names[index]}" "${service_failures[index]}" ||
        echo 'Service failure log unavailable; other services continue.' >&2
      if (( service_failures[index] <= 3 )); then
        start_service "$index"
      else
        pids[index]=0
        echo "Local ${service_names[index]} service failed repeatedly; other services continue." >&2
      fi
    fi
  done
}

start_node

if initial_scope="$(scope_plan)"; then
  launch_gateway "$initial_scope"
else
  launch_gateway ""
fi

start_simulation
start_training
start_vite

# The live gateway repeats clock and storage preflight before binding 8765.
for attempt in {1..160}; do
  supervise_services
  if [[ "$gateway_mode" == live ]] && ! kill -0 "$gateway_pid" 2>/dev/null; then
    wait "$gateway_pid" 2>/dev/null || true
    launch_gateway ""
  fi
  if ready_local; then
    echo 'Alpha Grid offline development: DATA_ONLY / NO TRADE.'
    echo 'Press Ctrl+C or use Simulation Stop to shut down the local services.'
    if [[ "${1:-}" != --no-browser ]]; then
      open "http://127.0.0.1:5173/"
    fi
    check_counter=0
    live_attempts=0
    if [[ "$gateway_mode" == live ]]; then live_attempts=1; fi
    offline_restarts=0
    gateway_failed=0
    attempt_day="$(TZ=Asia/Kolkata date +%Y-%m-%d)"
    while true; do
      if [[ -e "$private/stop-requested" ]]; then exit 0; fi
      if [[ "$gateway_mode" == live ]] && (( $(date +%s) >= gateway_expiry )); then
        stop_gateway
        launch_gateway ""
      fi
      day="$(TZ=Asia/Kolkata date +%Y-%m-%d)"
      if [[ "$day" != "$attempt_day" ]]; then attempt_day="$day"; live_attempts=0; offline_restarts=0; gateway_failed=0; fi
      if (( gateway_failed == 0 )) && ! kill -0 "$gateway_pid" 2>/dev/null; then
        wait "$gateway_pid" 2>/dev/null || true
        if [[ "$gateway_mode" == live && "$live_attempts" -lt 12 ]] && next_scope="$(scope_plan)"; then
          live_attempts=$((live_attempts + 1))
          launch_gateway "$next_scope"
        else
          if [[ "$gateway_mode" == offline ]]; then
            offline_restarts=$((offline_restarts + 1))
            record_service_failure gateway "$offline_restarts" ||
              echo 'Gateway failure log unavailable; other services continue.' >&2
            if (( offline_restarts > 2 )); then
              gateway_failed=1
              echo 'Offline gateway failed repeatedly; other local services continue.' >&2
            fi
          fi
          if (( gateway_failed == 0 )); then launch_gateway ""; fi
        fi
      fi
      if [[ "$gateway_mode" == live ]] && (( depth_pid > 0 )) && ! kill -0 "$depth_pid" 2>/dev/null; then
        wait "$depth_pid" 2>/dev/null || true
        depth_pid=0
        echo 'Optional depth collector stopped; standard raw capture continues.' >&2
      fi
      check_counter=$((check_counter + 1))
      if (( check_counter >= 30 )); then
        check_counter=0
        if next_scope="$(scope_plan "$gateway_mode")"; then
          IFS='|' read -r _ _ _ _ _ _ _ _ next_standard_mode _ _ _ <<< "$next_scope"
          if [[ "$gateway_mode" != live ]] && (( live_attempts < 12 )); then
            stop_gateway
            live_attempts=$((live_attempts + 1))
            launch_gateway "$next_scope"
            gateway_failed=0
          elif [[ ( "$gateway_capture_mode" == RAW_CAPTURE_ONLY &&
                  "$next_scope" == *'|RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION|'* ) ||
                  "$gateway_standard_mode" != "$next_standard_mode" ]] &&
                  (( live_attempts < 12 )); then
            # A new dated proof can enable the optional projection after one
            # bounded reconnect; raw capture was already active until now.
            stop_gateway
            live_attempts=$((live_attempts + 1))
            launch_gateway "$next_scope"
          fi
        elif [[ "$gateway_mode" == live ]]; then
          stop_gateway
          launch_gateway ""
        fi
        if [[ "$gateway_mode" == offline ]]; then
          desired_offline_ids="$offline_ids"
          if [[ "$desired_offline_ids" != "$offline_ids" ]]; then
            stop_gateway
            launch_gateway ""
          fi
        fi
      fi
      supervise_services
      sleep 1
    done
  fi
  sleep 0.25
done
echo 'Offline services did not become ready.' >&2
exit 1
