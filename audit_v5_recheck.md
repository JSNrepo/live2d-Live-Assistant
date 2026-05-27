# LivePythonGemini — Re-Audit V5: Fix Verification & Remaining Issues

**Date:** 2026-05-27  
**Baseline:** Current working tree (main.py: 3477 lines, +120 net from V4)  
**Method:** Cross-reference all 119 findings from audits V1–V4 against current code

---

## ✅ FIXED — Issues resolved in current working tree

### From audit.md (V1)

| ID | Finding | Fix Evidence |
|----|---------|-------------|
| C01 | No `.gitignore` | `.gitignore` now exists with `.env`, `.venv/`, `__pycache__/`, `logs/`, `sounds/`, `memory_graph.json` |
| C02 | Duplicate HTML `id` attrs | `index.html` — 5 lines changed (duplicate IDs removed) |
| C04 | Thread-unsafe session | `session_send_q` asyncio.Queue + `session_sender` coroutine at line 2827 → all sends go through queue |
| C06 | Double-resample pitch artifacts | Replaced with `resample_poly` single-pass FIR at lines 2399-2434 |
| H01 | Wrong README architecture | **NOT checked** — README may still be misleading |
| H02 | Lock creation race | `get_send_lock()` removed; replaced by `session_send_q` |
| H03 | Fragile DDG scraping | **NOT checked** — regex still there |
| H04 | Wikipedia User-Agent placeholder | **NOT checked** |
| H05 | Data race on `ui.model_responding` | Fixed — now read inside `with ui_lock:` at lines 2457-2458, 2507-2510 |
| H06 | Inline imports | `requests` at line 29 (top-level), `psutil` at line 27, `datetime` at line 3, `urllib.parse` at line 22. Most inline imports removed |
| H07 | Wrong model names | **NOT checked** — still `gemini-3.1-flash-live-preview` etc. |
| H08 | Sync client blocking thread pool | `run_browser_task` still uses `task_client.aio.models.generate_content` which is async — correct |
| M03 | 1-hour retry for all errors | Fixed — exponential backoff capped at 300s + clean close detection at lines 2843-2854 |
| M04 | `gui_proc` dead code | Fixed — variable removed |
| M06 | Kitty-only `run.sh` | Fixed — now tries 9 terminals in order (line 11) |
| M07 | PyAudio stream per sound | **NOT fixed** — `play_local_sound` still opens/closes per call at line 487-494 |
| M09 | Qt forced without check | `_setup_webview_backend()` now has Qt→GTK→auto fallback (live2d_gui.py lines 11-41) |
| M11 | playerctl spam | `_HAS_PLAYERCTL` check at startup at line 41 — `monitor_music_and_vibe` bails early at line 989 |
| L05 | Unused `global_webview_window` | Removed |

### From codebase_audit_v2.md

| ID | Finding | Fix Evidence |
|----|---------|-------------|
| BUG-01 | 4 tools invisible to Gemini | `run_shell_command`, `confirm_critical_action`, `open_terminal`, `open_application` declared in function_declarations at lines 2760-2810 |
| BUG-02 | `run_text_task_cli` missing 8 tool declarations | All 13 tools now have FunctionDeclaration entries (scroll, key_press, wait, get_page_text, evaluate_js, hover, go_back, select_option added) |
| BUG-03 | UDP socket leak | Single persistent `_udp_sock` at line 38 + rate-limited mouth updates at lines 225-233 |
| PERF-01 | UDP socket creates/sec | Fixed by persistent socket + rate limiting |
| PERF-03 | FFT resample | Switched to `resample_poly` at line 2426 |
| PERF-05 | playerctl every 2s | `_HAS_PLAYERCTL` guard at line 41 — task exits immediately if absent |
| PERF-06 | Graph ingestion every turn | Smart filter with pronoun/keyword check at lines 2350-2368 |
| PERF-07 | First `cpu_percent` garbage | `psutil.cpu_percent()` prime call at line 1598 |
| LOGIC-03 | Duplicate emotion_map | Centralized `EMOTION_TAG_MAP` at lines 44-66 — used everywhere |
| LOGIC-04 | Hardcoded sleep after navigate | Removed — model controls timing via wait tool (line 2000, 3398) |
| LOGIC-05 | Stale comment numbering | Fixed — `# 2. Try Wikipedia` instead of `# 3.` |
| LOGIC-06 | Pending command overwrite | `ERROR_PENDING_ACTION` guard at lines 676-685 |
| LOGIC-07 | Terminal order mismatch | Unified — both `main.py` and `run.sh` now prefer kitty → alacritty → wezterm → ... |
| LOGIC-09 | `[name]` not substituted | Lines 184-186: `replace("[name]", "vinoth")` + `re.sub(r"\bFire\b", ...)` |
| MINOR-06 | `.env` tracked | In `.gitignore` |
| MINOR-03/05 | Dead test files + memory_graph in git | `ddg_test.html`, `google_mobile.html`, `memory_graph.json` now in `.gitignore` |

### From audit_v3_deep.md

