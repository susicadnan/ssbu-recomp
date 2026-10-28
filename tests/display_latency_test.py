"""Compile the real display mode/release functions with a virtual clock.

No game files, display, Windows SDK or sleeping are needed. Only platform calls
and surrounding state are substituted; the scheduling functions come directly
from runtime/display.c. Run with --cc clang (or the project's clang.exe).
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def function(source, declaration):
    start = source.index(declaration)
    # These functions end at a column-zero brace. Their nested blocks are indented.
    end = source.index("\n}", start) + 2
    return source[start:end]


STUBS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <strings.h>
#define _stricmp strcasecmp
#endif
typedef int32_t LONG;
typedef int64_t LONG64;
typedef void *HANDLE;
typedef struct { LONG64 QuadPart; } LARGE_INTEGER;
#define TIMER_ALL_ACCESS 0
static int g_lat_mode, g_l1, g_lockstep, g_lock_k, test_started;
static double g_lat_margin = 3.0, g_vb_period;
static LARGE_INTEGER g_freq = {1000000};
static LONG g_lat_backoff, g_lat_ri;
static LONG64 g_lat_r[256], g_lat_release, g_lat_d_sum, g_lat_d_n;
static LONG64 g_lat_waits, g_lat_last_d, g_lat_backoffs, g_lat_near;
static LONG64 g_vsync_n = 1, g_latch = 1, g_tick_at[16];
static LONG64 clock_now, test_waits, test_due;
static int failures;
static int gfx_latency(void) { return 1; }
int rbn_started(void) { return test_started; }
static HANDLE CreateWaitableTimerExW(void *a, void *b, int c, int d) {
    (void)a; (void)b; (void)c; (void)d; return (HANDLE)(uintptr_t)1;
}
static void precise_wait_until(LONG64 due, HANDLE timer) {
    (void)timer; test_waits++; test_due = due; clock_now = due;
}
static void QueryPerformanceCounter(LARGE_INTEGER *out) { out->QuadPart = clock_now; }
static LONG InterlockedDecrement(LONG *value) { return --*value; }
static void l1_add(int k, LONG64 a, LONG64 b) { (void)k; (void)a; (void)b; }
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)
'''


CASES = r'''
static void reset(int configured, int started, int lockstep) {
    g_lat_mode = configured; test_started = started; g_lockstep = lockstep;
    g_l1 = 0; g_lat_ri = 256; g_lat_backoff = 0;
    g_lat_d_sum = g_lat_d_n = g_lat_waits = g_lat_last_d = 0;
    test_waits = test_due = 0; clock_now = 1000000; g_tick_at[1] = clock_now;
    for (int i = 0; i < 256; i++) g_lat_r[i] = 4000;
}
int main(void) {
    for (int configured = 0; configured <= 2; configured++) {
        for (int started = 0; started <= 1; started++) {
            for (int lockstep = 0; lockstep <= 1; lockstep++) {
                reset(configured, started, lockstep);
                int delayed = configured > 0 && !started && !lockstep;
                CHECK(latency_mode() == (started ? 0 : configured));
                lat_release();
                CHECK(test_waits == delayed);
                CHECK(g_lat_waits == delayed);
                CHECK(g_lat_d_n == delayed);
                CHECK((g_lat_last_d > 0) == delayed);
                CHECK(g_lat_release == clock_now);
                CHECK(g_lat_mode == configured); /* preserve the user's setting */
            }
        }
    }
    for (int configured = 1; configured <= 2; configured++) {
        reset(configured, 0, 0);
        lat_release(); CHECK(test_waits == 1); CHECK(g_lat_last_d > 0);
        test_started = 1; clock_now += 20000;
        lat_release(); CHECK(test_waits == 1); CHECK(g_lat_last_d == 0);
        test_started = 0; g_tick_at[1] = clock_now;
        lat_release(); CHECK(test_waits == 2); CHECK(g_lat_last_d > 0);
        g_lockstep = 1;
        lat_release(); CHECK(test_waits == 2); CHECK(g_lat_last_d == 0);
    }
    if (failures) { fprintf(stderr, "%d checks failed\n", failures); return 1; }
    puts("PASS: 12 display-mode combinations, online/offline transitions and delay attribution");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default='clang')
    args = parser.parse_args()
    source = (ROOT / 'runtime/display.c').read_text(encoding='utf-8')
    code = '\n'.join((STUBS, function(source, 'int latency_mode(void) {'),
                      function(source, 'static void lat_release(void) {'), CASES))
    with tempfile.TemporaryDirectory(prefix='ssbu-display-latency-') as tmp:
        path = Path(tmp) / 'test.c'
        exe = Path(tmp) / 'test.exe'
        path.write_text(code, encoding='utf-8')
        subprocess.run([args.cc, '-O1', '-Werror', '-D_CRT_SECURE_NO_WARNINGS',
                        str(path), '-o', str(exe)], check=True)
        return subprocess.run([str(exe)], timeout=10).returncode


if __name__ == '__main__':
    raise SystemExit(main())
