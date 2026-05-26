#!/usr/bin/env bash
# run.sh — Launch Sakura in any available terminal emulator (M06: cross-distro fix)
cd "$(dirname "$0")"

PYTHON="./.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
  PYTHON="python3"
fi

# Ordered list of terminal emulators to try (most feature-rich first)
TERMINALS=(
  "kitty:kitty -o font_size=28 -o initial_window_width=7c -o initial_window_height=2c --title Sakura --"
  "alacritty:alacritty --title Sakura -e"
  "wezterm:wezterm start --"
  "foot:foot --title Sakura --"
  "gnome-terminal:gnome-terminal --title Sakura --"
  "xfce4-terminal:xfce4-terminal --title Sakura -x"
  "konsole:konsole --title Sakura -e"
  "lxterminal:lxterminal --title Sakura -e"
  "xterm:xterm -title Sakura -e"
)

for entry in "${TERMINALS[@]}"; do
  TERM_EXE="${entry%%:*}"
  TERM_CMD="${entry#*:}"

  if command -v "$TERM_EXE" &>/dev/null; then
    echo "[run.sh] Using terminal: $TERM_EXE"
    exec $TERM_CMD "$PYTHON" main.py "$@"
  fi
done

# Last resort: run in current terminal (no separate window)
echo "[run.sh] No GUI terminal found — running inline"
exec "$PYTHON" main.py "$@"
