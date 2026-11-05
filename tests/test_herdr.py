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
                       {"result": {"move_result": {"changed": True, "pane": {}}}}])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    with pytest.raises(HerdrError) as info:
        h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
    assert info.value.code == "not_moved" and "same_tab" in str(info.value)
    with pytest.raises(HerdrError) as info:
        h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
    assert info.value.code == "bad_response"


@pytest.mark.parametrize("reply", [
    {"result": {}},
    {"result": {"move_result": "x"}},
    {"result": {"move_result": {"pane": {"pane_id": "w1:p9"}}}},                         # no changed
    {"result": {"move_result": {"changed": "false", "pane": {"pane_id": "w1:p9"}}}},     # truthy string
    {"result": {"move_result": {"changed": 1, "pane": {"pane_id": "w1:p9"}}}},
    {"result": {"move_result": {"changed": None, "pane": {"pane_id": "w1:p9"}}}},
    {"result": {"move_result": {"changed": True, "pane": {"pane_id": ""}}}},
    {"result": {"move_result": {"changed": True, "pane": "w1:p9"}}},
    {"result": "weird"},
    {"result": None},
])
def test_move_beside_rejects_unconfirmed_results(reply) -> None:
    run, _ = fake_run([reply])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
    assert info.value.code == "bad_response"


def test_open_viewer_in_new_tab_reads_the_documented_path() -> None:
    opened = {"result": {"plugin_pane": {"entrypoint": "p",
                                         "pane": {"pane_id": "w1:p2", "tab_id": "w1:t2"},
                                         "plugin_id": "repetui"},
                         "type": "plugin_pane_opened"}}
    run, calls = fake_run([opened])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    assert h.open_viewer_in_new_tab("repetui", "w1") == "w1:p2"
    assert calls[0] == ["plugin", "pane", "open", "--plugin", "repetui", "--entrypoint", "review",
                        "--placement", "tab", "--workspace", "w1", "--no-focus"]


@pytest.mark.parametrize("reply", [
    {"result": {"type": "pane_info", "pane": {"pane_id": "w1:p2"}}},                     # some other command's shape
    {"result": {"type": "plugin_pane_opened"}},
    {"result": {"type": "plugin_pane_opened", "plugin_pane": {"pane": {}}}},
    {"result": {"type": "plugin_pane_opened", "plugin_pane": {"pane": {"pane_id": ""}}}},
    {"result": {"type": "plugin_pane_opened", "plugin_pane": {"pane": {"pane_id": 7}}}},
    {"result": {"type": "plugin_pane_opened", "plugin_pane": "w1:p2"}},
    {"result": {"plugin_pane": {"pane": {"pane_id": "w1:p2"}}}},                         # type missing
    {"result": {"type": "other", "plugin_pane": {"pane": {"pane_id": "w1:p2"}}}},
    {"result": {"nested": {"pane": {"pane_id": "w1:p2"}}, "type": "plugin_pane_opened"}},  # no recursive search
    {"result": "weird"},
    {},
])
def test_open_viewer_in_new_tab_rejects_unconfirmed_results(reply) -> None:
    run, _ = fake_run([reply])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).open_viewer_in_new_tab("repetui", "w1")
    assert info.value.code == "bad_response"


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


def reply_once(short_dir, name, reply: bytes):
    path = str(short_dir / f"{name}.sock")
    return path, serve_once(path, reply)


def finish(path, thread):
    thread.join()
    Path(path).unlink(missing_ok=True)


def process_info(processes_json: str | None) -> bytes:
    body = '"pane_id":"w1:p1"' + ("" if processes_json is None else f',"foreground_processes":{processes_json}')
    return ('{"id":"$ID","result":{"type":"pane_process_info","process_info":{' + body + "}}}\n").encode()


@pytest.mark.parametrize("processes", [
    "null", "[7]", '["zsh"]', "[null]", '{"argv":["zsh"]}', '"zsh"', "5",
    '[{"argv":"zsh"}]', '[{"argv":["zsh",1]}]', '[{"argv":[["zsh"]]}]', '[{"argv":{"0":"zsh"}}]',
    '[{"argv":["zsh"]},7]',
])
def test_process_info_rejects_malformed_process_data(short_dir, processes) -> None:
    path, t = reply_once(short_dir, "pm", process_info(processes))
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).foreground_processes("w1:p1")
        assert info.value.code == "bad_response"
    finally:
        finish(path, t)


