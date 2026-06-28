"""Shared test doubles."""

from __future__ import annotations

from collections.abc import Sequence

from benchctl.device import RunResult


class RecordingDevice:
    """A Device/Runner double that records argv and replays scripted results.

    Queue results with ``queue(returncode, stdout, stderr)``; each ``run`` pops
    the next, or returns an empty success if the queue is empty.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.sudo_flags: list[bool] = []
        self.pushes: list[tuple[str, str]] = []
        self._results: list[RunResult] = []

    def queue(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> "RecordingDevice":
        self._results.append(RunResult(returncode, stdout, stderr))
        return self

    def run(self, argv: Sequence[str], *, sudo: bool = False) -> RunResult:
        self.calls.append(list(argv))
        self.sudo_flags.append(sudo)
        if self._results:
            return self._results.pop(0)
        return RunResult(0, "", "")

    def push(self, local: str, remote: str) -> None:
        self.pushes.append((local, remote))

    @property
    def last_call(self) -> list[str]:
        return self.calls[-1]


class FakeClock:
    """Deterministic clock: ``sleep`` advances virtual time, never blocks."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def power_config(**slots):
    """Config with a power backend enabled and rollback_via=power (legacy A/B)."""
    from benchctl.config import Config, PowerConfig, SlotConfig, SSHConfig

    return Config(
        ssh=SSHConfig(host="sim", user="root"),
        slots=SlotConfig(rollback_via="power", **slots),
        power=PowerConfig(backend="sim", address="sim"),
    )


def make_orchestrator(sim, *, power=None, clock=None, config=None):
    """Wire an Orchestrator over a SimDevice with the real wrappers + transports."""
    from benchctl.bootctl import Bootctl
    from benchctl.config import Config, SSHConfig
    from benchctl.device import UartDevice
    from benchctl.orchestrator import Orchestrator
    from benchctl.ota import Ota
    from benchctl.sim import SimPower, SimUart, SimUartfs
    from benchctl.uart import UartClient
    from benchctl.uartfs import UartfsClient

    config = config or Config(ssh=SSHConfig(host="sim", user="root"))
    uart = UartClient(["uart"], SimUart(sim))
    uartfs = UartfsClient(["uartfs"], SimUartfs(sim))
    return Orchestrator(
        device=sim,
        bootctl=Bootctl(sim),
        ota=Ota(sim),
        uart=uart,
        power=power if power is not None else SimPower(sim),
        clock=clock or FakeClock(),
        config=config,
        experiment=UartDevice(uartfs, uart),
        uartfs=uartfs,
    )
