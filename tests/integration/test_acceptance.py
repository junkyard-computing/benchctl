"""Acceptance — both worlds driven through the real CLI in sim mode (no hardware).

  uartfs world (felix mainline default): in-place delta-flash, stay on the
  experiment slot, retry-exhaustion recovery, no power backend.
  pixel-ota world (legacy A/B): flash inactive slot from home base, passive
  rollback + power-cycle backstop.

Exit codes: 0 ok · 3 refusal · 4 unrecoverable · 5 boot-failed.
"""

import json

from benchctl.cli import main

IMAGES = ["boot.img", "vendor_boot.img", "dtbo.img"]


def run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


# ============================ uartfs world ================================

def test_uartfs_iterate_success_stays_on_experiment(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-on-experiment", "--sim-boots", "good",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["flash"] == "uartfs"
    assert data["outcome"] == "iterated"


def test_uartfs_bad_flash_recovers_via_retry_exhaustion(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-on-experiment", "--sim-boots", "good",
         "--sim-flash-bad", "--sim-rollback-after", "2", "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["boot"]["classification"] == "failed"
    assert data["outcome"] == "rolled-back"
    assert data["power_cycles"] == 0  # no power backend involved


def test_uartfs_iterate_refuses_when_experiment_not_up(capsys):
    # default (booted on home base) -> the experiment isn't up on UART
    rc, out = run(["--sim", "iterate", *IMAGES], capsys)
    assert rc == 3
    assert "experiment" in out.lower()


def test_retry_exhaustion_recover(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-on-experiment", "--sim-boots", "good",
         "--sim-experiment-retries", "3", "recover"],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["outcome"] == "rolled-back"
    assert data["power_cycles"] == 0


def test_reboot_budget_refusal(capsys):
    rc, out = run(
        ["--sim", "--sim-on-experiment", "--sim-boots", "good", "--reboot-budget", "2",
         "iterate", *IMAGES],
        capsys,
    )
    assert rc == 3
    assert "budget" in out.lower()


# ============================ pixel-ota world =============================

_OTA = ["--flash", "pixel-ota", "--rollback-via", "power"]


def test_pixel_ota_fail_then_rollback(capsys):
    rc, out = run(
        ["--json", "--sim", *_OTA, "--sim-boots", "bad", "--sim-rollback-after", "2",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["outcome"] == "rolled-back"
    assert data["boot"]["classification"] == "failed"
    assert data["power_cycles"] == 0


def test_pixel_ota_wedge_one_power_cycle(capsys):
    rc, out = run(
        ["--json", "--sim", *_OTA, "--sim-boots", "bad", "--sim-rollback-after", "none",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["outcome"] == "wedged-recovered"
    assert data["power_cycles"] == 1


def test_pixel_ota_refuses_boot_experiment_when_power_unreachable(capsys):
    rc, out = run([*_OTA, "--sim", "--sim-power-unreachable", "boot-experiment"], capsys)
    assert rc == 3


def test_pixel_ota_aborts_when_experiment_marked_successful(capsys):
    rc, out = run([*_OTA, "--sim", "--sim-mark-successful", "iterate", *IMAGES], capsys)
    assert rc == 3
    assert "successful" in out.lower()


def test_pixel_ota_unrecoverable(capsys):
    rc, out = run(
        ["--json", "--sim", *_OTA, "--sim-rollback-after", "none", "--sim-no-power-recovers",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 4
    assert data["outcome"] == "unrecoverable"


# ============================ general ====================================

def test_status_json(capsys):
    rc, out = run(["--json", "--sim", "status"], capsys)
    data = json.loads(out)
    assert rc == 0
    assert data["active"] == "a"
    assert data["home_base_healthy"] is True


def test_power_subcommand_refuses_without_backend(capsys):
    # default felix config has no power backend
    rc, out = run(["--sim", "power", "cycle"], capsys)
    assert rc == 3


def test_power_subcommand_with_backend(capsys):
    rc, out = run(["--sim", "--rollback-via", "power", "power", "cycle"], capsys)
    assert rc == 0
