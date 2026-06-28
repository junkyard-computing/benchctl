"""Sim power backend: a Power that drives the model's cold-boot selection."""

from __future__ import annotations

from benchctl.sim.fake_device import SimDevice


class SimPower:
    def __init__(self, sim: SimDevice, *, reachable: bool = True) -> None:
        self._sim = sim
        self._reachable = reachable

    def off(self) -> None:
        self._sim.booted = None

    def on(self) -> None:
        self._sim.power_cycle()

    def cycle(self) -> None:
        self._sim.power_cycle()

    def reachable(self) -> bool:
        return self._reachable
