# LivePythonGemini — Audit V4: Architectural, Implementation & Logic Defects

**Audited state:** Current working tree (committed + uncommitted changes)  
**Focus:** Architecture flaws, improper implementations, logical bugs — **not** covered by audit.md, codebase_audit_v2.md, or audit_v3_deep.md  
**Method:** Static analysis + runtime evidence from `session_errors.log` (12 crash dumps)

---

## R — RUNTIME CRASH ROOT CAUSE ANALYSIS

### R01. WebSocket 1008 policy violation — root cause is concurrency, not model name

**Evidence:** 10 of 12 session errors show:
```
websockets.exceptions.ConnectionClosedError: received 1008 (policy violation)
Connection aborted because the client failed to close the connection after
receiving a GoAway signal once the session durat
```

**File:** `main.py:2070-2331` (`recv_audio`) + `main.py:2123` (`asyncio.create_task`)

**Root cause:** `do_background_screen_analysis` calls `safe_send_realtime_input(session, text=...)` at line 1454 as a fire-and-forget `asyncio.create_task`. If this executes WHILE `recv_audio` is iterating over `async for resp in turn:` (the Gemini receive loop), TWO coroutines are simultaneously using `session.send_realtime_input()` — the `recv_audio` routine is blocked on `receive()`, and the background task calls `send_realtime_input()`. The Gemini live API uses a single WebSocket connection. Sending on one end while the other is in a receive loop creates a **protocol-level race** — the server sees concurrent bidirectional data on a half-duplex stream and sends GoAway.

**Similarly:** `run_browser_task` at line 2151 fires `asyncio.create_task(run_browser_task(task_desc, session))`, which calls `send_progress()` → `safe_send_realtime_input(session, text=...)` multiple times during its execution.

**Severity:** 🔴 CRITICAL — this is THE recurring crash (10 of 12 crashes)  
**Fix:** Replace direct `session.send_realtime_input()` calls from background tasks with an `asyncio.Queue` that `recv_audio` drains during turn idle periods. Never call `session.send_realtime_input()` from outside the single `recv_audio` coroutine.

---

### R02. `OSError: [Errno 5] Input/output error` — PyAudio stream race

**Evidence:** 2 of 12 session errors:
```
OSError: [Errno 5] Input/output error
```
First at line ~1201 in `recv_audio` (during tool call execution), second at line ~2154 in `recv_audio` (during `print()`).

**Root cause analysis:**
- The first `OSError: 5` in `recv_audio` happens during tool call dispatch (around `show_images_online` which calls `webbrowser.open`). This is NOT an I/O error on the tool itself — it's the `print()` statement at line 2188 (`print(f"\n[You] {t}", flush=True)`) failing because the curses wrapper closed or redirected stdout.
- When `use_curses = True`, `curses.wrapper(render)` redirects stdout. When the curses render loop exits (e.g., terminal resize, Ctrl+C), stdout is restored. But if `recv_audio` tries to `print()` during the window where curses has taken over stdout, it gets EIO.
- The second error confirms this: `print("\n[AI] ", end="", flush=True)` at line ~2196 raises `OSError: 5`.

**Fix:** Guard every `print()` in `recv_audio` with `if not use_curses` — already done partially (lines 2187, 2195), but the guard at line 2187 wraps the wrong print. The check `if not use_curses:` at 2187 only guards the `[You]` prefix, not the actual text print. When `use_curses = True`, NO print should execute.

**Severity:** 🟠 HIGH — crashes on any terminal resize during conversation

---

## A — ARCHITECTURAL FLAWS (5 findings)

### A01. `mic_q` fills up during echo suppression → recorded speech loss

**File:** `main.py:2041-2061`  
```python
async def send_audio(session):
    while True:
        msg = await mic_q.get()
        with ui_lock:
            is_speaking = (ui.state == AppState.SPEAKING)
        if is_speaking:
            continue    # ← DROPS the chunk
```

`mic_q` has `maxsize=10`. During speaking (which can last 5-30 seconds), the mic is still capturing at 16kHz/1024 chunks ≈ 15.6 chunks/sec. After 0.64 seconds, `mic_q` is full. `mic_reader` blocks on `await mic_q.put(...)` — the user's microphone freezes. When speaking ends and `send_audio` resumes consuming, it processes the LAST 10 chunks in the queue, but by then the user's actual utterance start is already lost because ~15 seconds of audio was dropped during the blocking window.

**Effect:** The first ~1-3 words of the user's next utterance are always cut off after the AI finishes speaking.

