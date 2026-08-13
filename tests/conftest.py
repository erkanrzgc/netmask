from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NETMASK_CONFIG_DIR", str(tmp_path / "config"))
