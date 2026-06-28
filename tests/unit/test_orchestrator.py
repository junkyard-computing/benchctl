"""M5: orchestrator — invariant-first. Safety refusals before happy paths."""

import pytest

from benchctl.errors import Refusal, Unrecoverable
from benchctl.sim import SimDevice, SimPower
from tests.support import FakeClock, make_orchestrator

IMAGES = ["boot.img", "vendor_boot.img", "dtbo.img"]


# --- preflight / verify_home_base refusals --------------------------------

def test_verify_home_base_ok_on_fresh_sim():
    orch = make_orchestrator(SimDevice())
    orch.verify_home_base()  # does not raise


def test_refuses_when_ssh_down():
    sim = SimDevice()
    sim.booted = sim.experiment  # unreachable
    with pytest.raises(Refusal):
        make_orchestrator(sim).verify_home_base()


def test_refuses_when_not_on_home_base():
    sim = SimDevice()
    sim.active = "b"  # reachable on a, but active slot is not home base
    with pytest.raises(Refusal):
        make_orchestrator(sim).verify_home_base()


def test_refuses_when_home_base_not_successful():
    sim = SimDevice()
    sim.slots["a"].successful = False
    with pytest.raises(Refusal):
        make_orchestrator(sim).verify_home_base()


def test_refuses_when_power_unreachable():
    sim = SimDevice()
    orch = make_orchestrator(sim, power=SimPower(sim, reachable=False))
    with pytest.raises(Refusal):
        orch.verify_home_base()


# --- staging --------------------------------------------------------------

def test_stage_pushes_images_and_switches_rollback_safe():
    sim = SimDevice()
    orch = make_orchestrator(sim)
    orch.stage(IMAGES)
    assert len(sim.pushes) == len(IMAGES)
    assert sim.staged_dir is not None
    st = orch.bootctl.status()
    assert st.active == "b"  # experiment slot now active
    assert st.flags("b").successful is False  # rollback-safe


def test_stage_aborts_if_experiment_marked_successful():
    sim = SimDevice(update_marks_successful=True)
    orch = make_orchestrator(sim)
    with pytest.raises(Refusal):
        orch.stage(IMAGES)
    # home base success flag untouched
    assert sim.slots["a"].successful is True


# --- boot-experiment classification ---------------------------------------

def test_boot_experiment_classifies_failure():
    sim = SimDevice(experiment_boots="bad")
    orch = make_orchestrator(sim)
    orch.stage(IMAGES)
    res = orch.boot_experiment(success_regex=r"Reached target", fail_regex=r"Kernel panic", timeout=5)
    assert res.classification == "failed"
    assert "panic" in res.console.lower()


def test_boot_experiment_classifies_success():
    sim = SimDevice(experiment_boots="good")
    orch = make_orchestrator(sim)
    orch.stage(IMAGES)
    res = orch.boot_experiment(success_regex=r"Reached target", fail_regex=r"Kernel panic", timeout=5)
    assert res.classification == "success"


def test_boot_experiment_refuses_when_power_unreachable():
    sim = SimDevice()
    orch = make_orchestrator(sim, power=SimPower(sim, reachable=False))
    with pytest.raises(Refusal):
        orch.boot_experiment(success_regex="x", fail_regex="y", timeout=5)


def test_boot_experiment_refuses_when_home_base_unhealthy():
    sim = SimDevice()
    sim.slots["a"].successful = False
    with pytest.raises(Refusal):
        make_orchestrator(sim).boot_experiment(success_regex="x", fail_regex="y", timeout=5)


# --- recover --------------------------------------------------------------

def test_recover_rolled_back():
    sim = SimDevice(experiment_boots="bad", rollback_after=2)
    orch = make_orchestrator(sim)
    orch.stage(IMAGES)
    orch.boot_experiment(success_regex=r"never", fail_regex=r"Kernel panic", timeout=5)
    outcome = orch.recover()
    assert outcome == "rolled-back"
    assert sim.power_cycles == 0
    assert orch.bootctl.status().active == "a"


def test_recover_wedged_does_exactly_one_power_cycle():
    sim = SimDevice(experiment_boots="bad", rollback_after=None)
    orch = make_orchestrator(sim)
    orch.stage(IMAGES)
    orch.boot_experiment(success_regex=r"never", fail_regex=r"Kernel panic", timeout=5)
    outcome = orch.recover()
    assert outcome == "wedged-recovered"
    assert sim.power_cycles == 1


def test_recover_unrecoverable_raises():
    sim = SimDevice(experiment_boots="bad", rollback_after=None, power_cycle_recovers=False)
    orch = make_orchestrator(sim)
    orch.stage(IMAGES)
    orch.boot_experiment(success_regex=r"never", fail_regex=r"Kernel panic", timeout=5)
    with pytest.raises(Unrecoverable):
        orch.recover()


# --- full iterate ---------------------------------------------------------

def test_iterate_fail_then_rollback_end_to_end():
    sim = SimDevice(experiment_boots="bad", rollback_after=2)
    orch = make_orchestrator(sim)
    result = orch.iterate(IMAGES, success_regex=r"Reached target", fail_regex=r"Kernel panic")
    assert result.outcome == "rolled-back"
    assert result.boot.classification == "failed"
    assert "panic" in result.boot.console.lower()
    # safety: home base still successful, experiment never confirmed
    assert sim.slots["a"].successful is True
    assert sim.slots["b"].successful is False


def test_iterate_refuses_on_unhealthy_home_base():
    sim = SimDevice()
    sim.slots["a"].successful = False
    orch = make_orchestrator(sim)
    with pytest.raises(Refusal):
        orch.iterate(IMAGES, success_regex=r"x", fail_regex=r"y")


def test_recover_honors_timeout_bounds(monkeypatch):
    # rollback never happens; recover must terminate (not hang) and power-cycle.
    sim = SimDevice(experiment_boots="bad", rollback_after=None)
    clock = FakeClock()
    orch = make_orchestrator(sim, clock=clock)
    orch.stage(IMAGES)
    orch.boot_experiment(success_regex=r"never", fail_regex=r"Kernel panic", timeout=5)
    orch.recover(rollback_timeout=30, power_cycle_timeout=30, poll_interval=5)
    # virtual time advanced by at least the rollback window before the power cycle
    assert clock.now() >= 30


def test_home_base_up_requires_successful_flag():
    # Recovery re-verify: booting on the home base slot is not enough — it must
    # still be a valid rollback anchor (marked successful).
    sim = SimDevice()  # booted on home base 'a', reachable
    orch = make_orchestrator(sim)
    assert orch._home_base_up() is True
    sim.slots["a"].successful = False  # anchor lost its success flag
    assert orch._home_base_up() is False
