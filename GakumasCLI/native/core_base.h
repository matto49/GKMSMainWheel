// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 1: types and primitives. comments cite <file>:<line> of the Python source. CUDA-compatible
// subset: no heap, no STL, no exceptions, no virtuals.
#pragma once
#ifdef __CUDACC_RTC__
// NVRTC: no system headers; memcpy / memset / ceil / exp / isinf are builtins
typedef signed char int8_t; typedef short int16_t; typedef int int32_t; typedef long long int64_t;
typedef unsigned char uint8_t; typedef unsigned short uint16_t; typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
#define INT64_MIN (-9223372036854775807LL - 1)
#else
#include <stdint.h>
#include <string.h>
#include <math.h>
#endif
#include "gkms_state.h"
#include "gkms_master.h"

#ifndef GK_FN
#if defined(__CUDACC__) || defined(__CUDACC_RTC__)
#define GK_FN static inline __host__ __device__
#else
#define GK_FN static inline
#endif
#endif
#include "gkms_tables.h"
#ifndef GK_HEAVY
#define GK_HEAVY GK_FN
#endif
#ifndef GK_GUARD
#if defined(__CUDACC__) || defined(__CUDACC_RTC__)
#define GK_GUARD static __host__ __device__ __forceinline__
#else
#define GK_GUARD GK_FN
#endif
#endif
#ifndef GK_OUTLINE
#if defined(__CUDACC__) || defined(__CUDACC_RTC__)
#define GK_OUTLINE static __host__ __device__ __noinline__
#else
#define GK_OUTLINE GK_FN
#endif
#endif
#ifndef GK_BIND
#if defined(__CUDACC__) || defined(__CUDACC_RTC__)
#define GK_BIND GK_FN
#elif defined(_MSC_VER)
#define GK_BIND static __declspec(noinline)
#else
#define GK_BIND static __attribute__((noinline, cold))
#endif
#endif
#ifndef INFINITY
#define INFINITY (__longlong_as_double(0x7ff0000000000000LL))
#endif

#define NONE GKMS_NONE_I64
#define INT_MAX32 2147483647LL
enum { Z_DECK = 0, Z_HAND = 1, Z_GRAVE = 2, Z_HOLD = 3, Z_LOST = 4,
       Z_REMOVE = 5, Z_FUSED = 6 };
#define MAX_PLAY 12
#define MAX_SEL 64

// One process-wide table, read and cleared through gk_counters (api.cpp); the searches of one thread at a time are
// counted exactly, a threaded tail's counts race.
#define GK_COUNTER_LIST(X) \
    X(steps) X(refresh_calls) X(resolve_misses) X(resolve_grows) X(can_play_calls) \
    X(select_calls) X(tes_full_scan_slots) X(tes_actual_slots) \
    X(tes_rebuilds) X(tes_rebuild_slots) X(tes_appends) X(turn_end_compactions) X(turn_end_removed) \
    X(phase_tests) X(phase_list_walks) \
    X(grow_appends) X(grow_steps) X(grow_registry_rows) X(card_enchant_spec_calls) X(card_enchant_rows) X(cust_rows) \
    X(card_row_calls) X(card_row_steps) \
    X(prob_calls) X(exp_calls) X(adapt_calls) X(trace_copy_bytes) X(policy_copy_bytes) X(core_copies)
#if defined(GK_COUNTERS) && !defined(GK_COUNT)
struct GkCounters {
#define GK_COUNTER_FIELD(name) uint64_t name;
    GK_COUNTER_LIST(GK_COUNTER_FIELD)
#undef GK_COUNTER_FIELD
};
static GkCounters gk_counter_table;
#define GK_COUNT(name, amount) (gk_counter_table.name += (uint64_t)(amount))
#endif
#ifndef GK_PHASE_MASKS
#define GK_PHASE_MASKS(M) 0
#endif
#ifndef GK_COUNT
#define GK_COUNT(name, amount) ((void)0)
#endif
#if defined(__CUDACC__) || defined(__CUDACC_RTC__)
#include "core_ctz_nvcc.h"
#elif defined(_MSC_VER)
#include <intrin.h>
static __forceinline int gk_ctz64(unsigned long long x) { unsigned long i; _BitScanForward64(&i, x); return (int)i; }
#define GK_CTZ64(x) gk_ctz64(x)
#else
#define GK_CTZ64(x) __builtin_ctzll(x)
#endif
// ---------------------------------------------------------------------------
// derived card description (deck.py refresh_card / effects.py resolve_card_customizes)
// ---------------------------------------------------------------------------
struct Link { int32_t effect, trigger, once; int64_t v1; };

struct Resolved {
    int32_t row;
    int32_t n_play; Link play[MAX_PLAY];
    int32_t move_pos;
    int32_t play_trigger;
    int32_t status_enchant;
    int32_t is_initial;
    int64_t lesson_add, cost_reduce, cost_fpp_add, cost_add, lesson_count_add, cost_fpp_reduce, cost_pen_reduce;
    int32_t n_va; int32_t va_t[8]; int64_t va_v[8];
    int32_t n_ta; int32_t ta_t[8]; int64_t ta_v[8];
    int64_t scd[8];
    int32_t n_xeg; int32_t xeg[8];
};

