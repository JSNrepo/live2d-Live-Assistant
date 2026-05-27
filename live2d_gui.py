#!/usr/bin/env python3
import os
# Force XWayland (X11 compatibility layer) — works correctly on both
# pure X11 and Wayland+XWayland sessions with pywebview.
os.environ["GDK_BACKEND"] = "x11"
os.environ["QT_QPA_PLATFORM"] = "xcb"
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
            os.environ["QT_QPA_PLATFORM"] = "xcb"  # Always use XCB via XWayland
            return "qt"
    except Exception:
        pass

    # Try GTK
    try:
        import importlib
        if importlib.util.find_spec("gi") is not None:
            os.environ["PYWEBVIEW_GUI"] = "gtk"
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
            data, _ = sock.recvfrom(1024)
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
            elif cmd == "state":
                window.evaluate_js(f'window.setState("{val}");')
            elif cmd == "speech":
                safe_text = val.replace('"', '\\"').replace('\n', '\\n')
                window.evaluate_js(f'window.addSpeechText("{safe_text}");')
            elif cmd == "interrupted":
                window.evaluate_js('window.triggerInterruption();')
        except Exception:
            pass


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

    # Create the transparent viewport window
    window = webview.create_window(
        title='Live2D AI Companion',
        url=html_path,
        width=live2d_width,
        height=live2d_height,
        frameless=True,      # Discards window borders
        easy_drag=True,      # Enables dragging the face around
        transparent=True,    # Floating transparency
        background_color='#000000'  # Hex triplet!
    )

    def on_loaded():
        window.evaluate_js(f"window.updateLayout({live2d_scale}, {live2d_y_offset});")

    window.events.loaded += on_loaded

    # Launch UDP listener in a parallel daemon thread
    listener_thread = threading.Thread(target=listen_udp, args=(window,), daemon=True)
    listener_thread.start()

    # Start pywebview main GUI loop
    webview.start(debug=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
