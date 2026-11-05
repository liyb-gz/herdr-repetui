"""Thin herdr access: CLI via HERDR_BIN_PATH, raw socket only where the CLI can't."""

from __future__ import annotations

import json
import socket
import subprocess
import uuid
from collections.abc import Callable

# herdr 0.9.5 (Task 1 spike): pane.get/process_info on a gone pane -> "pane_not_found";
# plugin.pane.close on a gone or non-plugin pane -> "plugin_pane_not_found". Both mean the viewer
# is confirmed gone. Timeouts and malformed replies are deliberately NOT here.
MISSING_PANE_CODE = "pane_not_found"
GONE_CODES = frozenset({MISSING_PANE_CODE, "plugin_pane_not_found", "server_unreachable"})


class HerdrError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _bad(message: str) -> HerdrError:
    return HerdrError("bad_response", message)


def _need_dict(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise _bad(f"{what}: expected an object")
    return value


def _error_from_value(error: object, what: str) -> HerdrError:
    """Build the HerdrError for a parsed `error` member; a malformed one is bad_response."""
    if not isinstance(error, dict):
        return _bad(f"{what}: error is not an object")
    code = error.get("code")
    if not isinstance(code, str) or not code:
        return _bad(f"{what}: error has no code")
    return HerdrError(code, str(error.get("message", "")))


def _error_from(text: str) -> HerdrError:
    """Interpret the output of a failed CLI call (non-zero exit)."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return HerdrError("cli_failed", str(text).strip()[:200])
    if not isinstance(parsed, dict) or "error" not in parsed:
        return HerdrError("cli_failed", str(text).strip()[:200])
    if "result" in parsed:
        return _bad("cli: both result and error")
    return _error_from_value(parsed["error"], "cli")


def _check_processes(processes: object, pane_id: str) -> list[dict]:
    if processes is None:
        raise _bad(f"pane.process_info for {pane_id}: foreground_processes is null")
    if not isinstance(processes, list):
        raise _bad(f"pane.process_info for {pane_id}: foreground_processes is not a list")
    for process in processes:
        if not isinstance(process, dict):
            raise _bad(f"pane.process_info for {pane_id}: foreground process is not an object")
        argv = process.get("argv")
        if argv is not None and not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
            raise _bad(f"pane.process_info for {pane_id}: argv is not a list of strings")
    return processes


class Herdr:
    def __init__(self, bin_path: str, socket_path: str, *,
                 run: Callable[..., object] = subprocess.run, timeout: float = 5.0) -> None:
        self.bin_path = bin_path
        self.socket_path = socket_path
        self._run = run
        self._timeout = timeout

    def cli(self, *args: str) -> dict:
        try:
            proc = self._run([self.bin_path, *args], capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise HerdrError("timeout", f"herdr {' '.join(args[:2])}") from exc
        except FileNotFoundError as exc:
            raise HerdrError("no_herdr", str(exc)) from exc
        if proc.returncode != 0:
            raise _error_from(proc.stderr or proc.stdout)
        if not proc.stdout.strip():
            return {}
        try:
            parsed = json.loads(proc.stdout)
        except ValueError as exc:
            raise _bad(proc.stdout[:200]) from exc
        if not isinstance(parsed, dict):
            raise _bad(f"herdr {' '.join(args[:2])}: output is not an object")
        if "error" in parsed:
            if "result" in parsed:
                raise _bad(f"herdr {' '.join(args[:2])}: both result and error")
            raise _error_from_value(parsed["error"], f"herdr {' '.join(args[:2])}")
        return parsed

    def request(self, method: str, params: dict, *, socket_path: str | None = None) -> dict:
        request_id = f"repetui-{uuid.uuid4().hex[:8]}"
        payload = json.dumps({"id": request_id, "method": method, "params": params}).encode() + b"\n"
        data = b""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self._timeout)
                conn.connect(socket_path or self.socket_path)
                conn.sendall(payload)
                while b"\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise HerdrError("server_unreachable", str(exc)) from exc
        except TimeoutError as exc:
            raise HerdrError("timeout", method) from exc
        except OSError as exc:
            raise HerdrError("transport", str(exc)) from exc
        if b"\n" not in data:
            raise HerdrError("no_response", f"{method}: connection closed without a response")
        try:
            response = json.loads(data.split(b"\n", 1)[0])
        except ValueError as exc:
            raise _bad(method) from exc
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise _bad(f"{method}: id mismatch")
        # exactly one of result/error, so a contradictory reply is never read as a disappearance code
        if "error" in response and "result" in response:
            raise _bad(f"{method}: both result and error")
        if "error" in response:
            raise _error_from_value(response["error"], method)
        if not isinstance(response.get("result"), dict):
            raise _bad(f"{method}: no result object")
        return response

    def open_viewer_in_new_tab(self, plugin_id: str, workspace_id: str) -> str:
        envelope = self.cli("plugin", "pane", "open", "--plugin", plugin_id, "--entrypoint", "review",
                            "--placement", "tab", "--workspace", workspace_id, "--no-focus")
        # schema: {"result": {"type": "plugin_pane_opened", "plugin_pane": {"pane": {"pane_id": ...}}}}
        result = _need_dict(envelope.get("result"), "plugin pane open result")
        if result.get("type") != "plugin_pane_opened":
            raise _bad(f"plugin pane open: unexpected result type {result.get('type')!r}")
        plugin_pane = _need_dict(result.get("plugin_pane"), "plugin pane open plugin_pane")
        pane = _need_dict(plugin_pane.get("pane"), "plugin pane open pane")
        pane_id = pane.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            raise _bad("plugin pane open: no pane id")
        return pane_id

    def move_beside(self, pane_id: str, tab_id: str, target_pane_id: str, direction: str, ratio: float) -> str:
        envelope = self.cli("pane", "move", pane_id, "--tab", tab_id, "--target-pane", target_pane_id,
                            "--split", direction, "--ratio", f"{ratio:.4f}", "--focus")
        # schema: {"result": {"move_result": {"changed": bool, "pane": {"pane_id": ...}, "reason"?}}}
        result = _need_dict(envelope.get("result"), "pane move result")
        move = _need_dict(result.get("move_result"), "pane move move_result")
        changed = move.get("changed")
        if not isinstance(changed, bool):
            raise _bad("pane move: changed is not a boolean")
        if not changed:
            raise HerdrError("not_moved", str(move.get("reason", "unknown")))
        new_id = _need_dict(move.get("pane"), "pane move pane").get("pane_id")
        if not isinstance(new_id, str) or not new_id:
            raise _bad("pane move: no pane id")
        return new_id

    def close_viewer(self, pane_id: str, socket_path: str) -> None:
        if socket_path == self.socket_path:
            envelope = self.cli("plugin", "pane", "close", pane_id)
        else:
            envelope = self.request("plugin.pane.close", {"pane_id": pane_id}, socket_path=socket_path)
        result = envelope.get("result")
        # schema: {"type": "plugin_pane_closed", "pane_id": ...}, both required
        if (not isinstance(result, dict) or result.get("type") != "plugin_pane_closed"
                or result.get("pane_id") != pane_id):
            raise _bad(f"plugin.pane.close: {str(envelope)[:200]}")

    def focus(self, pane_id: str) -> None:
        self.request("pane.focus", {"pane_id": pane_id})

    def foreground_processes(self, pane_id: str, socket_path: str | None = None) -> list[dict] | None:
        try:
            response = self.request("pane.process_info", {"pane_id": pane_id}, socket_path=socket_path)
        except HerdrError as exc:
            if exc.code in GONE_CODES:
                return None
            raise
        # schema: {"type": "pane_process_info", "process_info": {"pane_id", "foreground_processes", ...}}
        result = response["result"]
        if result.get("type") != "pane_process_info":
            raise _bad(f"pane.process_info for {pane_id}: unexpected result type")
        info = result.get("process_info")
        if not isinstance(info, dict) or info.get("pane_id") != pane_id:
            raise _bad(f"pane.process_info for {pane_id}: wrong or missing process_info")
        if "foreground_processes" not in info:  # the schema allows omitting it
            return []
        return _check_processes(info["foreground_processes"], pane_id)

    def panes(self) -> list[dict]:
        # `pane list` without --workspace covers all workspaces (verified on herdr 0.9.5)
        envelope = self.cli("pane", "list")
        listed = _need_dict(envelope.get("result"), "pane list result").get("panes")
        if not isinstance(listed, list):
            raise _bad("pane list: panes is not a list")
        panes = []
        for pane in listed:
            if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str):
                raise _bad("pane list: entry without a string pane_id")
            for key in ("tab_id", "workspace_id"):
                if pane.get(key) is not None and not isinstance(pane[key], str):
                    raise _bad(f"pane list: {key} is not a string")
            panes.append({"pane_id": pane["pane_id"], "tab_id": pane.get("tab_id"),
                          "workspace_id": pane.get("workspace_id")})
        return panes

    def layout(self, pane_id: str) -> dict:
        envelope = self.cli("pane", "layout", "--pane", pane_id)
        layout = _need_dict(_need_dict(envelope.get("result"), "pane layout result").get("layout"),
                            "pane layout layout")
        if not isinstance(layout.get("area"), dict) or not isinstance(layout.get("panes"), list):
            raise _bad("pane layout: layout needs an area object and a panes list")
        return layout

    def notify(self, title: str, body: str) -> bool:
        try:
            envelope = self.cli("notification", "show", title, "--body", body)
        except HerdrError as exc:
            if exc.code == "bad_response":  # unconfirmed, so not "shown"; transport errors still raise
                return False
            raise
        result = envelope.get("result")
        return isinstance(result, dict) and result.get("shown") is True

    def send_keys(self, pane_id: str, *keys: str) -> None:
        self.cli("pane", "send-keys", pane_id, *keys)
