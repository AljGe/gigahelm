# -*- coding: utf-8 -*-
"""One-shot: fix sub-floor curve points (1-24%) in installed profiles.
CPU tables -> 25 (cosmetic, EC clamps anyway); GPU tables -> 0 (fan off at
low temps - the validated factory behavior). Run elevated; stops the app,
edits atomically, restarts via the startup task."""
import json
import os
import subprocess
import sys
import tempfile
import time

CONFIG = r"C:\Tools\Gigahelm\control_config.json"
TASK = "Gigahelm Startup Task"


def main() -> int:
    try:
        with open(CONFIG, "rb") as fh:
            fh.read(1)
        probe = os.path.join(os.path.dirname(CONFIG), ".wtest")
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
    except PermissionError:
        print("cannot write the installed config - run from an elevated terminal")
        return 2

    subprocess.run(["taskkill", "/IM", "Gigahelm.exe", "/F"],
                   capture_output=True)
    time.sleep(2.0)

    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)

    changes = []
    for name, prof in cfg.get("profiles", {}).items():
        for key, floor in (("cpu_fan_table", 25), ("gpu_fan_table", 0)):
            for pt in prof.get(key, []):
                if isinstance(pt, list) and len(pt) == 2 and 0 < pt[1] < 25:
                    changes.append((name, key, pt[0], pt[1], floor))
                    pt[1] = floor

    if not changes:
        print("no sub-floor points found - nothing to do")
        return 0

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CONFIG),
                               prefix="control_config_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=4)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, CONFIG)

    subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True,
                   timeout=20)

    print("changed points (profile, table, temp, old, new):")
    for row in changes:
        print(f"  {row[0]:8} {row[1]:14} [{row[2]} C] {row[3]} -> {row[4]}")

    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            age = time.time() - os.path.getmtime(
                os.path.join(os.path.dirname(CONFIG), "heartbeat.state"))
            if age < 10:
                print(f"app restarted, heartbeat fresh ({age:.1f}s)")
                return 0
        except OSError:
            pass
        time.sleep(2.0)
    print("WARNING: heartbeat not fresh within 60s - check the app")
    return 1


if __name__ == "__main__":
    sys.exit(main())
