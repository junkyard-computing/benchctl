"""R6/R7/R8: the felix mainline path — uartfs in-place iterate, retry-exhaustion
recovery (no power), and the reboot/battery budget refusal."""

import pytest

from benchctl.config import BatteryConfig, Config, SSHConfig
from benchctl.errors import Refusal
from benchctl.sim import SimDevice
from tests.support import make_orchestrator

IMAGES = ["boot.img", "vendor_boot.img", "dtbo.img"]


def _sim_config(**battery):
    # Default felix config: power=none, rollback_via=retry-exhaustion.
    cfg = Config(ssh=SSHConfig(host="sim", user="root"))
    if battery:
        cfg.battery = BatteryConfig(**battery)
    return cfg


def _on_experiment(sim):
    sim.active = sim.experiment
    sim._boot(sim.experiment)


# --- retry-exhaustion recovery (no power backend) -------------------------

def test_recover_retry_exhaustion_rolls_back():
    sim = SimDevice(experiment_boots="good", experiment_retries=3, rollback_after=None)
    _on_experiment(sim)
    orch = make_orchestrator(sim, power=None, config=_sim_config())
    outcome = orch.recover()
    assert outcome == "rolled-back"
    assert sim.power_cycles == 0  # no power used
    assert orch.reboots_used >= 3
    assert orch.bootctl.status().active == "a"


def test_recover_retry_exhaustion_unrecoverable():
    # Experiment never exhausts within the reboot budget and never comes home.
    sim = SimDevice(experiment_boots="good", experiment_retries=99, rollback_after=None)
    _on_experiment(sim)
    cfg = _sim_config()
    cfg.slots.rollback_reboots = 3
    orch = make_orchestrator(sim, power=None, config=cfg)
    from benchctl.errors import Unrecoverable
    with pytest.raises(Unrecoverable):
        orch.recover()


# --- uartfs in-place iterate ----------------------------------------------

def test_iterate_uartfs_success_stays_on_experiment():
    sim = SimDevice(experiment_boots="good", experiment_retries=None)
    _on_experiment(sim)
    orch = make_orchestrator(sim, power=None, config=_sim_config())
    res = orch.iterate_uartfs(IMAGES, success_regex=r"Reached target", fail_regex=r"Kernel panic")
    assert res.outcome == "iterated"
    assert res.flash == "uartfs"
    assert sim.booted == sim.experiment  # never left the experiment slot
    # flashed in place against the experiment slot's partitions
    assert ("boot.img", "boot_b") in sim.uartfs_flashes
    assert ("vendor_boot.img", "vendor_boot_b") in sim.uartfs_flashes


def test_iterate_uartfs_bad_flash_recovers_home_via_retry_exhaustion():
    sim = SimDevice(experiment_boots="good", rollback_after=2)
    _on_experiment(sim)
    sim.flash_outcome = "bad"  # the kernel we flash will panic on next boot
    orch = make_orchestrator(sim, power=None, config=_sim_config())
    res = orch.iterate_uartfs(IMAGES, success_regex=r"Reached target", fail_regex=r"Kernel panic")
    assert res.boot.classification == "failed"
    assert res.outcome == "rolled-back"
    assert orch.bootctl.status().active == "a"


def test_iterate_uartfs_refuses_without_uartfs_transport():
    sim = SimDevice()
    from benchctl.bootctl import Bootctl
    from benchctl.orchestrator import Orchestrator
    from benchctl.ota import Ota
    from benchctl.sim import SimUart
    from benchctl.uart import UartClient
    from tests.support import FakeClock
    orch = Orchestrator(
        device=sim, bootctl=Bootctl(sim), ota=Ota(sim),
        uart=UartClient(["uart"], SimUart(sim)), power=None,
        clock=FakeClock(), config=_sim_config(),
    )  # no experiment/uartfs wired
    with pytest.raises(Refusal):
        orch.iterate_uartfs(IMAGES)


def test_iterate_uartfs_refuses_when_experiment_not_up():
    sim = SimDevice()  # booted on home base, experiment not up
    orch = make_orchestrator(sim, power=None, config=_sim_config())
    with pytest.raises(Refusal):
        orch.iterate_uartfs(IMAGES)


# --- reboot / battery budget ----------------------------------------------

def test_iterate_refuses_when_reboot_budget_too_low():
    sim = SimDevice(experiment_boots="good", experiment_retries=None)
    _on_experiment(sim)
    # budget smaller than worst-case (1 boot + rollback_reboots) -> refuse up front
    orch = make_orchestrator(sim, power=None, config=_sim_config(reboot_budget=2))
    with pytest.raises(Refusal):
        orch.iterate_uartfs(IMAGES)


def test_budget_unenforced_when_zero():
    sim = SimDevice(experiment_boots="good", experiment_retries=None)
    _on_experiment(sim)
    orch = make_orchestrator(sim, power=None, config=_sim_config(reboot_budget=0))
    res = orch.iterate_uartfs(IMAGES, success_regex=r"Reached target", fail_regex=r"Kernel panic")
    assert res.outcome == "iterated"
