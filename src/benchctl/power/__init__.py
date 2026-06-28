"""Power backend registry + factory.

``create_power(cfg)`` builds the backend named by ``cfg.backend``. Transports are
injectable (``http``/``runner``/``sleep``) so the suite drives backends with no
network, subprocess, or real waiting.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from benchctl.config import PowerConfig
from benchctl.errors import BenchctlError
from benchctl.power.base import HttpClient, Power, UrllibHttpClient
from benchctl.power.shelly import ShellyPower
from benchctl.power.tasmota import TasmotaPower
from benchctl.power.uhubctl import UhubctlPower

__all__ = ["Power", "create_power", "UnknownBackend"]


class UnknownBackend(BenchctlError):
    """Configured power backend name is not registered."""


def create_power(
    cfg: PowerConfig,
    *,
    http: HttpClient | None = None,
    runner=None,
    sleep: Callable[[float], None] | None = None,
) -> Power:
    sleep = sleep or time.sleep
    opts = cfg.options or {}
    delay = float(opts.get("cycle_delay", 5.0))
    http_timeout = float(opts.get("timeout", 5.0))

    if cfg.backend == "tasmota":
        return TasmotaPower(
            _require_address(cfg),
            http=http or UrllibHttpClient(),
            sleep=sleep,
            cycle_delay=delay,
            timeout=http_timeout,
        )
    if cfg.backend == "shelly":
        return ShellyPower(
            _require_address(cfg),
            http=http or UrllibHttpClient(),
            sleep=sleep,
            channel=int(opts.get("channel", 0)),
            cycle_delay=delay,
            timeout=http_timeout,
        )
    if cfg.backend == "uhubctl":
        from benchctl.device import LocalRunner

        return UhubctlPower(
            location=opts["location"],
            port=int(opts["port"]),
            runner=runner or LocalRunner(),
            sleep=sleep,
            cycle_delay=delay,
        )
    raise UnknownBackend(f"unknown power backend: {cfg.backend!r}")


def _require_address(cfg: PowerConfig) -> str:
    if not cfg.address:
        raise UnknownBackend(f"power backend {cfg.backend!r} requires an address")
    return cfg.address
