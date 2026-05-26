#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

# Add the project directory to path
sys.path.append(str(Path(__file__).parent))

try:
    from main import (
        check_webbridge_active_sync,
        webbridge_navigate,
        webbridge_get_content,
        webbridge_click,
        webbridge_fill,
        webbridge_screenshot as webbridge_screenshot_sync
    )
except ImportError as e:
    print(f"Error importing from main.py: {e}")
    sys.exit(1)

def print_banner():
    print("=" * 60)
    print("        🎛️  Sakura Kimi WebBridge Interactive Test Console")
    print("=" * 60)

def print_status():
    print("\n[Status] Checking Kimi WebBridge daemon...")
    active = check_webbridge_active_sync()
    if active:
        print("  🟢 WebBridge Daemon is RUNNING and extension is CONNECTED!")
    else:
        print("  🔴 WebBridge Daemon is NOT running or extension is DISCONNECTED.")
        print("     Please start WebBridge first: kimi-webbridge start")

def test_navigation():
    url = input("\nEnter URL to navigate to (default: https://www.google.com): ").strip()
    if not url:
        url = "https://www.google.com"
    print(f"\n[Test] Navigating to {url}...")
    res = webbridge_navigate(url, new_tab=True)
    print(f"[Response] {res}")

def test_get_content():
    print("\n[Test] Fetching active tab page structure and content...")
    res = webbridge_get_content()
    if "error" in res:
        print(f"[Error] {res['error']}")
    else:
        print(f"[Title] {res.get('title')}")
        print(f"[URL] {res.get('url')}")
        content = res.get("page_content", "")
        # Print a snippet of the accessibility tree
        print("\n[Page Content Snippet - First 1000 chars]:")
        print("-" * 50)
        print(content[:1000])
        if len(content) > 1000:
            print("...")
        print("-" * 50)

def test_fill():
    print("\nMake sure you have a page open with a text field (e.g. google.com).")
    selector = input("Enter selector (CSS or @e ref, e.g. 'textarea[name=\"q\"]'): ").strip()
    value = input("Enter value to type: ")
    if not selector:
        print("[Error] Selector is required.")
        return
    print(f"\n[Test] Filling element '{selector}' with value '{value}'...")
    res = webbridge_fill(selector, value)
    print(f"[Response] {res}")

def test_click():
    print("\nMake sure the element you want to click is visible.")
    selector = input("Enter selector (CSS or @e ref, e.g. 'input[type=\"submit\"]'): ").strip()
    if not selector:
        print("[Error] Selector is required.")
        return
    print(f"\n[Test] Clicking element '{selector}'...")
    res = webbridge_click(selector)
    print(f"[Response] {res}")

def test_screenshot():
    print("\n[Test] Capturing browser screenshot...")
    res = webbridge_screenshot_sync()
    if "error" in res:
        print(f"[Error] {res['error']}")
    else:
        print(f"🟢 Success! Screenshot saved to: {res.get('filepath')}")

def main():
    print_banner()
    print_status()

    while True:
        print("\nSelect a functionality to test:")
        print("  1. 🌐 Test Navigation (webbridge_navigate)")
        print("  2. 📝 Test Content Extraction (webbridge_get_content)")
        print("  3. ⌨️  Test Form/Editor Filling (webbridge_fill)")
        print("  4. 🖱️  Test Element Click (webbridge_click)")
        print("  5. 📸 Test Browser Screenshot (webbridge_screenshot)")
        print("  6. 🔄 Check Daemon Status")
        print("  7. ❌ Exit")
        
        choice = input("\nEnter choice (1-7): ").strip()
        
        if choice == "1":
            test_navigation()
        elif choice == "2":
            test_get_content()
        elif choice == "3":
            test_fill()
        elif choice == "4":
            test_click()
        elif choice == "5":
            test_screenshot()
        elif choice == "6":
            print_status()
        elif choice == "7" or choice.lower() in ("exit", "quit"):
            print("\nExiting. Goodbye!")
            break
        else:
            print("Invalid choice, please select 1-7.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting. Goodbye!")