// play-start snapshot (exam.py ExamState._play_snapshot) / consume snapshot (exam_session.py ExamSession.play, the
// consume snapshot)
struct Snap {
    int32_t valid, stance, stance_step;
    int64_t fpp, fpp_sum, stance_cc, conc_cc, pres_cc, fp_cc;
    int64_t lesson_buff, parameter_buff; int32_t pbmpt; int64_t review, aggressive, block, stamina;
    int64_t pbm_turns;
    int32_t n_sc; int32_t sc_id[4]; int64_t sc_n[4];
};

typedef EffectRow Eff;

struct ResolveKey { int row, has, n; int ids[4], counts[4]; int card, upg; };
// what the binding reads back from a step that Python's ExamSession keeps on itself -- its play log (exam_session.py
// log), the turn-start read (turn_start_reads) and the select stand-in's picks (deck.select_choices).
#define GK_BIND_EVENTS 64
#define GK_BIND_SELECTS 8
#define GK_BIND_CANDS 80
struct BindEvent { int32_t kind, ref, upgrade, stance; int64_t turn, score, stamina, block, fpp; };
// one source of a split's score adds (BindLog.ss)
#define GK_SPLIT_SOURCES 32
struct BindSource { int32_t kind, source, settled, pad; int64_t value; };
struct BindSelect {
    int32_t effect, search; int64_t pick_min, pick_max;
    int32_t n_cand, n_picked; CardNo cand[GK_BIND_CANDS]; CardNo picked[GK_BIND_CANDS];
};
// The binding sets a buffer on the followed session only.
struct BindTrace { int32_t turn, effect, type, kind, source, indirect; int64_t v1; };
#define GK_OPEN_STATUSES 32
struct OpenStatus { int32_t type, sid; int64_t a0; };
struct OpenDesc {
    int32_t n_status, pad; OpenStatus status[GK_OPEN_STATUSES];
    int32_t has_param_values, n_values; int64_t value_type[3], value_value[3];
    int64_t audition_limit_turn, npc_advances, prefix_advances;
    int32_t initial_mode, n_initial; int32_t initial[16];
};
struct BindLog {
    int32_t overflow;
    int32_t ts_valid, ts_hand_n; int64_t ts_turn, ts_score, ts_stamina, ts_review; int32_t ts_hand[GKMS_MAX_ZONE];
    int32_t n_events; BindEvent events[GK_BIND_EVENTS];
    int32_t n_selects; BindSelect selects[GK_BIND_SELECTS];
    int32_t split_on, split_pad; int64_t split_direct, split_indirect;
    BindTrace *trace; int32_t trace_cap, trace_n, trace_overflow, trace_mode;
    int32_t te_valid, te_pad; int64_t te_turn, te_score, te_stamina, te_block, te_addition_block, te_consumed;
    int32_t ss_n, ss_pad; int64_t ss_rest; BindSource ss[GK_SPLIT_SOURCES];
};

struct Core {
    GkmsState g;
    uint64_t phase_candidates[64];
    int32_t tes_row[GKMS_MAX_TES];
    uint64_t resolved_valid;
    ResolveKey resolved_keys[GKMS_MAX_CARDS];
    const uint64_t *phase_masks;
    int16_t grow_reg_at[GKMS_MAX_CARDS];
    Resolved res[GKMS_MAX_CARDS];
    const MasterDesc *M;
    const RoundDesc *R;
    int32_t sel_pick;
    int32_t sel_taken;
    int32_t playing_resolved_valid;
    Resolved *playing_resolved;
    int32_t install_origin;
    int32_t target_card;
    int32_t indirect_depth;
    int32_t sc_diff_valid; int64_t sc_diff;
    int32_t permil_depth; int64_t permil_stack[64]; int32_t permil_none[64];
    int32_t card_own_depth;
    int32_t running_tes;
    int32_t trigger_commands_enabled;
    int32_t support_rolls_off;
    int32_t live_hand_valid; int32_t n_live_hand; CardNo live_hand[16];
    Snap play_start, consume;
    int32_t consume_valid;
    Snap play_card, pending_card;
    int32_t unsup;
    int32_t unsup_info;
    uint64_t sce_mask;
    int32_t n_pa_sids;
    int32_t pa_sids[16];
    int32_t pa_scanned, pa_hold;
    int32_t sy_playing_top;
    uint64_t sy_removed;
    uint64_t lost_by_enchant;
    BindLog *blog;
    const OpenDesc *open;
    int64_t greedy_choices;
    int (*select_cb)(void *core, int32_t effect, int64_t pick_min, int64_t pick_max, const int32_t *cand,
                     int32_t n_cand, int64_t *picked);
    int64_t effect_draws;
    int32_t predict;
    int32_t forecast_serial;
    int32_t tes_serial[GKMS_MAX_TES];
    int32_t timer_serial[GKMS_MAX_SMALL][GKMS_MAX_TIMER_IDS];
};

