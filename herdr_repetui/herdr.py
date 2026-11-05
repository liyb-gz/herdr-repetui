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


def _error_from(text: str) -> HerdrError:
    try:
        error = json.loads(text)["error"]
        return HerdrError(str(error.get("code", "error")), str(error.get("message", "")))
    except (ValueError, KeyError, TypeError):
        return HerdrError("cli_failed", text.strip()[:200])


def _first_pane_id(value: object) -> str | None:
    """Find result.*.pane.pane_id (e.g. .result.plugin_pane.pane.pane_id from plugin pane open)."""
    if isinstance(value, dict):
        pane = value.get("pane")
        if isinstance(pane, dict) and isinstance(pane.get("pane_id"), str):
            return pane["pane_id"]
        for child in value.values():
            found = _first_pane_id(child)
            if found:
                return found
    return None


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
        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else {}
        except ValueError as exc:
            raise HerdrError("bad_response", proc.stdout[:200]) from exc

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
            raise HerdrError("bad_response", method) from exc
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise HerdrError("bad_response", f"{method}: id mismatch")
        if "error" in response:
            error = response["error"] or {}
            raise HerdrError(str(error.get("code", "error")), str(error.get("message", "")))
        if "result" not in response:
            raise HerdrError("bad_response", f"{method}: no result")
        return response

    def open_viewer_in_new_tab(self, plugin_id: str, workspace_id: str) -> str:
        result = self.cli("plugin", "pane", "open", "--plugin", plugin_id, "--entrypoint", "review",
                          "--placement", "tab", "--workspace", workspace_id, "--no-focus")
        pane_id = _first_pane_id(result)
        if pane_id is None:
            raise HerdrError("no_pane_id", json.dumps(result)[:200])
        return pane_id

    def move_beside(self, pane_id: str, tab_id: str, target_pane_id: str, direction: str, ratio: float) -> str:
        result = self.cli("pane", "move", pane_id, "--tab", tab_id, "--target-pane", target_pane_id,
                          "--split", direction, "--ratio", f"{ratio:.4f}", "--focus")
        move = (result.get("result") or {}).get("move_result") or {}
        if not isinstance(move, dict) or not move.get("changed", False):
            raise HerdrError("not_moved", str(move.get("reason", "unknown")) if isinstance(move, dict) else "?")
        new_id = (move.get("pane") or {}).get("pane_id")
        if not isinstance(new_id, str) or not new_id:
            raise HerdrError("bad_response", "pane move: no pane id")
        return new_id

    def close_viewer(self, pane_id: str, socket_path: str) -> None:
        if socket_path == self.socket_path:
            envelope = self.cli("plugin", "pane", "close", pane_id)
        else:
            envelope = self.request("plugin.pane.close", {"pane_id": pane_id}, socket_path=socket_path)
        result = envelope.get("result") if isinstance(envelope, dict) else None
        # schema: {"type": "plugin_pane_closed", "pane_id": ...}, both required
        if (not isinstance(result, dict) or result.get("type") != "plugin_pane_closed"
                or result.get("pane_id") != pane_id):
            raise HerdrError("bad_response", f"plugin.pane.close: {str(envelope)[:200]}")

    def focus(self, pane_id: str) -> None:
        self.request("pane.focus", {"pane_id": pane_id})

    def foreground_processes(self, pane_id: str, socket_path: str | None = None) -> list[dict] | None:
        try:
            response = self.request("pane.process_info", {"pane_id": pane_id}, socket_path=socket_path)
        except HerdrError as exc:
            if exc.code in GONE_CODES:
                return None
            raise
        result = response["result"]
        # schema: {"type": "pane_process_info", "process_info": {"pane_id", "foreground_processes", ...}}
        if not isinstance(result, dict) or result.get("type") != "pane_process_info":
            raise HerdrError("bad_response", f"pane.process_info for {pane_id}")
        info = result.get("process_info")
        if not isinstance(info, dict) or info.get("pane_id") != pane_id:
            raise HerdrError("bad_response", f"pane.process_info for {pane_id}")
        processes = info.get("foreground_processes")
        return [p for p in processes if isinstance(p, dict)] if isinstance(processes, list) else []

    def panes(self) -> list[dict]:
        # `pane list` without --workspace covers all workspaces (verified on herdr 0.9.5)
        listed = self.cli("pane", "list").get("result", {}).get("panes", [])
        return [{"pane_id": p["pane_id"], "tab_id": p.get("tab_id"), "workspace_id": p.get("workspace_id")}
                for p in listed]

    def layout(self, pane_id: str) -> dict:
        return self.cli("pane", "layout", "--pane", pane_id)["result"]["layout"]

    def notify(self, title: str, body: str) -> bool:
        envelope = self.cli("notification", "show", title, "--body", body)
        result = envelope.get("result") if isinstance(envelope, dict) else None
        return isinstance(result, dict) and result.get("shown") is True

    def send_keys(self, pane_id: str, *keys: str) -> None:
        self.cli("pane", "send-keys", pane_id, *keys)
