"""The outer loop: stage → assert-rollback-armed → boot → classify → recover.

Pure decision logic over injected seams (device/bootctl/ota/uart/power/clock).
No subprocess, socket, or real sleep here — that's what makes it testable and
what keeps the safety invariants auditable in one place:

- home base slot stays successful; never cleared.
- benchctl never ``confirm``s the experiment slot, never marks it successful.
- post-stage, pre-reboot: assert the experiment slot is active-but-NOT-successful.
- never writes ``super`` (no flash-rootfs path exists here).
- before any device-losing reboot: home base healthy + power reachable, else refuse.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from benchctl.bootctl import Bootctl
from benchctl.clock import Clock
from benchctl.config import Config
from benchctl.device import Device
from benchctl.errors import CommandError, Refusal, Unrecoverable, UartTimeout
from benchctl.ota import Ota
from benchctl.power.base import Power
from benchctl.uart import UartClient

DEFAULT_SUCCESS_REGEX = r"login:|Reached target Multi-User"
DEFAULT_FAIL_REGEX = r"Kernel panic|Unable to mount|Out of memory"
# User-writable so ``scp`` (which runs as the login user, not root) can stage
# here; ``pixel-ota update`` then reads it as root.
DEFAULT_REMOTE_DIR = "/tmp/benchctl/staged"


@dataclass(frozen=True)
class BootResult:
    classification: str  # "success" | "failed" | "timeout"
    console: str


@dataclass(frozen=True)
class StatusReport:
    reachable: bool
    active: str | None
    home_base: str
    home_base_healthy: bool
    power_reachable: bool
    slots: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IterateResult:
    outcome: str  # "rolled-back" | "wedged-recovered" | "unrecoverable"
    boot: BootResult
    timings: dict
    power_cycles: int = 0


class Orchestrator:
    def __init__(
        self,
        *,
        device: Device,
        bootctl: Bootctl,
        ota: Ota,
        uart: UartClient,
        power: Power,
        clock: Clock,
        config: Config,
    ) -> None:
        self.device = device
        self.bootctl = bootctl
        self.ota = ota
        self.uart = uart
        self.power = power
        self.clock = clock
        self.config = config
        self.power_cycle_count = 0

    # --- derived ---------------------------------------------------------

    @property
    def home_base(self) -> str:
        return self.config.slots.home_base

    @property
    def experiment(self) -> str:
        return "b" if self.home_base == "a" else "a"

    # --- status / preflight ---------------------------------------------

    def status(self) -> StatusReport:
        try:
            st = self.bootctl.status()
        except CommandError:
            return StatusReport(
                reachable=False,
                active=None,
                home_base=self.home_base,
                home_base_healthy=False,
                power_reachable=self._power_reachable(),
            )
        healthy = st.active == self.home_base and st.flags(self.home_base).successful
        return StatusReport(
            reachable=True,
            active=st.active,
            home_base=self.home_base,
            home_base_healthy=healthy,
            power_reachable=self._power_reachable(),
            slots={s: vars(st.flags(s)) for s in st.slots},
        )

    def verify_home_base(self) -> None:
        """Refuse unless SSH is up, on the home base slot, marked successful, and
        the power backend is reachable. Anything else is a hard refusal."""
        try:
            st = self.bootctl.status()
        except CommandError as exc:
            raise Refusal(f"home base unreachable over SSH: {exc}") from exc
        if st.active != self.home_base:
            raise Refusal(
                f"active slot is {st.active!r}, expected home base {self.home_base!r}"
            )
        if not st.flags(self.home_base).successful:
            raise Refusal(f"home base slot {self.home_base!r} is not marked successful")
        if not self._power_reachable():
            raise Refusal("power backend is not reachable")

    def _guard_reboot(self) -> None:
        """Lighter check run immediately before a device-losing reboot. Does NOT
        require active==home_base (after staging the experiment slot is active by
        design); it ensures we can still talk to a bootable system, the home base
        slot remains a valid rollback anchor, and power is reachable."""
        try:
            st = self.bootctl.status()
        except CommandError as exc:
            raise Refusal(f"device unreachable over SSH before reboot: {exc}") from exc
        if not st.flags(self.home_base).successful:
            raise Refusal(
                f"home base slot {self.home_base!r} is not marked successful — "
                "no safe rollback anchor; refusing to reboot"
            )
        if not self._power_reachable():
            raise Refusal("power backend is not reachable; refusing to reboot")

    # --- stage -----------------------------------------------------------

    def stage(self, images: list[str], remote_dir: str = DEFAULT_REMOTE_DIR) -> None:
        self.verify_home_base()

        self.device.run(["mkdir", "-p", remote_dir])
        for img in images:
            self.device.push(img, f"{remote_dir}/{os.path.basename(img)}")

        self.ota.update(remote_dir)

        # Assert the rollback is actually armed before we hand the device a reboot.
        st = self.bootctl.status()
        if st.active != self.experiment:
            raise Refusal(
                f"after update active slot is {st.active!r}, expected experiment "
                f"{self.experiment!r}"
            )
        if st.flags(self.experiment).successful:
            raise Refusal(
                f"experiment slot {self.experiment!r} is marked SUCCESSFUL after staging — "
                "rollback would be defeated; aborting"
            )

    # --- boot + classify -------------------------------------------------

    def boot_experiment(
        self,
        *,
        success_regex: str = DEFAULT_SUCCESS_REGEX,
        fail_regex: str = DEFAULT_FAIL_REGEX,
        timeout: float | None = None,
    ) -> BootResult:
        timeout = self.config.timeouts.boot if timeout is None else timeout

        self._guard_reboot()
        self.device.run(["reboot"], sudo=True)

        classification = "timeout"
        try:
            self.uart.wait(success_regex, timeout=timeout)
            classification = "success"
        except UartTimeout:
            console = self.uart.peek().text
            if re.search(fail_regex, console):
                classification = "failed"

        return BootResult(classification=classification, console=self.uart.peek().text)

    # --- recover ---------------------------------------------------------

    def recover(
        self,
        *,
        rollback_timeout: float | None = None,
        power_cycle_timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> str:
        rollback_timeout = rollback_timeout if rollback_timeout is not None else self.config.timeouts.rollback_wait
        power_cycle_timeout = power_cycle_timeout if power_cycle_timeout is not None else self.config.timeouts.power_cycle_wait
        poll_interval = poll_interval if poll_interval is not None else self.config.timeouts.poll_interval

        self.power_cycle_count = 0
        if self._wait_for_home_base(rollback_timeout, poll_interval):
            return "rolled-back"

        # Backstop: exactly one power cycle, then wait again.
        self.power.cycle()
        self.power_cycle_count += 1
        if self._wait_for_home_base(power_cycle_timeout, poll_interval):
            return "wedged-recovered"

        raise Unrecoverable(
            "home base did not return after rollback window and one power cycle"
        )

    # --- iterate ---------------------------------------------------------

    def iterate(
        self,
        images: list[str],
        *,
        success_regex: str = DEFAULT_SUCCESS_REGEX,
        fail_regex: str = DEFAULT_FAIL_REGEX,
        boot_timeout: float | None = None,
        rollback_timeout: float | None = None,
        power_cycle_timeout: float | None = None,
        poll_interval: float | None = None,
        remote_dir: str = DEFAULT_REMOTE_DIR,
    ) -> IterateResult:
        timings: dict[str, float] = {}

        t0 = self.clock.now()
        self.stage(images, remote_dir)  # stage() verifies home base first
        timings["stage"] = self.clock.now() - t0

        t1 = self.clock.now()
        boot = self.boot_experiment(
            success_regex=success_regex, fail_regex=fail_regex, timeout=boot_timeout
        )
        timings["boot"] = self.clock.now() - t1

        t2 = self.clock.now()
        try:
            outcome = self.recover(
                rollback_timeout=rollback_timeout,
                power_cycle_timeout=power_cycle_timeout,
                poll_interval=poll_interval,
            )
        except Unrecoverable:
            timings["recover"] = self.clock.now() - t2
            return IterateResult(
                outcome="unrecoverable", boot=boot, timings=timings,
                power_cycles=self.power_cycle_count,
            )
        timings["recover"] = self.clock.now() - t2

        return IterateResult(
            outcome=outcome, boot=boot, timings=timings,
            power_cycles=self.power_cycle_count,
        )

    # --- helpers ---------------------------------------------------------

    def _power_reachable(self) -> bool:
        try:
            return bool(self.power.reachable())
        except Exception:
            return False

    def _home_base_up(self) -> bool:
        # Recovery is only "done" when the home base is a *valid rollback anchor*
        # again: booted on the home base slot AND still marked successful. A slot
        # that came up but lost its success flag is one bad boot from fastboot, so
        # we don't declare victory on ``active`` alone.
        try:
            st = self.bootctl.status()
        except CommandError:
            return False
        return st.active == self.home_base and st.flags(self.home_base).successful

    def _wait_for_home_base(self, timeout: float, poll_interval: float) -> bool:
        deadline = self.clock.now() + timeout
        while True:
            if self._home_base_up():
                return True
            if self.clock.now() >= deadline:
                return False
            self.clock.sleep(poll_interval)
