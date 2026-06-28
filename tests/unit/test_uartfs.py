"""R2: uartfs CLI wrapper — the delta-flash + reliable-exec transport over UART.

uartfs runs locally on the bench host and rides uartd's socket to the experiment
slot. Exit codes mirror uart: 0 ok, 1 op-failure (e.g. hash mismatch), 2 daemon/
connection, 3 uartfs/remote error. `run` returns the *remote* command's result;
`flash` delta-flashes a partition and verifies.

NOTE: the real uartfs technician CLI is still a stub (uartd UF5). This pins the
contract our mock assumes — keep it in sync when UF5 lands.
"""

import json

import pytest

from benchctl.errors import UartfsError
from benchctl.uartfs import UartfsClient
from tests.support import RecordingDevice


def _client(dev, command=("uartfs",)):
    return UartfsClient(list(command), dev)


def test_run_returns_remote_result():
    payload = json.dumps({"stdout": "ok\n", "stderr": "", "rc": 0})
    dev = RecordingDevice().queue(0, payload)
    res = _client(dev).run("uname -r")
    assert dev.last_call == ["uartfs", "run", "uname -r", "--json"]
    assert res.returncode == 0
    assert res.stdout == "ok\n"


def test_run_propagates_remote_nonzero_rc():
    payload = json.dumps({"stdout": "", "stderr": "no such file", "rc": 1})
    dev = RecordingDevice().queue(0, payload)  # transport ok, remote rc=1
    res = _client(dev).run("cat /missing")
    assert res.returncode == 1
    assert "no such file" in res.stderr


def test_run_transport_failure_raises():
    dev = RecordingDevice().queue(2, "", "daemon not running")
    with pytest.raises(UartfsError):
        _client(dev).run("true")


def test_custom_invocation_prefixed():
    dev = RecordingDevice().queue(0, json.dumps({"stdout": "", "stderr": "", "rc": 0}))
    _client(dev, command=("uartfs", "--socket", "/run/uartd.sock")).run("true")
    assert dev.last_call[:3] == ["uartfs", "--socket", "/run/uartd.sock"]


def test_flash_builds_argv_and_ok():
    dev = RecordingDevice().queue(0, json.dumps({"ok": True, "sha256": "abc", "bytes_sent": 1234}))
    res = _client(dev).flash("boot.img", "boot_a")
    assert dev.last_call == ["uartfs", "flash", "boot.img", "boot_a", "--json"]
    assert res.ok is True


def test_flash_dry_run_flag():
    dev = RecordingDevice().queue(0, json.dumps({"ok": True}))
    _client(dev).flash("boot.img", "boot_a", dry_run=True)
    assert "--dry-run" in dev.last_call


def test_flash_hash_mismatch_raises():
    dev = RecordingDevice().queue(1, json.dumps({"ok": False, "error": "sha256 mismatch"}), "sha256 mismatch")
    with pytest.raises(UartfsError):
        _client(dev).flash("boot.img", "boot_a")


def test_pull_builds_argv():
    dev = RecordingDevice().queue(0, json.dumps({"ok": True, "bytes": 42}))
    _client(dev).pull("vendor_boot_a", "/tmp/snap.img")
    assert dev.last_call == ["uartfs", "pull", "vendor_boot_a", "/tmp/snap.img", "--json"]
