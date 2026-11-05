import json
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from herdr_captures import CAPTURES
from herdr_repetui.herdr import GONE_CODES, Herdr, HerdrError


def real(label: str) -> SimpleNamespace:
    """The verbatim exit code/stdout/stderr herdr 0.9.5 produced (see herdr_captures.py)."""
    capture = CAPTURES[label]
    return SimpleNamespace(returncode=capture["returncode"], stdout=capture["stdout"],
                           stderr=capture["stderr"])


def fake_run(responses):
    """Fake subprocess.run. A response is a `real(...)` capture, a HerdrError (exit 1, JSON on stderr
    like herdr), a str (raw stdout, exit 0) or a dict (JSON stdout, exit 0)."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1:])
        out = responses.pop(0)
        if isinstance(out, SimpleNamespace):
            return out
        if isinstance(out, HerdrError):
            body = {"error": {"code": out.code, "message": "x"}, "id": "cli:test"}
            return SimpleNamespace(returncode=1, stdout="", stderr=json.dumps(body))
        if isinstance(out, str):
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    return run, calls


def herdr_with(*responses):
    run, calls = fake_run(list(responses))
    return Herdr("herdr", "/tmp/none.sock", run=run), calls


def envelope(result_type: str, **fields) -> dict:
    return {"id": "cli:test", "result": {**fields, "type": result_type}}


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

# ---- CLI replies: validated per subcommand against the shapes captured from herdr 0.9.5 -------------

def test_real_pane_list_is_parsed() -> None:
    h, calls = herdr_with(real("pane list"))
    assert h.panes() == [
        {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1"},
        {"pane_id": "w1:p3", "tab_id": "w1:t1", "workspace_id": "w1"},
        {"pane_id": "w1:p4", "tab_id": "w1:t1", "workspace_id": "w1"},
        {"pane_id": "w2:p1", "tab_id": "w2:t1", "workspace_id": "w2"}]
    assert calls == [["pane", "list"]]


def test_real_pane_layout_is_returned() -> None:
    h, calls = herdr_with(real("pane layout"))
    layout = h.layout("w1:p1")
    assert layout["area"] == {"height": 47, "width": 134, "x": 0, "y": 0}
    assert [p["pane_id"] for p in layout["panes"]] == ["w1:p1", "w1:p3", "w1:p4"]
    assert calls == [["pane", "layout", "--pane", "w1:p1"]]


def test_real_plugin_pane_open_yields_the_pane_id() -> None:
    h, calls = herdr_with(real("plugin pane open"))
    assert h.open_viewer_in_new_tab("spike.probe", "w1") == "w1:p7"
    assert calls == [["plugin", "pane", "open", "--plugin", "spike.probe", "--entrypoint", "review",
                      "--placement", "tab", "--workspace", "w1", "--no-focus"]]


def test_real_pane_move_returns_the_kept_id_and_rejects_same_tab() -> None:
    h, calls = herdr_with(real("pane move (cross-tab, changed)"), real("pane move (same tab, unchanged)"))
    assert h.move_beside("w1:p7", "w1:t1", "w1:p1", "down", 0.75) == "w1:p7"
    assert calls[0] == ["pane", "move", "w1:p7", "--tab", "w1:t1", "--target-pane", "w1:p1",
                        "--split", "down", "--ratio", "0.7500", "--focus"]
    with pytest.raises(HerdrError) as info:
        h.move_beside("w1:p7", "w1:t1", "w1:p1", "right", 0.8)
    assert info.value.code == "not_moved" and "same_tab" in str(info.value)


def test_real_plugin_pane_close_and_its_gone_errors() -> None:
    h, calls = herdr_with(real("plugin pane close (plugin pane)"),
                          real("plugin pane close (again: already gone)"),
                          real("plugin pane close (non-plugin pane)"))
    h.close_viewer("w1:p7", h.socket_path)
    assert calls == [["plugin", "pane", "close", "w1:p7"]]
    for _ in range(2):
        with pytest.raises(HerdrError) as info:
            h.close_viewer("w1:p7", h.socket_path)
        assert info.value.code == "plugin_pane_not_found" and info.value.code in GONE_CODES


def test_close_viewer_rejects_a_reply_for_another_pane() -> None:
    h, _ = herdr_with(real("plugin pane close (plugin pane)"))
    with pytest.raises(HerdrError) as info:
        h.close_viewer("w1:p3", h.socket_path)  # the capture closed w1:p7
    assert info.value.code == "bad_response"


def test_real_notification_show_reports_shown() -> None:
    h, calls = herdr_with(real("notification show"))
    assert h.notify("t", "b") is True
    assert calls == [["notification", "show", "t", "--body", "b"]]


def test_notify_reports_a_false_shown_flag() -> None:
    h, _ = herdr_with(envelope("notification_show", shown=False, reason="disabled"),
                      envelope("notification_show", shown="true"),
                      envelope("notification_show", reason="shown"),
                      envelope("something_else", shown=True))
    assert [h.notify("t", "b") for _ in range(4)] == [False, False, False, False]


def test_real_send_keys_success_prints_nothing_and_is_accepted() -> None:
    h, calls = herdr_with(real("pane send-keys (plain shell pane)"))
    assert h.send_keys("w1:p6", "q") is None
    assert calls == [["pane", "send-keys", "w1:p6", "q"]]


def test_real_error_replies_come_from_stderr_with_their_code() -> None:
    h, _ = herdr_with(real("pane send-keys (missing pane)"), real("pane get (missing: error case)"))
    with pytest.raises(HerdrError) as info:
        h.send_keys("w999:p999", "q")
    assert info.value.code == "pane_not_found" and info.value.code in GONE_CODES
    with pytest.raises(HerdrError) as info:
        h.cli("pane", "get", "w999:p999")
    assert info.value.code == "pane_not_found"


def test_cli_error_and_timeout() -> None:
    h, _ = herdr_with(HerdrError("pane_not_found", "x"))
    with pytest.raises(HerdrError) as info:
        h.cli("pane", "get", "w1:p1")
    assert info.value.code == "pane_not_found"

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("herdr", 1)

    with pytest.raises(HerdrError) as info:
        Herdr("herdr", "/tmp/none.sock", run=slow).cli("pane", "list")
    assert info.value.code == "timeout"

    def missing(*a, **k):
        raise FileNotFoundError("herdr")

    with pytest.raises(HerdrError) as info:
        Herdr("nope", "/tmp/none.sock", run=missing).cli("pane", "list")
    assert info.value.code == "no_herdr"


# Every JSON-printing subcommand uses the envelope class: exit 0 and exactly one of result/error.
ENVELOPE_CALLS = {
    "pane list": lambda h: h.panes(),
    "pane layout": lambda h: h.layout("w1:p1"),
    "plugin pane open": lambda h: h.open_viewer_in_new_tab("spike.probe", "w1"),
    "pane move": lambda h: h.move_beside("w1:p7", "w1:t1", "w1:p1", "down", 0.75),
    "plugin pane close": lambda h: h.close_viewer("w1:p7", h.socket_path),
    "raw cli": lambda h: h.cli("pane", "get", "w1:p1"),
}

UNCONFIRMED_STDOUT = {
    "empty": "",
    "whitespace": "  \n",
    "empty object": "{}",
    "unrelated key": '{"unrelated":1}',
    "id only": '{"id":"cli:test"}',
    "result is null": '{"id":"cli:test","result":null}',
    "result is a string": '{"id":"cli:test","result":"ok"}',
    "result is a list": '{"id":"cli:test","result":[]}',
    "both result and error": '{"id":"cli:test","result":{"type":"x"},"error":{"code":"pane_not_found","message":"m"}}',
    "error is a string": '{"id":"cli:test","error":"boom"}',
    "error without code": '{"id":"cli:test","error":{"message":"m"}}',
    "plain text": "No plugins installed.",
    "json list": "[1,2]",
    "json string": '"ok"',
    "json null": "null",
    "truncated json": '{"id":"cli:test","result":{"type":',
}


@pytest.mark.parametrize("stdout", list(UNCONFIRMED_STDOUT.values()), ids=list(UNCONFIRMED_STDOUT))
@pytest.mark.parametrize("call", list(ENVELOPE_CALLS.values()), ids=list(ENVELOPE_CALLS))
def test_envelope_subcommands_reject_unconfirmed_exit_zero_output(call, stdout) -> None:
    h, _ = herdr_with(stdout)
    with pytest.raises(HerdrError) as info:
        call(h)
    assert info.value.code == "bad_response"


@pytest.mark.parametrize("call", list(ENVELOPE_CALLS.values()), ids=list(ENVELOPE_CALLS))
def test_envelope_subcommands_raise_the_code_of_an_exit_zero_error(call) -> None:
    h, _ = herdr_with('{"error":{"code":"pane_not_found","message":"gone"},"id":"cli:test"}')
    with pytest.raises(HerdrError) as info:
        call(h)
    assert info.value.code == "pane_not_found"


@pytest.mark.parametrize("stdout", list(UNCONFIRMED_STDOUT.values())[:6], ids=list(UNCONFIRMED_STDOUT)[:6])
def test_notify_treats_unconfirmed_output_as_not_shown(stdout) -> None:
    h, _ = herdr_with(stdout)
    assert h.notify("t", "b") is False


def test_notify_does_not_hide_transport_errors_or_exit_zero_errors() -> None:
    h, _ = herdr_with(HerdrError("timeout", "x"),
                      '{"error":{"code":"server_unreachable","message":"m"},"id":"cli:test"}')
    for code in ("timeout", "server_unreachable"):
        with pytest.raises(HerdrError) as info:
            h.notify("t", "b")
        assert info.value.code == code


@pytest.mark.parametrize("stdout", [s for k, s in UNCONFIRMED_STDOUT.items() if k not in ("empty", "whitespace")],
                         ids=[k for k in UNCONFIRMED_STDOUT if k not in ("empty", "whitespace")])
def test_send_keys_accepts_only_empty_output(stdout) -> None:
    h, _ = herdr_with(stdout)
    with pytest.raises(HerdrError) as info:
        h.send_keys("w1:p6", "q")
    assert info.value.code == "bad_response"


def test_send_keys_exit_zero_with_error_raises_its_code() -> None:
    h, _ = herdr_with('{"error":{"code":"pane_not_found","message":"gone"},"id":"cli:test"}')
    with pytest.raises(HerdrError) as info:
        h.send_keys("w999:p999", "q")
    assert info.value.code == "pane_not_found"


def test_send_keys_empty_output_with_a_failing_exit_still_raises() -> None:
    h, _ = herdr_with(SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(HerdrError) as info:
        h.send_keys("w1:p6", "q")
    assert info.value.code == "cli_failed"


def test_empty_output_is_only_accepted_when_the_caller_opts_in() -> None:
    h, _ = herdr_with("", "")
    assert h.cli("pane", "send-keys", "w1:p1", "q", empty_ok=True) == {}
    with pytest.raises(HerdrError) as info:
        h.cli("pane", "send-keys", "w1:p1", "q")
    assert info.value.code == "bad_response"


@pytest.mark.parametrize("reply", [
    envelope("pane_info", pane={"pane_id": "w1:p2"}),     # another command's shape
    envelope("plugin_pane_opened_x"),
    {"id": "cli:test", "result": {"plugin_pane": {"pane": {"pane_id": "w1:p2"}}}},   # no type
])
@pytest.mark.parametrize("call", [ENVELOPE_CALLS[k] for k in
                                  ("pane list", "pane layout", "plugin pane open", "pane move",
                                   "plugin pane close")])
def test_envelope_subcommands_check_the_result_type(call, reply) -> None:
    h, _ = herdr_with(reply)
    with pytest.raises(HerdrError) as info:
        call(h)
    assert info.value.code == "bad_response"


@pytest.mark.parametrize("stderr", [
    json.dumps({"error": "boom", "id": "x"}),
    json.dumps({"error": None, "id": "x"}),
    json.dumps({"error": ["pane_not_found"], "id": "x"}),
    json.dumps({"error": {"message": "no code"}, "id": "x"}),
    json.dumps({"error": {"code": "pane_not_found", "message": "x"}, "result": {}, "id": "x"}),
])
def test_cli_failure_with_malformed_or_contradictory_error_is_bad_response(stderr) -> None:
    h, _ = herdr_with(SimpleNamespace(returncode=1, stdout="", stderr=stderr))
    with pytest.raises(HerdrError) as info:
        h.cli("pane", "get", "w1:p1")
    assert info.value.code == "bad_response"


@pytest.mark.parametrize("stderr", ["", "Segmentation fault", "[1,2]", '{"msg":"no error key"}'])
def test_cli_failure_without_error_object_is_cli_failed(stderr) -> None:
    h, _ = herdr_with(SimpleNamespace(returncode=2, stdout="", stderr=stderr))
    with pytest.raises(HerdrError) as info:
        h.cli("pane", "get", "w1:p1")
    assert info.value.code == "cli_failed"


def test_move_beside_rejects_unchanged_and_bad_move_results() -> None:
    def moved(**move) -> dict:
        return envelope("pane_move", move_result=move)

    h, _ = herdr_with(
        moved(changed=False, reason="same_tab", pane={"pane_id": "w1:p9"}),
        moved(changed=False),
        moved(changed=True, pane={}),
        moved(changed=True, pane={"pane_id": ""}),
        moved(changed=True, pane="w1:p9"),
        moved(pane={"pane_id": "w1:p9"}),
        moved(changed="false", pane={"pane_id": "w1:p9"}),
        moved(changed=1, pane={"pane_id": "w1:p9"}),
        moved(changed=None, pane={"pane_id": "w1:p9"}),
        envelope("pane_move", move_result="x"),
        envelope("pane_move"),
    )
    expected = ["not_moved", "not_moved"] + ["bad_response"] * 9
    for code in expected:
        with pytest.raises(HerdrError) as info:
            h.move_beside("w1:p9", "w1:t1", "w1:p2", "down", 0.75)
        assert info.value.code == code


def test_open_viewer_rejects_unconfirmed_pane_ids() -> None:
    def opened(**fields) -> dict:
        return envelope("plugin_pane_opened", **fields)

    h, _ = herdr_with(
        opened(),
        opened(plugin_pane={"pane": {}}),
        opened(plugin_pane={"pane": {"pane_id": ""}}),
        opened(plugin_pane={"pane": {"pane_id": 7}}),
        opened(plugin_pane="w1:p2"),
        opened(nested={"pane": {"pane_id": "w1:p2"}}),     # no recursive search
        envelope("pane_info", pane={"pane_id": "w1:p2"}),   # the pane_info shape of other commands
    )
    for _ in range(7):
        with pytest.raises(HerdrError) as info:
            h.open_viewer_in_new_tab("spike.probe", "w1")
        assert info.value.code == "bad_response"


@pytest.mark.parametrize("panes", [
    None, "w1:p1", {"pane_id": "w1:p1"}, ["w1:p1"], [None], [{"tab_id": "w1:t1"}], [{"pane_id": 7}],
    [{"pane_id": "w1:p1"}, {"pane_id": None}], [{"pane_id": "w1:p1", "tab_id": 3}],
    [{"pane_id": "w1:p1", "workspace_id": ["w1"]}],
])
def test_panes_rejects_a_malformed_listing(panes) -> None:
    h, _ = herdr_with(envelope("pane_list", panes=panes), envelope("pane_list"))
    with pytest.raises(HerdrError) as info:
        h.panes()
    assert info.value.code == "bad_response"
    with pytest.raises(HerdrError) as info:      # panes missing altogether is not an empty listing
        Herdr("herdr", "/tmp/none.sock", run=fake_run([envelope("pane_list")])[0]).panes()
    assert info.value.code == "bad_response"


def test_panes_normalises_optional_ids() -> None:
    h, _ = herdr_with(envelope("pane_list", panes=[{"pane_id": "w2:p1", "extra": 1}]))
    assert h.panes() == [{"pane_id": "w2:p1", "tab_id": None, "workspace_id": None}]


@pytest.mark.parametrize("layout", [
    None, [], "x", {"panes": []}, {"area": {}}, {"area": [], "panes": []}, {"area": {}, "panes": {}},
    {"area": None, "panes": None},
])
def test_layout_rejects_a_malformed_layout(layout) -> None:
    h, _ = herdr_with(envelope("pane_layout", layout=layout), envelope("pane_layout"))
    with pytest.raises(HerdrError) as info:
        h.layout("w1:p1")
    assert info.value.code == "bad_response"
    with pytest.raises(HerdrError) as info:
        h.layout("w1:p1")
    assert info.value.code == "bad_response"


def test_close_viewer_rejects_missing_or_other_pane_ids() -> None:
    h, _ = herdr_with(envelope("plugin_pane_closed"), envelope("plugin_pane_closed", pane_id="w1:p4"),
                      envelope("something_else", pane_id="w1:p3"))
    for _ in range(3):
        with pytest.raises(HerdrError) as info:
            h.close_viewer("w1:p3", h.socket_path)
        assert info.value.code == "bad_response"


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
