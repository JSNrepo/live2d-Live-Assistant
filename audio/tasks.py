import asyncio
import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path
from PIL import Image
from google import genai
from google.genai import types

from config import log, TASK_MODEL, VISION_MODEL, SYSTEM_INSTRUCTION, EMOTION_TAG_MAP, AppState
from live2d import send_live2d_cmd, ui, ui_lock
from memory import memory_db
from audio.pipeline import safe_send_realtime_input, spk_q

async def wait_for_ai_speech_finish():
    """
    Helper that pauses background task result injection until Hiyori
    has fully finished speaking her current sentence, preventing self-interruptions.
    """
    while True:
        with ui_lock:
            is_active = (ui.state == AppState.SPEAKING or ui.model_responding)
        if not is_active and spk_q.empty():
            break
        await asyncio.sleep(0.1)

# Import all low-level tools used by background tasks
from tools.webbridge import (
    check_webbridge_active,
    webbridge_screenshot_async,
    capture_screenshot,
    get_webbridge_status,
    webbridge_navigate,
    webbridge_get_content,
    webbridge_click,
    webbridge_fill,
    webbridge_screenshot,
    webbridge_scroll,
    webbridge_key_press,
    webbridge_wait,
    webbridge_get_page_text,
    webbridge_evaluate_js,
    webbridge_hover,
    webbridge_go_back,
    webbridge_select_option,
)
from tools.web_search import search_web_contents
from tools.system import run_shell_command

# Initialize model clients
task_client = genai.Client(
    api_key=os.environ.get("TASK_API_KEY") or os.environ.get("GOOGLE_API_KEY")
)
vision_client = genai.Client(
    api_key=os.environ.get("VISION_API_KEY") or os.environ.get("GOOGLE_API_KEY")
)

_PROMPT_CACHES = {}
_PROMPT_CACHES_LOCK = asyncio.Lock()


async def get_or_create_prompt_cache(client, cache_key: str, model: str, system_instruction: str, tools=None) -> str:
    """
    Retrieves or generates an explicit prompt cache resource using client.caches.create.
    Caches expire after 1 hour (TTL: 3600 seconds) to stay optimized and clean.
    """
    async with _PROMPT_CACHES_LOCK:
        now = time.time()
        # If cache exists and has more than 5 minutes before expiration, reuse it
        if cache_key in _PROMPT_CACHES:
            cache_info = _PROMPT_CACHES[cache_key]
            if cache_info["expires_at"] > now + 300:
                log.debug("Reusing existing prompt cache for key: %s (expires in %ds)", cache_key, int(cache_info["expires_at"] - now))
                return cache_info["name"]

        log.info("Creating new explicit prompt cache for key: %s under model: %s", cache_key, model)

        # Build contents from system instruction
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=system_instruction)]
            )
        ]

        # We enforce a 1-hour TTL
        config = types.CreateCachedContentConfig(
            contents=contents,
            ttl="3600s",
        )
        if tools:
            config.tools = tools

        try:
            # We run in a thread with a tight timeout because caches.create is a blocking synchronous call in the google-genai SDK
            cache = await asyncio.wait_for(
                asyncio.to_thread(
                    client.caches.create,
                    model=model,
                    config=config
                ),
                timeout=8.0
            )
            _PROMPT_CACHES[cache_key] = {
                "name": cache.name,
                "expires_at": now + 3600
            }
            log.info("Successfully generated prompt cache: %s (expires_at: %s)", cache.name, datetime.datetime.fromtimestamp(now + 3600).isoformat())
            return cache.name
        except Exception as e:
            log.warning("Prompt caching not supported or limits exceeded (e.g. Free Tier key with limit=0 storage tokens). Falling back to standard non-cached requests. Details: %s", e)
            return None


