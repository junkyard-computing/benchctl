"""Wrapper around the local ``uart`` binary (companion to uartd).

uart owns the serial port via uartd; benchctl shells out to the configured
invocation per turn and parses ``--json``. The invocation prefix is taken from
config (e.g. ``["uart", "--socket", "/run/uartd.sock"]``).

Assumed ``--json`` contract (pin this against the real binary before hardware):
- ``read``/``peek``        -> {"text": "...", "lines": [...]}
- ``wait`` / ``send --expect`` -> {"matched": bool, "text": "..."}, exit non-zero on timeout
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from benchctl.device import Runner
from benchctl.errors import UartTimeout


@dataclass(frozen=True)
class UartResult:
    text: str
    matched: bool = False
    lines: list = None  # type: ignore[assignment]


def _parse(stdout: str) -> UartResult:
    try:
        data = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        data = {"text": stdout}
    return UartResult(
        text=data.get("text", ""),
        matched=bool(data.get("matched", False)),
        lines=data.get("lines") or [],
    )


class UartClient:
    def __init__(self, command: list[str], runner: Runner) -> None:
        self._command = list(command)
        self._runner = runner

    def _run(self, *args: str):
        return self._runner.run([*self._command, *args, "--json"])

    def read(self) -> UartResult:
        return _parse(self._run("read").stdout)

    def peek(self) -> UartResult:
        return _parse(self._run("peek").stdout)

    def send(self, text: str, *, expect: str | None = None, timeout: float | None = None) -> UartResult:
        args = ["send", text]
        if expect is not None:
            args += ["--expect", expect]
        if timeout is not None:
            args += ["--timeout", _fmt(timeout)]
        res = self._run(*args)
        if expect is not None and not res.ok:
            raise UartTimeout(f"send --expect {expect!r} timed out")
        return _parse(res.stdout)

    def wait(self, regex: str, *, timeout: float) -> UartResult:
        res = self._run("wait", regex, "--timeout", _fmt(timeout))
        if not res.ok:
            raise UartTimeout(f"wait {regex!r} timed out after {timeout}s")
        return _parse(res.stdout)


def _fmt(seconds: float) -> str:
    return str(int(seconds)) if float(seconds).is_integer() else str(seconds)
