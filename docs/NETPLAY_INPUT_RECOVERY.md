# Preserve pending inputs when packets are delayed or reordered

The receive queue keeps 256 input frames in a ring. Previously it accepted duplicates of already-confirmed frames
unless they were older than almost the whole ring. An old frame could share a slot with a future input already received
out of order. Writing the duplicate erased that future input and created a false gap in consecutive acknowledgements.

Reproducer: confirm frames 1 through 300, receive frame 306, then receive an old copy of frame 50. Frames 50 and 306 use
the same slot. Even after 301 through 305 arrived, confirmation stopped at 305 until 306 was transmitted again.

The input queue now ignores all frames at or below its consecutive confirmation point. It also rejects frames beyond
the next 256 slots, which could otherwise overwrite a pending frame before its gap closes. Legitimate redundant packets
and out-of-order delivery within the window still work; no protocol change is required. The normal seven-frame rollback
limit and input delay settings are unaffected.

The packet parser's frame-range check also avoids adding one to `INT_MAX` for a zero-input packet. Empty startup
messages remain valid.

## Validation

Added deterministic regressions to `tests/rb_test.c` for the stale-duplicate reproduction, future out-of-window packets,
a full 256-frame receive window arriving backwards, extreme frame numbers, and empty startup messages. The new
duplicate/window assertions failed before the fix and pass afterward. A build with undefined-behavior traps terminated
on the old frame-range expression; the corrected suite passes with the same instrumentation.

```
clang -O1 -fsanitize=undefined -fsanitize-undefined-trap-on-error -I runtime tests/rb_test.c runtime/rb_input.c runtime/rb_wire.c runtime/rb_timesync.c -o rb_test.exe
rb_test.exe
```

The existing 3,000-frame loss/reorder simulation still yields the exact perfect-input reference state: 211 rollbacks,
512 replayed frames, deepest rollback six frames. `tests/rbn_receive_test.c` also passes with undefined-behavior traps,
including all five timing scenarios and session lifecycle checks.

This fixes a demonstrated receive-history failure; no real-match frame-time improvement is claimed for it. End-to-end
Smash determinism and internet tests remain required alongside the other netplay changes.
