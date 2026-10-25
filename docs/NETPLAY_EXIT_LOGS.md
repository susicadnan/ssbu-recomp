# Trust complete netplay logs at process exit

The runtime exits through `report_and_exit` and `ExitProcess`. That bypasses C stream cleanup and `atexit` handlers.
With detailed net logging enabled, an actual paired run ended with the same partial record on both peers:
`f 1187 h 5b41384a6b693e` (14 hash digits, no line terminator). The comparator accepted it and credited an extra frame,
even though the producer always writes 16 hex digits and a newline. The complete range 1..1152 was independently
verified; the partial tail was excluded from that run's evidence.

The normal runtime exit path now calls `rbn_exit_report`. For an active session it emits the last known netplay
counters and flushes already-written checksum records before process termination; between matches it just flushes
the checksum stream. It does not receive new traffic, finalize pending predictions or alter simulation state.
Disabled rollback stays silent, and repeated shutdown entry emits the report only once. This does not add final R1
snapshot/tick timing reports; those remain separate.

`tests/rbn_cmp.py` now requires every match/frame record to be newline-terminated and every checksum to contain
exactly 16 hex digits. Signs, prefixes and truncated values are rejected, including identical malformed logs on both
sides. Hex case is normalized by value. Existing fixture hashes now use the producer's full-width format.

Validation: 23 comparison/runner tests pass (the old parser fails seven new assertions). The Windows session suite
passes with undefined-behavior traps. Its shutdown test holds a checksum record in a buffered stream, checks the
underlying OS file has zero bytes, invokes the real exit reporter, then verifies the full record reached the OS file
and the counters were emitted once. This isolates flushing from rewind/close side effects. Full build/audit and
paired game exit validation follow separately.
