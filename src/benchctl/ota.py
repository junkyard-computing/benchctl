"""pixel-ota wrapper (boot-chain flasher, runs on the device over SSH).

Drives, does not reimplement. ``update`` flashes the *inactive* slot's boot chain
and switches rollback-safe (target active, NOT successful). benchctl never calls
``flash-rootfs`` (destructive/rollback-free) and never auto-``confirm``s an
experiment slot.
"""

from __future__ import annotations

from benchctl.device import Device
from benchctl.errors import CommandError

BINARY = "pixel-ota"


class Ota:
    def __init__(self, device: Device) -> None:
        self._dev = device

    def _run(self, *args: str):
        res = self._dev.run([BINARY, *args], sudo=True)  # writes block devices — root
        if not res.ok:
            raise CommandError([BINARY, *args], res.returncode, res.stderr)
        return res

    def update(
        self,
        remote_dir: str,
        *,
        slot: str | None = None,
        no_switch: bool = False,
        dry_run: bool = False,
    ) -> None:
        args = ["update", remote_dir]
        if slot is not None:
            args += ["--slot", slot]
        if no_switch:
            args.append("--no-switch")
        if dry_run:
            args.append("--dry-run")
        self._run(*args)

    def confirm(self) -> None:
        self._run("confirm")
