import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from herdr_repetui.actions import (  # noqa: E402
    _mac_notify, context_from_env, notify_user, quit_repetui, startup, toggle,
)
from herdr_repetui.config import ConfigError, load_config  # noqa: E402
from herdr_repetui.dtach import SocketProbeError  # noqa: E402
from herdr_repetui.herdr import Herdr, HerdrError  # noqa: E402
from herdr_repetui.locking import LockTimeout  # noqa: E402


def _report(herdr: Herdr, message: str) -> None:
    notify_user(herdr, "repetui", message, notify_mac=_mac_notify)


def main(argv: list[str]) -> int:
    herdr = Herdr(os.environ.get("HERDR_BIN_PATH", "herdr"), os.environ.get("HERDR_SOCKET_PATH", ""))
    command = argv[1] if len(argv) > 1 else ""
    if command not in {"startup", "quit", "toggle-bottom", "toggle-side"}:
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    try:
        config = load_config(os.environ)
        if command == "startup":
            startup(herdr, config)
        elif command == "quit":
            quit_repetui(context_from_env(os.environ), herdr, config)
        else:
            toggle(command.removeprefix("toggle-"), context_from_env(os.environ), herdr, config)
    except (HerdrError, LockTimeout, SocketProbeError, ConfigError, OSError) as exc:
        _report(herdr, f"{command} failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
