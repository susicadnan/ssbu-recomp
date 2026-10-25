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
percentiles. Paired game rematch validation of this specific change is pending. No Slippi comparison has been run.
