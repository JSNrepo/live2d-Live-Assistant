# IDEAS — Sakura Desktop Companion

## Current Implementations

### Core
- **Voice conversation** with Gemini Live API (`gemini-3.1-flash-live-preview`)
- **Audio pipeline**: PyAudio mic → asyncio queue → `session.send_realtime_input()` → Gemini → `recv_audio()` → speaker queue → PyAudio playback
- **System prompt**: Aggressive Tamil rowdy personality (Thirunelveli slang, insults)
- **Auto-reconnect**: Outer retry loop reconnects session on error (3s delay)
- **Session lifecycle**: Activates on launch, runs until Ctrl+C

### Visual — Kaomoji mode (curses)
- **19 emotion states** mapped to kaomoji sequences in `emoticons.json`
- **Per-emotion animation speeds** (0.12s speaking to 0.5s idle/process)
- **Natural blinking** in process animation (occasional `(--_--)` inserted into mostly `(￣_￣)` frames)
- **Color-coded** faces by emotion: cyan (idle), green (speaking), yellow (listening), red (angry/error), magenta (boot/shutdown)
- Centered in terminal window, bold text

### Visual — Image mode (Kitty protocol)
- `--imageface` flag switches to JPEG images via Kitty graphics protocol
- 20 sliced expressions in `faces/` directory (833×833 px each)
- Image scaled to terminal size, chunked base64 transfer

### Window Management
- `run.sh`: double-clickable shell script, opens Kitty OS window with `font_size=30`, 7×2 cell window
- Window auto-sizes to face dimensions, no decorations

### Audio Pipeline Details
- **Send rate**: 16000 Hz (mic)
- **Recv rate**: 24000 Hz (speaker)
- **Chunk size**: 1024 frames
- **Speaker queue drain**: Emptied at end of each turn (after turn completes)
- **ALSA/JACK silence**: stderr redirected to /dev/null during PyAudio init

### Configuration Files
- `emoticons.json`: 19 animation sequences (8-24 frames each)
- `config.toml`: voice name, model, pitch, session settings, noise gate, emotion timing, logging
- `.env`: `GOOGLE_API_KEY`

### Supporting Files
- `slice_faces.py`: 4×5 grid image slicer → 20 face images
- `testing.py`: Reference implementation for simple GenAI SDK pattern

---

## Feature Ideas

### Ambient Life / Companion Behavior
- Time-aware greetings (morning/evening/night variants)
- Random idle commentary — blurts out unprompted lines on a timer
- Activity detection — mutters or changes expression after periods of silence
- Mood persistence — carries emotion/grumpiness level across sessions
- Nickname memory — remembers what you call it, calls you names back

### Reactive Personality
- Emotion persistence based on conversation history
- Initiate topics unprompted (movies, tech, rant about something)
- Deeper pool of canned lines for offline mode (no Gemini connection)

### Visual Improvements
- **Procedural kaomoji** — build faces dynamically each frame (pick eyes, mouth based on mood, add micro-animations like blinks, eye darts)
- **Smooth transitions** — cross-fade or morph between emotion states instead of instant switches
- **Effects** — vibration, pulsing, color transitions, subtle bounce
- **Multi-line face** — larger 3-5 line ASCII face with animated features
- **Image mode** — cross-fade between emotion images, animated eyes/mouth overlay

### Interaction
- Click/poke reaction — clicking the face triggers a response
- Mouse hover effects
- Drag to move window

### Offline / Resilience
- Offline mode — shows face and does canned expressions without Gemini connection
- Local fallback responses if API call fails
- Cached personality state survives restarts

### Ambient UI
- Floating window idle animation — subtle pulse, wobble, or slow drift
- Desktop notifications for long events
- Always-on-top window mode

### Sound / Voice
- Voice activity indicator — face changes while listening/speaking
- Local sound effects (blips, notifications)
- Volume-reactive face (open mouth wider when louder)

### Memory & Context
- Remember conversation topics across sessions (disk-backed)
- Recall user preferences (name, mood, common requests)
- Session log browsing
