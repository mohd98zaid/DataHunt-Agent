#!/usr/bin/env python3
"""
Test runner utility for DataHunt Agent.
Runs pytest suite with automatic environment verification.
"""
import sys
import subprocess

def main():
    print("=" * 60)
    print("  DataHunt Agent — Automated Test Suite Runner")
    print("=" * 60)
    cmd = [sys.executable, "-m", "pytest", "tests/", "-v"]
    res = subprocess.run(cmd)
    sys.exit(res.returncode)

if __name__ == "__main__":
    main()
