# -*- coding: utf-8 -*-
"""Set the active profile from an elevated terminal (the installed data home
is admin-writable only). Usage:
    .venv\\Scripts\\python.exe -X utf8 tools\\bench\\activate_profile.py Gaming
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import appctl  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: activate_profile.py <profile-name>")
        return 2
    name = sys.argv[1]
    cfg = appctl.load_cfg()
    if name not in cfg.get("profiles", {}):
        print(f"no such profile: {name} (have: {sorted(cfg.get('profiles', {}))})")
        return 2
    mode = cfg["profiles"][name].get("fan_mode", "auto")
    appctl.apply_profile(name, mode)
    print(f"active: {name} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
