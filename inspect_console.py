#!/usr/bin/env python3
import asyncio
import json
import websockets
import requests

async def main():
    # 1. Fetch active targets from debugging port
    targets = None
    for port in [8228, 8229, 8230]:
        try:
            res = requests.get(f"http://127.0.0.1:{port}/json", timeout=1.0)
            targets = res.json()
            print(f"Connected to remote debugger on port {port}.")
            break
        except Exception:
            pass

    if not targets:
        print("Error: Could not connect to remote debugger on port 8228, 8229, or 8230.")
        return

    companion_target = None
    for t in targets:
        if "Live2D Companion" in t.get("title", "") or "Live2D AI Companion" in t.get("title", ""):
            companion_target = t
            break

    if not companion_target:
        print("Error: Could not find 'Live2D Companion' inspectable page target.")
        print(f"Active targets found: {[t.get('title') for t in targets]}")
        return

    ws_url = companion_target.get("webSocketDebuggerUrl")
    print(f"Connecting to remote debugger WebSocket: {ws_url}")

    async with websockets.connect(ws_url) as ws:
        # 2. Enable Log, Console, and Runtime domains to capture messages
        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        await ws.send(json.dumps({"id": 2, "method": "Log.enable"}))
        await ws.send(json.dumps({"id": 3, "method": "Network.enable"}))
        
        # 3. Evaluate basic variables to check state
        eval_cmd = {
            "id": 10,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "JSON.stringify({ live2dModel: !!window.live2dModel, currentState, currentEmotion, currentMouth })",
                "returnByValue": True
            }
        }
        await ws.send(json.dumps(eval_cmd))

        print("\n--- Listening for JavaScript Console Logs & Exceptions ---")
        
        # 4. Listen for incoming logs, errors, and responses
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                
                # Check for evaluation result
                if data.get("id") == 10:
                    result = data.get("result", {}).get("result", {}).get("value")
                    print(f"\n[Browser State Check]: {result}\n")
                
                method = data.get("method")
                params = data.get("params", {})
                
                # Capture standard console.log / console.error
                if method == "Runtime.consoleAPICalled":
                    args = params.get("args", [])
                    log_type = params.get("type", "log").upper()
                    val = " ".join([str(arg.get("value", arg)) if isinstance(arg, dict) else str(arg) for arg in args])
                    print(f"[{log_type}]: {val}")
                
                # Capture uncaught exceptions / JS crashes
                elif method == "Runtime.exceptionThrown":
                    details = params.get("exceptionDetails", {})
                    text = details.get("text", "")
                    ex = details.get("exception", {})
                    desc = ex.get("description", "Unknown exception")
                    print(f"\n🔴 [UNCAUGHT EXCEPTION]: {text} - {desc}\n")
                
                # Capture standard log entries
                elif method == "Log.entryAdded":
                    entry = params.get("entry", {})
                    lvl = entry.get("level", "log").upper()
                    txt = entry.get("text", "")
                    print(f"[LOG-{lvl}]: {txt}")
                
                # Capture network response errors
                elif method == "Network.responseReceived":
                    resp = params.get("response", {})
                    status = resp.get("status")
                    if status and status >= 400:
                        print(f"🔴 [NETWORK ERROR] Status {status}: {resp.get('url')}")

            except asyncio.TimeoutError:
                # No new log messages in the last 2 seconds, keep listening
                pass
            except Exception as e:
                print(f"Error reading websocket: {e}")
                break

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDisconnected from debugger.")
