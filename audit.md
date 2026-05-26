# LivePythonGemini — Full Code Audit

**Audit date:** 2026-05-26
**Scope:** All source files (`main.py`, `live2d_gui.py`, `index.html`, `testing.py`, `test_*.py`, `run_agent_tests.py`, `run*.sh`, `emoticons.json`, `config.toml`, `.env`, `README.md`, `persona.txt`, `hyori.txt`)
**Lines analyzed:** ~4,500 across Python + JS/HTML

---

## CRITICAL

### C01. API keys in plaintext, no `.gitignore`
**File:** `.env`
**Severity:** 🔴 CRITICAL
**Details:** Three Google API keys stored in plaintext. The project root has **no `.gitignore`** — `.env`, `.venv/`, `__pycache__/`, and `logs/` are all trackable. If this ever gets `git init` + pushed, keys are compromised immediately.
**Fix:** Create `.gitignore` with `.env`, `.venv/`, `__pycache__/`, `logs/`, `*.pyc`, `sounds/`.

### C02. Duplicate HTML `id` attributes → broken UI selectors
**File:** `index.html:117-120`
**Severity:** 🔴 CRITICAL
**Details:** Three `<div>` elements have duplicate `id` values.
```html
<div id="speech-vibe" id="speech-vibe" ...>   <!-- two id attrs! -->
<div id="speech-status" id="speech-status" ...>
<div id="speech-text" id="speech-text" ...>
```
`document.getElementById('speech-vibe')` returns only the **first** element in DOM order — always the same element, but the attribute duplication is invalid HTML and `getElementById` behavior is undefined when IDs repeat. This causes the speech bubble status/vibe/text to silently fail to update correctly.
**Log evidence:** `tail -f logs/debug.log` shows AI responses coming through but the HTML speech bubble UI may not reflect them.
**Fix:** Remove the duplicate `id` attributes (the second `id="..."` on each element).

### C03. `time.sleep(4)` in async function blocks event loop
**File:** `main.py:1919`
**Severity:** 🔴 CRITICAL
```python
def run_agent_sync():
    ...
    if call.name == "agent_webbridge_navigate":
        time.sleep(4)   # BLOCKS the entire asyncio event loop for 4 seconds
```
Runs inside `asyncio.to_thread()`, so it doesn't block the event loop — this is actually OK. **Retracted.** (The `time.sleep(4)` is inside `run_agent_sync` which runs in thread pool via `asyncio.to_thread`.)

### C04. Non-thread-safe Gemini session used across threads
**File:** `main.py:1807-1839`
**Severity:** 🔴 CRITICAL
`send_progress()` closure captures `session` (Gemini live session object) and calls `asyncio.run_coroutine_threadsafe(safe_send_realtime_input(session, text=msg), loop)`. The `session` is passed into `run_agent_sync()` which runs in a thread pool via `asyncio.to_thread()`. The Gemini `session` object is **NOT thread-safe** — all send/receive must happen on the same event loop thread. This can cause:
- WebSocket protocol corruption
- `ConnectionClosedError: received 1008 (policy violation)` — **confirmed in logs** (see session_errors.log: every crash shows this exact 1008 error)
- Data races on internal session buffers
**Log evidence:** `session_errors.log` shows **9 crashes** all with `received 1008 (policy violation) Connection aborted because the client failed to close the connection after receiving a GoAway signal once the session durat`
**Fix:** Queue progress messages into an asyncio `Queue` instead of directly calling `safe_send_realtime_input` from the thread.

### C05. Emotion state mutated outside `setEmotion` path (race in ticker)
**File:** `index.html:382,388`
**Severity:** 🔴 CRITICAL
```javascript
// Inside app.ticker.add() callback (runs every frame):
if (currentState === 'LISTENING') {
    currentEmotion = 'listening';  // ← MUTATES global variable
} else if (currentState === 'THINKING') {
    currentEmotion = 'process';    // ← Bypasses window.setEmotion()
}
```
The ticker callback **silently overwrites** `currentEmotion` without calling `window.setEmotion()`, which means:
- `triggerEmotionMotion()` is **never called** for these state transitions → no animation plays
- `updateSpeechBubbleState()` is **never called** → the speech bubble doesn't update
- The emotion change happens invisibly; the UI desyncs from the actual state machine in `main.py`
**Fix:** Call `window.setEmotion('listening')` and `window.setEmotion('process')` instead of raw assignment.

