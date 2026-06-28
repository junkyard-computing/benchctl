# benchctl — implementation plan

Host-side orchestrator for **unattended mainline-kernel iteration** on a Pixel Fold
(felix / gs201). It flashes an experimental boot chain to the inactive A/B slot, boots it,
captures the result over UART, and **guarantees return to a known-good slot** — with no
human and no network on the device under test.

Spec: [../junkyard-boot-img/prompts/benchctl.md](../junkyard-boot-img/prompts/benchctl.md).
On-device contract: [../junkyard-boot-img/prompts/existing-tools-contract.md](../junkyard-boot-img/prompts/existing-tools-contract.md).

This plan is the build order. It is **test-driven**: every milestone is a red→green→refactor
cycle, no production code without a failing test first.

---

## 1. Scope & non-goals

**In scope:** the outer loop — preflight, stage, assert-rollback-armed, reboot, UART-classify,
recover (rollback-wait → power-cycle backstop), report. CLI + pluggable power/flash backends +
config + a hardware-free **simulation suite**.

**Not rebuilt (driven, not reimplemented):**
- `pixel-bootctl`, `pixel-ota` — on-device static aarch64 binaries, invoked over SSH.
- `uart` — **a static binary** (companion to `uartd`), invoked as an external command via a
  configurable invocation string. benchctl shells out to it; it never opens the serial port.

**Non-goals:** flashing logic, slot mechanics, the serial daemon. No hardware needed to build or
test — real hardware bring-up is a later, separate phase.

---

## 2. Architecture — seams are the design

The whole thing is structured so every external effect sits behind a small injectable interface.
The orchestrator is **pure decision logic** over those interfaces; tests inject fakes; sim mode
wires the same fakes into the CLI. Nothing in the core touches a socket, subprocess, or clock
directly.

```
src/benchctl/
  cli.py            argparse dispatch, --json rendering, exit codes
  config.py         load + merge: defaults < file < env < flags
  errors.py         typed exceptions (refusal vs failure vs unrecoverable)
  orchestrator.py   iterate / recover / boot-experiment state machine — PURE
  device.py         Device protocol: run(argv)->Result, push(local,remote)   [SSH impl]
  bootctl.py        pixel-bootctl wrapper over a Device (status/set-active-slot/mark-successful)
  ota.py            pixel-ota wrapper over a Device (update/confirm)
  uart.py           Uart protocol + UartClient (shells the `uart` binary, parses --json)
  clock.py          Clock protocol: now()/sleep()  [real impl + FakeClock for tests]
  power/
    base.py         Power protocol: off()/on()/cycle(); registry by backend name
    tasmota.py  shelly.py  uhubctl.py
  sim/
    fake_device.py  models slots/devinfo + bootloader retry/rollback semantics
    fake_uart.py    scripted boot-console streams
    fake_power.py   in-memory switch that drives the fake bootloader on cycle
tests/
  unit/             per-module, fakes injected
  integration/      sim-mode end-to-end == the acceptance scenarios
```

**Injected seams (the only things that touch the outside world):** `Device` (SSH),
`Uart` (the binary), `Power` (HTTP/uhubctl), `Clock` (time). Mock these four and the
orchestrator is fully exercisable in-process, instantly, with no waiting.

`Clock` matters: every timeout/wait path takes the injected clock so timeout tests advance
time deterministically — **no real sleeps in the test suite.**

---

## 3. The simulation model (load-bearing fake)

`sim/fake_device.py` is a state machine modelling the bootloader/slot behaviour benchctl's
safety depends on. Its own correctness is TDD'd before the orchestrator relies on it.

State: per-slot `{active, successful, retry_count}`; a shared `super` (never slotted); a
scriptable boot outcome.

Modelled transitions:
- `pixel-bootctl status` → reports slot flags.
- `pixel-ota update <dir>` → flashes **inactive** boot chain (refuses active), switches active to
  it **rollback-safe: active, NOT successful**. No reboot.
