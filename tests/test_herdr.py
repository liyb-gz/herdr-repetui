import json
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from herdr_repetui.herdr import GONE_CODES, Herdr, HerdrError


def fake_run(responses):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1:])
        out = responses.pop(0)
        if isinstance(out, HerdrError):
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr=json.dumps({"error": {"code": out.code, "message": "x"}}))
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    return run, calls


def serve_once(path: str, reply: bytes | None, delay: float = 0.0):
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen(1)

    def loop():
        conn, _ = server.accept()
        data = b""
        while not data.endswith(b"\n"):
            data += conn.recv(4096)
        request = json.loads(data)
        threading.Event().wait(delay)
        if reply is not None:
            try:
                conn.sendall(reply.replace(b"$ID", request["id"].encode()))
            except OSError:  # client already timed out and hung up
                pass
        conn.close()
        server.close()

    thread = threading.Thread(target=loop)
    thread.start()
    return thread


def test_gone_codes_cover_missing_pane_and_unreachable_server() -> None:
    assert {"pane_not_found", "plugin_pane_not_found", "server_unreachable"} <= GONE_CODES
    assert "timeout" not in GONE_CODES and "bad_response" not in GONE_CODES


def test_move_beside_returns_new_id_and_rejects_unchanged() -> None:
    run, calls = fake_run([
        {"result": {"move_result": {"changed": True, "pane": {"pane_id": "w1:p9"}}}},
        {"result": {"move_result": {"changed": False, "reason": "same_tab", "pane": {"pane_id": "w1:p9"}}}},
    ])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    assert h.move_beside("w2:p1", "w1:t1", "w1:p2", "down", 0.75) == "w1:p9"
    assert calls[0] == ["pane", "move", "w2:p1", "--tab", "w1:t1", "--target-pane", "w1:p2",
                        "--split", "down", "--ratio", "0.7500", "--focus"]
    with pytest.raises(HerdrError):
        h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)


def test_move_beside_same_tab_without_pane_is_not_moved() -> None:
    run, _ = fake_run([{"result": {"move_result": {"changed": False, "reason": "same_tab"}}},
                       {"result": {"move_result": {"changed": True, "pane": {}}}},
                       {"result": {}}])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    with pytest.raises(HerdrError) as info:
        h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
    assert info.value.code == "not_moved" and "same_tab" in str(info.value)
    with pytest.raises(HerdrError) as info:
        h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
    assert info.value.code == "bad_response"
    with pytest.raises(HerdrError) as info:
        h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
    assert info.value.code == "not_moved"


def test_open_viewer_in_new_tab_finds_pane_id_in_plugin_pane_result() -> None:
    opened = {"result": {"plugin_pane": {"entrypoint": "p",
                                         "pane": {"pane_id": "w1:p2", "tab_id": "w1:t2"},
                                         "plugin_id": "repetui"},
                         "type": "plugin_pane_opened"}}
    run, calls = fake_run([opened, {"result": {"type": "plugin_pane_opened"}}])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    assert h.open_viewer_in_new_tab("repetui", "w1") == "w1:p2"
    assert calls[0] == ["plugin", "pane", "open", "--plugin", "repetui", "--entrypoint", "review",
                        "--placement", "tab", "--workspace", "w1", "--no-focus"]
    with pytest.raises(HerdrError) as info:
        h.open_viewer_in_new_tab("repetui", "w1")
    assert info.value.code == "no_pane_id"


def test_cli_error_and_timeout() -> None:
    run, _ = fake_run([HerdrError("pane_not_found", "x")])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("pane", "get", "w1:p1")
    assert info.value.code == "pane_not_found"

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("herdr", 1)

    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=slow).cli("pane", "list")
    assert info.value.code == "timeout"


def test_close_viewer_validates_result() -> None:
    run, _ = fake_run([{"result": {"type": "plugin_pane_closed", "pane_id": "w1:p3"}},
                       {"result": {"type": "something_else", "pane_id": "w1:p3"}},
                       {"result": {"type": "plugin_pane_closed"}},
                       {"result": {"type": "plugin_pane_closed", "pane_id": "w1:p4"}}])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    h.close_viewer("w1:p3", "/tmp/none.sock")
    for _ in range(3):
        with pytest.raises(HerdrError):
            h.close_viewer("w1:p3", "/tmp/none.sock")


