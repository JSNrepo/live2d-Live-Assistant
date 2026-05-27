import sys
import os
import asyncio
from pathlib import Path
from PIL import Image

# Add project root to sys.path
sys.path.append("/home/vinoth/projects/python/livepythongemini")

# Load environment
from dotenv import load_dotenv
load_dotenv(Path("/home/vinoth/projects/python/livepythongemini") / ".env")

from main import task_client, vision_client

TEST_MODEL = "gemini-2.5-flash"

async def test_apis():
    print("=== STARTING VISION & TASK API INTEGRATION TESTS ===")
    
    # 1. Test Task API Client
    print(f"\n1. Testing task_client using model={TEST_MODEL}...")
    try:
        response = await task_client.aio.models.generate_content(
            model=TEST_MODEL,
            contents="Say 'Task API is working successfully!' in a rowdy Tirunelveli tone."
        )
        print(f"PASS: Task API response text: '{response.text}'")
    except Exception as e:
        print(f"FAIL: Task API call failed: {e}")

    # 2. Test Vision API Client
    print(f"\n2. Testing vision_client using model={TEST_MODEL}...")
    try:
        # Create a dummy solid color PIL image to send
        dummy_img = Image.new('RGB', (200, 200), color = 'red')
        
        response = await vision_client.aio.models.generate_content(
            model=TEST_MODEL,
            contents=[dummy_img, "What is the primary color of this image? Answer in one word."]
        )
        print(f"PASS: Vision API response text: '{response.text}'")
    except Exception as e:
        print(f"FAIL: Vision API call failed: {e}")

    print("\n=== VISION & TASK API INTEGRATION TESTS COMPLETED ===")

if __name__ == "__main__":
    asyncio.run(test_apis())
