"""Shelly (gen1) HTTP power backend."""

from __future__ import annotations

from collections.abc import Callable

from benchctl.power.base import HttpClient


class ShellyPower:
    def __init__(
        self,
        address: str,
        *,
        http: HttpClient,
        sleep: Callable[[float], None],
        channel: int = 0,
        cycle_delay: float = 5.0,
        timeout: float = 5.0,
    ) -> None:
        self._base = address.rstrip("/")
        self._http = http
        self._sleep = sleep
        self._channel = channel
        self._cycle_delay = cycle_delay
        self._timeout = timeout

    def _turn(self, state: str) -> None:
        self._http.get(f"{self._base}/relay/{self._channel}?turn={state}", timeout=self._timeout)

    def off(self) -> None:
        self._turn("off")

    def on(self) -> None:
        self._turn("on")

    def cycle(self) -> None:
        self.off()
        self._sleep(self._cycle_delay)
        self.on()

    def reachable(self) -> bool:
        try:
            return self._http.get(f"{self._base}/status", timeout=self._timeout).status == 200
        except Exception:
            return False
