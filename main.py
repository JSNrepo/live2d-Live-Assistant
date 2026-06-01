import asyncio
import curses
import datetime
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
import atexit
import shutil
import psutil
from pathlib import Path

from google import genai
from google.genai import types

from config import AppState, log, MODEL, TASK_MODEL, VOICE_NAME, SYSTEM_INSTRUCTION, use_curses
from live2d import ui, ui_lock, get_face, set_state, send_live2d_cmd
from tools.sounds import play_local_sound, stop_any_music
from tools.media import monitor_music_and_vibe

from audio import (
    mic_reader,
    send_audio,
    recv_audio,
    play_audio,
    session_sender,
    safe_send_realtime_input,
    safe_create_task,
    reset_audio_queues,
    text_input_sender,
)

from tools import (
    webbridge_navigate,
    webbridge_get_content,
    webbridge_click,
    webbridge_fill,
    webbridge_screenshot,
    webbridge_scroll,
    webbridge_key_press,
    webbridge_wait,
    webbridge_get_page_text,
    webbridge_evaluate_js,
    webbridge_hover,
    webbridge_go_back,
    webbridge_select_option,
)

# API client initialization
client = genai.Client()


def _handle_sigterm(signum, frame):
    log.debug("signal %d", signum)
    import live2d
    live2d.trigger_shutdown()


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGHUP, _handle_sigterm)


async def monitor_gui_process():
    import live2d
    log.debug("monitor_gui_process start")
    # Wait 8 seconds for startup initially
    await asyncio.sleep(8)

    cached_pid = None

    def _find_gui_proc():
        nonlocal cached_pid
        if cached_pid is not None:
            try:
                proc = psutil.Process(cached_pid)
                cmdline = proc.cmdline() or []
                if any('live2d_gui.py' in c for c in cmdline):
                    return cached_pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cached_pid = None

        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('live2d_gui.py' in c for c in cmdline):
                    cached_pid = proc.info['pid']
                    return cached_pid
            except Exception:
                pass
        return None

    def _restart_gui():
        """Restart the live2d_gui.py as a new background process."""
        nonlocal cached_pid
        try:
            python_exe = sys.executable
            gui_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live2d_gui.py')
            import subprocess as _sp
            # I03: Only force X11 compatibility variables if we are NOT on a Wayland session
            wayland = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            env = {**os.environ}
            if not wayland:
                env["GDK_BACKEND"] = "x11"
                env["QT_QPA_PLATFORM"] = "xcb"
            proc = _sp.Popen(
                [python_exe, gui_script],
                start_new_session=True,
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                env=env,
            )
            cached_pid = proc.pid
            log.warning("GUI restarted with PID %d", proc.pid)
            return proc
        except Exception as e:
            log.error("Failed to restart GUI: %s", e)
            return None

    consecutive_missing = 0
    restart_count = 0
    MAX_GUI_RESTARTS = 5
    while not live2d.is_shutdown():
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
                if restart_count >= MAX_GUI_RESTARTS:
                    log.error("GUI restart limit (%d) reached — giving up", MAX_GUI_RESTARTS)
                    return
                log.warning("GUI 'live2d_gui.py' confirmed gone — restarting it... (attempt %d/%d)", restart_count + 1, MAX_GUI_RESTARTS)
                _restart_gui()
                restart_count += 1
                consecutive_missing = 0
                # Wait longer after restart for it to come up
                await asyncio.sleep(10)
                continue
        else:
            consecutive_missing = 0

        await asyncio.sleep(2)


