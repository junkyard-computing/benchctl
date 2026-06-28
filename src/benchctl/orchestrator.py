"""The iteration loop(s) over injected seams — pure decision logic, no I/O here.

Two flash/recovery worlds, both expressed against the same seams:

- **pixel-ota / A-B**: from the home base (AOSP, SSH), flash the *inactive* slot's
  boot chain, reboot into it, classify over UART, recover to the home base. Recovery
  is a passive rollback wait with an optional power-cycle backstop.

- **uartfs / in-place** (felix mainline reality): the experiment slot is up on UART
  with no network; delta-flash its own boot partition in place, reboot, classify —
  *staying on the experiment slot*, never round-tripping the home base. Recovery to
  the home base is **retry-exhaustion**: reboot until the never-committed slot burns
  its retry budget and the bootloader rolls back (no power relay needed).

Safety invariants held across both:
- home base slot stays successful; never cleared; never auto-confirmed.
- post-stage, pre-reboot (A/B): assert experiment slot active-but-NOT-successful.
- no ``super`` write path exists.
- before a device-losing reboot: home base is a valid rollback anchor; power, if
  *required* by config, is reachable.
- respect the reboot/battery budget: refuse an iteration that can't safely complete.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from benchctl.bootctl import Bootctl
from benchctl.clock import Clock
from benchctl.config import Config
from benchctl.device import Device, RunResult
from benchctl.errors import CommandError, Refusal, Unrecoverable, UartTimeout
from benchctl.ota import Ota
from benchctl.power.base import Power
from benchctl.uart import UartClient

DEFAULT_SUCCESS_REGEX = r"login:|Reached target Multi-User|reached target multi-user"
# felix UART failure signatures (mainline bring-up).
DEFAULT_FAIL_REGEX = (
    r"Kernel panic|Unable to mount|Out of memory|No working init|"
    r"Ramdisk copy error|failed to boot android|watchdog"
)
# User-writable so ``scp`` (login user, not root) can stage; pixel-ota reads as root.
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
    # A/B: "rolled-back" | "wedged-recovered" | "unrecoverable"
    # uartfs: "iterated" (success, still on experiment) | "rolled-back" | "unrecoverable"
    outcome: str
    boot: BootResult
    timings: dict
    power_cycles: int = 0
    reboots: int = 0
    flash: str = "pixel-ota"


class Orchestrator:
    def __init__(
        self,
        *,
        device: Device,
        bootctl: Bootctl,
        ota: Ota,
        uart: UartClient,
        power: Power | None,
        clock: Clock,
        config: Config,
        experiment=None,   # UartDevice for the experiment slot (uartfs path)
        uartfs=None,       # UartfsClient for in-place flashing
    ) -> None:
        self.device = device
        self.bootctl = bootctl
        self.ota = ota
        self.uart = uart
        self.power = power
        self.clock = clock
        self.config = config
        self.experiment_dev = experiment
        self.uartfs = uartfs
        self.power_cycle_count = 0
        self.reboots_used = 0
        # Host-side cache of the last image flashed to each partition; it *is* the
        # current on-device content, so the next flash ships only a zstd delta.
        self._flash_base: dict[str, str] = {}

    # --- derived ---------------------------------------------------------

    @property
    def home_base(self) -> str:
        return self.config.slots.home_base

    @property
    def experiment(self) -> str:
        return "b" if self.home_base == "a" else "a"

    def _partlabel_for(self, image: str) -> str:
        # In-place flash targets the running (experiment) slot's partition.
        stem = os.path.basename(image)
        for suffix in (".img", ".bin"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        return f"{stem}_{self.experiment}"

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
        (if power is required) the power backend is reachable."""
        try:
            st = self.bootctl.status()
        except CommandError as exc:
            raise Refusal(f"home base unreachable over SSH: {exc}") from exc
        if st.active != self.home_base:
            raise Refusal(f"active slot is {st.active!r}, expected home base {self.home_base!r}")
        if not st.flags(self.home_base).successful:
            raise Refusal(f"home base slot {self.home_base!r} is not marked successful")
        if not self._power_reachable():
            raise Refusal("power backend is not reachable")

    def _guard_reboot(self) -> None:
        """Lighter check before a device-losing reboot. Does NOT require
        active==home_base (after staging the experiment slot is active by design)."""
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

    # --- stage (A/B, pixel-ota) -----------------------------------------

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

    def _classify_after(
        self,
        reboot_fn: Callable[[], object],
        success_regex: str,
        fail_regex: str,
        timeout: float,
    ) -> BootResult:
        # Drain stale console so we classify *this* boot, not a marker left over
        # from a previous one; `wait` then observes only the new boot's output.
        self.uart.read()
        reboot_fn()
        classification = "timeout"
        captured = ""
        try:
            captured = self.uart.wait(success_regex, timeout=timeout).text
            classification = "success"
        except UartTimeout:
            captured = self.uart.peek().text
            if re.search(fail_regex, captured):
                classification = "failed"
        return BootResult(classification=classification, console=captured)

    def boot_experiment(
        self,
        *,
        success_regex: str = DEFAULT_SUCCESS_REGEX,
        fail_regex: str = DEFAULT_FAIL_REGEX,
        timeout: float | None = None,
    ) -> BootResult:
        timeout = self.config.timeouts.boot if timeout is None else timeout
        self._guard_reboot()
        return self._classify_after(
            lambda: self._reboot_via_ssh(), success_regex, fail_regex, timeout
        )

    # --- reboots (budget-counted) ----------------------------------------

    def _reboot_via_ssh(self) -> RunResult:
        self.reboots_used += 1
        return self.device.run(["reboot"], sudo=True)

    def _reboot_experiment(self) -> RunResult:
        """Reboot the experiment slot. Over UART (uartfs) when we have that
        transport; otherwise via the main device. No-op-safe when the slot is
        already down (the bootloader auto-retries on its own)."""
        self.reboots_used += 1
        if self.experiment_dev is not None:
            return self.experiment_dev.run(["reboot"], sudo=True)
        return self.device.run(["reboot"], sudo=True)

    # --- recover ---------------------------------------------------------

    def recover(
        self,
        *,
        rollback_timeout: float | None = None,
        power_cycle_timeout: float | None = None,
        poll_interval: float | None = None,
        rollback_reboots: int | None = None,
    ) -> str:
        poll_interval = poll_interval if poll_interval is not None else self.config.timeouts.poll_interval
        self.power_cycle_count = 0

        via = self.config.slots.rollback_via
        if via == "retry-exhaustion":
            reboots = rollback_reboots if rollback_reboots is not None else self.config.slots.rollback_reboots
            return self._recover_retry_exhaustion(reboots, poll_interval)
        if via == "power":
            rollback_timeout = rollback_timeout if rollback_timeout is not None else self.config.timeouts.rollback_wait
            power_cycle_timeout = power_cycle_timeout if power_cycle_timeout is not None else self.config.timeouts.power_cycle_wait
            return self._recover_power(rollback_timeout, power_cycle_timeout, poll_interval)
        if via == "fastboot":
            raise Refusal(
                "rollback_via=fastboot needs an operator UART↔fastboot cable swap; "
                "not an autonomous recovery path"
            )
        raise Refusal(f"unknown slots.rollback_via {via!r}")

    def _recover_power(self, rollback_timeout, power_cycle_timeout, poll_interval) -> str:
        if self._wait_for_home_base(rollback_timeout, poll_interval):
            return "rolled-back"
        if self.config.power.enabled and self.power is not None:
            self.power.cycle()
            self.power_cycle_count += 1
            if self._wait_for_home_base(power_cycle_timeout, poll_interval):
                return "wedged-recovered"
        raise Unrecoverable(
            "home base did not return after the rollback window"
            + ("" if self.config.power.enabled else " (no power backstop configured)")
        )

    def _recover_retry_exhaustion(self, rollback_reboots: int, poll_interval: float) -> str:
        """Reboot the experiment until its never-committed slot burns its retry
        budget and the bootloader rolls back to the home base."""
        for _ in range(rollback_reboots):
            if self._home_base_up():
                return "rolled-back"
            self._reboot_experiment()
            self.clock.sleep(poll_interval)  # let the boot settle (virtual time)
        if self._home_base_up():
            return "rolled-back"
        raise Unrecoverable(
            f"home base did not return after exhausting {rollback_reboots} experiment reboots"
        )

    # --- iterate: A/B (pixel-ota) ---------------------------------------

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
        self._check_reboot_budget(1 + self.config.slots.rollback_reboots)
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
            outcome = "unrecoverable"
        timings["recover"] = self.clock.now() - t2

        return IterateResult(
            outcome=outcome, boot=boot, timings=timings,
            power_cycles=self.power_cycle_count, reboots=self.reboots_used, flash="pixel-ota",
        )

    # --- iterate: in-place (uartfs) -------------------------------------

    def iterate_uartfs(
        self,
        images: list[str],
        *,
        success_regex: str = DEFAULT_SUCCESS_REGEX,
        fail_regex: str = DEFAULT_FAIL_REGEX,
        boot_timeout: float | None = None,
        recover_on_fail: bool = True,
    ) -> IterateResult:
        if self.uartfs is None or self.experiment_dev is None:
            raise Refusal("uartfs flash path requires a uartfs transport (none configured)")
        self._check_reboot_budget(1 + self.config.slots.rollback_reboots)
        timeout = self.config.timeouts.boot if boot_timeout is None else boot_timeout
        timings: dict[str, float] = {}

        self._assert_experiment_up()

        t0 = self.clock.now()
        for img in images:
            part = self._partlabel_for(img)
            self.uartfs.flash(img, part, base=self._flash_base.get(part))
            self._flash_base[part] = img  # next iteration delta-flashes against this
        timings["flash"] = self.clock.now() - t0

        t1 = self.clock.now()
        boot = self._classify_after(self._reboot_experiment, success_regex, fail_regex, timeout)
        timings["boot"] = self.clock.now() - t1

        if boot.classification == "success":
            # Stay on the experiment slot — the whole point of the in-place loop.
            return IterateResult(
                outcome="iterated", boot=boot, timings=timings,
                reboots=self.reboots_used, flash="uartfs",
            )

        # Bad flash: the experiment is wedged; bring the home base back.
        outcome = "failed"
        if recover_on_fail:
            t2 = self.clock.now()
            try:
                outcome = self.recover()
            except Unrecoverable:
                outcome = "unrecoverable"
            timings["recover"] = self.clock.now() - t2
        return IterateResult(
            outcome=outcome, boot=boot, timings=timings,
            power_cycles=self.power_cycle_count, reboots=self.reboots_used, flash="uartfs",
        )

    # --- helpers ---------------------------------------------------------

    def _assert_experiment_up(self) -> None:
        if not self.uartfs.ping():
            raise Refusal("experiment slot is not up on UART (uartfs agent not responding)")

    def _check_reboot_budget(self, estimated_reboots: int) -> None:
        budget = self.config.battery.reboot_budget
        if budget and self.reboots_used + estimated_reboots > budget:
            raise Refusal(
                f"reboot budget {budget} would be exceeded "
                f"({self.reboots_used} used + up to {estimated_reboots} this iteration); "
                "charge the device (park in fastboot) before continuing"
            )

    def _power_reachable(self) -> bool:
        if not self.config.power.enabled:
            return True  # power not required when backend is none
        if self.power is None:
            return False
        try:
            return bool(self.power.reachable())
        except Exception:
            return False

    def _home_base_up(self) -> bool:
        # Recovery is "done" only when the home base is a valid rollback anchor
        # again: booted on the home base slot AND still marked successful.
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
