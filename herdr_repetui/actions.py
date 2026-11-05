"""Toggle / quit / startup. Each action runs under the global lock."""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import Config, herdr_config_path
from .dtach import socket_state
from .herdr import GONE_CODES, Herdr, HerdrError
from .locking import global_lock
from .sizing import MIN_COLS, MIN_ROWS, split_ratio
from .state import Pending, Viewer, load_pending, load_state, save_state

_DIRECTION = {"bottom": "down", "side": "right"}


@dataclass(frozen=True)
class Context:
    workspace_id: str
    tab_id: str
    focused_pane_id: str
    server_socket: str
    plugin_root: str


def context_from_env(env: Mapping[str, str]) -> Context:
    try:
        ctx = json.loads(env.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    # Key names: Task 1 Step 3 CONTEXT_KEYS (workspace_id, tab_id, focused_pane_id).
    return Context(
        workspace_id=str(ctx.get("workspace_id") or env.get("HERDR_WORKSPACE_ID", "")),
        tab_id=str(ctx.get("tab_id") or env.get("HERDR_TAB_ID", "")),
        focused_pane_id=str(ctx.get("focused_pane_id") or ctx.get("pane_id") or env.get("HERDR_PANE_ID", "")),
        server_socket=env.get("HERDR_SOCKET_PATH", ""),
        plugin_root=env.get("HERDR_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent),
    )


def is_attached(processes: list[dict] | None, socket: Path) -> bool:
    return any(
        (argv := p.get("argv") or []) and Path(argv[0]).name == "dtach" and "-a" in argv
        and str(socket) in argv
        for p in processes or []
    )


def _is_launcher(argv: list[str]) -> bool:
    return any(Path(a).name == "launch.py" for a in argv)


def _owner(argv: list[str]) -> str | None:
    return argv[argv.index("--owner") + 1] if "--owner" in argv[:-1] else None


def is_viewer(processes: list[dict] | None, socket: Path, plugin_root: str, *,
              recorded: bool = False) -> bool:
    """Attached dtach client, or a pending launcher that is provably ours.

    A launcher with `--owner <socket>` must name our socket. A launcher that has not re-exec'd
    yet (no `--owner`, a microsecond window) only counts for the *recorded* pane: the state
    record, written under the lock, already proves ownership. Adoption scans require the token.
    """
    if is_attached(processes, socket):
        return True
    root = os.path.realpath(plugin_root)
    for p in processes or []:
        argv = p.get("argv") or []
        if not _is_launcher(argv):
            continue
        owner = _owner(argv)
        if owner is not None:
            if owner == str(socket):
                return True
        elif recorded and os.path.realpath(p.get("cwd") or "") == root:
            return True
    return False


ADOPT_WAIT = 2.0  # seconds to wait for process info to resolve (launcher re-exec, missing argv)
_sleep = time.sleep
_clock = time.monotonic


def _tokenless_launcher(processes: list[dict] | None, plugin_root: str) -> bool:
    root = os.path.realpath(plugin_root)
    return any(
        _is_launcher(p.get("argv") or []) and _owner(p.get("argv") or []) is None
        and os.path.realpath(p.get("cwd") or "") == root
        for p in processes or []
    )


def _unknown(processes: list[dict] | None) -> bool:
    """herdr returned processes but some lack argv: we cannot tell whose pane this is."""
    return bool(processes) and any(not p.get("argv") for p in processes)


def _poll(herdr: Herdr, pane_id: str, socket_path: str | None,
          unresolved: Callable[[list[dict]], bool]) -> list[dict] | None:
    """Read a pane's processes until they are resolved or the pane is confirmed gone (None).

    Unresolved for longer than ADOPT_WAIT aborts the action with `ownership_unresolved`: neither
    keeping a stale owner nor discarding a live one is safe, so nothing is changed.
    """
    processes = herdr.foreground_processes(pane_id, socket_path)
    deadline = None
    while processes is not None and unresolved(processes):
        if deadline is None:
            deadline = _clock() + ADOPT_WAIT
        elif _clock() > deadline:
            raise HerdrError("ownership_unresolved", f"cannot tell whether {pane_id} is our viewer")
        _sleep(0.05)
        processes = herdr.foreground_processes(pane_id, socket_path)
    return processes


def _scan_processes(herdr: Herdr, pane_id: str, socket_path: str | None, config: Config,
                    ctx: Context) -> list[dict] | None:
    """Processes of a pane met in an adoption scan. A tokenless launcher of ours (an action died
    between `plugin pane open` and its re-exec) or an argv-less entry is unresolved: adopting
    nothing could leave a second viewer, adopting blindly could close someone else's. An empty
    list is resolved: no foreground process, so not a viewer."""
    return _poll(herdr, pane_id, socket_path, lambda ps: not is_viewer(ps, config.socket, ctx.plugin_root)
                 and (_tokenless_launcher(ps, ctx.plugin_root) or _unknown(ps)))


def _close(herdr: Herdr, pane_id: str, server_socket: str) -> None:
    """Close a viewer; a confirmed disappearance counts as closed, anything else raises."""
    try:
        herdr.close_viewer(pane_id, server_socket)
    except HerdrError as exc:
        if exc.code not in GONE_CODES:
            raise


def _settle_pending(herdr: Herdr, config: Config, ctx: Context) -> None:
    """An earlier action wrote a pending marker and died before recording its pane. Scan the
    marker's server (raw socket: it may be another herdr server) for a pane of ours and close it,
    recording it first so a failed close is retried. The marker is cleared only once the scan
    found nothing left, or the server is unreachable (its panes are gone with it)."""
    pending = load_pending(config.state_path)
    if pending is None:
        return
    viewer, last = load_state(config.state_path)
    try:
        panes = herdr.panes_on(pending.server_socket)
    except HerdrError as exc:
        if exc.code != "server_unreachable":
            raise                                    # unknown: keep the marker, abort the action
        panes = []
    for pane in panes:
        if viewer is not None and (viewer.server_socket, viewer.pane_id) == (pending.server_socket,
                                                                             pane["pane_id"]):
            continue                                 # already recorded: handled as the viewer
        processes = _scan_processes(herdr, pane["pane_id"], pending.server_socket, config, ctx)
        if not is_viewer(processes, config.socket, ctx.plugin_root):
            continue
        orphan = Viewer(pending.server_socket, pane["pane_id"], pane.get("tab_id") or "", "", last)
        save_state(config.state_path, viewer or orphan, last, pending=pending)
        _close(herdr, orphan.pane_id, orphan.server_socket)
        save_state(config.state_path, viewer, last, pending=pending)
    save_state(config.state_path, viewer, last)


def _current_viewer(herdr: Herdr, config: Config, ctx: Context) -> Viewer | None:
    viewer, last = load_state(config.state_path)
    if viewer is not None:
        # The record proves ownership, so a tokenless launcher counts; but an empty or argv-less
        # reading proves nothing either way: wait for it, never clear the record on it.
        processes = _poll(herdr, viewer.pane_id, viewer.server_socket,
                          lambda ps: not is_viewer(ps, config.socket, ctx.plugin_root, recorded=True)
                          and (not ps or _unknown(ps)))
        if is_viewer(processes, config.socket, ctx.plugin_root, recorded=True):
            return viewer
        save_state(config.state_path, None, last)      # confirmed: gone, or no longer our viewer
    for pane in herdr.panes():
        processes = _scan_processes(herdr, pane["pane_id"], None, config, ctx)
        if is_viewer(processes, config.socket, ctx.plugin_root):
            adopted = Viewer(herdr.socket_path, pane["pane_id"], pane.get("tab_id") or "", "", "bottom")
            save_state(config.state_path, adopted, last)  # recorded before anything may close it
            return adopted
    return None


def _target(herdr: Herdr, ctx: Context, viewer: Viewer | None) -> str | None:
    """Pane in the invoking tab to attach beside; never a viewer pane."""
    skip = {viewer.pane_id} if viewer is not None and viewer.server_socket == herdr.socket_path else set()
    candidates = [p["pane_id"] for p in herdr.panes()
                  if p.get("tab_id") == ctx.tab_id and p["pane_id"] not in skip]
    for choice in (ctx.focused_pane_id, viewer.origin_pane_id if viewer else ""):
        if choice and choice in candidates:
            return choice
    return candidates[0] if candidates else None


def _size(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HerdrError("bad_response", f"pane layout: {what} is not a positive integer")
    return value


def _ratio(herdr: Herdr, target: str, placement: str, config: Config) -> float:
    layout = herdr.layout(target)
    dim, minimum, fraction = (("height", MIN_ROWS, config.bottom_fraction) if placement == "bottom"
                              else ("width", MIN_COLS, config.side_fraction))
    tab = _size(layout["area"].get(dim), f"area {dim}")
    if not all(isinstance(p, dict) for p in layout["panes"]):
        raise HerdrError("bad_response", "pane layout: pane entry is not an object")
    entry = next((p for p in layout["panes"] if p.get("pane_id") == target), None)
    if entry is None:
        return split_ratio(fraction, tab, tab, minimum)
    rect = entry.get("rect")
    if not isinstance(rect, dict):
        raise HerdrError("bad_response", f"pane layout: no rect for {target}")
    return split_ratio(fraction, tab, _size(rect.get(dim), f"rect {dim}"), minimum)


def _show(placement: str, target: str, ctx: Context, herdr: Herdr, config: Config, plugin_id: str) -> str:
    ratio = _ratio(herdr, target, placement, config)
    # Marker first: if this action dies before the record is saved, the next one (from any herdr
    # server) scans this server for the orphan. Replaced below by the record, or cleared.
    save_state(config.state_path, None, placement, pending=Pending(herdr.socket_path, time.time()))
    pane = herdr.open_viewer_in_new_tab(plugin_id, ctx.workspace_id)
    try:
        moved = herdr.move_beside(pane, ctx.tab_id, target, _DIRECTION[placement], ratio)
    except HerdrError:
        try:
            _close(herdr, pane, herdr.socket_path)
            save_state(config.state_path, None, placement)
        except HerdrError:
            # not confirmed closed: keep it recorded (tab unknown) so the next action closes it;
            # the move error is what the user needs to see
            save_state(config.state_path, Viewer(herdr.socket_path, pane, "", "", placement), placement)
        raise
    save_state(config.state_path, Viewer(herdr.socket_path, moved, ctx.tab_id, target, placement), placement)
    return moved


def _in_this_tab(viewer: Viewer | None, herdr: Herdr, ctx: Context) -> bool:
    return viewer is not None and viewer.server_socket == herdr.socket_path and viewer.tab_id == ctx.tab_id


def _notice(herdr: Herdr, body: str, title: str = "repetui") -> None:
    notify_user(herdr, title, body, notify_mac=_mac_notify)


def toggle(placement: str, ctx: Context, herdr: Herdr, config: Config, *, plugin_id: str = "repetui.bar") -> str:
    with global_lock(config.lock_path):
        _settle_pending(herdr, config, ctx)
        viewer = _current_viewer(herdr, config, ctx)
        if _in_this_tab(viewer, herdr, ctx) and viewer.placement == placement:
            _close(herdr, viewer.pane_id, viewer.server_socket)
            save_state(config.state_path, None, placement)
            if viewer.origin_pane_id:
                try:
                    herdr.focus(viewer.origin_pane_id)
                except HerdrError as exc:
                    if exc.code not in GONE_CODES:
                        raise
                    # origin gone: herdr already focused a neighbour
            return "hidden"
        target = _target(herdr, ctx, viewer)
        if viewer is not None:
            _close(herdr, viewer.pane_id, viewer.server_socket)
            save_state(config.state_path, None, placement)
        if target is None:
            _notice(herdr, "No pane here to attach the bar to.")
            return "hidden"
        _show(placement, target, ctx, herdr, config, plugin_id)
        return "shown"


def quit_repetui(ctx: Context, herdr: Herdr, config: Config, *, plugin_id: str = "repetui.bar",
                 wait: float = 5.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> str:
    if socket_state(config.socket) != "live":
        _notice(herdr, "Nothing to quit.", title="repetui is not running")
        return "not_running"
    with global_lock(config.lock_path):
        _settle_pending(herdr, config, ctx)
        viewer = _current_viewer(herdr, config, ctx)
        _, last = load_state(config.state_path)
        if _in_this_tab(viewer, herdr, ctx):
            last = viewer.placement
            herdr.focus(viewer.pane_id)
            pane = viewer.pane_id
        else:
            target = _target(herdr, ctx, viewer)
            if viewer is not None:
                _close(herdr, viewer.pane_id, viewer.server_socket)
                save_state(config.state_path, None, last)
            if target is None:
                _notice(herdr, "No pane here to show repetui in.")
                return "no_target"
            pane = _show(last, target, ctx, herdr, config, plugin_id)
        # The launcher's live-socket fast path takes no lock, so waiting here cannot deadlock.
        deadline = clock() + wait
        while True:
            processes = herdr.foreground_processes(pane)
            if is_attached(processes, config.socket):
                break
            if processes is None:                       # confirmed gone: the launcher gave up
                save_state(config.state_path, None, last)
                _notice(herdr, "The viewer closed before it attached; try again.")
                return "attach_timeout"
            if clock() > deadline:
                _notice(herdr, "The viewer did not attach; try again.")
                return "attach_timeout"
            sleep(0.1)
        herdr.send_keys(pane, "q")
    return "quit_sent"


_VISIBLE_DELIVERY = frozenset({"herdr", "terminal", "system"})


def herdr_toasts_visible(env: Mapping[str, str] = os.environ) -> bool:
    """herdr 0.9.5 reports shown:true even with ui.toast.delivery = "off" (Task 1 spike), so the
    effective delivery setting decides. Missing setting / unreadable file = herdr's default "off"."""
    try:
        with herdr_config_path(env).open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError, RuntimeError):  # RuntimeError: no home directory for "~"
        return False
    ui = data.get("ui")
    toast = ui.get("toast") if isinstance(ui, dict) else None
    delivery = toast.get("delivery", "off") if isinstance(toast, dict) else "off"
    return delivery in _VISIBLE_DELIVERY


def notify_user(herdr: Herdr, title: str, body: str, *, notify_mac: Callable[[str, str], None],
                toasts_visible: Callable[[], bool] | None = None) -> str:
    """herdr toast when it will actually be seen; otherwise a macOS notification."""
    visible = toasts_visible or herdr_toasts_visible
    try:
        if visible() and herdr.notify(title, body):
            return "warned_herdr"
    except Exception:  # any failure, including malformed output, falls back to macOS
        pass
    notify_mac(title, body)
    return "warned_macos"


def _mac_notify(title: str, body: str) -> None:
    script = f"display notification {json.dumps(body)} with title {json.dumps(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass  # last-resort channel: nothing further to report to


def startup(herdr: Herdr, config: Config, *, notify_mac: Callable[[str, str], None] | None = None,
            toasts_visible: Callable[[], bool] | None = None) -> str:
    if socket_state(config.socket) != "live":
        return "idle"
    return notify_user(herdr, "repetui is running in the background",
                       "Quit it before opening Anki Desktop (prefix+a, then q).",
                       notify_mac=notify_mac or _mac_notify, toasts_visible=toasts_visible)