async def execute_screen_analysis(query: str) -> dict:
    """
    Captures desktop screenshot, immediately forwards it to GUI HUD preview,
    performs Vision API analysis, and returns the structural result directly.
    """
    temp_img_path = Path(__file__).resolve().parent.parent / "logs" / "screenshot.png"
    temp_img_path.parent.mkdir(parents=True, exist_ok=True)

    # Smart fallback: Try Kimi WebBridge first if active to capture the active tab
    is_webbridge_active = await check_webbridge_active()
    success = False
    if is_webbridge_active:
        res = await webbridge_screenshot_async(session="kimi")
        if "filepath" in res:
            try:
                shutil.copy(res["filepath"], temp_img_path)
                success = True
            except Exception:
                pass

    if not success:
        success = await capture_screenshot(temp_img_path)

    if success:
        send_live2d_cmd("screen_capture:logs/screenshot.png")
    else:
        return {"error": "Failed to capture the screen."}

    try:
        # Optimize image size for lightning-fast network transfer and Vision API ingestion
        img = Image.open(temp_img_path)
        if img.width > 1280 or img.height > 720:
            img.thumbnail((1280, 720), Image.Resampling.LANCZOS)

        prompt = (
            f"The user wants you to look at a screenshot of their desktop. "
            f"Focus on their specific request: '{query}'. "
            f"Describe what you see on the screen and respond naturally in your character. "
            f"Keep your response concise (1-3 sentences)."
        )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.4
        )

        # High-reliability timeout safety guard to prevent any hangs
        response = await asyncio.wait_for(
            vision_client.aio.models.generate_content(
                model=VISION_MODEL,
                contents=[img, prompt],
                config=config
            ),
            timeout=18.0
        )
        send_live2d_cmd("screen_capture_complete")
        return {"analysis": response.text or "I couldn't make out anything on the screen."}
    except asyncio.TimeoutError:
        log.warning("Vision API request timed out after 18 seconds.")
        send_live2d_cmd("screen_capture_complete")
        return {"error": "Vision analysis timed out. Please try again."}
    except Exception as e:
        send_live2d_cmd("screen_capture_complete")
        return {"error": f"Screen analysis failed: {str(e)}"}


def get_hud_images(query: str):
    """
    Dynamically fetches highly relevant images from Wikipedia Commons / English Wikipedia pageimages
    matching the user query, with default curated stock fallbacks.
    """
    import requests
    import re
    
    # 1. Curated stock fallbacks
    stock_images = [
        {"url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=150&auto=format&fit=crop", "title": "Cyber Core Pipeline", "resolution": "3840x2160"},
        {"url": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=150&auto=format&fit=crop", "title": "Micro-processor IC", "resolution": "1920x1080"},
        {"url": "https://images.unsplash.com/photo-1601524909162-be87252be298?w=150&auto=format&fit=crop", "title": "Liquid Cooling Array", "resolution": "2560x1440"},
    ]
    
    # Clean conversational query from helper stopwords
    q = query.lower()
    stop_words = {"search", "for", "look", "up", "find", "info", "information", "on", "about", "the", "a", "an", "please", "show", "me", "web", "google"}
    words = [w for w in q.split() if w not in stop_words and len(w) > 1]
    cleaned_query = " ".join(words) if words else query
    
    # 2. Try Wikipedia PageImages search
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": cleaned_query,
            "gsrlimit": "8",
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": "500",
            "format": "json"
        }
        headers = {
            "User-Agent": "LivePythonGemini/1.0 (vinoth.live2d@gmail.com)"
        }
        res = requests.get(url, params=params, headers=headers, timeout=4)
        if res.status_code == 200:
            data = res.json()
            pages = data.get("query", {}).get("pages", {})
            dynamic_images = []
            resolutions = ["1920x1080", "2560x1440", "3840x2160", "4096x2160"]
            
            # Sort pages by index (search rank) to keep most relevant first
            sorted_pages = sorted(pages.values(), key=lambda p: p.get("index", 100))
            
            for page in sorted_pages:
                thumb = page.get("thumbnail", {})
                img_url = thumb.get("source")
                if img_url:
                    title = page.get("title", "").strip()
                    if len(title) > 22:
                        title = title[:20] + "..."
                    
                    res_val = resolutions[len(dynamic_images) % len(resolutions)]
                    dynamic_images.append({
                        "url": img_url,
                        "title": title,
                        "resolution": res_val
                    })
                    if len(dynamic_images) >= 4:
                        break
            if len(dynamic_images) >= 2:
                return dynamic_images
    except Exception as e:
        log.warning("Wikipedia PageImages search failed: %s", e)
        
    # 3. Secondary Fallback to Wikimedia Commons search if Wikipedia search had no thumbnails
    try:
        url = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": cleaned_query,
            "gsrlimit": "6",
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json"
        }
        headers = {
            "User-Agent": "LivePythonGemini/1.0 (vinoth.live2d@gmail.com)"
        }
        res = requests.get(url, params=params, headers=headers, timeout=4)
        if res.status_code == 200:
            data = res.json()
            pages = data.get("query", {}).get("pages", {})
            dynamic_images = []
            resolutions = ["1920x1080", "2560x1440", "3840x2160"]
            
            for page_id, page in pages.items():
                img_info = page.get("imageinfo", [])
                if img_info:
                    img_url = img_info[0].get("url")
                    if img_url and not img_url.lower().endswith(".svg"):
                        title = page.get("title", "").replace("File:", "")
                        title = re.sub(r'\.[a-zA-Z0-9]+$', '', title)
                        title = title.replace("_", " ").strip()
                        if len(title) > 22:
                            title = title[:20] + "..."
                        
                        res_val = resolutions[len(dynamic_images) % len(resolutions)]
                        dynamic_images.append({
                            "url": img_url,
                            "title": title,
                            "resolution": res_val
                        })
                        if len(dynamic_images) >= 4:
                            break
            if len(dynamic_images) >= 2:
                return dynamic_images
    except Exception as e:
        log.warning("Commons search failed: %s", e)
        
    return []