// the source of what runs now (the trace's kind and source; the split's sources, api 6)
GK_BIND void bind_source(Core *c, int32_t *kind, int32_t *source) {
    const ExamStateP *S = &c->g.st;
    if (c->running_tes >= 0) { *kind = 1; *source = S->tes[c->running_tes].enchant_id; }
    else if (c->blog->trace_mode) { *kind = c->blog->trace_mode; *source = -1; }
    else if (S->playing_card >= 0) { *kind = 4; *source = c->g.cards[S->playing_card].produce_card_id; }
    else { *kind = 0; *source = -1; }
}
GK_BIND void bind_split(Core *c, int64_t adding) {
    BindLog *b = c->blog;
    if (!b->split_on) return;
    if (c->indirect_depth) b->split_indirect += adding; else b->split_direct += adding;
    if (!adding) return;
    int32_t kind, source, settled = b->te_valid ? 1 : 0;
    bind_source(c, &kind, &source);
    for (int i = 0; i < b->ss_n; i++) {
        BindSource *s = &b->ss[i];
        if (s->kind == kind && s->source == source && s->settled == settled) { s->value += adding; return; }
    }
    if (b->ss_n >= GK_SPLIT_SOURCES) { b->ss_rest += adding; return; }
    BindSource *s = &b->ss[b->ss_n++];
    s->kind = kind; s->source = source; s->settled = settled; s->pad = 0; s->value = adding;
}
GK_BIND void bind_trace_row(Core *c, int32_t effect, int32_t type, int64_t v1) {
    BindLog *b = c->blog;
    if (!b->trace) return;
    if (b->trace_n >= b->trace_cap) { b->trace_overflow = 1; return; }
    BindTrace *t = &b->trace[b->trace_n++];
    const ExamStateP *S = &c->g.st;
    t->turn = (int32_t)S->turn; t->effect = effect; t->type = type; t->v1 = v1;
    t->indirect = c->indirect_depth > 0;
    bind_source(c, &t->kind, &t->source);
}
GK_BIND void bind_trace(Core *c, const Eff *e) { bind_trace_row(c, e->id, e->type, e->has_v1 ? e->v1 : NONE); }
GK_BIND int bind_trace_mode(Core *c, int mode) {
    BindLog *b = c->blog;
    int outer = b->trace_mode;
    if (mode >= 0) b->trace_mode = mode;
    return outer;
}
GK_BIND void bind_event(Core *c, int kind, int32_t ref, int32_t upgrade) {
    BindLog *b = c->blog;
    if (b->n_events >= GK_BIND_EVENTS) { b->overflow = 1; return; }
    BindEvent *e = &b->events[b->n_events++];
    const ExamStateP *S = &c->g.st;
    e->kind = kind; e->ref = ref; e->upgrade = upgrade; e->stance = S->idol_status_type;
    e->turn = c->g.s_turn; e->score = S->score; e->stamina = S->stamina; e->block = S->block; e->fpp = S->full_power_point;
}

#define UNSUP_RET(c, info, ret) do { if (!(c)->unsup) { (c)->unsup = __LINE__; (c)->unsup_info = (int32_t)(info); } return ret; } while (0)
#define UNSUP(c, info) do { if (!(c)->unsup) { (c)->unsup = __LINE__; (c)->unsup_info = (int32_t)(info); } } while (0)

