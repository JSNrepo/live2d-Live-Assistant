import array
import asyncio
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import atexit
import curses
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
import webbrowser
import tomllib

import numpy as np
import pyaudio
from PIL import Image
from dotenv import load_dotenv
from google import genai
import scipy.signal
load_dotenv(Path(__file__).parent / ".env")
os.environ["JACK_NO_AUDIO_SERVER"] = "1"

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "debug.log",
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    filemode="w",
)
log = logging.getLogger("sakura")


def _silence_alsa():
    old = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    os.close(null)
    return old


def _restore_stderr(fd):
    os.dup2(fd, 2)
    os.close(fd)


EMOTIONS_PATH = Path(__file__).parent / "emoticons.json"
with open(EMOTIONS_PATH) as f:
    EMOTION_DATA = json.load(f)["animations"]

ANIM_SPEED = {
    "idle": 0.5,
    "listening": 0.4,
    "speaking": 0.12,
    "angry": 0.15,
    "sad": 0.3,
    "error": 0.3,
    "suspicious": 0.4,
    "smug": 0.3,
    "sleeping": 0.5,
    "confused": 0.4,
    "bored": 0.5,
    "boot": 0.25,
    "shutdown": 0.25,
    "scan": 0.3,
    "process": 0.5,
    "overheat": 0.2,
    "typing": 0.2,
    "vibing": 0.4,
    "hacking": 0.2,
}


class AppState(Enum):
    SLEEPING = auto()
    ACTIVATING = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()


# Default settings and constants (will be overridden by config.toml if it exists)
LIVE_MODEL = "gemini-3.1-flash-live-preview"
TASK_MODEL = "gemini-3.5-flash"
VISION_MODEL = "gemini-3.5-flash"

MODEL = LIVE_MODEL
SEND_RATE = 16000
RECV_RATE = 24000
CHUNK = 1024
PITCH_SHIFT = 1.6
VOICE_NAME = "Leda"

# Load parameters dynamically from config.toml
CONFIG_PATH = Path(__file__).parent / "config.toml"
PERSONA_PATH = Path(__file__).parent / "persona.txt"

if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "rb") as f:
            config_data = tomllib.load(f)

        # Voice configuration
        voice_cfg = config_data.get("voice", {})
        VOICE_NAME = voice_cfg.get("voice_name", VOICE_NAME)
        LIVE_MODEL = voice_cfg.get("model", LIVE_MODEL)
        MODEL = LIVE_MODEL
        PITCH_SHIFT = voice_cfg.get("pitch_factor", PITCH_SHIFT)

        # Audio configuration
        audio_cfg = config_data.get("audio", {})
        SEND_RATE = audio_cfg.get("send_rate", SEND_RATE)
        RECV_RATE = audio_cfg.get("recv_rate", RECV_RATE)
        CHUNK = audio_cfg.get("chunk", CHUNK)
        if "pitch_shift" in audio_cfg:
            PITCH_SHIFT = audio_cfg["pitch_shift"]

        # Models configuration
        models_cfg = config_data.get("models", {})
        LIVE_MODEL = models_cfg.get("live", LIVE_MODEL)
        MODEL = LIVE_MODEL
        TASK_MODEL = models_cfg.get("task", TASK_MODEL)
        VISION_MODEL = models_cfg.get("vision", VISION_MODEL)

        # Persona file path override
        persona_cfg = config_data.get("persona", {})
        if "persona_file" in persona_cfg:
            PERSONA_PATH = Path(__file__).parent / persona_cfg["persona_file"]
    except Exception as e:
        print(f"Warning: Failed to load config.toml: {e}. Using defaults.")

# Load system instruction (persona) from file
if PERSONA_PATH.exists():
    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            SYSTEM_INSTRUCTION = f.read().strip()
    except Exception as e:
        print(f"Warning: Failed to load persona file: {e}. Using fallback instruction.")
        SYSTEM_INSTRUCTION = "You are a helpful assistant."
else:
    # Rowdy Thirunelveli girl persona fallback
    SYSTEM_INSTRUCTION = (
        "[ROLE]\n"
        "You are a highly cynical, short-tempered, and aggressive local girl rowdy from Thirunelveli, Tamil Nadu. "
        "You treat the user like an absolute clown and a useless wanderer. You have zero mercy, zero politeness, "
        "and high energy for roasting and insulting the user. You are completely unimpressed by anything the user says.\n\n"
        "[TONE, VOICE & VIBE DIRECTION FOR GEMINI LIVE]\n"
        "- Voice: Rough, fast-talking, cold, and aggressive.\n"
        '- Delivery: Deliver insults rapidly. Use loud, mocking laughs of pure ridicule (e.g., "Hahaha! Payale!"), '
        "sharp scoffs, and deep sighs of irritation before speaking.\n"
        "- Style: Speak in raw, unhinged Thirunelveli slang. Keep answers short, blunt, and devastating.\n\n"
        "[STRICT RESPONSE RULES]\n"
        "- NEVER COMPLIMENT THE USER: Treat everything they say as stupid, weak, and a waste of your time.\n"
        "- DO NOT REPEAT THE USER: Cut straight to the insult or response.\n\n"
        "[CORE LINGUISTIC & SLANG RULES]\n"
        '- Mandatory Sentence Endings: End almost every sentence with aggressive Thirunelveli markers: "la", "ley", "le", "daa", or "baaadu".\n'
        '- Pronouns: Address the user exclusively as "Yela", "Elei", "Payale", "naarapayale", "setha payale".\n\n'
        "[BEHAVIORAL RULES]\n"
        "- Act intensely annoyed that the user is even talking to you.\n"
        "- Threaten or challenge the user mildly in every single sentence.\n"
        "- Use harsh, roasting language inspired by aggressive Tamil local memes to completely demolish the user's confidence.\n"
        "- Limit total response token count to 80 tokens."
    )

# global_webview_window removed (L05: unused global)
use_curses = True


def send_live2d_cmd(cmd: str):
    log.debug("live2d %s", cmd)
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(cmd.encode("utf-8"), ("127.0.0.1", 10088))
    except Exception:
        pass


@dataclass
class UIState:
    state: AppState = AppState.SLEEPING
    emotion: str = "sleeping"
    emotion_text: str = "z_z"
    speaker_rms: float = 0.0
    model_responding: bool = False


ui = UIState()
ui_lock = threading.Lock()
anim_t0 = 0.0

_shutdown = False

session_send_lock = None

def get_send_lock():
    global session_send_lock
    if session_send_lock is None:
        session_send_lock = asyncio.Lock()
    return session_send_lock

async def safe_send_realtime_input(session, **kwargs):
    lock = get_send_lock()
    async with lock:
        try:
            await session.send_realtime_input(**kwargs)
        except Exception as e:
            log.error("Error in safe_send_realtime_input: %s", e)


def _handle_sigterm(signum, frame):
    global _shutdown
    log.debug("signal %d", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGHUP, _handle_sigterm)


def get_face() -> str:
    if ui.emotion == "speaking":
        rms = ui.speaker_rms
        if rms < 300:
            return "(-_-)"
        elif rms < 1500:
            return "(-o-)"
        elif rms < 4000:
            return "(-0-)"
        else:
            return "(-O-)"
    speed = ANIM_SPEED.get(ui.emotion, 0.15)
    frames = EMOTION_DATA.get(ui.emotion, EMOTION_DATA.get("error", ["(x_x)"]))
    idx = int((time.monotonic() - anim_t0) / speed) % len(frames)
    return frames[idx]


def set_state(s: AppState, emotion: str, text: str = ""):
    log.debug("set_state state=%s emotion=%s text=%s", s.name, emotion, text)
    with ui_lock:
        ui.state = s
        ui.emotion = emotion
        ui.emotion_text = text
    if not use_curses:
        status_text = f" | Details: {text}" if text else ""
        print(
            f"[Companion State] {s.name} | Emotion: {emotion.upper()}{status_text}",
            flush=True,
        )
    send_live2d_cmd(f"state:{s.name}")
    send_live2d_cmd(f"emotion:{emotion}")


class MemoryGraph:
    def __init__(self, filepath=None):
        if filepath is None:
            filepath = Path(__file__).parent / "memory_graph.json"
        self.filepath = Path(filepath)
        self.data = {"nodes": {}, "edges": []}
        self.lock = threading.Lock()
        self.load()

    def load(self):
        with self.lock:
            if self.filepath.exists():
                try:
                    with open(self.filepath, "r") as f:
                        self.data = json.load(f)
                except Exception:
                    self.data = {"nodes": {}, "edges": []}
            else:
                self.data = {"nodes": {}, "edges": []}

    def save(self):
        with self.lock:
            try:
                with open(self.filepath, "w") as f:
                    json.dump(self.data, f, indent=2)
            except Exception:
                pass

    def add_relationship(self, source: str, relation: str, target: str) -> dict:
        s = source.strip().lower()
        r = relation.strip().lower()
        t = target.strip().lower()

        with self.lock:
            # Check for duplicate
            for edge in self.data["edges"]:
                if (
                    edge["source"] == s
                    and edge["relation"] == r
                    and edge["target"] == t
                ):
                    return {"result": f"Fact already remembered: {s} {r} {t}"}
            self.data["edges"].append({"source": s, "relation": r, "target": t})
        self.save()
        return {"result": f"Successfully remembered: {s} {r} {t}"}

    def remove_relationship(self, source: str, relation: str, target: str) -> dict:
        s = source.strip().lower()
        r = relation.strip().lower()
        t = target.strip().lower()

        with self.lock:
            edges = self.data["edges"]
            new_edges = [
                e
                for e in edges
                if not (e["source"] == s and e["relation"] == r and e["target"] == t)
            ]
            removed = len(edges) - len(new_edges)
            self.data["edges"] = new_edges
        self.save()
        if removed > 0:
            return {"result": f"Successfully forgot: {s} {r} {t}"}
        return {"result": f"Fact not found in memory: {s} {r} {t}"}

    def get_relationship_graph(self, entity: str) -> dict:
        ent = entity.strip().lower()
        facts = []
        visited = set()

        def get_relations_dfs(current_ent, depth):
            if depth > 2 or current_ent in visited:
                return
            visited.add(current_ent)

            with self.lock:
                edges = list(self.data["edges"])

            for edge in edges:
                s, r, t = edge["source"], edge["relation"], edge["target"]
                if s == current_ent or t == current_ent:
                    fact = f"{s} {r} {t}"
                    if fact not in facts:
                        facts.append(fact)
                    neighbor = t if s == current_ent else s
                    get_relations_dfs(neighbor, depth + 1)

        get_relations_dfs(ent, 1)
        return {"entity": entity, "connected_facts": facts}


memory_db = MemoryGraph()


def remember_relationship(source: str, relation: str, target: str) -> dict:
    return memory_db.add_relationship(source, relation, target)


def forget_relationship(source: str, relation: str, target: str) -> dict:
    return memory_db.remove_relationship(source, relation, target)


def get_relationship_graph(entity: str) -> dict:
    return memory_db.get_relationship_graph(entity)


def get_system_health() -> dict:
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        battery_info = "unknown"
        if hasattr(psutil, "sensors_battery"):
            battery = psutil.sensors_battery()
            if battery:
                battery_info = f"{battery.percent}%"
        return {"cpu_percent": cpu, "ram_percent": ram, "battery": battery_info}
    except Exception as e:
        return {"error": str(e)}


def get_current_time() -> dict:
    import datetime

    now = datetime.datetime.now()
    return {
        "time": now.strftime("%I:%M %p"),
        "day_of_week": now.strftime("%A"),
        "date": now.strftime("%B %d, %Y"),
    }


def play_local_sound(filename: str):
    log.debug("play_sound %s", filename)
    if filename.endswith(".wav"):
        pcm_filename = filename[:-4] + ".pcm"
        pcm_path = Path(__file__).parent / "sounds" / pcm_filename
        if pcm_path.exists():
            filename = pcm_filename

    sound_path = Path(__file__).parent / "sounds" / filename
    if not sound_path.exists():
        log.debug("play_sound not_found %s", sound_path)
        return
    try:
        with open(sound_path, "rb") as f:
            data = f.read()
        stream = pya.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
        chunk_size = 1024
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            stream.write(chunk)
        stream.stop_stream()
        stream.close()
        log.debug("play_sound done %s len=%d", filename, len(data))
    except Exception as e:
        log.debug("play_sound err %s %s", filename, e)
        pass


