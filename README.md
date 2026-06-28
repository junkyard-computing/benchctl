# benchctl

Host-side orchestrator for **unattended mainline-kernel iteration** on a Pixel Fold
(felix / gs201). It flashes an experimental boot chain to the inactive A/B slot, boots it,
captures the result over UART, and **guarantees return to a known-good slot** — with no
human and no network on the device under test.

It *drives* existing on-device tools; it does not reimplement flashing or slot logic.

## Two iteration worlds

benchctl supports two flash/recovery paths, chosen by `flash.backend`:

- **`uartfs` (felix mainline — the default).** The experiment slot runs the mainline kernel: it has
  **no network**, reachable only over UART. benchctl **delta-flashes its own boot partition in
  place** over UART (via the [`uartfs`](../../uartd) tool) and **stays on the experiment slot** —
  flash → reboot → classify → repeat, never round-tripping the home base. Recovery to the home base
  is **retry-exhaustion**: the experiment slot never self-commits, so rebooting it burns the
  bootloader's retry budget until it auto-rolls-back — **no power relay needed**.
- **`pixel-ota` (legacy A/B).** From the home base (AOSP, SSH), flash the *inactive* slot's boot
  chain, reboot into it, classify over UART, recover by passive rollback with an optional
  **power-cycle** backstop.

## The non-obvious constraints

- **`super` (rootfs) is single/shared, not A/B.** Only `boot`/`vendor_boot`/`dtbo` are slotted.
  benchctl has **no code path that writes `super`**.
- **No fastboot in the autonomous loop** — it shares the USB-C port with UART (a human cable swap).
  It's an opt-in, interactive recovery path only.
- **The experiment slot has no network.** Classification is over UART; on mainline, even *exec* is
  over UART (`uartfs run`), since SSH/pixel-* don't work there.
- **No power backend on felix** (battery-only; can't charge while rigged for UART). `power.backend`
  is optional (`none`); recovery doesn't require it. benchctl tracks a **reboot budget** and refuses
  to start an iteration that can't safely complete (the device has gone flat mid-session).
- **Rollback safety (A/B) hinges on the experiment slot being `active-but-NOT-successful`.** The two
  on-device READMEs *disagree* on whether `set-active-slot` marks the target successful, and a slot
  mistakenly marked successful gives **no auto-rollback** (this has bitten before). benchctl asserts
  via `pixel-bootctl status` after staging and **aborts** if the experiment slot reads successful.

## Safety invariants (enforced in `orchestrator.py`)

- Home base slot stays successful; never cleared. benchctl never `confirm`s the experiment slot.
- Post-stage, pre-reboot (A/B): experiment slot must be active-but-not-successful.
- No `super` write path exists (no `flash-rootfs`).
- Before any device-losing reboot: home base is a valid rollback anchor; power, *if required*, reachable.
- Never exceed the configured reboot/battery budget.

## Install / run

This is a [uv](https://docs.astral.sh/uv/) project.

```sh
uv sync                 # create the venv, install deps
uv run pytest           # the full hardware-free test suite
uv run benchctl --help
```

## Simulation mode (no hardware)

`--sim` wires an in-process model of the felix bootloader/slot machine, a scripted UART, a uartfs
transport, and a fake power switch — so both worlds (including retry-exhaustion rollback and the
power-cycle backstop) run with no device. The `--sim-*` knobs reproduce each scenario:

```sh
# --- uartfs world (felix mainline default) ---
# in-place flash, stay on the experiment slot
uv run benchctl --json --sim --sim-on-experiment --sim-boots good iterate boot.img vendor_boot.img dtbo.img
# a bad kernel flash -> retry-exhaustion rollback home (no power)
uv run benchctl --json --sim --sim-on-experiment --sim-boots good --sim-flash-bad --sim-rollback-after 2 \
    iterate boot.img vendor_boot.img dtbo.img
# refuse when the experiment isn't up on UART
uv run benchctl --sim iterate boot.img
# refuse when the reboot budget can't cover the iteration
uv run benchctl --sim --sim-on-experiment --sim-boots good --reboot-budget 2 iterate boot.img

# --- pixel-ota world (legacy A/B + power) ---
uv run benchctl --json --sim --flash pixel-ota --rollback-via power --sim-boots bad --sim-rollback-after 2 \
    iterate boot.img vendor_boot.img dtbo.img            # fail-then-rollback
uv run benchctl --json --sim --flash pixel-ota --rollback-via power --sim-rollback-after none \
    iterate boot.img vendor_boot.img dtbo.img            # wedge -> one power-cycle
uv run benchctl --sim --flash pixel-ota --rollback-via power --sim-mark-successful \
    iterate boot.img vendor_boot.img dtbo.img            # abort: experiment marked successful
```

## CLI

```
benchctl [--json] [--config PATH]
         [--flash {uartfs|pixel-ota|fastboot}] [--rollback-via {retry-exhaustion|power|fastboot}]
         [--reboot-budget N] [--sim ...] <command>

status                                      home base health + slot flags
stage <imgs...>                             (A/B) push + switch rollback-safe
boot-experiment [--success-regex R]         (A/B) reboot, capture UART, classify
                [--fail-regex R] [--timeout S]
iterate <imgs...> [--success-regex ...]     the full loop (uartfs in-place or A/B, per --flash)
recover                                     return to home base per rollback_via
power {off|on|cycle}                        drive the power backend (if configured)
```

Exit codes: `0` ok · `2` usage · `3` refusal · `4` unrecoverable · `5` boot classified not-success.

## Backends (pluggable)

- **Flash** (`flash.backend`): `uartfs` (in-place over UART), `pixel-ota` (A/B from home base),
  `fastboot` (interactive cable-swap).
- **Recovery** (`slots.rollback_via`): `retry-exhaustion` (reboot until the bootloader rolls back),
  `power` (passive rollback + one power-cycle), `fastboot` (operator cable-swap).
- **Power** (`power.backend`, optional / `none`): `tasmota` / `shelly` (HTTP) and `uhubctl` ship in
  [`src/benchctl/power/`](src/benchctl/power/). Interface is `off`/`on`/`cycle`/`reachable`.
- **On-device / transport tools**: `pixel-bootctl` / `pixel-ota` over SSH (`bootctl.py` / `ota.py`);
  `uart` and `uartfs` are local companion binaries to [`uartd`](../../uartd), invoked per the
  configured `uart.command` / `uart.uartfs_command`.

See [`benchctl.toml.sample`](benchctl.toml.sample) for configuration. Precedence is
defaults < file < environment (`BENCHCTL_<SECTION>_<KEY>`) < flags.

## Architecture

Every external effect sits behind an injectable seam — `Device` (SSH home base), `UartDevice`
(experiment over uartfs/UART), `Runner` (local `uart`/`uartfs`), `Power`, `Clock` — so the
orchestrator is pure decision logic and the suite runs in-process with no network, subprocess, or
real waiting. Built test-first; see [`plan.md`](plan.md).

## Status / caveats

Developed and tested entirely against the simulation. To validate on real felix before trusting it
unattended:

1. The **rollback-vs-fastboot** behaviour when the experiment slot's retry budget exhausts.
2. The **`uart` / `uartfs` `--json` contracts** assumed in [`uart.py`](src/benchctl/uart.py) /
   [`uartfs.py`](src/benchctl/uartfs.py) against the real binaries (uartfs is still a stub in uartd —
   pin its CLI when UF5 lands).
3. **Battery awareness is reboot-count only** until a `fastboot getvar battery-voltage` reader is
   added (needs a cable swap).
