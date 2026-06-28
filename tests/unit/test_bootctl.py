"""M2: pixel-bootctl wrapper — argv construction, status parsing, error mapping."""

import pytest

from benchctl.bootctl import Bootctl
from benchctl.errors import CommandError
from tests.support import RecordingDevice

STATUS_OUTPUT = """\
active=b
a successful=true retries=7
b successful=false retries=0
"""


def test_status_parses_active_and_per_slot_flags():
    dev = RecordingDevice().queue(0, STATUS_OUTPUT)
    st = Bootctl(dev).status()
    assert dev.last_call == ["pixel-bootctl", "status"]
    assert st.active == "b"
    assert st.flags("a").successful is True
    assert st.flags("a").retries == 7
    assert st.flags("b").successful is False
    assert st.flags("b").retries == 0


def test_status_helper_inactive_slot():
    dev = RecordingDevice().queue(0, STATUS_OUTPUT)
    st = Bootctl(dev).status()
    assert st.inactive == "a"


def test_set_active_slot_builds_argv():
    dev = RecordingDevice()
    Bootctl(dev).set_active_slot("a")
    assert dev.last_call == ["pixel-bootctl", "set-active-slot", "a"]


def test_mark_successful_builds_argv():
    dev = RecordingDevice()
    Bootctl(dev).mark_successful()
    assert dev.last_call == ["pixel-bootctl", "mark-successful"]


def test_nonzero_exit_raises_command_error():
    dev = RecordingDevice().queue(1, "", "boom")
    with pytest.raises(CommandError) as ei:
        Bootctl(dev).status()
    assert "boom" in str(ei.value)


def test_runs_privileged():
    dev = RecordingDevice().queue(0, STATUS_OUTPUT)
    Bootctl(dev).status()
    assert dev.sudo_flags == [True]  # devinfo/UFS sysfs need root
