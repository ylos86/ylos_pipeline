#!/bin/bash
# launch_ui.command — start / stop / inspect the Ylos Pipeline web server (Project Browser).
#
# The ONE place that knows how to launch the server. Ylos.app (same folder) is a Terminal-less
# shell around this file: double-click it for daily use, run this file for the terminal flavour.
#
#   ./launch_ui.command             foreground: this Terminal window IS the server (Ctrl-C stops
#                                   it). Double-click from the Finder works too.
#   ./launch_ui.command --detach    background: no terminal needed, log in ~/.ylos/ui-server.log
#   ./launch_ui.command --stop      stop the Ylos server listening on the port
#   ./launch_ui.command --status    print  stopped | running | stale | blocked
#                                     stale   = ylos_ui.py / create_project.py changed since the
#                                               server started: it still runs the OLD code
#                                     blocked = the port is held by something that is not Ylos
#                                   (exit 0 when a Ylos server is up, 1 otherwise)
#
# Behaviour (foreground and --detach):
#   - a Ylos server already on the port -> just open the browser on it
#   - the port held by another application -> say so, never open that page
#   - otherwise start `python3 ylos_ui.py` (the active project persisted in
#     ~/.ylos/active_project is kept — nothing is reset) and open the browser
#
# "Is it Ylos?" is decided on the PROCESS (a listener on the port whose command line contains
# ylos_ui.py), not on "something answers HTTP": a foreign dev server on 8765 must not be taken
# for the cockpit, and --stop must never kill a stranger.
#
# The repo path is resolved from the script's own location (never a hard-coded absolute
# path): the launcher survives a move of the repo, same principle as $PROJ_ROOT (cf. README).
#
# $PROJ_ROOT / $PROJ_CACHE are deliberately NOT exported here: since plan-usable-v1
# Phase 0.2 the server hands each launched DCC its own per-session environment
# (ylos_ui._launch_env + tools/blender/launch_context._apply_session_env), so two
# projects opened from the same cockpit never collide over one global shell value.
#
# Port: $YLOS_PORT (default 8765). Kept bash 3.2-clean: that is the /bin/bash of macOS.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${YLOS_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"
YLOS_HOME="$HOME/.ylos"                   # same folder as ylos_ui.YLOS_DIR
LOG="$YLOS_HOME/ui-server.log"            # --detach output (the previous run is kept as .1)
MARKER="$YLOS_HOME/ui-server.started"     # its mtime = when the server was last started

usage() {
    cat <<EOF
Usage: launch_ui.command [--detach | --stop | --status]
  (no option)  foreground: this Terminal window is the server (Ctrl-C stops it)
  --detach     start in the background and open the browser (log: ~/.ylos/ui-server.log)
  --stop       stop the Ylos server on port \$YLOS_PORT (default 8765)
  --status     print stopped | running | stale | blocked
EOF
}

# PIDs of the Ylos server(s) listening on $PORT, one per line. The command line must run
# ylos_ui.py itself (" ylos_ui.py " or ".../ylos_ui.py "), not merely mention it: a test runner
# called on tests/test_ylos_ui.py is not the server.
ylos_pids() {
    local pid
    for pid in $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | sort -un); do
        case " $(ps -ww -p "$pid" -o command= 2>/dev/null) " in
            *" ylos_ui.py "*|*"/ylos_ui.py "*) echo "$pid" ;;
        esac
    done
}

# Does anything answer HTTP on the port? (any status — a reply is a reply). Probes the tiny
# /favicon.ico (204 on the Ylos server) instead of "/" so it never pulls the whole cockpit page.
port_answers() { curl -s -o /dev/null --noproxy '*' --max-time 2 "${URL}favicon.ico"; }

mark_started() { mkdir -p "$YLOS_HOME" && : > "$MARKER"; }

ylos_state() {
    if [ -n "$(ylos_pids)" ]; then
        if [ -f "$MARKER" ] && [ -n "$(find "$REPO_DIR/ylos_ui.py" "$REPO_DIR/create_project.py" -newer "$MARKER" 2>/dev/null)" ]; then
            echo stale
        else
            echo running
        fi
    elif port_answers; then
        echo blocked
    else
        echo stopped
    fi
}

