"""M2: uart CLI wrapper — argv construction, --json parsing, timeout mapping.

The `uart` binary runs locally on the bench host (it owns the serial port via
uartd); benchctl shells out to the configured invocation and parses --json.
"""

import json

import pytest

from benchctl.errors import UartTimeout
from benchctl.uart import UartClient
from tests.support import RecordingDevice


def _client(dev, command=("uart",)):
    return UartClient(list(command), dev)


def test_read_builds_argv_and_returns_text():
    payload = json.dumps({"text": "boot line\n", "lines": [{"t": 1.0, "text": "boot line"}]})
    dev = RecordingDevice().queue(0, payload)
    out = _client(dev).read()
    assert dev.last_call == ["uart", "read", "--json"]
    assert out.text == "boot line\n"


def test_custom_invocation_is_prefixed():
    dev = RecordingDevice().queue(0, json.dumps({"text": ""}))
    _client(dev, command=("uart", "--socket", "/run/u.sock")).read()
    assert dev.last_call[:3] == ["uart", "--socket", "/run/u.sock"]


def test_send_builds_argv():
    dev = RecordingDevice().queue(0, json.dumps({"text": ""}))
    _client(dev).send("reboot")
    assert dev.last_call == ["uart", "send", "reboot", "--json"]


def test_wait_matched_returns_text():
    payload = json.dumps({"matched": True, "text": "login: "})
    dev = RecordingDevice().queue(0, payload)
    res = _client(dev).wait(r"login:", timeout=30)
    assert dev.last_call == ["uart", "wait", "login:", "--timeout", "30", "--json"]
    assert res.matched is True
    assert "login:" in res.text


def test_wait_timeout_nonzero_raises():
    dev = RecordingDevice().queue(1, json.dumps({"matched": False, "text": ""}))
    with pytest.raises(UartTimeout):
        _client(dev).wait(r"never", timeout=2)
