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

    # Simulation mode (hardware-free). The sim-* knobs reproduce the scenarios.
    parser.add_argument("--sim", action="store_true", help="run against the in-process simulation")
    parser.add_argument("--sim-boots", choices=["good", "bad"], default="bad")
    parser.add_argument("--sim-rollback-after", default="2", help="N probes, or 'none' to never roll back")
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

def _build_real(args):
    from benchctl.bootctl import Bootctl
    from benchctl.clock import RealClock
    from benchctl.config import load_config
    from benchctl.device import LocalRunner, SSHDevice
    from benchctl.orchestrator import Orchestrator
    from benchctl.ota import Ota
    from benchctl.power import create_power
    from benchctl.uart import UartClient

    cfg = load_config(path=args.config, env=dict(os.environ))
    device = SSHDevice(
        cfg.ssh.host,
        cfg.ssh.user,
        cfg.ssh.key,
        cfg.ssh.port,
        sudo=cfg.ssh.sudo,
        connect_timeout=cfg.ssh.connect_timeout,
        command_timeout=cfg.ssh.command_timeout,
    )
    return Orchestrator(
        device=device,
        bootctl=Bootctl(device),
        ota=Ota(device),
        uart=UartClient(cfg.uart.command, LocalRunner()),
        power=create_power(cfg.power),
        clock=RealClock(),
        config=cfg,
    )


def _build_sim(args):
    from benchctl.bootctl import Bootctl
    from benchctl.clock import InstantClock
    from benchctl.config import Config, SSHConfig
    from benchctl.orchestrator import Orchestrator
    from benchctl.ota import Ota
    from benchctl.sim import SimDevice, SimPower, SimUart
    from benchctl.uart import UartClient

    rollback_after = None if str(args.sim_rollback_after).lower() == "none" else int(args.sim_rollback_after)
    sim = SimDevice(
        experiment_boots=args.sim_boots,
        rollback_after=rollback_after,
        update_marks_successful=args.sim_mark_successful,
        power_cycle_recovers=not args.sim_no_power_recovers,
    )
    if args.sim_home_unhealthy:
        sim.slots["a"].successful = False

    cfg = Config(ssh=SSHConfig(host="sim", user="root"))
    return Orchestrator(
        device=sim,
        bootctl=Bootctl(sim),
        ota=Ota(sim),
        uart=UartClient(["uart"], SimUart(sim)),
        power=SimPower(sim, reachable=not args.sim_power_unreachable),
        clock=InstantClock(),
        config=cfg,
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
    res = orch.iterate(args.images, boot_timeout=timeout, **kw)
    payload = {
        "outcome": res.outcome,
        "boot": {"classification": res.boot.classification, "console": res.boot.console},
        "power_cycles": res.power_cycles,
        "timings": res.timings,
    }
    human = f"outcome: {res.outcome} (boot={res.boot.classification}, power_cycles={res.power_cycles})"
    _emit(args, payload, human)
    return EXIT_UNRECOVERABLE if res.outcome == "unrecoverable" else EXIT_OK


def _cmd_recover(orch, args) -> int:
    outcome = orch.recover()
    _emit(args, {"outcome": outcome, "power_cycles": orch.power_cycle_count}, f"outcome: {outcome}")
    return EXIT_OK


def _cmd_power(orch, args) -> int:
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