async def execute_web_search(query: str) -> dict:
    """
    Executes web search, opens visual database results in HUD columns,
    and returns search snippets directly for single-shot response.
    """
    try:
        res = await asyncio.to_thread(search_web_contents, query)
        results = res.get("results", [])

        if not results:
            return {"results": [], "message": "No search results found."}

        import base64
        # Determine display limit for HUD results: only 1 link for simple searches
        q_lower = query.lower()
        comprehensive_keywords = {"links", "sources", "websites", "list", "pages", "sites", "articles", "all"}
        words = set(re.findall(r'\b\w+\b', q_lower))
        has_comp_kw = not words.isdisjoint(comprehensive_keywords)
        
        limit = 4 if (has_comp_kw or len(words) > 5) else 1
        
        hud_results = []
        for r in results[:limit]:
            hud_results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", "")
            })
        
        try:
            b64_results = base64.b64encode(json.dumps(hud_results).encode('utf-8')).decode('utf-8')
            send_live2d_cmd(f"search_results:{b64_results}")
        except Exception as e:
            log.warning("Failed to encode search results to base64: %s", e)

        # Scrape and send dynamic image results (highly relevant Wikipedia Commons)
        images = get_hud_images(query)
        try:
            # Always encode and send images (even if empty, to trigger automatic HUD closure/hiding)
            b64_imgs = base64.b64encode(json.dumps(images).encode('utf-8')).decode('utf-8')
            send_live2d_cmd(f"search_images:{b64_imgs}")
        except Exception as e:
            log.warning("Failed to encode HUD images: %s", e)

        return {"results": results}
    except Exception as e:
        return {"error": str(e)}


