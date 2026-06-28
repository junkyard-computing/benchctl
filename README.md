# benchctl

Host-side orchestrator for **unattended mainline-kernel iteration** on a Pixel Fold
(felix / gs201). It flashes an experimental boot chain to the inactive A/B slot, boots it,
captures the result over UART, and **guarantees return to a known-good slot** — with no
human and no network on the device under test.

It *drives* existing on-device tools; it does not reimplement flashing or slot logic.

## Why it works this way (the non-obvious constraints)

- **`super` (rootfs) is single/shared, not A/B.** Only `boot`/`vendor_boot`/`dtbo` are slotted,
  so each iteration flashes the boot chain only and the experiment kernel boots on the shared
  rootfs. benchctl has **no code path that writes `super`**.
- **No fastboot in the loop** (it doesn't work through the bench USB hub). Flashing happens from
  the booted *home base* over SSH; recovery is A/B rollback or a **network power switch**.
- **The experiment slot has no network** (USB is what's under test). Its only output is the UART
  console, so classification is done over UART, not SSH.
- **Rollback safety hinges on the experiment slot being `active-but-NOT-successful`.** The two
  on-device tools' READMEs *disagree* on whether `set-active-slot` marks the target successful, and
  a slot mistakenly marked successful gives **no auto-rollback** (this has bitten before). benchctl
  therefore asserts via `pixel-bootctl status` after staging and **aborts** if the experiment slot
  reads successful.

## The loop — `benchctl iterate`

1. **Verify home base:** SSH up, on slot A, A marked successful, power backend reachable — else refuse.
2. **Stage + switch:** `scp` images → `pixel-ota update` (boot chain → inactive slot, rollback-safe switch).
3. **Assert rollback armed:** `pixel-bootctl status` → experiment slot active but **NOT** successful, else abort.
4. **Reboot + classify:** reboot, capture the boot over `uart`, classify by success/fail regex.
5. **Recover:** wait for SSH home base to return (rollback); if not within timeout → **exactly one**
   power-cycle → wait again; re-verify home base.
6. **Report:** outcome (`rolled-back` / `wedged-recovered` / `unrecoverable`), UART capture, timings.

## Safety invariants (enforced in `orchestrator.py`)

- Home base slot stays successful; never cleared. benchctl never `confirm`s the experiment slot.
- Post-stage, pre-reboot: experiment slot must be active-but-not-successful.
- No `super` write path exists (no `flash-rootfs`).
- Before any device-losing reboot: home base is a valid rollback anchor + power reachable, else refuse.

## Install / run

This is a [uv](https://docs.astral.sh/uv/) project.

```sh
uv sync                 # create the venv, install deps
uv run pytest           # the full hardware-free test suite
uv run benchctl --help
```

## Simulation mode (no hardware)

`--sim` wires an in-process model of the felix bootloader/slot machine, a scripted UART, and a
fake power switch — so the whole flow (including rollback and the power-cycle backstop) runs with
no device. The `--sim-*` knobs reproduce each scenario:

```sh
# 1. fail-then-rollback
uv run benchctl --json --sim --sim-boots bad --sim-rollback-after 2 iterate boot.img vendor_boot.img dtbo.img
# 2. wedge -> exactly one power-cycle
uv run benchctl --json --sim --sim-rollback-after none iterate boot.img vendor_boot.img dtbo.img
# 3. refusal (unhealthy home base / unreachable power)
uv run benchctl --sim --sim-home-unhealthy boot-experiment
uv run benchctl --sim --sim-power-unreachable boot-experiment
# 4. abort when the experiment slot reads successful after staging
uv run benchctl --sim --sim-mark-successful iterate boot.img vendor_boot.img dtbo.img
# 5. unrecoverable
uv run benchctl --json --sim --sim-rollback-after none --sim-no-power-recovers iterate boot.img vendor_boot.img dtbo.img
```

## CLI

```
benchctl [--json] [--config PATH] [--sim ...] <command>

status                                      home base health + slot flags
stage <imgs...>                             push + switch rollback-safe
boot-experiment [--success-regex R]         reboot, capture UART, classify
                [--fail-regex R] [--timeout S]
iterate <imgs...> [--success-regex ...]     the full loop
recover                                     wait for rollback, else one power-cycle
power {off|on|cycle}                         drive the power backend
```

Exit codes: `0` ok · `2` usage · `3` refusal · `4` unrecoverable · `5` boot classified not-success.

## Backends (pluggable)

- **Power** (`power.backend`): `tasmota` / `shelly` (HTTP) and `uhubctl` (USB hub port power) ship in
  [`src/benchctl/power/`](src/benchctl/power/). Interface is `off`/`on`/`cycle`/`reachable`.
- **On-device tools**: `pixel-bootctl` / `pixel-ota` are driven over SSH (`bootctl.py` / `ota.py`).
  `uart` is the local companion binary to `uartd`, invoked per the configured `uart.command`.

See [`benchctl.toml.sample`](benchctl.toml.sample) for configuration. Precedence is
defaults < file < environment (`BENCHCTL_<SECTION>_<KEY>`) < flags.

## Architecture

Every external effect sits behind an injectable seam — `Device` (SSH), `Runner` (local `uart`),
`Power`, `Clock` — so the orchestrator is pure decision logic and the suite runs in-process with no
network, subprocess, or real waiting. Built test-first; see [`plan.md`](plan.md).

## Status / caveats

Developed and tested entirely against the simulation. Two things to validate on real felix before
trusting it unattended:

1. The **rollback-vs-fastboot** behaviour when the experiment slot's retry budget exhausts.
2. The **`uart` `--json` contract** assumed in [`uart.py`](src/benchctl/uart.py) against the real binary.
