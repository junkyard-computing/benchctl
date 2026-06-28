"""uhubctl power backend — cut/restore USB port power on a smart hub."""

from __future__ import annotations

from collections.abc import Callable

from benchctl.device import Runner

BINARY = "uhubctl"


class UhubctlPower:
    def __init__(
        self,
        *,
        location: str,
        port: int,
        runner: Runner,
        sleep: Callable[[float], None],
        cycle_delay: float = 5.0,
    ) -> None:
        self._location = location
        self._port = str(port)
        self._runner = runner
        self._sleep = sleep
        self._cycle_delay = cycle_delay

    def _action(self, action: str) -> int:
        res = self._runner.run([BINARY, "-a", action, "-l", self._location, "-p", self._port])
        return res.returncode

    def off(self) -> None:
        self._action("off")

    def on(self) -> None:
        self._action("on")

    def cycle(self) -> None:
        self.off()
        self._sleep(self._cycle_delay)
        self.on()

    def reachable(self) -> bool:
        res = self._runner.run([BINARY, "-l", self._location])
        return res.returncode == 0
