#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from main import do_background_graph_ingestion, memory_db

async def test():
    print("=" * 60)
    print("🎬 Testing Asynchronous Memory Graph Ingestion (Cold Path)")
    print("=" * 60)

    user_utterance = "I am vinoth. I love playing cricket and coding python."
    ai_utterance = "[SMUG] Hahaha! Cricket ah? Python coding ah? Romba nalladhu la! Nanum python la tha create aana!"
    
    print("\n[Input Segment]:")
    print(f"  User: '{user_utterance}'")
    print(f"  AI:   '{ai_utterance}'")
    
    # Check current relationships before ingestion
    print("\nRelationships before ingestion:")
    print(memory_db.data.get("edges", []))
    
    print("\nTriggering background Cold Path ingestion...")
    await do_background_graph_ingestion(user_utterance, ai_utterance)
    
    # Wait a moment for background file writes/lock to settle
    await asyncio.sleep(2)
    
    print("\nRelationships after ingestion (Hot Path read):")
    res = memory_db.get_relationship_graph("user")
    print("Connected facts:", res.get("connected_facts"))

if __name__ == "__main__":
    asyncio.run(test())
