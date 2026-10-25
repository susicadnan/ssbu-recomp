# Desync checks without a fighter debug file

The fighter frame hook previously collected its state and called `rbn_hash_add` only while `SSBU_FIGHT_HASH` had an
open output file. Normal rollback play with that optional file disabled therefore produced no finalized match
checksums, even though input packets and reports supported them. Zero reported desyncs could mean zero comparisons.

The hook now collects the same fighter/RNG record when an actual rollback session is started, independently of file
output. `fprintf`, `fflush`, optional work-variable logging and verbose generator reports remain behind the debug-file
guard. Collection for netplay stops between rollback matches. Offline debug logging keeps its existing behavior.

Rollback configuration is initialized before the hook's startup decision, so a missing debug file does not prevent
installation of the checksum hook. No wire-format or handshake change is required. The runtime's existing comparison
and desync-reporting path consumes the checksums; this change does not add state resynchronization or hash every byte
of game memory. It uses the same logged fighter/RNG/input subset as the existing explicit-file tests.

## Verified and pending

The session test supplies representative state records with `SSBU_FIGHT_HASH` unset, finalizes them, verifies that an
ordinary input packet carries the resulting checksum, accepts an equal peer checksum and detects an intentional
mismatch. It also checks collection stops and counters reset between matches. The core session suite passes with
undefined-behavior traps, and `nnro.c` (which includes the changed fighter hook) compiles with the Windows toolchain.

The session fixture drives the collector's output directly; it does **not** execute the game getters in the fighter
hook. A paired game run now covers that path as described below. More rematch/network-condition coverage and a matched
frame-time comparison remain needed, because collecting state has a cost even when file output is disabled.

## Paired game result (2026-10-25)

Codex built `e25094e` with `scripts/make_release.py`; the full runtime build and package audit passed. A separately named
test executable was placed beside the installed module DLLs (the loader resolves them relative to the executable).
The installed executable was not replaced. Test saves were copied by the existing driver, and each game used a separate
renderer cache.

Two instances on one PC, host/join through the in-game lobby, 39 ms simulated outgoing delay on each (about 78 ms RTT),
bots 7/11, automatic delay 3 frames, 1,200 requested fight frames. Both startup logs confirmed `SSBU_FIGHT_HASH off` and
deterministic mode on. Both game processes exited with status 0 (85/81 seconds including menus and shutdown).

Strict comparison required at least 1,000 common frames: **1,152 matching finalized frame checksums**, with identical
coverage from 1 through 1152 and matching match IDs. Periodic reports showed 261/311 checksum comparisons on host/joiner,
zero reported desyncs and zero final-pad differences. The host's frame-1200 status recorded one two-frame rollback and
zero stalls. This verifies checksum collection through the actual game getters without the optional debug file.

The timing reports were periodic snapshots covering only 264/317 tick intervals, with p99 33.47/33.44 ms. They are not
full-run timing distributions or an off/on performance comparison. Remaining comparisons: multiple seeds and rematches,
jitter/loss, normal game pacing with and without explicit file output, and two-PC internet play. No Slippi comparison
has been performed. The PR remains in draft for review and further validation.
