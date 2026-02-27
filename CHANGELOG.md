# Changelog

## v2.1.0 - 2026-02-27

- Reworked restart flow to strict state machine with invariant:
  - `down_detected && start_attempted && up_healthy`
- Added origin-session proactive ACK contract (`restart_guard.result.v1`) and structured delivery metadata.
- Added disaster delivery route with retry budget:
  - `origin session -> agent:main:main -> discovered external channels`
- Added diagnostics bundle generation for failure paths:
  - concise external summary + local detailed diagnostics files.
- Added zero-config auto entry (`scripts/auto_restart.py`) and channel discovery (`scripts/discover_channels.py`).
- Fixed config parsing robustness and backward compatibility mappings for legacy fields.
- Updated docs to bilingual format (`README.md`, `SKILL.md`) and added implementation spec.
- Added unit tests for parser, state machine, delivery fallback, channel discovery, and origin selection.

