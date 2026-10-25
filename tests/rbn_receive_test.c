/* Windows, no game/dump/network required. Exercise the actual rb_net receive path, including its tagged wire
 * packets, receive queue, pre-frame drain, input prediction and mismatch handling. Transport/renderer/game hooks
 * are stubs; simulation timestamps below are synthetic, not measured game performance.
 * clang -O1 -D_CRT_SECURE_NO_WARNINGS tests/rbn_receive_test.c -o rbn_receive_test.exe
 * SPDX-License-Identifier: GPL-3.0-or-later */
#include "../runtime/rb_net.c"

int g_game_build;
static int sent_pads, last_sent_newest, last_sent_count;
int net_rb_send(const void *p, int n) {
    const uint8_t *b = p;
    if (n > 2 && b[0] == RB_MSG_PADS) {
        RbMsg m;
        if (!rb_msg_parse(b, (size_t)n - 2, &m)) abort();
        sent_pads++; last_sent_newest = m.newest; last_sent_count = m.count;
    }
    return 1;
}
int net_rb_ready(void) { return 1; }
int net_rb_no_destination(void) { return 0; }
int net_mode(void) { return 2; }
int net_online(void) { return 0; }
int net_ini_value(const char *key, char *out, size_t n) { (void)key; (void)out; (void)n; return 0; }
uint64_t ga(uint64_t addr) { (void)addr; abort(); }
void host_fixed_random_reseed(uint64_t seed) { (void)seed; abort(); }
void menu_notice(const char *text) { if (text) abort(); }
void menu_popup(const char *title, const char *text) { (void)title; (void)text; abort(); }
void nvn_front_osk_redraw(void) { abort(); }
long long nvn_front_presents(void) { abort(); }
uint64_t nnhid_menu_buttons(void) { abort(); }
int nnldn_role(void) { return 0; }
int nnldn_players(void) { return 2; }
void net_match_find_rb(void) { abort(); }
void net_match_cancel(void) { abort(); }
void net_match_get(NetMatch *out) { (void)out; abort(); }

static int fails;
#define CHECK(c, text) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, text); fails++; } } while (0)

static void reset_session(int enabled) {
    rbn_match_reset();
    g_predrain = enabled; g_loop = 0; g_delay_cfg = 2; g_local = 0;
    rbn_start(0);
}

static void receive(const RbMsg *m, int old_match) {
    uint8_t b[RB_PADS_HEAD + RB_MAX_PADS_PER_MSG * RB_PAD_WIRE + 2];
    size_t n = rb_msg_build(b, sizeof b - 2, m);
    CHECK(n > 0, "packet built");
    b[n++] = (uint8_t)(pads_tag() ^ (old_match ? 1 : 0));
    b[n++] = 4;
    rbn_rx(b, (int)n);
}

static void receive_one(int frame, uint64_t buttons) {
    RbMsg m = {0}; m.type = RB_MSG_PADS; m.player = 1; m.newest = frame; m.count = 1;
    m.pads[0].buttons = buttons;
    receive(&m, 0);
}

static void boundary_tests(void) {
    reset_session(0);
    receive_one(1, 1);
    rbn_before_frame(0);
    CHECK(rbn_confirmed() == 0, "default keeps the old receive boundary");
    rbn_drain(0);
    CHECK(rbn_confirmed() == 1, "ordinary safe-point drain still works");

    reset_session(1);
    receive_one(1, 1);              /* arrives during the timer wait, before any pad read */
    rbn_before_frame(0);
    CHECK(rbn_pad(1, 1).buttons == 1 && g_stat_pred == 0, "available input is used without prediction");
    CHECK(g_preframe_pads == 1 && g_cur == 0, "account for received frames without advancing the clock");
    CHECK(rbn_take_mismatch() == 0, "input received before simulation needs no rollback");
    receive_one(2, 2);              /* arrives between pad reads: it must stay queued */
    CHECK(rbn_pad(1, 2).buttons == 1 && rbn_pad(1, 2).buttons == 1, "pad reads cannot drain halfway through a pass");
    rbn_before_frame(2);
    CHECK(rbn_pad(1, 3).buttons == 2, "next pass predicts from the freshest input");
    CHECK(rbn_take_mismatch() == 2, "earlier wrong prediction survives the pre-frame drain");
    CHECK(g_hfinal_upto == 0, "pre-frame drain must not finalize the wrong simulation");

    reset_session(1);
    RbMsg m = {0}; m.type = RB_MSG_PADS; m.player = 1; m.newest = 1; m.count = 1; m.pads[0].buttons = 9;
    receive(&m, 1);
    rbn_before_frame(0);
    CHECK(rbn_confirmed() == 0, "previous-match packets remain rejected");
    receive_one(2, 2); receive_one(1, 1); receive_one(2, 2);
    rbn_before_frame(0);
    CHECK(rbn_confirmed() == 2 && g_preframe_pads == 2, "reordered and duplicated packets confirm once");
    receive_one(3, 3);
    g_loop = 1; rbn_before_frame(2);
    CHECK(rbn_confirmed() == 2, "loop-test delivery schedule is unchanged");
    g_loop = 0;
    rbn_match_reset(); rbn_before_frame(0);
    CHECK(!g_preframe_pads && !g_started, "no drain between matches; per-match telemetry resets");
}

