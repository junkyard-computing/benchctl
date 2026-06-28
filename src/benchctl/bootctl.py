"""pixel-bootctl wrapper (A/B slot primitive, runs on the device over SSH).

Drives, does not reimplement. The parsed ``status`` format is the contract this
codes against:

    active=<a|b>
    a successful=<true|false> retries=<int>
    b successful=<true|false> retries=<int>

If the real binary's output differs, only the parser below changes — the
structured ``SlotStatus`` the orchestrator consumes stays put.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchctl.device import Device
from benchctl.errors import CommandError

BINARY = "pixel-bootctl"
SLOTS = ("a", "b")


@dataclass(frozen=True)
class SlotFlags:
    successful: bool
    retries: int


@dataclass(frozen=True)
class SlotStatus:
    active: str
    slots: dict[str, SlotFlags]

    def flags(self, slot: str) -> SlotFlags:
        return self.slots[slot]

    @property
    def inactive(self) -> str:
        return "b" if self.active == "a" else "a"


def _parse_bool(token: str) -> bool:
    return token.strip().lower() in ("1", "true", "yes")


def _parse_status(text: str) -> SlotStatus:
    active = ""
    slots: dict[str, SlotFlags] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("active="):
            active = line.split("=", 1)[1].strip()
            continue
        parts = line.split()
        slot = parts[0]
        if slot not in SLOTS:
            continue
        kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        slots[slot] = SlotFlags(
            successful=_parse_bool(kv.get("successful", "false")),
            retries=int(kv.get("retries", "0")),
        )
    if not active or not slots:
        raise CommandError([BINARY, "status"], 0, f"unparseable status: {text!r}")
    return SlotStatus(active=active, slots=slots)


class Bootctl:
    def __init__(self, device: Device) -> None:
        self._dev = device

    def _run(self, *args: str):
        res = self._dev.run([BINARY, *args], sudo=True)  # reads devinfo / UFS sysfs — root
        if not res.ok:
            raise CommandError([BINARY, *args], res.returncode, res.stderr)
        return res

    def status(self) -> SlotStatus:
        return _parse_status(self._run("status").stdout)

    def set_active_slot(self, slot: str) -> None:
        self._run("set-active-slot", slot)

    def mark_successful(self) -> None:
        self._run("mark-successful")
