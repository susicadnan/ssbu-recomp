# Retain snapshots for corrections that have not finished

Receiving an input is different from replaying the simulation with that input. If an input arrives during rollback
for a frame already replayed, the middle drain leaves its mismatch pending for a second correction. Consecutive input
confirmation can advance beyond that frame before the second correction starts.

Previously `rbg_capture` gave the snapshot ring only that consecutive confirmation point, plus any explicitly refused
snapshot load. Under pool pressure, `rg_free_unneeded` may discard an older snapshot when a newer snapshot exists at or
before the supplied point. That newer snapshot can still contain a wrong prediction covered by the queued correction.

The capture's retention floor now includes the state before the earliest pending mismatch. The mismatch is inspected
without consuming it. A capture immediately before a load (`SSBU_RB_NET_KEEP=1`) also pins the correction already taken
by the caller. Existing refused-load protection remains in place. Capture-skipping rules, input delay and the wire
protocol are unchanged; the fix constrains which older states are safe to release.

## Reproducer and validation

`tests/rbn_receive_test.c` uses the production input queue and session receive path to confirm inputs 1..6 while a
correction remains pending at frame 3. It models snapshots at frames 2 and 3, where frame 2 is correct and frame 3
contains the old prediction. Applying the pool's retention rule with the old floor of 6 discards frame 2, and replay
from the remaining frame 3 fails a perfect-input toy reference. The corrected floor of 2 preserves frame 2 and produces
the reference result.

The regression failed against the previous floor and passes with the fix. It also verifies that the peek preserves the
pending mismatch, a correction already taken for a load remains pinned, and a correction at frame 1 retains the base
state. The full session suite passes with undefined-behavior traps; the affected `r1_state.c` translation unit compiles
with the Windows toolchain.

This is a game-free reproduction of the retention-policy failure, not an in-game desync reproduction or a claim of
improved frame times. Paired game validation should combine late inputs with the existing forced pool-pressure
switch (`SSBU_RB_NET_TEST_POOL`), including `SSBU_RB_NET_KEEP=1`, and require matching finalized frame hashes, no missing
rollback states and no final-pad discrepancies. Memory pressure may cause capture skips or delay-based stalls while
the required older state remains pinned; preserving recoverable simulation state takes precedence over discarding it.