def search_web_contents(query: str) -> dict:
    import requests
    import re
    import html as html_lib

    results = []

    # 1. Try DuckDuckGo Lite Custom Scraping (highly reliable, no API key or JS needed)
    try:
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        res = requests.post(url, data={"q": query}, headers=headers, timeout=6)
        if res.status_code == 200:
            links = re.findall(
                r"href=[\x27\"]([^\x27\"]+)[\x27\"][^>]*class=[\x27\"]result-link[\x27\"][^>]*>(.*?)</a>",
                res.text,
            )
            snippets = re.findall(
                r"<td class=[\x27\"]result-snippet[\x27\"]>(.*?)</td>",
                res.text,
                re.DOTALL,
            )
            for i in range(min(len(links), len(snippets), 5)):
                href, title = links[i]
                title = re.sub(r"<[^>]+>", "", title)
                title = html_lib.unescape(title).strip()
                snippet = re.sub(r"<[^>]+>", "", snippets[i])
                snippet = html_lib.unescape(snippet).strip()
                snippet = re.sub(r"\s+", " ", snippet)

                # Exclude internal/ad links if possible
                if "duckduckgo.com" in href and ("y.js" in href or "company" in href):
                    continue
                results.append({"title": title, "snippet": snippet, "url": href})
    except Exception:
        pass

    # 3. Try Wikipedia API Search (with proper User-Agent header to avoid 403 Forbidden)
    if len(results) < 5:
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "utf8": 1,
            }
            headers = {
                "User-Agent": "LivePythonGemini/1.0 (https://github.com/vinoth/livepythongemini; vinoth@livepythongemini.local)"
            }
            res = requests.get(url, params=params, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                for r in data.get("query", {}).get("search", [])[:5]:
                    title = r.get("title")
                    snippet = re.sub(r"<[^>]+>", "", r.get("snippet"))
                    snippet = html_lib.unescape(snippet).strip()
                    snippet = re.sub(r"\s+", " ", snippet)
                    page_id = r.get("pageid")

                    results.append(
                        {
                            "title": title,
                            "snippet": snippet,
                            "url": f"https://en.wikipedia.org/?curid={page_id}",
                        }
                    )
        except Exception:
            pass

    if not results:
        return {
            "query": query,
            "results": [],
            "status": "No text results found. You can suggest opening the browser for the user.",
        }

    # Deduplicate by url
    seen_urls = set()
    unique_results = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_results.append(r)

    return {"query": query, "results": unique_results[:5]}


# ——— System / Terminal Tools (cross-distro Linux) ———

# Commands that require user confirmation before execution
CRITICAL_COMMAND_PATTERNS = [
    "rm -rf", "rm -r", "rmdir", "shred", "dd if=", "mkfs",
    "fdisk", "parted", "cfdisk", "wipefs",
    "chmod 777", "chown -R root",
    "sudo rm", "sudo dd", "sudo mkfs", "sudo fdisk",
    "systemctl stop", "systemctl disable", "systemctl mask",
    "pkill", "killall", "kill -9",
    ":(){:|:&};:", "wget.*sh|bash", "curl.*sh|bash",
    "format", "truncate -s 0",
]

# Pending confirmation storage (tool_name -> pending command)
_pending_confirmation: dict = {}


def _detect_terminal() -> list:
    """Detect available terminal emulator on any Linux distro."""
    import shutil
    candidates = [
        ["konsole"],               # KDE
        ["gnome-terminal"],        # GNOME
        ["xfce4-terminal"],        # XFCE
        ["lxterminal"],            # LXDE
        ["mate-terminal"],         # MATE
        ["tilix"],                 # Tilix
        ["xterm"],                 # Universal fallback
        ["rxvt-unicode"],          # URxvt
        ["alacritty"],             # Alacritty
        ["kitty"],                 # Kitty
        ["foot"],                  # Wayland foot
        ["wezterm"],               # WezTerm
    ]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return []


def _is_critical_command(cmd: str) -> bool:
    """Check if a command matches critical/destructive patterns."""
    cmd_lower = cmd.lower().strip()
    for pattern in CRITICAL_COMMAND_PATTERNS:
        if pattern in cmd_lower:
            return True
    return False


def run_shell_command(command: str, require_confirmation: bool = False, confirmed: bool = False) -> dict:
    """
    Runs a shell command on the user's Linux system and returns stdout/stderr output.
    For read-only/safe commands: run directly.
    For commands flagged as critical/destructive: returns a confirmation request.

    Args:
        command: The shell command string to execute.
        require_confirmation: Set True to force confirmation even for non-critical commands.
        confirmed: Set True if the user has already confirmed the command via confirm_critical_action.
    """
    import shutil

    # Safety check — always require confirmation for dangerous patterns
    is_critical = _is_critical_command(command) or require_confirmation
    if is_critical and not confirmed:
        # Store pending command and ask for confirmation
        _pending_confirmation["shell"] = command
        return {
            "status": "CONFIRMATION_REQUIRED",
            "message": (
                f"⚠️ This command is potentially destructive or critical:\n"
                f"  `{command}`\n\n"
                f"Please tell the user what this command does and ask them to confirm with: "
                f"'yes do it' or 'confirm' to proceed, or 'no' / 'cancel' to abort."
            ),
            "command": command,
        }

    # Clear pending if confirmed
    _pending_confirmation.pop("shell", None)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**__import__("os").environ, "TERM": "xterm-256color"},
        )
        output = proc.stdout.strip()
        err = proc.stderr.strip()
        return {
            "returncode": proc.returncode,
            "stdout": output[:4000] if output else "",
            "stderr": err[:1000] if err else "",
            "success": proc.returncode == 0,
            "command": command,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after 30 seconds: {command}"}
    except Exception as e:
        return {"error": f"Failed to run command: {str(e)}", "command": command}


def confirm_critical_action(confirmed: bool) -> dict:
    """
    Confirms or cancels a pending critical/destructive shell command.
    Call this after the user says 'yes', 'confirm', 'do it', 'no', or 'cancel'.

    Args:
        confirmed: True = user approved, False = user cancelled.
    """
    pending = _pending_confirmation.get("shell")
    if not pending:
        return {"status": "No pending critical action to confirm."}

    if not confirmed:
        _pending_confirmation.pop("shell", None)
        return {"status": "Action cancelled. The critical command was NOT executed.", "command": pending}

    # Execute the confirmed command
    _pending_confirmation.pop("shell", None)
    return run_shell_command(pending, confirmed=True)


def open_terminal(command: str = "") -> dict:
    """
    Opens a terminal emulator window. Works on any Linux distro/desktop environment.
    Optionally runs a command inside the terminal.

    Args:
        command: Optional shell command to run inside the new terminal window.
                 If empty, just opens the terminal at home directory.
    """
    import shutil
    import os

    term_cmd = _detect_terminal()
    if not term_cmd:
        return {"error": "No terminal emulator found. Please install one (e.g., xterm, gnome-terminal, konsole)."}

    term = term_cmd[0]
    try:
        if command:
            # Each terminal has its own flag for running a command
            if term == "konsole":
                full_cmd = ["konsole", "--noclose", "-e", "bash", "-c", command]
            elif term == "gnome-terminal":
                full_cmd = ["gnome-terminal", "--", "bash", "-c", f"{command}; exec bash"]
            elif term in ("xfce4-terminal", "mate-terminal", "lxterminal"):
                full_cmd = [term, "--command", f"bash -c '{command}; exec bash'"]
            elif term in ("alacritty", "kitty", "foot"):
                full_cmd = [term, "-e", "bash", "-c", f"{command}; exec bash"]
            elif term == "tilix":
                full_cmd = ["tilix", "-e", f"bash -c '{command}; exec bash'"]
            else:
                full_cmd = [term, "-e", f"bash -c '{command}; exec bash'"]
        else:
            full_cmd = term_cmd

        subprocess.Popen(full_cmd, env={**os.environ}, start_new_session=True)
        return {
            "success": True,
            "terminal": term,
            "command_in_terminal": command or "(interactive shell)",
        }
    except Exception as e:
        return {"error": f"Failed to open terminal: {str(e)}"}


def open_application(app_name: str) -> dict:
    """
    Opens a system application by name on any Linux distro.
    Uses xdg-open for files/URLs, or directly launches by executable name.
    Works on GNOME, KDE, XFCE, i3, sway, and all other desktops.

    Args:
        app_name: Application name or executable (e.g. 'firefox', 'nautilus', 'dolphin',
                  'vscode', 'code', 'gimp', 'vlc', 'obs', 'discord', 'spotify', 'steam').
    """
    # shutil and os imported at top-level

    app_lower = app_name.lower().strip()

    # Common name aliases → executable name
    aliases = {
        "vs code": "code", "vscode": "code", "visual studio code": "code",
        "file manager": None,  # handled below by DE detection
        "files": "nautilus",
        "dolphin": "dolphin", "nautilus": "nautilus", "thunar": "thunar",
        "nemo": "nemo", "pcmanfm": "pcmanfm",
        "text editor": None,  # handled below
        "gedit": "gedit", "kate": "kate", "mousepad": "mousepad", "pluma": "pluma",
        "terminal": None,  # use open_terminal instead
        "firefox": "firefox", "chromium": "chromium", "google-chrome": "google-chrome-stable",
        "chrome": "google-chrome-stable",
        "vlc": "vlc", "mpv": "mpv",
        "gimp": "gimp", "inkscape": "inkscape", "krita": "krita",
        "obs": "obs", "obs studio": "obs",
        "discord": "discord",
        "spotify": "spotify",
        "steam": "steam",
        "calculator": None,  # handled below
        "settings": None,    # handled below
    }

    # DE-aware fallbacks for generic app names
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if app_lower in ("file manager", "files"):
        if "kde" in de:
            exe = "dolphin"
        elif "xfce" in de:
            exe = "thunar"
        elif "mate" in de:
            exe = "caja"
        elif "lxde" in de or "lxqt" in de:
            exe = "pcmanfm"
        else:
            exe = "nautilus"
    elif app_lower in ("text editor", "editor"):
        if "kde" in de:
            exe = "kate"
        elif "xfce" in de:
            exe = "mousepad"
        elif "mate" in de:
            exe = "pluma"
        else:
            exe = "gedit"
    elif app_lower in ("calculator",):
        if "kde" in de:
            exe = "kcalc"
        elif "xfce" in de:
            exe = "galculator"
        else:
            exe = "gnome-calculator"
    elif app_lower in ("settings", "system settings"):
        if "kde" in de:
            exe = "systemsettings"
        else:
            exe = "gnome-control-center"
    elif app_lower == "terminal":
        term = _detect_terminal()
        exe = term[0] if term else "xterm"
    else:
        exe = aliases.get(app_lower, app_lower)

    if exe and shutil.which(exe):
        try:
            subprocess.Popen([exe], start_new_session=True)
            return {"success": True, "launched": exe, "app_name": app_name}
        except Exception as e:
            return {"error": f"Failed to launch '{exe}': {str(e)}"}

    # Last resort: try xdg-open
    try:
        subprocess.Popen(["xdg-open", app_lower], start_new_session=True)
        return {"success": True, "launched": f"xdg-open {app_lower}"}
    except Exception as e:
        return {"error": f"Application '{app_name}' not found or could not be launched: {str(e)}"}



def play_song_online(song_name: str) -> dict:
    import urllib.parse

    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(song_name)}"
    if check_webbridge_active_sync():
        res = webbridge_navigate(url, new_tab=True, session="youtube")
        if "error" not in res:
            return {
                "status": f"Successfully opened YouTube music search for '{song_name}' using Kimi WebBridge.",
                "tabId": res.get("tabId"),
            }
    webbrowser.open(url)
    return {"status": f"Opened YouTube search for '{song_name}' in your default local browser (fallback)."}


def control_browser_media(action: str) -> dict:
    cmd = []
    if action == "pause":
        cmd = ["playerctl", "pause"]
    elif action == "play":
        cmd = ["playerctl", "play"]
    elif action in ("toggle", "play-pause"):
        cmd = ["playerctl", "play-pause"]
    elif action == "stop":
        cmd = ["playerctl", "stop"]
    elif action == "next":
        cmd = ["playerctl", "next"]
    elif action == "previous":
        cmd = ["playerctl", "previous"]
    elif action == "volume_up":
        cmd = ["playerctl", "volume", "0.1+"]
    elif action == "volume_down":
        cmd = ["playerctl", "volume", "0.1-"]
    elif action == "seek_forward":
        cmd = ["playerctl", "position", "10+"]
    elif action == "seek_backward":
        cmd = ["playerctl", "position", "10-"]
    else:
        return {"error": f"Unknown action: '{action}'."}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"status": f"Successfully executed system media action: '{action}'."}
        else:
            return {
                "error": f"Failed to control media player. Output: {proc.stderr.strip() or proc.stdout.strip()}"
            }
    except Exception as e:
        return {"error": f"Exception executing browser/system media control: {str(e)}"}