async def monitor_system_resources(session):
    import live2d
    log.debug("monitor_resources start")
    # Prime the cpu_percent delta counter (PERF-07: first call returns garbage)
    psutil.cpu_percent()
    COOLDOWN_PERIOD = 120.0  # 2 minutes
    ALERT_THRESHOLD = 90.0  # 90%

    async def get_gpu_usage() -> float:
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

    while True:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            gpu = await get_gpu_usage()

            now = time.monotonic()

            if cpu > ALERT_THRESHOLD:
                if now - live2d._RESOURCE_COOLDOWNS["cpu"] > COOLDOWN_PERIOD:
                    live2d._RESOURCE_COOLDOWNS["cpu"] = now
                    log.debug("alert cpu=%.1f", cpu)
                    alert_prompt = (
                        f"[SYSTEM ALERT: CPU usage is critically high at {cpu:.1f}%. "
                        f"Warn the user about this in your own voice and personality.]"
                    )
                    await safe_send_realtime_input(session, text=alert_prompt)

            if ram > ALERT_THRESHOLD:
                if now - live2d._RESOURCE_COOLDOWNS["ram"] > COOLDOWN_PERIOD:
                    live2d._RESOURCE_COOLDOWNS["ram"] = now
                    log.debug("alert ram=%.1f", ram)
                    alert_prompt = (
                        f"[SYSTEM ALERT: RAM/Memory usage is critically high at {ram:.1f}%. "
                        f"Warn the user about this in your own voice and personality.]"
                    )
                    await safe_send_realtime_input(session, text=alert_prompt)

            if gpu is not None and gpu > ALERT_THRESHOLD:
                if now - live2d._RESOURCE_COOLDOWNS["gpu"] > COOLDOWN_PERIOD:
                    live2d._RESOURCE_COOLDOWNS["gpu"] = now
                    log.debug("alert gpu=%.1f", gpu)
                    alert_prompt = (
                        f"[SYSTEM ALERT: GPU usage is critically high at {gpu:.1f}%. "
                        f"Warn the user about this in your own voice and personality.]"
                    )
                    await safe_send_realtime_input(session, text=alert_prompt)

        except Exception as e:
            log.debug("monitor_resources err %s", e)
            pass

        await asyncio.sleep(15)