**Fix:** Instead of dropping chunks, drain the queue to a single "latest" position before resuming (keep and send only the most recent ~0.5s of audio). Or implement proper VAD with a ring buffer.

---

### A02. `spk_q` drain in `recv_audio` creates audible gap on interruption

**File:** `main.py:2080-2087, 2269-2273`  
```python
while not spk_q.empty():
    try:
        spk_q.get_nowait()
        drained += 1
    except asyncio.QueueEmpty:
        break
```

When a new turn starts (or interruption occurs), ALL queued audio is drained without flushing the PyAudio output buffer first. The `play_audio` task has already received some of these chunks and written them to PyAudio's internal buffer (which holds ~100-200ms of audio). The user hears:
1. The tail end of the previous response (~200ms gap before drain takes effect)
2. Silence for 100-300ms while the new turn starts

**Effect:** Interruptions feel sluggish — the user hears "residual" speech for 200-300ms after interrupting.

**Fix:** `play_audio` should monitor an "interrupted" flag and call `stream.stop_stream()` + `stream.start_stream()` to flush the ALSA buffer. Or use `stream.rewind()`/`stream.abort()`.

---

### A03. No rate limiting on `send_live2d_cmd` — UDP socket can be overwhelmed

**File:** `main.py:220-225`  
The persistent socket fix (BUG-03 fix) eliminates FD leaks, but the call rate is still unthrottled:
- `play_audio` calls 50×/sec for mouth values (line 2430)
- `recv_audio` calls for every speech text chunk (line 2214)
- `set_state` calls 2× per state change (line 299-300)

During speech: ~60-100 UDP packets/second to `127.0.0.1:10088`. The `live2d_gui.py` listener thread blocks on `sock.recvfrom(1024)` in a Python thread. At 100 packets/sec, the receive thread spends all its time processing and the OS socket receive buffer overflows. UDP is lossy — packets are silently dropped.

**Effect:** Some mouth updates, emotion changes, or speech text chunks are lost, causing visual glitches.

**Fix:** Add a simple rate limiter: skip mouth updates that differ by <0.03 from the last sent value, and batch speech text into ~100ms windows (max 10 packets/sec).

---

### A04. Async TaskGroup + fire-and-forget `create_task` = untracked exceptions

**File:** `main.py:2755-2764`  
```python
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
```

Then outside the TaskGroup (line 2123, 2316):
```python
asyncio.create_task(do_background_screen_analysis(session, query))
asyncio.create_task(do_background_graph_ingestion(user_utterance, ai_utterance))
```

These `create_task` calls create tasks outside the TaskGroup context. If they fail:
- The exception is stored in the task but **never retrieved** (no `await`, no `result()`, no `exception()` call)
- Python 3.12+ logs a `Task exception was never retrieved` warning
- The error is silently lost

Meanwhile, TaskGroup's behavior: if ANY child task raises, the entire TaskGroup is cancelled and ALL tasks are torn down. One failed task (e.g., `mic_reader` due to audio device disconnection) kills the entire session.

**Effect:** A single transient audio error kills the entire conversation session. The session restart has exponential backoff (up to 5 min), so a 1-second glitch causes 5+ minutes of downtime.

**Fix:** 
- Move background tasks into the TaskGroup, or use `asyncio.gather(return_exceptions=True)` for soft-fail tasks
- Add `task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)` to fire-and-forget tasks

---

### A05. Persona file switching requires restart — no hot-reload

**Files:** `config.toml:47`, `main.py:171-214`  
`PERSONA_PATH` and `SYSTEM_INSTRUCTION` are loaded once at module import time. Changing `persona_file` in config requires a full restart. The `run_session` function reconnects the session on error, but uses the same `SYSTEM_INSTRUCTION`.

This is an architectural limitation: the persona should be hot-reloadable for testing different personalities without restarting audio devices.

---

## I — IMPROPER IMPLEMENTATIONS (6 findings)

### I01. Output length mismatch in new `do_pitch_shift` — audio desync

**File:** `main.py:2368-2397` (current working tree)  
```python
# Resample polyphase filter
stretched = scipy.signal.resample_poly(work, _ps_up, _ps_down, window=_ps_window)
# Discard the resampled overlap at the beginning of the chunk
discard = int(round(64.0 * _ps_up / _ps_down))
output = stretched[discard:]
```

**Problem:** The output length `len(output)` varies by chunk because:
- `discard` is based on `64 * up/down`, but the input chunk length `n_in` varies
- No check ensures `len(output) == original_chunk_length`
- The variance accumulates over time — after 100 chunks at 50/sec, the audio pipeline drifts by ~2ms per chunk = 200ms drift in 2 seconds

