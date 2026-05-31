import asyncio
import json
import shutil
import time
from pathlib import Path
import requests

from config import log, PROJECT_ROOT


def check_webbridge_active_sync() -> bool:
    """Synchronously check if Kimi WebBridge is running and active."""
    try:
        resp = requests.get("http://127.0.0.1:10086/status", timeout=2)
        if resp.status_code == 200:
            js = resp.json()
            return js.get("running") and js.get("extension_connected")
    except Exception:
        pass
    return False


async def check_webbridge_active() -> bool:
    """Asynchronously check if Kimi WebBridge is running and active."""
    return await asyncio.to_thread(check_webbridge_active_sync)


def call_webbridge(action: str, args: dict = None, session: str = "kimi") -> dict:
    """Helper to communicate with the local Kimi WebBridge daemon."""
    url = "http://127.0.0.1:10086/command"
    payload = {"action": action, "args": args or {}, "session": session}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("ok"):
                return res_json.get("data", {})

            # Robust Stale Tab ID Recovery
            err_data = res_json.get("error", "")
            err_msg = ""
            if isinstance(err_data, dict):
                err_msg = err_data.get("message", "")
            else:
                err_msg = str(err_data)

            if "No tab with given id" in err_msg:
                log.warning("Stale tab ID detected for session '%s'. Recovering session...", session)
                # Clean the session reference in daemon first
                try:
                    requests.post(url, json={"action": "close_session", "args": {}, "session": session}, timeout=5)
                except Exception:
                    pass

                # If navigating, we can recover automatically by forcing a new tab!
                if action == "navigate":
                    log.info("Retrying navigation in a new tab for session '%s'...", session)
                    payload["args"]["newTab"] = True
                    response = requests.post(url, json=payload, timeout=15)
                    if response.status_code == 200:
                        res_json = response.json()
                        if res_json.get("ok"):
                            return res_json.get("data", {})

            return {"error": res_json.get("error", "Unknown WebBridge error")}
        return {"error": f"HTTP status {response.status_code}"}
    except Exception as e:
        return {"error": f"Failed to connect to WebBridge daemon: {str(e)}"}


