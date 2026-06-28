"""Sim uartfs transport: a Runner that answers `uartfs` verbs from the model.

Mirrors the uartfs exit-code contract: 0 ok (remote result in payload), 2 when
the transport is down (experiment slot not up on UART).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from benchctl.device import RunResult
from benchctl.sim.fake_device import SimDevice

_VERBS = {"run", "flash", "pull", "push"}
_CONN_DOWN = RunResult(2, "", "uartfs: experiment slot not reachable")


class SimUartfs:
    def __init__(self, sim: SimDevice) -> None:
        self._sim = sim

    def run(self, argv: Sequence[str]) -> RunResult:
        argv = [a for a in argv if a != "--json"]
        verb = next((a for a in argv if a in _VERBS), None)
        rest = argv[argv.index(verb) + 1 :] if verb else []

        if verb == "run":
            remote = self._sim.uartfs_run(rest[0] if rest else "")
            if remote is None:
                return _CONN_DOWN
            return _ok({"stdout": remote.stdout, "stderr": remote.stderr, "rc": remote.returncode})

        if verb == "flash":
            image, partlabel = rest[0], rest[1]
            if "--dry-run" in rest:
                return _ok({"ok": True, "dry_run": True})
            if not self._sim.uartfs_flash(image, partlabel):
                return _CONN_DOWN
            return _ok({"ok": True, "sha256": "0" * 64, "bytes_sent": 4096})

        if verb in ("pull", "push"):
            if self._sim.booted != self._sim.experiment:
                return _CONN_DOWN
            return _ok({"ok": True, "bytes": 0})

        return _CONN_DOWN


def _ok(payload: dict) -> RunResult:
    return RunResult(0, json.dumps(payload), "")