async def execute_shell_command(command: str, require_confirmation: bool = False) -> dict:
    """
    Runs a manual shell command, updates monospace console feed, and
    returns output directly to Gemini Live session.
    """
    try:
        res = await asyncio.to_thread(run_shell_command, command, require_confirmation)
        res_status = res.get("status", "")

        import base64
        if res_status == "CONFIRMATION_REQUIRED":
            console_payload = {
                "command": command,
                "output": f"[CONFIRMATION REQUIRED]\n{res.get('message', '')}",
                "type": "warning"
            }
            try:
                b64_console = base64.b64encode(json.dumps(console_payload).encode('utf-8')).decode('utf-8')
                send_live2d_cmd(f"terminal_command:{b64_console}")
            except Exception as e:
                log.warning("Failed to encode log: %s", e)
            return res

        if res_status == "ERROR_PENDING_ACTION":
            console_payload = {
                "command": command,
                "output": f"[BLOCKED: PENDING ACTION]\n{res.get('message', '')}",
                "type": "error"
            }
            try:
                b64_console = base64.b64encode(json.dumps(console_payload).encode('utf-8')).decode('utf-8')
                send_live2d_cmd(f"terminal_command:{b64_console}")
            except Exception as e:
                log.warning("Failed to encode log: %s", e)
            return res

        stdout = res.get("stdout", "").strip()
        stderr = res.get("stderr", "").strip()
        returncode = res.get("returncode", 0)
        output = stdout or stderr or "(no output)"

        success = res.get("success", True)
        cmd_type = "success" if (success and returncode == 0) else "error"
        output_limit = output[:400] + "\n... (truncated)" if len(output) > 400 else output
        
        console_payload = {
            "command": command,
            "output": f"[Exit Code: {returncode}]\n{output_limit}",
            "type": cmd_type
        }
        try:
            b64_console = base64.b64encode(json.dumps(console_payload).encode('utf-8')).decode('utf-8')
            send_live2d_cmd(f"terminal_command:{b64_console}")
        except Exception as e:
            log.warning("Failed to encode shell execution logs: %s", e)

        return res
    except Exception as e:
        return {"error": str(e)}


async def do_background_graph_ingestion(user_text: str, ai_text: str):
    """
    Asynchronously extracts facts from the active turn and ingests them into memory_graph in background.
    """
    if not user_text.strip() and not ai_text.strip():
        return

    log.debug("Cold Path memory graph ingestion triggered for turn")

    graph_ingestion_system_instruction = (
        "You are a silent memory graph database ingestion worker for a desktop voice companion.\n"
        "Your task is to analyze the dialogue turn between the User and the AI, "
        "and extract any personal facts, preferences, relationships, or hobbies about the user.\n\n"
        "Extract these facts as simple semantic triples: (Source, Relation, Target).\n"
        "Guidelines:\n"
        "- Source should almost always be 'user' (unless it refers to user's pet, friend, cat, etc.).\n"
        "- Relation should be a simple short keyword (e.g. 'likes', 'lives_in', 'owns', 'hobby', 'name').\n"
        "- Target should be the value (e.g. 'cricket', 'tamil nadu', 'sakura').\n"
        "- Keep it clean, simple, and strictly accurate to what the user explicitly stated.\n"
        "- If no new facts or personal details are mentioned in this turn, return an empty list.\n\n"
        "Return the extracted relationships strictly in a JSON list format containing objects with 'source', 'relation', and 'target' keys. Do not include markdown code block formatting."
    )

    prompt = f"User said: '{user_text}'\nAI said: '{ai_text}'"

    def extract_sync():
        try:
            config = types.GenerateContentConfig(
                system_instruction=graph_ingestion_system_instruction,
                response_mime_type="application/json"
            )
            response = task_client.models.generate_content(
                model=TASK_MODEL,
                contents=prompt,
                config=config
            )
            if response.text:
                try:
                    txt = response.text.strip()
                    if txt.startswith("```"):
                        lines = txt.splitlines()
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines[-1].startswith("```"):
                            lines = lines[:-1]
                        txt = "\n".join(lines).strip()
                    triples = json.loads(txt)
                    if isinstance(triples, list):
                        for t in triples:
                            s = t.get("source")
                            r = t.get("relation")
                            tgt = t.get("target")
                            if s and r and tgt:
                                res = memory_db.add_relationship(s, r, tgt)
                                log.info("Cold Path remembered: %s", res)
                except Exception as je:
                    log.debug("Failed to parse extracted JSON: %s, text: %s", je, response.text)
        except Exception as e:
            log.error("Cold Path memory extraction failed: %s", e)

    await asyncio.to_thread(extract_sync)


