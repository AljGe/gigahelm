# -*- coding: utf-8 -*-
"""One-shot: remove the contaminated Tuned profiles, repoint power-source
automation. Run elevated; stops the app, edits the config atomically,
restarts via the startup task (no save/reload race with the app)."""
import json
import os
import subprocess
import sys
import tempfile
import time

CONFIG = r"C:\Tools\Gigahelm\control_config.json"
TASK = "Gigahelm Startup Task"
DOOMED = ("Tuned AC", "Tuned Battery")
ON_AC_FALLBACK = "Gaming"
ON_BATTERY_FALLBACK = "Silent"


def main() -> int:
    if not os.path.exists(CONFIG):
        print(f"config not found: {CONFIG}")
        return 2
    subprocess.run(["taskkill", "/IM", "Gigahelm.exe", "/F"],
                   capture_output=True)
    time.sleep(2.0)

    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    profiles = cfg.get("profiles", {})
    removed = [n for n in DOOMED if n in profiles]
    for name in removed:
        profiles.pop(name)
    cfg["profile_order"] = [n for n in cfg.get("profile_order", [])
                            if n not in removed]
    if cfg.get("active_profile_name") in removed:
        cfg["active_profile_name"] = ON_BATTERY_FALLBACK
    if cfg.get("profile_on_battery") in removed:
        cfg["profile_on_battery"] = ON_BATTERY_FALLBACK
    if cfg.get("profile_on_ac") in removed:
        cfg["profile_on_ac"] = ON_AC_FALLBACK

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CONFIG),
                               prefix="control_config_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=4)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, CONFIG)

    subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True,
                   timeout=20)
    print(f"removed: {removed}")
    print(f"active: {cfg.get('active_profile_name')}, "
          f"on_ac: {cfg.get('profile_on_ac')}, "
          f"on_battery: {cfg.get('profile_on_battery')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
