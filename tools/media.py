import asyncio
import subprocess
import urllib.parse
import webbrowser

from config import AppState, log, _HAS_PLAYERCTL
from live2d import ui, ui_lock, set_state
from tools.webbridge import check_webbridge_active_sync, webbridge_navigate


def play_song_online(song_name: str) -> dict:
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(song_name)}"
    if check_webbridge_active_sync():
        res = webbridge_navigate(url, new_tab=True, session="youtube")
        if "error" not in res:
            return {
                "status": f"Successfully opened YouTube music search for '{song_name}' using Kimi WebBridge.",
                "tabId": res.get("tabId"),
            }
    webbrowser.open(url)
    return {"status": f"Opened YouTube search for '{song_name}' in your default local browser (fallback)."}


def control_browser_media(action: str) -> dict:
    cmd = []
    if action == "pause":
        cmd = ["playerctl", "pause"]
    elif action == "play":
        cmd = ["playerctl", "play"]
    elif action in ("toggle", "play-pause"):
        cmd = ["playerctl", "play-pause"]
    elif action == "stop":
        cmd = ["playerctl", "stop"]
    elif action == "next":
        cmd = ["playerctl", "next"]
    elif action == "previous":
        cmd = ["playerctl", "previous"]
    elif action == "volume_up":
        cmd = ["playerctl", "volume", "0.1+"]
    elif action == "volume_down":
        cmd = ["playerctl", "volume", "0.1-"]
    elif action == "seek_forward":
        cmd = ["playerctl", "position", "10+"]
    elif action == "seek_backward":
        cmd = ["playerctl", "position", "10-"]
    else:
        return {"error": f"Unknown action: '{action}'."}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return {"status": f"Successfully executed system media action: '{action}'."}
        else:
            return {
                "error": f"Failed to control media player. Output: {proc.stderr.strip() or proc.stdout.strip()}"
            }
    except Exception as e:
        return {"error": f"Exception executing browser/system media control: {str(e)}"}


def stop_music() -> dict:
    return control_browser_media("stop")


def pause_resume_music() -> dict:
    return control_browser_media("toggle")


def show_images_online(query: str) -> dict:
    query_encoded = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?tbm=isch&q={query_encoded}"
    if check_webbridge_active_sync():
        res = webbridge_navigate(url, new_tab=True, session="images")
        if "error" not in res:
            return {
                "status": f"Successfully opened Google Image search for '{query}' using Kimi WebBridge.",
                "tabId": res.get("tabId"),
            }
    webbrowser.open(url)
    return {"status": f"Successfully opened images for '{query}' in default local browser (fallback)."}


def open_browser(url: str) -> dict:
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    if check_webbridge_active_sync():
        res = webbridge_navigate(url, new_tab=True, session="kimi")
        if "error" not in res:
            return {
                "status": f"Successfully opened and navigated to '{url}' using Kimi WebBridge.",
                "tabId": res.get("tabId"),
            }
    webbrowser.open(url)
    return {"status": f"Successfully opened and navigated to '{url}' in default local browser (fallback)."}


async def check_music_playing() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "playerctl",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        result = b"Playing" in stdout
        log.debug("check_music %s", result)
        return result
    except Exception as e:
        log.debug("check_music err %s", e)
        return False


async def monitor_music_and_vibe(session):
    log.debug("monitor_music start")
    if not _HAS_PLAYERCTL:
        log.debug("monitor_music disabled (playerctl absent)")
        return
    while True:
        try:
            is_playing = await check_music_playing()
            with ui_lock:
                current_state = ui.state
                current_emotion = ui.emotion

            if is_playing:
                if current_state in (
                    AppState.LISTENING,
                    AppState.SLEEPING,
                ) and current_emotion not in ("speaking", "process", "searching"):
                    log.debug("monitor_music vibing")
                    set_state(AppState.LISTENING, "vibing", "Vibing to music...")
            else:
                if current_emotion == "vibing":
                    log.debug("monitor_music idle")
                    set_state(AppState.LISTENING, "idle", "Listening...")
        except Exception as e:
            log.debug("monitor_music err %s", e)
            pass
        await asyncio.sleep(2.0)
