import asyncio
import json
import websockets
import requests

async def main():
    # 1. Find debugging port
    port = None
    for p in [8228, 8229, 8230]:
        try:
            res = requests.get(f"http://127.0.0.1:{p}/json", timeout=1.0)
            targets = res.json()
            port = p
            break
        except Exception:
            pass

    if not port:
        print("Error: Could not connect to remote debugger on port 8228, 8229, or 8230.")
        return

    companion_target = None
    for t in targets:
        if "Live2D" in t.get("title", ""):
            companion_target = t
            break

    if not companion_target:
        print(f"Error: Could not find Live2D target on port {port}.")
        return

    ws_url = companion_target.get("webSocketDebuggerUrl")
    print(f"Connecting to: {ws_url}")

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Log.enable"}))
        await ws.send(json.dumps({"id": 3, "method": "Network.enable"}))
        await ws.send(json.dumps({"id": 4, "method": "Page.enable"}))

        print("Reloading page...")
        await ws.send(json.dumps({"id": 5, "method": "Page.reload"}))

        print("Listening for errors for 5 seconds...")
        
        # Listen for 5 seconds
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 5.0:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                data = json.loads(msg)
                
                method = data.get("method")
                params = data.get("params", {})
                
                if method == "Runtime.consoleAPICalled":
                    args = params.get("args", [])
                    log_type = params.get("type", "log").upper()
                    val = " ".join([str(arg.get("value", arg)) if isinstance(arg, dict) else str(arg) for arg in args])
                    print(f"[{log_type}]: {val}")
                elif method == "Runtime.exceptionThrown":
                    details = params.get("exceptionDetails", {})
                    text = details.get("text", "")
                    ex = details.get("exception", {})
                    desc = ex.get("description", "Unknown exception")
                    print(f"🔴 [JS EXCEPTION]: {text} - {desc}")
                elif method == "Log.entryAdded":
                    entry = params.get("entry", {})
                    lvl = entry.get("level", "log").upper()
                    txt = entry.get("text", "")
                    print(f"[LOG-{lvl}]: {txt}")
                elif method == "Network.responseReceived":
                    resp = params.get("response", {})
                    status = resp.get("status")
                    if status and status >= 400:
                        print(f"🔴 [NETWORK ERROR] Status {status}: {resp.get('url')}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"Error: {e}")
                break

if __name__ == "__main__":
    asyncio.run(main())
