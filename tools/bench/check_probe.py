# -*- coding: utf-8 -*-
"""Exit 0 iff probe-once.txt holds a sane EC sample; elevates nothing.

Usage: python check_probe.py <path-to-probe-once.txt>
Sane = cpu_t in [30, 100] and decoded rpm1/rpm2 in [0, 8000].
"""
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        sample = json.loads(fh.readline())
    ok = (30 <= sample["cpu_t"] <= 100
          and 0 <= sample["rpm1"] <= 8000
          and 0 <= sample["rpm2"] <= 8000)
except Exception:
    ok = False
sys.exit(0 if ok else 5)