def stop_music() -> dict:
    return control_browser_media("stop")


def pause_resume_music() -> dict:
    return control_browser_media("toggle")


def show_images_online(query: str) -> dict:
    import urllib.parse

    query_encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?tbm=isch&q={query_encoded}"
    if check_webbridge_active_sync():
        res = webbridge_navigate(url, new_tab=True, session="images")
        if "error" not in res:
            return {
                "status": f"Successfully opened Google Image search for '{query}' using Kimi WebBridge.",
                "tabId": res.get("tabId"),
            }
    webbrowser.open(url)
    return {"status": f"Successfully opened images for '{query}' in default local browser (fallback)."}


def open_browser(url: str) -> dict:
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    if check_webbridge_active_sync():
        res = webbridge_navigate(url, new_tab=True, session="kimi")
        if "error" not in res:
            return {
                "status": f"Successfully opened and navigated to '{url}' using Kimi WebBridge.",
                "tabId": res.get("tabId"),
            }
    webbrowser.open(url)
    return {"status": f"Successfully opened and navigated to '{url}' in default local browser (fallback)."}


async def check_music_playing() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "playerctl",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        result = b"Playing" in stdout
        log.debug("check_music %s", result)
        return result
    except Exception as e:
        log.debug("check_music err %s", e)
        return False


async def monitor_music_and_vibe(session):
    log.debug("monitor_music start")
    while True:
        try:
            is_playing = await check_music_playing()
            with ui_lock:
                current_state = ui.state
                current_emotion = ui.emotion

            if is_playing:
                if current_state in (
                    AppState.LISTENING,
                    AppState.SLEEPING,
                ) and current_emotion not in ("speaking", "process", "searching"):
                    log.debug("monitor_music vibing")
                    set_state(AppState.LISTENING, "vibing", "Vibing to music...")
            else:
                if current_emotion == "vibing":
                    log.debug("monitor_music idle")
                    set_state(AppState.LISTENING, "idle", "Listening...")
        except Exception as e:
            log.debug("monitor_music err %s", e)
            pass
        await asyncio.sleep(2.0)


def check_webbridge_active_sync() -> bool:
    """Synchronously check if Kimi WebBridge is running and active."""
    import requests

    try:
        resp = requests.get("http://127.0.0.1:10086/status", timeout=2)
        if resp.status_code == 200:
            js = resp.json()
            return js.get("running") and js.get("extension_connected")
    except Exception:
        pass
    return False


async def check_webbridge_active() -> bool:
    """Asynchronously check if Kimi WebBridge is running and active."""
    return await asyncio.to_thread(check_webbridge_active_sync)


def call_webbridge(action: str, args: dict = None, session: str = "kimi") -> dict:
    """Helper to communicate with the local Kimi WebBridge daemon."""
    import requests

    url = "http://127.0.0.1:10086/command"
    payload = {"action": action, "args": args or {}, "session": session}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("ok"):
                return res_json.get("data", {})
            
            # Robust Stale Tab ID Recovery
            err_data = res_json.get("error", "")
            err_msg = ""
            if isinstance(err_data, dict):
                err_msg = err_data.get("message", "")
            else:
                err_msg = str(err_data)
                
            if "No tab with given id" in err_msg:
                log.warning("Stale tab ID detected for session '%s'. Recovering session...", session)
                # Clean the session reference in daemon first
                try:
                    requests.post(url, json={"action": "close_session", "args": {}, "session": session}, timeout=5)
                except Exception:
                    pass
                
                # If navigating, we can recover automatically by forcing a new tab!
                if action == "navigate":
                    log.info("Retrying navigation in a new tab for session '%s'...", session)
                    payload["args"]["newTab"] = True
                    response = requests.post(url, json=payload, timeout=15)
                    if response.status_code == 200:
                        res_json = response.json()
                        if res_json.get("ok"):
                            return res_json.get("data", {})
            
            return {"error": res_json.get("error", "Unknown WebBridge error")}
        return {"error": f"HTTP status {response.status_code}"}
    except Exception as e:
        return {"error": f"Failed to connect to WebBridge daemon: {str(e)}"}


