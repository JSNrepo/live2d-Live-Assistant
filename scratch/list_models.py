import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.append("/home/vinoth/projects/python/livepythongemini")

# Load environment
from dotenv import load_dotenv
load_dotenv(Path("/home/vinoth/projects/python/livepythongemini") / ".env")

from main import task_client

def list_all_models():
    print("=== LISTING ALL AVAILABLE MODELS ===")
    try:
        models = task_client.models.list()
        for m in models:
            print(f"- {m.name}")
    except Exception as e:
        print(f"Error listing models: {e}")

if __name__ == "__main__":
    list_all_models()