// --------------------------------------------------------------------------- copying a Core.
struct CoreSpan { size_t a, b; };
GK_FN int core_clamp(int32_t n, int cap) { return n < 0 ? 0 : n > cap ? cap : (int)n; }
GK_FN void core_span(CoreSpan *t, int *k, const void *base, const void *from, const void *to) {
    size_t a = (size_t)((const char *)from - (const char *)base), b = (size_t)((const char *)to - (const char *)base);
    if (b > a) { t[*k].a = a; t[*k].b = b; (*k)++; }
}
// the unused tails of ``s`` in ascending order (offsets from s); returns their count.
#define CORE_MAX_SPANS (2 * GKMS_MAX_CARDS + 40)
GK_FN int core_tails(const Core *s, CoreSpan *t, int hash) {
    const GkmsState *g = &s->g;
    const ExamStateP *S = &g->st;
    int k = 0, nc = core_clamp(g->n_cards, GKMS_MAX_CARDS);
    int ng = core_clamp(g->d_n_grow_registry, GKMS_MAX_CARDS);
    for (int i = 0; i < ng; i++) {
        const GrowRegistry *r = &g->d_grow_registry[i];
        core_span(t, &k, s, &r->ids[core_clamp(r->n, GKMS_MAX_GROWS)], &r->ids[GKMS_MAX_GROWS]);
    }
    core_span(t, &k, s, &g->d_grow_registry[ng], &g->d_grow_registry[GKMS_MAX_CARDS]);
    for (int i = 0; i < nc; i++) {
        const Card *cd = &g->cards[i];
        core_span(t, &k, s, &cd->grows[core_clamp(cd->n_grows, GKMS_MAX_GROWS)], &cd->grows[GKMS_MAX_GROWS]);
    }
    core_span(t, &k, s, &g->cards[nc], &g->cards[GKMS_MAX_CARDS]);
    if (hash) {
        core_span(t, &k, s, &S->card_enchant_specs[core_clamp(S->n_card_enchant_specs, GKMS_MAX_ENCHANTS)],
                  &S->card_enchant_specs[GKMS_MAX_ENCHANTS]);
        int ntg = core_clamp(S->timed_gain_layers.n, GKMS_MAX_SMALL);
        core_span(t, &k, s, &S->timed_gain_layers.expire[ntg], &S->timed_gain_layers.expire[GKMS_MAX_SMALL]);
        core_span(t, &k, s, &S->timed_gain_layers.value[ntg], &S->timed_gain_layers.value[GKMS_MAX_SMALL]);
        core_span(t, &k, s, &S->timed_gain_layers.fresh[ntg], &S->timed_gain_layers.fresh[GKMS_MAX_SMALL]);
    }
    const TupleList *L[14] = {
        &S->lesson_buff_multiple_layers, &S->lesson_parameter_multiple_layers, &S->review_multiple_layers,
        &S->stamina_consumption_add_fix_layers, &S->card_draw_down_layers, &S->lesson_debuff_layers,
        &S->lesson_parameter_multiple_down_layers, &S->enthusiastic_additive_layers,
        &S->full_power_lesson_multiple_additive_layers, &S->full_power_point_additive_layers, &S->play_card_limits,
        &S->review_count_add_layers,
        &S->concentration_lesson_multiple_additive_layers,
        &S->enthusiastic_kept};
    for (int i = 0; i < 14; i++) {
        int n = core_clamp(L[i]->n, GKMS_MAX_LAYERS);
        if (hash) core_span(t, &k, s, &L[i]->len[n], &L[i]->len[GKMS_MAX_LAYERS]);
        core_span(t, &k, s, &L[i]->v[n], &L[i]->v[GKMS_MAX_LAYERS]);
    }
    core_span(t, &k, s, &S->tes[core_clamp(S->n_tes, GKMS_MAX_TES)], &S->tes[GKMS_MAX_TES]);
    core_span(t, &k, s, &s->resolved_keys[nc], &s->resolved_keys[GKMS_MAX_CARDS]);
    core_span(t, &k, s, &s->res[nc], &s->res[GKMS_MAX_CARDS]);
    return k;
}
#if defined(__CUDACC__) || defined(__CUDACC_RTC__)
GK_FN void gk_copy_bytes(char *d, const char *s, size_t n) {
    if ((((size_t)d ^ (size_t)s) & 15) == 0) {
        while (n && ((size_t)d & 15)) { *d++ = *s++; n--; }
        struct alignas(16) W16 { uint64_t a, b; };
        W16 *dw = (W16 *)d; const W16 *sw = (const W16 *)s;
        for (size_t i = 0, m = n >> 4; i < m; i++) dw[i] = sw[i];
        d += n & ~(size_t)15; s += n & ~(size_t)15; n &= 15;
    }
    while (n--) *d++ = *s++;
}
#else
GK_FN void gk_copy_bytes(char *d, const char *s, size_t n) { memcpy(d, s, n); }
#endif
// the copy given the tails of ``s`` (core_tails(s, t, 0)); the GPU kernel lists the root's tails once per block
// instead of once per rollout
GK_FN void core_copy_spans(Core *d, const Core *s, const CoreSpan *t, int k) {
    size_t at = 0;
    for (int i = 0; i < k; i++) {
        if (t[i].a > at) gk_copy_bytes((char *)d + at, (const char *)s + at, t[i].a - at);
        at = t[i].b;
    }
    if (sizeof(Core) > at) gk_copy_bytes((char *)d + at, (const char *)s + at, sizeof(Core) - at);
#ifdef GK_POISON_TAILS
    for (int i = 0; i < k; i++) memset((char *)d + t[i].a, GK_POISON_TAILS, t[i].b - t[i].a);
#endif
}
GK_FN void core_copy(Core *d, const Core *s) {
    GK_COUNT(core_copies, 1);
    CoreSpan t[CORE_MAX_SPANS];
    int k = core_tails(s, t, 0);
    core_copy_spans(d, s, t, k);
}

// ---------------------------------------------------------------------------
// float32 emulation (exam.py f32 helpers)
// ---------------------------------------------------------------------------
GK_FN double f32(double v) { return (double)(float)v; }
GK_FN double f32_mul(double a, double b) { return f32(f32(a) * f32(b)); }
GK_FN double f32_div(double a, double b) { return f32(f32(a) / f32(b)); }
GK_FN int64_t ceil_f32(double v) { return (int64_t)ceil(f32(v)); }
GK_FN int64_t imax(int64_t a, int64_t b) { return a > b ? a : b; }
GK_FN int64_t imin(int64_t a, int64_t b) { return a < b ? a : b; }

