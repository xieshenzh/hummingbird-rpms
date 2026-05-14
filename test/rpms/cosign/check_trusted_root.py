#!/usr/bin/env python3
"""Verify trusted_root.json contains Rekor, CTLog, and Fulcio entries."""

import json
import sys

if len(sys.argv) != 2:
    print(f"Usage: {sys.argv[0]} <trusted_root.json>", file=sys.stderr)
    sys.exit(1)

with open(sys.argv[1]) as f:
    root = json.load(f)

ok = True
for key, label in [
    ("tlogs", "Rekor"),
    ("ctlogs", "CTLog"),
    ("certificateAuthorities", "Fulcio"),
]:
    entries = root.get(key, [])
    if not entries:
        print(f"FAIL: no {label} entries in trusted_root.json")
        ok = False
    else:
        print(f"PASS: {label} — {len(entries)} entries found")

sys.exit(0 if ok else 1)
