"""Wrapper around the local ``uartfs`` binary — delta-flash + reliable exec over UART.

uartfs rides the serial console owned by uartd, framing/ACK'ing/sha256-verifying a
delta-aware transport to the experiment slot (which has no network). benchctl shells
out to the configured invocation and parses ``--json``.

The operations benchctl needs:
- ``run <cmd>``            reliable remote exec → {stdout, stderr, rc}; also the
                           experiment-slot ``Device.run`` primitive.
- ``flash <img> <part>``   delta-flash a partition vs its live contents, verify,
                           dd, read-back-verify → {ok, sha256, ...}.
- ``pull <remote> <out>``  snapshot an on-device file/partition for diff-base.

Process exit mirrors uart: 0 ok · 1 op-failure · 2 daemon/conn · 3 uartfs/remote.
On success ``run`` carries the *remote* command's rc inside the payload.

The real uartfs CLI is not finalized (uartd UF5); this is the assumed contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from benchctl.device import RunResult, Runner
from benchctl.errors import UartfsError

EXIT_OK = 0
EXIT_OP_FAILURE = 1
EXIT_CONN = 2
EXIT_REMOTE = 3


@dataclass(frozen=True)
class FlashResult:
    ok: bool
    sha256: str | None = None
    bytes_sent: int | None = None


class UartfsClient:
    def __init__(self, command: list[str], runner: Runner) -> None:
        self._command = list(command)
        self._runner = runner

    def _run(self, *args: str) -> RunResult:
        return self._runner.run([*self._command, *args, "--json"])

    def _payload(self, res: RunResult, op: str) -> dict:
        # Any non-zero uartfs exit is a transport/op failure, not a remote result.
        if res.returncode != EXIT_OK:
            raise UartfsError(f"uartfs {op} failed (exit {res.returncode}): {res.stderr.strip()}")
        try:
            return json.loads(res.stdout) if res.stdout.strip() else {}
        except json.JSONDecodeError as exc:
            raise UartfsError(f"uartfs {op}: unparseable output: {res.stdout!r}") from exc

    def run(self, cmd: str) -> RunResult:
        """Run a shell command on the experiment slot; return its remote result."""
        data = self._payload(self._run("run", cmd), "run")
        return RunResult(
            returncode=int(data.get("rc", 0)),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
        )

    def flash(self, image: str, partlabel: str, *, dry_run: bool = False) -> FlashResult:
        args = ["flash", image, partlabel]
        if dry_run:
            args.append("--dry-run")
        data = self._payload(self._run(*args), "flash")
        if not data.get("ok", False):
            raise UartfsError(f"uartfs flash {partlabel}: {data.get('error', 'not ok')}")
        return FlashResult(
            ok=True,
            sha256=data.get("sha256"),
            bytes_sent=data.get("bytes_sent"),
        )

    def pull(self, remote: str, local: str) -> dict:
        return self._payload(self._run("pull", remote, local), "pull")

    def push(self, local: str, remote: str) -> dict:
        return self._payload(self._run("push", local, remote), "push")
