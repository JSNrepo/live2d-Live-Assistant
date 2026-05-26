# LivePythonGemini — Retest Report (Post-Antigravity)

**Date:** 2026-05-26
**Scope:** Full codebase audit revisited — what antigravity fixed, what regressed, and what remains

---

## SUMMARY

| Severity | Original | Fixed by Antigravity | New/Regression | Remaining |
|----------|----------|---------------------|----------------|-----------|
| 🔴 CRITICAL | 6 | 3 | 1 (C06 confirmed) | 3 |
| 🟠 HIGH | 9 | 1 | 0 | 8 |
| 🟡 MEDIUM | 11 | 0 | 0 | 11 |
| 🔵 LOW | 8 | 1 | 0 | 7 |
| 🏗️ ARCH | 5 | 0 | 0 | 5 |

---

## WHAT ANTIGRAVITY FIXED

### C01. No .gitignore — **FIXED** ✓
`.gitignore` now exists with: `.env`, `.venv/`, `__pycache__/`, `logs/`, `*.pyc`, `sounds/`

### C02. Duplicate HTML `id` attributes — **FIXED** ✓
`index.html:117-120` now has single `id` attributes (no duplicates).

### C04. Non-thread-safe Gemini session — **FIXED** ✓
`run_agent_sync` (which called `safe_send_realtime_input` from a thread pool via `asyncio.to_thread`) has been **removed**. Replaced by `run_browser_task` which is an `async def` running on the event loop via `asyncio.create_task()`.

### H02. `get_send_lock()` race — **PARTIALLY FIXED** ~
`session_send_lock = asyncio.Lock()` added at `main.py:2719-2720` inside `main_async()`, initializing the lock before any coroutines start. The lazy init `get_send_lock()` still exists but is now a no-op since the lock is always pre-initialized.

### L04. UDP crash on missing colon — **FIXED** ✓
`live2d_gui.py:32-35` now has `if ":" in msg` check before splitting.

---

## NEW/REGRESSION FINDING

### C06. Pitch shifter produces severe audio artifacts — **CONFIRMED (CRITICAL)**
**File:** `main.py:2291-2361` (`do_pitch_shift`)
**Symptoms:** Scratching/"keech keech" noise during AI speech. SNR measured at **1.2 dB** (signal barely above noise floor). 158+ sample discontinuities/second > 10000 amplitude.

**Root Cause:** Dual-tap delay line with `D_max=512` and `pitch_factor=3.15`. The crossfade weights (`w1 = sin(theta)`, `w2 = cos(theta)`) jump discontinuously when `d1` wraps from near-0 back to `D_max`. The output transitions from `tap2` (delay D_max/2) to `tap1` (delay D_max) in one sample — these are different buffer positions.

**Evidence:**
- D_max=512, factor=3.15: SNR=1.2 dB, 158 discontinuities/sec, max jump=36401
- D_max=4096, factor=3.15: SNR=-5.0 dB, 11 discontinuities/sec, max jump=39136

**Fix:** Increase `D_max` to at least 4096 and fix the crossfade transition logic. Or replace with `librosa.effects.pitch_shift` (preferred) or a proper phase vocoder.

---

## REMAINING AUDIT FINDINGS (Unchanged)

### CRITICAL
- **C01 (cont.).** API keys in `.env` plaintext — mitigated by `.gitignore` but not eliminated.
- **C03 (retracted).** Was inside `run_agent_sync` which is now removed.
- **C05.** `index.html:404`: `currentEmotion = 'confused'` still bypasses `window.setEmotion()` inside ticker (startled override). LISTENING/THINKING overrides removed — partial fix only.

### HIGH
- **H01.** README describes LiveKit Agents architecture (doesn't exist). Still misleading.
- **H02 (cont.).** `get_send_lock()` lazy init race still present in code (line 201-207) — mitigated by pre-init at line 2720.
- **H03.** DDG Lite regex scraping will break on HTML change (`main.py:437-457`).
- **H04.** Wikipedia User-Agent has placeholder contact info (`main.py:473`).
- **H05.** UI state accessed without lock in `play_audio()` — `anim_t0` not protected.
- **H06.** 38 inline imports still function bodies (`main.py:177-3046`).
- **H07.** Model names may be incorrect (`gemini-3.1-flash-live-preview` etc.).
- **H08.** `task_client.aio.models.generate_content()` runs async but in 15-step loop — long-running but non-blocking now.
- **H09.** `run_live2d.sh` error — GUI exits on WebBridge failure.

### MEDIUM (11 findings — all unchanged)
- M01: Config semantics unclear
- M02: Search limited to 3 DDG results
- M03: Session retry sleeps 3600s flat
- M04: Dead code `gui_proc = None`
- M05: No `.editorconfig`
- M06: `run.sh` hardcoded to Kitty
- M07: PyAudio stream per sound effect
- M08: PID file at `/tmp/sakura-assistant.pid`
- M09: Qt/XCB forced without check
- M10: `emoticons.json` formatting
- M11: `playerctl` spam errors

### LOW (7 remaining)
- L01, L02, L03, L05, L06, L07, L08 — all unchanged from original audit.

### ARCHITECTURAL (5 — all unchanged)
- A01: Monolithic main.py (3115 lines)
- A02: No test infrastructure
- A03: Hybrid threading issues
- A04: No graceful shutdown
- A05: Concurrent session injection

---

## VERIFICATION NOTES

### Syntax Check
`python3 -m py_compile main.py` — **PASS** (no syntax errors)

### Dependency Check
All core deps installed: numpy 2.4.6, scipy 1.17.1, google-generativeai 0.8.6, pyaudio (needs system lib)

**Note:** `scipy.signal` imported at `main.py:23` but **not used anywhere** — dead import.

### Pitch Shift Test
`do_pitch_shift` at `main.py:2291`:
- Algorithm: Dual-tap delay line with sine/cosine crossfade (NOT the double-resample hallucinated in original audit)
- `D_max = 512`, `N_buf = 16384`
- Called with `PITCH_SHIFT = 3.15` (from config)
- Processed in `sub_chunk_size = 960` (20ms) pieces for lip-sync
- **Confirmed: ~158 severe discontinuities/second**, SNR 1.2 dB

---

## RECOMMENDED NEXT STEPS

1. **P0 — Fix pitch shifter** (`main.py:2291-2361`): Replace with proper implementation
2. **P1 — Emotion ticker fix** (`index.html:404`): Use `window.setEmotion('confused')` instead of raw assignment
3. **P2 — Remove dead `scipy.signal` import** (`main.py:23`): Unused import, wastes startup time
4. **P3 — Move inline imports to top** (38 occurrences across `main.py`)
