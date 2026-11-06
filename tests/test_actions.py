import pytest

from herdr_repetui import actions
from herdr_repetui.actions import Context, quit_repetui, startup, toggle
from herdr_repetui.config import load_config
from herdr_repetui.herdr import HerdrError
from herdr_repetui.state import Viewer, load_state, save_state

ROOT = "/plugins/repetui"


class FakeHerdr:
    """Two servers' worth of panes; viewer panes start 'pending' (launch.py) until attach() runs."""

    def __init__(self, sock: str, socket_path: str = "/tmp/srv-a.sock") -> None:
        self.sock, self.socket_path = sock, socket_path
        self.calls: list[tuple] = []
        self.listed: list[str] = []                      # sockets whose panes were listed
        self.base = {(socket_path, "w1:p2"): "w1:t1", (socket_path, "w2:p1"): "w2:t1"}
        self.viewers: dict[tuple[str, str], dict] = {}
        self.next_id, self.fail_move, self.shown, self.attach_after = 10, False, True, 0

    def _proc(self, state):
        if state == "pending":
            return [{"argv": ["python3", "launch.py"], "cwd": ROOT}]          # before re-exec
        if state == "pending-owned":
            return [{"argv": ["python3", f"{ROOT}/launch.py", "--owner", self.sock], "cwd": ROOT}]
        return [{"argv": ["dtach", "-a", self.sock, "-E", "-z", "-r", "ctrl_l"], "cwd": "/"}]

    def panes(self):
        return self._list(self.socket_path)

    def panes_on(self, socket_path):
        self.listed.append(socket_path)
        return self._list(socket_path)

    def _list(self, socket_path):
        out = [{"pane_id": p, "tab_id": t, "workspace_id": t.split(":")[0]}
               for (s, p), t in self.base.items() if s == socket_path]
        out += [{"pane_id": p, "tab_id": v["tab"], "workspace_id": v["tab"].split(":")[0]}
                for (s, p), v in self.viewers.items() if s == socket_path]
        return out

    def layout(self, pane_id):
        return {"area": {"width": 264, "height": 74},
                "panes": [{"pane_id": pane_id, "rect": {"width": 264, "height": 74}}]}

    def open_viewer_in_new_tab(self, plugin_id, workspace_id):
        self.next_id += 1
        pane = f"{workspace_id}:p{self.next_id}"
        self.viewers[(self.socket_path, pane)] = {"tab": f"{workspace_id}:tmp", "state": "pending", "polls": 0}
        self.calls.append(("open", workspace_id))
        return pane

    def move_beside(self, pane_id, tab_id, target, direction, ratio):
        if self.fail_move:
            raise HerdrError("not_moved", "same_tab")
        self.calls.append(("move", pane_id, tab_id, target, direction))
        viewer = self.viewers.pop((self.socket_path, pane_id))
        viewer["tab"] = tab_id
        self.viewers[(self.socket_path, pane_id + "m")] = viewer
        return pane_id + "m"

    def close_viewer(self, pane_id, socket_path):
        self.calls.append(("close", pane_id, socket_path))
        if (socket_path, pane_id) not in self.viewers:
            raise HerdrError("pane_not_found", pane_id)
        del self.viewers[(socket_path, pane_id)]

    def focus(self, pane_id):
        self.calls.append(("focus", pane_id))

    def foreground_processes(self, pane_id, socket_path=None):
        viewer = self.viewers.get((socket_path or self.socket_path, pane_id))
        if viewer is None:
            return None if (socket_path or self.socket_path, pane_id) not in self.base else [
                {"argv": ["zsh"], "cwd": "/"}]
        viewer["polls"] += 1
        if viewer["state"] in {"pending", "pending-owned"} and viewer["polls"] > self.attach_after:
            viewer["state"] = "dtach"
        return self._proc(viewer["state"])

    def notify(self, title, body):
        self.calls.append(("notify", title))
        return self.shown

    def send_keys(self, pane_id, *keys):
        self.calls.append(("keys", pane_id, keys))


@pytest.fixture
def env(short_dir, monkeypatch):
    (short_dir / "c.toml").write_text(f'state_dir = "{short_dir}"\nsocket = "{short_dir}/r.sock"\n')
    config = load_config({"HERDR_REPETUI_CONFIG": str(short_dir / "c.toml")})
    herdr = FakeHerdr(str(config.socket))
    monkeypatch.setattr(actions, "socket_state", lambda p: "live")
    # notices go through notify_user: keep them hermetic (no real herdr config, never osascript)
    herdr.mac = []
    monkeypatch.setattr(actions, "herdr_toasts_visible", lambda env=None: True)
    monkeypatch.setattr(actions, "_mac_notify", lambda t, b: herdr.mac.append((t, b)))
    ctx = Context("w1", "w1:t1", "w1:p2", herdr.socket_path, ROOT)
    yield config, herdr, ctx
    for path in (config.lock_path, config.state_path):
        path.unlink(missing_ok=True)


def kinds(herdr):
    return [c[0] for c in herdr.calls]


def test_show_when_nothing_is_open(env) -> None:
    config, herdr, ctx = env
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert kinds(herdr) == ["open", "move"] and herdr.calls[1][2:] == ("w1:t1", "w1:p2", "down")
    viewer, last = load_state(config.state_path)
    assert viewer.placement == "bottom" and viewer.origin_pane_id == "w1:p2" and last == "bottom"


