import asyncio
import math
import os
import re
import time
import numpy as np
import scipy.signal
import pyaudio
import queue
import threading
from google.genai import types

from config import AppState, log, SEND_RATE, RECV_RATE, CHUNK, PITCH_SHIFT, VOICE_NAME, EMOTION_TAG_MAP, NOISE_GATE_ENABLED, NOISE_GATE_MIN_RMS, NOISE_GATE_HOLD_FRAMES, BARGE_IN_ENABLED, BARGE_IN_THRESHOLD, BARGE_IN_FEEDBACK_RATIO
from live2d import ui, ui_lock, set_state, send_live2d_cmd
from tools.sounds import play_local_sound

# Import all tools for dispatcher
from tools.system import get_system_health, get_current_time, confirm_critical_action, open_terminal, open_application
from tools.media import play_song_online, stop_music, pause_resume_music, control_browser_media, show_images_online, open_browser
from memory import remember_relationship, forget_relationship, get_relationship_graph

# Setup Alsa silencer
def _silence_alsa():
    old = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    os.close(null)
    return old


def _restore_stderr(fd):
    os.dup2(fd, 2)
    os.close(fd)


fd = _silence_alsa()
pya = pyaudio.PyAudio()
_restore_stderr(fd)

# Shared Queues
mic_q: asyncio.Queue = asyncio.Queue(maxsize=10)
spk_q: asyncio.Queue = asyncio.Queue()
spk_thread_q = queue.Queue()
session_send_q: asyncio.Queue = asyncio.Queue()
active_spk_stream = None
active_tool_task = None
text_input_q: asyncio.Queue = asyncio.Queue()

_mic_thread_started = False
_play_thread_started = False
_active_loop = None
_pending_texts = []


def flush_audio_stream():
    global active_spk_stream
    while not spk_thread_q.empty():
        try:
            spk_thread_q.get_nowait()
        except Exception:
            break
    if active_spk_stream:
        try:
            active_spk_stream.stop_stream()
            active_spk_stream.start_stream()
            log.debug("PyAudio speaker stream hardware buffer flushed successfully.")
        except Exception as e:
            log.error("Error flushing PyAudio speaker stream: %s", e)


def reset_audio_queues():
    log.debug("Resetting and clearing all audio and session queues.")
    # Clear asyncio mic_q
    while not mic_q.empty():
        try:
            mic_q.get_nowait()
        except Exception:
            break
    # Clear asyncio spk_q
    while not spk_q.empty():
        try:
            spk_q.get_nowait()
        except Exception:
            break
    # Clear thread-safe spk_thread_q
    while not spk_thread_q.empty():
        try:
            spk_thread_q.get_nowait()
        except Exception:
            break
    # Clear asyncio session_send_q
    while not session_send_q.empty():
        try:
            session_send_q.get_nowait()
        except Exception:
            break


async def safe_send_realtime_input(session, **kwargs):
    global session_send_q
    if session_send_q is None:
        log.error("session_send_q is not initialized!")
        return
    await session_send_q.put(kwargs)


async def session_sender(session):
    global session_send_q
    log.debug("session_sender start")
    while True:
        try:
            payload = await session_send_q.get()
            if "tool_response" in payload:
                await session.send_tool_response(function_responses=payload["tool_response"])
                log.debug("session_sender sent tool response successfully")
            else:
                await session.send_realtime_input(**payload)
            session_send_q.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Error in session_sender: %s", e)


def safe_create_task(coro):
    task = asyncio.create_task(coro)
    def _done_callback(t):
        try:
            if not t.cancelled() and t.exception():
                log.error("Background task failed: %s", t.exception(), exc_info=t.exception())
        except Exception:
            pass
    task.add_done_callback(_done_callback)
    return task


_text_listener_started = False

