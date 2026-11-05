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


def test_same_long_state_dir_hashes_to_the_same_socket(tmp_path) -> None:
    env = {"HERDR_PLUGIN_STATE_DIR": str(tmp_path / ("a" * 120))}
    assert load_config(env).socket == load_config(dict(env)).socket


def test_invalid_toml_is_a_config_error(tmp_path) -> None:
    env = write(tmp_path, "profile = \n")
    with pytest.raises(ConfigError) as info:
        load_config(env)
    assert str(tmp_path / "c.toml") in str(info.value)


def test_non_utf8_config_is_a_config_error(tmp_path) -> None:
    (tmp_path / "c.toml").write_bytes(b"profile = '\xff\xfe'\n")
    env = {"HERDR_REPETUI_CONFIG": str(tmp_path / "c.toml"), "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")}
    with pytest.raises(ConfigError):
        load_config(env)


def test_unreadable_config_is_a_config_error(tmp_path) -> None:
    (tmp_path / "c.toml").mkdir()  # a directory where the file should be
    with pytest.raises(ConfigError):
        load_config({"HERDR_REPETUI_CONFIG": str(tmp_path / "c.toml"),
                     "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")})
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "config.toml").mkdir()
    with pytest.raises(ConfigError):
        load_config({"HERDR_PLUGIN_CONFIG_DIR": str(tmp_path / "cfg"),
                     "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")})


def test_explicit_config_file_must_exist(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config({"HERDR_REPETUI_CONFIG": str(tmp_path / "missing.toml"),
                     "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")})


def test_missing_file_in_plugin_config_dir_is_optional(tmp_path) -> None:
    (tmp_path / "cfg").mkdir()
    cfg = load_config({"HERDR_PLUGIN_CONFIG_DIR": str(tmp_path / "cfg"),
                       "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")})
    assert cfg.repetui == "repetui"
    cfg = load_config({"HERDR_PLUGIN_CONFIG_DIR": str(tmp_path / "no-such-dir"),
                       "HERDR_PLUGIN_STATE_DIR": str(tmp_path / "s")})
    assert cfg.repetui == "repetui"


@pytest.mark.parametrize("line", [
    "repetui = 5",
    "dtach = true",
    "profile = 1",
    "socket = 3",
    "anki_home = 7",
    "state_dir = 9",
    'repetui = ""',
    'dtach = ""',
    'anki_home = ""',
    'socket = "relative.sock"',
    'anki_home = "relative/Anki2"',
    'state_dir = "relative/state"',
])
def test_invalid_values_are_config_errors(short_dir, monkeypatch, line) -> None:
    monkeypatch.chdir(short_dir)  # a relative socket would fit here, so only the absolute check can reject it
    with pytest.raises(ConfigError):
        load_config(write(short_dir, line + "\n"))


def test_relative_socket_is_rejected_for_being_relative(short_dir, monkeypatch) -> None:
    monkeypatch.chdir(short_dir)
    with pytest.raises(ConfigError, match="absolute"):
        load_config(write(short_dir, 'socket = "relative.sock"\n'))


def test_empty_profile_and_empty_socket_and_state_dir_mean_default(tmp_path) -> None:
    env = write(tmp_path, 'profile = ""\nsocket = ""\nstate_dir = ""\n')
    env["HERDR_PLUGIN_STATE_DIR"] = str(tmp_path / "s")
    cfg = load_config(env)
    assert cfg.profile == ""
    assert cfg.state_dir == tmp_path / "s"
    assert cfg.socket.name.endswith(".sock")


def test_tilde_paths_are_expanded_not_rejected(tmp_path) -> None:
    cfg = load_config(write(tmp_path, 'anki_home = "~/Anki-test"\nstate_dir = "~/st"\n'))
    assert cfg.anki_home == Path("~/Anki-test").expanduser()
    assert cfg.state_dir == Path("~/st").expanduser()


def test_relative_plugin_state_dir_is_rejected() -> None:
    with pytest.raises(ConfigError, match="absolute"):
        load_config({"HERDR_PLUGIN_STATE_DIR": "relative/state"})
