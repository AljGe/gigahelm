# -*- coding: utf-8 -*-
"""Battery health from Windows capacity data.

The EC's ``GetBatteryHealth`` method returns a status code on this platform
(observed: 1), not a percentage.  Windows exposes the full-charge capacity and
``powercfg /batteryreport`` exposes the design capacity; health is
full / design * 100.  Results are cached because the report is expensive.
"""

import html
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Optional

CACHE_SECONDS = 300.0
_cache_value: int = -1
_cache_until: float = 0.0


def _extract_capacity(text: str, label: str) -> Optional[int]:
    match = re.search(rf"{re.escape(label)}\s+([\d,]+)\s*mWh", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _read_from_wmi() -> Optional[int]:
    try:
        import wmi
        conn = wmi.WMI(namespace=r"root\WMI")
        static = conn.query("SELECT * FROM BatteryStaticData")
        full = conn.query("SELECT * FROM BatteryFullChargedCapacity")
        if not static or not full:
            return None
        design = int(static[0].DesignedCapacity)
        full_capacity = int(full[0].FullChargedCapacity)
        if design <= 0 or full_capacity <= 0:
            return None
        percent = round(full_capacity * 100 / design)
        return percent if 0 <= percent <= 100 else None
    except Exception:
        return None


def _read_from_powercfg() -> Optional[int]:
    if os.name != "nt":
        return None
    fd, path = tempfile.mkstemp(prefix="gigahelm-battery-", suffix=".html")
    os.close(fd)
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powercfg", "/batteryreport", "/output", path],
            capture_output=True,
            timeout=15,
            creationflags=creationflags,
        )
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)
        design = _extract_capacity(text, "DESIGN CAPACITY")
        full = _extract_capacity(text, "FULL CHARGE CAPACITY")
        if design and full and design > 0:
            percent = round(full * 100 / design)
            return percent if 0 <= percent <= 100 else None
        return None
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def get_battery_health(ec_value: int) -> int:
    """Return a percentage, or -1 when Windows cannot provide capacities.

    ``ec_value`` is only trusted when it looks like a percentage (50-100);
    small values are status codes and must not be rendered as 1%.
    """
    global _cache_value, _cache_until
    now = time.monotonic()
    if now >= _cache_until:
        value = _read_from_wmi()
        if value is None:
            value = _read_from_powercfg()
        _cache_value = value if value is not None else -1
        _cache_until = now + CACHE_SECONDS
        if _cache_value < 0:
            print("Battery health: no Windows design/full capacity available.", file=sys.stderr)

    if _cache_value >= 0:
        return _cache_value
    if 50 <= ec_value <= 100:
        return ec_value
    return -1
