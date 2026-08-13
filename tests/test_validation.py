from __future__ import annotations

import ipaddress

import pytest

from netmask_cli.validation import (
    format_duration,
    is_unicast,
    is_valid_ip,
    is_valid_mac,
    is_valid_netmask,
    parse_duration,
    prefix_length,
    random_ip_in_subnet,
    random_mac,
)


@pytest.mark.parametrize(
    "value",
    ["02:00:00:00:00:01", "AA-BB-CC-DD-EE-FE", "00:11:22:33:44:55"],
)
def test_valid_mac(value):
    assert is_valid_mac(value)


@pytest.mark.parametrize(
    "value", ["", "nope", "00:11:22:33:44", "gg:11:22:33:44:55", "001122334455"]
)
def test_invalid_mac(value):
    assert not is_valid_mac(value)


def test_unicast_rejects_multicast_and_invalid():
    assert is_unicast("02:00:00:00:00:00")
    assert not is_unicast("01:00:5e:00:00:01")
    assert not is_unicast("bad")


def test_random_mac_is_local_unicast_and_changes():
    values = {random_mac() for _ in range(50)}
    assert len(values) > 1
    assert all(is_valid_mac(value) and is_unicast(value) for value in values)
    assert all(int(value[:2], 16) & 2 for value in values)


@pytest.mark.parametrize("value", ["0.0.0.0", "192.168.1.1", "255.255.255.255"])
def test_valid_ipv4(value):
    assert is_valid_ip(value)


@pytest.mark.parametrize("value", ["", "300.1.1.1", "::1", "1.2.3", "hostname"])
def test_invalid_ipv4(value):
    assert not is_valid_ip(value)


@pytest.mark.parametrize(
    ("value", "prefix"), [("255.255.255.0", 24), ("255.255.0.0", 16), ("0", 0), (32, 32)]
)
def test_netmask_prefix(value, prefix):
    assert is_valid_netmask(value)
    assert prefix_length(value) == prefix


@pytest.mark.parametrize("value", ["255.0.255.0", "33", "-1", "wat"])
def test_invalid_netmask(value):
    assert not is_valid_netmask(value)
    with pytest.raises(ValueError):
        prefix_length(value)


def test_random_ip_stays_in_subnet_and_excludes_reserved_and_current():
    current = ipaddress.IPv4Interface("192.0.2.10/24")
    for _ in range(100):
        generated = ipaddress.IPv4Address(random_ip_in_subnet(str(current)))
        assert generated in current.network
        assert generated not in {
            current.ip,
            current.network.network_address,
            current.network.broadcast_address,
        }


@pytest.mark.parametrize("cidr", ["192.0.2.0/31", "192.0.2.1/32", "invalid"])
def test_random_ip_rejects_unusable_subnet(cidr):
    with pytest.raises(ValueError):
        random_ip_in_subnet(cidr)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("30s", 30), ("5m", 300), ("2h", 7200), ("1h30m", 5400), ("1d2h", 93600), ("45", 45), (9, 9)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "abc", "1x", "1h!", "0", 0, -1])
def test_parse_duration_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize(
    ("seconds", "formatted"),
    [(0, "0s"), (59, "59s"), (60, "1m"), (3661, "1h1m1s"), (90061, "1d1h1m1s")],
)
def test_format_duration(seconds, formatted):
    assert format_duration(seconds) == formatted
