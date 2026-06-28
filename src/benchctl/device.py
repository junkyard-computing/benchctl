"""Command execution seams.

``Runner`` runs a command and returns its result. ``Device`` is a Runner that
also pushes files — used for the on-device tools reached over SSH. ``uart`` and
the power backends run locally and only need a Runner.

Concrete transports (SSHDevice, LocalRunner) wrap subprocess; tests and sim mode
inject in-memory doubles implementing the same Protocols.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from benchctl.errors import CommandError


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@runtime_checkable
class Runner(Protocol):
    def run(self, argv: Sequence[str], *, sudo: bool = False) -> RunResult: ...


@runtime_checkable
class Device(Runner, Protocol):
    def push(self, local: str, remote: str) -> None: ...


class LocalRunner:
    """Runs commands on the bench host (used for the local ``uart`` binary)."""

    def run(self, argv: Sequence[str], *, sudo: bool = False) -> RunResult:
        # ``sudo`` is meaningful only for the SSH transport; local helpers
        # (uart, uhubctl) run as the bench user. Accepted for protocol parity.
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, check=False
        )
        return RunResult(proc.returncode, proc.stdout, proc.stderr)


class SSHDevice:
    """Runs commands and pushes files on the felix device over SSH.

    Thin wrapper over the ``ssh``/``scp`` binaries; exercised on hardware, not in
    the test suite (the suite injects doubles).
    """

    def __init__(
        self,
        host: str,
        user: str,
        key: str | None = None,
        port: int = 22,
        *,
        sudo: bool = True,
        connect_timeout: float = 10.0,
        command_timeout: float = 120.0,
    ) -> None:
        self.host = host
        self.user = user
        self.key = key
        self.port = port
        # The on-device tools (pixel-ota/pixel-bootctl) and ``reboot`` need root;
        # the documented login (``kalm``) has passwordless sudo, so privileged
        # calls are wrapped in ``sudo -n``. Set ``sudo=False`` when logging in as
        # root on an image without sudo.
        self.sudo = sudo
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout

    def _ssh_base(self) -> list[str]:
        base = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={int(self.connect_timeout)}",
            "-p",
            str(self.port),
        ]
        if self.key:
            base += ["-i", self.key]
        base.append(f"{self.user}@{self.host}")
        return base

    def run(self, argv: Sequence[str], *, sudo: bool = False) -> RunResult:
        remote = " ".join(shlex.quote(a) for a in argv)
        if sudo and self.sudo:
            remote = f"sudo -n {remote}"
        try:
            proc = subprocess.run(
                [*self._ssh_base(), remote],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired:
            # A bounded failure rather than an unbounded hang — the recover loop
            # treats a non-zero result as "not yet home" and keeps to its deadline.
            return RunResult(
                124, "", f"ssh command timed out after {self.command_timeout:g}s: {remote}"
            )
        return RunResult(proc.returncode, proc.stdout, proc.stderr)

    def push(self, local: str, remote: str) -> None:
        scp = ["scp", "-B", "-o", f"ConnectTimeout={int(self.connect_timeout)}", "-P", str(self.port)]
        if self.key:
            scp += ["-i", self.key]
        scp += [local, f"{self.user}@{self.host}:{remote}"]
        try:
            subprocess.run(
                scp, capture_output=True, text=True, check=True, timeout=self.command_timeout
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            err = getattr(exc, "stderr", "") or ""
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            raise CommandError(scp, getattr(exc, "returncode", None) or 124, err) from exc
