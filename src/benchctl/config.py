"""Configuration loading and merge.

Precedence (low to high): built-in defaults < TOML file < environment < flag overrides.

Environment keys are ``BENCHCTL_<SECTION>_<KEY>`` (e.g. ``BENCHCTL_SSH_HOST``,
``BENCHCTL_TIMEOUTS_ROLLBACK_WAIT``). Unknown sections/keys are ignored so an
operator's unrelated env doesn't break a run.
"""

from __future__ import annotations

import shlex
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the merged configuration is missing required fields."""


@dataclass
class SSHConfig:
    host: str
    user: str
    key: str | None = None
    port: int = 22
    # Wrap privileged on-device commands in ``sudo -n`` (the documented ``kalm``
    # login has passwordless sudo). Set false only when logging in as root.
    sudo: bool = True
    connect_timeout: float = 10.0
    command_timeout: float = 120.0


@dataclass
class SlotConfig:
    home_base: str = "a"


@dataclass
class PowerConfig:
    backend: str | None = None
    address: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class UartConfig:
    command: list[str] = field(default_factory=lambda: ["uart"])


@dataclass
class Timeouts:
    boot: float = 120.0
    rollback_wait: float = 180.0
    power_cycle_wait: float = 180.0
    ssh_probe: float = 5.0
    poll_interval: float = 5.0


@dataclass
class Config:
    ssh: SSHConfig
    slots: SlotConfig = field(default_factory=SlotConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    uart: UartConfig = field(default_factory=UartConfig)
    timeouts: Timeouts = field(default_factory=Timeouts)


_SECTIONS = {
    "ssh": SSHConfig,
    "slots": SlotConfig,
    "power": PowerConfig,
    "uart": UartConfig,
    "timeouts": Timeouts,
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _env_to_dict(env: dict[str, str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for raw_key, value in env.items():
        if not raw_key.startswith("BENCHCTL_"):
            continue
        rest = raw_key[len("BENCHCTL_") :].lower()
        section, _, sub = rest.partition("_")
        if not sub or section not in _SECTIONS:
            continue
        out.setdefault(section, {})[sub] = value
    return out


def _build_section(cls: type, raw: dict[str, Any]):
    # `from __future__ import annotations` makes field.type a string ("float",
    # "int", "list[str]", ...); match on that to coerce env strings.
    kwargs: dict[str, Any] = {}
    type_by_name = {f.name: str(f.type) for f in fields(cls)}
    for name, value in raw.items():
        if name not in type_by_name:
            continue  # ignore unknown keys
        target = type_by_name[name]
        if name == "command":
            value = shlex.split(value) if isinstance(value, str) else list(value)
        elif target == "float":
            value = float(value)
        elif target == "int":
            value = int(value)
        elif target == "bool":
            value = _coerce_bool(value)
        kwargs[name] = value
    return cls(**kwargs)


def load_config(
    path: str | Path | None = None,
    env: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    merged: dict[str, Any] = {}

    if path is not None:
        with open(path, "rb") as fh:
            merged = _deep_merge(merged, tomllib.load(fh))

    if env:
        merged = _deep_merge(merged, _env_to_dict(env))

    if overrides:
        merged = _deep_merge(merged, overrides)

    ssh_raw = merged.get("ssh", {})
    if not ssh_raw.get("host"):
        raise ConfigError("ssh.host is required")
    if not ssh_raw.get("user"):
        raise ConfigError("ssh.user is required")

    return Config(
        ssh=_build_section(SSHConfig, ssh_raw),
        slots=_build_section(SlotConfig, merged.get("slots", {})),
        power=_build_section(PowerConfig, merged.get("power", {})),
        uart=_build_section(UartConfig, merged.get("uart", {})),
        timeouts=_build_section(Timeouts, merged.get("timeouts", {})),
    )
