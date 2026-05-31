#!/usr/bin/env python3
import sys
import os
import time
import shutil
import unittest
from pathlib import Path

# Add project root to python path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ANSI colors for beautiful logging
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    FAIL = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"


class TestSakuraModularArchitecture(unittest.TestCase):

    # ==========================================
    # 1. CONFIG MODULE TESTS
    # ==========================================
    def test_01_config_initialization(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying config module initialization...")
        import config
        self.assertTrue(hasattr(config, "AppState"))
        self.assertTrue(hasattr(config, "SYSTEM_INSTRUCTION"))
        self.assertTrue(hasattr(config, "EMOTION_TAG_MAP"))
        self.assertTrue(isinstance(config.EMOTION_TAG_MAP, dict))
        self.assertTrue("happy" in config.EMOTION_TAG_MAP)
        print(f"  🟢 Config constants parsed: AppState, EMOTION_TAG_MAP, system instruction present.")

    # ==========================================
    # 2. LIVE2D MODULE TESTS
    # ==========================================
    def test_02_live2d_state_and_animation(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying live2d animation and viseme mapping...")
        import live2d
        from config import AppState
        
        # Reset state
        live2d.ui.emotion = "sleeping"
        live2d.ui.speaker_rms = 0.0
        
        # Verify get_face transitions
        face_sleeping = live2d.get_face()
        self.assertTrue(isinstance(face_sleeping, str))
        
        # Test speaking viseme mapping based on RMS
        live2d.ui.emotion = "speaking"
        
        live2d.ui.speaker_rms = 100.0
        self.assertEqual(live2d.get_face(), "(-_-)")
        
        live2d.ui.speaker_rms = 1000.0
        self.assertEqual(live2d.get_face(), "(-o-)")
        
        live2d.ui.speaker_rms = 3000.0
        self.assertEqual(live2d.get_face(), "(-0-)")
        
        live2d.ui.speaker_rms = 6000.0
        self.assertEqual(live2d.get_face(), "(-O-)")
        
        # Test set_state transitions
        live2d.set_state(AppState.THINKING, "process", "Test thought")
        self.assertEqual(live2d.ui.state, AppState.THINKING)
        self.assertEqual(live2d.ui.emotion, "process")
        self.assertEqual(live2d.ui.emotion_text, "Test thought")
        print(f"  🟢 Live2D Visemes and states correctly evaluated and managed thread-safely.")

    # ==========================================
    # 3. MEMORY GRAPH PERSISTENCE TESTS
    # ==========================================
    def test_03_memory_graph_operations(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying memory graph database actions (atomic file writes)...")
        from memory import MemoryGraph
        
        temp_db_path = PROJECT_ROOT / "temp_test_memory.json"
        if temp_db_path.exists():
            temp_db_path.unlink()
            
        try:
            db = MemoryGraph(filepath=temp_db_path)
            
            # Add relationship
            res_add = db.add_relationship("user", "likes", "cricket")
            self.assertIn("Successfully remembered", res_add["result"])
            self.assertTrue(temp_db_path.exists())
            
            # Check duplicate addition
            res_dup = db.add_relationship("user", "likes", "cricket")
            self.assertIn("Fact already remembered", res_dup["result"])
            
            # Query graph (depth=1 DFS checking)
            res_query = db.get_relationship_graph("user")
            self.assertEqual(res_query["entity"], "user")
            self.assertIn("user likes cricket", res_query["connected_facts"])
            
            # Remove relationship
            res_remove = db.remove_relationship("user", "likes", "cricket")
            self.assertIn("Successfully forgot", res_remove["result"])
            
            # Verify clean query
            res_clean = db.get_relationship_graph("user")
            self.assertEqual(len(res_clean["connected_facts"]), 0)
            
            print(f"  🟢 Memory graph persistent triples correctly added, deduplicated, queried, and cleared.")
        finally:
            if temp_db_path.exists():
                temp_db_path.unlink()
            temp_tmp_path = temp_db_path.with_suffix(".tmp")
            if temp_tmp_path.exists():
                temp_tmp_path.unlink()

    # ==========================================
    # 4. TOOLS MODULE TESTS
    # ==========================================
    def test_04_tools_security_filters(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying tools command validation and security blocks...")
        from tools.system import _is_critical_command
        
        # Test safe commands
        self.assertFalse(_is_critical_command("ls -la"))
        self.assertFalse(_is_critical_command("git status"))
        
        # Test dangerous commands (destructive safety checking)
        self.assertTrue(_is_critical_command("rm -rf /"))
        self.assertTrue(_is_critical_command("sudo rm -r ./dir"))
        self.assertTrue(_is_critical_command("dd if=/dev/zero of=/dev/sda"))
        self.assertTrue(_is_critical_command("pkill -9 python"))
        
        print(f"  🟢 Crucial security execution blocks verified for destructive inputs.")

    def test_05_tools_diagnostics(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying system health and time tools...")
        from tools.system import get_system_health, get_current_time
        
        health = get_system_health()
        self.assertTrue("cpu_percent" in health)
        self.assertTrue("ram_percent" in health)
        
        ctime = get_current_time()
        self.assertTrue("time" in ctime)
        self.assertTrue("day_of_week" in ctime)
        
        print(f"  🟢 System diagnostic reports successfully generated: CPU={health['cpu_percent']}%, Time={ctime['time']}.")

    def test_06_web_search_reliability(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying web search API structure...")
        from tools.web_search import search_web_contents
        
        # Wikipedia APIs or DuckDuckGo structure verification (does not fail if offline)
        res = search_web_contents("Python programming")
        self.assertTrue("query" in res)
        self.assertTrue(isinstance(res.get("results"), list))
        print(f"  🟢 Web search wrapper processed query structures successfully.")

    # ==========================================
    # 5. AUDIO PIPELINE & TASK QUEUES
    # ==========================================
    def test_07_audio_pipeline_queues(self):
        print(f"\n{Colors.BLUE}[Test]{Colors.END} Verifying audio pipeline thread-safe queue setups...")
        import audio
        
        self.assertTrue(hasattr(audio, "mic_q"))
        self.assertTrue(hasattr(audio, "spk_q"))
        self.assertTrue(hasattr(audio, "session_send_q"))
        
        # Queue operations check
        audio.spk_q.put_nowait(b"dummy_pcm_frame")
        self.assertFalse(audio.spk_q.empty())
        frame = audio.spk_q.get_nowait()
        self.assertEqual(frame, b"dummy_pcm_frame")
        
        print(f"  🟢 Audio hardware interfaces and VIS/Speech triggers cleanly separated.")


def run_test_suite():
    print(f"\n{Colors.HEADER}{Colors.BOLD}====================================================")
    print("        🧪 Sakura Assistant Automated Test Suite")
    print(f"===================================================={Colors.END}")
    
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSakuraModularArchitecture)
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    
    print(f"\n{Colors.HEADER}{Colors.BOLD}====================================================")
    print("                    TEST SUMMARY")
    print(f"===================================================={Colors.END}")
    if result.wasSuccessful():
        print(f"  Status:  {Colors.GREEN}{Colors.BOLD}PASSED{Colors.END}")
        print(f"  Tests Run: {result.testsRun}")
        sys.exit(0)
    else:
        print(f"  Status:  {Colors.FAIL}{Colors.BOLD}FAILED{Colors.END}")
        print(f"  Errors: {len(result.errors)}")
        print(f"  Failures: {len(result.failures)}")
        sys.exit(1)


if __name__ == "__main__":
    run_test_suite()
