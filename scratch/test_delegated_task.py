#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from main import run_browser_task

async def test():
    print("=" * 60)
    print("🎬 Testing Delegated Browser Task via Background Task Client")
    print("=" * 60)
    
    # Start Kimi WebBridge daemon check
    import requests
    try:
        resp = requests.get("http://127.0.0.1:10086/status", timeout=2)
        if not resp.json().get("running"):
            print("Error: WebBridge daemon is not running. Please start it.")
            sys.exit(1)
    except Exception:
        print("Error: Failed to connect to WebBridge. Please start the daemon first.")
        sys.exit(1)

    task_description = "navigate to google.com and get page title and content"
    print(f"\nTask: '{task_description}'\n")
    print("Running task autonomously...")
    
    res = await run_browser_task(task_description)
    print("\n" + "=" * 60)
    print("🏁 Task Execution Completed!")
    print("=" * 60)
    print("Success status:", res.get("success"))
    if res.get("success"):
        print("\n[Result Summary]:")
        print(res.get("result"))
    else:
        print("\n[Error Details]:")
        print(res.get("error"))

if __name__ == "__main__":
    asyncio.run(test())