// ---------------------------------------------------------------------------
// RNG (rng.py): xorshift32 13/17/5; range = lo + ((state*width)>>32), then advance
// ---------------------------------------------------------------------------
GK_FN uint32_t rng_next(Core *c) {
    uint32_t v = c->g.rng;
    v ^= v << 13; v ^= v >> 17; v ^= v << 5;
    c->g.rng = v;
    return v;
}
GK_FN int64_t rng_range(Core *c, int64_t lo, int64_t hi) {
    uint32_t old = c->g.rng;
    uint32_t width = (uint32_t)((hi - lo) & 0xFFFFFFFFLL);
    int64_t r = lo + (int64_t)(((uint64_t)old * (uint64_t)width) >> 32);
    rng_next(c);
    return r;
}
// deck.py raw_random_key / effects.py _signed_random_key
GK_FN int64_t rng_signed_key(Core *c) {
    uint32_t v = c->g.rng ^ 0x80000000u;
    rng_next(c);
    return v < 0x80000000u ? (int64_t)v : (int64_t)v - 0x100000000LL;
}

// ---------------------------------------------------------------------------
// master access
// ---------------------------------------------------------------------------
#define BY(c, k, sid) ((sid) < 0 ? -1 : (c)->M->by_sid[(k) * (c)->M->n_strings + (sid)])
GK_FN const int64_t *P64(const Core *c, int32_t off) { return c->M->pool64 + off; }
GK_FN const int32_t *P32(const Core *c, int32_t off) { return c->M->pool32 + off; }
GK_FN const TriggerRow *trig_row(const Core *c, int32_t sid) { int32_t i = BY(c, 1, sid); return i < 0 ? 0 : &c->M->triggers[i]; }
GK_FN const EnchantRow *ench_row(const Core *c, int32_t sid) { int32_t i = BY(c, 2, sid); return i < 0 ? 0 : &c->M->enchants[i]; }
GK_FN const SearchRow *search_row(const Core *c, int32_t sid) { int32_t i = BY(c, 3, sid); return i < 0 ? 0 : &c->M->searches[i]; }
GK_FN const CardRow *card_row(const Core *c, int32_t sid, int32_t upg) {
    int32_t i = BY(c, 7, sid);
    GK_COUNT(card_row_calls, 1);
    if (i < 0) return 0;
    for (; i < c->M->n_cards && c->M->cards[i].id == sid; i++) {
        GK_COUNT(card_row_steps, 1);
        if (c->M->cards[i].upg == upg) return &c->M->cards[i];
    }
    return 0;
}
GK_FN const Eff *eff_ptr(const Core *c, int32_t sid) {
    int32_t i = BY(c, 0, sid);
    return i < 0 ? 0 : &c->M->effects[i];
}
GK_FN int eff_load(const Core *c, int32_t sid, Eff *e) {
    const Eff *r = eff_ptr(c, sid);
    if (!r) return 0;
    *e = *r;
    return 1;
}
GK_FN int trig_has_phase(const Core *c, const TriggerRow *t, int64_t phase) {
    GK_COUNT(phase_tests, 1);
    if (c->phase_masks && (uint64_t)phase < 64)
        return (int)((c->phase_masks[t - c->M->triggers] >> phase) & 1);
    GK_COUNT(phase_list_walks, 1);
    const int64_t *p = P64(c, t->phase_off);
    for (int i = 0; i < t->n_phase; i++) if (p[i] == phase) return 1;
    return 0;
}
GK_FN int in_list64(const int64_t *p, int n, int64_t v) { for (int i = 0; i < n; i++) if (p[i] == v) return 1; return 0; }
GK_FN int in_list32(const int32_t *p, int n, int32_t v) { for (int i = 0; i < n; i++) if (p[i] == v) return 1; return 0; }

// ---------------------------------------------------------------------------
// zones (deck.py lists of card instances; a slot index is the identity)
// ---------------------------------------------------------------------------
GK_FN int zlen(const Core *c, int z) { return c->g.zone_n[z]; }
GK_FN int zget(const Core *c, int z, int i) { return c->g.zone[z][i]; }
GK_FN int zpop(Core *c, int z, int i) {
    int s = c->g.zone[z][i];
    for (int j = i; j + 1 < c->g.zone_n[z]; j++) c->g.zone[z][j] = c->g.zone[z][j + 1];
    c->g.zone_n[z]--;
    return s;
}
GK_FN void zappend(Core *c, int z, int s) {
    if (c->g.zone_n[z] >= GKMS_MAX_ZONE) { UNSUP(c, z); return; }
    c->g.zone[z][c->g.zone_n[z]++] = (int16_t)s;
}
GK_FN void zinsert(Core *c, int z, int i, int s) {
    if (c->g.zone_n[z] >= GKMS_MAX_ZONE) { UNSUP(c, z); return; }
    if (i > c->g.zone_n[z]) i = c->g.zone_n[z];
    for (int j = c->g.zone_n[z]; j > i; j--) c->g.zone[z][j] = c->g.zone[z][j - 1];
    c->g.zone[z][i] = (int16_t)s;
    c->g.zone_n[z]++;
}
GK_FN int zfind(const Core *c, int z, int s) {
    for (int i = 0; i < c->g.zone_n[z]; i++) if (c->g.zone[z][i] == s) return i;
    return -1;
}

