import errno
import socket
from pathlib import Path

import pytest

from herdr_repetui import dtach
from herdr_repetui.dtach import SocketProbeError, socket_state


def test_absent(short_dir) -> None:
    assert socket_state(short_dir / "none.sock") == "absent"


def test_live_and_stale(short_dir) -> None:
    path = short_dir / "live.sock"
    server = socket.socket(socket.AF_UNIX); server.bind(str(path)); server.listen(1)
    try:
        assert socket_state(path) == "live"
    finally:
        server.close()
    try:
        assert socket_state(path) == "stale"
    finally:
        path.unlink()


@pytest.mark.parametrize("exc", [PermissionError(errno.EACCES, "denied"), TimeoutError("slow"),
                                 OSError(errno.ENOTSOCK, "not a socket")])
def test_uncertain_failures_raise(tmp_path, monkeypatch, exc) -> None:
    class Boom:
        def __init__(self, *a): ...
        def settimeout(self, t): ...
        def connect(self, p): raise exc
        def close(self): ...
    monkeypatch.setattr(dtach.socket, "socket", Boom)
    with pytest.raises(SocketProbeError):
        socket_state(tmp_path / "x.sock")