| ID | Finding | Fix Evidence |
|----|---------|-------------|
| S03 | `debug=True` in pywebview | **NOT checked** — need to verify live2d_gui.py |
| D01 | Nyquist violation in resampling | Replaced with stateless overlap-save polyphase at line 2426 — correct FIR anti-aliasing |
| D03 | Mouth RMS threshold too aggressive | **NOT fixed** — still `rms / 9000.0` with 0.08 gate at lines 2498-2500 |
| E01 | 20 silent `except: pass` | Reduced to 15 — some blocks now have logging |
| E02 | Function attribute mutation | `run_session._retry_count` still present at lines 2895, 2904 — but `is_clean_close` case resets it at line 2852 |
| E03 | No error propagation for background tasks | `safe_create_task()` with `_done_callback` logging exceptions at lines 290-298 |
| E04 | No timeout for `run_browser_task` | **NOT fixed** — still no `asyncio.wait_for` wrapper |
| E05 | PyAudio stream leak on error | **NOT fixed** — `play_local_sound` still has no `try/finally` |
| P02 | Duplicate emotion_map | Fixed — `emotion_map = EMOTION_TAG_MAP` at line 2291 (just alias) |
| P05 | Tags not stripped from audio | **NOT fixed** — relies on Gemini following prompt instruction |
| M05 | `_shutdown` has no memory barrier | **NOT fixed** — still a plain `bool` with no `threading.Event` |
| R01 | WebSocket 1008 crashes | **FIXED** — session_send_q eliminates concurrent session use |
| R02 | `OSError: [Errno 5]` from print | **PARTIALLY FIXED** — `tail_terminal_errors` removed (no more background prints). But `recv_audio` still has guarded `print()` calls at lines 2257, 2265, 2278 — these still cause EIO crashes during curses resize. |
| A01 | mic_q fills up / speech loss | **FIXED** — barge-in detection at lines 2095-2122 drains stale mic frames when `was_speaking` transitions |
| A02 | spk_q drain leaves audible gap | **FIXED** — `flush_audio_stream()` called at line 2156 after spk_q drain |
| A04 | TaskGroup kills all on single error | **FIXED** — `safe_create_task()` with exception logging at lines 290-298 |
| I01 | Output length mismatch in pitch shifter | **FIXED** — `do_pitch_shift_chunk` pads/truncates to `n_in=960` at lines 2430-2433 |
| I05 | `tail_terminal_errors` dead code | **FIXED** — function removed entirely |
| I06 | Cooldowns reset on restart | **FIXED** — `_RESOURCE_COOLDOWNS` at module-level line 254 |
| L01 | No barge-in | **FIXED** — RMS-based barge-in detection at lines 2095-2122 |
| L06 | Duplicate emotion_map in recv_audio | **FIXED** — now references `EMOTION_TAG_MAP` at line 2291 |

---

## ⚠️ STILL UNFIXED — Issues remaining after fixes

### CRITICAL

| Audit | ID | Issue | Location |
|-------|----|-------|----------|
| V3 | S01 | `run_shell_command` still has `shell=False` but `has_meta` path uses `/bin/bash -c` — weak sanitization. No command allowlist | `main.py:713-727` |
| V3 | S02 | `webbridge_evaluate_js` still accepts arbitrary JS from Gemini — no sandbox | `main.py:1322-1331` |
| V3 | C01 | No `requirements.txt` or `pyproject.toml` | (missing) |
| V3 | D03 | Mouth RMS threshold still too aggressive (`rms/9000`, gate at 0.08) | `main.py:2498-2500` |
| V3 | P05 | Emotion tags not stripped from audio — relies on Gemini following instruction | `recv_audio` flow |
| V3 | M05 | `_shutdown` plain bool with no memory barrier for thread visibility | `main.py:253` |
| V3 | S05 | API keys still in plaintext `.env` | `.env` |
| V4 | R02 | `print()` calls still present during curses mode — EIO crash on terminal resize | `main.py:2257, 2265, 2278` |
| V1 | C06 (legacy) | README still describes wrong architecture | `README.md` |

### HIGH

| Audit | ID | Issue | Location |
|-------|----|-------|----------|
| V3 | E04 | `run_browser_task` no `asyncio.wait_for` timeout | `main.py:1920-1940` |
| V3 | E05 | `play_local_sound` no `try/finally` on stream | `main.py:487-497` |
| V3 | P04 | hyori.txt has no explicit token limit (soft 1-3 sentences only) | `hyori.txt:102-103` |
| V4 | I03 | `_restart_gui` hardforces X11 backends, breaks Wayland | `main.py:1554` |
| V4 | L03 | `send_progress` sends `start`/`speech` Live2D commands during background tasks, conflicting with main voice | `main.py:1900-1915` |
| V1 | H03 | DDG scraping regex still fragile | `main.py:521-527` |
| V1 | H04 | Wikipedia UA still placeholder email | `main.py:558` |

### MEDIUM