static uint64_t retention_step(uint64_t state, RbPad p) { return state * 131 + p.buttons; }
static void retention_tests(void) {
    reset_session(1);
    receive_one(1, 0); receive_one(2, 0); rbn_drain(0);
    uint64_t history[7] = {0}, reference = 0;
    for (int f = 1; f <= 6; f++) {
        rbn_local_pad(f, (RbPad){0});
        history[f] = retention_step(history[f - 1], rbn_pad(1, f));
        reference = retention_step(reference, (RbPad){f < 3 ? 0 : 1});
    }
    /* A rollback has replayed frame 3 using its old guess and captured states 2 and 3. The middle drain now
     * confirms 3..6, discovers that frame 3 was wrong, and puts the correction back for the next rollback. */
    for (int f = 3; f <= 6; f++) receive_one(f, 1);
    rbn_drain(6);
    int mismatch = rbn_take_mismatch(); rbn_put_mismatch(mismatch);
    CHECK(mismatch == 3 && rbn_confirmed() == 6, "inputs can be fully received while an earlier correction remains pending");
    long long floor = rbn_snapshot_floor(0);
    CHECK(floor == 2 && rb_input_pending_mismatch() == 3, "retention pins the state before the pending mismatch without consuming it");
    /* Model the pool's retention rule: release an older state when a newer one exists at/before its supplied floor.
     * Old floor=6 discards correct state 2 in favor of wrong state 3; floor=2 retains the state needed for replay. */
    int retained = 3 <= floor ? 3 : 2;
    uint64_t restored = history[retained];
    for (int f = retained + 1; f <= 6; f++) restored = retention_step(restored, rb_input_get(1, f, NULL));
    CHECK(retained <= mismatch - 1 && restored == reference, "memory-pressure retention preserves a state that can reproduce the reference");
    mismatch = rbn_take_mismatch();
    CHECK(rbn_snapshot_floor(mismatch) == 2, "capture immediately before a load also pins the already-taken correction");
    CHECK(rbn_snapshot_floor(0) == 6, "completed correction can release old history again");
    CHECK(rbn_snapshot_floor(1) == 0, "a correction at the first frame retains the base state");
    printf("RETENTION_MODEL confirmed 6, pending correction 3: retained state %d; reference %s\n", retained, restored == reference ? "identical" : "DIFFERS");
}

