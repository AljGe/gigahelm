# -*- coding: utf-8 -*-
"""Undo/redo history for the curve editor.

Stacks hold PRE-edit snapshots as (curve_type, deep-copied points) taken
before each committed edit, so undo works across interleaved CPU/GPU edits.
Restore is the caller's job (profile write); the stacks never mutate live
data themselves.
"""
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

Snapshot = Tuple[str, List[List[int]]]


class CurveEditHistory:
    """Bounded pre-edit snapshot stacks; clear() on profile switches."""

    def __init__(self, limit: int = 50):
        self._undo: Deque[Snapshot] = deque(maxlen=limit)
        self._redo: Deque[Snapshot] = deque(maxlen=limit)

    def push(self, curve_type: str, points: List[List[int]]) -> None:
        """Record the state before an edit; any redo chain becomes stale."""
        self._undo.append((curve_type, [list(p) for p in points]))
        self._redo.clear()

    def undo(self, current_points: Callable[[str], List[List[int]]]) -> Optional[Snapshot]:
        """Pop the last snapshot; the current state of that curve becomes the
        redo snapshot. Returns None when nothing to undo."""
        if not self._undo:
            return None
        snapshot = self._undo.pop()
        self._redo.append((snapshot[0], [list(p) for p in current_points(snapshot[0])]))
        return snapshot

    def redo(self, current_points: Callable[[str], List[List[int]]]) -> Optional[Snapshot]:
        """Pop the last undone snapshot; the current state of that curve
        becomes the undo snapshot. Returns None when nothing to redo."""
        if not self._redo:
            return None
        snapshot = self._redo.pop()
        self._undo.append((snapshot[0], [list(p) for p in current_points(snapshot[0])]))
        return snapshot

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