// ---------------------------------------------------------------------------
// effects.py resolve_card_customizes + deck.py refresh_card
// ---------------------------------------------------------------------------
GK_FN void res_add(int32_t *n, int32_t *t, int64_t *v, int32_t key, int64_t add, Core *c) {
    for (int i = 0; i < *n; i++) if (t[i] == key) { v[i] += add; return; }
    if (*n >= 8) { UNSUP(c, key); return; }
    t[*n] = key; v[*n] = add; (*n)++;
}
GK_FN int64_t res_get(int32_t n, const int32_t *t, const int64_t *v, int32_t key) {
    for (int i = 0; i < n; i++) if (t[i] == key) return v[i];
    return 0;
}

// One grow of a card's resolve: customizes' grows, then the card's own, in order (resolve_card); also the step
// append_card_grows takes on a card whose resolve is current.
GK_FN int resolve_grow(Core *c, const CardRow *row, Resolved *r, int32_t gid) {
    int32_t gidx = BY(c, 6, gid);
    if (gidx < 0) { UNSUP(c, gid); return 0; }
    const GrowRow *g = &c->M->grows[gidx];
    int t = g->type, sign = 0, tgt;
    if (t == 1) r->lesson_add += g->value;
    else if (t == 12) r->cost_reduce += g->value;
    else if (t == 49) r->cost_fpp_add += g->value;
    else if (t == 3) r->lesson_count_add += g->value;
    else if (t == 13) r->cost_add += g->value;
    else if (t == 32) {
        if (r->n_play >= MAX_PLAY) { UNSUP(c, t); return 0; }
        Link *L = &r->play[r->n_play++];
        L->effect = g->play_effect; L->trigger = -1; L->once = 0; L->v1 = 0;
        for (int i = 0; i < g->n_eg; i++) {
            int32_t eg = P32(c, g->eg_off)[i];
            if (in_list32(P32(c, row->eg_off), row->n_eg, eg) || in_list32(r->xeg, r->n_xeg, eg)) continue;
            if (r->n_xeg >= 8) { UNSUP(c, t); return 0; }
            r->xeg[r->n_xeg++] = eg;
        }
    }
    else if ((tgt = grow_to_effect_type(t))) res_add(&r->n_va, r->va_t, r->va_v, tgt, g->value, c);
    else if ((tgt = grow_to_effect_turn(t))) res_add(&r->n_ta, r->ta_t, r->ta_v, tgt, g->value, c);
    else if (t == 48) r->cost_fpp_reduce += g->value;
    else if ((tgt = grow_to_status_cost(t, &sign))) r->scd[tgt] += sign * g->value;
    else if (t == 14) r->cost_pen_reduce += g->value;
    else if (t == 15) r->cost_pen_reduce -= g->value;
    else if (t == 39) r->is_initial = 1;
    else if (t == 34) {
        const int32_t *tg = P32(c, g->tgt_off);
        for (int i = 0; i < r->n_play; i++)
            if (in_list32(tg, g->n_tgt, r->play[i].effect)) {
                r->play[i].effect = g->play_effect; r->play[i].trigger = -1; r->play[i].once = 0; r->play[i].v1 = 0;
            }
        for (int i = 0; i < g->n_eg; i++) {
            int32_t eg = P32(c, g->eg_off)[i];
            if (in_list32(P32(c, row->eg_off), row->n_eg, eg) || in_list32(r->xeg, r->n_xeg, eg)) continue;
            if (r->n_xeg >= 8) { UNSUP(c, t); return 0; }
            r->xeg[r->n_xeg++] = eg;
        }
    }
    else if (t == 38) r->move_pos = g->move_pos;
    else if (t == 35) r->status_enchant = g->status_enchant;
    else if (t == 36) {
        if (in_list32(P32(c, g->ttgt_off), g->n_ttgt, r->play_trigger)) r->play_trigger = g->play_trigger;
    }
    else if (t == 37) {
        const int32_t *tg = P32(c, g->ttgt_off);
        for (int i = 0; i < r->n_play; i++)
            if (in_list32(tg, g->n_ttgt, r->play[i].trigger)) r->play[i].trigger = g->play_effect_trigger;
    }
    else { UNSUP(c, t); return 0; }
    return 1;
}
GK_FN void resolve_card(Core *c, int slot, const CardRow *row, Resolved *r) {
    const Card *cd = &c->g.cards[slot];
    memset(r, 0, sizeof(*r));
    r->row = (int32_t)(row - c->M->cards);
    r->n_play = 0;
    for (int i = 0; i < row->n_link; i++) {
        const LinkRow *L = &c->M->links[row->link_off + i];
        if (r->n_play >= MAX_PLAY) { UNSUP(c, r->n_play); return; }
        r->play[r->n_play].effect = L->effect; r->play[r->n_play].trigger = L->trigger;
        r->play[r->n_play].once = L->once; r->play[r->n_play].v1 = L->v1;
        r->n_play++;
    }
    r->move_pos = row->move_pos;
    r->play_trigger = row->play_trigger;
    r->status_enchant = row->status_enchant;
    r->is_initial = row->is_initial;
    int32_t grows[32]; int ng = 0;
    if (cd->has_customizes) {
        for (int k = 0; k < cd->n_customizes; k++) {
            int32_t cid = cd->customize_id[k], cnt = cd->customize_count[k];
            const CustRow *cr = 0;
            for (int i = 0; i < c->M->n_custs; i++) {
                GK_COUNT(cust_rows, 1);
                if (c->M->custs[i].id == cid && c->M->custs[i].count == cnt) { cr = &c->M->custs[i]; break; }
            }
            if (!cr) { UNSUP(c, cid); return; }
            if (cr->card >= 0 && cr->card == cd->produce_card_id && cr->upg != cd->current_upgrade_count) {
                for (int i = 0; i < c->M->n_custs; i++) {
                    const CustRow *x = &c->M->custs[i];
                    if (x->card == cr->card && x->upg == cd->current_upgrade_count && x->idx == cr->idx && x->count == cnt) {
                        cr = x; break;
                    }
                }
            }
            for (int j = 0; j < cr->n_grow; j++) {
                if (ng >= 32) { UNSUP(c, ng); return; }
                grows[ng++] = P32(c, cr->grow_off)[j];
            }
        }
    }
    int nall = ng + cd->n_grows;
    GK_COUNT(resolve_grows, nall);
    for (int gi = 0; gi < nall; gi++)
        if (!resolve_grow(c, row, r, gi < ng ? grows[gi] : cd->grows[gi - ng])) return;
}