static void resend_tests(void) {
    reset_session(0);
    g_resend_ms = 17;
    rbn_local_pad(18, (RbPad){7});
    sent_pads = 0;
    for (int half_ms = 0; half_ms < 2000; half_ms++) rbn_resend(half_ms / 2.0);
    CHECK(sent_pads == 59 && g_stall_resends == 59, "one second of polling is bounded to 59 retries at 17 ms");
    CHECK(last_sent_newest == 20 && last_sent_count == 20, "stalled retries carry the same outstanding input history");
    CHECK(rb_input_local_latest() == 20, "retries do not invent simulation frames");
    g_started = 0; rbn_resend(1001); CHECK(sent_pads == 59, "no retransmit between matches");
    g_started = 1; g_loop = 1; rbn_resend(1001); CHECK(sent_pads == 59, "no retransmit in the loop bot"); g_loop = 0;
    rbn_match_reset(); CHECK(g_stall_resends == 0 && g_resend_last_ms < 0, "retry timer and counter reset between matches");

    /* Both simulations are stopped: drive only the production retry function with a virtual monotonic clock. Drop
     * all packets until a one-way blackout ends; the first successful retry arrives 39 ms later (78 ms RTT).
     * Sweep blackout endpoints so a favorable alignment with either retry period cannot select the result. */
    double means[2] = {0}, worst[2] = {0};
    for (int policy = 0; policy < 2; policy++) {
        reset_session(0); g_resend_ms = policy ? 17 : 50;
        rbn_local_pad(18, (RbPad){7});
        for (int blackout_ms = 1; blackout_ms <= 200; blackout_ms++) {
            g_resend_last_ms = -1; double delivered_at = -1;
            for (int ms = 0; ms <= 300; ms++) {
                int before = sent_pads; rbn_resend(ms);
                if (sent_pads > before && ms >= blackout_ms) { delivered_at = ms + 39; break; }
            }
            CHECK(delivered_at >= 0, "stalled peer eventually receives the outstanding packet");
            double recovery = delivered_at - blackout_ms;
            means[policy] += recovery / 200;
            if (recovery > worst[policy]) worst[policy] = recovery;
        }
    }
    CHECK(means[1] < means[0] && worst[1] < worst[0], "shorter bounded retry improves mean and worst blackout recovery");
    printf("RECOVERY_MODEL 200 blackout endpoints, 39 ms transit | mean %.2f -> %.2f ms | worst %.2f -> %.2f ms after blackout\n",
           means[0], means[1], worst[0], worst[1]);
    g_resend_ms = 50;
}

/* Identical precomputed delivery schedules are replayed with the feature off and on. Every packet has 16 redundant
 * inputs (within the existing protocol); loss and jitter do not depend on the receiving policy. No wall-clock sleeps.
 * The toy simulation hashes every input, so correctness is checked against a perfect-input reference. */