def test_same_key_hides_and_focuses_origin(env) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    herdr.calls.clear()
    assert toggle("bottom", ctx, herdr, config) == "hidden"
    assert kinds(herdr) == ["close", "focus"] and herdr.calls[1] == ("focus", "w1:p2")
    assert load_state(config.state_path)[0] is None


def test_pending_launch_counts_as_the_viewer(env) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 99                     # launcher never execs dtach during this test
    toggle("bottom", ctx, herdr, config)
    herdr.calls.clear()
    toggle("side", ctx, herdr, config)
    assert kinds(herdr) == ["close", "open", "move"]
    assert len(herdr.viewers) == 1


def test_switching_while_the_viewer_has_focus_targets_the_origin(env) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    viewer, _ = load_state(config.state_path)
    from_viewer = Context("w1", "w1:t1", viewer.pane_id, herdr.socket_path, ROOT)
    herdr.calls.clear()
    assert toggle("side", from_viewer, herdr, config) == "shown"
    move = [c for c in herdr.calls if c[0] == "move"][0]
    assert move[3] == "w1:p2" and move[4] == "right"


def test_no_surviving_target_only_hides(env) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    viewer, _ = load_state(config.state_path)
    del herdr.base[(herdr.socket_path, "w1:p2")]          # origin closed; viewer is the tab's last pane
    from_viewer = Context("w1", "w1:t1", viewer.pane_id, herdr.socket_path, ROOT)
    herdr.calls.clear()
    assert toggle("side", from_viewer, herdr, config) == "hidden"
    assert "open" not in kinds(herdr) and ("notify", "repetui") in herdr.calls


def test_viewer_in_another_tab_is_brought_here(env) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    herdr.calls.clear()
    assert toggle("bottom", Context("w2", "w2:t1", "w2:p1", herdr.socket_path, ROOT), herdr, config) == "shown"
    assert kinds(herdr) == ["close", "open", "move"]


def test_viewer_in_another_server_is_closed_there(env) -> None:
    config, herdr, ctx = env
    save_state(config.state_path, Viewer("/tmp/srv-b.sock", "w5:p1", "w5:t1", "w5:p0", "bottom"), "bottom")
    herdr.viewers[("/tmp/srv-b.sock", "w5:p1")] = {"tab": "w5:t1", "state": "dtach", "polls": 5}
    toggle("bottom", ctx, herdr, config)
    assert ("close", "w5:p1", "/tmp/srv-b.sock") in herdr.calls


def test_unknown_close_error_keeps_record(env, monkeypatch) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    monkeypatch.setattr(herdr, "close_viewer", lambda *a: (_ for _ in ()).throw(HerdrError("timeout", "")))
    with pytest.raises(HerdrError):
        toggle("bottom", ctx, herdr, config)
    assert load_state(config.state_path)[0] is not None


def test_unrecorded_owned_launcher_is_adopted(env) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    herdr.viewers[(herdr.socket_path, "w3:p4")] = {"tab": "w3:t1", "state": "pending-owned", "polls": 2}
    toggle("side", ctx, herdr, config)
    assert ("close", "w3:p4", herdr.socket_path) in herdr.calls


