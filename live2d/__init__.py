"""Live2D avatar control and shared UI state."""

import socket
import threading
import time
from dataclasses import dataclass

from config import AppState, log, EMOTION_DATA, ANIM_SPEED

# Module-level persistent UDP socket for Live2D commands
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

_last_mouth_val = -1.0


def send_live2d_cmd(cmd: str):
    global _last_mouth_val
    log.debug("live2d %s", cmd)
    if cmd.startswith("mouth:"):
        try:
            val = float(cmd.split(":", 1)[1])
            # Rate limit mouth updates: skip if change is negligible (< 0.03) to reduce UDP noise
            if abs(val - _last_mouth_val) < 0.03:
                return
            _last_mouth_val = val
        except Exception:
            pass
    try:
        _udp_sock.sendto(cmd.encode("utf-8"), ("127.0.0.1", 10088))
    except Exception:
        pass


@dataclass
class UIState:
    state: AppState = AppState.SLEEPING
    emotion: str = "sleeping"
    emotion_text: str = "z_z"
    speaker_rms: float = 0.0
    mic_rms: float = 0.0
    model_responding: bool = False


ui = UIState()
ui_lock = threading.Lock()
anim_t0 = 0.0

_shutdown_event = threading.Event()

def is_shutdown() -> bool:
    return _shutdown_event.is_set()

def trigger_shutdown():
    _shutdown_event.set()

_RESOURCE_COOLDOWNS = {"cpu": 0.0, "ram": 0.0, "gpu": 0.0}


def get_face() -> str:
    with ui_lock:
        emotion = ui.emotion
        rms = ui.speaker_rms

    if emotion == "speaking":
        if rms < 300:
            return "(-_-)"
        elif rms < 1500:
            return "(-o-)"
        elif rms < 4000:
            return "(-0-)"
        else:
            return "(-O-)"

    speed = ANIM_SPEED.get(emotion, 0.15)
    frames = EMOTION_DATA.get(emotion, EMOTION_DATA.get("error", ["(x_x)"]))
    if not frames:
        return "^.^"
    idx = int((time.monotonic() - anim_t0) / speed) % len(frames)
    return frames[idx]


def set_state(s: AppState, emotion: str, text: str = ""):
    from config import use_curses
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
