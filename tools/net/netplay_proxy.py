"""Loopback-only, single-client UDP link impairment for paired netplay tests.

Place between the joiner and the host's in-game lobby. Disable direct paths on both games and the runtime's own
SSBU_NET_SIM to prevent bypass/double impairment. JSON control updates affect new datagrams without restarting games.
Only standard Python libraries; no payload inspection or game data. SPDX-License-Identifier: GPL-3.0-or-later.
"""
import argparse
from dataclasses import asdict, dataclass
import heapq
import json
import math
from pathlib import Path
import random
import selectors
import socket
import time


@dataclass(frozen=True)
class Profile:
    latency_ms: float = 0
    jitter_ms: float = 0
    loss_percent: float = 0

    def __post_init__(self):
        for key, limit in (('latency_ms', 5000), ('jitter_ms', 5000), ('loss_percent', 100)):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= limit:
                raise ValueError('%s must be finite and between 0 and %d' % (key, limit))

    def updated(self, values):
        if not isinstance(values, dict) or set(values) - set(asdict(self)):
            raise ValueError('control must be an object containing latency_ms, jitter_ms and/or loss_percent')
        return Profile(**(asdict(self) | values))


class Link:
    def __init__(self, upstream, profile, seed=1, queue_limit=4096):
        self.upstream, self.profile = upstream, profile
        self.client = None
        self.rng = random.Random(seed)
        self.pending = []
        self.serial = 0
        self.queue_limit = queue_limit
        self.counts = dict(received=0, sent=0, lost=0, queue_full=0, send_errors=0, receive_errors=0, unexpected_peer=0, peak_queued=0)

    def receive(self, payload, source, now):
        if source == self.upstream:
            if self.client is None:
                self.counts['unexpected_peer'] += 1
                return
            target = self.client
        else:
            if self.client is None:
                self.client = source
            if source != self.client:
                self.counts['unexpected_peer'] += 1
                return
            target = self.upstream
        self.counts['received'] += 1
        if self.rng.random() * 100 < self.profile.loss_percent:
            self.counts['lost'] += 1
            return
        if len(self.pending) >= self.queue_limit:
            self.counts['queue_full'] += 1
            return
        delay = self.profile.latency_ms + self.rng.random() * self.profile.jitter_ms
        self.serial += 1
        heapq.heappush(self.pending, (now + delay / 1000, self.serial, payload, target))
        self.counts['peak_queued'] = max(self.counts['peak_queued'], len(self.pending))

    def flush(self, now, send):
        while self.pending and self.pending[0][0] <= now:
            _, _, payload, target = heapq.heappop(self.pending)
            try:
                send(payload, target)
            except OSError:
                self.counts['send_errors'] += 1
            else:
                self.counts['sent'] += 1


class Control:
    def __init__(self, path):
        self.path = Path(path)
        self.signature = None

    def poll(self, current):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if signature == self.signature:
            return None
        self.signature = signature
        return current.updated(json.loads(self.path.read_text(encoding='utf-8-sig')))


def emit(event, **data):
    print(json.dumps(dict(event=event, **data), sort_keys=True), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--listen-port', type=int, default=39521)
    ap.add_argument('--upstream-port', type=int, default=39520)
    ap.add_argument('--latency-ms', type=float, default=39, help='delay in each direction; 39 ms approximates 78 ms RTT')
    ap.add_argument('--jitter-ms', type=float, default=0, help='additional uniform delay from 0 to this value per datagram')
    ap.add_argument('--loss-percent', type=float, default=0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--duration', type=float, default=600, help='bounded run time in seconds')
    ap.add_argument('--control', help='optional JSON file to change link conditions during a running game')
    args = ap.parse_args(argv)
    if not 0 <= args.listen_port <= 65535 or not 1 <= args.upstream_port <= 65535 or args.listen_port == args.upstream_port:
        ap.error('use distinct valid UDP ports (listen port 0 chooses a free port)')
    if not math.isfinite(args.duration) or args.duration <= 0:
        ap.error('--duration must be finite and positive')
    try:
        profile = Profile(args.latency_ms, args.jitter_ms, args.loss_percent)
    except ValueError as exc:
        ap.error(str(exc))
    link = Link(('127.0.0.1', args.upstream_port), profile, args.seed)
    control = Control(args.control) if args.control else None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, selectors.DefaultSelector() as selector:
        sock.bind(('127.0.0.1', args.listen_port))
        sock.setblocking(False)
        selector.register(sock, selectors.EVENT_READ)
        start = time.monotonic()
        next_status = start + 5
        emit('ready', listen=sock.getsockname(), upstream=link.upstream, profile=asdict(link.profile), seed=args.seed)
        try:
            while (now := time.monotonic()) < start + args.duration:
                if control:
                    try:
                        changed = control.poll(link.profile)
                    except (OSError, ValueError) as exc:
                        emit('control_error', message=str(exc))
                    else:
                        if changed is not None:
                            link.profile = changed
                            emit('profile', elapsed_s=round(now - start, 3), profile=asdict(changed))
                link.flush(now, sock.sendto)
                if now >= next_status:
                    emit('status', elapsed_s=round(now - start, 3), queued=len(link.pending), **link.counts)
                    next_status = now + 5
                timeout = min(0.1, max(0, start + args.duration - now))
                if link.pending:
                    timeout = min(timeout, max(0, link.pending[0][0] - now))
                if selector.select(timeout):
                    for _ in range(128):  # do not starve due packets under continuous inbound traffic
                        try:
                            payload, source = sock.recvfrom(65535)
                        except BlockingIOError:
                            break
                        except ConnectionResetError:
                            # Windows reports an ICMP port-unreachable this way when the lobby has not opened yet.
                            # Treat it as a failed datagram, so a short startup/restart gap cannot kill the test link.
                            link.counts['receive_errors'] += 1
                            continue
                        link.receive(payload, source, time.monotonic())
        except KeyboardInterrupt:
            pass
        emit('stopped', elapsed_s=round(time.monotonic() - start, 3), queued_remaining=len(link.pending), **link.counts)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
