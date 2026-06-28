"""SSHDevice transport: sudo wrapping, ConnectTimeout, and bounded command timeout.

Exercised here with a monkeypatched ``subprocess.run`` so the seam is covered
without real SSH (the suite otherwise injects doubles for the orchestrator).
"""

import subprocess

import pytest

from benchctl.device import SSHDevice
from benchctl.errors import CommandError


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _capture(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["kwargs"] = kwargs
        return _Completed(0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_privileged_command_is_wrapped_in_sudo(monkeypatch):
    seen = _capture(monkeypatch)
    res = SSHDevice("h", "kalm").run(["pixel-bootctl", "status"], sudo=True)
    assert res.ok and res.stdout == "ok"
    assert seen["argv"][-1] == "sudo -n pixel-bootctl status"
    assert "ConnectTimeout=10" in seen["argv"]
    assert seen["kwargs"]["timeout"] == 120.0


def test_unprivileged_command_is_not_wrapped(monkeypatch):
    seen = _capture(monkeypatch)
    SSHDevice("h", "kalm").run(["uname", "-r"])  # sudo defaults False
    assert seen["argv"][-1] == "uname -r"


def test_sudo_disabled_by_config_never_wraps(monkeypatch):
    seen = _capture(monkeypatch)
    SSHDevice("h", "root", sudo=False).run(["pixel-ota", "update", "/d"], sudo=True)
    assert seen["argv"][-1] == "pixel-ota update /d"


def test_command_timeout_returns_124_not_hang(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = SSHDevice("h", "kalm", command_timeout=3.0).run(["pixel-bootctl", "status"], sudo=True)
    assert res.returncode == 124
    assert "timed out" in res.stderr


def test_push_timeout_raises_command_error(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CommandError):
        SSHDevice("h", "kalm").push("/local/boot.img", "/tmp/boot.img")
