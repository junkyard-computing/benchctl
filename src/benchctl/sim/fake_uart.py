"""Sim UART transport: a Runner that answers `uart` verbs from the model console.

Reads SimDevice.console synchronously — ``wait`` matches against whatever the
boot has already emitted, so timeout paths return immediately (no real waiting).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from benchctl.device import RunResult
from benchctl.sim.fake_device import SimDevice

_VERBS = {"read", "peek", "send", "wait", "log", "status"}


class SimUart:
    def __init__(self, sim: SimDevice) -> None:
        self._sim = sim
        self._read_offset = 0

    def run(self, argv: Sequence[str], *, sudo: bool = False) -> RunResult:
        argv = list(argv)
        verb = next((a for a in argv if a in _VERBS), None)
        rest = argv[argv.index(verb) + 1 :] if verb else []

        if verb == "read":
            text = self._sim.console[self._read_offset :]
            self._read_offset = len(self._sim.console)
            return _ok({"text": text, "lines": []})
        if verb == "peek":
            return _ok({"text": self._sim.console, "lines": []})
        if verb == "send":
            return _ok({"text": ""})
        if verb == "wait":
            regex = rest[0] if rest else ""
            return self._wait(regex)
        if verb == "log":
            return _ok({"text": "/var/log/uartd/sim.log"})
        return _ok({"text": self._sim.console})

    def _wait(self, regex: str) -> RunResult:
        # uartd's `wait` observes a rolling window from when the call starts, not
        # the whole forensic log — so it matches only output since the last read,
        # never a stale marker from a previous boot.
        window = self._sim.console[self._read_offset :]
        match = re.search(regex, window)
        if match:
            return _ok({"matched": True, "text": window})
        return RunResult(1, json.dumps({"matched": False, "text": window}), "")


def _ok(payload: dict) -> RunResult:
    return RunResult(0, json.dumps(payload), "")
