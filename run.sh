#!/usr/bin/env bash
# Start the studio. Safe to re-run: installs anything missing, then serves.
#
#   ./run.sh              # sensible default for where it's running
#   ./run.sh 127.0.0.1    # force loopback only
#   ./run.sh 0.0.0.0      # force all interfaces
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8080}"

# Inside a container, binding loopback makes the app unreachable: Docker
# forwards a published port to the container's external interface, not to its
# loopback. There the boundary is the port publishing on the host (this
# container publishes to 127.0.0.1 only), not the bind address. Outside a
# container, loopback is the safe default.
if [ -n "${1:-}" ]; then
  HOST="$1"
elif [ -f /.dockerenv ]; then
  HOST="0.0.0.0"
else
  HOST="127.0.0.1"
fi

# Container rebuilds wipe site-packages, so check rather than assume. The pip
# cache is persistent, so a reinstall is quick.
if ! python3 -c "import fastapi, uvicorn, httpx, PIL, multipart" 2>/dev/null; then
  echo "Installing dependencies..."
  pip install -q --break-system-packages -r requirements.txt
fi

# Keys live in ~/.secrets in this container; elsewhere, export them yourself.
if [ -f "$HOME/.secrets" ]; then
  set -a; . "$HOME/.secrets"; set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "No OPENAI_API_KEY or OPENROUTER_API_KEY set — the model list will be empty." >&2
fi

if [ "$HOST" = "0.0.0.0" ] && [ -z "${STUDIO_PASSWORD:-}" ]; then
  echo "Warning: serving on all interfaces with no STUDIO_PASSWORD." >&2
  echo "Fine if the port is only published to localhost, as it is in this" >&2
  echo "container. On a public host, set STUDIO_PASSWORD — anyone who reaches" >&2
  echo "this port can spend your API credits." >&2
fi

# ---------- Port resolution ----------
#
# Uvicorn binds *after* it prints its startup banner, so a port clash used to
# surface as "Application startup complete" immediately followed by Errno 98 —
# output that reads like a success and then dies. Resolve the port up front
# instead, so nothing is announced until it is known to be bindable.

# Mirrors the bind uvicorn will actually attempt, SO_REUSEADDR included, so a
# port in TIME_WAIT isn't reported as taken when uvicorn could have claimed it.
port_free() {
  python3 - "$HOST" "$1" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind((sys.argv[1], int(sys.argv[2])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

# Names the process holding a port. ss/lsof/netstat are absent from slim
# container images, so read /proc directly: find the listening socket's inode
# in /proc/net/tcp{,6}, then find which process has it open. Only processes
# owned by this user are visible, so this degrades to silence rather than
# failing.
port_holder() {
  python3 - "$1" <<'PY'
import os, sys, glob

want = int(sys.argv[1])
inodes = set()
for table in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        rows = open(table).read().splitlines()[1:]
    except OSError:
        continue
    for row in rows:
        f = row.split()
        # st == 0A is LISTEN; local_address is host:port, both hex.
        if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == want:
            inodes.add(f[9])

for fd in glob.glob("/proc/[0-9]*/fd/*"):
    try:
        link = os.readlink(fd)
        if link.startswith("socket:[") and link[8:-1] in inodes:
            pid = fd.split("/")[2]
            # cmdline can contain newlines (e.g. python3 -c "<script>"), which
            # would break the one-line format; collapse all whitespace.
            raw = open(f"/proc/{pid}/cmdline").read().replace("\0", " ")
            cmd = " ".join(raw.split())
            print(f"  held by pid {pid}: {cmd[:90]}")
            break
    except OSError:
        continue
PY
}

# Distinguishes "I already started this" from "something unrelated holds the
# port". /api/config needs no password, so this works on a locked instance too.
is_studio() {
  python3 - "$1" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(f"http://localhost:{sys.argv[1]}/api/config", timeout=2) as r:
        sys.exit(0 if "models" in json.load(r) else 1)
except Exception:
    sys.exit(1)
PY
}

# Moving to an arbitrary free port is wrong in a container: only published
# ports reach the host, so the studio would start and be unreachable from the
# browser. Fall back within the published set instead — override the list if
# your container publishes different ones. Outside a container any port works,
# so scan upward.
if [ -z "${STUDIO_PORT_FALLBACKS:-}" ]; then
  if [ -f /.dockerenv ]; then
    STUDIO_PORT_FALLBACKS="8000 3001 5173 5174 4321"
  else
    STUDIO_PORT_FALLBACKS="$(seq $((PORT + 1)) $((PORT + 9)))"
  fi
fi

if ! port_free "$PORT"; then
  if is_studio "$PORT"; then
    echo "Image Studio is already running on http://localhost:$PORT" >&2
    echo "Nothing to do. Stop it first, or set PORT to run a second instance." >&2
    exit 0
  fi

  echo "Port $PORT is in use by something that isn't the studio." >&2
  port_holder "$PORT" >&2 || true

  for candidate in $STUDIO_PORT_FALLBACKS; do
    [ "$candidate" = "$PORT" ] && continue
    if port_free "$candidate"; then
      echo "Using port $candidate instead." >&2
      PORT="$candidate"
      break
    fi
  done
fi

if ! port_free "$PORT"; then
  echo >&2
  tried="$(printf '%s\n' "$PORT" $STUDIO_PORT_FALLBACKS | awk '!seen[$0]++' | tr '\n' ' ')"
  echo "No free port among: $tried" >&2
  echo "Free one up, or pass an explicit port: PORT=9000 ./run.sh" >&2
  if [ -f /.dockerenv ]; then
    echo "In this container only published ports reach the host browser —" >&2
    echo "an unpublished port will serve fine and still look unreachable." >&2
  fi
  exit 1
fi

echo "Studio on http://localhost:$PORT"
exec uvicorn server:app --host "$HOST" --port "$PORT"
