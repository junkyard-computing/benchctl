"""Wrapper around the local ``uartfs`` binary — delta-flash + reliable exec over UART.

uartfs (uartd workspace, UF5–UF8) rides the serial console owned by uartd, framing/
ACK'ing/sha256-verifying a delta-aware transport to the experiment slot's phone-side
agent. benchctl shells out to the configured invocation.

Real CLI contract (matched against uartd `crates/uartfs/src/main.rs`):
- Global flags (before the subcommand): ``--socket``, ``--chunk``, ``--device-dir``,
  ``--sudo`` (prefixes device-side privileged actions: push/pull/flash/install-module).
- ``ping``                         handshake with the agent.
- ``run <cmd...>``                 exec on device; stdout→stdout, stderr→stderr, exit =
                                   the *remote* command's code.
- ``push <local> <remote>``        verified file copy.
- ``pull <spec> <local|->``        read a file or ``partlabel:off:len`` slice.
- ``flash <img> <partlabel> [--base <local>] [--dry-run] [--raw-target]``
                                   delta-flash a partition (``--base`` ships a zstd delta),
                                   dd, read-back-verify.
- ``install-module <local.ko> [--insmod]``, ``bootstrap``, ``quit``.
- Exit codes: 0 ok · 1 device command non-zero (run) · 2 link/daemon · 3 transfer/verify.

There is **no ``--json``**; ``run`` output is the device command's raw stdout/stderr,
which is exactly what a ``Runner`` already captures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from benchctl.device import RunResult, Runner
from benchctl.errors import UartfsError

EXIT_OK = 0
EXIT_LINK = 2       # daemon/link error (not a remote result)
EXIT_TRANSFER = 3   # transfer / verify failure

_SHA_RE = re.compile(r"sha256\s+([0-9a-f]{64})")
_BYTES_RE = re.compile(r"(?:flashed|delta-flashed|pushed)\s+(\d+)\s+bytes")


@dataclass(frozen=True)
class FlashResult:
    ok: bool
    sha256: str | None = None
    bytes_sent: int | None = None


class UartfsClient:
    def __init__(self, command: list[str], runner: Runner, *, sudo: bool = True) -> None:
        self._command = list(command)
        self._runner = runner
        self._sudo = sudo

    # privileged device-side actions take the global --sudo; `run` does not
    # (the caller embeds sudo in the command string itself).
    def _privileged(self, *args: str) -> RunResult:
        prefix = [*self._command, *(["--sudo"] if self._sudo else [])]
        return self._runner.run([*prefix, *args])

    def ping(self) -> bool:
        return self._runner.run([*self._command, "ping"]).returncode == EXIT_OK

    def run(self, cmd: str) -> RunResult:
        """Run a shell command on the experiment slot; return its remote result.
        A link/daemon error (exit 2) is a transport failure, not a remote code."""
        res = self._runner.run([*self._command, "run", cmd])
        if res.returncode == EXIT_LINK:
            raise UartfsError(f"uartfs run: link/daemon error: {res.stderr.strip()}")
        return res  # returncode is the device command's exit code

    def flash(
        self,
        image: str,
        partlabel: str,
        *,
        base: str | None = None,
        dry_run: bool = False,
        raw_target: bool = False,
    ) -> FlashResult:
        args = ["flash", image, partlabel]
        if base is not None:
            args += ["--base", base]
        if dry_run:
            args.append("--dry-run")
        if raw_target:
            args.append("--raw-target")
        res = self._privileged(*args)
        if res.returncode != EXIT_OK:
            raise UartfsError(f"uartfs flash {partlabel}: {res.stderr.strip()}")
        return FlashResult(
            ok=True,
            sha256=_search(_SHA_RE, res.stderr),
            bytes_sent=_search_int(_BYTES_RE, res.stderr),
        )

    def pull(self, spec: str, local: str) -> None:
        res = self._privileged("pull", spec, local)
        if res.returncode != EXIT_OK:
            raise UartfsError(f"uartfs pull {spec}: {res.stderr.strip()}")

    def push(self, local: str, remote: str) -> None:
        res = self._privileged("push", local, remote)
        if res.returncode != EXIT_OK:
            raise UartfsError(f"uartfs push {remote}: {res.stderr.strip()}")

    def bootstrap(self) -> None:
        res = self._runner.run([*self._command, "bootstrap"])
        if res.returncode != EXIT_OK:
            raise UartfsError(f"uartfs bootstrap failed: {res.stderr.strip()}")


def _search(rx: re.Pattern, text: str) -> str | None:
    m = rx.search(text or "")
    return m.group(1) if m else None


def _search_int(rx: re.Pattern, text: str) -> int | None:
    m = rx.search(text or "")
    return int(m.group(1)) if m else None