def text_input_listener():
    """
    Listens on UDP port 10089 for text_input messages from JSBridge (pywebview process)
    and pushes them into the audio pipeline's text_input_q.
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 10089))
    except Exception as e:
        log.error("[TextInput] Could not bind UDP socket on port 10089: %s", e)
        return

    log.info("[TextInput] Listening for text input on 127.0.0.1:10089...")
    while True:
        try:
            data, _ = sock.recvfrom(65536)
            msg = data.decode("utf-8").strip()
            if msg.startswith("text_input:"):
                text = msg[len("text_input:"):]
                global _active_loop
                if _active_loop:
                    _active_loop.call_soon_threadsafe(text_input_q.put_nowait, text)
                else:
                    log.info("[TextInput] Event loop not active yet, buffering: %s", text[:40])
                    _pending_texts.append(text)
        except Exception as e:
            log.error("[TextInput] Error receiving text: %s", e)


async def text_input_sender(session):
    """Watches text_input_q for typed messages from the GUI prompt box and sends them as user text turns."""
    log.debug("text_input_sender start")
    global _text_listener_started, _active_loop
    _active_loop = asyncio.get_running_loop()
    
    if not _text_listener_started:
        import threading
        thread = threading.Thread(target=text_input_listener, daemon=True)
        thread.start()
        _text_listener_started = True

    global _pending_texts
    # Flush any buffered text prompts received during startup / PAM auth
    while _pending_texts:
        try:
            text = _pending_texts.pop(0)
            log.info("Flushing buffered text prompt: %s", text[:80])
            text_input_q.put_nowait(text)
        except Exception as e:
            log.warning("Failed to flush buffered text: %s", e)
            
    while True:
        try:
            text = await text_input_q.get()
            if text and text.strip():
                log.info("Text prompt received from GUI: %s", text[:80])
                await safe_send_realtime_input(session, text=text)
                text_input_q.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Error in text_input_sender: %s", e)


def mic_thread_worker(loop):
    log.debug("mic_thread_worker background thread started")
    info = pya.get_default_input_device_info()
    
    def open_stream():
        fd = _silence_alsa()
        try:
            stream = pya.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SEND_RATE,
                input=True,
                input_device_index=info["index"],
                frames_per_buffer=CHUNK,
            )
            time.sleep(0.15)
            return stream
        finally:
            _restore_stderr(fd)

    try:
        stream = open_stream()
    except Exception as e:
        log.error("Failed to open microphone stream in background thread: %s", e)
        return

    kw = {"exception_on_overflow": False} if __debug__ else {}
    consecutive_zeros = 0
    
    while True:
        try:
            data = stream.read(CHUNK, **kw)
            
            # Check for digital silence (all zeros indicating ALSA capture block)
            samples = np.frombuffer(data, dtype=np.int16)
            if len(samples) > 0 and np.all(samples == 0):
                consecutive_zeros += 1
                if consecutive_zeros >= 80: # ~5 seconds of silence
                    log.warning("⚠️ [ALSA Recovery] Digital silence detected. Re-opening mic stream in background thread...")
                    try:
                        stream.close()
                    except Exception:
                        pass
                    time.sleep(0.5)
                    stream = open_stream()
                    consecutive_zeros = 0
            else:
                consecutive_zeros = 0
                
            global _active_loop
            target_loop = _active_loop if _active_loop is not None else loop
            target_loop.call_soon_threadsafe(mic_q.put_nowait, {"data": data, "mime_type": "audio/pcm"})
            
        except Exception as e:
            log.error("Error in mic background thread: %s. Re-opening in 1s...", e)
            try:
                stream.close()
            except Exception:
                pass
            time.sleep(1.0)
            try:
                stream = open_stream()
            except Exception:
                pass

async def mic_reader():
    global _mic_thread_started, _active_loop
    _active_loop = asyncio.get_running_loop()
    if not _mic_thread_started:
        thread = threading.Thread(target=mic_thread_worker, args=(_active_loop,), daemon=True)
        thread.start()
        _mic_thread_started = True
    
    while True:
        await asyncio.sleep(3600)


async def send_audio(session):
    log.debug("send_audio start")
    from config import use_curses
    sent = 0
    was_speaking = False
    
    # Noise gate state tracking (LOGIC-08)
    gate_open = False
    hold_counter = 0
    
    # Rolling noise floor tracking for adaptive thresholds
    rms_history = []
    
    while True:
        msg = await mic_q.get()
        sent += 1

        # Calculate mic RMS globally to drive the voice-reactive UI ring & logging
        data_bytes = msg.get("data", b"")
        try:
            samples = np.frombuffer(data_bytes, dtype=np.int16).astype(np.float64)
            mic_rms = math.sqrt(np.mean(samples ** 2)) if len(samples) > 0 else 0.0
        except Exception:
            mic_rms = 0.0

        # Update rolling minimum to track silent background noise floor (caps to ~2s window)
        rms_history.append(mic_rms)
        if len(rms_history) > 30:
            rms_history.pop(0)
        noise_floor = min(rms_history) if rms_history else 0.0

        # Update thread-safe shared UIState
        with ui_lock:
            ui.mic_rms = mic_rms
            is_speaking = (ui.state == AppState.SPEAKING and (ui.model_responding or not spk_q.empty() or ui.speaker_rms > 100.0))
            current_speaker_rms = ui.speaker_rms

        # Broadcast mic RMS to Live2D GUI socket
        send_live2d_cmd(f"mic_rms:{mic_rms:.2f}")

        # Noise Gate Implementation with Adaptive Noise Floor Scaling (LOGIC-08)
        if NOISE_GATE_ENABLED:
            current_gate_min = max(NOISE_GATE_MIN_RMS, noise_floor * 1.5)
            if mic_rms >= current_gate_min:
                gate_open = True
                hold_counter = NOISE_GATE_HOLD_FRAMES
            else:
                if hold_counter > 0:
                    hold_counter -= 1
                else:
                    gate_open = False

            if not gate_open:
                # Discard background hum/room noise frames and skip sending to session
                continue

        # Real-time console voice logging for non-curses users
        if not use_curses and mic_rms > 1200.0:
            try:
                bar_len = int(min(15, mic_rms / 1200.0))
                bar = "█" * bar_len + "░" * (15 - bar_len)
                print(f"\r[Mic Input Active] Level: {mic_rms:5.0f} | {bar}", end="", flush=True)
            except (OSError, ValueError):
                pass

        if is_speaking:
            if not BARGE_IN_ENABLED:
                was_speaking = True
                continue

            # Dynamic threshold modifier combining adaptive noise floor scaling and speaker feedback ratio.
            # Base threshold automatically scales with your microphone gain and room silent level.
            base_threshold = max(BARGE_IN_THRESHOLD, noise_floor * 2.5)
            barge_in_threshold = max(base_threshold, min(3500.0, current_speaker_rms * BARGE_IN_FEEDBACK_RATIO))

            if mic_rms > barge_in_threshold:
                log.info("Barge-in voice activity detected! mic_rms=%.1f (threshold=%.1f)", mic_rms, barge_in_threshold)
                
                # 1. Instantly stop Hiyori's speech playback on client side
                while not spk_q.empty():
                    try:
                        spk_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await asyncio.to_thread(flush_audio_stream)
                
                # 2. Instantly update UI state to LISTENING and notify Live2D WebGL
                set_state(AppState.LISTENING, "idle", "Interrupted...")
                send_live2d_cmd("interrupted")
                
                was_speaking = False
                # Do NOT continue/skip! Fall through to send this voice frame to the server immediately.
            else:
                was_speaking = True
                continue
        else:
            # Hiyori is NOT speaking
            if was_speaking:
                # She just transitioned from speaking to not speaking naturally
                was_speaking = False
                drained = 0
                # Atomic race-free mic queue draining (N02)
                try:
                    while True:
                        mic_q.get_nowait()
                        drained += 1
                except asyncio.QueueEmpty:
                    pass
                if drained:
                    log.debug("Drained %d stale mic frames after speaking ended naturally", drained)
                continue  # Discard the first frame post-speech to avoid tail echo

        try:
            await safe_send_realtime_input(session, audio=msg)
            log.debug("send_audio sent=%d len=%d", sent, len(msg.get("data", b"")))
        except Exception:
            log.debug("send_audio err", exc_info=True)
            pass


_TURN_EMOTION_BUFFER = ""
_LAST_SPOKEN_EMOTION = "speaking"


async def recv_audio(session):
    global _TURN_EMOTION_BUFFER, _LAST_SPOKEN_EMOTION, active_tool_task
    # Late import to prevent circular dependency
    from audio.tasks import (
        execute_screen_analysis,
        execute_web_search,
        execute_shell_command,
        do_background_graph_ingestion,
        run_browser_task,
    )

    async def execute_and_respond(call_obj, coro):
        try:
            res = await coro
            response = types.FunctionResponse(name=call_obj.name, id=call_obj.id, response=res)
            await session_send_q.put({"tool_response": response})
            log.debug("tool_result queued %s -> %s", call_obj.name, str(res)[:100])
        except asyncio.CancelledError:
            log.debug("Tool call %s cancelled silently (discarding response to respect session interruption).", call_obj.name)
            raise
        except Exception as e:
            log.error("Error in tool call %s: %s", call_obj.name, e)
            try:
                response = types.FunctionResponse(name=call_obj.name, id=call_obj.id, response={"error": str(e)})
                await session_send_q.put({"tool_response": response})
            except Exception:
                pass

    async def execute_and_respond_background(call_obj, coro):
        try:
            # 1. Immediately return a function response to the Gemini Live session so it unblocks!
            msg = f"Started the {call_obj.name} task in the background."
            response = types.FunctionResponse(name=call_obj.name, id=call_obj.id, response={"status": "started", "message": msg})
            await session_send_q.put({"tool_response": response})
            log.debug("Sent immediate background tool response for %s", call_obj.name)

            # 2. Add a tiny delay to allow Gemini to start its speaking turn first
            await asyncio.sleep(0.8)

            # 3. Run the actual heavy task coroutine in the background!
            res = await coro
            log.debug("Background task %s completed: %s", call_obj.name, str(res)[:100])
            
            # 3. Wait for Hiyori to finish speaking if she is currently talking, to ensure smooth flow
            from audio.tasks import wait_for_ai_speech_finish
            await wait_for_ai_speech_finish()

            # 4. Stream or inject the final report into the session as a text instruction so she speaks the summary next!
            import json
            report_msg = ""
            if isinstance(res, dict) and "error" in res:
                report_msg = f"[SYSTEM: The background task '{call_obj.name}' failed with error: {res['error']}. Please inform the user and explain what went wrong.]"
            else:
                if call_obj.name == "search_web_contents":
                    report_msg = f"[SYSTEM: Web search finished successfully for '{call_obj.args.get('query', '')}'. Results: {json.dumps(res)[:1000]}. Please speak a friendly, brief cyberpunk-style summary of these findings to the user next.]"
                elif call_obj.name == "analyze_screen":
                    report_msg = f"[SYSTEM: Screen analysis finished. Findings: {json.dumps(res)}. Please describe these screen analysis details clearly to the user using your voice and persona next.]"
                elif call_obj.name == "run_browser_task":
                    report_msg = f"[SYSTEM: Browser task '{call_obj.args.get('task_description', '')}' is fully complete. Final summary: {json.dumps(res)}. Speak this complete confirmation summary to the user next.]"
                elif call_obj.name == "run_shell_command":
                    report_msg = f"[SYSTEM: Shell command '{call_obj.args.get('command', '')}' executed successfully. Output: {json.dumps(res)}. Tell the user the command result briefly next.]"
                else:
                    report_msg = f"[SYSTEM: Background task '{call_obj.name}' has finished. Results: {json.dumps(res)}. Summarize this to the user next.]"

            await safe_send_realtime_input(session, text=report_msg)
        except asyncio.CancelledError:
            log.debug("Background task %s was cancelled.", call_obj.name)
            raise
        except Exception as e:
            log.error("Error running background task %s: %s", call_obj.name, e)

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
            await asyncio.to_thread(flush_audio_stream)

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
                        active_tool_task = safe_create_task(execute_and_respond_background(call, execute_screen_analysis(query)))
                        continue
                    elif call.name == "search_web_contents":
                        set_state(AppState.THINKING, "searching", "Searching web...")
                        query = call.args.get("query", "")
                        active_tool_task = safe_create_task(execute_and_respond_background(call, execute_web_search(query)))
                        continue
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
                        active_tool_task = safe_create_task(execute_and_respond_background(call, run_browser_task(task_desc, session)))
                        continue
                    elif call.name == "run_shell_command":
                        set_state(AppState.THINKING, "hacking", "Running command...")
                        cmd = call.args.get("command", "")
                        req_conf = call.args.get("require_confirmation", False)
                        active_tool_task = safe_create_task(execute_and_respond_background(call, execute_shell_command(cmd, req_conf)))
                        continue
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
                        response = types.FunctionResponse(name=call.name, id=call.id, response=res)
                        await session_send_q.put({"tool_response": response})
                        log.debug("tool_result queued %s -> %s", call.name, str(res)[:100])
                    except Exception as e:
                        log.error("Failed to queue tool response: %s", e)
                continue

            sc = resp.server_content
            if not sc:
                continue

            if sc.input_transcription:
                t = sc.input_transcription.text
                log.debug("input_transcription text='%s'", t)
                user_utterance += t
                from config import use_curses
                if not use_curses:
                    try:
                        print(f"\r\033[K[You] {t}", flush=True)
                    except (OSError, ValueError):
                        pass

            if sc.output_transcription:
                if not ai_turn_started:
                    ai_turn_started = True
                    log.debug("TURN START")
                    send_live2d_cmd("start")
                    from config import use_curses
                    if not use_curses:
                        try:
                            print("\r\033[K[AI] ", end="", flush=True)
                        except OSError:
                            pass
                t = sc.output_transcription.text
                log.debug("output_transcription text='%s'", t)
                ai_utterance += t

                # Accumulate raw text to parse metadata tags and keep printing clean
                _TURN_EMOTION_BUFFER += t

                # 1. Clean metadata tags dynamically for terminal printing
                clean_text = re.sub(r'(?i)\[[a-z]+\]', '', _TURN_EMOTION_BUFFER)
                new_chars = clean_text[_LAST_PRINTED_CLEAN_LEN:]
                from config import use_curses
                if new_chars and not use_curses:
                    try:
                        print(new_chars, end="", flush=True)
                    except OSError:
                        pass
                _LAST_PRINTED_CLEAN_LEN = len(clean_text)

                # Send cleaned text chunks to speech viseme mapping in WebGL
                if new_chars:
                    send_live2d_cmd(f"speech:{new_chars}")

                # 2. Check for explicit emotion tags in the transcription stream
                tags = re.findall(r'(?i)\[([a-z]+)\]', _TURN_EMOTION_BUFFER)
                explicit_detected = None
                if tags:
                    tag_candidate = tags[-1].lower()
                    emotion_map = EMOTION_TAG_MAP
                    if tag_candidate in emotion_map:
                        explicit_detected = emotion_map[tag_candidate]
                    elif tag_candidate in ("angry", "smug", "sad", "confused", "bored", "speaking"):
                        explicit_detected = tag_candidate

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
                with ui_lock:
                    ui.model_responding = False
                    q_empty = spk_q.empty()

                if q_empty:
                    set_state(AppState.LISTENING, "idle", "Listening...")
                    send_live2d_cmd("stop")

                # Trigger Cold Path Asynchronous Graph Ingestion in background
                should_ingest = False
                user_lower = user_utterance.lower()
                ai_lower = ai_utterance.lower()
                # Check for personal pronouns in user utterance
                for pronoun in [r"\bi\b", r"\bmy\b", r"\bmine\b", r"\bme\b", r"\bwe\b", r"\bour\b", r"\bus\b"]:
                    if re.search(pronoun, user_lower):
                        should_ingest = True
                        break
                # Check for memory-relevant keywords
                if not should_ingest:
                    keywords = ["name", "live", "work", "job", "hobby", "like", "love", "hate", "friend", "sister",
                                "brother", "father", "mother", "girlfriend", "boyfriend", "wife", "husband",
                                "son", "daughter", "pet", "cat", "dog", "born", "age", "birthday", "study", "college"]
                    for kw in keywords:
                        if kw in user_lower or kw in ai_lower:
                            should_ingest = True
                            break
                if should_ingest:
                    safe_create_task(do_background_graph_ingestion(user_utterance, ai_utterance))

                user_utterance = ""
                ai_utterance = ""

                turn_count += 1
                log.debug("turn end count=%d", turn_count)
                if turn_count >= 30 and not warned_token_limit:
                    warned_token_limit = True
                    log.debug("token_limit_warning")
                    alert_prompt = (
                        "[SYSTEM ALERT: The conversation context is almost full after many turns. "
                        "Let the user know naturally in your own voice that the session will need to restart soon.]"
                    )
                    try:
                        await safe_send_realtime_input(session, text=alert_prompt)
                    except Exception:
                        pass

            if getattr(sc, "interrupted", False):
                log.debug("interrupted")
                # Note: background tasks run continuously in parallel and are NOT cancelled on user conversational interruptions!
                
                _TURN_EMOTION_BUFFER = ""
                _LAST_PRINTED_CLEAN_LEN = 0
                ai_turn_started = False
                with ui_lock:
                    ui.model_responding = False
                user_utterance = ""
                ai_utterance = ""
                while not spk_q.empty():
                    try:
                        spk_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await asyncio.to_thread(flush_audio_stream)
                set_state(AppState.LISTENING, "idle", "Interrupted...")
                send_live2d_cmd("interrupted")
                continue

            if sc.model_turn:
                part_count = 0
                with ui_lock:
                    ui.model_responding = True
                    current_emo = ui.emotion
                if current_emo in ("idle", "listening", "sleeping", "searching", "process", "scan", "suspicious"):
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


def play_thread_worker(loop):
    global active_spk_stream
    log.debug("play_thread_worker background thread started")
    try:
        fd = _silence_alsa()
        try:
            stream = pya.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RECV_RATE,
                output=True,
            )
            time.sleep(0.15)
        finally:
            _restore_stderr(fd)
    except Exception as e:
        log.error("Failed to open speaker stream in background thread: %s", e)
        return
        
    active_spk_stream = stream

    # Fast numpy linear pitch shift resampler
    _ps_input_buffer = np.zeros(0, dtype=np.float32)

    def do_pitch_shift_chunk(chunk_arr: np.ndarray) -> np.ndarray:
        n_in = len(chunk_arr)
        if n_in == 0:
            return chunk_arr
        indices = np.arange(0, n_in, PITCH_SHIFT)
        indices = indices[indices < n_in]
        output = np.interp(indices, np.arange(n_in), chunk_arr)
        
        n_target = 960
        if len(output) < n_target:
            output = np.pad(output, (0, n_target - len(output)), mode='edge')
        elif len(output) > n_target:
            output = output[:n_target]
        return output

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
        n_in = 960
        n_read = max(1, int(round(n_in * PITCH_SHIFT))) if abs(PITCH_SHIFT - 1.0) >= 0.005 else n_in

        # Accumulate input until we have at least n_read samples
        while len(_ps_input_buffer) < n_read:
            # Check if there is no audio left in the thread queue
            if spk_thread_q.empty():
                with ui_lock:
                    responding = ui.model_responding
                if not responding and len(_ps_input_buffer) > 0:
                    # Flush: pad to n_read with zeros
                    pad_len = n_read - len(_ps_input_buffer)
                    _ps_input_buffer = np.concatenate([_ps_input_buffer, np.zeros(pad_len, dtype=np.float32)])
                    break
                elif not responding:
                    # Thread sleeps when idle to avoid burning CPU
                    time.sleep(0.005)
                    continue

            try:
                # Synchronous blocking wait for next audio chunk
                data_bytes = spk_thread_q.get(timeout=0.005)
            except queue.Empty:
                continue

            arr = np.frombuffer(data_bytes, dtype=np.int16).astype(np.float32)
            _ps_input_buffer = np.concatenate([_ps_input_buffer, arr])
            # Pitch shift memory safety (N03): Cap buffer size to 24000 samples (1s of audio)
            if len(_ps_input_buffer) > 24000:
                _ps_input_buffer = _ps_input_buffer[-24000:]

        if len(_ps_input_buffer) < n_read:
            continue

        # Extract n_read samples from accumulated buffer
        chunk_arr = _ps_input_buffer[:n_read]
        _ps_input_buffer = _ps_input_buffer[n_read:]

        if abs(PITCH_SHIFT - 1.0) >= 0.005:
            output_arr = do_pitch_shift_chunk(chunk_arr)
        else:
            output_arr = chunk_arr

        data = np.clip(output_arr, -32768, 32767).astype(np.int16).tobytes()
        chunk_id += 1

        # Slice the shift-processed audio chunk into 20ms frames for high-frequency lip-sync
        i = 0
        while i < len(data):
            sub_chunk = data[i : i + sub_chunk_size]
            rms = calculate_rms(sub_chunk)
            with ui_lock:
                ui.speaker_rms = rms

            # Convert RMS to mouth open value (0.0 to 1.0)
            # D03: Less aggressive scaling divisor and gate for high-fidelity lip sync during soft speech
            mouth_val = min(1.0, rms / 6000.0)
            if mouth_val < 0.02:
                mouth_val = 0.0
            send_live2d_cmd(f"mouth:{mouth_val:.2f}")

            # Direct synchronous hardware write - incredibly smooth!
            try:
                stream.write(sub_chunk)
            except Exception as se:
                log.error("Speaker stream write error: %s. Attempting to recover...", se)
                try:
                    stream.close()
                except Exception:
                    pass
                time.sleep(0.5)
                try:
                    fd = _silence_alsa()
                    try:
                        stream = pya.open(
                            format=pyaudio.paInt16,
                            channels=1,
                            rate=RECV_RATE,
                            output=True,
                        )
                        time.sleep(0.15)
                    finally:
                        _restore_stderr(fd)
                    active_spk_stream = stream
                    log.info("Speaker stream successfully recovered!")
                except Exception as ree:
                    log.error("Failed to recover speaker stream: %s", ree)
            i += sub_chunk_size

        if spk_thread_q.empty() and len(_ps_input_buffer) == 0:
            with ui_lock:
                ui.speaker_rms = 0.0
                responding = ui.model_responding
            send_live2d_cmd("mouth:0.00")
            if not responding:
                set_state(AppState.LISTENING, "idle", "Listening...")
                send_live2d_cmd("stop")

async def play_audio():
    global _play_thread_started
    if not _play_thread_started:
        loop = asyncio.get_running_loop()
        thread = threading.Thread(target=play_thread_worker, args=(loop,), daemon=True)
        thread.start()
        _play_thread_started = True
    
    while True:
        data_bytes = await spk_q.get()
        spk_thread_q.put(data_bytes)
        spk_q.task_done()
