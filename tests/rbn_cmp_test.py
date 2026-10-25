"""Regression tests for trustworthy rollback log comparisons; no game needed."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import rbn_cmp
import rbn_pair


class ComparisonTests(unittest.TestCase):
    def compare_logs(self, a, b, *options):
        with tempfile.TemporaryDirectory(prefix='rbn-cmp-') as temp:
            left, right = Path(temp) / 'a.txt', Path(temp) / 'b.txt'
            left.write_text(a)
            right.write_text(b)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return rbn_cmp.main([str(left), str(right), *options])

    def test_empty_logs_fail(self):
        self.assertNotEqual(self.compare_logs('', ''), 0)

    def test_metadata_only_logs_fail(self):
        self.assertNotEqual(self.compare_logs('m 1 id abcd\n', 'm 1 id abcd\n'), 0)

    def test_second_empty_match_fails(self):
        log = 'm 1 id abcd\nf 1 h 0000000000000123\nm 2 id def\n'
        self.assertNotEqual(self.compare_logs(log, log), 0)

    def test_equal_logs_pass(self):
        log = 'm 1 id abcd\nf 1 h 0000000000000123\nf 2 h 0000000000000456\n'
        self.assertEqual(self.compare_logs(log, log, '--min-frames', '2', '--strict'), 0)

    def test_hash_mismatch_fails(self):
        self.assertNotEqual(self.compare_logs('f 1 h 0000000000000123\n', 'f 1 h 0000000000000456\n'), 0)

    def test_different_match_ids_fail(self):
        self.assertNotEqual(self.compare_logs('m 1 id a\nf 1 h 0000000000000001\n', 'm 1 id b\nf 1 h 0000000000000001\n'), 0)

    def test_insufficient_common_frames_fail(self):
        self.assertNotEqual(self.compare_logs('f 1 h 0000000000000001\n', 'f 1 h 0000000000000001\n', '--min-frames', '2'), 0)

    def test_common_tail_policy_is_explicit(self):
        a, b = 'f 1 h 0000000000000001\nf 2 h 0000000000000002\n', 'f 1 h 0000000000000001\n'
        self.assertEqual(self.compare_logs(a, b), 0)
        self.assertNotEqual(self.compare_logs(a, b, '--strict'), 0)

    def test_matching_gaps_fail_in_strict_mode(self):
        log = 'f 1 h 0000000000000001\nf 3 h 0000000000000003\n'
        self.assertNotEqual(self.compare_logs(log, log, '--strict'), 0)

    def test_conflicting_duplicate_hash_fails(self):
        self.assertNotEqual(self.compare_logs('f 1 h 0000000000000002\nf 1 h 0000000000000001\n', 'f 1 h 0000000000000001\n'), 0)

    def test_identical_duplicate_hash_is_harmless(self):
        self.assertEqual(self.compare_logs('f 1 h 0000000000000001\nf 1 h 0000000000000001\n', 'f 1 h 0000000000000001\n'), 0)

    def test_invalid_records_fail(self):
        for log in ('f 0 h 0000000000000001\n', 'f 1 h garbage\n', 'f 1 h\n', 'm 1 nope x\n', 'm -1 id x\nf 1 h 0000000000000001\n'):
            with self.subTest(log=log):
                self.assertNotEqual(self.compare_logs(log, log), 0)

    def test_debug_lines_are_ignored(self):
        self.assertEqual(self.compare_logs('d 1 diagnostic\nf 1 h 0000000000000001\n', 'f 1 h 0000000000000001\n'), 0)


    def test_truncated_hashes_fail_even_when_both_files_match(self):
        for value in ('1', '5b41384a6b693e', '123456789abcdef'):
            with self.subTest(value=value):
                log = 'f 1 h ' + value + '\n'
                self.assertNotEqual(self.compare_logs(log, log, '--strict'), 0)

    def test_unterminated_records_fail(self):
        for log in ('f 1 h 0123456789abcdef', 'm 1 id abcd\nf 1 h 0123456789abcdef\nm 2 id def'):
            with self.subTest(log=log):
                self.assertNotEqual(self.compare_logs(log, log), 0)

    def test_hash_prefix_and_sign_are_not_wire_log_format(self):
        for value in ('0x0123456789abcdef', '+0123456789abcdef', '-000000000000001'):
            with self.subTest(value=value):
                log = 'f 1 h ' + value + '\n'
                self.assertNotEqual(self.compare_logs(log, log), 0)

    def test_hash_case_does_not_change_its_value(self):
        self.assertEqual(self.compare_logs('f 1 h 0123456789abcdef\n', 'f 1 h 0123456789ABCDEF\n'), 0)


class PairRunnerTests(unittest.TestCase):
    def run_pair(self, compare_code=0, child_code=0, verdict='EXIT 0'):
        with tempfile.TemporaryDirectory(prefix='rbn-pair-') as temp:
            for side in ('h', 'j'):
                for prefix in ('r1_', 'n11_'):
                    (Path(temp) / (prefix + 'test' + side + ('.txt' if prefix == 'r1_' else '.log'))).write_text('')

            def start(argv, **kwargs):
                kwargs['stdout'].write('%s: %s after 10s, test fixture\n' % (argv[2], verdict))
                return SimpleNamespace(wait=lambda: child_code, returncode=child_code)

            with (patch.object(rbn_pair, 'HERE', temp), patch.object(rbn_pair, 'STUDY', temp),
                  patch.object(rbn_pair, 'LOGS', temp), patch.object(rbn_pair.sys, 'argv', ['rbn_pair.py', 'test']),
                  patch.object(rbn_pair.time, 'sleep'), patch.object(rbn_pair.subprocess, 'Popen', side_effect=start),
                  patch.object(rbn_pair.subprocess, 'run', return_value=SimpleNamespace(returncode=compare_code)) as comparison,
                  contextlib.redirect_stdout(io.StringIO())):
                result = rbn_pair.main()
                self.assertEqual(comparison.call_args.args[0][-2:], ['--min-frames', '120'])
                return result

    def test_comparison_failure_reaches_caller(self):
        self.assertEqual(self.run_pair(compare_code=2), 2)

    def test_child_failure_reaches_caller(self):
        self.assertNotEqual(self.run_pair(child_code=1), 0)

    def test_comparator_crash_reaches_caller(self):
        self.assertNotEqual(self.run_pair(compare_code=-11), 0)

    def test_reported_timeout_fails_even_if_legacy_child_exits_zero(self):
        self.assertNotEqual(self.run_pair(verdict='TIMEOUT'), 0)

    def test_reported_game_crash_fails(self):
        self.assertNotEqual(self.run_pair(verdict='EXIT -1073741819'), 0)

    def test_success_and_intentional_stop_pass(self):
        self.assertEqual(self.run_pair(), 0)
        self.assertEqual(self.run_pair(verdict='STOPPED at N5_STOP_ON'), 0)


if __name__ == '__main__':
    unittest.main()