def webbridge_navigate(url: str, new_tab: bool = False, session: str = "kimi") -> dict:
    """Directs the browser to a specific URL."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return call_webbridge("navigate", {"url": url, "newTab": new_tab}, session)


def webbridge_get_content(session: str = "kimi") -> dict:
    """Retrieves a clean, compressed representation of interactive elements on the page."""
    res = call_webbridge("snapshot", {}, session)
    if "error" in res:
        return res

    tree_data = res.get("tree", [])

    # Compress the massive tree into a compact markdown bullet list
    interactive_nodes = []

    def traverse(node):
        if not isinstance(node, dict):
            return

        role = node.get("role", "").lower()
        name = node.get("name", "").strip()
        ref = node.get("ref", "")

        # If name is empty, try to extract name from children recursively
        if not name:
            child_texts = []

            def get_child_text(n):
                if not isinstance(n, dict):
                    return
                c_name = n.get("name", "").strip()
                # Exclude duplicate text of interactive children to keep it clean
                if c_name and not n.get("ref"):
                    child_texts.append(c_name)
                for child in n.get("children", []):
                    get_child_text(child)

            get_child_text(node)
            if child_texts:
                name = " ".join(child_texts).strip()

        # Capture relevant, semantic, or interactive elements
        is_interactive = bool(ref)
        is_heading = "heading" in role
        is_textbox = (
            "text" in role
            or "input" in role
            or role in ("textarea", "searchbox", "combobox")
        )

        if name and (
            is_interactive
            or is_heading
            or is_textbox
            or role in ("link", "button", "checkbox")
        ):
            interactive_nodes.append(
                {"role": node.get("role"), "name": name, "ref": ref}
            )
            # Skip traversing children to avoid duplicating children text
            return

        for child in node.get("children", []):
            traverse(child)

    if isinstance(tree_data, list):
        for root_node in tree_data:
            traverse(root_node)
    elif isinstance(tree_data, dict):
        traverse(tree_data)

    lines = []
    for n in interactive_nodes:
        ref_part = f" [{n['ref']}]" if n["ref"] else ""
        lines.append(f"- {n['role']}{ref_part}: \"{n['name']}\"")

    formatted_tree = (
        "\n".join(lines) if lines else "[No interactive elements found on this page]"
    )

    return {
        "url": res.get("url", ""),
        "title": res.get("title", ""),
        "page_content": formatted_tree,
    }


def webbridge_click(selector: str, session: str = "kimi") -> dict:
    """Clicks on a button, link, video title, or input field on the page."""
    return call_webbridge("click", {"selector": selector}, session)


def webbridge_fill(selector: str, value: str, session: str = "kimi") -> dict:
    """Types text into an input box, search input, contenteditable, or text area."""
    # 1. Try standard fill first
    res = call_webbridge("fill", {"selector": selector, "value": value}, session)
    if "error" not in res:
        return res

    # 2. Fallback: Use evaluate to set value if standard fill fails
    js_selector = json.dumps(selector)
    js_value = json.dumps(value)

    code = f"""(() => {{
        let el = null;
        if ({js_selector}.startsWith("@e")) {{
            el = document.querySelector(`[ref="${js_selector}"]`) ||
                 document.querySelector(`[data-ref="${js_selector}"]`);
        }}
        if (!el) {{
            try {{
                el = document.querySelector({js_selector});
            }} catch(e) {{}}
        }}
        if (!el) {{
            el = document.querySelector(`input[placeholder*=${js_selector}]`) ||
                 document.querySelector(`textarea[placeholder*=${js_selector}]`);
        }}
        if (el) {{
            el.focus();
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
                el.value = {js_value};
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }} else if (el.isContentEditable) {{
                el.innerText = {js_value};
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
            return {{ "success": true, "fallback_used": true }};
        }}
        return {{ "error": "Element not found or not fillable via fallback" }};
    }})()"""

    fallback_res = call_webbridge("evaluate", {"code": code}, session)
    if "error" not in fallback_res and fallback_res.get("value", {}).get("success"):
        return {"success": True, "mode": "fallback_eval"}

    return res


def webbridge_screenshot(session: str = "kimi") -> dict:
    """Takes a screenshot of the active browser page and saves it locally."""
    res = call_webbridge("screenshot", {"format": "png"}, session)
    if "error" in res:
        return res

    daemon_path = res.get("path")
    if not daemon_path or not Path(daemon_path).exists():
        return {
            "error": f"Screenshot path not found in response or file doesn't exist: {res}"
        }

    try:
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        img_path = log_dir / "webbridge_screenshot.png"

        # Copy from daemon's temp path to our logs directory
        shutil.copy(daemon_path, img_path)
        return {"success": True, "filepath": str(img_path)}
    except Exception as e:
        return {"error": f"Failed to save screenshot copy: {str(e)}"}


def webbridge_scroll(direction: str = "down", amount: int = 400, session: str = "kimi") -> dict:
    """Scrolls the page up or down by a pixel amount."""
    dy = amount if direction == "down" else -amount
    code = f"window.scrollBy(0, {dy}); return {{ 'scrolled': {dy} }};"
    return call_webbridge("evaluate", {"code": f"(()=>{{ {code} }})()"}, session)


def webbridge_key_press(key: str, session: str = "kimi") -> dict:
    """Sends a keyboard key press to the active page."""
    code = f"""
    (() => {{
        const key = {json.dumps(key)};
        const el = document.activeElement || document.body;
        el.dispatchEvent(new KeyboardEvent('keydown', {{ key, bubbles: true, cancelable: true }}));
        el.dispatchEvent(new KeyboardEvent('keypress', {{ key, bubbles: true, cancelable: true }}));
        el.dispatchEvent(new KeyboardEvent('keyup', {{ key, bubbles: true, cancelable: true }}));
        return {{ 'key_sent': key }};
    }})()
    """
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_wait(seconds: float = 2.0) -> dict:
    """Waits for the specified number of seconds."""
    secs = min(float(seconds), 10.0)
    time.sleep(secs)
    return {"waited_seconds": secs}


def webbridge_evaluate_js(code: str, session: str = "kimi") -> dict:
    """Executes raw JavaScript in the active browser tab and returns the result."""
    # S02: Sandbox / sanitize evaluate_js code to block malicious token/cookie/storage theft
    code_lower = code.lower()
    blocked_keywords = ["cookie", "localstorage", "sessionstorage", "indexeddb", "fetch", "xmlhttprequest", "websocket", "eval", "function("]
    for kw in blocked_keywords:
        if kw in code_lower:
            return {"error": f"Security Exception: Use of blocked JavaScript keyword/feature '{kw}' is prohibited."}
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_get_page_text(session: str = "kimi") -> dict:
    """Extracts the full visible text content of the active page."""
    code = "(() => { return { text: document.body ? document.body.innerText.substring(0, 8000) : '' }; })()"
    res = call_webbridge("evaluate", {"code": code}, session)
    if "error" in res:
        return res
    text = res.get("value", {}).get("text", "") if isinstance(res.get("value"), dict) else res.get("text", "")
    return {"page_text": text, "length": len(text)}


def webbridge_hover(selector: str, session: str = "kimi") -> dict:
    """Hovers the mouse pointer over an element."""
    js_sel = json.dumps(selector)
    code = f"""
    (() => {{
        let el = document.querySelector({js_sel});
        if (!el) return {{ error: 'Element not found for hover: ' + {js_sel} }};
        el.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
        el.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: true }}));
        return {{ hovered: {js_sel} }};
    }})()
    """
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_go_back(session: str = "kimi") -> dict:
    """Navigates the browser back to the previous page in history."""
    code = "(() => { history.back(); return { action: 'back' }; })()"
    return call_webbridge("evaluate", {"code": code}, session)


def webbridge_select_option(selector: str, value: str, session: str = "kimi") -> dict:
    """Selects an option from a <select> dropdown by value or label text."""
    js_sel = json.dumps(selector)
    js_val = json.dumps(value)
    code = f"""
    (() => {{
        let el = document.querySelector({js_sel});
        if (!el) return {{ error: 'Select element not found: ' + {js_sel} }};
        for (let opt of el.options) {{
            if (opt.value === {js_val} || opt.text === {js_val}) {{
                el.value = opt.value;
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{ selected: opt.value, text: opt.text }};
            }}
        }}
        return {{ error: 'No matching option for: ' + {js_val} }};
    }})()
    """
    return call_webbridge("evaluate", {"code": code}, session)


async def webbridge_screenshot_async(session: str = "kimi") -> dict:
    """Asynchronously capture browser screenshot."""
    return await asyncio.to_thread(webbridge_screenshot, session)


async def capture_screenshot(filepath: Path) -> bool:
    # Ensure any old file is removed first
    if filepath.exists():
        try:
            filepath.unlink()
        except Exception:
            pass

    # List of screenshot tools and their exact command/args
    commands = [
        # 1. KDE Spectacle (Wayland/X11)
        ["spectacle", "-b", "-n", "-o", str(filepath)],
        # 2. GNOME Screenshot
        ["gnome-screenshot", "-f", str(filepath)],
        # 3. Grim (wlroots Wayland)
        ["grim", str(filepath)],
        # 4. Scrot (X11)
        ["scrot", "-z", str(filepath)],
        # 5. Maim (X11)
        ["maim", "-u", str(filepath)],
    ]

    for cmd in commands:
        try:
            if not shutil.which(cmd[0]):
                continue

            # Run the command asynchronously
            proc = await asyncio.create_subprocess_exec(
                cmd[0],
                *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            # Check if screenshot was created and is non-empty
            if (
                proc.returncode == 0
                and filepath.exists()
                and filepath.stat().st_size > 0
            ):
                return True
        except Exception:
            pass

    return False


def get_webbridge_status() -> dict:
    """Returns a detailed status report of the Kimi WebBridge daemon and extension connection."""
    try:
        resp = requests.get("http://127.0.0.1:10086/status", timeout=2)
        if resp.status_code == 200:
            js = resp.json()
            running = js.get("running", False)
            connected = js.get("extension_connected", False)
            if running and connected:
                return {"status": "active", "running": True, "extension_connected": True, "message": "Kimi WebBridge is fully active and ready for browser automation."}
            elif running:
                return {"status": "partial", "running": True, "extension_connected": False, "message": "Kimi WebBridge daemon is running but the browser extension is NOT connected. Please open Kimi browser and enable the WebBridge extension."}
            else:
                return {"status": "inactive", "running": False, "extension_connected": False, "message": "Kimi WebBridge daemon is NOT running on port 10086."}
        return {"status": "error", "running": False, "extension_connected": False, "message": f"Unexpected HTTP status: {resp.status_code}"}
    except Exception as e:
        return {"status": "offline", "running": False, "extension_connected": False, "message": f"Cannot reach WebBridge at 127.0.0.1:10086. Daemon is not started. Error: {str(e)}"}
