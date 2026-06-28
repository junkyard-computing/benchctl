"""M4: the simulation model — bootloader/slot semantics benchctl's safety leans on.

These prove the fake itself before the orchestrator trusts it.
"""

from benchctl.bootctl import Bootctl
from benchctl.ota import Ota
from benchctl.sim import SimDevice, SimPower, SimUart
from benchctl.uart import UartClient


def _boot(dev):
    """Capture-side helper: reboot and let the model boot the active slot."""
    dev.run(["reboot"])


# --- initial state --------------------------------------------------------

def test_initial_status_home_base_active_and_successful():
    dev = SimDevice()
    st = Bootctl(dev).status()
    assert st.active == "a"
    assert st.flags("a").successful is True
    assert st.flags("b").successful is False


# --- staging is rollback-safe ---------------------------------------------

def test_update_switches_to_inactive_not_successful():
    dev = SimDevice()
    Ota(dev).update("/staged")
    st = Bootctl(dev).status()
    assert st.active == "b"  # switched to inactive (experiment)
    assert st.flags("b").successful is False  # rollback-safe: active, NOT successful
    assert st.flags("a").successful is True  # home base untouched


def test_update_refuses_active_slot():
    dev = SimDevice()
    res = dev.run(["pixel-ota", "update", "/staged", "--slot", "a"])  # a is active
    assert res.returncode != 0


def test_update_marks_successful_knob_models_the_hazard():
    dev = SimDevice(update_marks_successful=True)
    Ota(dev).update("/staged")
    st = Bootctl(dev).status()
    assert st.flags("b").successful is True  # the "bitten before" mis-marking


# --- connectivity ---------------------------------------------------------

def test_experiment_boot_makes_device_unreachable():
    dev = SimDevice()
    Ota(dev).update("/staged")
    _boot(dev)
    # experiment slot has no network -> SSH down
    assert dev.run(["pixel-bootctl", "status"]).returncode != 0


# --- fail-then-rollback ---------------------------------------------------

def test_bad_experiment_rolls_back_after_retries():
    dev = SimDevice(experiment_boots="bad", rollback_after=2)
    Ota(dev).update("/staged")
    _boot(dev)
    # poll until reachable again (rollback) — bounded
    reachable = False
    for _ in range(5):
        if dev.run(["pixel-bootctl", "status"]).ok:
            reachable = True
            break
    assert reachable
    st = Bootctl(dev).status()
    assert st.active == "a"  # rolled back to home base
    assert st.flags("a").successful is True


# --- wedge ----------------------------------------------------------------

def test_wedge_never_rolls_back_until_power_cycle():
    dev = SimDevice(experiment_boots="bad", rollback_after=None)
    Ota(dev).update("/staged")
    _boot(dev)
    for _ in range(10):
        assert dev.run(["pixel-bootctl", "status"]).returncode != 0  # never recovers
    SimPower(dev).cycle()
    st = Bootctl(dev).status()
    assert st.active == "a"  # power-cycle picked the marked-good slot
    assert dev.power_cycles == 1


# --- uart console ---------------------------------------------------------

def test_uart_sees_panic_on_bad_boot():
    dev = SimDevice(experiment_boots="bad")
    uart = UartClient(["uart"], SimUart(dev))
    Ota(dev).update("/staged")
    _boot(dev)
    console = uart.peek().text
    assert "panic" in console.lower()


def test_uart_sees_success_marker_on_good_boot():
    dev = SimDevice(experiment_boots="good")
    uart = UartClient(["uart"], SimUart(dev))
    Ota(dev).update("/staged")
    _boot(dev)
    assert "Reached target" in uart.peek().text
