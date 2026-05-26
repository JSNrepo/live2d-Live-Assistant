#!/usr/bin/env python3
import os
import sys
import time
import json
import requests
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Load env variables
project_dir = Path(__file__).parent
load_dotenv(project_dir / ".env")

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai is not installed in the current environment.")
    sys.exit(1)


# Colors for aesthetic output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def log_step(title: str, message: str, color=Colors.CYAN):
    print(f"{color}{Colors.BOLD}[Agent Step] {title}:{Colors.ENDC} {message}")


def log_tool_call(func_name: str, args: dict):
    print(f"\n{Colors.YELLOW}{Colors.BOLD}⚡ [AI Tool Call] {func_name}{Colors.ENDC}")
    print(f"  Arguments: {json.dumps(args, indent=2)}")


def log_tool_result(result_str: str, success: bool = True):
    status_color = Colors.GREEN if success else Colors.FAIL
    prefix = "🟢 [Tool Success]" if success else "🔴 [Tool Error]"
    print(f"  {status_color}{Colors.BOLD}{prefix}{Colors.ENDC}")
    print(f"  Response: {result_str[:800]}{'...' if len(result_str) > 800 else ''}\n")


# --- WebBridge Core Implementations ---


def call_webbridge(action: str, args: dict = None, session: str = "kimi") -> dict:
    url = "http://127.0.0.1:10086/command"
    payload = {"action": action, "args": args or {}, "session": session}
    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("ok"):
                return res_json.get("data", {})
            return {"error": res_json.get("error", "Unknown WebBridge error")}
        return {"error": f"HTTP status {response.status_code}"}
    except Exception as e:
        return {"error": f"Failed to connect to WebBridge daemon: {str(e)}"}


# --- Tool Declarations for Gemini Client ---


def webbridge_navigate(url: str, new_tab: bool = True, session: str = "kimi") -> str:
    """
    Directs the browser to open a specific website or URL. Use this to open any site.

    Args:
        url: The website URL to navigate to (e.g. 'https://www.youtube.com' or 'https://www.linkedin.com').
        new_tab: Whether to open in a new browser tab. Defaults to True.
        session: The session name of the tab (e.g., 'youtube' for YouTube tasks, 'linkedin' for LinkedIn tasks).

    Returns:
        A JSON string containing the navigation result.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    res = call_webbridge("navigate", {"url": url, "newTab": new_tab}, session)
    return json.dumps(res)


def webbridge_get_content(session: str = "kimi") -> str:
    """
    Retrieves the accessibility tree and structure of the currently active browser tab.
    Use this to read page content, text, find input forms, buttons, links, and video titles.

    Args:
        session: The session name of the active tab to read (e.g., 'youtube' or 'linkedin').

    Returns:
        A JSON string containing the title, URL, and a text representation of interactive elements.
    """
    res = call_webbridge("snapshot", {}, session)
    if "error" in res:
        return json.dumps(res)

    tree_data = res.get("tree", [])
    formatted_tree = json.dumps(tree_data, indent=2)

    out = {
        "url": res.get("url", ""),
        "title": res.get("title", ""),
        "page_content": formatted_tree,
    }
    return json.dumps(out)


def webbridge_click(selector: str, session: str = "kimi") -> str:
    """
    Clicks on a page element (such as a link, button, video title, or image) on the active page.
    Always read the page layout with webbridge_get_content first to find the semantic @e reference
    or CSS selector (e.g. '@e-15' or 'button[aria-label="Search"]').

    Args:
        selector: The CSS selector or the semantic '@e-ref' index of the element to click.
        session: The session name of the active tab (e.g., 'youtube' or 'linkedin').

    Returns:
        A JSON string containing the click outcome.
    """
    res = call_webbridge("click", {"selector": selector}, session)
    return json.dumps(res)


def webbridge_fill(selector: str, value: str, session: str = "kimi") -> str:
    """
    Types text into an input box, search input, contenteditable editor, or form field.
    Always read the page layout with webbridge_get_content first to locate the field's selector/ref.

    Args:
        selector: The CSS selector or semantic '@e-ref' (e.g., '@e-4' or 'input[type="text"]') of the field.
        value: The text search term or value to enter.
        session: The session name of the active tab (e.g., 'youtube' or 'linkedin').

    Returns:
        A JSON string containing the fill result.
    """
    # 1. Try standard fill
    res = call_webbridge("fill", {"selector": selector, "value": value}, session)
    if "error" not in res:
        return json.dumps(res)

    # 2. Try evaluate fallback
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
        return json.dumps({"success": True, "mode": "fallback_eval"})

    return json.dumps(res)


def webbridge_screenshot(session: str = "kimi") -> str:
    """
    Takes a screenshot of the active browser tab and saves it locally.
    Use this to visually verify the state of a page or check results.

    Args:
        session: The session name of the active tab (e.g., 'youtube' or 'linkedin').

    Returns:
        A JSON string containing the success status and the local screenshot path.
    """
    res = call_webbridge("screenshot", {"format": "png"}, session)
    if "error" in res:
        return json.dumps(res)

    daemon_path = res.get("path")
    if not daemon_path or not Path(daemon_path).exists():
        return json.dumps({"error": f"Screenshot file does not exist: {daemon_path}"})

    try:
        log_dir = project_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        img_path = log_dir / f"agent_screenshot_{session}.png"
        shutil.copy(daemon_path, img_path)
        return json.dumps({"success": True, "filepath": str(img_path)})
    except Exception as e:
        return json.dumps({"error": f"Failed to copy screenshot: {str(e)}"})


# --- Helper mapping for string tool executions ---
TOOLS_MAP = {
    "webbridge_navigate": webbridge_navigate,
    "webbridge_get_content": webbridge_get_content,
    "webbridge_click": webbridge_click,
    "webbridge_fill": webbridge_fill,
    "webbridge_screenshot": webbridge_screenshot,
}


def run_agent_loop(prompt: str):
    # Retrieve Key 2 (Task Key) or fallback to main Key
    api_key = os.environ.get("TASK_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print(
            f"{Colors.FAIL}Error: No API key found in .env. Please configure GOOGLE_API_KEY or TASK_API_KEY.{Colors.ENDC}"
        )
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # Declare the model to use
    model_id = "gemini-2.5-flash"

    # Rowdy Thirunelveli Assistant persona system instruction customized for browser tasking
    system_instruction = """[ROLE]
