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

For in-game validation, compare steady links at 2/78/150 ms first, then change simulated delay between matches in a
multi-match session. Require enough finalized hashes, zero reported desyncs and final-pad differences, and report the
chosen delay and queue-residence statistics alongside frame-time percentiles. Do not enable this experiment by
default based only on the trace tests.
