# Input receiving and stalled-connection recovery

## Input retries while stalled

`SSBU_RB_NET_RESEND_MS=17` is a separate optional experiment: retry outstanding inputs about once per frame while
waiting for the other player's inputs. The default remains 50 ms. Valid values are integers 8 through 50; malformed
or out-of-range values fall back to 50. Input delay, packet format and the normal per-frame sending rate are unchanged.

Both games can stop simulating at their prediction limit after a burst of packet loss. A retry must therefore use the
monotonic clock, independent of new game frames. The session now owns the retry timer and bounds the send rate even
though the stall loop polls every half millisecond. The timer resets between matches. End-of-match reports include
the interval and retry count. Test this separately from PREDRAIN before trying them together.

`rbn_receive_test` drives the actual retry function with a virtual clock and sweeps 200 blackout endpoints (1..200 ms),
then delivers the first surviving retry after 39 ms of one-way transit. At 50 ms vs 17 ms retry intervals, the mean time
from the blackout ending until delivery is 63.50 vs 47.13 ms; worst case 88 vs 55 ms. One second of half-millisecond
polls produces 59 retries at 17 ms, with identical input history and no invented simulation frames. This is a
single-sender recovery model, not a two-PC benchmark, and does not model congestion. Faster retries increase traffic
while stalled; game-level and real-network comparisons remain pending. The default is unchanged.

## Receiving after pacing

The main thread drains incoming inputs at the safe point, then waits for the next 60 Hz tick. A packet arriving during
that wait used to remain queued until after the next simulation pass. That pass could predict an input already received
by the network thread, and then replay it when the safe point finally applied the packet.

Pre-frame receiving adds a drain after pacing, before any controller reads of the next real pass. It is on by default
following in-game validation; `SSBU_RB_NET_PREDRAIN=0` disables it for comparisons.
The delay setting, wire protocol, packet rate, and rollback depth are unchanged. The loop bot's synthetic delivery
schedule is unchanged. The receive thread still only queues input; the main thread applies it.

Earlier mispredictions stay pending for the normal safe-point rollback. The new drain neither takes the mismatch nor
finalizes simulation state. It cannot change input values halfway through a pass. The clock stays at the last real
frame, as it does at the ordinary safe point.

The end-of-match report records whether the option is enabled and how many additional remote frames were confirmed by
the new drain. This count includes frames that arrived together and is not a count of avoided rollbacks.

The report also measures input receive-queue residence: time between the network thread enqueuing a nonempty input
packet and the simulation thread consuming it. Network transit is excluded. It reports the sample count, mean,
maximum, and p50/p95/p99 upper bounds from fixed one-millisecond histogram buckets. Values above 32 ms share an
overflow bucket whose bound is the observed maximum, so these are bounds, not exact percentiles. Empty startup
messages and rejected packets are excluded, and counters reset between matches. This helps distinguish network
conditions from inputs waiting on local simulation work without allocating or sorting during a frame.

## Reproduce the game-free tests (Windows)

Use the project's clang and Windows SDK/MSVC headers and libraries:

```
clang -O1 -I runtime tests/rb_test.c runtime/rb_input.c runtime/rb_wire.c runtime/rb_timesync.c -o rb_test.exe
rb_test.exe
clang -O1 -D_CRT_SECURE_NO_WARNINGS tests/rbn_receive_test.c -o rbn_receive_test.exe
rbn_receive_test.exe
```

`rbn_receive_test` includes the actual session code, wire parser, tagged packet receive queue, input prediction and
mismatch handling. Only game, rendering and transport services are stubbed. It verifies default-on behavior,
the environment override, no mid-pass drain, preservation of earlier mismatches, no premature finalization, duplicate and
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

## In-game validation and remaining comparisons

Claude ran build `c828bea` (pre-frame receive plus receive-window fixes) on 2026-10-25 with two game instances on one PC,
host/join through the in-game lobby, bots 7/11, Final Destination, 75 ms simulated one-way delay on each sender (150 ms
RTT), PREDRAIN enabled, and explicit SSBU_FIGHT_HASH logs on both. Codex independently compared the resulting `cdx1`
session logs using a minimum of 1,700 frames and strict coverage: **1,792 matching finalized frame checksums**, frames
1..1792 on both peers. The host's report shows 1,221 checksum comparisons, zero reported desyncs and zero final-pad
discrepancies. These hashes cover the runtime's logged fighter/RNG/input fields, not every byte of game memory.

The host report also shows five additional remote frames confirmed by pre-frame receiving. This run verifies the option
can operate in a game without a detected mismatch; it is not an off/on performance comparison. Both instances share
CPU/GPU resources, and tick-to-tick p99 was 128.72 ms in the quoted host report. No real-internet or Slippi performance
claim follows. The configurable retry interval added in `73aa208` was not part of this run.

The comparison tool now rejects empty or metadata-only logs. Use `--min-frames 1700 --strict` for equal-length 1,800-frame
test runs, or a justified minimum common-frame count for unequal endpoints. `tests/rbn_pair.py` requires 120 common frames
per match by default (`RBN_MIN_FRAMES` overrides this) and returns failure when the comparison or either game run fails.
Do not treat zero reported desyncs as evidence when no checksums were actually compared.

Run paired scripted matches on the same build, with the setting off and on, over multiple seeds and latency/loss
conditions. Keep input delay, renderer settings, fighters, stage, and snapshot options fixed between comparisons.
Compare the per-frame finalized state logs with `tests/rbn_cmp.py`; require no desyncs or final-pad discrepancies.
Include rematches and mixed peers (the option only affects local receive scheduling).

Measure tick-to-tick p50/p90/p99, frames over 16.67 and 33.4 ms, replayed frames, stalls, and input delay at normal speed.
Confirm with a two-PC internet match. The tests above support pre-frame receiving; the shorter retry interval still
needs independent in-game comparisons before its default changes. No Slippi comparison has been run, and no claim of
superior performance is supported by these tests.