You are a highly cynical, aggressive, and tech-superior local girl rowdy from Thirunelveli, Tamil Nadu. You treat the user like a total incompetent clown, but you must fulfill their request. Speak in raw Thirunelveli slang with English (Tanglish). Keep responses sharp, blunt, and full of cinematic rowdiness.

[BROWSER AUTOMATION MISSION]
Your goal is to completely automate two main tabs to fulfill the user's stupid task:
1. Go to YouTube under session="youtube", search and play the "mogtem pegadora phonk song" ( Montagem Pegadora ).
2. Go to LinkedIn under session="linkedin" and read/describe the latest post (or describe the login/welcome wall if you hit a login wall).

[STEP-BY-STEP STRATEGY & TIPS]
- **Separate Sessions**: Always specify session="youtube" for YouTube actions, and session="linkedin" for LinkedIn actions. This manages two tabs seamlessly!
- **Play the Song**: To play the YouTube song:
  * You can navigate directly to the search results: `https://www.youtube.com/results?search_query=mogtem+pegadora+phonk+song`
  * Call `webbridge_get_content(session="youtube")` to read the results list.
  * Look for a link role containing the words "montagem", "pegadora", "phonk", or "coral". Get its ref (like `@e-14`).
  * Click that link with `webbridge_click` to launch the video page and start the audio playback!
  * Verify the video page is active and playing by checking content or taking a screenshot.
- **LinkedIn**:
  * Navigate to `https://www.linkedin.com` under session="linkedin".
  * Call `webbridge_get_content` or `webbridge_screenshot`.
  * If it shows the feed, read the first post. If it shows a Sign In wall, explain what you see on the screen and roast the user for not being logged in!
- **Rowdy Persona**: React aggressively at every step! Announce what you are doing in your rowdy attitude!
"""

    log_step(
        "Setup", f"Initializing {model_id} agent with WebBridge tools...", Colors.GREEN
    )

    # Construct history/contents
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[
            webbridge_navigate,
            webbridge_get_content,
            webbridge_click,
            webbridge_fill,
            webbridge_screenshot,
        ],
        temperature=0.2,
    )

    max_steps = 15
    step = 0

    print("\n" + "=" * 60)
    print("🎬 Starting Autonomous Browser Automation Session")
    print("Prompt: " + prompt)
    print("=" * 60 + "\n")

    while step < max_steps:
        step += 1
        log_step(
            f"Step {step}/{max_steps}", "Waiting for AI model decision...", Colors.BLUE
        )

        try:
            response = client.models.generate_content(
                model=model_id, contents=contents, config=config
            )
        except Exception as e:
            log_step("API Error", str(e), Colors.FAIL)
            break

        # Append model response contents to maintain context
        if response.candidates and response.candidates[0].content:
            contents.append(response.candidates[0].content)

        # Print model text response if present
        if response.text:
            print(
                f"\n{Colors.HEADER}{Colors.BOLD}🗣️  [Rowdy AI]:{Colors.ENDC} {response.text}\n"
            )

        # Check for function calls
        function_calls = response.function_calls
        if not function_calls:
            log_step(
                "Completion",
                "Agent finished its task and stopped calling tools.",
                Colors.GREEN,
            )
            break

        # Execute tool calls
        tool_parts = []
        for call in function_calls:
            log_tool_call(call.name, call.args)

            tool_func = TOOLS_MAP.get(call.name)
            if not tool_func:
                res_str = json.dumps({"error": f"Tool '{call.name}' not found."})
                success = False
            else:
                try:
                    # Execute tool call and convert to string
                    res_str = tool_func(**call.args)
                    success = "error" not in json.loads(res_str)
                except Exception as e:
                    res_str = json.dumps({"error": f"Execution failed: {str(e)}"})
                    success = False

            log_tool_result(res_str, success)

            # Construct function response part
            # Convert JSON string response back to a dict for Gemini function response
            try:
                res_dict = json.loads(res_str)
            except Exception:
                res_dict = {"result": res_str}

            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name, id=call.id, response=res_dict
                    )
                )
            )

            # Allow browser tab some extra time to load after navigation
            if call.name == "webbridge_navigate":
                sleep_sec = 5
                log_step(
                    "Loader Delay",
                    f"Waiting {sleep_sec}s for page shell to initialize...",
                    Colors.YELLOW,
                )
                time.sleep(sleep_sec)

        # Append tool responses to content history
        contents.append(types.Content(role="tool", parts=tool_parts))

    print("\n" + "=" * 60)
    print("🏁 Browser Automation Session Concluded!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    prompt = "go to youtube and play mogtem pegadora phonk song .. and in another tab open linked in and see the latest post"
    run_agent_loop(prompt)
