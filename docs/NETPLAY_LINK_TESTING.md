# Change link conditions during a paired test

`tools/net/netplay_proxy.py` is a loopback-only UDP test link with configurable latency, jitter and packet loss in both
directions. A JSON control file changes conditions without restarting either game. It uses only the Python standard
library, forwards bytes unchanged and does not read game data.

This helps test whether a long session adapts when conditions change between matches. It can also create a temporary
blackout to exercise the stalled-input retry path. It models changed latency/loss; it does **not** actually change a
direct path into a relay or model congestion, bandwidth limits, router/NAT behavior, or the internet.

## Paired-game setup

Use separate test saves and renderer cache directories, as for the existing paired tests. Start the proxy before the
joiner. For the default ports:

```
python tools/net/netplay_proxy.py --listen-port 39521 --upstream-port 39520 --latency-ms 39 --control tests/logs/link.json --duration 600
```

Host game: `SSBU_ONLINE=host:39520`.
Joiner game: `SSBU_ONLINE=join:127.0.0.1:39521`.
Both games: `SSBU_ONLINE_DIRECT=0`, `SSBU_NET_SIM=0`, `SSBU_ONLINE_STUN=off`, `SSBU_ONLINE_UPNP=off`.

Direct paths must be disabled so traffic goes through the test link. The runtime's own link simulation must be disabled
to avoid adding delay twice. Host/join mode uses the host's in-game lobby; the proxy handles the joiner's traffic to and
from it. One proxy supports one joiner for one game session; restart it if that client process is replaced.

39 ms is added in **each direction**, approximating a 78 ms RTT before local processing costs. `--jitter-ms 10` adds a
uniform random extra delay from 0 through 10 ms to each datagram; `--loss-percent 2` drops about 2% independently in each
direction. The seed reproduces decisions for the same receive order. Concurrent game runs can produce different packet
orders, so use multiple seeds and report observed counts instead of assuming identical packet histories between builds.

## Change conditions while the games remain open

Write a UTF-8 JSON object to the control file. Replace it atomically when automating; invalid/partially written objects
are reported and leave the current profile active. Fields omitted from an update retain their current values.

```
{"latency_ms":146.5,"jitter_ms":0,"loss_percent":0}
```

That changes the modeled RTT to about 293 ms. After enough valid pings, start the next match and record its chosen input
delay, frame-time percentiles, receive-queue residence, finalized hash coverage and desync/pad-check results. Restore
`{"latency_ms":39}` and repeat. To test a short outage, apply `{"loss_percent":100}` and later restore the old loss value.

Existing queued datagrams keep the conditions under which they arrived; updates affect new arrivals. This can reorder
packets during a transition. The profile is checked at least once per 100 ms while the process is scheduled. The proxy
exits after its bounded duration or Ctrl+C, reporting how many packets remain queued.

The process prints JSON events for startup, accepted profile changes, periodic counts and shutdown. Counts distinguish
configured loss, queue overflow, unexpected peers and local socket errors. Any queue overflow or substantial socket
errors makes a performance comparison suspect; inspect the log before interpreting game results. No payload bytes are
logged. Windows ICMP errors while the lobby starts are counted without stopping the proxy.

## Verified without a game

```
python tools/net/test_netplay_proxy.py
```

Eleven tests cover bidirectional byte preservation, delay and jitter bounds, seeded loss, full outages, bounded queues,
packet ordering, live profile changes, peer ownership, socket errors and JSON updates. A real loopback subprocess test
starts with 100% loss, changes the control file to 0%, and verifies an intact datagram round trip before bounded exit.
This verifies the test helper; paired-game results using it are still pending.
