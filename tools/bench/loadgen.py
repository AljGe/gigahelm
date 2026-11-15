# -*- coding: utf-8 -*-
"""CPU load generator for the bench harness.

Spawns N busy-loop worker processes (Windows spawn start method). Workers are
terminated by stop(); each also self-terminates at its deadline so a crashed
harness never leaves burners behind.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import time

#: Load-name -> worker count.
LOAD_N = {"idle": 0, "dual": 2, "all": os.cpu_count() or 8}


def _burn(deadline: float) -> None:
    """Integer multiply-add loop; no allocations, saturates one hardware thread."""
    x = 1
    while time.time() < deadline:
        for _ in range(100_000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF


class Burners:
    """Context manager owning a set of burner processes."""

    def __init__(self) -> None:
        self._procs: list[multiprocessing.Process] = []

    def start(self, n: int, duration: float) -> None:
        self.stop()  # never stack two burner sets
        deadline = time.time() + duration + 5.0
        for _ in range(n):
            proc = multiprocessing.Process(target=_burn, args=(deadline,),
                                           daemon=True)
            proc.start()
            self._procs.append(proc)

    def stop(self) -> None:
        for proc in self._procs:
            if proc.is_alive():
                proc.terminate()
        for proc in self._procs:
            proc.join(5.0)
        self._procs = []

    def __enter__(self) -> "Burners":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


if __name__ == "__main__":
    # Smoke: run 2 burners for 3 s.
    with Burners() as burners:
        burners.start(LOAD_N["dual"], 3.0)
        time.sleep(4.0)
    print("loadgen ok")
    sys.exit(0)