#define SIM_FRAMES 1800
#define PERIOD_US 16667
typedef struct { int at, frame, delivered; } Arrival;
typedef struct { int rollbacks, replayed, predicted; uint64_t hash; } Result;
static Arrival arrivals[SIM_FRAMES];
static uint64_t states[SIM_FRAMES + 1];
static uint32_t rng;
static uint32_t random32(void) { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }
static RbPad pad(int player, int frame) {
    RbPad p = {0};
    if (frame > 0) {
        int phase = frame / (player ? 7 : 11) % 4;
        p.buttons = (uint64_t)phase;
        p.lx = (int16_t)(phase == 1 ? 20000 : phase == 2 ? -20000 : 0);
    }
    return p;
}
static uint64_t step(uint64_t h, RbPad a, RbPad b) {
    return (h ^ a.buttons ^ (b.buttons << 8) ^ ((uint64_t)(uint16_t)a.lx << 16) ^ ((uint64_t)(uint16_t)b.lx << 32)) * 1099511628211ull;
}
static void deliver_until(int time_us) {
    /* Traverse backwards deliberately: multiple packets in a batch are applied out of order. */
    for (int j = SIM_FRAMES - 1; j >= 0; j--) {
        Arrival *a = &arrivals[j];
        if (a->delivered || a->at > time_us) continue;
        a->delivered = 1;
        RbMsg m = {0}; m.type = RB_MSG_PADS; m.player = 1; m.newest = a->frame + 2;
        m.count = m.newest < 16 ? m.newest : 16;
        for (int k = 0; k < m.count; k++) m.pads[k] = pad(1, m.newest - k - 2);
        receive(&m, 0);
    }
}
static Result simulate(int enabled, int latency_us, int jitter_us, int loss_pct, int work_us) {
    reset_session(enabled);
    rng = 0x983217u;
    memset(states, 0, sizeof states);
    for (int j = 0; j < SIM_FRAMES; j++) {
        int jitter = jitter_us ? (int)(random32() % (unsigned)(2 * jitter_us + 1)) - jitter_us : 0;
        arrivals[j].at = j * PERIOD_US + latency_us + jitter;
        arrivals[j].frame = j + 1;
        arrivals[j].delivered = (int)(random32() % 100) < loss_pct && j < SIM_FRAMES - 1;
    }
    Result r = {0};
    for (int f = 1; f <= SIM_FRAMES; f++) {
        int start = (f - 1) * PERIOD_US;
        deliver_until(start);
        rbn_before_frame(f - 1);
        rbn_local_pad(f, pad(0, f));
        states[f] = step(states[f - 1], rbn_pad(0, f), rbn_pad(1, f));
        deliver_until(start + work_us);
        rbn_drain(f);
        int mis = rbn_take_mismatch();
        if (mis) {
            CHECK(mis <= f && f - mis < RB_RING - 1, "mismatch is in simulated history");
            r.rollbacks++; r.replayed += f - mis + 1;
            for (int k = mis; k <= f; k++) states[k] = step(states[k - 1], rb_input_get(0, k, NULL), rb_input_get(1, k, NULL));
        }
    }
    deliver_until(SIM_FRAMES * PERIOD_US + 1000000);
    rbn_drain(SIM_FRAMES);
    int mis = rbn_take_mismatch();
    if (mis) for (int k = mis; k <= SIM_FRAMES; k++) states[k] = step(states[k - 1], rb_input_get(0, k, NULL), rb_input_get(1, k, NULL));
    uint64_t ref = 0;
    for (int f = 1; f <= SIM_FRAMES; f++) ref = step(ref, pad(0, f - 2), pad(1, f - 2));
    r.hash = states[SIM_FRAMES]; r.predicted = (int)g_stat_pred;
    CHECK(rbn_confirmed() >= SIM_FRAMES, "all remote frames eventually arrive");
    CHECK(r.hash == ref, "final state equals the perfect-input reference");
    return r;
}
static void compare(const char *name, int latency, int jitter, int loss, int work, int must_improve) {
    Result old = simulate(0, latency, jitter, loss, work), next = simulate(1, latency, jitter, loss, work);
    CHECK(old.hash == next.hash, "receive policies produce identical final state");
    if (must_improve) CHECK(next.replayed < old.replayed, "arrivals during pacing require less replay work");
    if (work == PERIOD_US) CHECK(old.replayed == next.replayed, "no pacing wait: behavior is unchanged");
    printf("TIMING_MODEL %-14s | rollbacks %d -> %d | replayed %d -> %d | predicted %d -> %d | state identical\n",
           name, old.rollbacks, next.rollbacks, old.replayed, next.replayed, old.predicted, next.predicted);
}
int main(void) {
    _putenv("SSBU_RB_NET=net"); _putenv("SSBU_RB_NET_HANDSHAKE=0"); _putenv("SSBU_RB_NET_PREDRAIN=");
    _putenv("SSBU_RB_NET_DELAY=2"); _putenv("SSBU_RB_NET_BOT="); _putenv("SSBU_RB_NET_LOG=");
    _putenv("SSBU_RB_NET_RESEND_MS=");
    CHECK(rbn_on() && g_predrain, "pre-frame receive defaults on after in-game validation");
    CHECK(g_resend_ms == 50, "retry interval defaults to the previous 50 ms");
    g_on = -1; _putenv("SSBU_RB_NET_PREDRAIN=1"); CHECK(rbn_on() && g_predrain, "environment enables feature");
    g_on = -1; _putenv("SSBU_RB_NET_RESEND_MS=17"); CHECK(rbn_on() && g_resend_ms == 17, "bounded retry interval can be configured");
    g_on = -1; _putenv("SSBU_RB_NET_RESEND_MS=0"); CHECK(rbn_on() && g_resend_ms == 50, "invalid retry interval cannot cause busy resending");
    boundary_tests();
    retention_tests();
    resend_tests();
    compare("low-latency", 9000, 0, 0, 6500, 0);
    compare("during-wait", 27000, 0, 0, 6500, 1);
    compare("jitter-loss", 27000, 9000, 15, 6500, 1);
    compare("late-loss", 47000, 12000, 15, 6500, 0);
    compare("no-wait", 27000, 9000, 15, PERIOD_US, 0);
    printf("rbn_receive_test: %s\n", fails ? "FAILED" : "all passed");
    free(g_rq);
    return fails ? 1 : 0;
}
