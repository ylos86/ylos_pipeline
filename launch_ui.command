#!/bin/bash
# launch_ui.command — start the Ylos Pipeline web server and open the Project Browser.
# Double-click from the Finder (or from a Dock icon): no command to retype.
#
# Behaviour:
#   - a server already answering on the port -> just open the browser on it
#   - otherwise start `python3 ylos_ui.py` (the active project persisted in
#     ~/.ylos/active_project is kept — nothing is reset) and open the browser ~1s later
#   - Ctrl-C (or closing the Terminal window) stops the server, as with a manual launch
#
# The repo path is resolved from the script's own location (never a hard-coded absolute
# path): the launcher survives a move of the repo, same principle as $PROJ_ROOT (cf. README).
#
# $PROJ_ROOT / $PROJ_CACHE are deliberately NOT exported here: since plan-usable-v1
# Phase 0.2 the server hands each launched DCC its own per-session environment
# (ylos_ui._launch_env + tools/blender/launch_context._apply_session_env), so two
# projects opened from the same cockpit never collide over one global shell value.

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${YLOS_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"

cd "$REPO_DIR"

if curl -s -o /dev/null --max-time 2 "$URL"; then
    echo "[ylos] Server already running on $URL - opening the browser."
    open "$URL"
    exit 0
fi

echo "[ylos] Starting the server (port $PORT) from $REPO_DIR ..."
( sleep 1 && open "$URL" ) &

# `set -e` lifted from here on purpose: a failed start must be REPORTED (port already
# taken, python3 missing) instead of closing the window on a bare traceback the user
# never gets to read.
set +e
python3 ylos_ui.py --port "$PORT"
code=$?
if [ "$code" -ne 0 ] && [ "$code" -ne 130 ]; then   # 130 = Ctrl-C, a normal stop
    echo
    echo "[ylos] The server stopped with code $code."
    echo "[ylos] Port $PORT may already be held by another process:"
    echo "       lsof -i :$PORT              # see who holds it"
    echo "       YLOS_PORT=8766 \"$0\"        # or start on another port"
    echo
    read -r -p "Press Return to close this window."
fi
exit "$code"
