# Changelog

All notable changes will be documented here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and intends to use semantic versioning.

## [Unreleased]

### Added

- Target release: `0.3.0`.
- Collision-free `netmask-cli` primary command with the legacy `netmask` alias retained.
- Modern `inspect`, `change`, `restore`, `recover`, and `daemon` command forms.
- Human and JSON interface inspection plus mutation-free dry-run plans.
- Duplicate-address probing with `arping` before applying a random static IPv4 address.
- Crash recovery for preserved snapshots and Netmask-owned firewall state.
- Bash/Zsh completion output and a hardened systemd foreground service template.
- Transactional one-shot changes and daemon rotations with immediate automatic rollback.
- Multi-address IPv4 snapshots with related policy rules, custom route tables, and DNS state.
- Automatic DHCP integration through NetworkManager, systemd-networkd, or `dhclient` fallback.
- Preferred nftables kill-switch backend with isolated Netmask-owned tables.

## [0.1.0] - 2026-08-13

### Added

- Installable `netmask-cli` package and `netmask` command for Python 3.10+.
- Linux snapshot-based MAC/IP restore, subnet-aware random IPv4 selection, and dependency checks.
- Detached Linux rotation daemon with atomic state, instance locking, status, duration, and signal cleanup.
- Netmask-owned iptables kill-switch chains and scoped network hygiene.
- Experimental Windows one-shot backend.
- Unit, package smoke, Windows smoke, and isolated Linux integration CI coverage.

### Changed

- Project identity and config directory are now cleanly named Netmask.
- Scheduled IP rotation now renews DHCP after every MAC rotation.

### Removed

- Interactive menu/startup artwork, hostname mutation, browser-data deletion, and service restarts.

[Unreleased]: https://github.com/erkanrzgc/netmask/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/erkanrzgc/netmask/releases/tag/v0.1.0
