#!/usr/bin/env python3
import os
import sys
import socket
import threading
import tomllib
import webview

# M09: Detect available GUI backend instead of hard-forcing Qt/XCB.
# Try Qt first (preferred for KDE/X11), fall back to GTK, then webview default.
def _setup_webview_backend():
    """Pick the best available pywebview GUI backend for the current environment."""
    wayland = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"

    # Try Qt (PyQt5/PySide2/PySide6)
    try:
        import importlib
        qt_available = any(
            importlib.util.find_spec(m) is not None
            for m in ("PyQt5", "PySide2", "PySide6", "PyQt6")
        )
        if qt_available:
            os.environ["PYWEBVIEW_GUI"] = "qt"
            os.environ["QT_QPA_PLATFORM"] = "xcb"  # Force XCB via XWayland for Qt in pywebview
            return "qt"
    except Exception:
        pass

    # Try GTK
    try:
        import importlib
        if importlib.util.find_spec("gi") is not None:
            os.environ["PYWEBVIEW_GUI"] = "gtk"
            if not wayland:
                os.environ["GDK_BACKEND"] = "x11"
            return "gtk"
    except Exception:
        pass

    # Let webview pick its own default (cef, mshtml, edgechromium, etc.)
    return "auto"

_gui_backend = _setup_webview_backend()
print(f"[Live2D] Using pywebview backend: {_gui_backend}")


