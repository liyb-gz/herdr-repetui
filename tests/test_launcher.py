import fcntl
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from herdr_repetui.config import load_config
from herdr_repetui.launcher import (
    LaunchError, ensure_owner, herdr_keys_value, owner_argv, owner_env, viewer_argv,
)
from herdr_repetui.dtach import SocketProbeError
from herdr_repetui.locking import global_lock


@pytest.fixture
def cfg(short_dir):
    (short_dir / "c.toml").write_text(
        f'state_dir = "{short_dir}"\nsocket = "{short_dir}/r.sock"\nprofile = "User 1"\n')
    return load_config({"HERDR_REPETUI_CONFIG": str(short_dir / "c.toml")})


def test_argv(cfg) -> None:
    c = cfg
    assert owner_argv(c) == ["dtach", "-n", str(c.socket), "-E", "-z", "repetui", "--profile", "User 1"]
    assert viewer_argv(c) == ["dtach", "-a", str(c.socket), "-E", "-z", "-r", "ctrl_l"]


def test_live_socket_takes_no_lock_and_spawns_nothing(cfg, monkeypatch) -> None:
    import herdr_repetui.launcher as launcher
    monkeypatch.setattr(launcher, "global_lock", lambda *a, **k: (_ for _ in ()).throw(AssertionError("locked")))
    spawned = []
    ensure_owner(cfg, {}, spawn=lambda *a, **k: spawned.append(a), probe=lambda p: "live")
    assert spawned == []


