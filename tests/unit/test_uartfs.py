"""uartfs CLI wrapper — matched to the real uartd UF5–UF8 contract.

No --json: `run` returns the device command's raw stdout/stderr and exit code;
exit 2 = link/daemon down, 3 = transfer/verify. Global --sudo prefixes the
privileged device-side actions (push/pull/flash); `run` does not take it.
"""

import pytest

from benchctl.errors import UartfsError
from benchctl.uartfs import UartfsClient
from tests.support import RecordingDevice


def _client(dev, command=("uartfs",), sudo=True):
    return UartfsClient(list(command), dev, sudo=sudo)


def test_ping_true_false():
    assert _client(RecordingDevice().queue(0, "agent ready (v1)")).ping() is True
    assert _client(RecordingDevice().queue(2, "", "no agent")).ping() is False


def test_run_returns_raw_remote_result():
    dev = RecordingDevice().queue(0, "5.10.0\n", "")
    res = _client(dev).run("uname -r")
    assert dev.last_call == ["uartfs", "run", "uname -r"]  # no --json, no --sudo
    assert res.returncode == 0
    assert res.stdout == "5.10.0\n"


def test_run_propagates_remote_nonzero():
    dev = RecordingDevice().queue(1, "", "no such file")
    res = _client(dev).run("cat /missing")
    assert res.returncode == 1
    assert "no such file" in res.stderr


def test_run_link_error_raises():
    dev = RecordingDevice().queue(2, "", "agent not responding")
    with pytest.raises(UartfsError):
        _client(dev).run("true")


def test_custom_invocation_prefixed():
    dev = RecordingDevice().queue(0, "")
    _client(dev, command=("uartfs", "--socket", "/run/uartd.sock")).run("true")
    assert dev.last_call[:3] == ["uartfs", "--socket", "/run/uartd.sock"]


def test_flash_builds_argv_with_sudo_and_parses_report():
    dev = RecordingDevice().queue(
        0, "", "flashed 4096 bytes to /dev/disk/by-partlabel/boot_a (sha256 " + "a" * 64 + ") — read-back verified"
    )
    res = _client(dev).flash("boot.img", "boot_a")
    assert dev.last_call == ["uartfs", "--sudo", "flash", "boot.img", "boot_a"]
    assert res.ok is True
    assert res.sha256 == "a" * 64
    assert res.bytes_sent == 4096


def test_flash_delta_with_base():
    dev = RecordingDevice().queue(0, "", "delta-flashed 512 bytes to /dev/disk/by-partlabel/vendor_boot_a (sha256 " + "b" * 64 + ")")
    _client(dev).flash("vendor_boot.img", "vendor_boot_a", base="/cache/prev.img")
    assert dev.last_call == [
        "uartfs", "--sudo", "flash", "vendor_boot.img", "vendor_boot_a", "--base", "/cache/prev.img"
    ]


def test_flash_dry_run_and_raw_target_flags():
    dev = RecordingDevice().queue(0, "", "[dry-run] would flash")
    _client(dev).flash("boot.img", "/dev/block/sda", dry_run=True, raw_target=True)
    assert dev.last_call == [
        "uartfs", "--sudo", "flash", "boot.img", "/dev/block/sda", "--dry-run", "--raw-target"
    ]


def test_flash_no_sudo_when_disabled():
    dev = RecordingDevice().queue(0, "", "flashed 1 bytes to x (sha256 " + "0" * 64 + ")")
    _client(dev, sudo=False).flash("boot.img", "boot_a")
    assert dev.last_call == ["uartfs", "flash", "boot.img", "boot_a"]


def test_flash_verify_failure_raises():
    dev = RecordingDevice().queue(3, "", "sha256 mismatch after write")
    with pytest.raises(UartfsError):
        _client(dev).flash("boot.img", "boot_a")


def test_pull_and_push_argv():
    dev = RecordingDevice().queue(0, "", "pulled 42 bytes")
    _client(dev).pull("vendor_boot_a:0:4096", "/tmp/snap.img")
    assert dev.last_call == ["uartfs", "--sudo", "pull", "vendor_boot_a:0:4096", "/tmp/snap.img"]

    dev2 = RecordingDevice().queue(0, "", "pushed 10 bytes")
    _client(dev2).push("/tmp/x", "/data/x")
    assert dev2.last_call == ["uartfs", "--sudo", "push", "/tmp/x", "/data/x"]