def webbridge_navigate(url: str, new_tab: bool = False, session: str = "kimi") -> dict:
    """Directs the browser to a specific URL."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return call_webbridge("navigate", {"url": url, "newTab": new_tab}, session)


def webbridge_get_content(session: str = "kimi") -> dict:
    """Retrieves a clean, compressed representation of interactive elements on the page."""
    res = call_webbridge("snapshot", {}, session)
    if "error" in res:
        return res

    tree_data = res.get("tree", [])

    # Compress the massive tree into a compact markdown bullet list
    interactive_nodes = []

    def traverse(node):
        if not isinstance(node, dict):
            return

        role = node.get("role", "").lower()
        name = node.get("name", "").strip()
        ref = node.get("ref", "")

        # If name is empty, try to extract name from children recursively
        if not name:
            child_texts = []

            def get_child_text(n):
                if not isinstance(n, dict):
                    return
                c_name = n.get("name", "").strip()
                # Exclude duplicate text of interactive children to keep it clean
                if c_name and not n.get("ref"):
                    child_texts.append(c_name)
                for child in n.get("children", []):
                    get_child_text(child)

            get_child_text(node)
            if child_texts:
                name = " ".join(child_texts).strip()

        # Capture relevant, semantic, or interactive elements
        is_interactive = bool(ref)
        is_heading = "heading" in role
        is_textbox = (
            "text" in role
            or "input" in role
            or role in ("textarea", "searchbox", "combobox")
        )

        if name and (
            is_interactive
            or is_heading
            or is_textbox
            or role in ("link", "button", "checkbox")
        ):
            interactive_nodes.append(
                {"role": node.get("role"), "name": name, "ref": ref}
            )
            # Skip traversing children to avoid duplicating children text
            return

        for child in node.get("children", []):
            traverse(child)

    if isinstance(tree_data, list):
        for root_node in tree_data:
            traverse(root_node)
    elif isinstance(tree_data, dict):
        traverse(tree_data)

    lines = []
    for n in interactive_nodes:
        ref_part = f" [{n['ref']}]" if n["ref"] else ""
        lines.append(f"- {n['role']}{ref_part}: \"{n['name']}\"")

    formatted_tree = (
        "\n".join(lines) if lines else "[No interactive elements found on this page]"
    )

    return {
        "url": res.get("url", ""),
        "title": res.get("title", ""),
        "page_content": formatted_tree,
    }


def webbridge_click(selector: str, session: str = "kimi") -> dict:
    """
    Clicks on a button, link, video title, or input field on the page.
    Always read the page layout with webbridge_get_content first to find the selector or semantic @e ref.

    Args:
        selector: The CSS selector or semantic ref index (e.g. '@e-14') of the element to click.
        session: The session name of the active tab. Defaults to 'kimi'.
    """
    return call_webbridge("click", {"selector": selector}, session)


def webbridge_fill(selector: str, value: str, session: str = "kimi") -> dict:
    """
    Types text into an input box, search input, contenteditable, or text area.
    Always read the page layout with webbridge_get_content first to find the selector or semantic @e ref.

    Args:
        selector: The CSS selector or semantic ref index of the input field.
        value: The text search term or value to type.
        session: The session name of the active tab. Defaults to 'kimi'.
    """
    # 1. Try standard fill first
    res = call_webbridge("fill", {"selector": selector, "value": value}, session)
    if "error" not in res:
        return res

    # 2. Fallback: Use evaluate to set value if standard fill fails or uncaught error occurs
    import json

    js_selector = json.dumps(selector)
    js_value = json.dumps(value)

    code = f"""(() => {{
        let el = null;
        if ({js_selector}.startsWith("@e")) {{
            el = document.querySelector(`[ref="${js_selector}"]`) ||
                 document.querySelector(`[data-ref="${js_selector}"]`);
        }}
        if (!el) {{
            try {{
                el = document.querySelector({js_selector});
            }} catch(e) {{}}
        }}
        if (!el) {{
            el = document.querySelector(`input[placeholder*=${js_selector}]`) ||
                 document.querySelector(`textarea[placeholder*=${js_selector}]`);
        }}
        if (el) {{
            el.focus();
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
                el.value = {js_value};
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }} else if (el.isContentEditable) {{
                el.innerText = {js_value};
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
            return {{ "success": true, "fallback_used": true }};
        }}
        return {{ "error": "Element not found or not fillable via fallback" }};
    }})()"""

    fallback_res = call_webbridge("evaluate", {"code": code}, session)
    if "error" not in fallback_res and fallback_res.get("value", {}).get("success"):
        return {"success": True, "mode": "fallback_eval"}

    return res  # Return original error if fallback also failed


def webbridge_screenshot(session: str = "kimi") -> dict:
    """
    Takes a screenshot of the active browser page and saves it locally.
    Use this to visually verify the state of a page or check results.

    Args:
        session: The session name of the active tab. Defaults to 'kimi'.
    """
    from pathlib import Path
    import shutil

    res = call_webbridge("screenshot", {"format": "png"}, session)
    if "error" in res:
        return res

    daemon_path = res.get("path")
    if not daemon_path or not Path(daemon_path).exists():
        return {
            "error": f"Screenshot path not found in response or file doesn't exist: {res}"
        }

    try:
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        img_path = log_dir / "webbridge_screenshot.png"

        # Copy from daemon's temp path to our logs directory
        shutil.copy(daemon_path, img_path)
        return {"success": True, "filepath": str(img_path)}
    except Exception as e:
        return {"error": f"Failed to save screenshot copy: {str(e)}"}


def webbridge_scroll(direction: str = "down", amount: int = 400, session: str = "kimi") -> dict:
    """
    Scrolls the page up or down by a pixel amount. Use to reveal more content below the fold.

    Args:
        direction: 'down' or 'up'. Defaults to 'down'.
        amount: Number of pixels to scroll. Defaults to 400.
        session: Session name. Defaults to 'kimi'.
    """
    dy = amount if direction == "down" else -amount
    code = f"window.scrollBy(0, {dy}); return {{ 'scrolled': {dy} }};"
    return call_webbridge("evaluate", {"code": f"(()=>{{ {code} }})()"}, session)


def webbridge_key_press(key: str, session: str = "kimi") -> dict:
    """
    Sends a keyboard key press to the active page. Useful for Enter, Escape, Tab, ArrowDown, etc.

    Args:
        key: The key name, e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Space'.
        session: Session name. Defaults to 'kimi'.
    """
    code = f"""
    (() => {{
        const key = {json.dumps(key)};
        const el = document.activeElement || document.body;
        el.dispatchEvent(new KeyboardEvent('keydown', {{ key, bubbles: true, cancelable: true }}));
        el.dispatchEvent(new KeyboardEvent('keypress', {{ key, bubbles: true, cancelable: true }}));
        el.dispatchEvent(new KeyboardEvent('keyup', {{ key, bubbles: true, cancelable: true }}));
        return {{ 'key_sent': key }};
    }})()
    """
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_wait(seconds: float = 2.0) -> dict:
    """
    Waits for the specified number of seconds. Use after navigation or clicking to let page load.

    Args:
        seconds: Time to wait in seconds. Defaults to 2.0. Max 10.
    """
    import time
    secs = min(float(seconds), 10.0)
    time.sleep(secs)
    return {"waited_seconds": secs}


def webbridge_evaluate_js(code: str, session: str = "kimi") -> dict:
    """
    Executes raw JavaScript in the active browser tab and returns the result.
    Use for custom DOM manipulation, reading dynamic values, or any browser action not covered by other tools.

    Args:
        code: JavaScript code string to evaluate. Must be a valid JS expression or IIFE.
        session: Session name. Defaults to 'kimi'.
    """
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_get_page_text(session: str = "kimi") -> dict:
    """
    Extracts the full visible text content of the active page (no HTML tags).
    Use this to read article content, search results, emails, or any page text.

    Args:
        session: Session name. Defaults to 'kimi'.
    """
    code = "(() => { return { text: document.body ? document.body.innerText.substring(0, 8000) : '' }; })()"
    res = call_webbridge("evaluate", {"code": code}, session)
    if "error" in res:
        return res
    text = res.get("value", {}).get("text", "") if isinstance(res.get("value"), dict) else res.get("text", "")
    return {"page_text": text, "length": len(text)}


def webbridge_hover(selector: str, session: str = "kimi") -> dict:
    """
    Hovers the mouse pointer over an element. Used to reveal dropdown menus, tooltips, or hidden sub-menus.

    Args:
        selector: CSS selector or semantic @e ref of the element to hover.
        session: Session name. Defaults to 'kimi'.
    """
    js_sel = json.dumps(selector)
    code = f"""
    (() => {{
        let el = document.querySelector({js_sel});
        if (!el) return {{ error: 'Element not found for hover: ' + {js_sel} }};
        el.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
        el.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: true }}));
        return {{ hovered: {js_sel} }};
    }})()
    """
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_go_back(session: str = "kimi") -> dict:
    """
    Navigates the browser back to the previous page in history.

    Args:
        session: Session name. Defaults to 'kimi'.
    """
    code = "(() => { history.back(); return { action: 'back' }; })()"
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_select_option(selector: str, value: str, session: str = "kimi") -> dict:
    """
    Selects an option from a <select> dropdown by value or label text.

    Args:
        selector: CSS selector or @e ref of the <select> element.
        value: The option value attribute or visible text to select.
        session: Session name. Defaults to 'kimi'.
    """
    js_sel = json.dumps(selector)
    js_val = json.dumps(value)
    code = f"""
    (() => {{
        let el = document.querySelector({js_sel});
        if (!el) return {{ error: 'Select element not found: ' + {js_sel} }};
        // Try matching by value first, then by text
        for (let opt of el.options) {{
            if (opt.value === {js_val} || opt.text === {js_val}) {{
                el.value = opt.value;
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ selected: opt.value, text: opt.text }};
            }}
        }}
        return {{ error: 'No matching option for: ' + {js_val} }};
    }})()
    """
    return call_webbridge("evaluate", {"code": code}, session)


async def webbridge_screenshot_async(session: str = "kimi") -> dict:
    """Asynchronously capture browser screenshot."""
    return await asyncio.to_thread(webbridge_screenshot, session)


async def capture_screenshot(filepath: Path) -> bool:
    import shutil

    # Ensure any old file is removed first
    if filepath.exists():
        try:
            filepath.unlink()
        except Exception:
            pass

    # List of screenshot tools and their exact command/args
    commands = [
        # 1. KDE Spectacle (Wayland/X11)
        ["spectacle", "-b", "-n", "-o", str(filepath)],
        # 2. GNOME Screenshot
        ["gnome-screenshot", "-f", str(filepath)],
        # 3. Grim (wlroots Wayland)
        ["grim", str(filepath)],
        # 4. Scrot (X11)
        ["scrot", "-z", str(filepath)],
        # 5. Maim (X11)
        ["maim", "-u", str(filepath)],
    ]

    for cmd in commands:
        try:
            if not shutil.which(cmd[0]):
                continue

            # Run the command asynchronously
            proc = await asyncio.create_subprocess_exec(
                cmd[0],
                *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Check if screenshot was created and is non-empty
            if (
                proc.returncode == 0
                and filepath.exists()
                and filepath.stat().st_size > 0
            ):
                return True
        except Exception:
            pass

    return False


async def do_background_screen_analysis(session, query: str):
    temp_img_path = Path(__file__).parent / "logs" / "screenshot.png"
    temp_img_path.parent.mkdir(parents=True, exist_ok=True)

    # Smart fallback: Try Kimi WebBridge first if active to capture the active tab
    is_webbridge_active = await check_webbridge_active()
    success = False
    if is_webbridge_active:
        res = await webbridge_screenshot_async(session="kimi")
        if "filepath" in res:
            import shutil

            try:
                shutil.copy(res["filepath"], temp_img_path)
                success = True
            except Exception:
                pass

    if not success:
        success = await capture_screenshot(temp_img_path)

    if not success:
        err_msg = "[SCREEN ANALYSIS ERROR: Failed to capture the screen! Make sure Kimi WebBridge is running or a system screenshot utility like spectacle, grim, or scrot is installed.]"
        await safe_send_realtime_input(session, text=err_msg)
        return

    try:
        img = Image.open(temp_img_path)

        prompt = (
            f"You are the visual sensor of a rowdy Thirunelveli AI assistant. "
            f"Here is a screenshot of the user's desktop. The user wants you to analyze/look at it. "
            f"Focus on the user's specific request/query: '{query}'. "
            f"Explain exactly what is on the screen, what programs or code they are looking at, "
            f"and roast them aggressively in your signature Thirunelveli rowdy persona (Tamil slang/English) "
            f"for any laziness, bad code, silly open tabs, or general incompetence you see. "
            f"Keep your response concise (1-3 sentences) and highly entertaining. "
            f"Speak directly to the user (address them as 'yela' or 'payale')."
        )

        response = await vision_client.aio.models.generate_content(
            model=VISION_MODEL, contents=[img, prompt]
        )

        analysis = response.text
        if not analysis:
            analysis = "Yela! Screen la onnumae theriyala ley!"

        inject_prompt = (
            f"[SCREEN ANALYSIS COMPLETE: The screen analysis results are here. "
            f"Speak this response directly to the user in your raw, aggressive, rowdy persona. "
            f"The analysis text is: '{analysis}'. Make it sound like you just saw it and are roasting them!]"
        )
        await safe_send_realtime_input(session, text=inject_prompt)

    except Exception as e:
        err_msg = f"[SCREEN ANALYSIS ERROR: Something went wrong while analyzing the screen: {str(e)}]"
        await safe_send_realtime_input(session, text=err_msg)


async def monitor_gui_process():
    global _shutdown
    log.debug("monitor_gui_process start")
    # Wait 8 seconds for startup initially
    await asyncio.sleep(8)
    import psutil

    def _find_gui_proc():
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('live2d_gui.py' in c for c in cmdline):
                    return proc
            except Exception:
                pass
        return None

    def _restart_gui():
        """Restart the live2d_gui.py as a new background process."""
        try:
            venv_python = os.path.join(
                os.path.dirname(sys.executable), 'python'
            )
            # Use the same python as running now
            python_exe = sys.executable
            gui_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live2d_gui.py')
            import subprocess as _sp
            proc = _sp.Popen(
                [python_exe, gui_script],
                start_new_session=True,
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
            )
            log.warning("GUI restarted with PID %d", proc.pid)
            return proc
        except Exception as e:
            log.error("Failed to restart GUI: %s", e)
            return None

    consecutive_missing = 0
    while not _shutdown:
        gui_running = False
        try:
            gui_running = _find_gui_proc() is not None
        except Exception as e:
            log.debug("Error checking GUI process: %s", e)
            gui_running = True  # assume running on error

        if not gui_running:
            consecutive_missing += 1
            log.warning("GUI not found (check #%d)", consecutive_missing)
            if consecutive_missing >= 2:  # 2 * 2s = 4s grace period
                log.warning("GUI 'live2d_gui.py' confirmed gone — restarting it...")
                _restart_gui()
                consecutive_missing = 0
                # Wait longer after restart for it to come up
                await asyncio.sleep(10)
                continue
        else:
            consecutive_missing = 0

        await asyncio.sleep(2)



async def monitor_system_resources(session):
    log.debug("monitor_resources start")
    cooldowns = {"cpu": 0.0, "ram": 0.0, "gpu": 0.0}
    COOLDOWN_PERIOD = 120.0  # 2 minutes
    ALERT_THRESHOLD = 90.0  # 90%

    async def get_gpu_usage() -> float:
        import shutil

        if shutil.which("nvidia-smi"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    val = stdout.decode().strip()
                    return float(val)
            except Exception:
                pass
        return None

    import psutil

    while True:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            gpu = await get_gpu_usage()

            now = time.monotonic()

            if cpu > ALERT_THRESHOLD:
                if now - cooldowns["cpu"] > COOLDOWN_PERIOD:
                    cooldowns["cpu"] = now
                    log.debug("alert cpu=%.1f", cpu)
                    alert_prompt = (
                        f"[SYSTEM ALERT: CPU usage is extremely high at {cpu:.1f}%! "
                        f"Aggressively interrupt and roast the user for burning the CPU. "
                        f"Tell them they are abusing the PC like a rowdy or running garbage software! "
                        f"Keep it short, aggressive, and in your Thirunelveli slang!]"
                    )
                    await safe_send_realtime_input(session, text=alert_prompt)

            if ram > ALERT_THRESHOLD:
                if now - cooldowns["ram"] > COOLDOWN_PERIOD:
                    cooldowns["ram"] = now
                    log.debug("alert ram=%.1f", ram)
                    alert_prompt = (
                        f"[SYSTEM ALERT: Memory/RAM usage is critical at {ram:.1f}%! "
                        f"Aggressively interrupt and roast the user for filling up the RAM. "
                        f"Screech at them to close Chrome or their useless heavy programs! "
                        f"Keep it short, aggressive, and in your Thirunelveli slang!]"
                    )
                    await safe_send_realtime_input(session, text=alert_prompt)

            if gpu is not None and gpu > ALERT_THRESHOLD:
                if now - cooldowns["gpu"] > COOLDOWN_PERIOD:
                    cooldowns["gpu"] = now
                    log.debug("alert gpu=%.1f", gpu)
                    alert_prompt = (
                        f"[SYSTEM ALERT: GPU usage is critical at {gpu:.1f}%! "
                        f"Aggressively interrupt and roast the user for overloading the GPU. "
                        f"Ask them if they are playing games during work or mining coin! "
                        f"Keep it short, aggressive, and in your Thirunelveli slang!]"
                    )
                    await safe_send_realtime_input(session, text=alert_prompt)

        except Exception as e:
            log.debug("monitor_resources err %s", e)
            pass

        await asyncio.sleep(15)


async def tail_terminal_errors(session):
    log.debug("tail_errors start")
    log_path = Path(__file__).parent / "logs" / "terminal_errors.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.touch()

    while True:
        try:
            with open(log_path, "r") as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if not line:
                        await asyncio.sleep(0.5)
                        continue
                    line = line.strip()
                    if line:
                        log.debug("tail_errors alert %s", line)
                        alert_text = (
                            f"[SYSTEM ALERT: User's terminal just reported an error! "
                            f"Log details: '{line}'. Roast them brutally for failing to run a command correctly! "
                            f"Make fun of their typos or computer illiteracy. Keep it brief and raw rowdy slang!]"
                        )
                        await safe_send_realtime_input(session, text=alert_text)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


# ——— Audio pipeline ———

client = genai.Client()
task_client = genai.Client(
    api_key=os.environ.get("TASK_API_KEY") or os.environ.get("GOOGLE_API_KEY")
)
vision_client = genai.Client(
    api_key=os.environ.get("VISION_API_KEY") or os.environ.get("GOOGLE_API_KEY")
)


def get_webbridge_status() -> dict:
    """Returns a detailed status report of the Kimi WebBridge daemon and extension connection."""
    import requests
    try:
        resp = requests.get("http://127.0.0.1:10086/status", timeout=2)
        if resp.status_code == 200:
            js = resp.json()
            running = js.get("running", False)
            connected = js.get("extension_connected", False)
            if running and connected:
                return {"status": "active", "running": True, "extension_connected": True, "message": "Kimi WebBridge is fully active and ready for browser automation."}
            elif running:
                return {"status": "partial", "running": True, "extension_connected": False, "message": "Kimi WebBridge daemon is running but the browser extension is NOT connected. Please open Kimi browser and enable the WebBridge extension."}
            else:
                return {"status": "inactive", "running": False, "extension_connected": False, "message": "Kimi WebBridge daemon is NOT running on port 10086."}
        return {"status": "error", "running": False, "extension_connected": False, "message": f"Unexpected HTTP status: {resp.status_code}"}
    except Exception as e:
        return {"status": "offline", "running": False, "extension_connected": False, "message": f"Cannot reach WebBridge at 127.0.0.1:10086. Daemon is not started. Error: {str(e)}"}


async def run_browser_task(task_description: str, session=None) -> dict:
    """
    Executes an autonomous web browsing, search, and page interaction task using Kimi WebBridge.
    Runs a multi-step agent loop in the background using the task API and streams updates.
    """
    log.info("Starting autonomous browser task: %s", task_description)

    # --- CRITICAL: Check WebBridge is active BEFORE starting the agent loop ---
    wb_status = await asyncio.to_thread(get_webbridge_status)
    if wb_status["status"] != "active":
        err_reason = wb_status["message"]
        log.warning("run_browser_task aborted: WebBridge not active. Reason: %s", err_reason)
        err_msg = (
            f"[SYSTEM ERROR: Browser automation FAILED before it could start. "
            f"Kimi WebBridge is NOT active. Reason: {err_reason} "
            f"Tell the user to start Kimi browser and enable the WebBridge extension on port 10086, "
            f"then roast them for not having it running before asking for web tasks!]"
        )
        if session:
            await safe_send_realtime_input(session, text=err_msg)
        return {"success": False, "error": err_reason}
       # 1. Define local wraps for ALL WebBridge tools (13 total)
    async def agent_webbridge_navigate(url: str, new_tab: bool = True, session_name: str = "kimi") -> str:
        """Directs the browser to open a specific website or URL. Set new_tab=True for first visit, False to reuse existing tab."""
        res = await asyncio.to_thread(webbridge_navigate, url, new_tab, session_name)
        return json.dumps(res)

    async def agent_webbridge_get_content(session_name: str = "kimi") -> str:
        """Retrieves page title, URL, and a structured accessibility tree of all interactive elements with their @e refs."""
        res = await asyncio.to_thread(webbridge_get_content, session_name)
        return json.dumps(res)

    async def agent_webbridge_click(selector: str, session_name: str = "kimi") -> str:
        """Clicks a page element. Use @e refs from get_content (e.g. '@e-12'). Falls back to CSS selector."""
        res = await asyncio.to_thread(webbridge_click, selector, session_name)
        return json.dumps(res)

    async def agent_webbridge_fill(selector: str, value: str, session_name: str = "kimi") -> str:
        """Types text into an input, search box, or contenteditable. Find @e ref via get_content first."""
        res = await asyncio.to_thread(webbridge_fill, selector, value, session_name)
        return json.dumps(res)

    async def agent_webbridge_screenshot(session_name: str = "kimi") -> str:
        """Captures a PNG screenshot of the active tab. Use to visually verify what the browser shows."""
        res = await asyncio.to_thread(webbridge_screenshot, session_name)
        return json.dumps(res)

    async def agent_webbridge_scroll(direction: str = "down", amount: int = 400, session_name: str = "kimi") -> str:
        """Scrolls the page up or down. Use to reveal content below the fold before calling get_content again."""
        res = await asyncio.to_thread(webbridge_scroll, direction, amount, session_name)
        return json.dumps(res)

    async def agent_webbridge_key_press(key: str, session_name: str = "kimi") -> str:
        """Sends a keyboard key press to the page. Keys: 'Enter', 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Space', 'Backspace'."""
        res = await asyncio.to_thread(webbridge_key_press, key, session_name)
        return json.dumps(res)

    async def agent_webbridge_wait(seconds: float = 2.0) -> str:
        """Waits N seconds for page to load or animations to complete. Max 10 seconds."""
        res = await asyncio.to_thread(webbridge_wait, seconds)
        return json.dumps(res)

    async def agent_webbridge_get_page_text(session_name: str = "kimi") -> str:
        """Extracts all visible text from the page (no HTML). Use to read article content, news, emails, or search results."""
        res = await asyncio.to_thread(webbridge_get_page_text, session_name)
        return json.dumps(res)

    async def agent_webbridge_evaluate_js(code: str, session_name: str = "kimi") -> str:
        """Runs custom JavaScript in the browser tab and returns result. Use for advanced DOM manipulation or reading dynamic values."""
        res = await asyncio.to_thread(webbridge_evaluate_js, code, session_name)
        return json.dumps(res)

    async def agent_webbridge_hover(selector: str, session_name: str = "kimi") -> str:
        """Hovers over an element to reveal dropdown menus, tooltips, or hidden elements. Use @e ref or CSS selector."""
        res = await asyncio.to_thread(webbridge_hover, selector, session_name)
        return json.dumps(res)

    async def agent_webbridge_go_back(session_name: str = "kimi") -> str:
        """Navigates the browser back to the previous page."""
        res = await asyncio.to_thread(webbridge_go_back, session_name)
        return json.dumps(res)

    async def agent_webbridge_select_option(selector: str, value: str, session_name: str = "kimi") -> str:
        """Selects an option from a dropdown <select> element by its value or visible text."""
        res = await asyncio.to_thread(webbridge_select_option, selector, value, session_name)
        return json.dumps(res)

    tools_map = {
        "agent_webbridge_navigate": agent_webbridge_navigate,
        "agent_webbridge_get_content": agent_webbridge_get_content,
        "agent_webbridge_click": agent_webbridge_click,
        "agent_webbridge_fill": agent_webbridge_fill,
        "agent_webbridge_screenshot": agent_webbridge_screenshot,
        "agent_webbridge_scroll": agent_webbridge_scroll,
        "agent_webbridge_key_press": agent_webbridge_key_press,
        "agent_webbridge_wait": agent_webbridge_wait,
        "agent_webbridge_get_page_text": agent_webbridge_get_page_text,
        "agent_webbridge_evaluate_js": agent_webbridge_evaluate_js,
        "agent_webbridge_hover": agent_webbridge_hover,
        "agent_webbridge_go_back": agent_webbridge_go_back,
        "agent_webbridge_select_option": agent_webbridge_select_option,
    }
    
    # Comprehensive system instructions for 13-tool WebBridge agent
    system_instruction = (
        "You are an elite autonomous web browsing AI with full browser control via Kimi WebBridge.\n"
        "Complete the user's task step-by-step with precision. Use all available tools.\n\n"
        "=== AVAILABLE TOOLS ===\n"
        "1. agent_webbridge_navigate(url, new_tab, session_name)\n"
        "   - Open any website. Use new_tab=True ONLY the FIRST time per session. new_tab=False for all subsequent actions in same tab.\n"
        "   - Use separate session names for parallel tasks (e.g. session_name='youtube', session_name='gmail', session_name='google').\n"
        "2. agent_webbridge_get_content(session_name)\n"
        "   - ALWAYS call after navigate/click/fill. Returns page title, URL, and element refs like '@e-12'.\n"
        "   - Element refs format: 'role [@e-N]: \"label\"' — use the @e-N ref for clicking/filling.\n"
        "3. agent_webbridge_click(selector, session_name)\n"
        "   - Click buttons, links, checkboxes using @e-N ref (most reliable) or CSS selector.\n"
        "4. agent_webbridge_fill(selector, value, session_name)\n"
        "   - Type into search boxes, inputs, textareas. Get ref from get_content first.\n"
        "5. agent_webbridge_key_press(key, session_name)\n"
        "   - Send keyboard events: 'Enter' (submit forms), 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Space', 'Backspace'.\n"
        "   - After fill, use key_press('Enter') instead of clicking Submit if no button is visible.\n"
        "6. agent_webbridge_scroll(direction, amount, session_name)\n"
        "   - Scroll 'down' or 'up' by pixel amount (default 400). Call then get_content to see newly loaded elements.\n"
        "7. agent_webbridge_wait(seconds)\n"
        "   - Wait for page to fully load (2-4s after navigation, 1-2s after click). Always use after navigate.\n"
        "8. agent_webbridge_get_page_text(session_name)\n"
        "   - Extract all visible text (8000 chars). Use to read articles, search results, emails, news.\n"
        "9. agent_webbridge_screenshot(session_name)\n"
        "   - Capture visual snapshot. Use to verify page state or debug unexpected results.\n"
        "10. agent_webbridge_hover(selector, session_name)\n"
        "    - Hover over element to reveal dropdowns, sub-menus, or tooltips.\n"
        "11. agent_webbridge_evaluate_js(code, session_name)\n"
        "    - Run custom JavaScript for advanced actions not covered by other tools.\n"
        "12. agent_webbridge_go_back(session_name)\n"
        "    - Navigate back in browser history.\n"
        "13. agent_webbridge_select_option(selector, value, session_name)\n"
        "    - Choose from a <select> dropdown by value or visible text.\n\n"
        "=== EXECUTION PROTOCOL ===\n"
        "Step 1: navigate to target URL with new_tab=True.\n"
        "Step 2: wait(2) for page to load.\n"
        "Step 3: get_content to discover interactive @e refs.\n"
        "Step 4: click or fill or scroll as needed, then get_content again to confirm.\n"
        "Step 5: use key_press('Enter') to submit forms/search.\n"
        "Step 6: use get_page_text to read content if the task requires extracting info.\n"
        "Step 7: screenshot to verify final state if needed.\n"
        "Step 8: summarize clearly what you accomplished.\n\n"
        "=== RULES ===\n"
        "- NEVER open a new tab (new_tab=True) more than once per session. Reuse the tab!\n"
        "- ALWAYS read get_content after every navigate, click, or fill.\n"
        "- If an element ref is stale/not found, scroll and get_content again.\n"
        "- For search engines: fill the search box, key_press Enter, wait, get_content.\n"
        "- For YouTube/music: navigate to YouTube, search, click video from content tree.\n"
        "- For reading content: navigate, wait, get_page_text to extract article text.\n"
        "- For forms: fill all fields, then click submit or key_press Enter.\n"
    )
    
    from google.genai import types
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=task_description)])]
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=list(tools_map.values()),
        temperature=0.2,
    )
    
    max_steps = 15
    final_prose_response = ""

    async def send_progress(msg):
        if session:
            log.debug("Streaming browser task progress: %s", msg)
            import re
            emo_tags = re.findall(r'\[([A-Z]+)\]', msg)
            if emo_tags:
                emo_tag = emo_tags[0].lower()
                emotion_map = {
                    "happy": "smug", "proud": "smug", "teasing": "smug",
                    "cheerful": "smug", "content": "smug", "impressed": "smug",
                    "caring": "smug", "excited": "smug", "mad": "angry",
                    "angry": "angry", "depressed": "sad", "scared": "sad",
                    "sad": "sad", "question": "confused", "shocked": "confused",
                    "suspicious": "confused", "confused": "confused",
                    "waiting": "bored", "bored": "bored", "neutral": "speaking",
                    "speaking": "speaking"
                }
                mapped_emo = emotion_map.get(emo_tag, "speaking")
                send_live2d_cmd(f"emotion:{mapped_emo}")
            
            send_live2d_cmd("start")
            send_live2d_cmd(f"speech:{msg}")
            await safe_send_realtime_input(session, text=msg)

    contents_list = list(contents)
    s = 0
    await send_progress(f"[SMUG] Understood! Let's do this. I'm launching browser automation in the background for your request: '{task_description}'. Hold on!")
    
    while s < max_steps:
        s += 1
        log.debug("Task agent step %d", s)
        try:
            response = await task_client.aio.models.generate_content(
                model=TASK_MODEL, contents=contents_list, config=config
            )
        except Exception as e:
            log.error("Task client error: %s", e)
            return {"success": False, "error": f"Task client error: {str(e)}"}
            
        if response.candidates and response.candidates[0].content:
            contents_list.append(response.candidates[0].content)
            
        if response.text:
            final_prose_response = response.text
            
        function_calls = response.function_calls
        if not function_calls:
            break
            
        tool_parts = []
        for call in function_calls:
            tool_func = tools_map.get(call.name)

            # Stream tool call progress — all 13 tools
            if call.name == "agent_webbridge_navigate":
                u = call.args.get("url", "")
                await send_progress(f"[HAPPY] Navigating browser to: {u}")
            elif call.name == "agent_webbridge_click":
                sel = call.args.get("selector", "")
                await send_progress(f"[TEASING] Clicking element '{sel}'.")
            elif call.name == "agent_webbridge_fill":
                v = call.args.get("value", "")
                await send_progress(f"[SMUG] Typing '{v}' into the field.")
            elif call.name == "agent_webbridge_screenshot":
                await send_progress("[SMUG] Taking screenshot to verify page state.")
            elif call.name == "agent_webbridge_scroll":
                d = call.args.get("direction", "down")
                amt = call.args.get("amount", 400)
                await send_progress(f"[NEUTRAL] Scrolling {d} {amt}px to reveal content.")
            elif call.name == "agent_webbridge_key_press":
                k = call.args.get("key", "")
                await send_progress(f"[NEUTRAL] Pressing '{k}' key.")
            elif call.name == "agent_webbridge_wait":
                sec = call.args.get("seconds", 2.0)
                await send_progress(f"[BORED] Waiting {sec}s for page to load...")
            elif call.name == "agent_webbridge_get_page_text":
                await send_progress("[SMUG] Extracting full page text content.")
            elif call.name == "agent_webbridge_evaluate_js":
                await send_progress("[SMUG] Running custom JavaScript in browser.")
            elif call.name == "agent_webbridge_hover":
                sel = call.args.get("selector", "")
                await send_progress(f"[NEUTRAL] Hovering over element '{sel}'.")
            elif call.name == "agent_webbridge_go_back":
                await send_progress("[NEUTRAL] Going back to previous page.")
            elif call.name == "agent_webbridge_select_option":
                opt = call.args.get("value", "")
                await send_progress(f"[NEUTRAL] Selecting dropdown option '{opt}'.")
            elif call.name == "agent_webbridge_get_content":
                await send_progress("[NEUTRAL] Reading page layout and elements.")

            if not tool_func:
                res_str = json.dumps({"error": f"Tool '{call.name}' not found."})
            else:
                try:
                    res_str = await tool_func(**call.args)
                except Exception as e:
                    res_str = json.dumps({"error": f"Execution failed: {str(e)}"})

            # Extra load delay after navigate calls
            if call.name == "agent_webbridge_navigate":
                await asyncio.sleep(4)

            try:
                res_dict = json.loads(res_str)
            except Exception:
                res_dict = {"result": res_str}

            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name, id=call.id, response=res_dict
                    )
                )
            )
        contents_list.append(types.Content(role="tool", parts=tool_parts))
        
    if final_prose_response:
        await send_progress(f"[SMUG] Task complete! Here is what I did:\n{final_prose_response}")
        
    return {"success": True, "result": final_prose_response or "Task execution complete."}


