import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(("127.0.0.1", 10088))
    print("SUCCESS: Port 10088 is FREE and can be bound!")
except Exception as e:
    print(f"FAILED: Port 10088 is already OCCUPIED! Error: {e}")
