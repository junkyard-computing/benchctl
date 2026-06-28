"""M7: acceptance — the spec's five simulation-mode scenarios, driven through the
real CLI (argv in, exit code + JSON out). No hardware, no injection.

Exit codes: 0 ok, 3 refusal, 4 unrecoverable, 5 boot-failed.
"""

import json

from benchctl.cli import main

IMAGES = ["boot.img", "vendor_boot.img", "dtbo.img"]


def run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, out


# 1. fail-then-rollback -> rolled-back
def test_fail_then_rollback(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-boots", "bad", "--sim-rollback-after", "2",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["outcome"] == "rolled-back"
    assert data["boot"]["classification"] == "failed"
    assert data["power_cycles"] == 0


# 2. wedge -> exactly one power-cycle -> wedged-recovered
def test_wedge_recovered_with_one_power_cycle(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-boots", "bad", "--sim-rollback-after", "none",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["outcome"] == "wedged-recovered"
    assert data["power_cycles"] == 1


# 3. refuse boot-experiment if home base unhealthy / power unreachable
def test_refuses_boot_experiment_when_home_unhealthy(capsys):
    rc, out = run(["--sim", "--sim-home-unhealthy", "boot-experiment"], capsys)
    assert rc == 3
    assert "refus" in out.lower() or "not marked successful" in out.lower()


def test_refuses_boot_experiment_when_power_unreachable(capsys):
    rc, out = run(["--sim", "--sim-power-unreachable", "boot-experiment"], capsys)
    assert rc == 3


# 4. abort if post-stage experiment slot reads successful
def test_aborts_when_experiment_marked_successful(capsys):
    rc, out = run(["--sim", "--sim-mark-successful", "iterate", *IMAGES], capsys)
    assert rc == 3
    assert "successful" in out.lower()


# 5. unrecoverable path surfaces distinctly (never-rollback + cold boot fails)
def test_unrecoverable_exit_code(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-rollback-after", "none", "--sim-no-power-recovers",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 4
    assert data["outcome"] == "unrecoverable"


# good boot also returns rolled-back (never confirmed -> still rolls back)
def test_good_experiment_still_rolls_back(capsys):
    rc, out = run(
        ["--json", "--sim", "--sim-boots", "good", "--sim-rollback-after", "2",
         "iterate", *IMAGES],
        capsys,
    )
    data = json.loads(out)
    assert rc == 0
    assert data["outcome"] == "rolled-back"
    assert data["boot"]["classification"] == "success"


# status command renders JSON
def test_status_json(capsys):
    rc, out = run(["--json", "--sim", "status"], capsys)
    data = json.loads(out)
    assert rc == 0
    assert data["active"] == "a"
    assert data["home_base_healthy"] is True


# power subcommand drives the backend
def test_power_cycle_subcommand(capsys):
    rc, out = run(["--sim", "power", "cycle"], capsys)
    assert rc == 0