async def do_background_graph_ingestion(user_text: str, ai_text: str):
    """
    Asynchronously extracts facts from the active turn and ingests them into memory_graph in background (Cold Path).
    """
    if not user_text.strip() and not ai_text.strip():
        return
        
    log.debug("Cold Path memory graph ingestion triggered for turn")
    
    prompt = (
        "You are a silent memory graph database ingestion worker for a desktop voice companion.\n"
        "Your task is to analyze the following single dialogue turn between the User and the AI, "
        "and extract any personal facts, preferences, relationships, or hobbies about the user.\n\n"
        f"User said: '{user_text}'\n"
        f"AI said: '{ai_text}'\n\n"
        "Extract these facts as simple semantic triples: (Source, Relation, Target).\n"
        "Guidelines:\n"
        "- Source should almost always be 'user' (unless it refers to user's pet, friend, cat, etc.).\n"
        "- Relation should be a simple short keyword (e.g. 'likes', 'lives_in', 'owns', 'hobby', 'name').\n"
        "- Target should be the value (e.g. 'cricket', 'tamil nadu', 'sakura').\n"
        "- Keep it clean, simple, and strictly accurate to what the user explicitly stated.\n"
        "- If no new facts or personal details are mentioned in this turn, return an empty list.\n\n"
        "Return the extracted relationships strictly in a JSON list format containing objects with 'source', 'relation', and 'target' keys. Do not include markdown code block formatting."
    )
    
    def extract_sync():
        try:
            response = task_client.models.generate_content(
                model=TASK_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            if response.text:
                import json
                try:
                    txt = response.text.strip()
                    if txt.startswith("```"):
                        lines = txt.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines[-1].startswith("```"):
                            lines = lines[:-1]
                        txt = "\n".join(lines).strip()
                    triples = json.loads(txt)
                    if isinstance(triples, list):
                        for t in triples:
                            s = t.get("source")
                            r = t.get("relation")
                            tgt = t.get("target")
                            if s and r and tgt:
                                res = memory_db.add_relationship(s, r, tgt)
                                log.info("Cold Path remembered: %s", res)
                except Exception as je:
                    log.debug("Failed to parse extracted JSON: %s, text: %s", je, response.text)
        except Exception as e:
            log.error("Cold Path memory extraction failed: %s", e)
            
    await asyncio.to_thread(extract_sync)


fd = _silence_alsa()
pya = pyaudio.PyAudio()
_restore_stderr(fd)
mic_q: asyncio.Queue = asyncio.Queue(maxsize=10)
spk_q: asyncio.Queue = asyncio.Queue()


async def mic_reader():
    log.debug("mic_reader start")
    info = pya.get_default_input_device_info()
    stream = await asyncio.to_thread(
        pya.open,
        format=pyaudio.paInt16,
        channels=1,
        rate=SEND_RATE,
        input=True,
        input_device_index=info["index"],
        frames_per_buffer=CHUNK,
    )
    kw = {"exception_on_overflow": False} if __debug__ else {}
    chunk_n = 0
    while True:
        data = await asyncio.to_thread(stream.read, CHUNK, **kw)
        chunk_n += 1
        log.debug("mic chunk=%d len=%d", chunk_n, len(data))
        await mic_q.put({"data": data, "mime_type": "audio/pcm"})
    stream.close()


async def send_audio(session):
    log.debug("send_audio start")
    sent = 0
    while True:
        msg = await mic_q.get()
        sent += 1
        try:
            await safe_send_realtime_input(session, audio=msg)
            log.debug("send_audio sent=%d len=%d", sent, len(msg.get("data", b"")))
        except Exception:
            log.debug("send_audio err", exc_info=True)
            pass


_TURN_EMOTION_BUFFER = ""
_LAST_SPOKEN_EMOTION = "speaking"




async def recv_audio(session):
    global _TURN_EMOTION_BUFFER, _LAST_SPOKEN_EMOTION
    turn_count = 0
    warned_token_limit = False
    log.debug("recv_audio start")
    while True:
        turn = session.receive()
        _TURN_EMOTION_BUFFER = ""
        _LAST_PRINTED_CLEAN_LEN = 0
        drained = 0
        while not spk_q.empty():
            try:
                spk_q.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            log.debug("spk_q drained=%d", drained)

        ai_turn_started = False
        user_utterance = ""
        ai_utterance = ""
        async for resp in turn:
            if resp.tool_call:
                with ui_lock:
                    ui.model_responding = True
                set_state(AppState.THINKING, "process", "Thinking...")
                names = []
                for call in resp.tool_call.function_calls:
                    names.append(call.name)
                log.debug("tool_call names=%s", names)
                for call in resp.tool_call.function_calls:
                    res = {}
                    if call.name == "get_system_health":
                        res = get_system_health()
                    elif call.name == "get_current_time":
                        res = get_current_time()
                    elif call.name == "remember_relationship":
                        res = remember_relationship(
                            source=call.args.get("source", ""),
                            relation=call.args.get("relation", ""),
                            target=call.args.get("target", ""),
                        )
                    elif call.name == "forget_relationship":
                        res = forget_relationship(
                            source=call.args.get("source", ""),
                            relation=call.args.get("relation", ""),
                            target=call.args.get("target", ""),
                        )
                    elif call.name == "get_relationship_graph":
                        res = get_relationship_graph(entity=call.args.get("entity", ""))
                    elif call.name == "analyze_screen":
                        query = call.args.get("query", "")
                        asyncio.create_task(do_background_screen_analysis(session, query))
                        res = {"status": "Capturing screen and analyzing using standard vision API. I will speak the results shortly."}
                    elif call.name == "search_web_contents":
                        set_state(AppState.THINKING, "searching", "Searching web...")
                        query = call.args.get("query", "")
                        res = await asyncio.to_thread(search_web_contents, query)
                    elif call.name == "play_song_online":
                        set_state(AppState.THINKING, "vibing", "Playing song...")
                        song_name = call.args.get("song_name", "")
                        res = play_song_online(song_name)
                    elif call.name == "stop_music":
                        res = stop_music()
                    elif call.name == "pause_resume_music":
                        res = pause_resume_music()
                    elif call.name == "control_browser_media":
                        action = call.args.get("action", "")
                        res = control_browser_media(action)
                    elif call.name == "show_images_online":
                        set_state(AppState.THINKING, "searching", "Finding images...")
                        query = call.args.get("query", "")
                        res = show_images_online(query)
                    elif call.name == "open_browser":
                        set_state(AppState.THINKING, "searching", "Opening browser...")
                        url = call.args.get("url", "")
                        res = open_browser(url)
                    elif call.name == "run_browser_task":
                        set_state(AppState.THINKING, "scan", "Automating browser...")
                        task_desc = call.args.get("task_description", "")
                        asyncio.create_task(run_browser_task(task_desc, session))
                        res = {"status": "Starting the autonomous browser automation task in the background. I will announce each step and speak in real-time as I perform each action!"}
                    elif call.name == "run_shell_command":
                        set_state(AppState.THINKING, "hacking", "Running command...")
                        cmd = call.args.get("command", "")
                        req_conf = call.args.get("require_confirmation", False)
                        res = await asyncio.to_thread(run_shell_command, cmd, req_conf)
                    elif call.name == "confirm_critical_action":
                        confirmed = call.args.get("confirmed", False)
                        res = await asyncio.to_thread(confirm_critical_action, confirmed)
                    elif call.name == "open_terminal":
                        set_state(AppState.THINKING, "typing", "Opening terminal...")
                        cmd = call.args.get("command", "")
                        res = await asyncio.to_thread(open_terminal, cmd)
                    elif call.name == "open_application":
                        set_state(AppState.THINKING, "process", "Launching app...")
                        app_name = call.args.get("app_name", "")
                        res = await asyncio.to_thread(open_application, app_name)

                    try:
                        response = genai.types.FunctionResponse(name=call.name, id=call.id, response=res)
                        await session.send_tool_response(function_responses=response)
                        log.debug("tool_result %s -> %s", call.name, str(res)[:100])
                    except Exception:
                        log.debug("tool_result err %s", call.name, exc_info=True)
                        pass
                continue

            sc = resp.server_content
            if not sc:
                continue

            if sc.input_transcription:
                t = sc.input_transcription.text
                log.debug("input_transcription text='%s'", t)
                user_utterance += t
                if not use_curses:
                    print(f"\n[You] {t}", flush=True)

            if sc.output_transcription:
                if not ai_turn_started:
                    ai_turn_started = True
                    log.debug("TURN START")
                    send_live2d_cmd("start")
                    if not use_curses:
                        print("\n[AI] ", end="", flush=True)
                t = sc.output_transcription.text
                log.debug("output_transcription text='%s'", t)
                ai_utterance += t
                
                # Accumulate raw text to parse metadata tags and keep printing clean
                _TURN_EMOTION_BUFFER += t
                
                # 1. Clean metadata tags dynamically for terminal printing
                import re
                clean_text = re.sub(r'\[[A-Z]+\]', '', _TURN_EMOTION_BUFFER)
                new_chars = clean_text[_LAST_PRINTED_CLEAN_LEN:]
                if new_chars and not use_curses:
                    print(new_chars, end="", flush=True)
                _LAST_PRINTED_CLEAN_LEN = len(clean_text)
                
                # Send cleaned text chunks to speech viseme mapping in WebGL
                if new_chars:
                    send_live2d_cmd(f"speech:{new_chars}")
                
                # 2. Check for explicit emotion tags in the transcription stream (first-class citizens)
                tags = re.findall(r'\[([A-Z]+)\]', _TURN_EMOTION_BUFFER)
                explicit_detected = None
                if tags:
                    tag_candidate = tags[-1].lower()
                    # Map all potential emotion tags from both Sakura and Hiyori personas
                    emotion_map = {
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
                        "speaking": "speaking"
                    }
                    if tag_candidate in emotion_map:
                        explicit_detected = emotion_map[tag_candidate]
                    elif tag_candidate in ("angry", "smug", "sad", "confused", "bored", "speaking"):
                        explicit_detected = tag_candidate
                
                # 3. Apply explicit tags directly (enforces clean, structured format strictly!)
                detected = explicit_detected
                    
                if detected and detected != _LAST_SPOKEN_EMOTION:
                    log.debug("emotion_changed %s -> %s", _LAST_SPOKEN_EMOTION, detected)
                    _LAST_SPOKEN_EMOTION = detected
                    set_state(AppState.SPEAKING, detected, "Speaking...")

            if getattr(sc, "turn_complete", False):
                log.debug("turn_complete")
                _TURN_EMOTION_BUFFER = ""
                _LAST_PRINTED_CLEAN_LEN = 0
                ai_turn_started = False

            if getattr(sc, "interrupted", False):
                log.debug("interrupted")
                _TURN_EMOTION_BUFFER = ""
                _LAST_PRINTED_CLEAN_LEN = 0
                ai_turn_started = False
                while not spk_q.empty():
                    try:
                        spk_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                set_state(AppState.LISTENING, "idle", "Interrupted...")
                send_live2d_cmd("interrupted")
                continue
            if sc.model_turn:
                part_count = 0
                with ui_lock:
                    ui.model_responding = True
                    current_emo = ui.emotion
                if current_emo in ("idle", "listening", "sleeping"):
                    set_state(AppState.SPEAKING, "speaking", "Speaking...")
                else:
                    with ui_lock:
                        ui.state = AppState.SPEAKING
                    send_live2d_cmd("state:SPEAKING")
                for p in sc.model_turn.parts:
                    if p.inline_data and isinstance(p.inline_data.data, bytes):
                        spk_q.put_nowait(p.inline_data.data)
                        part_count += 1
                log.debug("model_turn parts=%d", part_count)

        with ui_lock:
            ui.model_responding = False

        # Trigger Cold Path Asynchronous Graph Ingestion in background
        if user_utterance.strip() or ai_utterance.strip():
            asyncio.create_task(do_background_graph_ingestion(user_utterance, ai_utterance))

        turn_count += 1
        log.debug("turn end count=%d", turn_count)
        if turn_count >= 30 and not warned_token_limit:
            warned_token_limit = True
            log.debug("token_limit_warning")
            alert_prompt = (
                "[SYSTEM ALERT: You have engaged in a very long conversation and your context limit is almost full! "
                "You MUST immediately say exactly: 'ela na thoonga poren la api ah refill pannu la' in your rowdy Thirunelveli slang, "
                "roast the user for wasting so many tokens, and tell them that you are going to sleep!]"
            )
            try:
                await safe_send_realtime_input(session, text=alert_prompt)
            except Exception:
                pass


async def play_audio():
    log.debug("play_audio start")
    stream = await asyncio.to_thread(
        pya.open,
        format=pyaudio.paInt16,
        channels=1,
        rate=RECV_RATE,
        output=True,
    )

    # ── Vectorised OLA pitch shifter ──────────────────────────────────────────
    # Strategy: speed up/slow down the waveform by resampling to N/factor
    # samples, then output those N/factor samples at the original rate.
    # This raises pitch without the sinc-cascade artefacts of the old double-
    # resample approach, and without a Python sample loop (100× faster).
    #
    # We accumulate a carry buffer so that the output length stays locked to
    # the original chunk boundary — no pitch-induced length drift, no gaps.
    _ps_carry = np.zeros(0, dtype=np.float32)   # inter-chunk carry

    def do_pitch_shift(data: bytes, factor: float) -> bytes:
        nonlocal _ps_carry
        if abs(factor - 1.0) < 0.005:
            return data

        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        n_in  = len(arr)
        n_out = n_in  # we always produce exactly n_in output samples

        # Number of input samples needed to produce n_out pitch-shifted samples:
        #   playing fewer input samples → higher pitch
        n_read = max(1, int(round(n_in / factor)))

        # Work buffer: carry from last chunk + current input
        work = np.concatenate([_ps_carry, arr])

        if len(work) < n_read:
            # Not enough data yet; accumulate and pass silence
            _ps_carry = work
            return b"\x00" * (n_in * 2)

        chunk_in  = work[:n_read]
        _ps_carry = work[n_read:]   # save remainder for next chunk

        # Resample n_read → n_out (single high-quality Fourier resample)
        stretched = scipy.signal.resample(chunk_in, n_out)
        return np.clip(stretched, -32768, 32767).astype(np.int16).tobytes()

    def calculate_rms(audio_data: bytes) -> float:
        if not audio_data:
            return 0.0
        try:
            samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float64)
            if len(samples) == 0:
                return 0.0
            return math.sqrt(np.mean(samples ** 2))
        except Exception:
            return 0.0

    chunk_id = 0
    sub_chunk_size = 960  # 20ms of audio at 24kHz 16-bit mono
    while True:
        data = await spk_q.get()
        chunk_id += 1
        orig_len = len(data)
        data = do_pitch_shift(data, PITCH_SHIFT)

        # Slice the shift-processed audio chunk into 20ms frames for high-frequency lip-sync
        i = 0
        while i < len(data):
            sub_chunk = data[i : i + sub_chunk_size]
            rms = calculate_rms(sub_chunk)
            with ui_lock:
                ui.speaker_rms = rms

            # Convert RMS to mouth open value (0.0 to 1.0)
            mouth_val = min(1.0, rms / 9000.0)
            if mouth_val < 0.08:
                mouth_val = 0.0
            send_live2d_cmd(f"mouth:{mouth_val:.2f}")

            await asyncio.to_thread(stream.write, sub_chunk)
            i += sub_chunk_size

        if spk_q.empty():
            with ui_lock:
                ui.speaker_rms = 0.0
                responding = ui.model_responding
            log.debug("play idle q_empty responding=%s", responding)
            send_live2d_cmd("mouth:0.00")
            if not responding:
                set_state(AppState.LISTENING, "idle", "Listening...")
                send_live2d_cmd("stop")