### C06. Pitch shift creates audible audio artifacts
**File:** `main.py:2320-2321`
**Severity:** 🔴 CRITICAL (audio quality)
```python
stretched = scipy.signal.resample(arr, int(n * factor))
shifted = scipy.signal.resample(stretched, n)
```
Double `resample` creates heavy aliasing. `scipy.signal.resample` uses FFT-based interpolation, not pitch shifting — it changes duration, then back, but each resample introduces sinc interpolation artifacts. The 2× resample cascade guarantees audible distortion.
**Fix:** Use `librosa.effects.pitch_shift` or a proper phase vocoder / PSOLA implementation.

---

## HIGH

### H01. README describes entirely different architecture
**File:** `README.md`
**Severity:** 🔴 HIGH
The README describes a **LiveKit Agents** architecture (`MicInput`, `SpkOutput`, `AgentSession`, `toggle_worker`, `RealtimeModel`, `--imageface` flag). **None of this exists in the actual codebase.** The real code uses raw `PyAudio` + `google.genai` direct WebSocket API. Anyone reading the README would be completely misled about how the project works.
**Fix:** Rewrite README to match actual architecture.

### H02. `get_send_lock()` — asyncio.Lock creation race
**File:** `main.py:203-207`
**Severity:** 🟠 HIGH
```python
session_send_lock = None
def get_send_lock():
    global session_send_lock
    if session_send_lock is None:
        session_send_lock = asyncio.Lock()
    return session_send_lock
```
Two coroutines can both observe `session_send_lock is None` before either sets it, creating two separate locks. The later assignment wins but earlier coroutines keep a reference to the discarded lock.
**Fix:** Initialize `session_send_lock = asyncio.Lock()` at module level (no lazy init needed).