- **reboot** → bootloader runs the active slot; per the script the boot fails or succeeds. On
  failure it burns one retry; when `retry_count` exhausts it **rolls back to the last successful
  slot** (home base). A successful experiment boot that is never `confirm`ed still rolls back.
- **power cycle** → same bootloader selection from cold; the backstop for a wedge that never
  exhausts retries in-window.
- A `wedge` script → neither rolls back nor returns within the timeout, so only a power-cycle
  recovers it.

This lets the integration tests reproduce *fail-then-rollback* and *wedge* deterministically,
and—critically—lets us assert benchctl checks the **active-but-NOT-successful** invariant the two
on-device READMEs disagree about (the documented "has bitten before" hazard).

---

## 4. TDD strategy

- **pytest**, `pytest -q` green at every milestone; CI runs the whole suite with no hardware.
- Each feature: write the failing test (red), minimum code (green), refactor. Commit per cycle.
- **Layered tests:**
  - *unit* — command construction & parsing (bootctl/ota/uart wrappers against a recording
    FakeDevice), config merge precedence, power backends against a mock transport, the sim model
    itself, orchestrator decisions against all-fakes.
  - *integration* — sim-mode runs through the real CLI entrypoint; these **are** the five
    acceptance criteria.
- **Test the refusals first.** Safety invariants are easier and more important to pin down than
  happy paths: a refusal test can't be faked green.
- No real time, network, or subprocess in the suite. The one optional exception: a single,
  marked, opt-in test that runs the CLI against a stub `uart` script on PATH to prove the
  real shell-out/parse path — skipped by default.

---

## 5. Build order (each milestone = TDD cycles, ends green)

**M0 — skeleton.** pyproject + flake (runnable pkg + dev shell), pytest wired, `benchctl
--version`/`--help` smoke test. Red→green on the smoke test.

**M1 — config.** `defaults < file < env < flags` precedence; required-field validation;
typed errors. Pure, fast — good first real TDD target. Covers: SSH host/user/key, slot &
partition names, power backend type+addr, **`uart` invocation string**, all timeouts.

**M2 — Device + on-device wrappers.** `Device` protocol; `bootctl`/`ota`/`uart` wrappers
tested against a recording FakeDevice: assert exact argv, parse `status`, parse `uart --json`,
map non-zero exits to typed errors. (Real SSH `Device` impl is thin; its integration is deferred
to the hardware phase.)

**M3 — power backends.** `Power` protocol + registry; Tasmota/Shelly drivers against a mock
HTTP transport; `uhubctl` against a fake subprocess runner. `off/on/cycle` semantics + an
`unreachable` path (preflight depends on it).

**M4 — simulation model.** Build & TDD `fake_device`/`fake_uart`/`fake_power` (§3). Tests prove
the model rolls back on retry exhaustion, wedges when scripted, and refuses to flash the active
slot — *before* anything trusts it.

**M5 — orchestrator (the heart).** State machine over the four seams, TDD'd invariant-first:
1. **preflight refuses** unless SSH up, on slot A, A successful, power backend reachable.
2. **stage** → scp + `pixel-ota update`; then **assert experiment slot active-but-NOT-successful**;
   **abort if successful** (rollback would be defeated).
3. **boot-experiment** → reboot, capture UART over the window, classify by success/fail regex,
   honour timeout.
4. **recover** → wait for SSH home base (rollback); if not within timeout → **exactly one**
   power-cycle → wait again; re-verify home base. Outcome ∈
   {`rolled-back`, `wedged-recovered`, `unrecoverable`}.
5. **never writes `super`** without `--include-rootfs`; home-base success flag never cleared;
   benchctl never `confirm`s the experiment slot.

**M6 — CLI surface.** Wire `status`, `stage`, `boot-experiment [--success-regex --fail-regex
--timeout]`, `iterate`, `recover`, `power {off|on|cycle}`. Global `--json`. Meaningful exit codes
(refusal ≠ experiment-fail ≠ unrecoverable). Everything timed; never hangs.