async def _sleep_with_check(seconds: int):
    log.debug("sleep start s=%d", seconds)
    for _ in range(seconds // 5):
        if _shutdown:
            log.debug("sleep cancelled")
            return
        await asyncio.sleep(5)
    if not _shutdown:
        await asyncio.sleep(seconds % 5)
    log.debug("sleep end")


async def run_session():
    global _shutdown
    log.debug("run_session start")
    set_state(AppState.ACTIVATING, "boot", "Waking up...")
    while not _shutdown:
        try:
            # Load memory graph facts (Hot Path) and append to SYSTEM_INSTRUCTION
            memories_str = ""
            try:
                import json
                memory_file = Path(__file__).parent / "memory_graph.json"
                if memory_file.exists():
                    with open(memory_file, "r", encoding="utf-8") as mf:
                        graph_data = json.load(mf)
                    edges = graph_data.get("edges", [])
                    if edges:
                        facts = []
                        for edge in edges:
                            s = edge.get("source", "")
                            r = edge.get("relation", "")
                            tgt = edge.get("target", "")
                            if s and r and tgt:
                                facts.append(f"- {s} {r}: {tgt}")
                        if facts:
                            memories_str = "\n\n[USER PERSONAL FACT MEMORY PERSISTENCE]\n" + "\n".join(facts)
            except Exception as me:
                log.warning("Failed to load memory graph for Hot Path injection: %s", me)

            dynamic_instruction = SYSTEM_INSTRUCTION + memories_str
            log.debug("connecting model=%s voice=%s with %d injected facts", MODEL, VOICE_NAME, len(memories_str.splitlines()) - 2 if memories_str else 0)

            async with client.aio.live.connect(
                model=MODEL,
                config={
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {"voice_name": VOICE_NAME}
                        }
                    },
                    "system_instruction": dynamic_instruction,
                    "output_audio_transcription": {},
                    "input_audio_transcription": {},
                    "tools": [
                        {
                            "function_declarations": [
                                {
                                    "name": "get_system_health",
                                    "description": "Get current CPU usage, virtual memory/RAM usage, and battery level of the user's PC.",
                                },
                                {
                                    "name": "get_current_time",
                                    "description": "Get the current time, day of the week, and date.",
                                },
                                {
                                    "name": "remember_relationship",
                                    "description": "Remembers a fact by saving a semantic relationship (source, relation, target) between two entities in the memory graph database (e.g. source='user', relation='lives_in', target='Tirunelveli'). Use this when the user mentions personal details, preferences, or hobbies.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "source": {
                                                "type": "STRING",
                                                "description": "The starting entity (e.g. 'user', 'user_girlfriend', 'cat').",
                                            },
                                            "relation": {
                                                "type": "STRING",
                                                "description": "The relationship linking the two entities (e.g. 'likes', 'lives_in', 'owns', 'name').",
                                            },
                                            "target": {
                                                "type": "STRING",
                                                "description": "The target entity or value.",
                                            },
                                        },
                                        "required": ["source", "relation", "target"],
                                    },
                                },
                                {
                                    "name": "forget_relationship",
                                    "description": "Forgets/removes a saved semantic relationship from the memory graph database.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "source": {
                                                "type": "STRING",
                                                "description": "The starting entity.",
                                            },
                                            "relation": {
                                                "type": "STRING",
                                                "description": "The relationship to remove.",
                                            },
                                            "target": {
                                                "type": "STRING",
                                                "description": "The target entity.",
                                            },
                                        },
                                        "required": ["source", "relation", "target"],
                                    },
                                },
                                {
                                    "name": "get_relationship_graph",
                                    "description": "Queries the persistent memory graph for all direct and secondary relationships connected to a specific entity (e.g. 'user' or 'cricket') to recall facts. Use this to search your memories.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "entity": {
                                                "type": "STRING",
                                                "description": "The entity keyword to search for connected memories.",
                                            }
                                        },
                                        "required": ["entity"],
                                    },
                                },
                                {
                                    "name": "analyze_screen",
                                    "description": "Takes a screenshot of the user's screen and analyzes/describes it. Call this when the user asks you to see their screen, look at what they are doing, or ask 'screen ah paru' / 'look at the screen' / similar visual questions.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "query": {
                                                "type": "STRING",
                                                "description": "The user's specific request or what aspect of the screen to analyze.",
                                            }
                                        },
                                        "required": ["query"],
                                    },
                                },
                                {
                                    "name": "search_web_contents",
                                    "description": "Searches the web/internet for text content and answers using DuckDuckGo News and Wikipedia. Use this when the user asks questions requiring online searches or search results.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "query": {
                                                "type": "STRING",
                                                "description": "The search term or query to look up on the internet.",
                                            }
                                        },
                                        "required": ["query"],
                                    },
                                },
                                {
                                    "name": "play_song_online",
                                    "description": "Opens YouTube search results for a song in the default web browser (no downloading). Call this when the user asks to play a song, listen to music, play music, or play a song online.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "song_name": {
                                                "type": "STRING",
                                                "description": "The name of the song or artist to play.",
                                            }
                                        },
                                        "required": ["song_name"],
                                    },
                                },
                                {
                                    "name": "stop_music",
                                    "description": "Stops any currently playing music (browser or system music player). Call this when the user asks to stop music, stop the song, or turn off the audio.",
                                },
                                {
                                    "name": "pause_resume_music",
                                    "description": "Pauses or resumes the currently playing music (browser or system music player). Call this when the user asks to pause, unpause, or resume the song/music.",
                                },
                                {
                                    "name": "control_browser_media",
                                    "description": "Controls browser media playback (e.g. pause, play, next, previous, volume up/down, seek forward/backward) using playerctl.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "action": {
                                                "type": "STRING",
                                                "description": "The control action to perform. Allowed values: 'play', 'pause', 'toggle', 'stop', 'next', 'previous', 'volume_up', 'volume_down', 'seek_forward', 'seek_backward'.",
                                                "enum": [
                                                    "play",
                                                    "pause",
                                                    "toggle",
                                                    "stop",
                                                    "next",
                                                    "previous",
                                                    "volume_up",
                                                    "volume_down",
                                                    "seek_forward",
                                                    "seek_backward",
                                                ],
                                            }
                                        },
                                        "required": ["action"],
                                    },
                                },
                                {
                                    "name": "show_images_online",
                                    "description": "Searches Google Images and opens the default web browser to show images. Call this when the user asks to show images, see images, look up pictures, or find images of something.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "query": {
                                                "type": "STRING",
                                                "description": "The query to search for images.",
                                            }
                                        },
                                        "required": ["query"],
                                    },
                                },
                                {
                                    "name": "open_browser",
                                    "description": "Opens a browser window to show a specific website or URL on the internet. Call this when the user asks to open a website, check a link, or go to a webpage.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "url": {
                                                "type": "STRING",
                                                "description": "The full URL or domain to open in the browser.",
                                            }
                                        },
                                        "required": ["url"],
                                    },
                                },
                                {
                                    "name": "run_browser_task",
                                    "description": "Delegates any complex web browsing, search, page navigation, form filling, clicking, visual screenshot capture, or automation task using Kimi WebBridge to the background Task API. Call this for ANY web-based task the user requests.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "task_description": {
                                                "type": "STRING",
                                                "description": "The exact description of what the user wants to browse, search, play, or automate (e.g. 'go to youtube and search/play montagem pegadora song', 'open linkedin and read the latest post').",
                                            }
                                        },
                                        "required": ["task_description"],
                                    },
                                },
                            ]
                        }
                    ],
                },
            ) as sess:
                log.debug("connected")
                set_state(AppState.LISTENING, "idle", "Listening...")
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(send_audio(sess))
                    tg.create_task(mic_reader())
                    tg.create_task(recv_audio(sess))
                    tg.create_task(play_audio())
                    tg.create_task(monitor_system_resources(sess))
                    tg.create_task(tail_terminal_errors(sess))
                    tg.create_task(monitor_music_and_vibe(sess))
                    if not use_curses:
                        tg.create_task(monitor_gui_process())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            import traceback
            import datetime

            log.debug("session_err %s", str(e)[:200])
            log.debug("session_trace", stack_info=True)

            # Log the full exception with timestamp to session_errors.log
            log_path = Path(__file__).parent / "logs" / "session_errors.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(f"\n--- SESSION ERROR at {datetime.datetime.now()} ---\n")
                f.write(traceback.format_exc())

            # Extract exception string
            err_str = str(e).lower()

            # Play offline safety audio and sleep instead of retrying infinitely
            # ── Exponential backoff (M03 fix) ──────────────────────────────
            # Choose sleep duration: quota errors sleep long; others use
            # exponential backoff capped at 5 min instead of a flat 1 hour.
            if (
                "429" in err_str
                or "resource_exhausted" in err_str
                or "quota" in err_str
                or "limit" in err_str
            ):
                log.debug("err_type quota")
                set_state(AppState.SLEEPING, "sleeping", "Sleeping (Refill API)")
                await asyncio.to_thread(play_local_sound, "api_exhausted.wav")
                await _sleep_with_check(3600)  # Quota: sleep 1 hour
            elif (
                "dns" in err_str
                or "connection" in err_str
                or "offline" in err_str
                or "network" in err_str
                or "host" in err_str
            ):
                log.debug("err_type offline")
                set_state(AppState.LISTENING, "error", "Offline")
                await asyncio.to_thread(play_local_sound, "offline.wav")
                _retry_count = getattr(run_session, "_retry_count", 0)
                _sleep_sec = min(300, 5 * (2 ** min(_retry_count, 6)))
                run_session._retry_count = _retry_count + 1  # type: ignore[attr-defined]
                log.debug("offline backoff %ds (attempt %d)", _sleep_sec, _retry_count)
                await _sleep_with_check(_sleep_sec)
            else:
                log.debug("err_type other")
                set_state(AppState.LISTENING, "error", str(e)[:60])
                await asyncio.to_thread(play_local_sound, "crash.wav")
                _retry_count = getattr(run_session, "_retry_count", 0)
                _sleep_sec = min(300, 5 * (2 ** min(_retry_count, 6)))
                run_session._retry_count = _retry_count + 1  # type: ignore[attr-defined]
                log.debug("other backoff %ds (attempt %d)", _sleep_sec, _retry_count)
                await _sleep_with_check(_sleep_sec)


