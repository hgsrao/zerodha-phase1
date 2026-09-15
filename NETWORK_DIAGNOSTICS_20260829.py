#!/usr/bin/env python3
"""
Network Diagnostics - Check connectivity between PC and Laptop
"""

import socket
import subprocess
import sys
import requests

print("\n" + "="*80)
print("NETWORK DIAGNOSTICS - Master (PC) to Worker (Laptop)")
print("="*80 + "\n")

# Step 1: Check local machine IP
print("Step 1: Check local PC IP")
print("-" * 80)

try:
    hostname = socket.gethostname()
    pc_ip = socket.gethostbyname(hostname)
    print(f"✓ PC Hostname: {hostname}")
    print(f"✓ PC Local IP: {pc_ip}")
    print()
except Exception as e:
    print(f"✗ Error: {e}\n")

# Step 2: Test Ping to Laptop
print("Step 2: Ping Laptop (192.168.0.17)")
print("-" * 80)

try:
    # Use ping with count=1
    result = subprocess.run(
        ["ping", "-n", "1", "192.168.0.17"],
        capture_output=True,
        text=True,
        timeout=5
    )

    if result.returncode == 0:
        print("✓ Laptop is reachable via ICMP ping")
        print(result.stdout)
    else:
        print("✗ Ping failed - Laptop may not be reachable")
        print(result.stdout)
        print(result.stderr)
except Exception as e:
    print(f"✗ Ping error: {e}\n")

# Step 3: Test TCP connection to port 5001
print("Step 3: Test TCP connection to 192.168.0.17:5001")
print("-" * 80)

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)

    result = sock.connect_ex(('192.168.0.17', 5001))

    if result == 0:
        print("✓ Port 5001 is OPEN on Laptop")
        sock.close()
    else:
        print(f"✗ Port 5001 is CLOSED or FILTERED on Laptop (error code: {result})")
        print("  Possible causes:")
        print("  1. Worker is not running")
        print("  2. Worker is listening on wrong interface")
        print("  3. Firewall is blocking port 5001")
        print("  4. Wrong IP address (verify with ipconfig on Laptop)")

    sock.close()
except Exception as e:
    print(f"✗ Connection test error: {e}\n")

# Step 4: Try HTTP request to /health endpoint
print("\nStep 4: Test HTTP GET to Worker /health endpoint")
print("-" * 80)

try:
    response = requests.get("http://192.168.0.17:5001/health", timeout=3)

    if response.status_code == 200:
        print("✓ Worker /health endpoint is responding")
        print(f"  Response: {response.json()}")
    else:
        print(f"✗ Worker returned status {response.status_code}")

except requests.exceptions.ConnectionError as e:
    print(f"✗ Connection failed: {e}")
    print("  This means:")
    print("  - Laptop IP is wrong, OR")
    print("  - Port 5001 is not open, OR")
    print("  - Firewall is blocking, OR")
    print("  - Worker process crashed")

except requests.exceptions.Timeout:
    print("✗ Connection timeout - Worker not responding")

except Exception as e:
    print(f"✗ Error: {e}\n")

# Step 5: Get all network interfaces on PC
print("\nStep 5: PC Network Interfaces")
print("-" * 80)

try:
    result = subprocess.run(
        ["ipconfig"],
        capture_output=True,
        text=True,
        timeout=5
    )

    print("Current network configuration:")
    print(result.stdout[:1000])  # First 1000 chars
    print("\nLook for:")
    print("  - IPv4 Address: (should be 192.168.0.47 or similar)")
    print("  - Default Gateway: (should connect to Laptop)")

except Exception as e:
    print(f"✗ Error: {e}\n")

# Step 6: Recommendations
print("\n" + "="*80)
print("TROUBLESHOOTING CHECKLIST")
print("="*80 + "\n")

print("✓ VERIFY ON LAPTOP:")
print("  1. Run: ipconfig")
print("     Look for IPv4 Address (should be 192.168.0.x)")
print("  2. Check Worker is running:")
print("     Run: python WORKER_NODE_DCS_20260829.py")
print("  3. Look for: 'Flask server starting on 0.0.0.0:5001'")
print()

print("✓ VERIFY ON PC:")
print("  1. Confirm IP address matches (use ipconfig)")
print("  2. Check network connection to Laptop")
print("     Ping: ping 192.168.0.17")
print("  3. Test port connectivity:")
print("     telnet 192.168.0.17 5001")
print()

print("✓ IF STILL FAILING:")
print("  1. Check Windows Firewall on Laptop")
print("     Settings → Firewall → Allow app through firewall")
print("     Make sure Python is allowed")
print("  2. Disable Windows Defender Firewall temporarily:")
print("     Settings → Firewall & network protection → Turn off")
print("  3. Check network is private (not public):")
print("     Settings → Network & internet → Network profile")
print()

print("✓ ALTERNATIVE - USE LOCALHOST (same machine):")
print("  If network is unavailable, run on same PC:")
print("  1. In MASTER_NODE_DCS_20260829.py, change:")
print("     WORKER_URL = 'http://127.0.0.1:5001'")
print("  2. Run Worker: python WORKER_NODE_DCS_20260829.py")
print("  3. Run Master: python MASTER_NODE_DCS_20260829.py")
print()

print("="*80 + "\n")