async def run_browser_task(task_description: str, session=None) -> dict:
    log.info("Starting autonomous browser task: %s", task_description)

    # --- Check and Auto-Start Kimi WebBridge Daemon if offline ---
    wb_status = await asyncio.to_thread(get_webbridge_status)
    if wb_status["status"] not in ("active", "partial"):
        try:
            import subprocess
            daemon_path = Path.home() / ".kimi-webbridge/bin/kimi-webbridge"
            if daemon_path.exists():
                log.info("Auto-starting Kimi WebBridge daemon...")
                subprocess.Popen([str(daemon_path), "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                await asyncio.sleep(0.8)
                wb_status = await asyncio.to_thread(get_webbridge_status)
        except Exception as start_err:
            log.warning("Failed to auto-start Kimi WebBridge daemon: %s", start_err)

    # If the extension is still not connected (partial or offline), we log a warning
    # but ALLOW the task agent to proceed using the robust local browser fallbacks.
    if wb_status["status"] != "active":
        log.info("WebBridge status is '%s' (not active). Proceeding with robust local browser fallbacks.", wb_status["status"])

    async def agent_webbridge_navigate(url: str, new_tab: bool = False, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_navigate, url, new_tab, session_name)
        return json.dumps(res)

    async def agent_webbridge_get_content(session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_get_content, session_name)
        return json.dumps(res)

    async def agent_webbridge_click(selector: str, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_click, selector, session_name)
        return json.dumps(res)

    async def agent_webbridge_fill(selector: str, value: str, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_fill, selector, value, session_name)
        return json.dumps(res)

    async def agent_webbridge_screenshot(session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_screenshot, session_name)
        if isinstance(res, dict) and res.get("success"):
            send_live2d_cmd("screen_capture:logs/webbridge_screenshot.png")
        return json.dumps(res)

    async def agent_webbridge_scroll(direction: str = "down", amount: int = 400, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_scroll, direction, amount, session_name)
        return json.dumps(res)

    async def agent_webbridge_key_press(key: str, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_key_press, key, session_name)
        return json.dumps(res)

    async def agent_webbridge_wait(seconds: float = 2.0) -> str:
        secs = min(float(seconds), 10.0)
        await asyncio.sleep(secs)
        return json.dumps({"waited_seconds": secs})

    async def agent_webbridge_get_page_text(session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_get_page_text, session_name)
        return json.dumps(res)

    async def agent_webbridge_evaluate_js(code: str, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_evaluate_js, code, session_name)
        return json.dumps(res)

    async def agent_webbridge_hover(selector: str, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_hover, selector, session_name)
        return json.dumps(res)

    async def agent_webbridge_go_back(session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_go_back, session_name)
        return json.dumps(res)

    async def agent_webbridge_select_option(selector: str, value: str, session_name: str = "kimi") -> str:
        res = await asyncio.to_thread(webbridge_select_option, selector, value, session_name)
        return json.dumps(res)

    tools_map = {
        "agent_webbridge_navigate": agent_webbridge_navigate,
        "agent_webbridge_get_content": agent_webbridge_get_content,
        "agent_webbridge_click": agent_webbridge_click,
        "agent_webbridge_fill": agent_webbridge_fill,
        "agent_webbridge_screenshot": agent_webbridge_screenshot,
        "agent_webbridge_scroll": agent_webbridge_scroll,
        "agent_webbridge_key_press": agent_webbridge_key_press,
        "agent_webbridge_wait": agent_webbridge_wait,
        "agent_webbridge_get_page_text": agent_webbridge_get_page_text,
        "agent_webbridge_evaluate_js": agent_webbridge_evaluate_js,
        "agent_webbridge_hover": agent_webbridge_hover,
        "agent_webbridge_go_back": agent_webbridge_go_back,
        "agent_webbridge_select_option": agent_webbridge_select_option,
    }

    system_instruction = (
        "You are an elite autonomous web browsing AI with full browser control via Kimi WebBridge.\n"
        "Complete the user's task step-by-step with precision. Use all available tools.\n\n"
        "=== AVAILABLE TOOLS ===\n"
        "1. agent_webbridge_navigate(url, new_tab, session_name)\n"
        "   - Open any website. By default, it navigates the current tab (new_tab=False). Pass new_tab=True ONLY if you explicitly want to open another side tab alongside current ones.\n"
        "   - Use separate session names for parallel tasks (e.g. session_name='youtube', session_name='gmail', session_name='google').\n"
        "2. agent_webbridge_get_content(session_name)\n"
        "   - ALWAYS call after navigate/click/fill. Returns page title, URL, and element refs like '@e-12'.\n"
        "   - Element refs format: 'role [@e-N]: \"label\"' — use the @e-N ref for clicking/filling.\n"
        "3. agent_webbridge_click(selector, session_name)\n"
        "   - Click buttons, links, checkboxes using @e-N ref (most reliable) or CSS selector.\n"
        "4. agent_webbridge_fill(selector, value, session_name)\n"
        "   - Type into search boxes, inputs, textareas. Get ref from get_content first.\n"
        "5. agent_webbridge_key_press(key, session_name)\n"
        "   - Send keyboard events: 'Enter' (submit forms), 'Escape', 'Tab', 'ArrowDown', 'ArrowUp', 'Space', 'Backspace'.\n"
        "   - After fill, use key_press('Enter') instead of clicking Submit if no button is visible.\n"
        "6. agent_webbridge_scroll(direction, amount, session_name)\n"
        "   - Scroll 'down' or 'up' by pixel amount (default 400). Call then get_content to see newly loaded elements.\n"
        "7. agent_webbridge_wait(seconds)\n"
        "   - Wait for page to fully load (2-4s after navigation, 1-2s after click). Always use after navigate.\n"
        "8. agent_webbridge_get_page_text(session_name)\n"
        "   - Extract all visible text (8000 chars). Use to read articles, search results, emails, news.\n"
        "9. agent_webbridge_screenshot(session_name)\n"
        "   - Capture visual snapshot. Use to verify page state or debug unexpected results.\n"
        "10. agent_webbridge_hover(selector, session_name)\n"
        "    - Hover over element to reveal dropdowns, sub-menus, or tooltips.\n"
        "11. agent_webbridge_evaluate_js(code, session_name)\n"
        "    - Run custom JavaScript for advanced actions not covered by other tools.\n"
        "12. agent_webbridge_go_back(session_name)\n"
        "    - Navigate back in browser history.\n"
        "13. agent_webbridge_select_option(selector, value, session_name)\n"
        "    - Choose from a <select> dropdown by value or visible text.\n\n"
        "=== EXECUTION PROTOCOL ===\n"
        "- Task on already opened site/page: If the task is to interact with or automate an ALREADY OPENED page/tab, DO NOT start by navigating. Instead, start directly by calling 'agent_webbridge_get_content' or 'agent_webbridge_screenshot' to discover the elements of the already active page and perform the requested actions!\n"
        "- Task on newly opening site/page: If the task is on a newly opening site, navigate to the target URL with new_tab=True, wait(2) for page load, get_content to discover refs, then execute actions.\n"
        "- General Steps: click, fill, scroll, or key_press as needed, get_content to confirm, get_page_text to read content, and summarize clearly what you accomplished when done.\n\n"
        "=== RULES ===\n"
        "- REUSE tabs: By default, always navigate the current tab (new_tab=False) to avoid opening duplicate tabs.\n"
        "- ALWAYS read get_content after every navigate, click, or fill.\n"
        "- If an element ref is stale/not found, scroll and get_content again.\n"
        "- For search engines: fill the search box, key_press Enter, wait, get_content.\n"
        "- For YouTube/music: navigate to YouTube, search, click video from content tree.\n"
        "- For reading content: navigate, wait, get_page_text to extract article text.\n"
        "- For forms: fill all fields, then click submit or key_press Enter.\n"
    )

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=task_description)])]

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=list(tools_map.values()),
        temperature=0.2,
    )

    max_steps = 15
    BROWSER_TASK_TIMEOUT = 180
    final_prose_response = ""

    async def send_progress(msg):
        if session:
            log.debug("Streaming browser task progress: %s", msg)
            emo_tags = re.findall(r'(?i)\[([a-z]+)\]', msg)
            if emo_tags:
                emo_tag = emo_tags[0].lower()
                mapped_emo = EMOTION_TAG_MAP.get(emo_tag, "speaking")
                send_live2d_cmd(f"emotion:{mapped_emo}")

            # Late import to prevent circular dependency
            from audio.pipeline import safe_create_task
            # Send progress text asynchronously to live session to make actions lightning-fast
            safe_create_task(safe_send_realtime_input(session, text=msg))

    contents_list = list(contents)
    s = 0
    await send_progress(f"[SMUG] Understood! Let's do this. I'm launching browser automation in the background for your request: '{task_description}'. Hold on!")

    task_deadline = time.monotonic() + BROWSER_TASK_TIMEOUT
    while s < max_steps:
        s += 1
        log.debug("Task agent step %d", s)
        if time.monotonic() > task_deadline:
            log.warning("run_browser_task: hit %ds hard timeout at step %d", BROWSER_TASK_TIMEOUT, s)
            if session:
                await wait_for_ai_speech_finish()
                await safe_send_realtime_input(session, text="[SYSTEM: Browser task timed out after 3 minutes. Aborting.]")
            break
        try:
            response = await asyncio.wait_for(
                task_client.aio.models.generate_content(
                    model=TASK_MODEL, contents=contents_list, config=config
                ),
                timeout=45.0
            )
        except asyncio.TimeoutError:
            log.error("Task client generate_content timed out after 45 seconds.")
            return {"success": False, "error": "Task client request timed out."}
        except Exception as e:
            log.error("Task client error: %s", e)
            return {"success": False, "error": f"Task client error: {str(e)}"}

        if response.candidates and response.candidates[0].content:
            contents_list.append(response.candidates[0].content)

        if response.text:
            final_prose_response = response.text

        function_calls = response.function_calls
        if not function_calls:
            break

        tool_parts = []
        for call in function_calls:
            tool_func = tools_map.get(call.name)

            # Stream tool call progress
            if call.name == "agent_webbridge_navigate":
                u = call.args.get("url", "")
                await send_progress(f"[HAPPY] Navigating browser to: {u}")
            elif call.name == "agent_webbridge_click":
                sel = call.args.get("selector", "")
                await send_progress(f"[TEASING] Clicking element '{sel}'.")
            elif call.name == "agent_webbridge_fill":
                v = call.args.get("value", "")
                await send_progress(f"[SMUG] Typing '{v}' into the field.")
            elif call.name == "agent_webbridge_screenshot":
                await send_progress("[SMUG] Taking screenshot to verify page state.")
            elif call.name == "agent_webbridge_scroll":
                d = call.args.get("direction", "down")
                amt = call.args.get("amount", 400)
                await send_progress(f"[NEUTRAL] Scrolling {d} {amt}px to reveal content.")
            elif call.name == "agent_webbridge_key_press":
                k = call.args.get("key", "")
                await send_progress(f"[NEUTRAL] Pressing '{k}' key.")
            elif call.name == "agent_webbridge_wait":
                sec = call.args.get("seconds", 2.0)
                await send_progress(f"[BORED] Waiting {sec}s for page to load...")
            elif call.name == "agent_webbridge_get_page_text":
                await send_progress("[SMUG] Extracting full page text content.")
            elif call.name == "agent_webbridge_evaluate_js":
                await send_progress("[SMUG] Running custom JavaScript in browser.")
            elif call.name == "agent_webbridge_hover":
                sel = call.args.get("selector", "")
                await send_progress(f"[NEUTRAL] Hovering over element '{sel}'.")
            elif call.name == "agent_webbridge_go_back":
                await send_progress("[NEUTRAL] Going back to previous page.")
            elif call.name == "agent_webbridge_select_option":
                opt = call.args.get("value", "")
                await send_progress(f"[NEUTRAL] Selecting dropdown option '{opt}'.")
            elif call.name == "agent_webbridge_get_content":
                await send_progress("[NEUTRAL] Reading page layout and elements.")

            if not tool_func:
                res_str = json.dumps({"error": f"Tool '{call.name}' not found."})
            else:
                try:
                    res_str = await tool_func(**call.args)
                except Exception as e:
                    res_str = json.dumps({"error": f"Execution failed: {str(e)}"})

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
        contents_list.append(types.Content(role="tool", parts=tool_parts))

    if final_prose_response:
        await send_progress(f"[SMUG] Task complete! Here is what I did:\n{final_prose_response}")

    return {"success": True, "result": final_prose_response or "Task execution complete."}