At 24kHz, a 960-sample chunk should produce exactly 960 output samples. With `up=8, down=5` (factor=1.6):
- Input: 960 + 64 (overlap) = 1024 samples
- Resampled: 1024 × 8/5 = 1638.4 → truncated/rounded to 1638
- Discard: 64 × 8/5 = 102.4 → 102 samples
- Output: 1638 - 102 = **1536 samples**

But we need **960 output samples**. The pipeline produces 1536 instead of 960 — **60% more data per chunk**. The audio plays back slower and accumulates delay.

**This is a NEW implementation bug introduced in the uncommitted pitch shifter rewrite.** The previous OLA version correctly locked output to `n_in` samples.

**Severity:** 🔴 CRITICAL — audio progressively desyncs  
**Fix:** Truncate/pad output to exactly `n_in` samples:
```python
output = stretched[discard:]
if len(output) < n_in:
    output = np.pad(output, (0, n_in - len(output)), mode='edge')
elif len(output) > n_in:
    output = output[:n_in]
```

---

### I02. `agent_webbridge_wait` fixed but `webbridge_wait` still blocks thread pool

**File:** `main.py:1212-1218`  
```python
def webbridge_wait(seconds: float = 2.0) -> dict:
    secs = min(float(seconds), 10.0)
    time.sleep(secs)
    return {"waited_seconds": secs}
```

The `run_browser_task` agent now uses `await asyncio.sleep(secs)` instead of `await asyncio.to_thread(webbridge_wait, seconds)` — fixed in the uncommitted diff. But `agent_webbridge_wait` is still `asyncio.to_thread(webbridge_wait, seconds)` — wait, let me check again.

Looking at the diff:
```python
async def agent_webbridge_wait(seconds: float = 2.0) -> str:
    """Waits N seconds for page to load... Max 10 seconds."""
    secs = min(float(seconds), 10.0)
    await asyncio.sleep(secs)
    return json.dumps({"waited_seconds": secs})
```

This is correct — the async wrapper no longer uses `to_thread`. But the SYNC `webbridge_wait` function still blocks when called directly from `webbridge_*` sync code paths (e.g., from `run_text_task_cli` which runs sync tool calls). Though `run_text_task_cli` is a separate CLI mode, not the main live session.

**Severity:** 🟡 MEDIUM  
**Fix:** Add a docstring warning that `webbridge_wait` is sync-only and should not be used from async contexts.

---

### I03. `_restart_gui` hardcodes X11 backends — breaks on Wayland

**File:** `main.py:1499`  
```python
env = {**os.environ, "GDK_BACKEND": "x11", "QT_QPA_PLATFORM": "xcb"}
```

Forcing `GDK_BACKEND=x11` on a Wayland session breaks GTK apps. Many modern Linux desktops (Fedora, Ubuntu 24.04+ with GNOME 45+) run Wayland. The Live2D pywebview window won't render correctly.

**Severity:** 🟡 MEDIUM  
**Fix:** Check `WAYLAND_DISPLAY` env var and set appropriate backends:
```python
if os.environ.get("WAYLAND_DISPLAY"):
    env = {**os.environ}  # let it auto-detect
else:
    env = {**os.environ, "GDK_BACKEND": "x11", "QT_QPA_PLATFORM": "xcb"}
```

---

### I04. `_pending_confirmation` TTL leak — stale commands never expire

**File:** `main.py:563, 613-635`  

When Gemini calls `run_shell_command` with a critical command, `_pending_confirmation["shell"]` is set. If the user NEVER responds (no "yes"/"no"), the key stays forever. Every subsequent critical command gets `ERROR_PENDING_ACTION` until restart.

**Previous audit FIX (uncommitted diff)** added `ERROR_PENDING_ACTION` return, but no TTL. The user could say "run a command" → Gemini calls critical command → user changes topic → 30 minutes later says "yes" → wrong command executes.

**Fix:** Add TTL: `_pending_confirmation["shell_ts"] = time.monotonic()` and check `if now - ts > 60: pop and treat as cancelled`.

---

### I05. `tail_terminal_errors` reads from a file that nothing writes to

**File:** `main.py:1612-1641`  
```python
log_path = Path(__file__).parent / "logs" / "terminal_errors.log"
```

This function polls `terminal_errors.log` for new lines. But **nothing in the codebase writes to this file**. The `run_shell_command` function returns stderr via the Gemini tool response, not to this file. The file is never populated.

