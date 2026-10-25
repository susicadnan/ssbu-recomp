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

Network collection is also independent of the eight-entry lifetime table used for debug frame counts. If a new fighter
object is no longer represented in that table, its state still contributes to the network checksum. Network records
use an unknown-entry marker (-1), independently of the optional generated debug header; otherwise a developer with
that header could hash entry IDs 0/1 while a runtime-only installation hashes -1 and report a false desync. Fighter
state remains ordered by frame-line calls. File diagnostics retain their resolved IDs and tracked frame counts. This
does not reset game RNG state or change first-fight initialization; natural table exhaustion remains untested.

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

## Debug-cache regression (2026-10-25)

Two private staged builds exercised the eight-entry debug-table limit. Both builds forced that table full only after
its first fighter initialization and rollback startup; the fixture changed the host debug table, not game memory or
RNG initialization. The before build used `e25094e`, and the after build used `f6cd4e9`. This instrumentation is absent
from production sources and release builds.

Both paired runs used the same fixed seed, bots 7/11, 39 ms outgoing delay per peer, no fighter debug file, and 1,200
requested fight frames. All four game processes exited 0. Before the fix: no finalized checksum records and zero peer
comparisons. After the fix: **1,152 matching finalized frame checksums** (1..1152, identical match ID), 261/290 peer
comparisons in the periodic reports, zero desyncs and zero final-pad differences. The host reached frame 1200 with one
single-frame rollback and zero stalls. This reproduces and fixes the collection failure when the table is full; it
is not a natural multi-rematch reproduction.

The installed test setup lacks `fighters/sdk_generated/lua_const.h`. Its fighter entry lookup therefore returned -1
in both builds, including the original tracked path. The regression proves collection continues for uncached objects;
cross-installation identity validation is described separately below, and natural table exhaustion is still untested.
No full-run performance improvement or Slippi comparison is inferred from these runs.

## Natural rematch result (2026-10-25)

A normal production-source `f6cd4e9` executable completed two online matches in one process session on each peer,
with no forced-table fixture and no fighter debug files. Same-PC pair, fixed handshake seed, bots 7/11, 39 ms outgoing
delay each, one stock and walkoff after frame 1000. Strict comparison at a 400-frame minimum found **485 matching
finalized frames in match 1 and 1,093 in match 2**, with equal per-match coverage and IDs. The first fight ended early;
the attempted 1,000-frame minimum did not pass for that match. Both second-match reports showed zero desyncs and zero
final-pad differences. The second match's complete 1,092 tick intervals had p99 17.04/16.71 ms on host/joiner. This is
one observed run, not an off/on cost measurement or an internet benchmark.

The harness's stop expression initially targeted a message in the separate R1 file while its driver watched stdout.
The games were stopped after the completed match-2 reports/at the 300-second harness limit; this run therefore does
not establish clean shutdown. The checksum logs were flushed by the match-end reports. This verifies a natural
rematch with collection continuing; it does not naturally exhaust all eight debug slots.

## Installation-independent network identity

The network record now reserves its entry field as -1 even when debug metadata resolves a fighter ID. Optional debug
headers must not change peer checksums for identical gameplay. This also removes repeated constant lookup for actors
outside the debug table. The fighter-hook translation unit compiles; full runtime build/audit at `49636bb` passes.

A private before/after fixture sets only host debug IDs to slot indices and joiner IDs to -1 after initialization.
Both pairs used the same seed/bots, 39 ms outgoing delay per peer, no fighter file, detailed net logging and 1,200
requested fight frames. All four games exited 0. With `42898f2`, all 1,152 complete, bounded checksum records differed;
the host periodic report compared 253 and reported 253 desyncs. Yet all 1,152 detailed frame records matched after
normalizing only the debug entry labels (pads, status, motion, positions, damage and RNG were equal). With `49636bb`,
the same fixture produced **1,152 identical complete finalized checksums**, matching detailed records, 290 host peer
comparisons, zero desyncs and zero final-pad differences. No game/RNG state was changed by the fixture.

The detail-enabled logs contain an incomplete final record because the runtime exit path bypasses C stream cleanup.
Evidence above explicitly uses the fully written frame range 1..1152, extracted with complete 16-digit hashes and
line terminators. The current comparator can accept a truncated hash; its raw 1,187-frame success is not credited.
Shutdown flushing and strict log-format validation are follow-up work. This is a targeted debug-metadata regression,
not a performance or full-memory determinism claim.
