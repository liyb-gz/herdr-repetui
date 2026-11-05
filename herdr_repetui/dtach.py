"""Probe the dtach master socket (connect, then close). Only ECONNREFUSED means stale."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Literal


class SocketProbeError(RuntimeError):
    pass


def socket_state(path: Path) -> Literal["live", "stale", "absent"]:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
        return "live"
    except FileNotFoundError:
        return "absent"
    except ConnectionRefusedError:
        return "stale"
    except OSError as exc:
        raise SocketProbeError(f"cannot probe {path}: {exc}") from exc
    finally:
        probe.close()