@pytest.mark.parametrize(("processes", "expected"), [
    (None, []),                                           # key omitted: schema allows it
    ("[]", []),
    ('[{"cwd":"/tmp"}]', [{"cwd": "/tmp"}]),              # argv omitted
    ('[{"argv":null,"cwd":"/tmp"}]', [{"argv": None, "cwd": "/tmp"}]),
    ('[{"argv":["zsh","-l"]},{"argv":[]}]', [{"argv": ["zsh", "-l"]}, {"argv": []}]),
])
def test_process_info_accepts_documented_process_data(short_dir, processes, expected) -> None:
    path, t = reply_once(short_dir, "pa", process_info(processes))
    try:
        assert Herdr("herdr", path).foreground_processes("w1:p1") == expected
    finally:
        finish(path, t)


def test_process_info_result_must_be_an_object(short_dir) -> None:
    path, t = reply_once(short_dir, "pr", b'{"id":"$ID","result":"weird"}\n')
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).foreground_processes("w1:p1")
        assert info.value.code == "bad_response"
    finally:
        finish(path, t)


@pytest.mark.parametrize("reply", [
    b'{"id":"$ID","error":"boom"}\n',
    b'{"id":"$ID","error":null}\n',
    b'{"id":"$ID","error":["pane_not_found"]}\n',
    b'{"id":"$ID","error":{"message":"no code"}}\n',
    b'{"id":"$ID","error":{"code":5,"message":"x"}}\n',
    b'{"id":"$ID","result":"weird"}\n',
    b'{"id":"$ID","result":null}\n',
    b'[1,2]\n',
])
def test_request_normalises_malformed_envelopes(short_dir, reply) -> None:
    path, t = reply_once(short_dir, "rn", reply)
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).request("pane.get", {"pane_id": "w1:p1"})
        assert info.value.code == "bad_response"
    finally:
        finish(path, t)


@pytest.mark.parametrize("reply", [
    b'{"id":"$ID","result":{"type":"pane_process_info"},"error":{"code":"pane_not_found","message":"x"}}\n',
    b'{"id":"$ID","result":{},"error":null}\n',
])
def test_request_with_both_result_and_error_is_bad_response_not_gone(short_dir, reply) -> None:
    path, t = reply_once(short_dir, "both", reply)
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).request("pane.process_info", {"pane_id": "w1:p1"})
        assert info.value.code == "bad_response"
    finally:
        finish(path, t)


def test_contradictory_envelope_never_reads_as_a_gone_pane(short_dir) -> None:
    reply = b'{"id":"$ID","result":{},"error":{"code":"pane_not_found","message":"x"}}\n'
    path, t = reply_once(short_dir, "gone", reply)
    try:
        with pytest.raises(HerdrError) as info:
            Herdr("herdr", path).foreground_processes("w1:p1")  # would be None if read as gone
        assert info.value.code == "bad_response"
    finally:
        finish(path, t)


def test_cli_with_both_result_and_error_is_bad_response() -> None:
    both = {"result": {"type": "plugin_pane_closed", "pane_id": "w1:p3"},
            "error": {"code": "plugin_pane_not_found", "message": "x"}}
    run, _ = fake_run([both])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("plugin", "pane", "close", "w1:p3")
    assert info.value.code == "bad_response"


def test_cli_failure_with_both_result_and_error_is_bad_response() -> None:
    def run(argv, **kwargs):
        body = {"result": {}, "error": {"code": "pane_not_found", "message": "x"}}
        return SimpleNamespace(returncode=1, stdout="", stderr=json.dumps(body))

    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("pane", "get", "w1:p1")
    assert info.value.code == "bad_response"


def test_cli_exit_zero_with_error_member_raises_that_error() -> None:
    run, _ = fake_run([{"error": {"code": "pane_not_found", "message": "gone"}}])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("pane", "get", "w1:p1")
    assert info.value.code == "pane_not_found"