def lock_is_held(path: Path) -> bool:
    """True if some other open file description holds the flock (flock conflicts per description)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)               # closing also drops a lock we just took
    return False


def test_stale_socket_is_unlinked_under_lock_then_owner_started(cfg, monkeypatch) -> None:
    c = cfg
    c.socket.write_text("")
    held = []                                      # lock state observed at each step, in order
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self == c.socket:
            held.append(("unlink", lock_is_held(c.lock_path)))
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink)

    states = iter(["stale", "stale", "absent", "live"])

    def probe(path):
        state = next(states)
        held.append((f"probe-{state}", lock_is_held(c.lock_path)))
        return state

    spawned = []

    def spawn(argv, **k):
        held.append(("spawn", lock_is_held(c.lock_path)))
        spawned.append(argv)
        return SimpleNamespace(returncode=0)

    ensure_owner(c, {}, spawn=spawn, probe=probe, sleep=lambda s: None)
    assert not c.socket.exists() and spawned == [owner_argv(c)]
    assert held == [("probe-stale", False),        # fast-path probe: no lock yet
                    ("probe-stale", True),         # re-probe under the lock
                    ("unlink", True),              # cleanup only under the lock
                    ("spawn", True),
                    ("probe-absent", True),
                    ("probe-live", True)]
    assert not lock_is_held(c.lock_path)           # released afterwards


def test_probe_error_never_unlinks(cfg) -> None:
    c = cfg
    c.socket.write_text("")

    def probe(path):
        raise SocketProbeError("EACCES")
    with pytest.raises(SocketProbeError):
        ensure_owner(c, {}, spawn=lambda *a, **k: pytest.fail("must not spawn"), probe=probe)
    assert c.socket.exists()


def test_spawn_gets_the_remaining_startup_budget(cfg) -> None:
    ticks = iter([0, 4, 4, 4])                     # deadline = 0 + 10, 4 s already used at spawn time
    kwargs = {}
    states = iter(["absent", "absent", "live"])

    def spawn(argv, **k):
        kwargs.update(k)
        return SimpleNamespace(returncode=0)
    ensure_owner(cfg, {}, spawn=spawn, probe=lambda p: next(states), sleep=lambda s: None,
                 clock=lambda: next(ticks), timeout=10)
    assert kwargs["timeout"] == 6 and kwargs["check"] is False


def test_hung_spawn_becomes_launch_error_and_releases_lock(cfg) -> None:
    def spawn(argv, **k):
        raise subprocess.TimeoutExpired(argv, k["timeout"])
    with pytest.raises(LaunchError, match="did not return in time"):
        ensure_owner(cfg, {}, spawn=spawn, probe=lambda p: "absent", sleep=lambda s: None)
    assert not lock_is_held(cfg.lock_path)


def test_no_spawn_when_budget_already_spent(cfg) -> None:
    ticks = iter([0, 11])
    with pytest.raises(LaunchError):
        ensure_owner(cfg, {}, spawn=lambda *a, **k: pytest.fail("must not spawn"),
                     probe=lambda p: "absent", sleep=lambda s: None, clock=lambda: next(ticks), timeout=10)


def test_timeout_raises(cfg) -> None:
    ticks = iter(range(100))
    with pytest.raises(LaunchError):
        ensure_owner(cfg, {}, spawn=lambda *a, **k: SimpleNamespace(returncode=0),
                     probe=lambda p: "absent", sleep=lambda s: None, clock=lambda: next(ticks), timeout=5)


def test_owner_env(cfg, tmp_path) -> None:
    herdr_cfg = tmp_path / "herdr.toml"
    herdr_cfg.write_text('[[keys.command]]\nkey = "prefix+y"\ntype = "plugin_action"\n'
                         'command = "repetui.bar.toggle-bottom"\n')
    env = owner_env(cfg, {"HERDR_PANE_ID": "w1:p1", "HERDR_TAB_ID": "w1:t1",
                                    "XDG_CONFIG_HOME": "/tmp/x", "PATH": "/bin"}, herdr_cfg)
    assert "HERDR_PANE_ID" not in env and "HERDR_TAB_ID" not in env
    assert env["PATH"] == "/bin" and env["XDG_CONFIG_HOME"] == "/tmp/x"   # inherited, isolates prefs
    assert env["REPETUI_NATIVE_IMAGES"] == "kitty"            # herdr forwards pictures by default
    assert env["REPETUI_HERDR_KEYS"] == "bottom=prefix+y;side=prefix+shift+a"


@pytest.mark.parametrize("toml,expected", [
    ("", "kitty"),
    ("[terminal]\nkitty_graphics = true\n", "kitty"),
    ("[terminal]\nkitty_graphics = false\n", "off"),
    ("not toml [", "kitty"),
])
def test_pictures_follow_herdr_kitty_graphics(cfg, tmp_path, toml, expected) -> None:
    herdr_cfg = tmp_path / "herdr.toml"
    herdr_cfg.write_text(toml)
    assert owner_env(cfg, {}, herdr_cfg)["REPETUI_NATIVE_IMAGES"] == expected


def test_herdr_keys_default_when_missing(tmp_path) -> None:
    assert herdr_keys_value(tmp_path / "nope.toml") == "bottom=prefix+a;side=prefix+shift+a"


def test_lock_serialises(short_dir) -> None:
    order = []
    def worker(name):
        with global_lock(short_dir / "l"):
            order.append(name + "-in"); threading.Event().wait(0.1); order.append(name + "-out")
    threads = [threading.Thread(target=worker, args=(n,)) for n in "ab"]
    for t in threads: t.start()
    for t in threads: t.join()
    assert order[0][0] == order[1][0] and order[2][0] == order[3][0]


@pytest.mark.parametrize("content", [b"[keys\nbroken", b"\xff\xfe\x00bad", b"keys = 5\n",
                                     b"[keys]\ncommand = 3\n"])
def test_herdr_keys_default_when_unreadable(tmp_path, content) -> None:
    path = tmp_path / "herdr.toml"
    path.write_bytes(content)
    assert herdr_keys_value(path) == "bottom=prefix+a;side=prefix+shift+a"


def test_failed_spawn_raises(cfg) -> None:
    with pytest.raises(LaunchError):
        ensure_owner(cfg, {}, spawn=lambda *a, **k: SimpleNamespace(returncode=2),
                     probe=lambda p: "absent", sleep=lambda s: None)


def test_live_after_lock_skips_spawn(cfg) -> None:
    states = iter(["absent", "live"])      # another launcher won the race while we waited
    spawned = []
    ensure_owner(cfg, {}, spawn=lambda *a, **k: spawned.append(a), probe=lambda p: next(states))
    assert spawned == []


def test_main_reports_config_error_without_launching(short_dir, monkeypatch) -> None:
    import herdr_repetui.launcher as launcher
    messages = []
    monkeypatch.setattr(launcher, "_fail", lambda m: (messages.append(m), 1)[1])
    monkeypatch.setattr(launcher, "ensure_owner", lambda *a, **k: pytest.fail("must not launch"))
    assert launcher.main({"HERDR_REPETUI_CONFIG": str(short_dir / "missing.toml")}) == 1
    assert messages and messages[0].startswith("config:")


def test_main_reports_missing_binaries(cfg, short_dir, monkeypatch) -> None:
    import herdr_repetui.launcher as launcher
    messages = []
    monkeypatch.setattr(launcher, "_fail", lambda m: (messages.append(m), 1)[1])
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(launcher, "ensure_owner", lambda *a, **k: pytest.fail("must not launch"))
    assert launcher.main({"HERDR_REPETUI_CONFIG": str(short_dir / "c.toml")}) == 1
    assert "not found" in messages[0] and "dtach" in messages[0]


def test_main_execs_viewer_after_ensure_owner(cfg, short_dir, monkeypatch) -> None:
    import herdr_repetui.launcher as launcher
    calls = []
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(launcher, "ensure_owner", lambda c, e: calls.append("ensure"))
    monkeypatch.setattr(launcher.os, "execvp", lambda f, argv: calls.append((f, argv)))
    launcher.main({"HERDR_REPETUI_CONFIG": str(short_dir / "c.toml")})
    assert calls == ["ensure", ("dtach", viewer_argv(cfg))]
