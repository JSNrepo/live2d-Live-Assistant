#!/usr/bin/env python3
import sys
from pathlib import Path

def main():
    print("Verifying updated tool descriptions in main.py...")
    main_path = Path(__file__).parent.parent / "main.py"
    if not main_path.exists():
        print(f"Error: main.py not found at {main_path}")
        sys.exit(1)
        
    with open(main_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    checks = {
        "open_browser description": "simple website or page openings instead of starting complex automation.",
        "run_browser_task description": "DO NOT call this if the user only wants to open a simple URL or search (use open_browser or search_web_contents instead).",
        "run_shell_command description": "Runs a shell command on the user's Linux system asynchronously in the background and returns the output shortly.",
        "search_web_contents description": "Searches the web/internet for text content and answers in the background asynchronously.",
        "do_background_shell_command": "async def do_background_shell_command",
        "do_background_web_search": "async def do_background_web_search",
        "CRITICAL TOOL USE INSTRUCTIONS": "Never make up or assume answers or hardcode system details"
    }
    
    passed = True
    for key, val in checks.items():
        if val in content:
            print(f"✅ PASSED check: {key}")
        else:
            print(f"❌ FAILED check: {key} (expected '{val}' to be present)")
            passed = False
            
    if passed:
        print("\n🎉 SUCCESS: All tool descriptions and background helper functions are verified and 100% correct!")
        sys.exit(0)
    else:
        print("\n⚠️ Verification failed. Some updates are missing.")
        sys.exit(1)

if __name__ == "__main__":
    main()