def test_close_viewer_on_foreign_socket_uses_raw_request(short_dir) -> None:
    path = str(short_dir / "cv.sock")
    good = b'{"id":"$ID","result":{"type":"plugin_pane_closed","pane_id":"w1:p3"}}\n'
    t = serve_once(path, good)
    try:
        run, calls = fake_run([])
        Herdr("herdr", "/tmp/none.sock", run=run).close_viewer("w1:p3", path)
        assert calls == []
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)


def test_close_viewer_reports_plugin_pane_not_found_as_gone_code() -> None:
    run, _ = fake_run([HerdrError("plugin_pane_not_found", "x")])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).close_viewer("w1:p3", "/tmp/none.sock")
    assert info.value.code in GONE_CODES


def test_process_info_validates_type_and_pane(short_dir) -> None:
    path = str(short_dir / "pi.sock")
    good = b'{"id":"$ID","result":{"type":"pane_process_info","process_info":{"pane_id":"w1:p1","foreground_processes":[{"argv":["zsh"]}]}}}\n'
    t = serve_once(path, good)
    try:
        assert Herdr("herdr", path).foreground_processes("w1:p1") == [{"argv": ["zsh"]}]
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)
    for bad in (b'{"id":"$ID","result":{"type":"other","process_info":{"pane_id":"w1:p1"}}}\n',
                b'{"id":"$ID","result":{"type":"pane_process_info","process_info":{"pane_id":"w9:p9"}}}\n'):
        t = serve_once(path, bad)
        try:
            with pytest.raises(HerdrError):
                Herdr("herdr", path).foreground_processes("w1:p1")
        finally:
            t.join()
            Path(path).unlink(missing_ok=True)


def test_process_info_gone_pane_is_none_but_timeout_raises(short_dir) -> None:
    path = str(short_dir / "pg.sock")
    gone = b'{"id":"$ID","error":{"code":"pane_not_found","message":"no such pane"}}\n'
    t = serve_once(path, gone)
    try:
        assert Herdr("herdr", path).foreground_processes("w1:p1") is None
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)
    t = serve_once(path, b'{"id":"$ID","result":{}}\n', delay=0.6)
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path, timeout=0.2).foreground_processes("w1:p1")
        assert info.value.code == "timeout"
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)
    assert Herdr("herdr", str(short_dir / "missing.sock")).foreground_processes("w1:p1") is None


def test_notify_reports_shown_flag() -> None:
    run, _ = fake_run([{"result": {"type": "notification_show", "shown": False, "reason": "disabled"}},
                       {"result": "weird"}, ["not", "a", "dict"],
                       {"result": {"type": "notification_show", "shown": True}}])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    assert h.notify("t", "b") is False and h.notify("t", "b") is False and h.notify("t", "b") is False
    assert h.notify("t", "b") is True


def test_request_ok(short_dir) -> None:
    path = str(short_dir / "ok.sock")
    t = serve_once(path, b'{"id":"$ID","result":{"ok":true}}\n')
    try:
        assert Herdr("herdr", path).request("pane.focus", {"pane_id": "w1:p2"})["result"] == {"ok": True}
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize(("reply", "code"), [
    (None, "no_response"),
    (b'{"id":"other","result":{}}\n', "bad_response"),
    (b'{"id":"$ID"}\n', "bad_response"),
    (b"not json\n", "bad_response"),
])
def test_request_rejects_bad_envelopes(short_dir, reply, code) -> None:
    path = str(short_dir / "bad.sock")
    t = serve_once(path, reply)
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).request("pane.get", {"pane_id": "w1:p1"})
        assert info.value.code == code
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)


def test_request_surfaces_error_envelope_code(short_dir) -> None:
    path = str(short_dir / "err.sock")
    t = serve_once(path, b'{"id":"$ID","error":{"code":"pane_not_found","message":"gone"}}\n')
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).request("pane.get", {"pane_id": "w1:p1"})
        assert info.value.code == "pane_not_found"
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)


def test_request_timeout_is_not_unreachable(short_dir) -> None:
    path = str(short_dir / "slow.sock")
    t = serve_once(path, b'{"id":"$ID","result":{}}\n', delay=0.6)
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path, timeout=0.2).request("pane.get", {"pane_id": "w1:p1"})
        assert info.value.code == "timeout"
    finally:
        t.join()
        Path(path).unlink(missing_ok=True)


def test_unreachable_server(short_dir) -> None:
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", str(short_dir / "missing.sock")).request("pane.get", {"pane_id": "x"})
    assert info.value.code == "server_unreachable"