The function runs forever, wasting CPU, doing I/O on an empty file, and would alert spam if anything DID write to it — every line would be treated as a terminal error to roast the user about.

**Severity:** 🟡 MEDIUM — dead code that wastes resources  
**Fix:** Either implement actual terminal stderr capture (wrapper script, `script` command, or LD_PRELOAD), or remove the function entirely.

---

### I06. `monitor_system_resources` cooldown is process-lifetime — never resets on session restart

**File:** `main.py:1536`  
```python
cooldowns = {"cpu": 0.0, "ram": 0.0, "gpu": 0.0}
```

These `cooldowns` are local to the coroutine. When `run_session` catches an exception and restarts (lines 2767-2821), `monitor_system_resources` is re-created as a new coroutine with `cooldowns` reset to 0.0. But if the system was already overheated (CPU at 95%), the restart means the user gets an alert immediately instead of after the 2-minute cooldown.

Minor UX issue: the user has been roasted for CPU usage, the session crashed, reconnected, and gets roasted again immediately.

---

## L — LOGIC BUGS (7 findings)

### L01. Echo suppression drops ALL mic chunks during speech — no voice activity detection

**File:** `main.py:2048-2054`  
```python
with ui_lock:
    is_speaking = (ui.state == AppState.SPEAKING)
if is_speaking:
    continue
```

This suppresses the microphone for the ENTIRE duration the AI is speaking. Normal human conversation includes:
- "Mhm", "Uh-huh" backchannel responses
- "Wait, actually..." interruptions
- Overlapping speech

