"""In-memory model of the felix A/B bootloader + on-device tools.

It implements the ``Device`` protocol (run/push) by interpreting pixel-bootctl /
pixel-ota / reboot commands against a slot state machine, and models the
properties benchctl's safety depends on:

- ``super`` is shared, never slotted.
- ``pixel-ota update`` flashes the inactive slot and switches rollback-safe
  (target active, NOT successful).
- An experiment slot has no network → unreachable over SSH while booted.
- A non-successful active slot burns its retry budget and rolls back to the
  marked-good slot; a wedge never rolls back and needs a power cycle.

Scenario knobs make fail-then-rollback / wedge / mis-marking deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from benchctl.device import RunResult

HOME_BASE_BOOT = (
    "[    0.000000] Booting Linux on physical CPU 0x0\n"
    "[   12.345678] systemd[1]: Reached target Multi-User System.\n"
    "felix login: \n"
)
EXP_GOOD_BOOT = (
    "[    0.000000] Booting Linux (experiment) on physical CPU 0x0\n"
    "[   13.500000] systemd[1]: Reached target Multi-User System.\n"
)
EXP_BAD_BOOT = (
    "[    0.000000] Booting Linux (experiment) on physical CPU 0x0\n"
    "[    3.210000] Kernel panic - not syncing: Attempted to kill init!\n"
)

SSH_DOWN = RunResult(255, "", "ssh: connect to host: Connection refused")


@dataclass
class _Slot:
    successful: bool
    retries: int


class SimDevice:
    def __init__(
        self,
        *,
        home_base: str = "a",
        experiment_boots: str = "bad",
        rollback_after: int | None = 2,
        update_marks_successful: bool = False,
        power_cycle_recovers: bool = True,
    ) -> None:
        self.home_base = home_base
        self.experiment = "b" if home_base == "a" else "a"
        self.power_cycle_recovers = power_cycle_recovers
        self.slots = {
            home_base: _Slot(successful=True, retries=7),
            self.experiment: _Slot(successful=False, retries=0),
        }
        self.active = home_base
        self.booted: str | None = home_base
        self.experiment_boots = experiment_boots
        self.rollback_after = rollback_after
        self.update_marks_successful = update_marks_successful

        self.console = ""
        self.staged_dir: str | None = None
        self.pushes: list[tuple[str, str]] = []
        self.power_cycles = 0
        self._unreachable_probes = 0

    # --- connectivity ----------------------------------------------------

    @property
    def reachable(self) -> bool:
        return self.booted == self.home_base

    # --- Device protocol -------------------------------------------------

    def push(self, local: str, remote: str) -> None:
        self.pushes.append((local, remote))

    def run(self, argv: Sequence[str], *, sudo: bool = False) -> RunResult:
        argv = list(argv)  # sudo is a transport detail; the model ignores it

        # A non-successful experiment slot burns retries; after enough probes
        # the bootloader rolls back to the marked-good slot.
        if not self.reachable:
            self._unreachable_probes += 1
            if self.rollback_after is not None and self._unreachable_probes >= self.rollback_after:
                self._rollback()
            else:
                return SSH_DOWN

        return self._dispatch(argv)

    # --- command dispatch (reachable only) -------------------------------

    def _dispatch(self, argv: list[str]) -> RunResult:
        if argv and argv[0] == "reboot":
            return self._reboot()
        if argv[:1] == ["pixel-bootctl"]:
            return self._bootctl(argv[1:])
        if argv[:1] == ["pixel-ota"]:
            return self._ota(argv[1:])
        return RunResult(0, "", "")  # generic probe succeeds when reachable

    def _bootctl(self, args: list[str]) -> RunResult:
        if args[:1] == ["status"]:
            return RunResult(0, self._status_text(), "")
        if args[:1] == ["set-active-slot"]:
            self.active = args[1]
            return RunResult(0, "", "")
        if args[:1] == ["mark-successful"]:
            if self.booted:
                self.slots[self.booted].successful = True
                self.slots[self.booted].retries = 7
            return RunResult(0, "", "")
        return RunResult(2, "", f"unknown pixel-bootctl: {args}")

    def _ota(self, args: list[str]) -> RunResult:
        if args[:1] == ["confirm"]:
            if self.booted:
                self.slots[self.booted].successful = True
            return RunResult(0, "", "")
        if args[:1] != ["update"]:
            return RunResult(2, "", f"unknown pixel-ota: {args}")

        remote_dir = args[1]
        slot = self.experiment if self.active == self.home_base else self.home_base
        no_switch = "--no-switch" in args
        dry_run = "--dry-run" in args
        if "--slot" in args:
            slot = args[args.index("--slot") + 1]
        if slot == self.active:
            return RunResult(1, "", f"refusing to flash active slot {slot!r}")
        if dry_run:
            return RunResult(0, "", "")

        self.staged_dir = remote_dir
        self.slots[slot] = _Slot(successful=self.update_marks_successful, retries=0)
        if not no_switch:
            self.active = slot  # rollback-safe: active, NOT successful
        return RunResult(0, "", "")

    # --- boot model ------------------------------------------------------

    def _reboot(self) -> RunResult:
        self._boot(self.active)
        return RunResult(0, "", "")  # reboot command returns before link drops

    def _boot(self, slot: str) -> None:
        self.booted = slot
        self._unreachable_probes = 0
        if slot == self.home_base:
            self.console += HOME_BASE_BOOT
        else:
            self.console += EXP_GOOD_BOOT if self.experiment_boots == "good" else EXP_BAD_BOOT

    def _rollback(self) -> None:
        target = self.home_base if self.slots[self.home_base].successful else self.experiment
        self.active = target
        self._boot(target)

    def power_cycle(self) -> None:
        self.power_cycles += 1
        self.booted = None
        if not self.power_cycle_recovers:
            # Models an unrecoverable wedge: even a cold boot fails to come back.
            self._boot(self.experiment)
            return
        # Cold boot: pick the active slot if successful, else the marked-good slot.
        if self.slots[self.active].successful:
            target = self.active
        else:
            target = self.home_base if self.slots[self.home_base].successful else self.experiment
        self.active = target
        self._boot(target)

    # --- rendering -------------------------------------------------------

    def _status_text(self) -> str:
        lines = [f"active={self.active}"]
        for name in ("a", "b"):
            s = self.slots[name]
            lines.append(f"{name} successful={'true' if s.successful else 'false'} retries={s.retries}")
        return "\n".join(lines) + "\n"
