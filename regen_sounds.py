#!/usr/bin/env python3
"""
regen_sounds.py – Regenerate the short .wav audio cues used by LivePythonGemini.

Dependencies (must be installed on the system):
    * espeak   – text‑to‑speech engine
    * ffmpeg   – converts the raw audio stream to .wav

Install on Debian/Ubuntu:
    sudo apt-get install espeak ffmpeg
"""

import subprocess
from pathlib import Path

# Directory where the .wav files live (relative to project root)
SOUNDS_DIR = Path(__file__).parent / "sounds"
SOUNDS_DIR.mkdir(exist_ok=True)

# Define the cues we want to (re)create. Adjust the text to match the new personality.
CUES = {
    "api_exhausted.wav": "You have exhausted the API, go refill it, you lazy fool.",
    "crash.wav": "The system crashed, you messed it up again.",
    "offline.wav": "You are offline, stop whining and get a connection.",
    "test_Aoede.wav": "Test sound Aoede, you pathetic user.",
    "test_Kore.wav": "Test sound Kore, get lost.",
}

def generate_wav(text: str, out_path: Path):
    """Generate a .wav file from *text* using espeak → ffmpeg.
    The output is mono, 16‑bit, 24 kHz, matching the project's audio settings.
    """
    # espeak prints raw PCM to stdout, we pipe that to ffmpeg to wrap in a .wav container.
    espeak_cmd = ["espeak", "-w", "-", "-s", "150", "-p", "50", "-v", "en-us", text]
    ffmpeg_cmd = ["ffmpeg", "-y", "-f", "s16le", "-ar", "22050", "-ac", "1", "-i", "-", str(out_path)]
    # Run the pipeline
    espeak = subprocess.Popen(espeak_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=espeak.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    espeak.stdout.close()  # Allow espeak to receive a SIGPIPE if ffmpeg exits.
    ffmpeg.communicate()
    espeak.wait()
    if ffmpeg.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {out_path}")

def main():
    for filename, text in CUES.items():
        out_path = SOUNDS_DIR / filename
        try:
            generate_wav(text, out_path)
            print(f"✅ Generated {out_path.name}")
        except Exception as e:
            print(f"❌ Failed to generate {out_path.name}: {e}")

if __name__ == "__main__":
    main()
