import os
from pathlib import Path

import pytest

from herdr_repetui.config import ConfigError, load_config


def write(tmp_path, text: str) -> dict:
    (tmp_path / "c.toml").write_text(text)
    return {"HERDR_REPETUI_CONFIG": str(tmp_path / "c.toml"), "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")}


def test_defaults(short_dir) -> None:
    cfg = load_config({"HERDR_PLUGIN_STATE_DIR": str(short_dir / "s")})
    real = Path(os.path.realpath(short_dir)) / "s"
    assert (cfg.repetui, cfg.dtach, cfg.profile) == ("repetui", "dtach", "")
    assert cfg.socket == real / "repetui.sock"
    assert cfg.lock_path == real / "repetui.sock.lock"
    assert cfg.state_path == real / "repetui.sock.state.json"
    assert (cfg.bottom_fraction, cfg.side_fraction) == (0.25, 0.2)
    assert cfg.anki_home == Path("~/Library/Application Support/Anki2").expanduser()


def test_override_file_and_plugin_config_dir(tmp_path) -> None:
    cfg = load_config(write(tmp_path, 'profile = "User 1"\nbottom_fraction = 0.3\n'))
    assert cfg.profile == "User 1" and cfg.bottom_fraction == 0.3
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "config.toml").write_text('repetui = "/opt/repetui"\n')
    cfg = load_config({"HERDR_PLUGIN_CONFIG_DIR": str(tmp_path / "cfg"),
                       "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")})
    assert cfg.repetui == "/opt/repetui"


def test_bad_fraction_falls_back(tmp_path) -> None:
    assert load_config(write(tmp_path, "side_fraction = 1.5\n")).side_fraction == 0.2


def test_same_socket_means_same_lock_even_with_different_state_dirs(tmp_path) -> None:
    a = load_config(write(tmp_path, f'socket = "/tmp/x-{os.getpid()}.sock"\nstate_dir = "{tmp_path}/a"\n'))
    b = load_config(write(tmp_path, f'socket = "/private/tmp/x-{os.getpid()}.sock"\nstate_dir = "{tmp_path}/b"\n'))
    assert a.socket == b.socket and a.lock_path == b.lock_path and a.state_path == b.state_path


def test_socket_symlink_maps_to_the_same_lock(short_dir) -> None:
    real = short_dir / "real.sock"
    real.touch()
    (short_dir / "alias.sock").symlink_to(real)
    a = load_config(write(short_dir, f'socket = "{real}"\n'))
    b = load_config(write(short_dir, f'socket = "{short_dir}/alias.sock"\n'))
    assert a.socket == b.socket and a.lock_path == b.lock_path


def test_long_default_socket_is_hashed_per_state_dir(tmp_path) -> None:
    one = load_config({"HERDR_PLUGIN_STATE_DIR": str(tmp_path / ("a" * 120))})
    two = load_config({"HERDR_PLUGIN_STATE_DIR": str(tmp_path / ("b" * 120))})
    assert one.socket != two.socket
    assert len(os.fsencode(str(one.socket))) < 104
    assert one.socket.parent == Path(f"/private/tmp/herdr-repetui-{os.getuid()}")


def test_herdr_config_path_resolution(tmp_path) -> None:
    from herdr_repetui.config import herdr_config_path
    assert herdr_config_path({"HERDR_CONFIG_PATH": str(tmp_path / "h.toml"),
                              "XDG_CONFIG_HOME": "/x"}) == tmp_path / "h.toml"
    assert herdr_config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "herdr" / "config.toml"
    assert herdr_config_path({}) == Path.home() / ".config" / "herdr" / "config.toml"


def test_explicit_socket_too_long_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, f'socket = "/tmp/{"x" * 120}.sock"\n'))
