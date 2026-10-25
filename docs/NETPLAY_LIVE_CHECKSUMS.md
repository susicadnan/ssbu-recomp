# Desync checks without a fighter debug file

The fighter frame hook previously collected its state and called `rbn_hash_add` only while `SSBU_FIGHT_HASH` had an
open output file. Normal rollback play with that optional file disabled therefore produced no finalized match
checksums, even though input packets and reports supported them. Zero reported desyncs could mean zero comparisons.

The hook now collects the same fighter/RNG record when an actual rollback session is started, independently of file
output. `fprintf`, `fflush`, optional work-variable logging and verbose generator reports remain behind the debug-file
guard. Collection for netplay stops between rollback matches. Offline debug logging keeps its existing behavior.

Rollback configuration is initialized before the hook's startup decision, so a missing debug file does not prevent
installation of the checksum hook. No wire-format or handshake change is required. The runtime's existing comparison
and desync-reporting path consumes the checksums; this change does not add state resynchronization or hash every byte
of game memory. It uses the same logged fighter/RNG/input subset as the existing explicit-file tests.

## Verified and pending

The session test supplies representative state records with `SSBU_FIGHT_HASH` unset, finalizes them, verifies that an
ordinary input packet carries the resulting checksum, accepts an equal peer checksum and detects an intentional
mismatch. It also checks collection stops and counters reset between matches. The core session suite passes with
undefined-behavior traps, and `nnro.c` (which includes the changed fighter hook) compiles with the Windows toolchain.

The session fixture drives the collector's output directly; it does **not** execute the game getters in the fighter
hook. Paired game validation remains required: unset `SSBU_FIGHT_HASH` on both games, set `SSBU_RB_NET_LOG` on both,
and require nonempty matching finalized hashes, positive checksum-comparison counts, zero reported desyncs and zero
final-pad discrepancies. Compare frame times with the explicit-debug-file case and the current shipping build, because
collecting state has a cost even when file output is disabled. Keep the PR in draft until this path is verified.