def listen_udp(window):
    """
    Asynchronously listens for UDP speech triggers from main.py
    and updates the WebGL canvas talking state instantly.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 10088))
    except Exception as e:
        print(f"Error: Could not bind UDP socket on port 10088: {e}")
        return

    print("[Live2D Listener] Listening for speech triggers on 127.0.0.1:10088...")
    while True:
        try:
            data, _ = sock.recvfrom(65536)
            msg = data.decode("utf-8").strip()
            if ":" in msg:
                cmd, val = msg.split(":", 1)
            else:
                cmd, val = msg, ""

            if cmd == "start":
                window.evaluate_js('window.startTalking();')
            elif cmd == "stop":
                window.evaluate_js('window.stopTalking();')
            elif cmd == "emotion":
                window.evaluate_js(f'window.setEmotion("{val}");')
            elif cmd == "mouth":
                window.evaluate_js(f'window.setMouth({val if val else 0.0});')
            elif cmd == "mic_rms":
                window.evaluate_js(f'window.setMicRMS({val if val else 0.0});')
            elif cmd == "state":
                window.evaluate_js(f'window.setState("{val}");')
            elif cmd == "speech":
                safe_text = val.replace('"', '\\"').replace('\n', '\\n')
                window.evaluate_js(f'window.addSpeechText("{safe_text}");')
            elif cmd == "interrupted":
                window.evaluate_js('window.triggerInterruption();')
            elif cmd == "search_results":
                window.evaluate_js(f'window.showSearchHUD("{val}");')
            elif cmd == "search_images":
                window.evaluate_js(f'window.showImagesHUD("{val}");')
            elif cmd == "terminal_command":
                window.evaluate_js(f'window.updateTerminalHUD("{val}");')
            elif cmd == "screen_capture":
                window.evaluate_js(f'window.showScreenCaptureHUD("{val}");')
            elif cmd == "screen_capture_complete":
                window.evaluate_js('window.screenAnalysisComplete();')
            elif cmd == "clear_huds":
                window.evaluate_js('window.clearSearchHUDs();')
        except Exception:
            pass


# UDP socket for sending text_input commands to the main pipeline
_text_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def update_toml_value(file_path, section, key, value):
    """
    Safely updates a key-value pair under a specific section in config.toml
    without modifying formatting, comments, or other sections.
    """
    if not os.path.exists(file_path):
        return False
    
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    new_lines = []
    current_section = None
    updated = False
    
    # Format value appropriately for TOML
    if isinstance(value, bool):
        val_str = "true" if value else "false"
    elif isinstance(value, (int, float)):
        val_str = str(value)
    else:
        val_str = f'"{value}"'
        
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            new_lines.append(line)
            continue
            
        if current_section == section and stripped.split("=")[0].strip() == key:
            comment = ""
            if "#" in line:
                comment = "  #" + line.split("#", 1)[1].strip()
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f"{indent}{key} = {val_str}{comment}\n")
            updated = True
        else:
            new_lines.append(line)
            
    if not updated:
        # If key wasn't found under target section, append it right before the next section
        new_lines = []
        current_section = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if current_section == section:
                    new_lines.append(f"{key} = {val_str}\n\n")
                    updated = True
                current_section = stripped[1:-1].strip()
            new_lines.append(line)
            
        if current_section == section and not updated:
            new_lines.append(f"{key} = {val_str}\n")
            updated = True
            
    # Atomic write
    temp_path = file_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.replace(temp_path, file_path)
    return True


class JSBridge:
    """JavaScript API bridge exposed to pywebview. JS calls window.pywebview.api.send_text(text)."""

    def send_text(self, text):
        """Send typed text from the GUI prompt box to the main AI pipeline."""
        if not text or not text.strip():
            return
        # Forward to main.py pipeline via a dedicated UDP message on port 10089
        try:
            _text_send_sock.sendto(f"text_input:{text}".encode("utf-8"), ("127.0.0.1", 10089))
        except Exception as e:
            print(f"[JSBridge] Error sending text: {e}")

    def get_tuning_config(self):
        """Loads and returns config.toml data for the weapon wheel UI."""
        import tomllib
        project_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(project_dir, 'config.toml')
        if os.path.exists(config_path):
            try:
                with open(config_path, "rb") as f:
                    return tomllib.load(f)
            except Exception as e:
                print(f"[JSBridge] Error reading config: {e}")
        return {}

    def save_tuning_config(self, section, key, value):
        """Updates a config parameter, saves config.toml atomically, and alerts main.py over UDP."""
        project_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(project_dir, 'config.toml')
        try:
            # Enforce correct type formatting for incoming JS variables
            if key in ("enabled", "visualizer_enabled"):
                value = bool(value)
            elif key in ("min_rms", "threshold", "feedback_ratio", "pitch_factor", "pitch_shift"):
                value = float(value)
            elif key in ("hold_frames", "width", "height"):
                value = int(value)

            success = update_toml_value(config_path, section, key, value)
            if success:
                # Alert main.py to dynamically reload configuration instantly!
                _text_send_sock.sendto(b"config_reload", ("127.0.0.1", 10089))
                return {"status": "SUCCESS"}
        except Exception as e:
            print(f"[JSBridge] Error saving config: {e}")
        return {"status": "ERROR"}




def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(project_dir, 'config.toml')
    html_path = os.path.join(project_dir, 'index.html')

    # Load defaults
    live2d_width = 500
    live2d_height = 800
    live2d_scale = 0.32
    live2d_y_offset = -120

    if os.path.exists(config_path):
        try:
            with open(config_path, "rb") as f:
                config_data = tomllib.load(f)
            live2d_cfg = config_data.get("live2d", {})
            live2d_width = live2d_cfg.get("width", live2d_width)
            live2d_height = live2d_cfg.get("height", live2d_height)
            live2d_scale = live2d_cfg.get("scale", live2d_scale)
            live2d_y_offset = live2d_cfg.get("y_offset", live2d_y_offset)
        except Exception:
            pass

    # Create JS API bridge for text input from the prompt box
    api = JSBridge()

    # Create the transparent viewport window
    window = webview.create_window(
        title='Live2D AI Companion',
        url=html_path,
        width=live2d_width,
        height=live2d_height,
        frameless=True,      # Discards window borders
        easy_drag=True,      # Enables dragging the face around
        transparent=True,    # Floating transparency
        background_color='#000000',  # Hex triplet!
        js_api=api,
    )

    def on_loaded():
        window.evaluate_js(f"window.updateLayout({live2d_scale}, {live2d_y_offset});")

    window.events.loaded += on_loaded

    # Launch UDP listener in a parallel daemon thread
    listener_thread = threading.Thread(target=listen_udp, args=(window,), daemon=True)
    listener_thread.start()


    # Start pywebview main GUI loop
    webview.start(debug=False)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

