/* Every fighter of the base game selectable from the start (graphics.ini unlock_all_fighters = on; SSBU_UNLOCK_ALL=1 / 0
 * wins). #included by nnro.c after fighter_sdk/hook_host.c; started from hook_start, before the game runs.
 *
 * The game asks one function whether a fighter is available: 13.0.4 0x7101797640 (ui_chara id, mode, x2) -> bool, which
 * reads the save data by id (mods_n5_src/n5_roster.cpp, sessions N3 / N4). The hook keeps the game's answer and turns a
 * "no" into "yes" for the fighters of the base roster below. Nothing is written to the save: with the key off the game
 * is exactly what it was. Add-on fighters are left to the game (it knows what is installed), and so are ids it does not
 * know. Not a mod: the build id the lobby compares stays the same, so rooms of friends without it can still be joined.
 *
 * 13.0.5: the function moved inside the region near 0x71016ab000..0x71018cc000 that moved by different amounts
 * (docs/VERSIONS.md), and no 13.0.5 dump was at hand to put it in g_code_13_0_5. So it is found here the way
 * tools/port/verify_13_0_5.py finds deltas: the game's own unwind table (.eh_frame_hdr through MOD0) is searched for the
 * delta at which the function and its six neighbours (13.0.4 starts and sizes from sbergeron42/ssbu-decomp
 * data/functions.csv) start with the same sizes. On 13.0.4 that delta is 0. Fewer than 6 of the 7 at one delta, or two
 * deltas that fit equally: no hook, and the log says so (the game runs as without the key).
 *
 *   SSBU_UNLOCK_LOG=1   log every distinct (fighter, mode, x2, caller) whose answer the hook changed (first 64) */
#include "gfx_options.h"

static const struct { uint32_t off, size; } k_ua_fp[] = {        /* 13.0.4 offsets from GUEST_BASE, sizes in bytes */
    {0x1797230, 312}, {0x1797370, 180}, {0x1797430, 520}, {0x1797640, 1128} /* the function */, {0x1797ab0, 236}, {0x1797ba0, 240}, {0x1797da0, 852}};
#define UA_FN 3                                                    /* its index in k_ua_fp */

/* ui_chara_<name> of the 74 fighters of the base game (Piranha Plant and the add-on fighters are not in it) */
static const char *const k_ua_roster[] = {
    "mario", "donkey", "link", "samus", "samusd", "yoshi", "kirby", "fox", "pikachu", "luigi", "ness", "captain", "purin",
    "peach", "daisy", "koopa", "ice_climber", "sheik", "zelda", "mariod", "pichu", "falco", "marth", "lucina", "younglink",
    "ganon", "mewtwo", "roy", "chrom", "gamewatch", "metaknight", "pit", "pitb", "szerosuit", "wario", "snake", "ike",
    "ptrainer", "pzenigame", "pfushigisou", "plizardon", "diddy", "lucas", "sonic", "dedede", "pikmin", "lucario", "robot",
    "toonlink", "wolf", "murabito", "rockman", "wiifit", "rosetta", "littlemac", "gekkouga", "miifighter", "miiswordsman",
    "miigunner", "palutena", "pacman", "reflet", "shulk", "koopajr", "duckhunt", "ryu", "ken", "cloud", "kamui", "bayonetta",
    "inkling", "ridley", "simon", "richter", "krool", "shizue", "gaogaen"};
#define UA_N ((int)(sizeof k_ua_roster / sizeof k_ua_roster[0]))
static uint64_t g_ua_hash[UA_N];
static ImportFn g_ua_orig;
static int g_ua_log;
static volatile long g_ua_changed;

static uint64_t ua_hash40(const char *s) {                         /* hash40: length << 32 | crc32 */
    uint32_t crc = 0xffffffffu; size_t n = strlen(s);
    for (size_t i = 0; i < n; i++) { crc ^= (uint8_t)s[i]; for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1))); }
    return ((uint64_t)n << 32) | (uint32_t)~crc;
}