// the cache miss of refresh_card: resolve and remember the key (it holds resolve_card's grow list)
GK_OUTLINE void refresh_resolve(Core *c, int slot, const CardRow *row, int rid) {
    const Card *cd = &c->g.cards[slot];
    ResolveKey *key = &c->resolved_keys[slot];
    resolve_card(c, slot, row, &c->res[slot]);
    if (!c->unsup) {
        key->row=rid;key->has=cd->has_customizes;key->n=cd->n_customizes;
        key->card=cd->produce_card_id;key->upg=cd->current_upgrade_count;
        if (cd->has_customizes) for(int j=0;j<cd->n_customizes;j++) {
            key->ids[j]=cd->customize_id[j];key->counts[j]=cd->customize_count[j];
        }
        c->resolved_valid |= uint64_t(1) << slot;
    }
}
// deck.py SegmentDeck.refresh_card refresh_card's cache test without its writes -- is the slot's resolve the one of
// the card as it stands?
GK_FN int resolve_is_current(const Core *c, int slot) {
    const Card *cd = &c->g.cards[slot];
    const ResolveKey *key = &c->resolved_keys[slot];
    if (!((c->resolved_valid >> slot) & 1)) return 0;
    const int32_t upg = cd->base_upgrade_count + cd->temporary_upgrade_count + cd->n_support_upgrade_ids;
    if (key->card != cd->produce_card_id || key->upg != upg || key->has != cd->has_customizes) return 0;
    if (cd->has_customizes) {
        if (key->n != cd->n_customizes) return 0;
        for (int j = 0; j < cd->n_customizes; j++)
            if (key->ids[j] != cd->customize_id[j] || key->counts[j] != cd->customize_count[j]) return 0;
    }
    return 1;
}
GK_FN Resolved *refresh_card(Core *c, int slot) {
    Card *cd = &c->g.cards[slot];
    cd->current_upgrade_count = cd->base_upgrade_count + cd->temporary_upgrade_count + cd->n_support_upgrade_ids;
    ResolveKey *key = &c->resolved_keys[slot];
    int hit = (c->resolved_valid >> slot) & 1;
    if (hit && (key->card != cd->produce_card_id || key->upg != cd->current_upgrade_count
                || key->has != cd->has_customizes)) hit = 0;
    if (hit && cd->has_customizes) {
        if (key->n != cd->n_customizes) hit = 0;
        for (int j=0;hit && j<cd->n_customizes;j++)
            if (key->ids[j]!=cd->customize_id[j] || key->counts[j]!=cd->customize_count[j]) hit=0;
    }
    GK_COUNT(refresh_calls, 1);
    if (!hit) {
        const CardRow *row = card_row(c, cd->produce_card_id, cd->current_upgrade_count);
        if (!row) {
            int best = -1;
            for (int u = 0; u <= cd->current_upgrade_count; u++) if (card_row(c, cd->produce_card_id, u)) best = u;
            if (best < 0) { UNSUP(c, cd->produce_card_id); return &c->res[slot]; }
            row = card_row(c, cd->produce_card_id, best);
        }
        GK_COUNT(resolve_misses, 1);
        refresh_resolve(c, slot, row, (int)(row - c->M->cards));
    }
    Resolved *r = &c->res[slot];
    cd->play_move_position_type = r->move_pos;
    if (r->status_enchant >= 0) { cd->has_card_status_enchant = 1; cd->card_status_enchant = r->status_enchant; }
    cd->play_trigger_id = r->play_trigger;
    return r;
}
GK_FN const CardRow *rrow(const Core *c, const Resolved *r) { return &c->M->cards[r->row]; }
GK_FN void resolved_copy(Resolved *d, const Resolved *s) {
    d->row = s->row; d->n_play = s->n_play;
    for (int i = 0; i < s->n_play; i++) d->play[i] = s->play[i];
    d->move_pos = s->move_pos; d->play_trigger = s->play_trigger; d->status_enchant = s->status_enchant;
    d->is_initial = s->is_initial;
    d->lesson_add = s->lesson_add; d->cost_reduce = s->cost_reduce; d->cost_fpp_add = s->cost_fpp_add;
    d->cost_add = s->cost_add; d->lesson_count_add = s->lesson_count_add; d->cost_fpp_reduce = s->cost_fpp_reduce;
    d->cost_pen_reduce = s->cost_pen_reduce;
    d->n_va = s->n_va; for (int i = 0; i < s->n_va; i++) { d->va_t[i] = s->va_t[i]; d->va_v[i] = s->va_v[i]; }
    d->n_ta = s->n_ta; for (int i = 0; i < s->n_ta; i++) { d->ta_t[i] = s->ta_t[i]; d->ta_v[i] = s->ta_v[i]; }
    for (int i = 0; i < 8; i++) d->scd[i] = s->scd[i];
    d->n_xeg = s->n_xeg; for (int i = 0; i < s->n_xeg; i++) d->xeg[i] = s->xeg[i];
}
// a card's effect groups are its master row's plus those its EFFECT_ADD grows add (effects.py resolve_card_customizes
// -> resolved["effectGroupIds"])
GK_FN int card_has_eg(const Core *c, const Resolved *r, int32_t eg) {
    const CardRow *row = rrow(c, r);
    return in_list32(P32(c, row->eg_off), row->n_eg, eg) || in_list32(r->xeg, r->n_xeg, eg);
}

