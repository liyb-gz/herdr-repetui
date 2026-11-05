"""Plugin configuration: herdr-provided dirs plus an optional TOML file."""

from __future__ import annotations

import hashlib
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_SUN_PATH_MAX = 104  # macOS AF_UNIX sun_path size, including the terminating NUL


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    repetui: str
    dtach: str
    socket: Path
    anki_home: Path
    profile: str
    bottom_fraction: float
    side_fraction: float
    state_dir: Path

    @property
    def lock_path(self) -> Path:
        return self.socket.with_name(self.socket.name + ".lock")

    @property
    def state_path(self) -> Path:
        return self.socket.with_name(self.socket.name + ".state.json")


def _fits(path: Path) -> bool:
    return len(os.fsencode(str(path))) < _SUN_PATH_MAX


def _expand(label: str, value: str) -> Path:
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:  # unknown user in "~user"
        raise ConfigError(f"{label}: {exc}") from exc


def _absolute(label: str, value: str) -> Path:
    """expanduser, then insist on an absolute path: a relative one would resolve against whatever
    cwd the caller happens to have, so toggle.py and launch.py could disagree."""
    path = _expand(label, value)
    if not path.is_absolute():
        raise ConfigError(f"{label} must be an absolute path (after ~ expansion): {value!r}")
    return path


def _string(raw: Mapping[str, object], key: str, default: str, *, non_empty: bool = False) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string, not {type(value).__name__}")
    if non_empty and not value:
        raise ConfigError(f"{key} must not be empty")
    return value


def _read_file(env: Mapping[str, str]) -> dict[str, object]:
    """HERDR_REPETUI_CONFIG must exist; HERDR_PLUGIN_CONFIG_DIR/config.toml is optional.
    Every way the file can be unusable surfaces as ConfigError (callers only catch that)."""
    if env.get("HERDR_REPETUI_CONFIG"):
        path, required = _expand("HERDR_REPETUI_CONFIG", env["HERDR_REPETUI_CONFIG"]), True
    elif env.get("HERDR_PLUGIN_CONFIG_DIR"):
        path, required = Path(env["HERDR_PLUGIN_CONFIG_DIR"]) / "config.toml", False
    else:
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        if required:
            raise ConfigError(f"{path}: config file not found") from exc
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _fraction(raw: Mapping[str, object], key: str, default: float) -> float:
    value = raw.get(key, default)
    return float(value) if isinstance(value, (int, float)) and 0 < value < 1 else default


def _canonical(path: Path) -> Path:
    """Resolve aliases (/tmp vs /private/tmp, symlinked dirs, a symlink to the socket itself),
    so one socket always maps to one lock and one state file."""
    return Path(os.path.realpath(path))


def _socket_path(raw: Mapping[str, object], state_dir: Path) -> Path:
    configured = _string(raw, "socket", "")
    if configured:
        path = _canonical(_absolute("socket", configured))
        if not _fits(path):
            raise ConfigError(f"socket path too long for a Unix socket: {path}")
        return path
    path = _canonical(state_dir / "repetui.sock")
    if _fits(path):
        return path
    digest = hashlib.sha1(os.fsencode(os.path.realpath(state_dir))).hexdigest()[:12]
    return Path(f"/private/tmp/herdr-repetui-{os.getuid()}") / f"{digest}.sock"


def herdr_config_path(env: Mapping[str, str] = os.environ) -> Path:
    """herdr's own config.toml: HERDR_CONFIG_PATH (documented in `herdr --help`), else
    $XDG_CONFIG_HOME/herdr/config.toml, else ~/.config/herdr/config.toml."""
    if env.get("HERDR_CONFIG_PATH"):
        return Path(env["HERDR_CONFIG_PATH"]).expanduser()
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "herdr" / "config.toml"


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    raw = _read_file(env)
    configured_state = _string(raw, "state_dir", "")
    if configured_state:
        state_dir = _absolute("state_dir", configured_state)
    else:
        state_dir = _absolute("HERDR_PLUGIN_STATE_DIR",
                              env.get("HERDR_PLUGIN_STATE_DIR") or "~/.local/state/herdr-repetui")
    return Config(
        repetui=_string(raw, "repetui", "repetui", non_empty=True),
        dtach=_string(raw, "dtach", "dtach", non_empty=True),
        socket=_socket_path(raw, state_dir),
        anki_home=_absolute("anki_home", _string(raw, "anki_home", "~/Library/Application Support/Anki2",
                                                 non_empty=True)),
        profile=_string(raw, "profile", ""),
        bottom_fraction=_fraction(raw, "bottom_fraction", 0.25),
        side_fraction=_fraction(raw, "side_fraction", 0.2),
        state_dir=state_dir,
    )