async def _sleep_with_check(seconds: int):
    import live2d
    log.debug("sleep start s=%d", seconds)
    for _ in range(seconds // 5):
        if live2d.is_shutdown():
            log.debug("sleep cancelled")
            return
        await asyncio.sleep(5)
    if not live2d.is_shutdown():
        await asyncio.sleep(seconds % 5)
    log.debug("sleep end")



async def run_session():
    import live2d
    log.debug("run_session start")
    set_state(AppState.ACTIVATING, "boot", "Waking up...")
    while not live2d.is_shutdown():
        try:
            # Load memory graph facts (Hot Path) and append to SYSTEM_INSTRUCTION
            memories_str = ""
            try:
                memory_file = Path(__file__).resolve().parent / "memory_graph.json"
                if memory_file.exists():
                    with open(memory_file, "r", encoding="utf-8") as mf:
                        graph_data = json.load(mf)
                    edges = graph_data.get("edges", [])
                    # C3: Only inject the most recent 200 facts to cap system instruction size
                    edges = edges[-200:]
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
                                    "description": "Searches the web/internet for text content and answers in the background asynchronously. Call this for web queries to continue talking to Vinoth while the search happens behind the scenes.",
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
                                    "description": "Opens a browser window directly to show a specific website or URL on the internet (e.g. going directly to a link or opening a page). Call this for simple website or page openings instead of starting complex automation.",
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
                                    "description": "Executes complex, multi-step web interaction tasks that require actions on an already opened site (like clicking buttons, filling forms, reading page text, or scrolling) OR performing multi-step search and navigation on a newly opened site. DO NOT call this if the user only wants to open a simple URL or search (use open_browser or search_web_contents instead).",
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
                                {
                                    "name": "run_shell_command",
                                    "description": "Runs a shell command on the user's Linux system asynchronously in the background and returns the output shortly. Use this for running general terminal commands requested by the user, so you can continue talking while it runs behind the scenes.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "command": {
                                                "type": "STRING",
                                                "description": "The exact shell command to execute.",
                                            },
                                            "require_confirmation": {
                                                "type": "BOOLEAN",
                                                "description": "Set True to force confirmation from the user for this command.",
                                            },
                                        },
                                        "required": ["command"],
                                    },
                                },
                                {
                                    "name": "confirm_critical_action",
                                    "description": "Confirms or cancels a pending critical/destructive shell command or action based on user input (yes/no/confirm/cancel).",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "confirmed": {
                                                "type": "BOOLEAN",
                                                "description": "True if the user confirmed/approved the action, False if they cancelled/aborted.",
                                            }
                                        },
                                        "required": ["confirmed"],
                                    },
                                },
                                {
                                    "name": "open_terminal",
                                    "description": "Opens a new graphical terminal window. Can optionally run a command inside the terminal window and keep it open.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "command": {
                                                "type": "STRING",
                                                "description": "The command to run inside the newly opened terminal window.",
                                            }
                                        },
                                    },
                                },
                                {
                                    "name": "open_application",
                                    "description": "Launches a graphical application (e.g. Firefox, VS Code, VLC) in the background.",
                                    "parameters": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "app_name": {
                                                "type": "STRING",
                                                "description": "The name or command of the desktop application to launch.",
                                            }
                                        },
                                        "required": ["app_name"],
                                    },
                                },
                            ]
                        }
                    ],
                },
            ) as sess:
                log.debug("connected")
                set_state(AppState.LISTENING, "idle", "Listening...")
                reset_audio_queues()
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(session_sender(sess))
                    tg.create_task(send_audio(sess))
                    tg.create_task(mic_reader())
                    tg.create_task(recv_audio(sess))
                    tg.create_task(play_audio())
                    tg.create_task(text_input_sender(sess))
                    tg.create_task(monitor_system_resources(sess))
                    tg.create_task(monitor_music_and_vibe(sess))
                    if not use_curses:
                        tg.create_task(monitor_gui_process())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Unwrap ExceptionGroup if needed (Python 3.11+ standard)
            base_exceptions = list(e.exceptions) if hasattr(e, "exceptions") else [e]
            
            is_clean_close = False
            for be in base_exceptions:
                be_name = type(be).__name__
                be_str = str(be).lower()
                
                # Check for standard WebSocket connection closed signals
                if "ConnectionClosed" in be_name:
                    code = getattr(be, "code", None)
                    if code in (1000, 1001, 1006, 1008) or any(w in be_str for w in ("session duration", "goaway", "aborted", "closed", "abnormal closure")):
                        is_clean_close = True
                        break
                
                # Check for common socket/EOF/Connection reset/Timeout/abnormal closure signals
                if any(w in be_str for w in (
                    "session duration", "goaway", "aborted", "eof", 
                    "connection closed", "broken pipe", "connection reset", 
                    "keepalive", "handshake", "1006", "1008", "abnormal closure", 
                    "abnormal", "closure", "reset by peer", "timeout", "time out",
                    "connection aborted"
                )):
                    is_clean_close = True
                    break

            if is_clean_close:
                log.info("Gemini Live session closed or completed a standard cycle. Reconnecting seamlessly...")
                run_session._retry_count = 0  # type: ignore[attr-defined]
                await asyncio.sleep(0.5)
                continue

            log.debug("session_err %s", str(e)[:200])
            log.debug("session_trace", stack_info=True)

            log_path = Path(__file__).resolve().parent / "logs" / "session_errors.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # H3: Cap session error log at 1MB to prevent unbounded growth
            if log_path.exists() and log_path.stat().st_size > 1_000_000:
                try:
                    log_path.unlink()
                except Exception:
                    pass
            with open(log_path, "a") as f:
                f.write(f"\n--- SESSION ERROR at {datetime.datetime.now()} ---\n")
                f.write(traceback.format_exc())

            err_str = str(e).lower()

            if (
                "429" in err_str
                or "resource_exhausted" in err_str
                or "quota" in err_str
                or "limit" in err_str
            ):
                log.debug("err_type quota")
                set_state(AppState.SLEEPING, "sleeping", "Sleeping (Refill API)")
                await asyncio.to_thread(play_local_sound, "api_exhausted.wav")
                await _sleep_with_check(3600)
            elif (
                "dns" in err_str
                or "connection" in err_str
                or "offline" in err_str
                or "network" in err_str
                or "host" in err_str
                or "temporary failure" in err_str
                or "name resolution" in err_str
                or "gaierror" in err_str
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
    log.debug("main_async start")
    try:
        import audio.pipeline as ap
        ap._active_loop = asyncio.get_running_loop()
        # Launch inter-process texting UDP listener thread immediately on event loop startup
        if not ap._text_listener_started:
            import threading
            thread = threading.Thread(target=ap.text_input_listener, daemon=True)
            thread.start()
            ap._text_listener_started = True
            log.info("[Companion] Text input listener started on startup.")
    except Exception as e:
        log.error("Failed to start text input listener thread: %s", e)
    try:
        await run_session()
    except asyncio.CancelledError:
        log.debug("main_async cancelled")
        pass


def run_async_loop():
    asyncio.run(main_async())


# ——— Curses UI ———

def render(stdscr):
    import live2d
    live2d.anim_t0 = time.monotonic()
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
            mic_level = ui.mic_rms
            spk_level = ui.speaker_rms

        cp = c_map.get(em, 1)
        try:
            x = max(0, (w - len(face)) // 2)
            y = max(0, (h - 1) // 2)
            stdscr.attron(curses.color_pair(cp) | curses.A_BOLD)
            stdscr.addstr(y, x, face)
            stdscr.attroff(curses.color_pair(cp) | curses.A_BOLD)

            # Draw glowing visual microphone sound meter
            m_bars = min(15, int(mic_level / 1200.0))
            m_bar_str = "█" * m_bars + "░" * (15 - m_bars)
            stdscr.addstr(h - 2, 2, f"🎤 MIC LEVEL: {mic_level:5.0f} | {m_bar_str}", curses.color_pair(1))

            # Draw glowing speaker mouth output meter
            s_bars = min(15, int(spk_level / 1200.0))
            s_bar_str = "█" * s_bars + "░" * (15 - s_bars)
            stdscr.addstr(h - 1, 2, f"🗣️ SPK LEVEL: {spk_level:5.0f} | {s_bar_str}", curses.color_pair(2))
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

    model_id = TASK_MODEL
    cli_client = genai.Client(api_key=api_key)

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
                    types.FunctionDeclaration(
                        name="webbridge_scroll",
                        description="Scrolls the active browser page up or down to reveal more content.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "direction": types.Schema(
                                    type="STRING",
                                    description="Direction to scroll: 'down' or 'up'. Defaults to 'down'.",
                                ),
                                "amount": types.Schema(
                                    type="INTEGER",
                                    description="Pixel distance to scroll. Defaults to 400.",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                ),
                            },
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_key_press",
                        description="Sends a keyboard key press event to the active tab.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "key": types.Schema(
                                    type="STRING",
                                    description="Key to press (e.g., 'Enter', 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Space', 'Backspace').",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                ),
                            },
                            required=["key"],
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_wait",
                        description="Pauses execution for a specified duration in seconds.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "seconds": types.Schema(
                                    type="NUMBER",
                                    description="Duration in seconds. Max 10.0. Defaults to 2.0.",
                                )
                            },
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_get_page_text",
                        description="Extracts the raw visible text content of the active page (no HTML tags). Use this to read page text.",
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
                        name="webbridge_evaluate_js",
                        description="Evaluates custom JavaScript code inside the browser page and returns the result.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "code": types.Schema(
                                    type="STRING",
                                    description="The JavaScript code snippet to run.",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                ),
                            },
                            required=["code"],
                        ),
                    ),
                    types.FunctionDeclaration(
                        name="webbridge_hover",
                        description="Simulates hovering the mouse cursor over an element.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "selector": types.Schema(
                                    type="STRING",
                                    description="CSS selector or @e ref of the element to hover over.",
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
                        name="webbridge_go_back",
                        description="Navigates back to the previous page in history.",
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
                        name="webbridge_select_option",
                        description="Selects an option from a dropdown element by value or text.",
                        parameters=types.Schema(
                            type="OBJECT",
                            properties={
                                "selector": types.Schema(
                                    type="STRING",
                                    description="CSS selector or @e ref of the select element.",
                                ),
                                "value": types.Schema(
                                    type="STRING",
                                    description="The value or text label of the option to select.",
                                ),
                                "session": types.Schema(
                                    type="STRING",
                                    description="The session name of the active tab. Defaults to 'kimi'.",
                                ),
                            },
                            required=["selector", "value"],
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
            response = cli_client.models.generate_content(
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

        contents.append(types.Content(role="tool", parts=tool_parts))
    print("\n" + "=" * 60 + "\n")


def _get_pid_file_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return Path(runtime_dir) / "sakura-assistant.pid"
    cache_dir = Path.home() / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "sakura-assistant.pid"


PID_FILE = _get_pid_file_path()


def check_single_instance() -> None:
    for _ in range(2):  # Try twice to handle stale PID file cleanup atomically
        try:
            fd = os.open(PID_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                pid = int(PID_FILE.read_text().strip())
                # Check if process actually exists
                os.kill(pid, 0)
                # If no exception, process is running! Let's terminate it to restart.
                print(f"[Launcher] Sakura is already running (PID {pid}). Terminating existing process to restart...")
                import signal
                import time
                try:
                    os.kill(pid, signal.SIGTERM)
                    for _ in range(15):
                        time.sleep(0.1)
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            break
                    else:
                        os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                PID_FILE.unlink(missing_ok=True)
            except (ValueError, OSError):
                # PID file is stale or unreadable; delete it atomically and retry once
                PID_FILE.unlink(missing_ok=True)
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))
    log.debug("pid_lock %d", os.getpid())


def main():
    check_single_instance()
    global use_curses
    if len(sys.argv) > 1 and sys.argv[1] == "--task":
        if len(sys.argv) < 3:
            print(
                "Error: Please provide a task description. Example: main.py --task 'go to youtube...'"
            )
            sys.exit(1)
        task_prompt = sys.argv[2]

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
