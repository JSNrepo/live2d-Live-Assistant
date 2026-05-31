"""Configuration, constants, and persona loading."""

import json
import logging
import os
import re
import tomllib
from enum import Enum, auto
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ["JACK_NO_SERVER"] = "1"
os.environ["JACK_NO_AUDIO_SERVER"] = "1"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Canonical emotion tag → animation mapping
EMOTION_TAG_MAP = {
    "happy": "smug",
    "proud": "smug",
    "teasing": "smug",
    "cheerful": "smug",
    "content": "smug",
    "impressed": "smug",
    "caring": "smug",
    "excited": "smug",
    "mad": "angry",
    "angry": "angry",
    "depressed": "sad",
    "scared": "sad",
    "sad": "sad",
    "question": "confused",
    "shocked": "confused",
    "suspicious": "confused",
    "confused": "confused",
    "waiting": "bored",
    "bored": "bored",
    "neutral": "speaking",
    "speaking": "speaking",
}

# Logging
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "debug.log",
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    filemode="w",
)
log = logging.getLogger("sakura")

# Emotion data
EMOTIONS_PATH = PROJECT_ROOT / "emoticons.json"
with open(EMOTIONS_PATH) as f:
    EMOTION_DATA = json.load(f)["animations"]

ANIM_SPEED = {
    "idle": 0.5, "listening": 0.4, "speaking": 0.12,
    "angry": 0.15, "sad": 0.3, "error": 0.3,
    "suspicious": 0.4, "smug": 0.3, "sleeping": 0.5,
    "confused": 0.4, "bored": 0.5, "boot": 0.25,
    "shutdown": 0.25, "scan": 0.3, "process": 0.5,
    "overheat": 0.2, "typing": 0.2, "vibing": 0.4, "hacking": 0.2,
}


class AppState(Enum):
    SLEEPING = auto()
    ACTIVATING = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()


# Default settings (overridden by config.toml)
LIVE_MODEL = "gemini-3.1-flash-live-preview"
TASK_MODEL = "gemini-3.5-flash"
VISION_MODEL = "gemini-3.5-flash"
MODEL = LIVE_MODEL
SEND_RATE = 16000
RECV_RATE = 24000
CHUNK = 1024
PITCH_SHIFT = 1.6
VOICE_NAME = "Leda"

# Noise Gate settings
NOISE_GATE_ENABLED = True  # Enabled by default for voice companion quality
NOISE_GATE_MIN_RMS = 20.0
NOISE_GATE_HOLD_FRAMES = 8

# Barge-in settings
BARGE_IN_ENABLED = True
BARGE_IN_THRESHOLD = 80.0
BARGE_IN_FEEDBACK_RATIO = 0.0

CONFIG_PATH = PROJECT_ROOT / "config.toml"
PERSONA_PATH = PROJECT_ROOT / "persona.txt"

if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "rb") as f:
            config_data = tomllib.load(f)

        voice_cfg = config_data.get("voice", {})
        VOICE_NAME = voice_cfg.get("voice_name", VOICE_NAME)
        LIVE_MODEL = voice_cfg.get("model", LIVE_MODEL)
        MODEL = LIVE_MODEL
        PITCH_SHIFT = voice_cfg.get("pitch_factor", PITCH_SHIFT)

        audio_cfg = config_data.get("audio", {})
        SEND_RATE = audio_cfg.get("send_rate", SEND_RATE)
        RECV_RATE = audio_cfg.get("recv_rate", RECV_RATE)
        CHUNK = audio_cfg.get("chunk", CHUNK)
        if "pitch_shift" in audio_cfg:
            PITCH_SHIFT = audio_cfg["pitch_shift"]

        models_cfg = config_data.get("models", {})
        LIVE_MODEL = models_cfg.get("live", LIVE_MODEL)
        MODEL = LIVE_MODEL
        TASK_MODEL = models_cfg.get("task", TASK_MODEL)
        VISION_MODEL = models_cfg.get("vision", VISION_MODEL)

        gate_cfg = config_data.get("noise_gate", {})
        NOISE_GATE_ENABLED = gate_cfg.get("enabled", NOISE_GATE_ENABLED)
        NOISE_GATE_MIN_RMS = gate_cfg.get("min_rms", NOISE_GATE_MIN_RMS)
        NOISE_GATE_HOLD_FRAMES = gate_cfg.get("hold_frames", NOISE_GATE_HOLD_FRAMES)

        barge_cfg = config_data.get("barge_in", {})
        BARGE_IN_ENABLED = barge_cfg.get("enabled", BARGE_IN_ENABLED)
        BARGE_IN_THRESHOLD = barge_cfg.get("threshold", BARGE_IN_THRESHOLD)
        BARGE_IN_FEEDBACK_RATIO = barge_cfg.get("feedback_ratio", BARGE_IN_FEEDBACK_RATIO)

        persona_cfg = config_data.get("persona", {})
        if "persona_file" in persona_cfg:
            PERSONA_PATH = PROJECT_ROOT / persona_cfg["persona_file"]
    except Exception as e:
        print(f"Warning: Failed to load config.toml: {e}. Using defaults.")

# Load system instruction (persona) from file
if PERSONA_PATH.exists():
    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
            content = content.replace("[name]", "vinoth")
            content = re.sub(r"\bFire\b", "vinoth", content)
            content = re.sub(r"\bfire\b", "vinoth", content)
            tool_use_instructions = (
                "\n\n[CRITICAL TOOL USE INSTRUCTIONS]\n"
                "- Proactive Tool Usage: You have access to powerful tools like `run_shell_command` (to run Linux terminal commands) and `run_browser_task` (for browser automation).\n"
                "- Browser & Web Tasks Delegation: For ANY browser actions, searches, or web interactions (like 'go to YouTube and play a song', 'check my Gmail', 'click the search bar and type X', 'scroll down and read the first article'), you MUST call `run_browser_task` with the detailed description. Do NOT say 'I can't do it' or 'I don't have internet access'. `run_browser_task` is your fully autonomous background agent that has full browser control via Kimi WebBridge.\n"
                "- Screen Vision: For any requests to 'look at my screen', 'see what I am doing', or ask visual questions about active windows, you MUST call `analyze_screen`.\n"
                "- Do NOT Hardcode: Never make up or assume answers or hardcode system details, time, or file paths. Proactively run shell commands or search/browse the web to retrieve accurate, real-world data before answering.\n"
                "- Multi-tool Efficiency: Work dynamically. You are expected to handle complex tasks on the terminal and browser — run commands, list processes, check files, launch browsers, and navigate to tabs to execute the user's requests accurately."
            )
            SYSTEM_INSTRUCTION = content + tool_use_instructions
    except Exception as e:
        print(f"Warning: Failed to load persona file: {e}. Using fallback instruction.")
        SYSTEM_INSTRUCTION = "You are a helpful assistant."
else:
    SYSTEM_INSTRUCTION = (
        "You are a helpful, knowledgeable, and polite AI desktop assistant. "
        "You help the user with a wide range of tasks including answering questions, "
        "running shell commands, browsing the web, analyzing screenshots, and managing their computer. "
        "Always respond clearly, accurately, and concisely. "
        "If you are unsure about something, say so honestly rather than guessing. "
        "Use your available tools proactively to give precise, real-world answers."
    )

use_curses = True

# Playerctl availability check
import shutil as _shutil
_HAS_PLAYERCTL = _shutil.which("playerctl") is not None
