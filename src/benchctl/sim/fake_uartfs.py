"""Sim uartfs transport: a Runner that answers real `uartfs` verbs from the model.

Mirrors the uartd UF5–UF8 CLI: no --json; `run` returns the device command's raw
stdout/stderr and exit code; exit 2 == link/daemon down (experiment not up); flash
reports a human line on stderr. Global flags (--sudo/--socket/...) are ignored.
"""

from __future__ import annotations

from collections.abc import Sequence

from benchctl.device import RunResult
from benchctl.sim.fake_device import SimDevice

_VERBS = {"ping", "run", "flash", "pull", "push", "install-module", "bootstrap", "quit"}
_LINK_DOWN = RunResult(2, "", "uartfs: link error: agent not responding")


class SimUartfs:
    def __init__(self, sim: SimDevice) -> None:
        self._sim = sim

    def run(self, argv: Sequence[str]) -> RunResult:
        argv = list(argv)
        verb = next((a for a in argv if a in _VERBS), None)
        rest = argv[argv.index(verb) + 1 :] if verb else []

        if verb == "ping":
            return RunResult(0, "agent ready (v1)\n", "") if self._sim.agent_reachable else _LINK_DOWN

        if verb == "bootstrap":
            if self._sim.uartfs_bootstrap():
                return RunResult(0, "", "agent bootstrapped and ready (v1)")
            return RunResult(3, "", "uartfs: agent did not come up after bootstrap")

        if verb == "run":
            remote = self._sim.uartfs_run(" ".join(rest))
            return remote if remote is not None else _LINK_DOWN

        if verb == "flash":
            positionals = [a for a in rest if not a.startswith("--")]
            image = positionals[0] if positionals else ""
            partlabel = positionals[1] if len(positionals) > 1 else ""
            target = f"/dev/disk/by-partlabel/{partlabel}"
            if "--dry-run" in rest:
                return RunResult(0, "", f"[dry-run] would flash to {target}")
            if not self._sim.uartfs_flash(image, partlabel):
                return _LINK_DOWN
            kind = "delta-flashed" if "--base" in rest else "flashed"
            return RunResult(
                0, "", f"{kind} 4096 bytes to {target} (sha256 {'0' * 64}) — read-back verified"
            )

        if verb in ("pull", "push", "install-module"):
            return RunResult(0, "", "ok") if self._sim.agent_reachable else _LINK_DOWN

        return RunResult(0, "", "")  # quit
