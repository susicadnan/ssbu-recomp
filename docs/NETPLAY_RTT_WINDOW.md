# Match delay after connection conditions change

`SSBU_RB_NET_RECENT_RTT=1` is an optional experiment. It chooses the next match's automatic input delay using the most
recent 32 valid round-trip measurements, rather than the best and mean of the entire session. The existing jitter
formula is preserved: best RTT plus 1.5 times the difference between mean and best, after ten samples. The default
remains the full-session estimator until paired game tests compare the experiment.

Pings are sent every 100 ms, so a full window represents about 3.2 seconds with no loss. Thirty-two **received samples**
are needed to completely replace older conditions; packet loss can make this take longer. A partial window uses the
available measurements. The window is retained across rematches with the same opponent and reset when the existing
new-opponent RTT reset runs.

Why this matters: a session may move between a fast direct connection and a slower relay. A long-running minimum is
still the old route's minimum, and a mean of thousands of old samples barely responds to the new connection. The next
match can then get too little delay for a slower route, or keep too much delay after the fast route recovers.

The window applies to automatic rollback delay and the no-snapshot lockstep fallback. Fixed `rollback_delay` or
`SSBU_RB_NET_DELAY` still takes precedence. The current match's input delay is not changed in the middle of a fight.
No packet or handshake change is required. Full-session RTT statistics remain available in reports, alongside the
recent-window statistics when enabled.

## Deterministic trace tests

`tests/rbn_receive_test.c` replays exact RTT observations through the production sample-recording and delay-selection
code. It also verifies that an actual PONG message feeds both histories. All tests run without network traffic or
game content, including under undefined-behavior traps.

| Observation history | Full-session estimator | Recent-window estimator |
|---|---|---|
| 3,000 samples at 78 ms, then 32 at 293 ms | Next-match delay 3 frames | 6 frames (current automatic maximum) |
| Same history, lockstep fallback | 4 frames | 10 frames |
| 3,000 samples at 293 ms, then 32 at 78 ms | Next-match delay 6 frames | 3 frames |
| Recent samples: sixteen at 78 ms, sixteen at 98 ms | Existing formula applied to history | 93 ms effective RTT, delay 4 frames |

Tests also check unknown-RTT fallbacks, partial windows, preservation of full-session reporting, rematch retention,
new-opponent reset, loop-mode behavior and fixed-delay overrides. These are estimator tests. They do not prove a
two-PC latency or frame-time improvement.

The core time-sync test also covers `INT32_MIN` offsets. Negating that value in 32-bit arithmetic was undefined; the
calculation now widens before negation and caps the number of extra frames before narrowing. The previous implementation
traps under undefined-behavior instrumentation for this case; the corrected core and session suites pass. This is an
edge-case arithmetic fix, not a measured frame-time optimization.

For in-game validation, compare steady links at 2/78/150 ms first, then change simulated delay between matches in a
multi-match session. Require enough finalized hashes, zero reported desyncs and final-pad differences, and report the
chosen delay and queue-residence statistics alongside frame-time percentiles. Do not enable this experiment by
default based only on the trace tests.

## Live changing-link pair (2026-10-25)

An isolated integration build (`b3702f5`, branch `codex/netplay-validation-integration`) combined PR3/4/5/6/7 for this
experiment. Combined session tests, proxy tests and comparison tests passed; the full runtime build and package audit
passed. The installed executable was not replaced. PR6's later optional-debug-identity normalization is not in this
build. Both test installations lacked that header and used the same record identity.

Two sequential same-PC pairs used one natural rematch, bots 7/11, one stock, a fixed handshake seed, no fighter debug
file, direct paths disabled and runtime latency simulation off. The loopback proxy changed from 39 to 90 ms delay in
each direction when the host began its second handshake. Configured jitter/loss were zero; actual RTTs included local
scheduling jitter (roughly 80–100 ms initially and 185–197 ms after the change). These are two observed runs, not an
identical packet trace: the same proxy seed cannot reproduce concurrent arrival order.

| Second-match result | Full-session estimator | Recent 32-sample window |
|---|---|---|
| Input delay, host/join | 4 / 4 frames | 6 / 6 frames |
| Rollbacks | 57 / 32 | 25 / 17 |
| Frames replayed | 251 / 152 | 27 / 20 |
| Stalls | 42 / 44 | 1 / 0 |
| Total stall time | 431 / 837 ms | 18 / 0 ms |
| Tick-to-tick p99 | 133.32 / 133.30 ms | 30.22 / 29.20 ms |
| Timing intervals | 1,093 / 1,093 | 1,095 / 1,095 |
| Finalized checksum coverage | 1,094 / 1,089 | 1,096 / 1,096 |

Every common checksum matched and reports showed zero desyncs/final-pad differences. The full-session run **failed
strict coverage** because its last five frames were not finalized on the joiner; that run is not a full correctness
pass. The recent-window run passed strict comparison: all 486 first-match frames and all 1,096 second-match frames
matched, with equal coverage and IDs. The recent host logged 32 samples, mean 196.8 ms and best 184.1 ms at the second
match's start. Completed match-end reports provided the timing distributions, rather than early periodic snapshots.

Proxy queues did not overflow, and there were no send errors or unexpected peers. Windows receive errors appeared
only after game processes stopped (6/12 in the final baseline/recent snapshots); they reflect closed test ports.
Drivers stopped their own games after the second match's reset/report; these were controlled stops, not clean-exit
checks. The owned proxy was stopped after a final periodic snapshot with its queue drained.

The smoother measured run adds **two frames of input delay**. This is one example of adapting to a slower connection,
not a free latency reduction. More seeds, recovery to a fast link, jitter/loss, two-PC tests and input-to-display
measurements remain needed. The option stays **off by default**; no Slippi comparison has been performed.