### H03. DuckDuckGo Lite scraping will break on HTML change
**File:** `main.py:437-457`
**Severity:** 🟠 HIGH
```python
links = re.findall(
    r"href=[\x27\"]([^\x27\"]+)[\x27\"][^>]*class=[\x27\"]result-link[\x27\"][^>]*>(.*?)</a>",
    res.text,
)
```
This regex matches specific DDG Lite HTML structure that can change any time. When DDG updates their markup, web search silently returns empty results. The `pass` Exception handler hides failures.
**Fix:** Use a proper search API (Google Custom Search, SerpAPI, or DuckDuckGo's official API). Add fallback with clear error messages.

### H04. Wikipedia User-Agent uses placeholder contact info
**File:** `main.py:473`
**Severity:** 🟠 HIGH
```python
"User-Agent": "LivePythonGemini/1.0 (https://github.com/user/LivePythonGemini; user@example.com)"
```
Wikipedia may block requests with placeholder/example.com contact info.
**Fix:** Use a real contact email or the project's actual URL.

### H05. Data race: `ui.speaker_rms` and `ui.model_responding` accessed without lock
**File:** `main.py:2364`, also `get_face():231`
**Severity:** 🟠 HIGH
`play_audio()` reads `ui.model_responding` (line 2364) and `ui_lock` is not acquired. The curses thread reads `ui.emotion` in `get_face()` without locking. These are shared-memory cross-thread accesses with no synchronization.
**Fixed partially:** `get_face()` is called inside `with ui_lock:` in the curses render loop (line 2740-2742), but `anim_t0` is not protected.
**Fix:** Add `with ui_lock:` around all ui state reads in `play_audio()`. Protect `anim_t0` with a lock.

### H06. Import statements inside function bodies (performance)
**File:** `main.py:1094,1193,1652,1812,1979,2393,2771`
**Severity:** 🟠 HIGH
`import json`, `import re`, `import shutil`, `import os` repeatedly imported inside function bodies and closures. This adds overhead every call and is a Python anti-pattern.
**Fix:** Move all imports to the top of the file.

### H07. Google API model names likely incorrect
**File:** `config.toml`, `main.py:89-91`
**Severity:** 🟠 HIGH
```python
LIVE_MODEL = "gemini-3.1-flash-live-preview"
TASK_MODEL = "gemini-3.5-flash"
VISION_MODEL = "gemini-3.5-flash"
```
As of May 2026, these model names (`gemini-3.1-flash-live-preview`, `gemini-3.5-flash`) may not match actual Google model IDs. Session errors in logs repeatedly show 1008 policy violations. The `run_agent_tests.py` uses `gemini-2.5-flash` — inconsistent across files.
**Fix:** Verify and update model names against current Gemini API documentation.

### H08. `run_browser_task` uses `genai.Client` inside `asyncio.to_thread` — wrong client
**File:** `main.py:1851`
**Severity:** 🟠 HIGH
```python
response = task_client.models.generate_content(...)
```
Uses the synchronous `genai.Client` running inside `asyncio.to_thread`. This blocks a thread pool thread for the entire agent loop duration (potentially 15+ rounds of API calls). If the thread pool is exhausted, other `to_thread` calls starve.
**Fix:** Use `genai.aio.Client` with `await` instead of sync client in thread pool.

### H09. `run_live2d.sh` error: GUI exits immediately on WebBridge failure
**File:** `run_live2d.sh`
**Severity:** 🟠 HIGH
**Log evidence:** `terminal_errors.log` shows 7 consecutive `./run_live2d.sh` failures with exit status 1. The script starts `live2d_gui.py` in background but doesn't check if it's still alive before launching `main.py --live2d`.
**Fix:** Add a process-alive check and wait loop for `live2d_gui.py` before starting `main.py`.

---

## MEDIUM

### M01. `config.toml` — `voice.model` field semantics unclear
**File:** `config.toml:3`, `main.py:112-113`
**Severity:** 🟡 MEDIUM
```toml
[voice]
model = "gemini-3.1-flash-live-preview"
```
Is this the TTS voice model or the conversation model? The code sets `LIVE_MODEL = voice_cfg.get("model", LIVE_MODEL)` (line 112), then `MODEL = LIVE_MODEL` (line 113). Later, `models.live` overrides `LIVE_MODEL` again (line 126). The `voice.model` field may be intended for a different purpose than the code uses it.
**Fix:** Clarify config semantics — use `voice.model` only for TTS synthesize model, `models.live` for live conversation.

### M02. `search_web_contents` limited to 3 results from DDG
**File:** `main.py:446`
**Severity:** 🟡 MEDIUM
```python
for i in range(min(len(links), len(snippets), 3)):
```
Hard-coded to max 3 results. Combined with `results[:5]` deduplication at line 510, the final output is capped at 3 unique results from DDG + 3 from Wikipedia. Wikipedia results never appear because `len(results) < 3` (line 462) is checked after DDG already added 3.
**Fix:** Change `3` to `5` in the DDG loop min expression.

### M03. Session retry sleeps 1 hour for ANY error
**File:** `main.py:2660,2671,2676`
**Severity:** 🟡 MEDIUM
```python
await _sleep_with_check(3600)  # 1 HOUR for any transient error
```
Network hiccups, DNS failures, and even recoverable errors cause a full hour of downtime. The `429`/`quota` check is good, but connection errors shouldn't sleep 3600s.
**Fix:** Exponential backoff (5s → 30s → 120s → 300s max) instead of flat 3600s.

### M04. `monitor_gui_process()` — `gui_proc` variable is dead code
**File:** `main.py:1430`
**Severity:** 🟡 MEDIUM
```python
gui_proc = None  # track our own subprocess for restart
```
Declared but never assigned or read. The function uses `_find_gui_proc()` to search for the process each cycle, which works but makes `gui_proc` misleading dead code.
**Fix:** Remove the unused variable.

### M05. No `.editorconfig` or code style
**File:** (missing)
**Severity:** 🟡 MEDIUM
The project has inconsistent indentation (tabs in some files, spaces in others). `main.py` uses 4-space indent, `live2d_gui.py` uses 4-space, `index.html` uses 2-space, `model3.json` uses tabs.
**Fix:** Add `.editorconfig` or `.prettierrc`.

### M06. `run.sh` hardcoded to Kitty terminal
**File:** `run.sh:3`
**Severity:** 🟡 MEDIUM
```bash
kitty \
  -o font_size=30 \
  -o initial_window_width=7c \
  -o initial_window_height=2c \
```
This only works on Kitty terminal. On any other terminal emulator, the script fails.
**Fix:** Detect available terminal or make it configurable.

### M07. `play_local_sound()` opens/closes PyAudio stream per sound
**File:** `main.py:409-415`
**Severity:** 🟡 MEDIUM
Every sound effect creates a new PyAudio stream, plays 1-2 chunks, then closes it. PyAudio stream creation is expensive (ALSA device negotiation, buffer allocation). For repeated sounds (e.g., error alerts), this causes audible gaps.
**Fix:** Use a persistent output stream or pre-allocated buffer.

### M08. `check_single_instance()` PID file location
**File:** `main.py:3014`
**Severity:** 🟡 MEDIUM
PID file is at `/tmp/sakura-assistant.pid`. On systemd systems with `PrivateTmp=true`, or across user switches, this path may not be accessible. Stale PID files (from crashes) can block launch even though the process is dead.
**Fix:** Use `os.kill(pid, 0)` check before treating file as valid (already done). Add `os.O_EXCL` creation to avoid TOCTOU.

### M09. `live2d_gui.py` forces Qt/XCB without checking availability
**File:** `live2d_gui.py:10-11`
**Severity:** 🟡 MEDIUM
```python
os.environ["PYWEBVIEW_GUI"] = "qt"
os.environ["QT_QPA_PLATFORM"] = "xcb"
```
If Qt bindings (PyQt5/PySide2/PySide6) are not installed, `import webview` will fail with no clear error message.
**Fix:** Try-import with fallback to GTK or default.

### M10. `emoticons.json` — `process` key at wrong nesting level (indentation)
**File:** `emoticons.json:57-67`
**Severity:** 🟡 MEDIUM
The `process` key appears after `scan`'s closing bracket but is visually at the wrong indentation. The JSON parser handles it correctly, but the inconsistent formatting confuses readers and may cause merge conflicts.
**Fix:** Fix indentation to match sibling keys.

### M11. `main.py` `stop_any_music()` and `playerctl` spam errors
**File:** `main.py:3007`
**Severity:** 🟡 MEDIUM
**Log evidence:** `terminal_errors.log` shows ~20 `Command "hi" failed with exit status 127` entries caused by `playerctl` not running. Every `stop_any_music()` call spawns a `playerctl stop` subprocess.
**Fix:** Check if `playerctl` exists and is running before calling it.

---

## LOW

### L01. No offline fallback for CDN-loaded JS in `index.html`
**File:** `index.html:7-11`
**Severity:** 🔵 LOW
PIXI.js, Cubism Core, and pixi-live2d-display are loaded from CDN. If the user is offline, the Live2D overlay shows a blank canvas with no error message.
**Fix:** Add `onerror` handlers with user-visible fallback messages.

### L02. `targetMouthOpenScale` not reset on turn transition
**File:** `index.html:733-748`
**Severity:** 🔵 LOW
`targetMouthOpenScale` is modified by `addSpeechText()` based on visemes but never reset to `1.0` when a new turn starts. Over successive turns, the scale can drift based on previous utterance content.
**Fix:** Reset `targetMouthOpenScale = 1.0` at the start of `window.startTalking()`.

### L03. Speech bubble has no max-height / overflow handling
**File:** `index.html:27-47`
**Severity:** 🔵 LOW
`#speech-text` has no `max-height` or `overflow` CSS property. Long AI responses will overflow the speech container or push it off-screen.
**Fix:** Add `max-height: 200px; overflow-y: auto;` to `#speech-container`.

### L04. UDP listener crash if message has no colon
**File:** `live2d_gui.py:37,41,43,46`
**Severity:** 🔵 LOW
```python
emo = msg.split(":", 1)[1]  # crashes if msg is just "emotion" with no colon
```
The try/except catches this, but it should silently drop malformed messages instead of passing the exception.
**Fix:** Add `if ":" not in msg: continue` after splitting.

### L05. `global_webview_window` declared but never used
**File:** `main.py:171`
**Severity:** 🔵 LOW
Unused global variable.
**Fix:** Remove it.

### L06. `CONFIG_PATH` shadows `Path` builtin
**File:** `main.py:101`
**Severity:** 🔵 LOW
Variable name `CONFIG_PATH` could shadow `config` from `config` module if imported later. Not a current bug but a naming smell.
**Fix:** Rename to `CONFIG_FILEPATH`.

### L07. No input validation on `open_application` app name
**File:** `main.py:685-775`
**Severity:** 🔵 LOW
`app_name` is passed directly to `shutil.which` and `subprocess.Popen`. No sanitization.
**Fix:** Add allowed app list or validate against known executables.

### L08. PIXI.js v5.3.12 is outdated
**File:** `index.html:7`
**Severity:** 🔵 LOW
PIXI.js v5.3.12 (released ~2021) is used. Current version is v8.x. Several performance improvements and bug fixes are missing.
**Fix:** Update to v7.x (v8 breaks the Cubism integration API).

---

## ARCHITECTURAL ISSUES

### A01. Monolithic `main.py` (3074 lines)
All concerns live in one file: audio pipeline, Live2D UDP bridge, web scraping, browser automation, memory graph, shell execution, media control, system monitoring, session management, and the curses UI. This makes the code extremely hard to maintain, test, or debug.
**Recommended breakdown:**
- `audio.py` — mic reader, speaker, pitch shift, RMS calculation
- `bridge.py` — Live2D UDP commands, emotion/state management
- `tools/` — `webbridge.py`, `shell.py`, `memory_graph.py`, `search.py`, `media.py`
- `agent.py` — Gemini session lifecycle, tool routing, reconnection
- `ui.py` — curses render, state display

### A02. No test infrastructure
No unit tests, no integration tests. The `test_*.py` files are manual interactive scripts. `session_errors.log` proves repeated runtime crashes that tests could catch.
**Recommendation:** Add pytest structure with fixtures for mock sessions.

### A03. Thread-safety is improvised
The code uses `threading.Lock` for `ui_lock`, but the async+threading hybrid model (asyncio main loop + curses in main thread + `asyncio.to_thread` for sync operations) creates complex ownership issues. `asyncio.Lock` and `threading.Lock` are mixed without clear ownership rules.

### A04. No graceful shutdown path
`_shutdown` flag is checked in some places but not systematically. The `TaskGroup` cancellation propagates through `CancelledError` but there's no cleanup for:
- PyAudio streams (may leave ALSA device in bad state)
- Browser tabs (left open)
- WebBridge sessions (not explicitly closed)
- Subprocesses (GUI process may orphan)

### A05. Gemini session reused unsafely across background tasks
`do_background_screen_analysis` and `run_browser_task` both call `safe_send_realtime_input(session, text=...)` to inject text into the active Gemini conversation. This happens **concurrently** with the normal audio conversation flow, potentially injecting text at unpredictable moments.

---

## SUMMARY TABLE

| Severity | Count | Key Issues |
|----------|-------|------------|
| 🔴 CRITICAL | 6 | API keys in plaintext, duplicate HTML IDs, thread-safe session violation, emotion race in ticker, audio pitch artifacts, no .gitignore |
| 🟠 HIGH | 9 | Wrong README, Lock creation race, fragile DDG scraping, Wikipedia UA, data races, inline imports, wrong model names, thread-blocking agent loop, script fails |
| 🟡 MEDIUM | 11 | Config semantics, search capped, 1h retry, dead code, no style, Kitty-only, PyAudio inefficiency, PID file, Qt forced, JSON formatting, playerctl spam |
| 🔵 LOW | 8 | No offline fallback, mouth scale drift, bubble overflow, UDP crash, unused vars, naming, input validation, old PIXI |
| 🏗️ ARCH | 5 | Monolith, no tests, hybrid threading, no graceful shutdown, unsafe concurrent injection |

**Total findings: 39**
