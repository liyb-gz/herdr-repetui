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


def _config_file(env: Mapping[str, str]) -> Path | None:
    if env.get("HERDR_REPETUI_CONFIG"):
        return Path(env["HERDR_REPETUI_CONFIG"]).expanduser()
    if env.get("HERDR_PLUGIN_CONFIG_DIR"):
        return Path(env["HERDR_PLUGIN_CONFIG_DIR"]) / "config.toml"
    return None


def _fraction(raw: Mapping[str, object], key: str, default: float) -> float:
    value = raw.get(key, default)
    return float(value) if isinstance(value, (int, float)) and 0 < value < 1 else default


def _canonical(path: Path) -> Path:
    """Resolve aliases (/tmp vs /private/tmp, symlinked dirs, a symlink to the socket itself),
    so one socket always maps to one lock and one state file."""
    return Path(os.path.realpath(path))


def _socket_path(raw: Mapping[str, object], state_dir: Path) -> Path:
    configured = raw.get("socket")
    if configured:
        path = _canonical(Path(str(configured)).expanduser())
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
    path = _config_file(env)
    raw: dict[str, object] = {}
    if path is not None and path.is_file():
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    default_state = env.get("HERDR_PLUGIN_STATE_DIR") or "~/.local/state/herdr-repetui"
    state_dir = Path(str(raw.get("state_dir") or default_state)).expanduser()
    return Config(
        repetui=str(raw.get("repetui", "repetui")),
        dtach=str(raw.get("dtach", "dtach")),
        socket=_socket_path(raw, state_dir),
        anki_home=Path(str(raw.get("anki_home", "~/Library/Application Support/Anki2"))).expanduser(),
        profile=str(raw.get("profile", "")),
        bottom_fraction=_fraction(raw, "bottom_fraction", 0.25),
        side_fraction=_fraction(raw, "side_fraction", 0.2),
        state_dir=state_dir,
    )
