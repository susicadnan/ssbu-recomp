# Keep received inputs inside their match

An input receiver could validate the old match tag, pause before acquiring the receive-queue lock, then resume after
the main thread had reset the session and started the next match. Queue clearing alone did not prevent this: the old
packet had not reached the queue when it was cleared. The next drain accepted the old frame because queue entries no
longer carried a tag. It could mark the peer started, supply a stale input and acknowledge the wrong history. Since
received inputs are immutable, a real input arriving for that same frame could then be discarded as a duplicate.

`rbn_rx` now checks tagged input packets and publishes their mode while holding the same queue lock used to publish
match start and reset. A receive that finishes before reset is cleared with the queue; one that finishes afterward
sees the new match tag or the inactive session and is rejected. The packet format, retry interval and pacing policy
are unchanged. Pings still receive an immediate response outside the input queue.

The Windows session fixture wraps the queue-lock acquisition only in its own translation unit. It inserts a completed
reset/start or reset/idle transition immediately before the receiver acquires that lock. This deterministically
exercises the actual production receiver at the problematic boundary without sleeps, external sockets or a production
test hook. With the old runtime, seven assertions fail: stale input can survive both transitions and prevent current
input from replacing it. With the fix, stale packets do not enter the queue, update the mode or count as current
traffic, and current input is accepted. The session suite and core input/wire/time-sync suite pass with
undefined-behavior traps enabled. The session fixture also aligns its default-on receive expectation with the
already-enabled runtime setting.

This proves the receive/reset interleaving, not its frequency in ordinary play or an improvement to frame-time
percentiles. Paired game rematch validation is described below. No Slippi comparison has been run.

Build validation: `scripts/make_release.py` completed the full runtime build and package audit at `1816d01` (9 audited
files, no game content or machine-specific content). The installed executable was not replaced.

The fixture additionally uses two real Windows threads and events to suspend the receiver at that boundary while
the main thread resets the session, alternating immediate restart and an idle interval across 16 transitions. All
pass on the fix; the old runtime fails 39 assertions in total (7 single-thread interleaving checks plus 32 checks in
the two-thread cases). The event waits are bounded, and no production timing hook was added.

## Paired rematch validation (2026-10-25)

Built runtime `1816d01` was tested as a separately named executable beside the installed module DLLs. Two instances
on one PC, bots 7/11, a fixed handshake seed, 39 ms outgoing simulated delay each, one stock and walkoff after frame
1000. This independent branch uses explicit fighter files to produce network checksums; PR6 removes that dependency
separately. No private race fixture was injected into the game build.

Both peers completed two natural matches. Strict comparison at a 400-frame minimum found **485 matching finalized
checksums in match 1 and 1,093 in match 2**, identical coverage and match IDs. Both second-match reports showed zero
desyncs and zero final-pad differences. The driver stopped each test process after the real match-2 reset message
and flushed reports (118/116 seconds); this was a controlled harness stop, not a clean-shutdown test. The quiet slot
was released after verifying no game process remained. This complements the controlled race regression; ordinary
rematches do not prove the rare interleaving occurred in-game.
