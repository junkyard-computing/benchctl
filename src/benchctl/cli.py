"""benchctl command-line entrypoint.

Builds an Orchestrator (from config + real backends, or from the in-process
simulation when ``--sim`` is given), dispatches the subcommand, and maps results
to stable exit codes:

    0  ok                3  refusal (safety precondition failed)
    1  error             4  unrecoverable (recovery exhausted)
    2  usage             5  boot-experiment classified not-success
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections.abc import Sequence

from benchctl import __version__
from benchctl.errors import BenchctlError, Refusal, Unrecoverable

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSAL = 3
EXIT_UNRECOVERABLE = 4
EXIT_BOOT_FAILED = 5


# --- parser ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchctl",
        description=(
            "Host orchestrator for unattended kernel iteration on a Pixel "
            "(felix/gs201): flash an experiment, boot it, capture UART, and "
            "guarantee return to a known-good slot."
        ),
    )
    parser.add_argument("--version", action="version", version=f"benchctl {__version__}")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--config", help="path to benchctl.toml")
    parser.add_argument(
        "--flash", choices=["uartfs", "pixel-ota", "fastboot"], help="flash backend override"
    )
    parser.add_argument(
        "--rollback-via", choices=["retry-exhaustion", "power", "fastboot"],
        help="recovery strategy override",
    )
    parser.add_argument("--reboot-budget", type=int, help="max reboots per iteration (0 = unenforced)")

    # Simulation mode (hardware-free). The sim-* knobs reproduce the scenarios.
    parser.add_argument("--sim", action="store_true", help="run against the in-process simulation")
    parser.add_argument("--sim-boots", choices=["good", "bad"], default="bad")
    parser.add_argument("--sim-rollback-after", default="2", help="N SSH probes, or 'none' to never roll back")
    parser.add_argument("--sim-experiment-retries", type=int, default=None, help="mainline reboot budget before rollback")
    parser.add_argument("--sim-on-experiment", action="store_true", help="start booted on the experiment slot (uartfs loop)")
    parser.add_argument("--sim-flash-bad", action="store_true", help="a uartfs flash makes the next boot panic")
    parser.add_argument("--sim-agent-down", action="store_true", help="uartfs agent not launched (exercise auto-bootstrap)")
    parser.add_argument("--sim-mark-successful", action="store_true", help="update wrongly marks experiment successful")
    parser.add_argument("--sim-no-power-recovers", action="store_true", help="cold boot also fails to recover")
    parser.add_argument("--sim-power-unreachable", action="store_true")
    parser.add_argument("--sim-home-unhealthy", action="store_true", help="home base slot not marked successful")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("status", help="report home base health and slot flags")

    p_stage = sub.add_parser("stage", help="push images and switch rollback-safe")
    p_stage.add_argument("images", nargs="+")

    p_boot = sub.add_parser("boot-experiment", help="reboot, capture UART, classify")
    p_boot.add_argument("--success-regex")
    p_boot.add_argument("--fail-regex")
    p_boot.add_argument("--timeout", type=float)

    p_iter = sub.add_parser("iterate", help="stage -> boot -> classify -> recover")
    p_iter.add_argument("images", nargs="+")
    p_iter.add_argument("--success-regex")
    p_iter.add_argument("--fail-regex")
    p_iter.add_argument("--timeout", type=float)

    sub.add_parser("recover", help="wait for rollback, else power-cycle once")

    p_power = sub.add_parser("power", help="drive the power backend")
    p_power.add_argument("action", choices=["off", "on", "cycle"])

    return parser


# --- orchestrator wiring --------------------------------------------------

def _apply_overrides(cfg, args) -> None:
    if args.flash:
        cfg.flash.backend = args.flash
    if args.rollback_via:
        cfg.slots.rollback_via = args.rollback_via
    if args.reboot_budget is not None:
        cfg.battery.reboot_budget = args.reboot_budget


def _build_real(args):
    from benchctl.bootctl import Bootctl
    from benchctl.clock import RealClock
    from benchctl.config import load_config
    from benchctl.device import LocalRunner, SSHDevice, UartDevice
    from benchctl.orchestrator import Orchestrator
    from benchctl.ota import Ota
    from benchctl.power import create_power
    from benchctl.uart import UartClient
    from benchctl.uartfs import UartfsClient

    cfg = load_config(path=args.config, env=dict(os.environ))
    _apply_overrides(cfg, args)
    device = SSHDevice(
        cfg.ssh.host,
        cfg.ssh.user,
        cfg.ssh.key,
        cfg.ssh.port,
        sudo=cfg.ssh.sudo,
        connect_timeout=cfg.ssh.connect_timeout,
        command_timeout=cfg.ssh.command_timeout,
    )
    runner = LocalRunner()
    uart = UartClient(cfg.uart.command, runner)
    uartfs = UartfsClient(cfg.uart.uartfs_command, runner, sudo=cfg.ssh.sudo)
    return Orchestrator(
        device=device,
        bootctl=Bootctl(device),
        ota=Ota(device),
        uart=uart,
        power=create_power(cfg.power) if cfg.power.enabled else None,
        clock=RealClock(),
        config=cfg,
        experiment=UartDevice(uartfs, uart),
        uartfs=uartfs,
    )


def _build_sim(args):
    from benchctl.bootctl import Bootctl
    from benchctl.clock import InstantClock
    from benchctl.config import Config, PowerConfig, SSHConfig
    from benchctl.device import UartDevice
    from benchctl.orchestrator import Orchestrator
    from benchctl.ota import Ota
    from benchctl.sim import SimDevice, SimPower, SimUart, SimUartfs
    from benchctl.uart import UartClient
    from benchctl.uartfs import UartfsClient

    rollback_after = None if str(args.sim_rollback_after).lower() == "none" else int(args.sim_rollback_after)
    sim = SimDevice(
        experiment_boots=args.sim_boots,
        rollback_after=rollback_after,
        update_marks_successful=args.sim_mark_successful,
        power_cycle_recovers=not args.sim_no_power_recovers,
        experiment_retries=args.sim_experiment_retries,
    )
    if args.sim_home_unhealthy:
        sim.slots["a"].successful = False
    if args.sim_flash_bad:
        sim.flash_outcome = "bad"
    if args.sim_on_experiment:
        sim.active = sim.experiment
        sim._boot(sim.experiment)
    if args.sim_agent_down:
        sim.agent_running = False

    cfg = Config(ssh=SSHConfig(host="sim", user="root"))
    _apply_overrides(cfg, args)
    if cfg.slots.rollback_via == "power":
        cfg.power = PowerConfig(backend="sim", address="sim")  # enable the power backstop

    uart = UartClient(["uart"], SimUart(sim))
    uartfs = UartfsClient(["uartfs"], SimUartfs(sim))
    return Orchestrator(
        device=sim,
        bootctl=Bootctl(sim),
        ota=Ota(sim),
        uart=uart,
        power=SimPower(sim, reachable=not args.sim_power_unreachable) if cfg.power.enabled else None,
        clock=InstantClock(),
        config=cfg,
        experiment=UartDevice(uartfs, uart),
        uartfs=uartfs,
    )


# --- output ---------------------------------------------------------------

def _emit(args, payload: dict, human: str) -> None:
    if args.json:
        print(json.dumps(payload))
    else:
        print(human)


# --- command handlers -----------------------------------------------------

def _cmd_status(orch, args) -> int:
    st = orch.status()
    payload = dataclasses.asdict(st)
    human = (
        f"reachable={st.reachable} active={st.active} "
        f"home_base={st.home_base} healthy={st.home_base_healthy} "
        f"power_reachable={st.power_reachable}"
    )
    _emit(args, payload, human)
    return EXIT_OK


def _cmd_stage(orch, args) -> int:
    orch.stage(args.images)
    _emit(args, {"staged": True}, "staged; experiment slot armed (active, not successful)")
    return EXIT_OK


def _classify_kwargs(args) -> dict:
    kw = {}
    if args.success_regex is not None:
        kw["success_regex"] = args.success_regex
    if args.fail_regex is not None:
        kw["fail_regex"] = args.fail_regex
    if args.timeout is not None:
        kw["timeout"] = args.timeout
    return kw


def _cmd_boot_experiment(orch, args) -> int:
    res = orch.boot_experiment(**_classify_kwargs(args))
    payload = {"classification": res.classification, "console": res.console}
    _emit(args, payload, f"boot classification: {res.classification}")
    return EXIT_OK if res.classification == "success" else EXIT_BOOT_FAILED


def _cmd_iterate(orch, args) -> int:
    kw = _classify_kwargs(args)
    timeout = kw.pop("timeout", None)
    if orch.config.flash.backend == "uartfs":
        res = orch.iterate_uartfs(args.images, boot_timeout=timeout, **kw)
    else:
        res = orch.iterate(args.images, boot_timeout=timeout, **kw)
    payload = {
        "outcome": res.outcome,
        "flash": res.flash,
        "boot": {"classification": res.boot.classification, "console": res.boot.console},
        "power_cycles": res.power_cycles,
        "reboots": res.reboots,
        "timings": res.timings,
    }
    human = (
        f"outcome: {res.outcome} (flash={res.flash}, boot={res.boot.classification}, "
        f"reboots={res.reboots}, power_cycles={res.power_cycles})"
    )
    _emit(args, payload, human)
    return EXIT_UNRECOVERABLE if res.outcome == "unrecoverable" else EXIT_OK


def _cmd_recover(orch, args) -> int:
    outcome = orch.recover()
    _emit(
        args,
        {"outcome": outcome, "power_cycles": orch.power_cycle_count, "reboots": orch.reboots_used},
        f"outcome: {outcome}",
    )
    return EXIT_OK


def _cmd_power(orch, args) -> int:
    if orch.power is None:
        _error(args, "refusal", "no power backend configured (power.backend = none)")
        return EXIT_REFUSAL
    getattr(orch.power, args.action)()
    _emit(args, {"power": args.action, "ok": True}, f"power {args.action}: ok")
    return EXIT_OK


_HANDLERS = {
    "status": _cmd_status,
    "stage": _cmd_stage,
    "boot-experiment": _cmd_boot_experiment,
    "iterate": _cmd_iterate,
    "recover": _cmd_recover,
    "power": _cmd_power,
}


# --- main -----------------------------------------------------------------

def main(argv: Sequence[str] | None = None, *, orchestrator=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    try:
        orch = orchestrator or (_build_sim(args) if args.sim else _build_real(args))
        return _HANDLERS[args.command](orch, args)
    except Refusal as exc:
        _error(args, "refusal", str(exc))
        return EXIT_REFUSAL
    except Unrecoverable as exc:
        _error(args, "unrecoverable", str(exc))
        return EXIT_UNRECOVERABLE
    except BenchctlError as exc:
        _error(args, "error", str(exc))
        return EXIT_ERROR


def _error(args, kind: str, message: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps({"error": kind, "message": message}))
    else:
        print(f"{kind}: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
