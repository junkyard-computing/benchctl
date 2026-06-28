"""M1: config merge precedence (defaults < file < env < flags) + validation."""

import textwrap

import pytest

from benchctl.config import ConfigError, load_config


def _write(tmp_path, body):
    p = tmp_path / "benchctl.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_defaults_apply_when_only_required_given():
    cfg = load_config(overrides={"ssh": {"host": "felix", "user": "root"}})
    assert cfg.ssh.host == "felix"
    assert cfg.ssh.user == "root"
    assert cfg.ssh.port == 22  # default
    assert cfg.slots.home_base == "a"  # default
    assert cfg.uart.command == ["uart"]  # default
    assert cfg.timeouts.boot == 120.0  # default
    assert cfg.ssh.sudo is True  # default: wrap privileged calls in sudo -n
    assert cfg.ssh.connect_timeout == 10.0
    assert cfg.ssh.command_timeout == 120.0


def test_file_overrides_defaults(tmp_path):
    path = _write(
        tmp_path,
        """
        [ssh]
        host = "felix"
        user = "root"
        port = 2222

        [slots]
        home_base = "b"

        [power]
        backend = "tasmota"
        address = "http://10.0.0.5"

        [uart]
        command = ["uart", "--socket", "/run/uartd.sock"]

        [timeouts]
        boot = 90.0
        """,
    )
    cfg = load_config(path=path)
    assert cfg.ssh.port == 2222
    assert cfg.slots.home_base == "b"
    assert cfg.power.backend == "tasmota"
    assert cfg.power.address == "http://10.0.0.5"
    assert cfg.uart.command == ["uart", "--socket", "/run/uartd.sock"]
    assert cfg.timeouts.boot == 90.0


def test_env_overrides_file(tmp_path):
    path = _write(
        tmp_path,
        """
        [ssh]
        host = "filevalue"
        user = "root"
        """,
    )
    env = {"BENCHCTL_SSH_HOST": "envvalue", "BENCHCTL_TIMEOUTS_ROLLBACK_WAIT": "240"}
    cfg = load_config(path=path, env=env)
    assert cfg.ssh.host == "envvalue"
    assert cfg.timeouts.rollback_wait == 240.0  # coerced to float


def test_flags_override_env_and_file(tmp_path):
    path = _write(tmp_path, '[ssh]\nhost = "filevalue"\nuser = "root"\n')
    env = {"BENCHCTL_SSH_HOST": "envvalue"}
    cfg = load_config(path=path, env=env, overrides={"ssh": {"host": "flagvalue"}})
    assert cfg.ssh.host == "flagvalue"


def test_uart_command_string_is_split(tmp_path):
    path = _write(tmp_path, '[ssh]\nhost = "x"\nuser = "root"\n[uart]\ncommand = "uart --socket /s"\n')
    cfg = load_config(path=path)
    assert cfg.uart.command == ["uart", "--socket", "/s"]


def test_missing_required_ssh_host_raises():
    with pytest.raises(ConfigError):
        load_config(overrides={"ssh": {"user": "root"}})


def test_missing_required_ssh_user_raises():
    with pytest.raises(ConfigError):
        load_config(overrides={"ssh": {"host": "felix"}})


def test_env_overrides_ssh_sudo_bool():
    cfg = load_config(
        overrides={"ssh": {"host": "x", "user": "root"}},
        env={"BENCHCTL_SSH_SUDO": "false"},
    )
    assert cfg.ssh.sudo is False  # string env coerced to bool


def test_file_sets_ssh_sudo_and_timeout(tmp_path):
    path = _write(
        tmp_path,
        '[ssh]\nhost = "x"\nuser = "root"\nsudo = false\ncommand_timeout = 60.0\n',
    )
    cfg = load_config(path=path)
    assert cfg.ssh.sudo is False
    assert cfg.ssh.command_timeout == 60.0


def test_unknown_env_keys_are_ignored():
    cfg = load_config(
        overrides={"ssh": {"host": "felix", "user": "root"}},
        env={"PATH": "/usr/bin", "BENCHCTL_NOTASECTION": "x"},
    )
    assert cfg.ssh.host == "felix"
