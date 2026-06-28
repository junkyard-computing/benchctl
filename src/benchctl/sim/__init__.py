"""Hardware-free simulation: a model device + uart + power for tests and sim mode."""

from benchctl.sim.fake_device import SimDevice
from benchctl.sim.fake_power import SimPower
from benchctl.sim.fake_uart import SimUart

__all__ = ["SimDevice", "SimPower", "SimUart"]
