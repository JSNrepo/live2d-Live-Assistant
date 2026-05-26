#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from main import check_webbridge_active_sync, webbridge_navigate, webbridge_get_content, webbridge_screenshot

def test():
    print("Checking WebBridge active status...")
    active = check_webbridge_active_sync()
    print("Active:", active)
    if not active:
        print("Error: WebBridge not active. Make sure daemon is running and extension is connected.")
        sys.exit(1)

    print("\nNavigating to google.com...")
    res = webbridge_navigate("https://www.google.com", new_tab=True)
    print("Navigate Response:", res)

    print("\nGetting page content...")
    content = webbridge_get_content()
    print("Content keys:", content.keys())
    print("Title:", content.get("title"))
    print("URL:", content.get("url"))
    print("Content snippet (100 chars):", repr(content.get("page_content", "")[:100]))

    print("\nTaking screenshot...")
    shot = webbridge_screenshot()
    print("Screenshot Response:", shot)

if __name__ == "__main__":
    test()