run_foreground() {
    local code
    if [ -n "$(ylos_pids)" ]; then
        echo "[ylos] Server already running on $URL - opening the browser."
        open "$URL"
        return 0
    fi
    if port_answers; then
        echo "[ylos] Port $PORT is held by another application (not the Ylos server)." >&2
        echo "       lsof -nP -iTCP:$PORT -sTCP:LISTEN    # see who holds it" >&2
        echo "       YLOS_PORT=8766 \"$0\"                 # or start on another port" >&2
        return 1
    fi

    echo "[ylos] Starting the server (port $PORT) from $REPO_DIR ..."
    mark_started
    cd "$REPO_DIR" || return 1
    ( sleep 1 && open "$URL" ) &

    # No `set -e` in this script on purpose: a failed start must be REPORTED (port already
    # taken, python3 missing) instead of closing the window on a bare traceback the user
    # never gets to read.
    python3 ylos_ui.py --port "$PORT"
    code=$?
    # 130 = Ctrl-C, 143 = --stop (SIGTERM), 129 = window closed (SIGHUP): normal stops.
    case "$code" in
        0|129|130|143) ;;
        *)
            echo
            echo "[ylos] The server stopped with code $code."
            echo "[ylos] Port $PORT may be held by another process:"
            echo "       lsof -nP -iTCP:$PORT -sTCP:LISTEN   # see who holds it"
            echo "       YLOS_PORT=8766 \"$0\"                # or start on another port"
            echo
            read -r -p "Press Return to close this window."
            ;;
    esac
    return "$code"
}

run_detached() {
    local pid i
    if [ -n "$(ylos_pids)" ]; then
        echo "[ylos] Server already running on $URL - opening the browser."
        open "$URL"
        return 0
    fi
    if port_answers; then
        echo "[ylos] Port $PORT is held by another application (not the Ylos server)." >&2
        echo "       Close it, or start Ylos on another port (YLOS_PORT=8766)." >&2
        return 1
    fi

    mkdir -p "$YLOS_HOME"
    [ -f "$LOG" ] && mv -f "$LOG" "$LOG.1"
    echo "[launcher] $(date '+%Y-%m-%d %H:%M:%S') python3 ylos_ui.py --port $PORT (repo: $REPO_DIR)" > "$LOG"
    mark_started
    cd "$REPO_DIR" || return 1
    # -u: unbuffered, so the log is live (Python block-buffers stdout when it is a file).
    # Own session (setsid): the server must not share this script's process group. Started from
    # Ylos.app, this script is the main process of a launchd job, and launchd kills what is left
    # in the job's process group when it exits: the server would die milliseconds after the
    # browser opened. macOS has no setsid(1), so python does it and then BECOMES the server: same
    # PID, same command line as before (python3 -u ylos_ui.py --port N), which is what ylos_pids,
    # --status and --stop rely on.
    nohup python3 -u -c 'import os, sys
try:
    os.setsid()
except OSError:         # already a group leader (job control on): not in our group anyway
    pass
os.execvp("python3", ["python3", "-u", "ylos_ui.py", "--port", sys.argv[1]])' "$PORT" >> "$LOG" 2>&1 < /dev/null &
    pid=$!

    # Up to ~30 s: a crash ends the wait at once (kill -0), so the long bound only matters for
    # a process that is alive but silent (a cold first start, a disk slow to wake up...).
    i=0
    while [ "$i" -lt 150 ]; do
        kill -0 "$pid" 2>/dev/null || break   # the process died: no point waiting
        if port_answers; then
            echo "[ylos] Server started on $URL (log: $LOG)"
            open "$URL"
            return 0
        fi
        sleep 0.2
        i=$((i + 1))
    done

    kill "$pid" 2>/dev/null                   # alive but never answered: do not leave it behind
    echo "[ylos] The server did not start. Last lines of $LOG:" >&2
    tail -n 12 "$LOG" >&2
    return 1
}

stop_server() {
    local pids pid i
    pids="$(ylos_pids)"
    if [ -z "$pids" ]; then
        if port_answers; then
            echo "[ylos] Port $PORT is held by another application - not touching it." >&2
            return 1
        fi
        echo "[ylos] No Ylos server running on port $PORT."
        return 0
    fi

    for pid in $pids; do kill "$pid" 2>/dev/null; done
    i=0
    while [ "$i" -lt 25 ]; do                 # ~5 s for a clean exit
        if [ -z "$(ylos_pids)" ]; then
            echo "[ylos] Server stopped."
            return 0
        fi
        sleep 0.2
        i=$((i + 1))
    done
    for pid in $(ylos_pids); do kill -9 "$pid" 2>/dev/null; done
    sleep 0.5
    if [ -z "$(ylos_pids)" ]; then
        echo "[ylos] Server stopped (forced)."
        return 0
    fi
    echo "[ylos] Could not stop the server on port $PORT." >&2
    return 1
}

case "${1:-}" in
    "")        run_foreground; exit $? ;;
    --detach)  run_detached;   exit $? ;;
    --stop)    stop_server;    exit $? ;;
    --status)
        state="$(ylos_state)"
        echo "$state"
        [ "$state" = running ] || [ "$state" = stale ]
        exit $?
        ;;
    -h|--help) usage; exit 0 ;;
    *)         echo "[ylos] Unknown option: $1" >&2; usage >&2; exit 2 ;;
esac
