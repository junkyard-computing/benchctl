"""M2: pixel-ota wrapper — argv construction for update/confirm."""

import pytest

from benchctl.errors import CommandError
from benchctl.ota import Ota
from tests.support import RecordingDevice


def test_update_default_flashes_inactive_no_extra_flags():
    dev = RecordingDevice()
    Ota(dev).update("/tmp/imgs")
    assert dev.last_call == ["pixel-ota", "update", "/tmp/imgs"]


def test_update_with_flags():
    dev = RecordingDevice()
    Ota(dev).update("/tmp/imgs", slot="b", no_switch=True, dry_run=True)
    assert dev.last_call == [
        "pixel-ota",
        "update",
        "/tmp/imgs",
        "--slot",
        "b",
        "--no-switch",
        "--dry-run",
    ]


def test_confirm_builds_argv():
    dev = RecordingDevice()
    Ota(dev).confirm()
    assert dev.last_call == ["pixel-ota", "confirm"]


def test_update_nonzero_raises():
    dev = RecordingDevice().queue(2, "", "refused to flash active slot")
    with pytest.raises(CommandError):
        Ota(dev).update("/tmp/imgs")


def test_runs_privileged():
    dev = RecordingDevice()
    Ota(dev).update("/tmp/imgs")
    assert dev.sudo_flags == [True]  # writes block devices — root