/* the delta of the function in the running build, from main's unwind table; 1 = found */
static int ua_find(int64_t *delta) {
    uint32_t m = rd32(GUEST_BASE + 4);
    if (memcmp((const void *)(uintptr_t)(GUEST_BASE + m), "MOD0", 4)) { printf("unlock_all: no MOD0 at main's start\n"); return 0; }
    uint64_t eh = GUEST_BASE + m + (uint64_t)(int64_t)(int32_t)rd32(GUEST_BASE + m + 16);
    if (rd8(eh + 1) != 0x1b || rd8(eh + 2) != 0x03 || rd8(eh + 3) != 0x3b) { printf("unlock_all: unexpected .eh_frame_hdr encoding\n"); return 0; }
    uint32_t n = rd32(eh + 8);
    int best = -1, ties = 0; int64_t best_d = 0;
    for (int64_t d = -0x800; d <= 0x800; d += 4) {
        int hit = 0;
        for (int k = 0; k < (int)(sizeof k_ua_fp / sizeof k_ua_fp[0]); k++) {
            uint64_t want = (uint64_t)((int64_t)k_ua_fp[k].off + d);
            uint32_t lo = 0, hi = n;                                /* the table is sorted by start */
            while (lo < hi) { uint32_t mid = (lo + hi) / 2; if (eh + (uint64_t)(int64_t)(int32_t)rd32(eh + 12 + 8ull * mid) - GUEST_BASE < want) lo = mid + 1; else hi = mid; }
            if (lo < n && eh + (uint64_t)(int64_t)(int32_t)rd32(eh + 12 + 8ull * lo) - GUEST_BASE == want) {
                uint64_t fde = eh + (uint64_t)(int64_t)(int32_t)rd32(eh + 16 + 8ull * lo);
                if (rd32(fde + 12) == k_ua_fp[k].size) hit += k == UA_FN ? 100 : 1;
            }
        }
        if (hit < 100) continue;                                    /* the function itself must be there */
        if (hit > best) { best = hit; best_d = d; ties = 0; } else if (hit == best) ties++;
    }
    if (best < 105 || ties) { printf("unlock_all: the availability function was not found in this build (best %d of 6 neighbours%s)\n", best < 0 ? 0 : best - 100, ties ? ", more than one place" : ""); return 0; }
    *delta = best_d;
    return 1;
}

static void ua_hook(Ctx *c, void *user) {
    (void)user;
    uint64_t id = c->x[0], mode = c->x[1], x2 = c->x[2], lr = c->x[30];
    g_ua_orig(c);
    if (c->x[0] & 1) return;
    uint64_t h = id & 0xffffffffffull;
    int i;
    for (i = 0; i < UA_N && g_ua_hash[i] != h; i++) ;
    if (i == UA_N) return;
    c->x[0] = 1;
    long n = InterlockedIncrement(&g_ua_changed);
    if (n == 1) printf("unlock_all: first locked fighter answered as available: ui_chara_%s (mode %u)\n", k_ua_roster[i], (unsigned)mode);
    if (g_ua_log) {
        static struct { int f; uint32_t mode, x2; uint64_t lr; } seen[64]; static volatile long nseen;
        int k, ns = (int)nseen;
        for (k = 0; k < ns; k++) if (seen[k].f == i && seen[k].mode == (uint32_t)mode && seen[k].x2 == (uint32_t)x2 && seen[k].lr == lr) break;
        if (k == ns && ns < 64) {
            seen[ns].f = i; seen[ns].mode = (uint32_t)mode; seen[ns].x2 = (uint32_t)x2; seen[ns].lr = lr; nseen = ns + 1;
            printf("unlock_all: ui_chara_%s mode %u x2 %u caller main+%llx: locked -> available\n", k_ua_roster[i], (unsigned)mode, (unsigned)x2, (unsigned long long)(lr - GUEST_BASE));
        }
    }
    fflush(stdout);
}

static void unlock_all_start(void) {
    const char *e = getenv("SSBU_UNLOCK_ALL");
    int on = e && e[0] ? e[0] != '0' : gfx_unlock_all();
    if (!on) return;
    g_ua_log = (e = getenv("SSBU_UNLOCK_LOG")) && e[0] && e[0] != '0';
    for (int i = 0; i < UA_N; i++) { char id[48]; snprintf(id, sizeof id, "ui_chara_%s", k_ua_roster[i]); g_ua_hash[i] = ua_hash40(id); }
    int64_t d;
    if (!ua_find(&d)) { printf("unlock_all: unlock_all_fighters is on but stays without effect in this run\n"); fflush(stdout); return; }
    uint64_t a = GUEST_BASE + (uint64_t)((int64_t)k_ua_fp[UA_FN].off + d);
    if (!hook_install(a, ua_hook, NULL, &g_ua_orig)) { printf("unlock_all: hook on main+%llx failed: %s\n", (unsigned long long)(a - GUEST_BASE), hook_error()); fflush(stdout); return; }
    printf("unlock_all: on: every fighter of the base roster is available (availability function at main+%llx, 13.0.4 main+%x %+lld)\n",
           (unsigned long long)(a - GUEST_BASE), k_ua_fp[UA_FN].off, (long long)d);
    fflush(stdout);
}
