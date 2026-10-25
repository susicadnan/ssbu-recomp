# Pre-frame input receive experiment

The main thread drains incoming inputs at the safe point, then waits for the next 60 Hz tick. A packet arriving during
that wait used to remain queued until after the next simulation pass. That pass could predict an input already received
by the network thread, and then replay it when the safe point finally applied the packet.

`SSBU_RB_NET_PREDRAIN=1` adds a drain after pacing, before any controller reads of the next real pass. Default: off.
The delay setting, wire protocol, packet rate, and rollback depth are unchanged. The loop bot's synthetic delivery
schedule is unchanged. The receive thread still only queues input; the main thread applies it.

Earlier mispredictions stay pending for the normal safe-point rollback. The new drain neither takes the mismatch nor
finalizes simulation state. It cannot change input values halfway through a pass. The clock stays at the last real
frame, as it does at the ordinary safe point.

The end-of-match report records whether the option is enabled and how many additional remote frames were confirmed by
the new drain. This count includes frames that arrived together and is not a count of avoided rollbacks.

## Reproduce the game-free tests (Windows)

Use the project's clang and Windows SDK/MSVC headers and libraries:

```
clang -O1 -I runtime tests/rb_test.c runtime/rb_input.c runtime/rb_wire.c runtime/rb_timesync.c -o rb_test.exe
rb_test.exe
clang -O1 -D_CRT_SECURE_NO_WARNINGS tests/rbn_receive_test.c -o rbn_receive_test.exe
rbn_receive_test.exe
```

`rbn_receive_test` includes the actual session code, wire parser, tagged packet receive queue, input prediction and
mismatch handling. Only game, rendering and transport services are stubbed. It verifies default-off behavior,
environment opt-in, no mid-pass drain, preservation of earlier mismatches, no premature finalization, duplicate and
reordered packets, previous-match rejection, match reset, and the unchanged loop delivery schedule.

The timing model runs 1,800 frames per policy, with a fixed seed, a two-frame input delay, 16 redundant inputs per
packet, and a 16,667 microsecond frame period. The same packet delivery schedule is applied to both policies. It uses
a toy state that hashes both players' inputs and checks against a perfect-input reference. These are **synthetic replay
counts, not measured Smash frame times or internet performance**. The model's pass cost is fixed and does not increase
when replay work occurs; it therefore cannot predict actual frame-time percentiles.

Measured with clang 9.0.0 on Windows, 2026-10-25:

| Scenario (one-way latency / jitter / loss) | Pass cost | Rollbacks off -> on | Replayed frames off -> on |
|---|---|---|---|
| 9 ms / none / none | 6.5 ms | 0 -> 0 | 0 -> 0 |
| 27 ms / none / none, arrivals during pacing | 6.5 ms | 256 -> 0 | 256 -> 0 |
| 27 ms / +/-9 ms / 15% | 6.5 ms | 197 -> 58 | 229 -> 90 |
| 47 ms / +/-12 ms / 15% | 6.5 ms | 256 -> 256 | 518 -> 518 |
| 27 ms / +/-9 ms / 15%, no pacing wait | 16.667 ms | 58 -> 58 | 67 -> 67 |

All five scenarios ended at the same reference state for both policies. The existing `rb_test` also passed (211
rollbacks, 512 replayed frames, deepest rollback six frames). Both `rb_net.c` and its caller in `r1_state.c` compiled
with the Windows toolchain. This does not validate game snapshot restore or render/audio behavior.

## In-game acceptance before enabling by default

Run paired scripted matches on the same build, with the setting off and on, over multiple seeds and latency/loss
conditions. Keep input delay, renderer settings, fighters, stage, and snapshot options fixed between comparisons.
Compare the per-frame finalized state logs with `tests/rbn_cmp.py`; require no desyncs or final-pad discrepancies.
Include rematches and mixed peers (the option only affects local receive scheduling).

Measure tick-to-tick p50/p90/p99, frames over 16.67 and 33.4 ms, replayed frames, stalls, and input delay at normal speed.
Confirm with a two-PC internet match. Until those checks pass, keep the option off in the installed game. No Slippi
comparison has been run, and no claim of superior performance is supported by these tests.
