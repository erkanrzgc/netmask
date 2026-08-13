# Netmask

[![CI](https://github.com/erkanrzgc/netmask/actions/workflows/ci.yml/badge.svg)](https://github.com/erkanrzgc/netmask/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Linux first](https://img.shields.io/badge/platform-Linux--first-informational.svg)](#platform-support)
[![MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Netmask is a Linux-first Python CLI for changing, randomizing, and restoring MAC/IP settings. It records the original interface state before a change and can rotate a MAC address followed by a DHCP renewal on a schedule.

## Features

- Set or generate a locally administered unicast MAC address.
- Set IPv4 manually, renew DHCP, or probe for an unused host in the current subnet.
- Restore MAC, link state, multiple IPv4 addresses, policy rules, routes, and supported DNS state.
- Roll back the whole command automatically if any requested change fails.
- Run scheduled Linux rotation in a detached process with status and duration tracking.
- Optionally isolate traffic with a Netmask-owned nftables table or iptables chains.
- Optionally flush interface-scoped ARP state and the supported DNS cache.
- Inspect state as human-readable text or JSON and preview changes without mutation.
- Recover preserved snapshots/firewall state after an interrupted process.

## Installation

Netmask requires Python 3.10 or newer.

```console
python -m pip install netmask-cli
netmask-cli --version
```

For development:

```console
git clone https://github.com/erkanrzgc/netmask.git
cd netmask
python -m pip install -e .
```

The distribution and primary command are both `netmask-cli`. A backward-compatible `netmask` alias is installed, but many Linux systems already provide `/usr/bin/netmask`; use `netmask-cli` in scripts and documentation to avoid that collision.

## System dependencies

Linux operations use `ip` from iproute2. Random IPv4 selection uses `arping` for duplicate-address detection. DHCP is detected in this order: NetworkManager (`nmcli`), systemd-networkd (`networkctl`), then `dhclient`. The kill switch prefers `nft` and falls back to `iptables`; `resolvectl` enables DNS snapshot/restore.

```console
# Debian / Ubuntu
sudo apt install iproute2 iputils-arping network-manager nftables

# Fedora
sudo dnf install iproute iputils NetworkManager nftables
```

Netmask checks required commands before changing the interface and reports a package hint when one is missing.

## Quick start

```console
netmask-cli list
netmask-cli inspect eth0
netmask-cli change eth0 --random-mac --random-ip --dry-run
sudo netmask-cli change eth0 --random-mac --dhcp
sudo netmask-cli change eth0 --ip 192.168.1.40 --netmask 255.255.255.0
sudo netmask-cli restore eth0
```

`--random-ip` excludes the network, broadcast, and current addresses, then uses duplicate-address detection before applying a candidate. This reduces conflicts but cannot guarantee that a silent or temporarily disconnected host does not own the address.

## Modern commands

| Command | Purpose |
| --- | --- |
| `netmask-cli list` | List interface names |
| `netmask-cli inspect eth0 [--json]` | Capture current state without root |
| `netmask-cli change eth0 OPTIONS` | Apply a transactional one-shot change |
| `netmask-cli restore eth0` | Restore the preserved original snapshot |
| `netmask-cli recover [eth0]` | Recover stale firewall/daemon state and snapshots |
| `netmask-cli daemon start eth0 OPTIONS` | Start detached scheduled rotation |
| `netmask-cli daemon foreground eth0 OPTIONS` | Run rotation under a service manager |
| `netmask-cli daemon status` | Show daemon and selected backend state |
| `netmask-cli daemon stop` | Stop and restore the daemon |
| `netmask-cli completion bash\|zsh` | Print shell completion code |
| `netmask-cli systemd-unit` | Print the hardened service template |

Legacy flags such as `netmask-cli -i eth0 --random-mac --dhcp` remain supported.

### Inspection and dry run

```console
netmask-cli inspect eth0 --json
netmask-cli change eth0 --random-mac --random-ip --dry-run --json
```

Dry run reads interface state and resolves proposed values, but does not require root, send an ARP probe, create a backup, or mutate networking. Random-IP dry-run output is explicitly marked as unverified.

## CLI reference

| Option | Purpose |
| --- | --- |
| `-i`, `--interface NAME` | Select an interface |
| `-m`, `--mac ADDRESS` | Set a unicast MAC |
| `-rm`, `--random-mac` | Generate and set a local unicast MAC |
| `--ip ADDRESS` | Set a static IPv4 address |
| `-ri`, `--random-ip` | Pick a host from the current subnet |
| `-n`, `--netmask MASK` | Static IPv4 mask or prefix (default `/24`) |
| `--dhcp` | Release and renew DHCP |
| `--reset` | Restore the saved snapshot (legacy form) |
| `--list-interfaces` | List interface names without requiring root |
| `--daemon` | Start Linux scheduled rotation |
| `-t`, `--interval SECONDS` | Rotation interval, minimum 10 seconds |
| `-d`, `--duration DURATION` | Stop after values such as `30s`, `5m`, or `2h` |
| `-ks`, `--kill-switch` | Block interface traffic during each rotation |
| `--network-hygiene` | Flush interface ARP and supported DNS caches |
| `--status` / `--stop` | Inspect or stop the daemon |
| `--version` | Print the installed version |

Conflicting actions are rejected with exit code `2`. Runtime failures return `1`; successful commands return `0`.

## Daemon and restore behavior

```console
sudo netmask-cli daemon start eth0 --interval 30 --duration 10m
sudo netmask-cli daemon start eth0 --interval 30 --kill-switch --network-hygiene
netmask-cli daemon status
sudo netmask-cli daemon stop
```

Each daemon cycle transactionally sets a new MAC and obtains an address through the detected DHCP manager. A failed step first rolls back that cycle. State is stored atomically in the Netmask config directory, and PID reuse is checked using the Linux process start identity. Signals, expiration, and errors all enter cleanup: Netmask removes only its own firewall table/chains and restores the saved snapshot. A failed restore keeps the backup for a later `--reset` and returns a nonzero result.

On Linux, state defaults to `${XDG_CONFIG_HOME:-~/.config}/netmask`. Set `NETMASK_CONFIG_DIR` to isolate it in tests or automation. Netmask neither migrates nor deletes data from older application directories.

### Crash recovery

If the daemon was killed without cleanup or an earlier restore failed:

```console
sudo netmask-cli recover
# Limit recovery to one saved interface:
sudo netmask-cli recover eth0
```

Recovery refuses to run while a verified daemon is alive. It removes only the recorded Netmask firewall object, attempts every relevant restore, preserves failed backups, and returns nonzero if cleanup is incomplete.

### systemd and completion

Review the generated unit before installation:

```console
netmask-cli systemd-unit | sudo tee /etc/systemd/system/netmask@.service
sudo systemctl daemon-reload
sudo systemctl enable --now netmask@eth0.service
```

The template runs Netmask in foreground mode, stores state in `/var/lib/netmask`, and limits capabilities to network administration/raw sockets. Adjust its interval or options with a systemd override.

```console
# Bash
netmask-cli completion bash > ~/.local/share/bash-completion/completions/netmask-cli

# Zsh
netmask-cli completion zsh > ~/.zfunc/_netmask-cli
```

## Safety notes

- Do not change the interface carrying your active SSH session; bringing it down can disconnect you before recovery.
- A MAC or private IPv4 change does not change your router's public IP and does not provide anonymity.
- Static and randomly selected IPv4 addresses can conflict with DHCP leases or other hosts.
- Drivers, managed networks, and cloud platforms may reject MAC changes.
- Review firewall access before enabling the kill switch. Netmask never flushes a global ruleset or built-in iptables chain.

Use Netmask only on systems and networks you are authorized to administer.

## Platform support

Linux is the supported platform for one-shot changes, restore, daemon, kill switch, and network hygiene. Windows one-shot MAC, IPv4, and DHCP backends are experimental and mock-tested; adapter drivers and localized `netsh` output vary. Daemon and kill-switch options fail explicitly on Windows.

## Troubleshooting

- **Permission denied:** use `sudo` on Linux or an elevated terminal on Windows.
- **Command missing:** install the package suggested by Netmask's preflight error.
- **No backup found:** a successful one-shot change must occur before `--reset`; inspect the configured `backup.json` if earlier cleanup failed.
- **DHCP renewal fails:** confirm NetworkManager/networkd owns the interface or install `dhclient` as fallback.
- **Daemon is stale:** `--status` verifies the stored process identity and discards stale state.
- **`netmask --version` shows another program:** invoke `netmask-cli`; `/usr/bin/netmask` is a separate utility on some distributions.

## Development and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and contribution workflow, [SECURITY.md](SECURITY.md) for private vulnerability reporting, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [CHANGELOG.md](CHANGELOG.md). Netmask is available under the [MIT License](LICENSE).