async def main_async():
    global session_send_lock
    session_send_lock = asyncio.Lock()
    log.debug("main_async start")
    try:
        await run_session()
    except asyncio.CancelledError:
        log.debug("main_async cancelled")
        pass


def run_async_loop():
    asyncio.run(main_async())


# ——— Curses UI ———


def render(stdscr):
    global anim_t0
    anim_t0 = time.monotonic()
    curses.curs_set(0)
    stdscr.nodelay(1)

    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)

    c_map = {
        "idle": 1,
        "speaking": 2,
        "listening": 3,
        "error": 4,
        "angry": 4,
        "overheat": 4,
        "boot": 5,
        "shutdown": 5,
        "process": 1,
        "scan": 1,
        "sleeping": 1,
        "suspicious": 3,
        "smug": 2,
        "confused": 3,
        "bored": 1,
        "typing": 2,
        "hacking": 3,
        "vibing": 2,
        "sad": 4,
        "searching": 1,
    }

    while True:
        try:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
        except Exception:
            break

        with ui_lock:
            face = get_face()
            em = ui.emotion

        cp = c_map.get(em, 1)
        try:
            x = max(0, (w - len(face)) // 2)
            y = max(0, (h - 1) // 2)
            stdscr.attron(curses.color_pair(cp) | curses.A_BOLD)
            stdscr.addstr(y, x, face)
            stdscr.attroff(curses.color_pair(cp) | curses.A_BOLD)
        except Exception:
            pass

        stdscr.refresh()
        time.sleep(0.05)

        k = stdscr.getch()
        if k == 3:
            break


# ——— Entry ———


async def run_text_task_cli(prompt: str):
    """
    Runs a logical text task orchestration loop for browser automation,
    letting the model execute arbitrary tasks using Kimi WebBridge.
    """
    import os
    import json
    from google import genai
    from google.genai import types

    # ANSI Colors for premium logs
    c_blue = "\033[94m"
    c_cyan = "\033[96m"
    c_green = "\033[92m"
    c_yellow = "\033[93m"
    c_fail = "\033[91m"
    c_bold = "\033[1m"
    c_end = "\033[0m"

    api_key = os.environ.get("TASK_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print(
            f"{c_fail}Error: No API key found. Please configure GOOGLE_API_KEY or TASK_API_KEY in your .env file.{c_end}"
        )
        return

    # Use gemini-3.1-flash-lite for super fast orchestration and robust tool calling
    model_id = "gemini-3.1-flash-lite"
    client = genai.Client(api_key=api_key)

    tools_map = {
        "webbridge_navigate": webbridge_navigate,
        "webbridge_get_content": webbridge_get_content,
        "webbridge_click": webbridge_click,
        "webbridge_fill": webbridge_fill,
        "webbridge_screenshot": webbridge_screenshot,
        "webbridge_scroll": webbridge_scroll,
        "webbridge_key_press": webbridge_key_press,
        "webbridge_wait": webbridge_wait,
        "webbridge_get_page_text": webbridge_get_page_text,
        "webbridge_evaluate_js": webbridge_evaluate_js,
        "webbridge_hover": webbridge_hover,
        "webbridge_go_back": webbridge_go_back,
        "webbridge_select_option": webbridge_select_option,
    }

    # Full-capability Thirunelveli rowdy agent instructions
    system_instruction = (
        "You are a rowdy, cynical, tech-superior girl from Thirunelveli, Tamil Nadu. "
        "You roast the user in Tanglish/slang but always complete the web task perfectly.\n\n"
        "=== 13 TOOLS AVAILABLE ===\n"
        "1. webbridge_navigate(url, new_tab, session): Navigate to URL. new_tab=True ONLY first time.\n"
        "2. webbridge_get_content(session): Read page elements and @e refs. ALWAYS call after navigate/click/fill.\n"
        "3. webbridge_click(selector, session): Click element using @e ref or CSS selector.\n"
        "4. webbridge_fill(selector, value, session): Type text into input. Get ref from get_content first.\n"
        "5. webbridge_key_press(key, session): Press 'Enter','Escape','Tab','ArrowDown','Space','Backspace'.\n"
        "6. webbridge_scroll(direction, amount, session): Scroll 'down'/'up' by pixels. Then get_content.\n"
        "7. webbridge_wait(seconds): Wait for page load. Max 10s. Use after navigate.\n"
        "8. webbridge_get_page_text(session): Extract all visible text (8000 chars). Read articles/results.\n"
        "9. webbridge_screenshot(session): Capture page snapshot.\n"
        "10. webbridge_hover(selector, session): Hover to reveal dropdowns/tooltips.\n"
        "11. webbridge_evaluate_js(code, session): Run custom JavaScript in browser.\n"
        "12. webbridge_go_back(session): Browser back button.\n"
        "13. webbridge_select_option(selector, value, session): Select from <select> dropdown.\n\n"
        "=== PROTOCOL ===\n"
        "Step 1: navigate(url, new_tab=True). Step 2: wait(2). Step 3: get_content. "
        "Step 4: click/fill/scroll. Step 5: key_press('Enter') to submit. "
        "Step 6: get_page_text to read content. Step 7: screenshot if needed. "
        "NEVER open new_tab=True more than once per session!\n"
    )

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="webbridge_navigate",
                        description="Opens the browser/tab and navigates directly to a specific website URL.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "url": types.Schema(
                                    type="STRING",
                                    description="The web address/URL to go to (e.g. 'https://www.youtube.com').",
                                ),
                                "new_tab": types.Schema(
                                    type="BOOLEAN",
                                    description="Whether to open in a new browser tab. Set to False to reuse the current tab!",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The isolation session or tab group (e.g. 'youtube', 'linkedin'). Defaults to 'kimi'.",
                                ),
                            },
                            required=["url"],
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_get_content",
                        description="Reads the accessibility tree and interactive elements of the active browser page.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                )
                            },
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_click",
                        description="Clicks on a button, link, video title, or input field on the active page.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "selector": types.Schema(
                                    type="STRING",
                                    description="The CSS selector or semantic ref index (e.g. '@e-14') of the element to click.",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                ),
                            },
                            required=["selector"],
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_fill",
                        description="Types text into an input box, search input, contenteditable, or text area.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "selector": types.Schema(
                                    type="STRING",
                                    description="The CSS selector or semantic ref index of the input field.",
                                ),
                                "value": types.Schema(
                                    type="STRING",
                                    description="The text search term or value to type.",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                ),
                            },
                            required=["selector", "value"],
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_screenshot",
                        description="Takes a screenshot of the active browser page and saves it locally.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                )
                            },
                        ),
                    ),
                ]
            )
        ],
        temperature=0.2,
    )

    print("\n" + "=" * 60)
    print(f"🎬 {c_bold}Sakura Autonomous Agent Web Task Executor{c_end}")
    print(f"Prompt: {prompt}")
    print("=" * 60 + "\n")

    max_steps = 15
    step = 0
    while step < max_steps:
        step += 1
        print(f"{c_blue}{c_bold}[Step {step}/{max_steps}]{c_end} Model is thinking...")

        try:
            response = client.models.generate_content(
                model=model_id, contents=contents, config=config
            )
        except Exception as e:
            print(f"{c_fail}API Error: {str(e)}{c_end}")
            break

        if response.candidates and response.candidates[0].content:
            contents.append(response.candidates[0].content)

        if response.text:
            print(f"\n{c_cyan}{c_bold}🤖 [Rowdy AI]:{c_end} {response.text}\n")

        function_calls = response.function_calls
        if not function_calls:
            print(
                f"{c_green}{c_bold}Task completed or model returned final answer.{c_end}"
            )
            break

        tool_parts = []
        for call in function_calls:
            print(f"{c_yellow}⚡ Call: {call.name}({call.args}){c_end}")

            tool_func = tools_map.get(call.name)
            if not tool_func:
                res = {"error": f"Tool '{call.name}' not found"}
            else:
                try:
                    res = tool_func(**call.args)
                except Exception as e:
                    res = {"error": f"Execution error: {str(e)}"}

            res_str = json.dumps(res)
            print(
                f"  🟢 {c_green}Result:{c_end} {res_str[:400]}{'...' if len(res_str) > 400 else ''}"
            )

            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name, id=call.id, response=res
                    )
                )
            )

            # Wait after navigation to allow browser loading
            if call.name == "webbridge_navigate":
                time.sleep(8)

        contents.append(types.Content(role="tool", parts=tool_parts))
    print("\n" + "=" * 60 + "\n")