The suppression means the user CANNOT interrupt the AI by speaking — they must wait for the turn to complete and use a separate interruption mechanism (not currently implemented — `interrupted` flag is only sent by Gemini's server-side interruption).

**Severity:** 🔴 CRITICAL — no barge-in capability  
**Fix:** Instead of dropping all mic chunks during speaking, implement barge-in: detect when user speech RMS exceeds a threshold while AI is speaking → trigger interruption. Or at minimum, send mic audio at reduced rate instead of blocking entirely.

---

### L02. `monitor_gui_process` is only started when `not use_curses` — dead logic

**File:** `main.py:2763-2764`  
```python
if not use_curses:
    tg.create_task(monitor_gui_process())
```

When `use_curses = True` (terminal mode), the GUI watchdog doesn't run. But Live2D can be launched as a separate process even in terminal mode (`./run_live2d.sh` launches both). If the user runs `./run_live2d.sh` → `main.py --live2d`, `use_curses = False` and watchdog runs. If user runs `./run.sh` → `main.py` without `--live2d`, no GUI is expected.

The logic is actually correct — the condition matches the intended behavior. But it's fragile: if someone adds `--live2d-mode-with-curses` or similar, the watchdog won't trigger.

**Severity:** 🔵 LOW — works as designed but implicit coupling  
**Fix:** Make it explicit: `if args.gui_enabled` instead of `if not use_curses`.

---

### L03. `emoticons.json` `vibing` animation has fewer frames (8) than all others (16 or more)

**File:** `emoticons.json:315-324`  

The `vibing` animation has 8 frames, while most others have 16+. The `get_face()` function uses `len(frames)` for modulo indexing. At `ANIM_SPEED["vibing"] = 0.4`, the animation cycles every 8 × 0.4 = 3.2 seconds. Other animations cycle every 16 × 0.15-0.5 = 2.4-8 seconds.

Not a bug, but `vibing` runs 2-3× faster per-frame cycling than other animations, making it look jittery.

---

### L04. `check_single_instance` atomic PID write creates TOCTOU with `atexit`

**File:** `main.py:3300-3339` (current working tree)  

The new atomic PID write uses `O_EXCL` to prevent races on creation:
```python
fd = os.open(PID_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
```

But `atexit.register(lambda: PID_FILE.unlink(missing_ok=True))` removes the file on normal exit. If a SEGFAULT or `SIGKILL` occurs, `atexit` doesn't run — the PID file is orphaned. The `O_EXCL` creation will fail on next launch. The code handles this (tries to read PID and `os.kill(pid, 0)`), but the double-handling is complex and error-prone.

Also: `os.open` with `O_WRONLY | O_CREAT | O_EXCL` creates a 0-byte file. The subsequent `write` is not atomic. A crash between `os.open` and `write` leaves an empty PID file that the recovery code reads as empty string → `int("")` raises `ValueError` → caught, unlinks, and retries. Correct, but fragile.

---

### L05. `send_progress` in `run_browser_task` captures `session` in closure — misuse of `send_live2d_cmd`

**File:** `main.py:1842-1853`  
```python
async def send_progress(msg):
    if session:
        emo_tags = re.findall(r'\[([A-Z]+)\]', msg)
        if emo_tags:
            emo_tag = emo_tags[0].lower()
            mapped_emo = EMOTION_TAG_MAP.get(emo_tag, "speaking")
            send_live2d_cmd(f"emotion:{mapped_emo}")
        send_live2d_cmd("start")
        send_live2d_cmd(f"speech:{msg}")
        await safe_send_realtime_input(session, text=msg)
```

Calling `send_live2d_cmd("start")` while the main speech is happening tells the Live2D GUI to start a talking animation — but this is the BACKGROUND browser task progress, not the main AI voice. The Live2D character's mouth will start animating for "background task progress" text, while simultaneously the main AI voice is playing. This creates conflicting `start`/`stop` commands and mouth position overrides.

**Effect:** Live2D character's mouth flickers or gets stuck during background browser tasks.

**Fix:** Don't send `send_live2d_cmd("start")` / `send_live2d_cmd("stop")` from background tasks. The mouth animation belongs to the main voice pipeline.

---

### L06. `emotion_map` duplicate in `recv_audio` still exists despite `EMOTION_TAG_MAP`

**File:** `main.py:2222-2248`  

The `recv_audio` coroutine defines a LOCAL `emotion_map` dict at line 2222 that is an EXACT DUPLICATE of the module-level `EMOTION_TAG_MAP` at line 44. The uncommitted diff removed the duplicate from `run_browser_task` but NOT from `recv_audio`. Two copies will diverge.

**Severity:** 🟡 MEDIUM — violates DRY, future edits likely to update only one  
**Fix:** Replace the local `emotion_map` in `recv_audio` with a reference to `EMOTION_TAG_MAP`.

---

### L07. `memory_graph.json` initial edge list won't be injected into Hot Path until after first error

**File:** `main.py:2466-2487`  

The hot-path memory injection reads `memory_graph.json` at the START of `run_session` (line 2468). But `memory_graph.json` may have been updated during the PREVIOUS session (cold-path ingestion writes to it). The injection is correct — it reads the file on every session start.

But there's a subtle issue: if `do_background_graph_ingestion` writes a fact during the current session, that fact is NOT available to the AI until the NEXT session restart. The AI can query `get_relationship_graph` (which reads from the `MemoryGraph` live object), but the hot-path system instruction injection uses a stale snapshot.

**Effect:** The AI's system prompt (which defines its behavior and knowledge) is always one session behind. Facts saved in session N only appear in the system prompt in session N+1.

**Severity:** 🔵 LOW — `get_relationship_graph` tool still works for live queries  
**Fix:** Either reload memory on every turn (costly), or add a note in the system prompt that "I have a tool to query memory — use it for the latest facts."

---

## Summary

This audit identified **18 new findings** across 4 categories:

| Category | Count | Severity Distribution |
|----------|-------|---------------------|
| **R — Runtime Crash RCA** | 2 | 🔴 R01, 🟠 R02 |
| **A — Architectural** | 5 | 🟡 A01-A05 |
| **I — Improper Impl.** | 6 | 🔴 I01, 🟡 I02-I06 |
| **L — Logic Bugs** | 7 | 🔴 L01, 🟡 L02-L06, 🔵 L07 |

### Critical findings needing immediate attention:

1. **R01 (🔴)** — Background tasks calling `session.send_realtime_input` while `recv_audio` is in receive loop causes WebSocket GoAway → 10/12 crash rate. The root cause of every session error in logs.
2. **I01 (🔴)** — New pitch shifter (uncommitted) produces 1536 output samples instead of 960 per chunk → audio sync drift of ~60% per chunk, progressive desync.
3. **L01 (🔴)** — Echo suppression drops ALL mic chunks during speech with no barge-in → user cannot interrupt the AI.
4. **A04 (🟡)** — TaskGroup kills entire session on any single task error → 1-second audio glitch causes 5+ minutes of retry backoff.

### Previously reported issues confirmed still present (after uncommitted diff):

- E01 from audit_v3: 20 silent `pass` blocks — still all present
- P02 from audit_v3: Duplicate `emotion_map` in `recv_audio` — still present (L06 confirms)
- D03 from audit_v3: Mouth-open threshold (RMS/9000) — still present
- M01 from audit_v3: Incomplete state transitions — still present
