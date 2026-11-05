"""User-wide viewer record (guarded by the global lock).

state.json: {"viewer": {...} | null, "last_placement": "bottom" | "side",
             "pending": {"server_socket": str, "started_at": float} | null}

`pending` is written just before a viewer pane is opened and replaced by the full viewer record
once the move succeeded (or cleared once the new pane was closed again). If the action dies in
between, the marker names the server whose panes must be scanned for the orphan.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PLACEMENTS = frozenset({"bottom", "side"})
STARTED_AT_MAX = 1e11  # epoch seconds; anything at or beyond this (year ~5138) is corrupt


@dataclass
class Viewer:
    server_socket: str
    pane_id: str
    tab_id: str
    origin_pane_id: str
    placement: str


@dataclass(frozen=True)
class Pending:
    server_socket: str
    started_at: float


def _viewer(raw: object) -> Viewer | None:
    """A well-formed record, or None: a corrupt record is treated as no record (the adoption scan,
    which needs the ownership token, then finds a live viewer if there is one)."""
    if not isinstance(raw, dict) or set(raw) != {f.name for f in fields(Viewer)}:
        return None
    if not all(isinstance(value, str) for value in raw.values()):
        return None
    if not raw["server_socket"] or not raw["pane_id"] or raw["placement"] not in PLACEMENTS:
        return None
    return Viewer(**raw)


def _pending(raw: object) -> Pending | None:
    """A well-formed marker, or None (a malformed marker counts as no marker)."""
    if not isinstance(raw, dict) or set(raw) != {"server_socket", "started_at"}:
        return None
    socket, started = raw["server_socket"], raw["started_at"]
    if not isinstance(socket, str) or not socket:
        return None
    if isinstance(started, bool) or not isinstance(started, (int, float)):
        return None
    try:
        started = float(started)
    except (ValueError, OverflowError, TypeError):   # e.g. a 400-digit integer
        return None
    if not math.isfinite(started) or not 0 < started < STARTED_AT_MAX:
        return None
    return Pending(socket, started)


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError):  # RecursionError: absurdly nested JSON
        return {}
    return data if isinstance(data, dict) else {}


def load_state(path: Path) -> tuple[Viewer | None, str]:
    data = _read(path)
    last = data.get("last_placement")
    return _viewer(data.get("viewer")), last if isinstance(last, str) and last in PLACEMENTS else "bottom"


def load_pending(path: Path) -> Pending | None:
    return _pending(_read(path).get("pending"))


def save_state(path: Path, viewer: Viewer | None, last_placement: str, *,
               pending: Pending | None = None) -> None:
    """Write the whole state. The marker is dropped unless passed: every action settles a marker
    first, so later writes in the same action must not resurrect it."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"viewer": asdict(viewer) if viewer else None,
                               "last_placement": last_placement,
                               "pending": asdict(pending) if pending else None}))
    os.replace(tmp, path)