def test_foreign_launcher_is_not_adopted(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    herdr.viewers[(herdr.socket_path, "w3:p6")] = {"tab": "w3:t1", "state": "foreign", "polls": 2}
    monkeypatch.setattr(herdr, "_proc", lambda state: (
        [{"argv": ["python3", "launch.py", "--owner", "/private/tmp/other.sock"], "cwd": ROOT}]
        if state == "foreign" else FakeHerdr._proc(herdr, state)))
    toggle("side", ctx, herdr, config)
    assert "w3:p6" not in [c[1] for c in herdr.calls if c[0] == "close"]


def test_interrupted_tokenless_launch_is_awaited_then_adopted(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    monkeypatch.setattr(actions, "_sleep", lambda s: None)
    herdr.viewers[(herdr.socket_path, "w3:p7")] = {"tab": "w3:t1", "state": "pending", "polls": 0}
    reads = {"n": 0}
    real = herdr.foreground_processes

    def fp(pane_id, socket_path=None):
        if pane_id == "w3:p7":
            reads["n"] += 1
            if reads["n"] >= 3:                       # re-exec happened: token now present
                herdr.viewers[(herdr.socket_path, "w3:p7")]["state"] = "pending-owned"
        return real(pane_id, socket_path)

    monkeypatch.setattr(herdr, "foreground_processes", fp)
    toggle("side", ctx, herdr, config)
    assert ("close", "w3:p7", herdr.socket_path) in herdr.calls
    assert len(herdr.viewers) == 1


def test_missing_argv_while_resolving_keeps_polling(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    monkeypatch.setattr(actions, "_sleep", lambda s: None)
    herdr.viewers[(herdr.socket_path, "w3:p9")] = {"tab": "w3:t1", "state": "pending", "polls": 0}
    sequence = iter(["pending", "no-argv", "no-argv", "pending-owned"])
    real = herdr.foreground_processes

    def fp(pane_id, socket_path=None):
        if pane_id != "w3:p9":
            return real(pane_id, socket_path)
        state = next(sequence, "pending-owned")
        return [{"name": "python3"}] if state == "no-argv" else herdr._proc(state)

    monkeypatch.setattr(herdr, "foreground_processes", fp)
    toggle("side", ctx, herdr, config)
    assert ("close", "w3:p9", herdr.socket_path) in herdr.calls
    assert len(herdr.viewers) == 1


def test_never_identified_launcher_aborts_the_action(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    ticks = iter(range(1000))
    monkeypatch.setattr(actions, "_sleep", lambda s: None)
    monkeypatch.setattr(actions, "_clock", lambda: next(ticks))
    herdr.viewers[(herdr.socket_path, "w3:p8")] = {"tab": "w3:t1", "state": "pending", "polls": 0}
    with pytest.raises(HerdrError) as info:
        toggle("side", ctx, herdr, config)
    assert info.value.code == "ownership_unresolved"
    assert "open" not in kinds(herdr)


def test_process_info_without_argv_keeps_the_record(env, monkeypatch) -> None:
    # Fix round 1 ruling: argv-less process info for the recorded viewer is unresolved -> poll, then
    # abort with ownership_unresolved; the record is kept and nothing is opened or closed.
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    recorded = load_state(config.state_path)[0]
    ticks = iter(range(1000))
    monkeypatch.setattr(actions, "_sleep", lambda s: None)
    monkeypatch.setattr(actions, "_clock", lambda: next(ticks))
    monkeypatch.setattr(herdr, "foreground_processes", lambda pane, socket_path=None: [{"name": "dtach"}])
    herdr.calls.clear()
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "ownership_unresolved"
    assert load_state(config.state_path)[0] == recorded and kinds(herdr) == []


def test_failed_move_closes_the_new_viewer(env) -> None:
    config, herdr, ctx = env
    herdr.fail_move = True
    with pytest.raises(HerdrError):
        toggle("bottom", ctx, herdr, config)
    assert kinds(herdr)[-1] == "close" and not herdr.viewers
    assert load_state(config.state_path)[0] is None


def test_quit_not_running(env, monkeypatch) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    assert quit_repetui(ctx, herdr, config) == "not_running"


def test_quit_shows_here_waits_for_attach_then_sends_q(env) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 3
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "quit_sent"
    assert kinds(herdr)[:2] == ["open", "move"] and herdr.calls[-1][0] == "keys"
    assert herdr.calls[-1][2] == ("q",)


def test_quit_focuses_existing_viewer_in_this_tab(env) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    viewer, _ = load_state(config.state_path)
    herdr.calls.clear()
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "quit_sent"
    assert herdr.calls[0] == ("focus", viewer.pane_id) and herdr.calls[-1] == ("keys", viewer.pane_id, ("q",))


def test_quit_attach_timeout(env) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    ticks = iter(range(1000))
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None, clock=lambda: next(ticks), wait=5) \
        == "attach_timeout"
    assert "keys" not in kinds(herdr)


def test_startup_warning_falls_back_to_macos(env, monkeypatch) -> None:
    config, herdr, ctx = env
    sent = []
    mac = lambda t, b: sent.append(t)
    assert startup(herdr, config, notify_mac=mac, toasts_visible=lambda: True) == "warned_herdr"
    assert sent == []
    assert startup(herdr, config, notify_mac=mac, toasts_visible=lambda: False) == "warned_macos"
    assert ("notify", "repetui is running in the background") not in herdr.calls[1:]
    herdr.shown = False
    assert startup(herdr, config, notify_mac=mac, toasts_visible=lambda: True) == "warned_macos"
    monkeypatch.setattr(herdr, "notify", lambda *a: (_ for _ in ()).throw(HerdrError("timeout", "")))
    assert startup(herdr, config, notify_mac=mac, toasts_visible=lambda: True) == "warned_macos"
    assert len(sent) == 3
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    assert startup(herdr, config, notify_mac=mac) == "idle"


@pytest.mark.parametrize(("toml", "visible"), [
    (None, False), ("", False), ('[ui.toast]\ndelivery = "off"\n', False),
    ('[ui.toast]\ndelivery = "herdr"\n', True), ('[ui.toast]\ndelivery = "system"\n', True),
    ("not = [valid", False),
])
def test_herdr_toasts_visible_reads_delivery(tmp_path, toml, visible) -> None:
    if toml is not None:
        (tmp_path / "herdr").mkdir()
        (tmp_path / "herdr" / "config.toml").write_text(toml)
    assert actions.herdr_toasts_visible({"XDG_CONFIG_HOME": str(tmp_path)}) is visible
    if toml is not None:
        assert actions.herdr_toasts_visible(
            {"HERDR_CONFIG_PATH": str(tmp_path / "herdr" / "config.toml")}) is visible


# --- additions beyond the brief (adaptations recorded in the Task 19 report) ---

SOCK = "/private/tmp/hr-x/r.sock"


def test_is_attached_and_is_viewer_need_our_socket(tmp_path) -> None:
    from pathlib import Path
    sock = Path(SOCK)
    ours = [{"argv": ["/opt/homebrew/bin/dtach", "-a", SOCK, "-E", "-z", "-r", "ctrl_l"], "cwd": "/"}]
    other = [{"argv": ["dtach", "-a", "/private/tmp/other.sock"], "cwd": "/"}]
    owner = [{"argv": ["python3", f"{ROOT}/launch.py", "--owner", SOCK], "cwd": "/elsewhere"}]
    tokenless = [{"argv": ["python3", "launch.py"], "cwd": ROOT}]
    assert actions.is_attached(ours, sock) and actions.is_viewer(ours, sock, ROOT)
    assert not actions.is_attached(other, sock) and not actions.is_viewer(other, sock, ROOT)
    assert actions.is_viewer(owner, sock, ROOT) and not actions.is_attached(owner, sock)
    assert not actions.is_viewer(tokenless, sock, ROOT)              # adoption needs the token
    assert actions.is_viewer(tokenless, sock, ROOT, recorded=True)   # the record proves ownership
    assert not actions.is_viewer([{"argv": ["python3", "launch.py"], "cwd": "/x"}], sock, ROOT,
                                 recorded=True)
    assert not actions.is_viewer(None, sock, ROOT) and not actions.is_attached(None, sock)


def test_context_from_env_reads_plugin_context_with_fallbacks() -> None:
    ctx = actions.context_from_env({
        "HERDR_PLUGIN_CONTEXT_JSON": '{"workspace_id":"w1","tab_id":"w1:t1","focused_pane_id":"w1:p3"}',
        "HERDR_SOCKET_PATH": "/s.sock", "HERDR_PLUGIN_ROOT": ROOT})
    assert ctx == Context("w1", "w1:t1", "w1:p3", "/s.sock", ROOT)
    ctx = actions.context_from_env({"HERDR_PLUGIN_CONTEXT_JSON": "not json", "HERDR_WORKSPACE_ID": "w2",
                                    "HERDR_TAB_ID": "w2:t1", "HERDR_PANE_ID": "w2:p1"})
    assert (ctx.workspace_id, ctx.tab_id, ctx.focused_pane_id) == ("w2", "w2:t1", "w2:p1")


@pytest.mark.parametrize("content", [
    "[1, 2]", '"x"', '{"viewer": [1]}', '{"viewer": {"pane_id": "p"}}',
    '{"viewer": {"server_socket": "/s", "pane_id": 7, "tab_id": "t", "origin_pane_id": "", "placement": "bottom"}}',
    '{"viewer": {"server_socket": "/s", "pane_id": "p", "tab_id": "t", "origin_pane_id": "", "placement": "up"}}',
    '{"viewer": null, "last_placement": []}', '{"viewer": null, "last_placement": {}}',
    '{"viewer": null, "last_placement": 5}',
])
def test_corrupt_state_reads_as_no_record(short_dir, content) -> None:
    path = short_dir / "s.json"
    path.write_text(content)
    assert load_state(path) == (None, "bottom")


def test_state_round_trip(short_dir) -> None:
    path = short_dir / "s.json"
    viewer = Viewer("/s.sock", "w1:p9", "w1:t1", "w1:p2", "side")
    save_state(path, viewer, "side")
    assert load_state(path) == (viewer, "side")
    save_state(path, None, "bottom")
    assert load_state(path) == (None, "bottom")


def test_hide_ignores_a_gone_origin_but_surfaces_other_focus_errors(env, monkeypatch) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    monkeypatch.setattr(herdr, "focus", lambda p: (_ for _ in ()).throw(HerdrError("pane_not_found", p)))
    assert toggle("bottom", ctx, herdr, config) == "hidden"
    toggle("bottom", ctx, herdr, config)
    monkeypatch.setattr(herdr, "focus", lambda p: (_ for _ in ()).throw(HerdrError("timeout", p)))
    with pytest.raises(HerdrError):
        toggle("bottom", ctx, herdr, config)
    assert load_state(config.state_path)[0] is None and not herdr.viewers   # the hide itself happened


def test_failed_cleanup_close_keeps_the_move_error(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.fail_move = True
    monkeypatch.setattr(herdr, "close_viewer", lambda *a: (_ for _ in ()).throw(HerdrError("timeout", "")))
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "not_moved"
    # fix round 2: not confirmed closed, so the pending marker stays for the next action's scan
    assert load_pending(config.state_path) is not None and load_state(config.state_path)[0] is None
    assert len(herdr.viewers) == 1


@pytest.mark.parametrize("layout", [
    {"area": {}, "panes": []},
    {"area": {"width": 264, "height": "74"}, "panes": []},
    {"area": {"width": 264, "height": 74}, "panes": [{"pane_id": "w1:p2", "rect": {"width": 264}}]},
    {"area": {"width": 264, "height": 74}, "panes": ["junk"]},
])
def test_malformed_layout_is_a_herdr_error(env, monkeypatch, layout) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(herdr, "layout", lambda pane_id: layout)
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "bad_response" and "open" not in kinds(herdr)


def test_notices_fall_back_to_macos_when_toasts_are_off(env, monkeypatch) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(actions, "herdr_toasts_visible", lambda env=None: False)
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    assert quit_repetui(ctx, herdr, config) == "not_running"
    assert "notify" not in kinds(herdr) and herdr.mac[0][0] == "repetui is not running"


def test_quit_stops_waiting_when_the_viewer_pane_disappears(env) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    real = herdr.foreground_processes
    herdr.foreground_processes = lambda pane, socket_path=None: (
        None if pane.endswith("m") else real(pane, socket_path))
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "attach_timeout"
    assert "keys" not in kinds(herdr) and load_state(config.state_path)[0] is None


def test_quit_without_target_reports_no_target(env) -> None:
    config, herdr, ctx = env
    del herdr.base[(herdr.socket_path, "w1:p2")]
    assert quit_repetui(ctx, herdr, config) == "no_target"
    assert ("notify", "repetui") in herdr.calls and "open" not in kinds(herdr)


def test_quit_brings_a_viewer_from_another_tab_at_its_last_placement(env) -> None:
    config, herdr, ctx = env
    toggle("side", Context("w2", "w2:t1", "w2:p1", herdr.socket_path, ROOT), herdr, config)
    herdr.calls.clear()
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "quit_sent"
    move = [c for c in herdr.calls if c[0] == "move"][0]
    assert kinds(herdr)[:3] == ["close", "open", "move"] and move[2:] == ("w1:t1", "w1:p2", "right")
    assert len(herdr.viewers) == 1


def _toggle_script():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "toggle.py"
    spec = importlib.util.spec_from_file_location("toggle_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_toggle_script_reports_failures_and_rejects_unknown_commands(short_dir, monkeypatch) -> None:
    script = _toggle_script()
    reported = []
    monkeypatch.setattr(script, "_report", lambda herdr, message: reported.append(message))
    monkeypatch.setenv("HERDR_REPETUI_CONFIG", str(short_dir / "missing.toml"))
    assert script.main(["toggle.py", "toggle-bottom"]) == 1
    assert reported and reported[0].startswith("toggle-bottom failed:")
    assert script.main(["toggle.py", "bogus"]) == 2


# --- fix round 1 ---

from herdr_repetui.state import Pending, load_pending  # noqa: E402

SRV_B = "/tmp/srv-b.sock"


@pytest.fixture
def no_wait(monkeypatch):
    ticks = iter(range(10**6))
    monkeypatch.setattr(actions, "_sleep", lambda s: None)
    monkeypatch.setattr(actions, "_clock", lambda: next(ticks))


def _procs_for(herdr, monkeypatch, pane_id, *answers):
    """Make one pane report `answers` in turn (the last one repeats); count the reads."""
    real, seq, reads = herdr.foreground_processes, list(answers), {"n": 0}

    def fp(pane, socket_path=None):
        if pane != pane_id:
            return real(pane, socket_path)
        reads["n"] += 1
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(herdr, "foreground_processes", fp)
    return reads


def test_recorded_viewer_with_empty_process_list_aborts_without_clearing(env, monkeypatch, no_wait) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    recorded = load_state(config.state_path)[0]
    reads = _procs_for(herdr, monkeypatch, recorded.pane_id, [])
    herdr.calls.clear()
    with pytest.raises(HerdrError) as info:
        toggle("side", ctx, herdr, config)
    assert info.value.code == "ownership_unresolved" and reads["n"] > 1
    assert load_state(config.state_path)[0] == recorded and kinds(herdr) == []


def test_recorded_viewer_without_argv_is_polled_until_resolved(env, monkeypatch, no_wait) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    recorded = load_state(config.state_path)[0]
    reads = _procs_for(herdr, monkeypatch, recorded.pane_id, [{"name": "python3"}], [],
                       herdr._proc("dtach"))
    herdr.calls.clear()
    assert toggle("bottom", ctx, herdr, config) == "hidden"
    assert reads["n"] == 3 and kinds(herdr) == ["close", "focus"]


def test_recorded_viewer_confirmed_gone_still_clears(env, monkeypatch) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    recorded = load_state(config.state_path)[0]
    _procs_for(herdr, monkeypatch, recorded.pane_id, None)
    del herdr.viewers[(herdr.socket_path, recorded.pane_id)]
    herdr.calls.clear()
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert kinds(herdr) == ["open", "move"]


def test_scan_pane_without_argv_aborts(env, monkeypatch, no_wait) -> None:
    config, herdr, ctx = env
    herdr.base[(herdr.socket_path, "w3:p5")] = "w3:t1"
    reads = _procs_for(herdr, monkeypatch, "w3:p5", [{"name": "python3", "argv": None}])
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "ownership_unresolved" and reads["n"] > 1
    assert "open" not in kinds(herdr)


def test_scan_pane_with_empty_process_list_is_skipped(env, monkeypatch, no_wait) -> None:
    config, herdr, ctx = env
    herdr.base[(herdr.socket_path, "w3:p5")] = "w3:t1"
    reads = _procs_for(herdr, monkeypatch, "w3:p5", [])
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert reads["n"] == 1 and "w3:p5" not in [c[1] for c in herdr.calls if c[0] == "close"]


def _server_b(herdr):
    """A second herdr server sharing the same world of panes."""
    other = FakeHerdr(herdr.sock, SRV_B)
    other.viewers, other.base = herdr.viewers, herdr.base
    other.base[(SRV_B, "w1:p1")] = "w1:t1"
    other.next_id = 50
    return other


def test_show_writes_the_pending_marker_before_open_and_replaces_it(env, monkeypatch) -> None:
    config, herdr, ctx = env
    seen = []
    real_open = herdr.open_viewer_in_new_tab

    def open_(plugin_id, workspace_id):
        seen.append((load_state(config.state_path)[0], load_pending(config.state_path)))
        return real_open(plugin_id, workspace_id)

    monkeypatch.setattr(herdr, "open_viewer_in_new_tab", open_)
    toggle("bottom", ctx, herdr, config)
    assert seen[0][0] is None and seen[0][1].server_socket == herdr.socket_path
    assert load_pending(config.state_path) is None and load_state(config.state_path)[0] is not None


def test_interrupted_show_on_server_a_then_toggle_on_server_b_leaves_one_viewer(env, monkeypatch) -> None:
    config, herdr, ctx = env

    def interrupted(*a):
        raise KeyboardInterrupt                       # the action dies between open and save

    monkeypatch.setattr(herdr, "move_beside", interrupted)
    with pytest.raises(KeyboardInterrupt):
        toggle("bottom", ctx, herdr, config)
    assert load_state(config.state_path)[0] is None and load_pending(config.state_path) is not None
    assert len(herdr.viewers) == 1                     # the orphan in server A's temp tab
    orphan = next(iter(herdr.viewers))
    b = _server_b(herdr)
    assert toggle("bottom", Context("w1", "w1:t1", "w1:p1", SRV_B, ROOT), b, config) == "shown"
    assert herdr.socket_path in b.listed
    assert ("close", orphan[1], herdr.socket_path) in b.calls
    assert len(herdr.viewers) == 1 and next(iter(herdr.viewers))[0] == SRV_B
    assert load_pending(config.state_path) is None
    assert load_state(config.state_path)[0].server_socket == SRV_B


def test_pending_orphan_close_timeout_keeps_record_and_marker(env, monkeypatch) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(herdr, "move_beside", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        toggle("bottom", ctx, herdr, config)
    orphan = next(iter(herdr.viewers))
    b = _server_b(herdr)
    monkeypatch.setattr(b, "close_viewer", lambda *a: (_ for _ in ()).throw(HerdrError("timeout", "")))
    with pytest.raises(HerdrError):
        toggle("bottom", Context("w1", "w1:t1", "w1:p1", SRV_B, ROOT), b, config)
    viewer, _ = load_state(config.state_path)
    assert (viewer.server_socket, viewer.pane_id) == orphan
    assert load_pending(config.state_path) is not None and "open" not in kinds(b)


def test_pending_marker_with_unreachable_server_is_cleared(env, monkeypatch) -> None:
    config, herdr, ctx = env
    save_state(config.state_path, None, "side", pending=Pending("/tmp/srv-gone.sock", 1.0))
    real = herdr.panes_on

    tried = []

    def panes_on(socket_path):
        tried.append(socket_path)
        if socket_path == "/tmp/srv-gone.sock":
            raise HerdrError("server_unreachable", socket_path)
        return real(socket_path)

    monkeypatch.setattr(herdr, "panes_on", panes_on)
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert tried == ["/tmp/srv-gone.sock"]
    assert load_pending(config.state_path) is None and len(herdr.viewers) == 1


def test_pending_marker_scan_error_keeps_marker_and_aborts(env, monkeypatch) -> None:
    config, herdr, ctx = env
    save_state(config.state_path, None, "side", pending=Pending(SRV_B, 1.0))
    monkeypatch.setattr(herdr, "panes_on", lambda s: (_ for _ in ()).throw(HerdrError("bad_response", s)))
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "bad_response"
    assert load_pending(config.state_path) == Pending(SRV_B, 1.0) and kinds(herdr) == []


def test_pending_marker_scan_with_no_orphan_is_cleared(env) -> None:
    config, herdr, ctx = env
    b = _server_b(herdr)
    save_state(config.state_path, None, "side", pending=Pending(SRV_B, 1.0))
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert SRV_B in herdr.listed and "close" not in kinds(herdr)
    assert load_pending(config.state_path) is None
    assert b.viewers is herdr.viewers and len(herdr.viewers) == 1


def test_quit_settles_a_pending_marker_first(env, monkeypatch) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(herdr, "move_beside", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        toggle("bottom", ctx, herdr, config)
    monkeypatch.undo()
    monkeypatch.setattr(actions, "socket_state", lambda p: "live")
    herdr.calls.clear()
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "quit_sent"
    assert kinds(herdr)[:3] == ["close", "open", "move"] and len(herdr.viewers) == 1


def test_adopted_viewer_close_timeout_keeps_it_recorded(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6
    herdr.viewers[(herdr.socket_path, "w3:p4")] = {"tab": "w3:t1", "state": "pending-owned", "polls": 2}
    monkeypatch.setattr(herdr, "close_viewer", lambda *a: (_ for _ in ()).throw(HerdrError("timeout", "")))
    with pytest.raises(HerdrError):
        toggle("side", ctx, herdr, config)
    viewer, _ = load_state(config.state_path)
    assert (viewer.server_socket, viewer.pane_id) == (herdr.socket_path, "w3:p4")
    assert "open" not in kinds(herdr)


@pytest.mark.parametrize("pending", [
    "[]", '"x"', "5", '{"server_socket": "/s"}', '{"server_socket": 3, "started_at": 1}',
    '{"server_socket": "", "started_at": 1}', '{"server_socket": "/s", "started_at": "1"}',
    '{"server_socket": "/s", "started_at": true}', '{"server_socket": "/s", "started_at": 1, "x": 1}',
    # fix round 2: never crash, reject non-finite and out-of-range timestamps
    '{"server_socket": "/s", "started_at": %s}' % ("1" + "0" * 400),
    '{"server_socket": "/s", "started_at": NaN}', '{"server_socket": "/s", "started_at": "nan"}',
    '{"server_socket": "/s", "started_at": Infinity}', '{"server_socket": "/s", "started_at": 1e400}',
    '{"server_socket": "/s", "started_at": -5}', '{"server_socket": "/s", "started_at": 0}',
    '{"server_socket": "/s", "started_at": 1e11}', '{"server_socket": "/s", "started_at": "1700000000"}',
])
def test_malformed_pending_marker_counts_as_none(short_dir, pending) -> None:
    path = short_dir / "s.json"
    path.write_text('{"viewer": null, "last_placement": "side", "pending": %s}' % pending)
    assert load_pending(path) is None and load_state(path) == (None, "side")


def test_pending_marker_round_trip(short_dir) -> None:
    path = short_dir / "s.json"
    save_state(path, None, "bottom", pending=Pending("/s.sock", 12.5))
    assert load_pending(path) == Pending("/s.sock", 12.5)
    save_state(path, None, "bottom")
    assert load_pending(path) is None


# --- fix round 2 ---


def test_deeply_nested_state_file_reads_as_empty(short_dir) -> None:
    path = short_dir / "s.json"
    path.write_text("[" * 100000 + "]" * 100000)
    assert load_state(path) == (None, "bottom") and load_pending(path) is None


def test_move_timeout_after_id_change_then_toggle_on_server_b_leaves_one_viewer(env, monkeypatch) -> None:
    config, herdr, ctx = env
    real_move = herdr.move_beside

    def move_then_time_out(*args):
        real_move(*args)                               # herdr moved it (new id) but the reply was lost
        raise HerdrError("timeout", "pane move")

    monkeypatch.setattr(herdr, "move_beside", move_then_time_out)
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "timeout"
    closes = [c for c in herdr.calls if c[0] == "close"]
    assert closes and not closes[0][1].endswith("m")   # cleanup tried the OLD id: herdr says gone
    assert len(herdr.viewers) == 1                     # the moved viewer survives ...
    assert load_pending(config.state_path) is not None  # ... so the marker must too
    b = _server_b(herdr)
    assert toggle("bottom", Context("w1", "w1:t1", "w1:p1", SRV_B, ROOT), b, config) == "shown"
    assert len(herdr.viewers) == 1 and next(iter(herdr.viewers))[0] == SRV_B
    assert load_pending(config.state_path) is None


def test_confirmed_cleanup_close_clears_the_marker(env) -> None:
    config, herdr, ctx = env
    herdr.fail_move = True
    with pytest.raises(HerdrError):
        toggle("bottom", ctx, herdr, config)
    assert load_pending(config.state_path) is None and not herdr.viewers


def test_quit_while_not_running_settles_a_leftover_marker(env, monkeypatch) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(herdr, "move_beside", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        toggle("bottom", ctx, herdr, config)
    orphan = next(iter(herdr.viewers))
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    herdr.calls.clear()
    assert quit_repetui(ctx, herdr, config) == "not_running"
    assert ("close", orphan[1], orphan[0]) in herdr.calls and not herdr.viewers
    assert load_pending(config.state_path) is None and "open" not in kinds(herdr)


def test_quit_while_not_running_without_marker_takes_no_lock(env, monkeypatch) -> None:
    config, herdr, ctx = env
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    monkeypatch.setattr(actions, "global_lock", lambda *a, **k: pytest.fail("lock taken"))
    assert quit_repetui(ctx, herdr, config) == "not_running"


# --- fix round 3 ---


def _orphan_with_marker(env, monkeypatch):
    """An interrupted show: pane opened on server A, marker written, no record."""
    config, herdr, ctx = env
    real_move = herdr.move_beside
    monkeypatch.setattr(herdr, "move_beside", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        toggle("bottom", ctx, herdr, config)
    monkeypatch.setattr(herdr, "move_beside", real_move)
    return next(iter(herdr.viewers))


def _flaky_close(herdr, monkeypatch, failures):
    """close_viewer times out `failures` times, then behaves normally."""
    real, left = herdr.close_viewer, {"n": failures}

    def close(pane_id, socket_path):
        if left["n"] > 0:
            left["n"] -= 1
            herdr.calls.append(("close-timeout", pane_id, socket_path))
            raise HerdrError("timeout", "plugin pane close")
        return real(pane_id, socket_path)

    monkeypatch.setattr(herdr, "close_viewer", close)


def test_quit_not_running_retries_a_recorded_orphan(env, monkeypatch) -> None:
    config, herdr, ctx = env
    orphan = _orphan_with_marker(env, monkeypatch)
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    _flaky_close(herdr, monkeypatch, 1)
    with pytest.raises(HerdrError):
        quit_repetui(ctx, herdr, config)
    viewer, _ = load_state(config.state_path)
    assert (viewer.server_socket, viewer.pane_id) == orphan and load_pending(config.state_path) is not None
    assert quit_repetui(ctx, herdr, config) == "not_running"
    assert ("close", orphan[1], orphan[0]) in herdr.calls and not herdr.viewers
    assert load_pending(config.state_path) is None and load_state(config.state_path)[0] is None


def test_toggle_retries_a_recorded_orphan_and_ends_with_one_viewer(env, monkeypatch) -> None:
    config, herdr, ctx = env
    orphan = _orphan_with_marker(env, monkeypatch)
    _flaky_close(herdr, monkeypatch, 1)
    b = _server_b(herdr)
    b_ctx = Context("w1", "w1:t1", "w1:p1", SRV_B, ROOT)
    _flaky_close(b, monkeypatch, 1)
    with pytest.raises(HerdrError):
        toggle("bottom", b_ctx, b, config)
    assert toggle("bottom", b_ctx, b, config) == "shown"
    assert ("close", orphan[1], orphan[0]) in b.calls and orphan not in herdr.viewers
    assert len(herdr.viewers) == 1 and next(iter(herdr.viewers))[0] == SRV_B
    assert load_pending(config.state_path) is None


@pytest.mark.parametrize("action", ["quit", "toggle"])
def test_orphan_close_failing_twice_raises_both_times_and_keeps_everything(env, monkeypatch, action) -> None:
    config, herdr, ctx = env
    orphan = _orphan_with_marker(env, monkeypatch)
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    _flaky_close(herdr, monkeypatch, 2)
    run = (lambda: quit_repetui(ctx, herdr, config)) if action == "quit" else (
        lambda: toggle("side", ctx, herdr, config))
    for _ in range(2):
        with pytest.raises(HerdrError) as info:
            run()
        assert info.value.code == "timeout"
        viewer, _ = load_state(config.state_path)
        assert (viewer.server_socket, viewer.pane_id) == orphan
        assert load_pending(config.state_path) is not None
    assert [c[0] for c in herdr.calls].count("close-timeout") == 2 and orphan in herdr.viewers
    assert "open" not in kinds(herdr)[1:]                 # only the interrupted show's open


def test_quit_not_running_closes_a_recorded_launcher_without_marker(env, monkeypatch) -> None:
    config, herdr, ctx = env
    herdr.attach_after = 10**6                           # the launcher is still starting the owner
    toggle("bottom", ctx, herdr, config)
    recorded = load_state(config.state_path)[0]
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    herdr.calls.clear()
    assert quit_repetui(ctx, herdr, config) == "not_running"
    assert ("close", recorded.pane_id, recorded.server_socket) in herdr.calls and not herdr.viewers
    assert load_state(config.state_path)[0] is None


def test_quit_not_running_clears_a_record_whose_pane_is_gone(env, monkeypatch) -> None:
    config, herdr, ctx = env
    save_state(config.state_path, Viewer(herdr.socket_path, "w9:p9", "w9:t1", "", "bottom"), "bottom")
    monkeypatch.setattr(actions, "socket_state", lambda p: "absent")
    assert quit_repetui(ctx, herdr, config) == "not_running"
    assert "close" not in kinds(herdr) and load_state(config.state_path)[0] is None


def test_quit_rechecks_running_under_the_lock(env, monkeypatch) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    states = iter(["absent", "live"])                    # the owner came up while we waited
    monkeypatch.setattr(actions, "socket_state", lambda p: next(states, "live"))
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "quit_sent"


# --- final review: argv-less foreign processes (O-I1) ---

def test_argv_less_foreign_process_blocks_neither_toggle_nor_quit(env, monkeypatch, no_wait) -> None:
    config, herdr, ctx = env
    reads = _procs_for(herdr, monkeypatch, "w2:p1", [{"pid": 1, "name": "sudo"}])  # unrelated pane
    assert toggle("bottom", ctx, herdr, config) == "shown"
    assert reads["n"] == 1                                  # read once, skipped, never polled
    assert toggle("bottom", ctx, herdr, config) == "hidden"  # hide clears the record
    herdr.calls.clear()
    assert quit_repetui(ctx, herdr, config, sleep=lambda s: None) == "quit_sent"
    assert kinds(herdr)[-1] == "keys" and "w2:p1" not in [c[1] for c in herdr.calls if c[0] == "close"]


@pytest.mark.parametrize("name", ["dtach", "python3", "python3.11", "python"])
def test_argv_less_process_that_could_be_ours_stays_unresolved(env, monkeypatch, no_wait, name) -> None:
    config, herdr, ctx = env
    reads = _procs_for(herdr, monkeypatch, "w2:p1", [{"pid": 1, "name": name}])
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "ownership_unresolved" and reads["n"] > 1
    assert "open" not in kinds(herdr)


def test_recorded_pane_keeps_strict_handling_of_any_argv_less_entry(env, monkeypatch, no_wait) -> None:
    config, herdr, ctx = env
    toggle("bottom", ctx, herdr, config)
    recorded = load_state(config.state_path)[0]
    _procs_for(herdr, monkeypatch, recorded.pane_id, [{"pid": 1, "name": "sudo"}])
    herdr.calls.clear()
    with pytest.raises(HerdrError) as info:
        toggle("bottom", ctx, herdr, config)
    assert info.value.code == "ownership_unresolved"
    assert load_state(config.state_path)[0] == recorded and kinds(herdr) == []
