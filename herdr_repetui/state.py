"""User-wide viewer record (guarded by the global lock)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PLACEMENTS = frozenset({"bottom", "side"})


@dataclass
class Viewer:
    server_socket: str
    pane_id: str
    tab_id: str
    origin_pane_id: str
    placement: str


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


def load_state(path: Path) -> tuple[Viewer | None, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, "bottom"
    if not isinstance(data, dict):
        return None, "bottom"
    last = data.get("last_placement")
    return _viewer(data.get("viewer")), last if last in PLACEMENTS else "bottom"


def save_state(path: Path, viewer: Viewer | None, last_placement: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"viewer": asdict(viewer) if viewer else None,
                               "last_placement": last_placement}))
    os.replace(tmp, path)