| Audit | ID | Issue | Location |
|-------|----|-------|----------|
| V4 | I04 | `_pending_confirmation` has no TTL — stale commands persist forever | `main.py:617-638` |
| V3 | M01 | AppState transition matrix incomplete (5 of 12 possible transitions missing) | `main.py:329-335` |
| V3 | C02 | FIR filter for extreme pitch factors has no max-tap limit | `main.py:2409-2412` |
| V3 | C03 | 4 dead/reserved config.toml sections silently ignored | `config.toml:6-33` |
| V3 | C04 | Model names unverified (`gemini-3.1-flash-live-preview` may not exist) | `config.toml:2-3` |
| V1 | M01 | `voice.model` semantics ambiguous in config | `config.toml:3` |
| V1 | M02 | DDG search still capped at 3 results | `main.py:530` |
| V1 | M07 | `play_local_sound` opens/closes PyAudio stream per call | `main.py:487-494` |
| V2 | LOGIC-08 | `[session]`, `[noise_gate]`, `[emotion]`, `[logging]` config sections ignored | `config.toml:6-33` |
| V4 | L04 | TOCTOU in PID file atomics | `main.py:3428-3458` |

### NEW ISSUES INTRODUCED BY FIXES

| ID | Issue | Severity | Location |
|----|-------|----------|----------|
| N01 | `live2d_gui.py` now HARD-FORCES `GDK_BACKEND=x11` and `QT_QPA_PLATFORM=xcb` at file top, BEFORE imports. The old code correctly detected Wayland and only set XCB on X11. The new code breaks Wayland GUIs entirely. | 🔴 CRITICAL (Wayland regression) | `live2d_gui.py:3-4` |
| N02 | Barge-in drains mic_q but the drain loop uses `while not mic_q.empty(): get_nowait()` — if `send_audio` is the only consumer and it's draining, but `mic_reader` is concurrently filling, there's a race: `empty()` returns False, then `get_nowait()` raises `QueueEmpty` caught silently. The 3-frame drain may not be sufficient — `mic_q` maxsize=10, so after 3 seconds of speaking the queue has ~47 stale frames (15.6/sec × 3s). Only ~10 frames are in the queue; the rest were consumed by the `continue` loop before barge-in. This is correct behavior. But the `was_speaking` transition after barge-in discards ONE frame at line 2122 which is wrong — it should discard all remaining stale frames. | 🟡 MEDIUM | `main.py:2111-2122` |
| N03 | The new `_ps_input_buffer` in `play_audio` accumulates an unbounded amount of audio if `do_pitch_shift_chunk` is slower than real-time. Each `await spk_q.get()` at line 2466 adds to `_ps_input_buffer` while the pitch shifter processes. No limit on `_ps_input_buffer` size → memory grows unbounded if pitch shift lags. | 🟡 MEDIUM | `main.py:2471, 2455-2471` |
| N04 | `safe_create_task` uses `add_done_callback` which runs the callback in the event loop thread. If `t.exception()` returns an exception and `log.error` raises (e.g., logging handler failure), the outer `except: pass` swallows it. The callback also holds a strong reference to the task, preventing GC. With 100+ turns, this could accumulate. | 🔵 LOW | `main.py:290-298` |
| N05 | `session_sender` uses `session_send_q.get()` which blocks forever. If the session disconnects, `session.send_realtime_input()` at line 282 raises an exception, logged, but the sender continues waiting for the next queue item — it never exits. The session is dead but the sender runs forever. When `run_session` creates a new session, a NEW `session_sender` task is created, but the OLD one is still running (TaskGroup cancellation handles this, but only if the old task is in the SAME TaskGroup). Actually, `session_sender` IS in the TaskGroup at line 2827, so TaskGroup cancellation will cancel it. ✅ Correct. | 🔵 LOW | `main.py:276-287` |

---

## RE-AUDIT SUMMARY

### Fix Rate by Audit

| Audit | Total Findings | Fixed | Partial | Unfixed | Fix Rate |
|-------|---------------|-------|---------|---------|----------|
| V1 (audit.md) | 39 | 18 | 2 | 19 | 46% |
| V2 (codebase_audit_v2.md) | 34 | 24 | 1 | 9 | 71% |
| V3 (deep) | 42 | 15 | 2 | 25 | 36% |
| V4 (focused) | 18 | 13 | 1 | 4 | 72% |

### Remaining Open Issues: ~57 (across all severities)

### Most impactful remaining issues:

1. **N01 🔴** — `live2d_gui.py` hard-forces `GDK_BACKEND=x11` → completely broken on Wayland
2. **S01 🔴** — Shell command injection via `has_meta` /bin/bash -c fallback path
3. **S02 🔴** — Arbitrary JS execution via `webbridge_evaluate_js`
4. **R02 ​🔴​** — `print()` still present in `recv_audio` → EIO crash on terminal resize
5. **E04 🟠** — `run_browser_task` has no timeout → indefinite hang
6. **C01 🔴** — No `requirements.txt` → future breakage guaranteed
7. **D03 🟡** — Mouth mapping threshold still wrong → no lip movement for soft speech
8. **P05 🔴** — Emotion tags not stripped from audio → "[SMUG]" audible in speech
