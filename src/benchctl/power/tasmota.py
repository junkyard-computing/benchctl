"""Tasmota HTTP power backend (single relay)."""

from __future__ import annotations

from collections.abc import Callable

from benchctl.power.base import HttpClient


class TasmotaPower:
    def __init__(
        self,
        address: str,
        *,
        http: HttpClient,
        sleep: Callable[[float], None],
        cycle_delay: float = 5.0,
        timeout: float = 5.0,
    ) -> None:
        self._base = address.rstrip("/")
        self._http = http
        self._sleep = sleep
        self._cycle_delay = cycle_delay
        self._timeout = timeout

    def _cmnd(self, value: str) -> None:
        self._http.get(f"{self._base}/cm?cmnd=Power%20{value}", timeout=self._timeout)

    def off(self) -> None:
        self._cmnd("Off")

    def on(self) -> None:
        self._cmnd("On")

    def cycle(self) -> None:
        self.off()
        self._sleep(self._cycle_delay)
        self.on()

    def reachable(self) -> bool:
        try:
            return self._http.get(f"{self._base}/cm?cmnd=Status", timeout=self._timeout).status == 200
        except Exception:
            return False
