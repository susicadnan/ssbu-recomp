# Cheaper rollback state saves and loads (R5 fast1)

Branch `rollback-fast-state`, off `netplay-latency`. Runtime only (`ssbu_native.exe`); the build id is unchanged.
Written and syntax-checked in the cloud (clang, MinGW target); **not yet run on a PC**. Every change has a switch, so a
run can be taken apart part by part.

## Why

Measured on the user's PC (loop test, `SSBU_RB_NET=loop,3`, bot 7, 16-thread CPU; log `build\work\tests\logs\r1_lw0.txt`):

| Part of a 1-frame rollback | ms | What it is |
|---|---|---|
| hold of the guest threads | 1.0-1.4 | 23 threads: SuspendThread + GetThreadContext each |
| load of the state | 3.1 | 6,197 pages (24 MB) copied back, parallel on 7 threads |
| re-run of the frame | 4.4 | the game's own logic (not touched here) |
| capture (when one is needed) | 3.3 | hold 1.05, compare of the 5,637 learned pages 1.77, copy of the ~980 changed 0.3 |

A normal frame has ~6.5 ms of idle time, so a 1-frame rollback must get under that to be invisible. The copy and compare
rates (25-27 GB/s of memory traffic) say the copies are **memory-bandwidth bound**: the way down is fewer bytes and
fewer passes, plus the fixed costs around them.

## What changed

1. **Streaming (non-temporal) stores for the page copies** (`r1_state.c cp_page`): a load writes 24 MB that nothing reads
   before the re-run pass, a capture writes pool buffers nothing reads before a load. Non-temporal stores skip the
   read-for-ownership of every destination line, about a third of a copy's memory traffic. `SSBU_RB_COPY_NT`: 0 = memcpy,
   1 = captures only, 2 = loads too (default).
2. **Copy helpers stay hot** (`cp_thread`): after a job a helper spins for `SSBU_RB_COPY_SPIN` ms (default 0.3) before
   sleeping on its event, so the two or three fan-outs of one capture start in microseconds, not a scheduler wake-up each.
3. **One pass instead of two in a capture** (`r1_ring.c rg_fused_*`, `rb_learned.c ls_capture`): the compare now copies a
   changed page the moment it finds it (while it is in that core's cache) into pool buffers taken in advance; the extras
   are compared in the same fan-out. The second pass runs only for pages beyond the buffers taken (rare after warm-up).
   `SSBU_RB_FUSE=0` turns it off.
4. **The saved set follows what the fight writes now** (`rb_learned.c`): the existing `SSBU_LS_DECAY` is on by default
   (every 8 captures a page unchanged for 8 captures goes read-only and leaves the set; its next write brings it back
   with one fault) and gets a **churn guard**: a page that comes back within 24 captures is pinned and never decays again
   in that match. Earlier runs with decay 10 showed capture 3.3 -> 2.7 ms and load 3.1 -> 2.3 ms with no desync; the guard
   should keep the fault cost of the fast-churning pages out of the frame. `SSBU_LS_DECAY=0` turns it off.
5. **Cheaper hold at the safe point** (`r1_state.c ra_hold_save`): the task workers are already suspended and verified
   blocked since the kick (H1); the hold now takes them as held from that record instead of suspending, reading the
   context and resuming each of them again. Their release stays where it was (H2's end). The watchdog's release of the
   workers skips a hold that counts on them. `SSBU_RB_HOLD_PRE=0` turns it off.

New report lines: `r1: RING fast1: ...` (fused pages per capture, buffers short, settings, pre-held workers) and the
extended `r1: LS decay ...` line (pages that came back, pinned, the set's size now).

## Expected (to be measured)

| | before | expected |
|---|---|---|
| capture | 3.3 ms | ~1.5-2.0 ms (set ~2,500 pages after decay, one pass, workers pre-held) |
| load k 1 | 3.1 ms | ~1.6-2.2 ms (fewer pages, no read-for-ownership) |
| 1-frame rollback | 8-9.5 ms | ~6.5-7.5 ms |

The re-run pass (4.4 ms) is the floor this does not touch. Getting the whole rollback under 6.5 ms will need that pass
cheaper too (skip masks, or its own study); this branch takes the save and load side as far as the memory allows.

## How to measure on the PC

1. **The memory floor, no game:** `clang -O2 tests\rb_pagecopy_bench.c -o rb_pagecopy_bench.exe && rb_pagecopy_bench.exe`.
   It prints, per helper-thread count, the ms for memcpy, streaming copy, compare, compare + copy, and an empty fan-out,
   with sleeping and with spinning helpers. The game's capture and load should approach the best lines.
2. **Build the exe** (`scripts\setup.py link`, as for the other runtime changes) and run the same loop test as before:
   `SSBU_RB_NET=loop,3` with bot 7, 13.0.5, the usual length. Compare in the log: `r1: RING capture: median ...`,
   `r1: RING load k 1 ...`, `r1: RB NET` rollback parts (before the load, load, re-run), `r1: RING fast1`, `r1: LS decay`,
   `r1: LS report` (first-write faults: the decay's cost shows there), and the frame-time percentiles.
3. **Take it apart if something is off:** `SSBU_RB_COPY_NT=0`, `SSBU_RB_COPY_SPIN=0`, `SSBU_RB_FUSE=0`, `SSBU_LS_DECAY=0`,
   `SSBU_RB_HOLD_PRE=0`, one at a time.
4. **Correctness:** the two-game pair test with checksums (`tests\rbn_pair.py`, the recipe in CODEX_NETPLAY_HANDOFF.md),
   at 78 ms with jitter and loss, rematches included; require zero desyncs and zero final-pad discrepancies, as before.
   Then `rollback_delay = 2` at 78 ms: count rollbacks and the slowest 1 % of frames against the 2026-10-25 morning numbers
   (46 rollbacks, p99 100 ms).

Nothing of this is installed until those runs pass.