@pytest.mark.parametrize("stderr", [
    json.dumps({"error": "boom"}),
    json.dumps({"error": None}),
    json.dumps({"error": ["pane_not_found"]}),
    json.dumps({"error": {"message": "no code"}}),
])
def test_cli_failure_with_malformed_error_member_is_bad_response(stderr) -> None:
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("pane", "get", "w1:p1")
    assert info.value.code == "bad_response"


@pytest.mark.parametrize("stderr", ["", "Segmentation fault", "[1,2]", '{"msg":"no error key"}'])
def test_cli_failure_without_error_object_is_cli_failed(stderr) -> None:
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr=stderr)

    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("pane", "get", "w1:p1")
    assert info.value.code == "cli_failed"


@pytest.mark.parametrize("stdout", ["not json", "[1,2]", '"text"', "7", "null"])
def test_cli_rejects_non_object_output(stdout) -> None:
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).cli("pane", "list")
    assert info.value.code == "bad_response"


def test_panes_normalises_and_validates() -> None:
    listed = {"result": {"panes": [{"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "extra": 1},
                                   {"pane_id": "w2:p1"}]}}
    run, calls = fake_run([listed])
    assert Herdr("herdr", "/tmp/none.sock", run=run).panes() == [
        {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1"},
        {"pane_id": "w2:p1", "tab_id": None, "workspace_id": None}]
    assert calls == [["pane", "list"]]


@pytest.mark.parametrize("reply", [
    {}, {"result": None}, {"result": "weird"}, {"result": {}},
    {"result": {"panes": None}}, {"result": {"panes": "w1:p1"}}, {"result": {"panes": {"pane_id": "w1:p1"}}},
    {"result": {"panes": ["w1:p1"]}}, {"result": {"panes": [None]}},
    {"result": {"panes": [{"tab_id": "w1:t1"}]}},
    {"result": {"panes": [{"pane_id": 7}]}},
    {"result": {"panes": [{"pane_id": "w1:p1"}, {"pane_id": None}]}},
    {"result": {"panes": [{"pane_id": "w1:p1", "tab_id": 3}]}},
    {"result": {"panes": [{"pane_id": "w1:p1", "workspace_id": ["w1"]}]}},
])
def test_panes_rejects_malformed_listing(reply) -> None:
    run, _ = fake_run([reply])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).panes()
    assert info.value.code == "bad_response"


def test_layout_returns_the_layout_object() -> None:
    layout = {"area": {"width": 100, "height": 40}, "panes": [{"pane_id": "w1:p1"}], "splits": []}
    run, calls = fake_run([{"result": {"layout": layout}}])
    assert Herdr("herdr", "/tmp/none.sock", run=run).layout("w1:p1") == layout
    assert calls == [["pane", "layout", "--pane", "w1:p1"]]


@pytest.mark.parametrize("reply", [
    {}, {"result": None}, {"result": "weird"}, {"result": {}},
    {"result": {"layout": None}}, {"result": {"layout": []}}, {"result": {"layout": "x"}},
    {"result": {"layout": {"panes": []}}},                               # no area
    {"result": {"layout": {"area": {}}}},                                # no panes
    {"result": {"layout": {"area": [], "panes": []}}},
    {"result": {"layout": {"area": {}, "panes": {}}}},
    {"result": {"layout": {"area": None, "panes": None}}},
])
def test_layout_rejects_malformed_layout(reply) -> None:
    run, _ = fake_run([reply])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).layout("w1:p1")
    assert info.value.code == "bad_response"


def test_close_viewer_rejects_malformed_and_contradictory_cli_replies() -> None:
    run, _ = fake_run([{"result": "weird"}, {},
                       {"result": {"type": "plugin_pane_closed", "pane_id": "w1:p3"},
                        "error": {"code": "plugin_pane_not_found", "message": "x"}}])
    h = Herdr("herdr", "/tmp/none.sock", run=run)
    for _ in range(3):
        with pytest.raises(HerdrError) as info:
            h.close_viewer("w1:p3", "/tmp/none.sock")
        assert info.value.code == "bad_response"


def test_notify_still_raises_transport_errors_but_not_shape_errors() -> None:
    run, _ = fake_run([HerdrError("timeout", "x")])
    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=run).notify("t", "b")
    assert info.value.code == "timeout"