// the one reading of a SearchRow against a card (neu/search_predicate.py card_matches); was four copies
// (matches_search, card_matches_trigger_search, the play-count buff's category, can_play's effect 113).
GK_FN int sy_group(const Core *c, int slot) {
    const Card *cd = &c->g.cards[slot];
    if (cd->p4_group > 0) return cd->p4_group;
    const CardRow *row = card_row(c, cd->produce_card_id, cd->current_upgrade_count);
    if (!row) row = card_row(c, cd->produce_card_id, 0);
    return row ? row->p4_group : 0;
}
GK_FN int sy_top(const Core *c, int slot) { int t = c->g.cards[slot].fusion_top1; return t > 0 ? t - 1 : slot; }
GK_FN int sy_count(const Core *c, int top) {
    int n = 1;
    for (int i = 0; i < c->g.zone_n[Z_FUSED]; i++) if (c->g.cards[c->g.zone[Z_FUSED][i]].fusion_top1 == top + 1) n++;
    return n;
}
// a ProduceCardSearch's シンクロ fields against a card instance: plan4GroupTypes, isFusionTopOnly, fusionCountMin / Max
// (the size of the card's bundle; 1 outside one).
GK_FN int sy_match(const Core *c, const SearchRow *s, int slot) {
    if (s->p4_mask && !((s->p4_mask >> sy_group(c, slot)) & 1)) return 0;
    if (s->fusion_top_only && c->g.cards[slot].fusion_top1 > 0) return 0;
    if (s->has_fusion_min || s->has_fusion_max) {
        int64_t n = sy_count(c, sy_top(c, slot));
        if (s->has_fusion_min && n < s->fusion_min) return 0;
        if (s->has_fusion_max && n > s->fusion_max) return 0;
    }
    return 1;
}

GK_FN int search_match(const Core *c, const SearchRow *s, const Resolved *r, int32_t card_id, int64_t upgrade,
                       int any_group, int slot = -1) {
    const CardRow *row = rrow(c, r);
    if (s->sy) {
        if (slot >= 0) { if (!sy_match(c, s, slot)) return 0; }
        else if (s->p4_mask && !((s->p4_mask >> row->p4_group) & 1)) return 0;
    }
    if (s->n_card && !in_list32(P32(c, s->card_off), s->n_card, card_id)) return 0;
    if (s->n_cat && !in_list64(P64(c, s->cat_off), s->n_cat, row->category)) return 0;
    if (s->n_rar && !in_list64(P64(c, s->rar_off), s->n_rar, row->rarity)) return 0;
    if (s->plan >= 0 && row->plan != s->plan) return 0;
    if (s->n_eg) {
        int hits = 0;
        for (int i = 0; i < s->n_eg; i++) hits += card_has_eg(c, r, P32(c, s->eg_off)[i]);
        if (any_group ? hits == 0 : hits < s->n_eg) return 0;
    }
    if (s->tag >= 0 && row->tag != s->tag) return 0;
    if (upgrade >= 0 && s->n_upg && !in_list64(P64(c, s->upg_off), s->n_upg, upgrade)) return 0;
    if (s->has_stmin || s->has_stmax) {
        int64_t stamina = (row->has_force && row->force) ? row->force : (row->has_stamina ? row->stamina : 0);
        if (s->has_stmin && stamina < s->stmin) return 0;
        if (s->has_stmax && stamina > s->stmax) return 0;
    }
    return 1;
}

GK_FN void card_enchant_on_stance_change(Core *c, int new_type, int direct);
GK_FN void leisurely_to_full_power_grow(Core *c);
