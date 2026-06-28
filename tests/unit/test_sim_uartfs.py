"""R3/R4: sim mainline-experiment reality + UartDevice transport.

The experiment slot runs the mainline kernel: no SSH, reachable only over uartfs/
UART; it can in-place delta-flash its own boot partition; it never self-commits,
so rebooting it burns the bootloader retry budget and eventually rolls back.
"""

from benchctl.device import UartDevice
from benchctl.sim import SimDevice, SimUart, SimUartfs
from benchctl.uart import UartClient
from benchctl.uartfs import UartfsClient


def _uartfs(sim):
    return UartfsClient(["uartfs"], SimUartfs(sim))


def _on_experiment(sim):
    """Put the model on the experiment slot, up on UART (mainline)."""
    sim.active = sim.experiment
    sim._boot(sim.experiment)


# --- uartfs transport reachability ---------------------------------------

def test_uartfs_run_works_on_experiment_slot():
    sim = SimDevice(experiment_boots="good")
    _on_experiment(sim)
    res = _uartfs(sim).run("uname -r")
    assert res.returncode == 0


def test_uartfs_run_fails_when_not_on_experiment():
    sim = SimDevice()  # booted on home base
    # transport to the experiment slot is down -> UartDevice surfaces nonzero
    dev = UartDevice(_uartfs(sim), UartClient(["uart"], SimUart(sim)))
    assert dev.run(["true"]).returncode != 0


def test_uartfs_flash_in_place_records_and_verifies():
    sim = SimDevice(experiment_boots="good")
    _on_experiment(sim)
    res = _uartfs(sim).flash("boot.img", "boot_a")
    assert res.ok is True
    assert ("boot.img", "boot_a") in sim.uartfs_flashes


# --- retry-exhaustion rollback (the recovery primitive) -------------------

def test_experiment_reboots_burn_retries_then_roll_back():
    sim = SimDevice(experiment_boots="good", experiment_retries=2)
    _on_experiment(sim)
    fs = _uartfs(sim)
    fs.run("reboot")              # burn 1
    assert sim.booted == sim.experiment
    fs.run("reboot")             # burn 2 -> now exhausted
    assert sim.booted == sim.experiment
    fs.run("reboot")             # exhausted -> roll back to home base
    assert sim.booted == sim.home_base
    assert sim.reachable is True


# --- UartDevice as a first-class transport --------------------------------

def test_uartdevice_run_and_wait():
    sim = SimDevice(experiment_boots="good")
    _on_experiment(sim)
    dev = UartDevice(_uartfs(sim), UartClient(["uart"], SimUart(sim)))
    assert dev.run(["uname", "-r"]).returncode == 0
    # the console carries the experiment boot; wait matches it
    res = dev.wait(r"Reached target", timeout=5)
    assert res.matched is True
