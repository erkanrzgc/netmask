from __future__ import annotations

import json

import pytest

from netmask_cli.interfaces import InterfaceSnapshot
from netmask_cli.presentation import as_json, completion_script, format_snapshot, systemd_unit


def test_snapshot_human_and_json_output():
    snapshot = InterfaceSnapshot(
        "eth0",
        "02:00:00:00:00:01",
        True,
        "192.0.2.10/24",
        "static",
        [{"dst": "default"}],
        rules=[{"priority": 1000}],
        dns={"servers": ["192.0.2.53"]},
    )
    text = format_snapshot(snapshot)
    assert "Interface: eth0" in text
    assert "192.0.2.10/24 (static)" in text
    assert "Policy rules: 1" in text
    assert json.loads(as_json(snapshot.to_dict()))["interface"] == "eth0"


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_completion_scripts_include_primary_and_alias(shell):
    script = completion_script(shell)
    assert "netmask-cli" in script
    assert "netmask" in script
    assert "inspect" in script
    assert "recover" in script


def test_completion_rejects_unknown_shell():
    with pytest.raises(ValueError, match="Unsupported"):
        completion_script("fish")


def test_systemd_unit_runs_foreground_with_hardening():
    unit = systemd_unit()
    assert "netmask-cli daemon foreground %I" in unit
    assert "StateDirectory=netmask" in unit
    assert "CAP_NET_ADMIN" in unit
    assert "ProtectSystem=strict" in unit
