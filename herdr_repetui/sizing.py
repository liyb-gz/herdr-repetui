"""Viewer size relative to the whole tab, expressed as a herdr pane-move ratio."""

from __future__ import annotations

RATIO_IS_TARGET_SHARE = True  # Task 1 Step 4 finding
MIN_ROWS = 6
MIN_COLS = 40


def split_ratio(fraction: float, tab_dim: int, target_dim: int, minimum: int) -> float:
    if target_dim <= 0:
        return 0.5
    want = max(minimum, round(fraction * tab_dim))
    target_share = min(0.9, max(0.1, 1 - want / target_dim))
    return target_share if RATIO_IS_TARGET_SHARE else 1 - target_share
