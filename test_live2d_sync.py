#!/usr/bin/env python3
import socket
import time
import math
import sys

def send_cmd(cmd: str):
    print(f"Sending to UDP 10088: {cmd}")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(cmd.encode("utf-8"), ("127.0.0.1", 10088))
    except Exception as e:
        print(f"Error sending UDP: {e}")

def run_test():
    print("=" * 60)
    print("🎬 Starting Live2D High-Fidelity Diagnostic Test Suite")
    print("Ensure the Live2D overlay is running via: ./run_live2d.sh")
    print("Right-click on Hiyori and select 'Inspect' to watch the DevTools Console!")
    print("=" * 60)
    time.sleep(2)

    # ----------------------------------------------------
    # TEST 1: Waking Up & Listening State (Rhythmic Nodding)
    # ----------------------------------------------------
    print("\n--- TEST 1: LISTENING POSTURE & RHYTHMIC NODDING ---")
    send_cmd("state:LISTENING")
    send_cmd("emotion:idle")
    print("Observe: Hiyori should immediately close her mouth (locked at 0.0) and begin a slow, rhythmic vertical head sway (nodding).")
    time.sleep(4)

    # ----------------------------------------------------
    # TEST 2: Thinking State (Tilt Head & Eye Drift)
    # ----------------------------------------------------
    print("\n--- TEST 2: THINKING STATE (HEAD TILT & EYE DRIFT) ---")
    send_cmd("state:THINKING")
    send_cmd("emotion:process")
    print("Observe: Hiyori's head should tilt slightly upward, and her eyeballs should drift away to the top-left (deep in thought).")
    time.sleep(4)

    # ----------------------------------------------------
    # TEST 3: Speaking State & Ultra-Smooth Lip-Sync Loop (50 FPS)
    # ----------------------------------------------------
    print("\n--- TEST 3: SPEAKING STATE & ULTRA-SMOOTH LIP-SYNC ---")
    send_cmd("state:SPEAKING")
    send_cmd("emotion:speaking")
    print("Observe: She should trigger her 'Tap' explaining hand gesture. Mouth should start moving smoothly in a 50 FPS sine wave.")
    
    # Simulate smooth speech sine wave at 50 FPS (20ms frames) for 4 seconds
    start_time = time.time()
    while time.time() - start_time < 4.0:
        elapsed = time.time() - start_time
        # Generates smooth mouth values oscillating between 0.0 and 0.8
        mouth_val = (math.sin(elapsed * 12) + 1.0) / 2.0 * 0.8
        send_cmd(f"mouth:{mouth_val:.2f}")
        time.sleep(0.02) # 20ms frames
        
    send_cmd("mouth:0.00")
    time.sleep(1)

    # ----------------------------------------------------
    # TEST 4: Viseme Syllable Mouth Shapes
    # ----------------------------------------------------
    print("\n--- TEST 4: VISEME SYLLABLE SHAPES & CLAUSE HEAD TILTS ---")
    send_cmd("state:SPEAKING")
    
    visemes = [
        ("speech:Aaa Eee Ooo", "A, E, O sounds (vertical open mouth form)"),
        ("speech:Iee Yyy", "I, Ee sounds (wide smile form)"),
        ("speech:Uoo Www", "U, W sounds (narrow rounded form)"),
        ("speech:Ayo, enna pa panre!", "Punctuation clause comma/period (sways head side to side)")
    ]

    for speech_cmd, description in visemes:
        print(f"\nViseme: {description}")
        send_cmd(speech_cmd)
        # Play mouth movement alongside the viseme shape
        start_time = time.time()
        while time.time() - start_time < 2.0:
            elapsed = time.time() - start_time
            mouth_val = (math.sin(elapsed * 15) + 1.0) / 2.0 * 0.7
            send_cmd(f"mouth:{mouth_val:.2f}")
            time.sleep(0.02)
        send_cmd("mouth:0.00")
        time.sleep(0.5)

    # ----------------------------------------------------
    # TEST 5: Emotion Transition Motions & Expressions
    # ----------------------------------------------------
    print("\n--- TEST 5: EMOTION TRANSITION MOTIONS & EXPRESSIONS ---")
    
    emotions = [
        ("emotion:angry", "ANGRY: Frowning eyebrows & plays 'Flick' head shake"),
        ("emotion:sad", "SAD: Drooping sad eyebrows & plays 'FlickDown' sad head drop"),
        ("emotion:smug", "SMUG: Blushing cheeks, happy smile & plays 'FlickUp' proud head lift"),
        ("emotion:suspicious", "SUSPICIOUS: Frowning curious eyebrows & plays 'Flick@Body' shrug step-back")
    ]

    for emo_cmd, description in emotions:
        print(f"\nTriggering: {description}")
        send_cmd(emo_cmd)
        # Play standard speaking lip-sync under this emotion
        start_time = time.time()
        while time.time() - start_time < 3.0:
            elapsed = time.time() - start_time
            mouth_val = (math.sin(elapsed * 10) + 1.0) / 2.0 * 0.7
            send_cmd(f"mouth:{mouth_val:.2f}")
            time.sleep(0.02)
        send_cmd("mouth:0.00")
        time.sleep(1)

    # ----------------------------------------------------
    # TEST 6: Real-Time Interruption Startled Override
    # ----------------------------------------------------
    print("\n--- TEST 6: REAL-TIME INTERRUPTION STARTLED OVERRIDE ---")
    send_cmd("state:SPEAKING")
    send_cmd("emotion:speaking")
    print("Simulating active speech...")
    
    # Active mouth movement
    for j in range(30):
        send_cmd(f"mouth:{(math.sin(j * 0.5) + 1.0)/2.0 * 0.8:.2f}")
        time.sleep(0.02)
        
    print("\n⚡ INTERRUPTING NOW! ⚡")
    send_cmd("interrupted")
    print("Observe: Mouth should slam shut instantly (locked at 0.0), eyes should widen in surprise (1.4 scale) for 1.2 seconds, and she plays a startled shock shrug motion.")
    
    # Try sending late mouth updates during the startled override to check robustness
    for j in range(30):
        send_cmd(f"mouth:0.80") # Mouth should remain CLOSED due to safety override!
        time.sleep(0.02)
        
    time.sleep(2)

    # ----------------------------------------------------
    # TEST 7: Return to Listening Posture
    # ----------------------------------------------------
    print("\n--- TEST 7: FINAL LISTENING STATE ---")
    send_cmd("state:LISTENING")
    send_cmd("emotion:idle")
    print("Observe: Hiyori returns smoothly to rhythmic vertical head sways with closed mouth.")
    print("\n" + "=" * 60)
    print("🏁 Live2D Diagnostic Test Suite Completed!")
    print("=" * 60)

if __name__ == "__main__":
    try:
        run_test()
    except KeyboardInterrupt:
        print("\nTest interrupted.")
        sys.exit(0)
