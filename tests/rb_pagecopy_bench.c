/* The page-copy floor of a rollback (R5 fast1): what ONE PC can copy and compare per millisecond, with the same shapes as the
 * snapshot ring (r1_state.c cp_part): 4 KB pages scattered over a large region (as the game's learned set is), copied into a
 * pool of buffers, by the main thread plus helper threads woken through events (as cp_run does), or spinning.
 * No game. Windows only. Build and run on the PC (the project's clang):
 *     clang -O2 tests/rb_pagecopy_bench.c -o rb_pagecopy_bench.exe && rb_pagecopy_bench.exe [pages] [spread MB]
 * Prints, per thread count, the median of 25 runs: memcpy, non-temporal copy, compare, compare + copy of the 16 % that differ,
 * and the cost of an empty fan-out (the wake-up alone). Read it against the game's "RING capture" / "RING load k" lines:
 * the game's numbers should approach these; what is left is the ring's own bookkeeping, not the memory.
 * SPDX-License-Identifier: GPL-3.0-or-later */
#include <windows.h>
#include <emmintrin.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define PG 4096
typedef struct { uint8_t *d; const uint8_t *s; } Job;
static Job *g_j; static size_t g_nj; static int g_nt, g_mode, g_spin; static uint8_t *g_diff; static volatile LONG g_found;
static HANDLE g_go[8], g_done[8]; static volatile LONG g_gok[8];
static double now_ms(void) { static LARGE_INTEGER f; LARGE_INTEGER c; if (!f.QuadPart) QueryPerformanceFrequency(&f); QueryPerformanceCounter(&c); return c.QuadPart * 1000.0 / f.QuadPart; }
static void nt_copy(uint8_t *d, const uint8_t *s) {
    const __m128i *a = (const __m128i *)s; __m128i *b = (__m128i *)d;
    for (int i = 0; i < PG / 64; i++, a += 4, b += 4) { __m128i x0 = _mm_load_si128(a), x1 = _mm_load_si128(a + 1), x2 = _mm_load_si128(a + 2), x3 = _mm_load_si128(a + 3); _mm_stream_si128(b, x0); _mm_stream_si128(b + 1, x1); _mm_stream_si128(b + 2, x2); _mm_stream_si128(b + 3, x3); }
}
static void part(int k) {
    size_t a = g_nj * (size_t)k / (size_t)(g_nt + 1), b = g_nj * (size_t)(k + 1) / (size_t)(g_nt + 1);
    switch (g_mode) {
    case 0: for (size_t i = a; i < b; i++) memcpy(g_j[i].d, g_j[i].s, PG); break;
    case 1: for (size_t i = a; i < b; i++) nt_copy(g_j[i].d, g_j[i].s); _mm_sfence(); break;
    case 2: for (size_t i = a; i < b; i++) g_diff[i] = memcmp(g_j[i].d, g_j[i].s, PG) != 0; break;
    case 3: for (size_t i = a; i < b; i++) if (memcmp(g_j[i].d, g_j[i].s, PG)) { nt_copy(g_j[i].d, g_j[i].s); InterlockedIncrement(&g_found); } _mm_sfence(); break;
    default: break;                                     /* 4: nothing (the fan-out alone) */
    }
}
static DWORD WINAPI worker(void *p) {
    int k = (int)(intptr_t)p;
    for (;;) {
        double t0 = now_ms();
        while (!InterlockedExchange(&g_gok[k], 0)) { if (!g_spin || now_ms() - t0 > 0.3) { WaitForSingleObject(g_go[k], INFINITE); continue; } YieldProcessor(); }
        part(k + 1); SetEvent(g_done[k]);
    }
    return 0;
}
static double run(void) {
    double t0 = now_ms();
    if (g_nt) { for (int k = 0; k < g_nt; k++) { InterlockedExchange(&g_gok[k], 1); SetEvent(g_go[k]); } part(0); WaitForMultipleObjects((DWORD)g_nt, g_done, TRUE, INFINITE); }
    else part(0);
    return now_ms() - t0;
}
static int cmp_d(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return x < y ? -1 : x > y; }
int main(int argc, char **argv) {
    size_t n = argc > 1 ? (size_t)atoi(argv[1]) : 6000, spread = (argc > 2 ? (size_t)atoi(argv[2]) : 2048) << 20;
    uint8_t *region = VirtualAlloc(NULL, spread, MEM_RESERVE, PAGE_READWRITE), *pool = VirtualAlloc(NULL, n * PG, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    uint8_t *alt = VirtualAlloc(NULL, n * PG, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!region || !pool || !alt) { printf("out of memory\n"); return 1; }
    g_j = malloc(n * sizeof *g_j); g_diff = malloc(n); srand(12345);
    size_t np = spread / PG; uint8_t *used = calloc(np, 1);
    for (size_t i = 0; i < n; i++) {                   /* n pages at random places of the region (committed one by one, like the game's) */
        size_t p; do p = ((size_t)rand() * 65536u + (size_t)rand()) % np; while (used[p]); used[p] = 1;
        uint8_t *a = region + p * PG; VirtualAlloc(a, PG, MEM_COMMIT, PAGE_READWRITE);
        for (size_t o = 0; o < PG; o += 64) a[o] = (uint8_t)(i + o);
        memcpy(pool + i * PG, a, PG); memcpy(alt + i * PG, a, PG);
        if (i % 6 == 0) alt[i * PG + 1000] ^= 1;           /* 16 % of the "newest values" differ from the page (a capture's share) */
    }
    SYSTEM_INFO si; GetSystemInfo(&si);
    printf("%zu pages (%.1f MB) spread over %zu MB; %lu processors\n", n, n * PG / 1048576.0, spread >> 20, (unsigned long)si.dwNumberOfProcessors);
    printf("threads  memcpy  NT-copy  compare  cmp+copy16%%  empty-fanout   (ms, medians of 25; event wake-ups / spinning helpers)\n");
    for (int k = 0; k < 8; k++) { g_go[k] = CreateEventA(NULL, FALSE, FALSE, NULL); g_done[k] = CreateEventA(NULL, FALSE, FALSE, NULL); HANDLE t = CreateThread(NULL, 0, worker, (void *)(intptr_t)k, 0, NULL); SetThreadPriority(t, THREAD_PRIORITY_HIGHEST); CloseHandle(t); }
    static double v[25];
    for (int spin = 0; spin < 2; spin++) for (int nt = 0; nt <= 7; nt += nt < 2 ? 1 : 2) {
        g_nt = nt; g_spin = spin; double med[5];
        for (int mode = 0; mode < 5; mode++) {
            g_mode = mode;
            for (int r = 0; r < 25; r++) {
                size_t m = mode == 4 ? 0 : n; g_nj = m; g_found = 0;
                for (size_t i = 0, p = 0; i < m; i++) { while (!used[p]) p++; g_j[i].d = region + p * PG; g_j[i].s = (mode == 2 || mode == 3 ? alt : pool) + i * PG; p++; }
                if (mode == 3) for (size_t i = 0; i < n; i += 6) g_j[i].d[1000] = alt[i * PG + 1000] ^ 1;      /* the 16 % differ again */
                Sleep(2);                                   /* helpers asleep, as between two frames (spin: they wake for 0.3 ms after a job) */
                v[r] = run();
            }
            qsort(v, 25, sizeof v[0], cmp_d); med[mode] = v[12];
        }
        printf("%s%d        %6.3f  %6.3f   %6.3f      %6.3f       %6.3f\n", spin ? "spin " : "     ", nt, med[0], med[1], med[2], med[3], med[4]);
    }
    printf("GB/s at the best thread count are what the ring's copies can reach; a load copies ~24 MB, a capture compares ~22 MB and copies ~4 MB (R3 net3 logs)\n");
    return 0;
}
