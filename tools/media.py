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
    elif action.startswith("seek_to:"):
        try:
            target_secs = float(action.split(":", 1)[1])
            cmd = ["playerctl", "position", str(target_secs)]
        except Exception as e:
            return {"error": f"Failed to parse seek position: {str(e)}"}
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


import json

async def check_music_playing() -> str:
    meta = await check_music_metadata()
    return meta["status"]

async def check_music_metadata() -> dict:
    try:
        proc_status = await asyncio.create_subprocess_exec(
            "playerctl",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc_status.communicate()
        status_str = stdout.decode("utf-8").strip()
        
        if not status_str or "No players found" in status_str:
            return {"status": "idle", "position": 0, "length": 0, "title": "", "artist": ""}
            
        status = "playing" if "Playing" in status_str else ("paused" if "Paused" in status_str else "idle")
        if status == "idle":
            return {"status": "idle", "position": 0, "length": 0, "title": "", "artist": ""}
            
        # Get position (returns seconds as float)
        proc_pos = await asyncio.create_subprocess_exec(
            "playerctl",
            "position",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_pos, _ = await proc_pos.communicate()
        try:
            position = float(stdout_pos.decode("utf-8").strip())
        except ValueError:
            position = 0.0
            
        # Get length (mpris:length is in microseconds)
        proc_len = await asyncio.create_subprocess_exec(
            "playerctl",
            "metadata",
            "mpris:length",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_len, _ = await proc_len.communicate()
        try:
            length_us = float(stdout_len.decode("utf-8").strip())
            length = length_us / 1000000.0
        except ValueError:
            length = 0.0
            
        # Get title
        proc_title = await asyncio.create_subprocess_exec(
            "playerctl",
            "metadata",
            "xesam:title",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_title, _ = await proc_title.communicate()
        title = stdout_title.decode("utf-8").strip()
        
        # Get artist
        proc_artist = await asyncio.create_subprocess_exec(
            "playerctl",
            "metadata",
            "xesam:artist",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_artist, _ = await proc_artist.communicate()
        artist = stdout_artist.decode("utf-8").strip()
        
        return {
            "status": status,
            "position": position,
            "length": length,
            "title": title,
            "artist": artist
        }
    except Exception as e:
        log.debug("check_music_metadata err %s", e)
        return {"status": "idle", "position": 0, "length": 0, "title": "", "artist": ""}


async def monitor_music_and_vibe(session):
    log.debug("monitor_music start")
    if not _HAS_PLAYERCTL:
        log.debug("monitor_music disabled (playerctl absent)")
        return
    while True:
        sleep_delay = 2.0
        try:
            meta = await check_music_metadata()
            music_status = meta["status"]
            
            try:
                import socket
                _gui_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                # Broadcast compatibility string
                _gui_sock.sendto(f"music:{music_status}".encode("utf-8"), ("127.0.0.1", 10088))
                
                # Broadcast rich JSON metadata
                meta_json = json.dumps(meta)
                _gui_sock.sendto(f"music_meta:{meta_json}".encode("utf-8"), ("127.0.0.1", 10088))
            except Exception:
                pass
                
            with ui_lock:
                current_state = ui.state
                current_emotion = ui.emotion

            is_playing = (music_status == "playing")
            if is_playing:
                sleep_delay = 0.5  # Poll faster (500ms) when playing to keep progress bars completely smooth!
                if current_state in (
                    AppState.LISTENING,
                    AppState.SLEEPING,
                ) and current_emotion not in ("speaking", "process", "searching"):
                    log.debug("monitor_music vibing")
                    set_state(AppState.LISTENING, "vibing", "Vibing to music...")
            else:
                sleep_delay = 2.0  # Poll slower (2.0s) in standby/paused modes to save CPU
                if current_emotion == "vibing":
                    log.debug("monitor_music idle")
                    set_state(AppState.LISTENING, "idle", "Listening...")
        except Exception as e:
            log.debug("monitor_music err %s", e)
            pass
        await asyncio.sleep(sleep_delay)