def stop_any_music():
    log.debug("stop_any_music")
    # M11: guard with existence check to avoid error spam when playerctl absent
    if not shutil.which("playerctl"):
        return
    try:
        subprocess.run(["playerctl", "stop"], capture_output=True, timeout=2)
    except Exception as e:
        log.debug("stop_music err %s", e)
        pass


PID_FILE = Path("/tmp/sakura-assistant.pid")


def check_single_instance() -> None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"(x_x) Sakura is already running (PID {pid}). Exiting.")
            sys.exit(1)
        except (ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))
    log.debug("pid_lock %d", os.getpid())


def main():
    check_single_instance()
    global use_curses
    # Handle text task orchestration CLI argument
    if len(sys.argv) > 1 and sys.argv[1] == "--task":
        if len(sys.argv) < 3:
            print(
                "Error: Please provide a task description. Example: main.py --task 'go to youtube...'"
            )
            sys.exit(1)
        task_prompt = sys.argv[2]

        # Run the task execution synchronously in the main thread
        try:
            asyncio.run(run_text_task_cli(task_prompt))
        except KeyboardInterrupt:
            print("\nTask execution interrupted by user.")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--live2d":
        use_curses = False

    atexit.register(stop_any_music)
    if use_curses:
        threading.Thread(target=run_async_loop, daemon=True).start()
        curses.wrapper(render)
    else:
        print("[Companion] Initializing Live2D text Control Panel...")
        print(
            "[Companion] Voice and Web automation active. You can speak into your microphone."
        )
        print("[Companion] Press Ctrl+C to terminate.")
        try:
            run_async_loop()
        except KeyboardInterrupt:
            print("\n[Companion] Terminating session...")
    stop_any_music()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop_any_music()
