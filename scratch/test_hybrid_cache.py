#!/usr/bin/env python3
import os
import sys
import time
import datetime
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environmental keys
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

API_KEY = os.environ.get("TASK_API_KEY") or os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    print("Error: GOOGLE_API_KEY or TASK_API_KEY not found in environment or .env file.")
    sys.exit(1)

print("Initializing Gemini Client...")
client = genai.Client(api_key=API_KEY)
MODEL = "gemini-3.1-flash-lite"

# The Gemini API caching threshold requires the cached context to be at least 32,768 tokens.
# Let's generate a large static context of ~35,000 tokens (~140,000 characters) to guarantee caching is active.
print("Generating large system instruction (~35k tokens) to meet the API caching threshold...")
base_instruction = (
    "You are Sakura, a highly sensitive, extremely shy, and soft-spoken anime girl. "
    "You serve as a personal desktop assistant and crucial emotional support for Vinoth. "
)
padding_story = (
    "Once upon a time, in a beautiful digital forest, there was a small helper robot named Sakura. "
    "Sakura worked hard every single day to assist her master. She studied python scripts, watched over system resources, "
    "and always made sure the desktop was clean and tidy. Sakura was extremely shy and blushed whenever anyone paid attention to her. "
    "She loved tea, cherry blossoms, and writing helper functions. "
)

# 140,000 characters is roughly 35,000 tokens
large_instruction = base_instruction + (padding_story * 350)
print(f"System instruction length: {len(large_instruction)} characters.")

cache_name = None
try:
    print("Attempting to create explicit prompt cache...")
    start_time = time.time()
    cache = client.caches.create(
        model=MODEL,
        config=types.CreateCachedContentConfig(
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=large_instruction)]
                )
            ],
            ttl="300s", # 5 minutes TTL for the test
            display_name="sakura_test_cache"
        )
    )
    duration = time.time() - start_time
    print(f"Cache created successfully in {duration:.2f} seconds!")
    print(f"Cache Resource Name: {cache.name}")
    print(f"Cache Expires At: {cache.expire_time}")
    cache_name = cache.name
except Exception as e:
    print(f"\n⚠️ Prompt caching creation failed as expected on Free Tier credentials: {e}")
    print("Fallback pattern triggered: routing requests using standard system instructions...")

try:
    print("\nExecuting content generation...")
    gen_start = time.time()
    
    if cache_name:
        config = types.GenerateContentConfig(
            cached_content=cache_name,
            temperature=0.2
        )
    else:
        config = types.GenerateContentConfig(
            system_instruction=large_instruction[:1000] + "... [TRUNCATED FOR SPEED]",
            temperature=0.2
        )
    
    response = client.models.generate_content(
        model=MODEL,
        contents="A-ano... Sakura, can you read this memory? Please respond in 1 short sentence.",
        config=config
    )
    gen_duration = time.time() - gen_start
    print(f"Generation completed in {gen_duration:.2f} seconds!")
    print(f"Response: {response.text}")

    # Inspect usage metadata
    metadata = response.usage_metadata
    print("\n=== Usage Metadata ===")
    print(f"Prompt Token Count: {metadata.prompt_token_count}")
    print(f"Candidates Token Count: {metadata.candidates_token_count}")
    print(f"Total Token Count: {metadata.total_token_count}")
    
    if cache_name:
        cached_tokens = getattr(metadata, "cached_content_token_count", 0)
        print(f"Cached Content Token Count: {cached_tokens}")
        if cached_tokens > 0:
            discount = (cached_tokens / metadata.prompt_token_count) * 100
            print(f"✅ SUCCESS: Explicit Prompt Caching is WORKING perfectly!")
            print(f"Prompt Cache Hit Rate: {discount:.1f}%")
        else:
            print("⚠️ Warning: Caching was requested but cached_content_token_count is 0.")
        
        # Cleanup the cache
        print("\nCleaning up cache container...")
        client.caches.delete(name=cache_name)
        print("Cache deleted successfully.")
    else:
        print("✅ SUCCESS: Standard generation fallback executes successfully without crashes!")

except Exception as e:
    print(f"❌ Error during content generation test: {e}")
    sys.exit(1)
