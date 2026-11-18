# -*- coding: utf-8 -*-
"""One-shot: move the installed profiles' fan park off the beat zone.

Evidence (whistle sweep 2026-11-18, three mic-scored recordings): any spin
request of 1-27 % parks at raw 57-62 where the two fans sit 35-90 Hz apart
- the roughness band, the silent-mode whistle. From raw 64 they self-separ-
ate ~250 Hz, and raw 69 (30 %) is the only park that is beat-free AND
stable across runs (28-29 % wander).

This edits every installed profile table:
- every point 0 < value < 30 -> 30 (the stable beat-free park),
- inserts a 0-plateau ~2 degC below each spin-onset knee, so idle temps map
  to fan-off instead of lingering in the beat zone (transitions through it
  are covered by the app's anti-beat detune, shipped in the same change).

Run elevated; stops the app, edits atomically (timestamped backup), rest-
arts via the startup task and waits for a fresh heartbeat. The knee jump
0 -> 30 crosses the beat zone in one tick, so no new steady state appears
there.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

CONFIG = r"C:\Tools\Gigahelm\control_config.json"
TASK = "Gigahelm Startup Task"
NEW_PARK = 30


def plateau_point(table, park_value):
    """[onset - 2, 0] below the first spinning point, or None."""
    first = next((pt for pt in table if 0 < pt[1]), None)
    if first is None:
        return None
    zeros_below = [pt for pt in table if pt[0] < first[0] and pt[1] == 0]
    if not zeros_below:
        return None
    prev = max(zeros_below, key=lambda pt: pt[0])
    if first[0] - prev[0] <= 4:
        return None  # already tight
    return [first[0] - 2, 0]


def main() -> int:
    try:
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

    backup = f"{CONFIG}.park-bak-{time.strftime('%Y%m%d-%H%M%S')}"
    with open(CONFIG, "rb") as src, open(backup, "wb") as dst:
        dst.write(src.read())

    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)

    changes = []
    for name, prof in cfg.get("profiles", {}).items():
        for key in ("cpu_fan_table", "gpu_fan_table"):
            table = prof.get(key)
            if not isinstance(table, list):
                continue
            for pt in table:
                if isinstance(pt, list) and len(pt) == 2 \
                        and 0 < pt[1] < NEW_PARK:
                    changes.append((name, key, pt[0], pt[1], NEW_PARK))
                    pt[1] = NEW_PARK
            ins = plateau_point(table, NEW_PARK)
            if ins is not None and not any(p[0] == ins[0] for p in table):
                table.append(list(ins))
                table.sort(key=lambda p: p[0])
                changes.append((name, key, ins[0], "inserted 0 plateau"))

    if not changes:
        print("no park points to move - nothing to do")
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

    print(f"changed points (profile, table, temp, old, new); backup: {backup}")
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
