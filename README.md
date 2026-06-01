# Hyori — Live2d desktop AI Companion

An AI voice companion powered by **Google Gemini Live** with a Live2D animated
character overlay, real-time audio processing, and an agentic tool layer for
browser automation, file operations, system control, and web search.

---
![Sakura AI Companion Interface Overlay](demos/thumbnail001.png)

## Showcase

Here is a visual demonstration of the Hyori AI Companion floating overlay, high-fidelity audio visualizers, and reactive interface:
view on youtube https://youtu.be/dZQYgihwA54

---

## Architecture

```
┌─────────────────────────────────┐
│           main.py               │
│  ┌──────────────────────────┐   │
│  │  Gemini Live WebSocket   │   │  ← google.genai realtime session
│  │  (asyncio TaskGroup)     │   │
│  │  ├── mic_reader()        │   │  ← PyAudio capture @ 16kHz
│  │  ├── send_audio()        │   │  ← streams PCM to Gemini
│  │  ├── recv_audio()        │   │  ← receives audio + tool calls
│  │  ├── play_audio()        │   │  ← pitch shift → PyAudio playback @ 24kHz
│  │  ├── monitor_system_*()  │   │  ← CPU/RAM/disk alerts
│  │  └── tail_terminal_*()   │   │  ← error log monitoring
│  └──────────────────────────┘   │
│  ┌──────────────────────────┐   │
│  │  Tool Layer              │   │
│  │  ├── search_web_contents │   │  ← DuckDuckGo Lite + Wikipedia API
│  │  ├── run_terminal_cmd    │   │  ← cross-distro shell (with confirmation)
│  │  ├── open_application    │   │  ← DE-aware app launcher (GNOME/KDE/XFCE…)
│  │  ├── webbridge_*         │   │  ← Kimi browser automation
│  │  ├── run_browser_task    │   │  ← agentic Gemini sub-loop (async)
│  │  ├── memory_graph_*      │   │  ← JSON memory store
│  │  └── control_media / music   │
│  └──────────────────────────┘   │
│  ┌──────────────────────────┐   │
│  │  Curses TUI (main thread)│   │  ← renders state/emotion/RMS
│  └──────────────────────────┘   │
└────────────────┬────────────────┘
                 │ UDP port 10088
                 ▼
┌─────────────────────────────────┐
│        live2d_gui.py            │  ← subprocess
│  pywebview (Qt/GTK/auto)        │
│  index.html + PIXI.js + Cubism4 │  ← Live2D character overlay
└─────────────────────────────────┘
```

## Key Components

| File | Purpose |
|---|---|
| `main.py` | Core async loop, audio pipeline, tool routing, Gemini session |
| `live2d_gui.py` | Standalone subprocess: pywebview window with Live2D |
| `index.html` | Live2D PIXI canvas, speech bubble UI, UDP command receiver |
| `config.toml` | Runtime configuration (voice, models, audio, emotions, noise gate) |
| `persona.txt` / `hyori.txt` | System instruction / personality prompt |
| `emoticons.json` | Emotion → animation frame mapping for the Live2D model |
| `run.sh` | Cross-distro launcher — auto-detects terminal emulator |
| `run_live2d.sh` | Launches `live2d_gui.py` + `main.py --live2d` together |

## Installation & Setup

We provide a distro-agnostic zero-dependency system installer script (`install.sh`) that automatically installs required packages (supporting `apt`, `dnf`, and `pacman`), provisions a virtual environment, and handles dependencies:

```bash
# Clone the repository and execute the installer
chmod +x install.sh
./install.sh
```

### Manual Prerequisites

If you prefer to configure the environment manually, ensure you have:
- **Python 3.11+**
- **System Packages:** `portaudio19-dev` / `portaudio-devel` (audio captures), `playerctl` (media HUD integrations), and a Pywebview backend (`python3-pyqt5` or `python3-gi`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

Copy `.env.example` → `.env` and set your active API keys:

```ini
GOOGLE_API_KEY=your_key_here
KIMI_WEBBRIDGE_API_KEY=your_key_here
```

Edit `config.toml` to customize voice indices, LLM models, pitch shifting factors, noise gate thresholds, etc.

---

## Running the Assistant

Sakura supports multiple runtime modes out of the box:

```bash
# 1. Full Mode (Glow HUD + Transparent WebGL Live2D Overlay)
./run_live2d.sh

# 2. Curses TUI-only Mode (Lightweight Terminal Session)
./run.sh

# 3. Direct execution
.venv/bin/python main.py
```

---

## Premium Visualizer & Media Features

1. **Ultra-Compact Single-Row Media HUD:**
   - Grouped Prev, Play/Pause, and Next buttons on the left, perfectly aligned near the neon canvas trace connection point (`rect.left`).
   - A dynamic progress seek bar with a high-tech glowing background stretching to occupy all remaining right-side space (`flex: 1`).
   - Integrates click-to-seek to jump directly to any track timestamp.

2. **Screen-Safe Audio Reactor Ticks:**
   - **Linear Mode:** Generates symmetrical bell-curved spectrum equalizers with high-amplitude peak sways that are clamped to `75px` to keep layouts beautiful.
   - **Circle Mode:** Draws rotating concentric arc reactors with radial dash spikes scaled to `24.0` multiplier, pulsing dynamically without overlapping background windows.
   - **Vigorous Sensitivity:** Features high-fidelity loopback RMS scale factors (`currentMicRMS / 650.0`) and synthesized idle beats that capture every detail of active songs.

3. **Smart Command Auto-Execution:**
   - Overhauled command security filters (`tools/system.py`) to stop demanding user confirmation for safe utility, developer, and query commands (such as `pytest`, `ls -la`, `git status`).
   - Retains bulletproof protection strictly for destructive, irrevocable terminal actions (like `rm -rf`, `dd if=`, `sudo rm`, and `pkill -9`). Non-disruptive commands run smoothly and instantly!

4. **Beat-Reactive procedural Dancing:**
   - PIXI app overrides procedure nod angles, eyeball drift, and symmetrical body sways synced to active sound inputs.
   - Restricts arm waving loops to a high baseline so Hiyori stays dancing elegantly without jerky resets to neutral!
