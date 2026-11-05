"""Pane entrypoint: make sure the single dtach owner exists, then exec a viewer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from .config import Config, ConfigError, herdr_config_path, load_config
from .dtach import SocketProbeError, socket_state
from .locking import LockTimeout, global_lock, private_dir

DEFAULT_KEYS = {"bottom": "prefix+a", "side": "prefix+shift+a"}
_ACTIONS = {"bottom": "repetui.bar.toggle-bottom", "side": "repetui.bar.toggle-side"}
_PANE_VARS = ("HERDR_PANE_ID", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID")


class LaunchError(RuntimeError):
    pass


def herdr_keys_value(herdr_config: Path) -> str:
    keys = dict(DEFAULT_KEYS)
    try:
        with herdr_config.open("rb") as handle:
            section = tomllib.load(handle).get("keys", {})
        commands = section.get("command", []) if isinstance(section, dict) else []
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        commands = []
    for entry in commands if isinstance(commands, list) else []:
        for name, action in _ACTIONS.items():
            if isinstance(entry, dict) and entry.get("command") == action and entry.get("key"):
                keys[name] = str(entry["key"])
    return ";".join(f"{name}={key}" for name, key in keys.items())


def owner_env(config: Config, base: Mapping[str, str], herdr_config: Path | None = None) -> dict[str, str]:
    herdr_config = herdr_config or herdr_config_path(base)
    env = {key: value for key, value in base.items() if key not in _PANE_VARS}
    env["REPETUI_ANKI_HOME"] = str(config.anki_home)
    env["REPETUI_NATIVE_IMAGES"] = "off"
    env["REPETUI_HERDR_KEYS"] = herdr_keys_value(herdr_config)
    return env


def owner_argv(config: Config) -> list[str]:
    argv = [config.dtach, "-n", str(config.socket), "-E", "-z", config.repetui]
    return argv + (["--profile", config.profile] if config.profile else [])


def viewer_argv(config: Config) -> list[str]:
    return [config.dtach, "-a", str(config.socket), "-E", "-z", "-r", "ctrl_l"]


def ensure_owner(
    config: Config,
    env: Mapping[str, str],
    *,
    spawn: Callable[..., object] = subprocess.run,
    probe: Callable[[Path], str] = socket_state,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = 10.0,
    herdr_config: Path | None = None,
) -> None:
    if probe(config.socket) == "live":
        return                                     # fast path: no lock (see Task 19 quit)
    with global_lock(config.lock_path):
        state = probe(config.socket)
        if state == "live":
            return
        if state == "stale":
            config.socket.unlink(missing_ok=True)  # only here, only under the lock
        private_dir(config.socket.parent)
        argv, owner = owner_argv(config), owner_env(config, env, herdr_config)
        deadline = clock() + timeout               # covers the spawn too: the lock is held throughout
        remaining = deadline - clock()
        if remaining <= 0:
            raise LaunchError("repetui did not start in time")
        try:
            result = spawn(argv, env=owner, check=False, timeout=remaining)
        except subprocess.TimeoutExpired:
            raise LaunchError("dtach did not return in time") from None
        if getattr(result, "returncode", 0) != 0:
            raise LaunchError(f"dtach failed to start repetui (exit {result.returncode})")
        while probe(config.socket) != "live":
            if clock() > deadline:
                raise LaunchError("repetui did not start in time")
            sleep(0.05)


def _fail(message: str) -> int:
    print(f"repetui: {message}\npress any key to close", flush=True)
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            os.read(fd, 1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        time.sleep(30)
    return 1


def main(env: Mapping[str, str] = os.environ) -> int:
    try:
        config = load_config(env)
    except (ConfigError, OSError) as exc:
        return _fail(f"config: {exc}")
    missing = [name for name in (config.dtach, config.repetui) if shutil.which(name) is None]
    if missing:
        return _fail("not found: " + ", ".join(missing))
    try:
        ensure_owner(config, env)
    except (LaunchError, LockTimeout, SocketProbeError, OSError) as exc:
        return _fail(str(exc))
    argv = viewer_argv(config)
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        return _fail(f"cannot exec {argv[0]}: {exc}")
    return 1
