#!/usr/bin/env bash
# Supervisor for the OpenMontage launcher.
#
# Runs `uvicorn web.server:app` in a loop and respawns when the process exits
# with code 75 — the sentinel used by POST /api/deploy after a successful
# `git pull`. This lets the Deploy button on the home page fully restart the
# server (not just hot-reload it), so the launcher can be managed entirely
# through the web UI without `--reload`.
#
# Usage:
#   scripts/run_launcher.sh                       # defaults: 127.0.0.1:8765
#   PORT=9000 scripts/run_launcher.sh             # custom port
#   HOST=0.0.0.0 PORT=8765 scripts/run_launcher.sh
#   scripts/run_launcher.sh --log-level debug     # extra args pass through
#
# Exit codes:
#   0           uvicorn exited normally (Ctrl+C, SIGTERM)
#   75          treated as restart-request; supervisor respawns instead of exiting
#   anything    propagated to the caller (CI, systemd, etc.)
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO"

# Tell the app it's running under a supervisor that handles respawn on exit 75.
export OPENMONTAGE_LAUNCHER_SUPERVISED=1

PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || command -v python)"
fi
if [ -z "${PYTHON:-}" ] || [ ! -x "$PYTHON" ]; then
  echo "[supervisor] could not find a Python interpreter; set PYTHON=/path/to/python" >&2
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"

# Handle Ctrl+C cleanly: forward signal to uvicorn, then exit.
child_pid=""
forward_signal() {
  if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap forward_signal INT TERM

while true; do
  echo "[supervisor] starting uvicorn on $HOST:$PORT"
  "$PYTHON" -m uvicorn web.server:app --host "$HOST" --port "$PORT" "$@" &
  child_pid=$!
  wait "$child_pid"
  EXIT=$?
  child_pid=""

  if [ "$EXIT" -eq 75 ]; then
    echo "[supervisor] deploy-restart requested, respawning..."
    continue
  fi
  echo "[supervisor] uvicorn exited with code $EXIT, stopping"
  exit "$EXIT"
done