**M7 — acceptance integration.** Sim-mode end-to-end through the CLI == the spec's five criteria
(§6). These gate "done."

**M8 — deliverables polish.** README (rationale + the rollback hazard + usage), `benchctl.toml`
sample, backend examples, flake/pyproject finalised.

---

## 6. Acceptance → tests (spec §Acceptance)

| # | Scenario | Test |
|---|---|---|
| 1 | fail-then-rollback → `rolled-back` | integration, fake scripted to exhaust retries |
| 2 | wedge → exactly one power-cycle → `wedged-recovered` | integration, wedge script; assert single cycle |
| 3 | refuse `boot-experiment` if home base unhealthy / power unreachable | unit + integration refusal |
| 4 | abort if post-stage experiment slot reads **successful** | unit orchestrator, fake forces successful |
| 5 | never writes `super` w/o `--include-rootfs`; success flag untouched; waits honour timeouts | unit invariants + FakeClock |

---

## 7. Tooling & packaging

Python 3, minimal deps, **robust over clever** (runs unattended while a flaky device reboots).
NixOS host → `flake.nix` exposing runnable `benchctl` + dev shell; `pyproject.toml` alongside.
Test deps (pytest, an HTTP mock) in the dev shell only.

---

## 8. Open questions / risks (carry into the hardware phase)

- **Rollback semantics contradiction** — pixel-bootctl vs pixel-ota READMEs disagree on whether
  `set-active-slot` marks the target successful. Sim asserts benchctl *checks* `active-but-NOT-
  successful`; only hardware proves the device actually rolls back vs. drops to fastboot. Verify
  early on real felix.
- **Build-order coupling** — `uart` (binary) and `uartd` are separate, not yet built. benchctl is
  developed entirely against the mocked `UartClient`; first real use is blocked on that binary
  existing and honouring the assumed `read|send|wait|log` + `--json` contract. Pin that contract
  now so the mock and the binary agree.
- **`flash-ssh.sh` reuse** — lift its pixel-ota/pixel-bootctl invocations + preflight verbatim
  into `ota.py`/`bootctl.py`; drop the destructive rootfs reflash; add rollback-wait + UART +
  power backstop. Read it before writing M2.

---

## Update — felix mainline rework (R1–R10)

After a real bring-up session ([jboot-mainline/prompts/benchctl-updates.md](../jboot-mainline/prompts/benchctl-updates.md)),
the device model gained a second, now-default world. Same TDD discipline, same seams.

- **uartfs transport + flash** — the experiment slot (mainline) has no network; it's reached over
  UART. `UartfsClient` wraps the real `uartfs` binary (uartd UF5–UF8: `ping`/`run`/`push`/`pull`/
  `flash --base`/`bootstrap`, exit codes `0/1/2/3`, no `--json`); `UartDevice` makes the experiment
  a first-class transport. Host-side base cache → subsequent flashes ship only a zstd delta.
- **In-place iterate** (`flash.backend = "uartfs"`, default) — delta-flash the experiment's own
  boot partition and **stay on it**, never round-tripping the home base.
- **Retry-exhaustion recovery** (`slots.rollback_via`, default) — reboot the never-committed
  experiment until the bootloader rolls back; **no power relay required**. `power` is optional
  (`backend = "none"`); the legacy passive-rollback + power-cycle path is `rollback_via = "power"`.
- **Reboot/battery budget** — refuse an iteration that can't safely complete; live
  `fastboot getvar battery-voltage` deferred (needs a cable swap).
- **Sim grew** a mainline mode (uartfs up/flash, retry-exhaustion countdown, panicked-kernel =
  no shell, flash-changes-next-boot) so both worlds run hardware-free.

uartfs (UF5–UF8) has landed; the wrapper is matched to its real CLI. Deferred until hardware:
live `FastbootDevice` flashing and `fastboot getvar battery-voltage` reads (both need a cable swap).
