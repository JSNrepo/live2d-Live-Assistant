#!/usr/bin/env bash
cd "$(dirname "$0")"

# Proactively kill any orphaned GUI processes
pkill -f live2d_gui.py 2>/dev/null
sleep 0.2

# 1. Locate and launch Kimi WebBridge dynamically
WEBBRIDGE_EXE=""
if command -v kimi-webbridge &>/dev/null; then
    WEBBRIDGE_EXE="kimi-webbridge"
elif [ -x "$HOME/.kimi-webbridge/bin/kimi-webbridge" ]; then
    WEBBRIDGE_EXE="$HOME/.kimi-webbridge/bin/kimi-webbridge"
fi

WEBBRIDGE_STARTED=0
if [ -n "$WEBBRIDGE_EXE" ]; then
    if ! "$WEBBRIDGE_EXE" status 2>/dev/null | grep -q '"running":true'; then
        echo "[Launcher] Starting Kimi WebBridge daemon..."
        "$WEBBRIDGE_EXE" start
        WEBBRIDGE_STARTED=1
        sleep 1
    else
        echo "[Launcher] Kimi WebBridge daemon is already running."
    fi
else
    echo "[Launcher] Kimi WebBridge executable not found in PATH or ~/.kimi-webbridge/bin/"
fi

echo "[Launcher] Starting Live2D Floating Face Companion..."
GDK_BACKEND=x11 QT_QPA_PLATFORM=xcb ./.venv/bin/python live2d_gui.py &
GUI_PID=$!

sleep 1.5
if ! kill -0 $GUI_PID 2>/dev/null; then
    echo "[Launcher] ERROR: live2d_gui.py failed to start or exited immediately!"
    exit 1
fi


cleanup() {
    trap - EXIT INT TERM
    echo "[Launcher] Cleaning up background processes..."
    kill $GUI_PID 2>/dev/null
    pkill -f live2d_gui.py 2>/dev/null
    if [ "$WEBBRIDGE_STARTED" -eq 1 ] && [ -n "$WEBBRIDGE_EXE" ]; then
        echo "[Launcher] Stopping Kimi WebBridge daemon..."
        "$WEBBRIDGE_EXE" stop 2>/dev/null
    fi
    exit 0
}
trap cleanup EXIT INT TERM

echo "[Launcher] Starting companion voice and web automation engine..."
./.venv/bin/python main.py --live2d
