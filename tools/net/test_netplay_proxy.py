"""Deterministic link-model tests and a real loopback datagram round trip; no game required."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from netplay_proxy import Control, ControlUpdate, Link, Profile, apply_control

UPSTREAM = ('127.0.0.1', 39520)
CLIENT = ('127.0.0.1', 50123)


class ControlApplicationTests(unittest.TestCase):
    def test_profile_timestamp_is_after_control_file_read(self):
        clock = [100.0]
        def read(current):
            clock[0] += .075                            # model I/O or scheduler delay during the control read
            return ControlUpdate(current.updated({'loss_percent': 100}))
        link = Link(UPSTREAM, Profile(39))
        with patch('netplay_proxy.time.monotonic', side_effect=lambda: clock[0]), patch('netplay_proxy.emit') as event:
            apply_control(SimpleNamespace(poll=read), link, 99)
        self.assertEqual(link.profile.loss_percent, 100)
        event.assert_called_once_with('profile', elapsed_s=1.075,
                                      profile=dict(latency_ms=39, jitter_ms=0, loss_percent=100))

    def test_bad_control_keeps_the_active_profile(self):
        def read(current):
            raise ValueError('bad update')
        link = Link(UPSTREAM, Profile(39))
        with patch('netplay_proxy.emit') as event:
            apply_control(SimpleNamespace(poll=read), link, 0)
        self.assertEqual(link.profile, Profile(39))
        event.assert_called_once_with('control_error', message='bad update')


class LinkTests(unittest.TestCase):
    def test_timed_blackout_recovers_at_packet_deadline_without_an_update(self):
        link, sent = Link(UPSTREAM, Profile()), []
        link.receive(b'establish', CLIENT, 0)
        end = link.blackout(350, 10)
        link.receive(b'before', CLIENT, 9.999)
        link.receive(b'blocked-client', CLIENT, 10)
        link.receive(b'blocked-server', UPSTREAM, end - .001)
        link.receive(b'at-deadline', UPSTREAM, end)
        link.receive(b'later', CLIENT, 11)
        link.flush(20, lambda p, t: sent.append((p, t)))
        self.assertEqual(sent, [(b'establish', UPSTREAM), (b'before', UPSTREAM),
                                (b'at-deadline', CLIENT), (b'later', UPSTREAM)])
        self.assertEqual((link.counts['lost'], link.counts['blackout_lost']), (2, 2))
        self.assertEqual(link.profile, Profile())

    def test_blackout_keeps_previously_queued_datagrams(self):
        link, sent = Link(UPSTREAM, Profile(39)), []
        link.receive(b'already-in-flight', CLIENT, 0)
        link.blackout(350, .01)
        link.receive(b'blocked', CLIENT, .02)
        link.flush(.05, lambda p, t: sent.append((p, t)))
        self.assertEqual(sent, [(b'already-in-flight', UPSTREAM)])

    def test_blackout_command_validation_and_cancel(self):
        link = Link(UPSTREAM, Profile())
        for value in (None, True, '350', -1, float('nan'), float('inf'), 5001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                link.blackout(value, 0)
        link.blackout(350, 0)
        link.blackout(0, .1)
        link.receive(b'cancelled', CLIENT, .1)
        self.assertEqual(link.counts['lost'], 0)

    def test_bidirectional_forwarding_preserves_bytes(self):
        link, sent = Link(UPSTREAM, Profile()), []
        link.receive(b'input\x00\xff', CLIENT, 0)
        link.flush(0, lambda p, t: sent.append((p, t)))
        link.receive(b'ack', UPSTREAM, 0)
        link.flush(0, lambda p, t: sent.append((p, t)))
        self.assertEqual(sent, [(b'input\x00\xff', UPSTREAM), (b'ack', CLIENT)])

    def test_delays_are_applied_in_both_directions(self):
        link, sent = Link(UPSTREAM, Profile(39)), []
        link.receive(b'input', CLIENT, 0)
        link.flush(0.038, lambda *args: sent.append(args))
        self.assertEqual(sent, [])
        link.flush(0.039, lambda *args: sent.append(args))
        link.receive(b'ack', UPSTREAM, 0.039)
        link.flush(0.077, lambda *args: sent.append(args))
        self.assertEqual(len(sent), 1)
        link.flush(0.078, lambda *args: sent.append(args))
        self.assertEqual(sent[-1], (b'ack', CLIENT))

    def test_seed_reproduces_jitter_and_loss(self):
        links = [Link(UPSTREAM, Profile(39, 10, 20), seed=812) for _ in range(2)]
        for link in links:
            for k in range(100):
                link.receive(bytes([k]), CLIENT, k / 60)
        self.assertEqual(links[0].pending, links[1].pending)
        self.assertEqual(links[0].counts, links[1].counts)
        self.assertGreater(links[0].counts['lost'], 0)
        for due, _, payload, _ in links[0].pending:
            original_time = payload[0] / 60
            self.assertGreaterEqual(due, original_time + 0.039)
            self.assertLessEqual(due, original_time + 0.049)

    def test_blackout_and_queue_limit_are_explicit(self):
        link = Link(UPSTREAM, Profile(loss_percent=100))
        link.receive(b'x', CLIENT, 0)
        self.assertEqual((link.counts['lost'], len(link.pending)), (1, 0))
        link = Link(UPSTREAM, Profile(100), queue_limit=1)
        link.receive(b'x', CLIENT, 0)
        link.receive(b'y', CLIENT, 0)
        self.assertEqual((link.counts['queue_full'], len(link.pending)), (1, 1))

    def test_live_change_only_affects_new_packets(self):
        link, sent = Link(UPSTREAM, Profile(100)), []
        link.receive(b'old', CLIENT, 0)
        link.profile = Profile()
        link.receive(b'new', CLIENT, 0.01)
        link.flush(0.01, lambda *args: sent.append(args))
        self.assertEqual(sent, [(b'new', UPSTREAM)])
        link.flush(0.1, lambda *args: sent.append(args))
        self.assertEqual(sent[-1], (b'old', UPSTREAM))

    def test_equal_deadlines_preserve_receive_order(self):
        link, sent = Link(UPSTREAM, Profile(10)), []
        for p in (b'one', b'two', b'three'):
            link.receive(p, CLIENT, 0)
        link.flush(0.01, lambda p, t: sent.append(p))
        self.assertEqual(sent, [b'one', b'two', b'three'])

    def test_unexpected_peer_cannot_take_over_client(self):
        link = Link(UPSTREAM, Profile())
        link.receive(b'unsolicited', UPSTREAM, 0)
        link.receive(b'legitimate', CLIENT, 0)
        link.receive(b'other', ('127.0.0.1', 60001), 0)
        self.assertEqual((link.client, link.counts['unexpected_peer'], len(link.pending)), (CLIENT, 2, 1))

    def test_send_errors_are_counted(self):
        link = Link(UPSTREAM, Profile())
        link.receive(b'x', CLIENT, 0)
        def fail(*args):
            raise OSError('fixture')
        link.flush(0, fail)
        self.assertEqual((link.counts['sent'], link.counts['send_errors']), (0, 1))

    def test_profiles_reject_invalid_or_misspelled_values(self):
        for values in ({'latency_ms': float('nan')}, {'jitter_ms': -1}, {'loss_percent': 101}, {'latency_ms': True}, {'latency': 39}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Profile().updated(values)

    def test_json_control_can_change_conditions_without_restart(self):
        with tempfile.TemporaryDirectory(prefix='netplay-control-') as temp:
            path = Path(temp) / 'link.json'
            control = Control(path)
            self.assertIsNone(control.poll(Profile()))
            path.write_text('{"latency_ms":146.5}', encoding='utf-8')
            update = control.poll(Profile(39, 10, 2))
            self.assertEqual(update, ControlUpdate(Profile(146.5, 10, 2)))
            self.assertIsNone(control.poll(update.profile))
            replacement = Path(temp) / 'updated.json'
            replacement.write_text('{"latency_ms":39,"loss_percent":0}', encoding='utf-8')
            os.replace(replacement, path)
            self.assertEqual(control.poll(update.profile), ControlUpdate(Profile(39, 10, 0)))

    def test_json_timed_blackout_is_applied_once_and_preserves_profile(self):
        with tempfile.TemporaryDirectory(prefix='netplay-blackout-') as temp:
            path = Path(temp) / 'link.json'
            control, link = Control(path), Link(UPSTREAM, Profile(39, 5, 2))
            path.write_text('{"blackout_ms":350}', encoding='utf-8')
            with patch('netplay_proxy.time.monotonic', return_value=10), patch('netplay_proxy.emit') as event:
                apply_control(control, link, 9)
            self.assertEqual((link.blackout_from, link.blackout_until, link.profile), (10, 10.35, Profile(39, 5, 2)))
            event.assert_any_call('blackout', elapsed_s=1, duration_ms=350, until_s=1.35)
            self.assertIsNone(control.poll(link.profile))
            for value in ('null', 'true', '-1', '5001', '"350"'):
                replacement = Path(temp) / 'new.json'
                replacement.write_text('{"blackout_ms":' + value + '}', encoding='utf-8')
                os.replace(replacement, path)
                with self.subTest(value=value), self.assertRaises(ValueError):
                    control.poll(link.profile)

    def test_real_cli_timed_blackout_recovers_without_second_file_update(self):
        with (tempfile.TemporaryDirectory(prefix='netplay-timed-live-') as temp,
              socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server,
              socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client):
            server.bind(('127.0.0.1', 0))
            server.settimeout(.05)
            client.settimeout(3)
            control = Path(temp) / 'link.json'
            control.write_text('{"blackout_ms":400}', encoding='utf-8')
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name('netplay_proxy.py')), '--listen-port', '0',
                                     '--upstream-port', str(server.getsockname()[1]), '--latency-ms', '0',
                                     '--control', str(control), '--duration', '2'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
            try:
                ready = json.loads(proc.stdout.readline())
                self.assertEqual(ready['event'], 'ready')
                self.assertEqual(json.loads(proc.stdout.readline())['event'], 'profile')
                blackout = json.loads(proc.stdout.readline())
                self.assertEqual((blackout['event'], blackout['duration_ms']), ('blackout', 400))
                self.assertAlmostEqual(blackout['until_s'] - blackout['elapsed_s'], .4, places=5)
                client.sendto(b'dropped', tuple(ready['listen']))
                with self.assertRaises(TimeoutError):
                    server.recvfrom(65535)
                time.sleep(.45)
                server.settimeout(3)
                client.sendto(b'recovered', tuple(ready['listen']))
                payload, proxy = server.recvfrom(65535)
                self.assertEqual(payload, b'recovered')
                server.sendto(b'ack', proxy)
                self.assertEqual(client.recvfrom(65535)[0], b'ack')
                out, err = proc.communicate(timeout=4)
                self.assertEqual(proc.returncode, 0, err)
                stopped = json.loads(out.strip().splitlines()[-1])
                self.assertEqual((stopped['lost'], stopped['blackout_lost'], stopped['sent']), (1, 1, 2))
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate()

    def test_real_cli_loopback_round_trip_and_live_control(self):
        with (tempfile.TemporaryDirectory(prefix='netplay-live-') as temp,
              socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server,
              socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client):
            server.bind(('127.0.0.1', 0))
            server.settimeout(0.15)
            client.settimeout(3)
            control = Path(temp) / 'link.json'
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name('netplay_proxy.py')), '--listen-port', '0',
                                     '--upstream-port', str(server.getsockname()[1]), '--latency-ms', '0', '--loss-percent', '100',
                                     '--control', str(control), '--duration', '2'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
            try:
                ready = json.loads(proc.stdout.readline())
                self.assertEqual(ready['event'], 'ready')
                client.sendto(b'lost', tuple(ready['listen']))
                with self.assertRaises(TimeoutError):
                    server.recvfrom(65535)
                control.write_text('{"loss_percent":0}', encoding='utf-8')
                changed = json.loads(proc.stdout.readline())
                self.assertEqual((changed['event'], changed['profile']['loss_percent']), ('profile', 0))
                server.settimeout(3)
                client.sendto(b'input\x00\xff', tuple(ready['listen']))
                payload, proxy = server.recvfrom(65535)
                self.assertEqual(payload, b'input\x00\xff')
                server.sendto(b'ack', proxy)
                self.assertEqual(client.recvfrom(65535)[0], b'ack')
                out, err = proc.communicate(timeout=4)
                self.assertEqual(proc.returncode, 0, err)
                stopped = json.loads(out.strip().splitlines()[-1])
                self.assertEqual((stopped['event'], stopped['sent'], stopped['queued_remaining']), ('stopped', 2, 0))
                self.assertEqual(stopped['lost'], 1)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate()


if __name__ == '__main__':
    unittest.main()
