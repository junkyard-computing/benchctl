"""M3: power backends (Tasmota/Shelly HTTP, uhubctl) + registry."""

import pytest

from benchctl.config import PowerConfig
from benchctl.device import RunResult
from benchctl.power import UnknownBackend, create_power
from benchctl.power.base import HttpResponse


class FakeHttp:
    """Records GET urls; returns scripted responses or raises to mean unreachable."""

    def __init__(self, status=200, body="", raise_on=None):
        self.urls = []
        self.timeouts = []
        self._status = status
        self._body = body
        self._raise_on = raise_on  # substring -> raise

    def get(self, url, timeout=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        if self._raise_on and self._raise_on in url:
            raise ConnectionError("unreachable")
        return HttpResponse(self._status, self._body)


class FakeRunner:
    def __init__(self, returncode=0):
        self.calls = []
        self._rc = returncode

    def run(self, argv):
        self.calls.append(list(argv))
        return RunResult(self._rc, "", "")


# --- registry -------------------------------------------------------------

def test_unknown_backend_raises():
    with pytest.raises(UnknownBackend):
        create_power(PowerConfig(backend="nope", address="x"))


# --- tasmota --------------------------------------------------------------

def test_tasmota_off_on_urls():
    http = FakeHttp()
    p = create_power(PowerConfig(backend="tasmota", address="http://10.0.0.5"), http=http)
    p.off()
    p.on()
    assert "Power%20Off" in http.urls[0]
    assert "Power%20On" in http.urls[1]
    assert http.urls[0].startswith("http://10.0.0.5")


def test_tasmota_reachable_true_and_false():
    ok = create_power(PowerConfig(backend="tasmota", address="http://h"), http=FakeHttp(status=200))
    assert ok.reachable() is True
    down = create_power(
        PowerConfig(backend="tasmota", address="http://h"),
        http=FakeHttp(raise_on="http://h"),
    )
    assert down.reachable() is False


def test_tasmota_cycle_is_off_then_on_with_sleep():
    http = FakeHttp()
    slept = []
    p = create_power(
        PowerConfig(backend="tasmota", address="http://h", options={"cycle_delay": 3}),
        http=http,
        sleep=slept.append,
    )
    p.cycle()
    assert "Power%20Off" in http.urls[0]
    assert "Power%20On" in http.urls[1]
    assert slept == [3]


def test_http_backends_pass_request_timeout():
    # Every GET (commands AND reachability) must carry a finite timeout so a hung
    # plug can't block the recover loop forever.
    http = FakeHttp()
    p = create_power(
        PowerConfig(backend="tasmota", address="http://h", options={"timeout": 2.5}),
        http=http,
    )
    p.off()
    p.reachable()
    assert http.timeouts == [2.5, 2.5]
    assert all(t is not None for t in http.timeouts)


# --- shelly ---------------------------------------------------------------

def test_shelly_relay_urls_with_channel():
    http = FakeHttp()
    p = create_power(
        PowerConfig(backend="shelly", address="http://shelly", options={"channel": 1}),
        http=http,
    )
    p.off()
    p.on()
    assert http.urls[0] == "http://shelly/relay/1?turn=off"
    assert http.urls[1] == "http://shelly/relay/1?turn=on"


def test_shelly_defaults_channel_zero():
    http = FakeHttp()
    create_power(PowerConfig(backend="shelly", address="http://s"), http=http).off()
    assert http.urls[0] == "http://s/relay/0?turn=off"


# --- uhubctl --------------------------------------------------------------

def test_uhubctl_off_on_argv():
    runner = FakeRunner()
    p = create_power(
        PowerConfig(backend="uhubctl", options={"location": "1-1", "port": 2}),
        runner=runner,
    )
    p.off()
    p.on()
    assert runner.calls[0] == ["uhubctl", "-a", "off", "-l", "1-1", "-p", "2"]
    assert runner.calls[1] == ["uhubctl", "-a", "on", "-l", "1-1", "-p", "2"]


def test_uhubctl_reachable_uses_exit_code():
    assert create_power(
        PowerConfig(backend="uhubctl", options={"location": "1-1", "port": 1}),
        runner=FakeRunner(returncode=0),
    ).reachable() is True
    assert create_power(
        PowerConfig(backend="uhubctl", options={"location": "1-1", "port": 1}),
        runner=FakeRunner(returncode=1),
    ).reachable() is False
