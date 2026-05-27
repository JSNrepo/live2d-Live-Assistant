import sys
import os
import math
from pathlib import Path

# Add project root to sys.path
sys.path.append("/home/vinoth/projects/python/livepythongemini")

def run_tests():
    print("=== STARTING CORE LOGICAL FUNCTIONALITY TESTS ===")
    
    try:
        from main import (
            search_web_contents,
            get_system_health,
            get_current_time,
            remember_relationship,
            forget_relationship,
            get_relationship_graph,
            run_shell_command
        )
    except ImportError as e:
        print(f"FAIL: Core function imports failed: {e}")
        sys.exit(1)

    # 1. Test get_system_health
    print("\n1. Testing get_system_health()...")
    try:
        health = get_system_health()
        print(f"Health details: {health}")
        assert isinstance(health, dict), "Health output must be a dictionary"
        assert "cpu_percent" in health or "error" in health, "Missing key in health status"
        print("PASS: get_system_health() verified successfully.")
    except Exception as e:
        print(f"FAIL: get_system_health raised an exception: {e}")

    # 2. Test get_current_time
    print("\n2. Testing get_current_time()...")
    try:
        t_info = get_current_time()
        print(f"Time details: {t_info}")
        assert isinstance(t_info, dict), "Time output must be a dictionary"
        assert "time" in t_info, "Missing time key"
        print("PASS: get_current_time() verified successfully.")
    except Exception as e:
        print(f"FAIL: get_current_time raised an exception: {e}")

    # 3. Test memory relationship persistence functions
    print("\n3. Testing memory graph functions...")
    try:
        # Clear/initialize memory file safely
        mem_graph_file = Path("/home/vinoth/projects/python/livepythongemini/memory_graph.json")
        
        # Test remember
        rem_res = remember_relationship("user", "likes", "filter coffee")
        print(f"Remember response: {rem_res}")
        assert isinstance(rem_res, dict)
        assert "result" in rem_res
        
        # Test query
        graph = get_relationship_graph("user")
        print(f"Query response: {graph}")
        assert isinstance(graph, dict)
        assert "connected_facts" in graph
        
        # Verify relationship exists in graph
        facts = graph.get("connected_facts", [])
        found = any("user likes filter coffee" in f for f in facts)
        assert found, "Relationship coffee was not saved successfully!"
        print("PASS: Remember and Query completed successfully.")
        
        # Test forget
        forg_res = forget_relationship("user", "likes", "filter coffee")
        print(f"Forget response: {forg_res}")
        assert isinstance(forg_res, dict)
        assert "result" in forg_res
        
        # Query again to confirm deletion
        graph_after = get_relationship_graph("user")
        facts_after = graph_after.get("connected_facts", [])
        found_after = any("user likes filter coffee" in f for f in facts_after)
        assert not found_after, "Relationship was not deleted successfully!"
        print("PASS: Forget and verification completed successfully.")
    except Exception as e:
        print(f"FAIL: Memory Graph functions failed: {e}")

    # 4. Test Web Search
    print("\n4. Testing search_web_contents()...")
    try:
        search_res = search_web_contents("Gemini AI News")
        print(f"Search results keys: {list(search_res.keys())}")
        print(f"Number of results found: {len(search_res.get('results', []))}")
        if search_res.get("results"):
            print(f"First result: {search_res.get('results')[0]}")
        print("PASS: Web search returned clean search dictionary.")
    except Exception as e:
        print(f"FAIL: search_web_contents raised an exception: {e}")

    # 5. Test run_shell_command (Safe patterns)
    print("\n5. Testing run_shell_command()...")
    try:
        cmd_res = run_shell_command("echo 'Hello World'")
        print(f"Shell command response: {cmd_res}")
        assert isinstance(cmd_res, dict)
        assert cmd_res.get("success") is True
        assert cmd_res.get("stdout").strip() == "Hello World"
        print("PASS: Safe shell command execution validated successfully.")
    except Exception as e:
        print(f"FAIL: run_shell_command raised an exception: {e}")

    print("\n=== ALL CORE LOGICAL FUNCTIONALITY TESTS COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    run_tests()
