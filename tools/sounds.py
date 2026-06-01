import os
import pyaudio
import shutil
import subprocess
from pathlib import Path
from config import log, PROJECT_ROOT


def _silence_alsa():
    old = os.dup(2)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    os.close(null)
    return old


def _restore_stderr(fd):
    os.dup2(fd, 2)
    os.close(fd)


# H1: Module-level PyAudio singleton — avoids re-querying all audio devices per sound
_pya_singleton = None
_pya_init_done = False


def _get_pya():
    """Lazily initialize a singleton PyAudio instance with ALSA warnings silenced."""
    global _pya_singleton, _pya_init_done
    if _pya_singleton is None or not _pya_init_done:
        fd = _silence_alsa()
        try:
            import time
            _pya_singleton = pyaudio.PyAudio()
            time.sleep(0.15)
            _pya_init_done = True
        finally:
            _restore_stderr(fd)
    return _pya_singleton


def play_local_sound(filename: str):
    log.debug("play_sound %s", filename)
    if filename.endswith(".wav"):
        pcm_filename = filename[:-4] + ".pcm"
        pcm_path = PROJECT_ROOT / "sounds" / pcm_filename
        if pcm_path.exists():
            filename = pcm_filename

    sound_path = PROJECT_ROOT / "sounds" / filename
    if not sound_path.exists():
        log.debug("play_sound not_found %s", sound_path)
        return
    stream = None
    try:
        with open(sound_path, "rb") as f:
            data = f.read()

        pya = _get_pya()
        fd = _silence_alsa()
        try:
            import time
            stream = pya.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
            time.sleep(0.15)
        finally:
            _restore_stderr(fd)
        chunk_size = 1024
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            stream.write(chunk)
        log.debug("play_sound done %s len=%d", filename, len(data))
    except Exception as e:
        log.debug("play_sound err %s %s", filename, e)
    finally:
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass


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
