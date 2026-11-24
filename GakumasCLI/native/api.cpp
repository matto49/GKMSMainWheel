// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- C ABI (host side).
#include <stdlib.h>
#include <algorithm>
#include <mutex>
#include <vector>
#include "gkms_master.h"
// every trigger's phases below 64 as one mask, by trigger row -- trig_has_phase tests a bit where it walked the row's
// phase list (3.4% of a search's instructions on a 全力 本戦 problem, callgrind).
static const uint64_t *gk_phase_masks(const MasterDesc *M) {
    static std::mutex mu;
    static std::vector<std::vector<uint64_t> *> kept;
    if (!M || M->n_triggers <= 0) return 0;
    std::vector<uint64_t> masks((size_t)M->n_triggers, 0);
    for (int i = 0; i < M->n_triggers; i++) {
        const TriggerRow *t = &M->triggers[i];
        const int64_t *p = M->pool64 + t->phase_off;
        for (int j = 0; j < t->n_phase; j++) if ((uint64_t)p[j] < 64) masks[(size_t)i] |= uint64_t(1) << p[j];
    }
    std::lock_guard<std::mutex> lock(mu);
    for (std::vector<uint64_t> *one : kept) if (*one == masks) return one->data();
    kept.push_back(new std::vector<uint64_t>(std::move(masks)));
    return kept.back()->data();
}
#define GK_PHASE_MASKS(M) gk_phase_masks(M)
#include "core_rollout.h"

#ifdef _WIN32
#define GK_API __declspec(dllexport)
#else
#define GK_API __attribute__((visibility("default")))
#endif

extern "C" {
// gk_exam_rules_version: the version of the exam rules this core implements.
GK_API int gk_exam_rules_version(void) { return 69; }
GK_API int gk_state_size(void) { return (int)sizeof(GkmsState); }
// the native session binding (native/native_session.py); gk_api_version rises with every change to these calls.
GK_API int gk_api_version(void) { return 6; }
GK_API const char *gk_notice(void) { return "Copyright (C) 2026 花吹雪また一年P d9fcd586c1e97ee7b0054839de6574b5"; }
GK_API int gk_bind_log_size(void) { return (int)sizeof(BindLog); }
GK_API int gk_debuff_count(void *core) { return active_debuff_count((Core *)core); }
GK_API void gk_set_bind_log(void *core, BindLog *log) { ((Core *)core)->blog = log; }
// exam_session.py play / use_drink / end_turn / start_turn on their own (preview_action forks; step is gk_step).
GK_API int gk_play(void *core, int index) {
    Core *c = (Core *)core;
    if (c->g.s_finished) return 2;
    if (index < 0 || index >= zlen(c, Z_HAND) || !can_play(c, zget(c, Z_HAND, index))) return c->unsup ? 1 : 2;
    session_play(c, index);
    return c->unsup ? 1 : 0;
}
GK_API int gk_use_drink(void *core, int index) {
    Core *c = (Core *)core;
    if (c->g.s_finished || index < 0 || index >= c->g.n_drinks) return 2;
    session_use_drink(c, index);
    return c->unsup ? 1 : 0;
}
GK_API int gk_end_turn(void *core, int explicit_) {
    Core *c = (Core *)core;
    if (c->g.s_finished) return 2;
    session_end_turn(c, explicit_);
    return c->unsup ? 1 : 0;
}
// the exam opening -- the state imported at turn 0 (the loading only, exam_session.deferred_open), then
// exam_session.py ExamSession.start_turn for turn 1 in the core.
GK_API int gk_open(void *core, const OpenDesc *d) {
    Core *c = (Core *)core;
    if (c->g.d_initialized || c->g.s_turn != 0) return 2;
    c->open = d;
    session_start_turn(c);
    c->open = 0;
    return c->unsup ? 1 : 0;
}
GK_API int gk_open_desc_size(void) { return (int)sizeof(OpenDesc); }
GK_API int gk_new_card(void *core, int32_t produce_card_id, int64_t upgrade) {
    Core *c = (Core *)core;
    c->unsup = 0;
    int s = new_card(c, produce_card_id, upgrade);
    return c->unsup ? -1 : s;
}
// 0, or 1 when n is out of range.
GK_API int gk_shuffle(uint32_t *rng, int32_t *items, int n) {
    if (n < 0 || n > 4096) return 1;
    uint32_t v = *rng;
    for (int size = n; size > 1; size--) {
        int idx = (int)(((uint64_t)v * (uint64_t)(uint32_t)size) >> 32);
        v ^= v << 13; v ^= v >> 17; v ^= v << 5;
        int32_t tmp = items[size - 1]; items[size - 1] = items[idx]; items[idx] = tmp;
    }
    *rng = v;
    return 0;
}
GK_API int gk_start_turn(void *core) {
    Core *c = (Core *)core;
    if (c->g.s_finished) return 2;
    session_start_turn(c);
    return c->unsup ? 1 : 0;
}
// the select queue (state.select_index_queue) as the binding keeps it: n rows of len[i] indexes each
GK_API int gk_select_queue(const void *core, int32_t *lens, int64_t *values) {
    const SelectQueue *q = &((const Core *)core)->g.st.select_index_queue;
    for (int i = 0; i < q->n; i++) {
        lens[i] = q->len[i];
        for (int j = 0; j < q->len[i]; j++) values[i * GKMS_MAX_SMALL + j] = q->v[i][j];
    }
    return q->n;
}
GK_API int gk_set_select_queue(void *core, int n, const int32_t *lens, const int64_t *values) {
    SelectQueue *q = &((Core *)core)->g.st.select_index_queue;
    if (n < 0 || n > GKMS_MAX_SMALL) return 1;
    for (int i = 0; i < n; i++) if (lens[i] < 0 || lens[i] > GKMS_MAX_SMALL) return 1;
    memset(q, 0, sizeof *q);
    q->n = n;
    for (int i = 0; i < n; i++) {
        q->len[i] = lens[i];
        for (int j = 0; j < lens[i]; j++) q->v[i][j] = values[i * GKMS_MAX_SMALL + j];
    }
    return 0;
}
// one card of a pile as exam_session.py observe and playability_reason see it: out = [cost type number, cost value
// (observe _costs), stamina cost (observe _costs), playable, reason, need, available, slot].
GK_API int gk_card_info(void *core, int zone, int index, int64_t *out) {
    Core *c = (Core *)core;
    if (zone < 0 || zone >= 5 || index < 0 || index >= zlen(c, zone)) return 1;
    int slot = zget(c, zone, index);
    int32_t prev_unsup = c->unsup, prev_info = c->unsup_info;
    const Resolved *r = refresh_card(c, slot);
    const CardRow *row = rrow(c, r);
    ExamStateP *S = ST(c);
    int ct = row->cost_type;
    int64_t value = (row->has_cost_value ? row->cost_value : 0) + (ct > 0 && ct < 8 ? r->scd[ct] : 0);
    if (ct == 5) value += r->cost_fpp_add - r->cost_fpp_reduce;
    int64_t cost_value = ct ? calc_buff_cost(c, imax(0, value)) : value;
    int64_t force = row->has_force ? row->force : 0;
    int64_t stamina = force ? imax(0, force - r->cost_pen_reduce) : (row->has_stamina ? row->stamina : 0);
    out[0] = ct; out[1] = imax(0, cost_value); out[2] = stamina; out[7] = slot;
    out[5] = 0; out[6] = 0;
    int reason = 0;
    if (S->playable <= 0) reason = 1;
    else if (!can_play(c, slot)) {
        reason = 3;
        for (int i = 0; i < S->play_card_limits.n; i++) {
            if (!timed_layer_live(c, &S->play_card_limits, i, S->turn, 113)) continue;
            const SearchRow *sr = search_row(c, (int32_t)S->play_card_limits.v[i][0].i);
            if (sr && search_match(c, sr, r, c->g.cards[slot].produce_card_id, -1, 0)) { reason = 2; break; }
        }
        if (reason == 3 && !(r->play_trigger >= 0 && trig_row(c, r->play_trigger) && trig_row(c, r->play_trigger)->n_fst
                             && !field_statuses_hold(c, trig_row(c, r->play_trigger)))) {
            int64_t need = calc_buff_cost(c, imax(0, value));
            int64_t avail = ct == 1 ? S->lesson_buff : ct == 2 ? S->review : ct == 3 ? S->aggressive
                          : ct == 4 ? S->parameter_buff_remaining : ct == 6 ? pbm_turns(c) : ct == 5 ? S->full_power_point : 0;
            if (ct >= 1 && ct <= 6) { reason = ct == 5 ? 4 : ct == 1 ? 5 : ct == 2 ? 6 : ct == 3 ? 7 : ct == 4 ? 8 : 9;
                                      out[5] = imax(0, need); out[6] = avail; }
            else {
                int pen = force > 0;
                int64_t base;
                if (c->g.cards[slot].has_panic_stamina_fix) base = c->g.cards[slot].panic_stamina_fix;
                else if (pen) base = imax(0, force - r->cost_pen_reduce);
                else base = imax(0, (row->has_stamina ? row->stamina : 0) - r->cost_reduce + r->cost_add);
                int64_t sd, bd;
                calculate_damage(c, base, pen, 1, &sd, &bd);
                int64_t total = sd + bd;
                if (S->idol_status_type == GK_STANCE_CONCENTRATION && S->idol_status_step_count >= 2 && total > 0)
                    total -= c->M->es_conc_penetrate_reduce2;
                reason = pen ? 10 : 11; out[5] = total; out[6] = S->stamina;
            }
        }
    }
    out[3] = reason == 0; out[4] = reason;
    int failed = c->unsup && !prev_unsup;
    c->unsup = prev_unsup; c->unsup_info = prev_info;
    return failed ? 2 : 0;
}

GK_API void gk_set_select_callback(void *core, int (*cb)(void *, int32_t, int64_t, int64_t, const int32_t *, int32_t,
                                                         int64_t *)) { ((Core *)core)->select_cb = cb; }
GK_API int64_t gk_effect_draws(const void *core) { return ((const Core *)core)->effect_draws; }
struct ForecastStatus { int32_t serial, slot, first, count; };
GK_API int gk_forecast_statuses(const void *core, ForecastStatus *out, int capacity) {
    const Core *c = (const Core *)core;
    const ExamStateP *s = &c->g.st;
    const TimerQueue *q = &s->effect_timer_queue;
    int n = 0;
    for (int i = 0; i < s->n_tes; i++) {
        if (n >= capacity) return -1;
        out[n++] = {c->tes_serial[i], i, 0, 0};
    }
    for (int i = 0; i < q->n; i++) {
        for (int first = 0; first < q->nids[i];) {
            int serial = c->timer_serial[i][first], end = first + 1;
            while (serial && end < q->nids[i] && c->timer_serial[i][end] == serial) end++;
            if (n >= capacity) return -1;
            out[n++] = {serial, ~i, first, end - first};
            first = end;
        }
    }
    if (n > 1) std::sort(out, out + n, [q](const ForecastStatus &a, const ForecastStatus &b) {
        if (a.serial != b.serial) return a.serial < b.serial;
        if (a.slot >= 0 || b.slot >= 0) {
            if ((a.slot >= 0) != (b.slot >= 0)) return a.slot >= 0;
            return a.slot < b.slot;
        }
        if (a.slot != b.slot) return q->turn[~a.slot] < q->turn[~b.slot];
        return a.first < b.first;
    });
    return n;
}
GK_API int gk_apply_effect(void *core, int32_t effect, int64_t permil) {
    Core *c = (Core *)core;
    const Eff *e = eff_ptr(c, effect);
    if (!e) return -1;
    uint64_t sce = c->sce_mask;
    int (*cb)(void *, int32_t, int64_t, int64_t, const int32_t *, int32_t, int64_t *) = c->select_cb;
    memset(c->phase_candidates, 0, sizeof c->phase_candidates);
    c->sce_mask = 0;
    c->select_cb = 0;
    c->predict = 1;
    apply_effect(c, e, permil);
    c->predict = 0;
    c->sce_mask = sce;
    c->select_cb = cb;
    rebuild_tes_index(c);
    return c->unsup;
}
GK_API int gk_core_size(void) { return (int)sizeof(Core); }
GK_API int gk_effect_row_size(void) { return (int)sizeof(EffectRow); }
GK_API int gk_trigger_row_size(void) { return (int)sizeof(TriggerRow); }
GK_API int gk_grow_row_size(void) { return (int)sizeof(GrowRow); }
GK_API int gk_master_desc_size(void) { return (int)sizeof(MasterDesc); }
GK_API int gk_round_desc_size(void) { return (int)sizeof(RoundDesc); }
GK_API const char *gk_counter_names(void) {
#define GK_COUNTER_NAME(name) #name "\n"
    return GK_COUNTER_LIST(GK_COUNTER_NAME);
#undef GK_COUNTER_NAME
}
GK_API int gk_counters(uint64_t *out, int cap, int reset) {
#ifdef GK_COUNTERS
    const int n = (int)(sizeof(GkCounters) / sizeof(uint64_t));
    const uint64_t *src = (const uint64_t *)&gk_counter_table;
    for (int i = 0; i < n && i < cap; i++) out[i] = src[i];
    if (reset) memset(&gk_counter_table, 0, sizeof(gk_counter_table));
    return n;
#else
    (void)out; (void)cap; (void)reset;
    return 0;
#endif
}
GK_API void *gk_new(void) { return calloc(1, sizeof(Core)); }
GK_API void gk_free(void *p) { free(p); }
GK_API void gk_import(void *core, const GkmsState *g, const MasterDesc *M, const RoundDesc *R) {
    core_import((Core *)core, g, M, R);
}
GK_API void gk_export(const void *core, GkmsState *out) { memcpy(out, &((const Core *)core)->g, sizeof(GkmsState)); }
GK_API void gk_copy(void *dst, const void *src) { memcpy(dst, src, sizeof(Core)); }
GK_API int gk_step(void *core, int kind, int index) { return session_step((Core *)core, kind, index); }
GK_API int gk_legal(void *core, int32_t *out) { return legal_actions((Core *)core, out); }
GK_API int gk_unsup(const void *core, int32_t *info) { *info = ((const Core *)core)->unsup_info; return ((const Core *)core)->unsup; }
GK_API int gk_finished(const void *core) { return ((const Core *)core)->g.s_finished; }
GK_API int64_t gk_score(const void *core) { return ((const Core *)core)->g.st.score; }
// search_match on one master card row (no instance, no grows): native_check section (34) plays every search against
// every card row through this and through neu.search_predicate.card_matches.
GK_API int gk_search_match_probe(const void *core, int32_t search_sid, int32_t card_sid, int32_t card_upg,
                                 int64_t upgrade, int any_group) {
    const Core *c = (const Core *)core;
    const SearchRow *s = search_row(c, search_sid);
    const CardRow *row = card_row(c, card_sid, card_upg);
    if (!s || !row) return -1;
    Resolved r;
    memset(&r, 0, sizeof r);
    r.row = (int32_t)(row - c->M->cards);
    return search_match(c, s, &r, card_sid, upgrade, any_group);
}

// throughput: random rollouts from an imported state (bench policy: a non-end action with p = 0.9, core_rollout.h).
GK_API int64_t gk_rollouts2(const void *base, int64_t n_rollouts, uint64_t seed, int64_t *n_unsup, int64_t *score_sum,
                            int32_t *last_line, int32_t *last_info) {
    Core *c = (Core *)malloc(sizeof(Core));
    uint64_t x = seed ? seed : 88172645463325252ull;
    int64_t steps = 0, uns = 0, ss = 0;
    *last_line = 0; *last_info = 0;
    for (int64_t r = 0; r < n_rollouts; r++) {
        int64_t st = 0;
        int rc = rollout_one(c, (const Core *)base, &x, &st);
        steps += st;
        if (rc != 0) { uns++; *last_line = c->unsup; *last_info = c->unsup_info; }
        ss += c->g.st.score;
    }
    free(c);
    *n_unsup = uns; *score_sum = ss;
    return steps;
}
GK_API int64_t gk_rollouts(const void *base, int64_t n_rollouts, uint64_t seed, int64_t *n_unsup, int64_t *score_sum) {
    int32_t l, i;
    return gk_rollouts2(base, n_rollouts, seed, n_unsup, score_sum, &l, &i);
}
// reference for the GPU trace: rollout i uses policy seed seeds[i] alone
GK_API void gk_rollouts_seeded(const void *base, int64_t n, const uint64_t *seeds, int64_t *scores, int64_t *steps,
                               int32_t *codes) {
    Core *c = (Core *)malloc(sizeof(Core));
    for (int64_t i = 0; i < n; i++) {
        uint64_t x = seeds[i] ? seeds[i] : 88172645463325252ull;
        int64_t st = 0;
        codes[i] = rollout_one(c, (const Core *)base, &x, &st);
        steps[i] = st;
        scores[i] = c->g.st.score;
    }
    free(c);
}
}

// ---------------------------------------------------------------------------
// native GNRPA (core_search.h)
// ---------------------------------------------------------------------------
#include "core_search.h"
#include <chrono>
static double host_now(void) {
    using namespace std::chrono;
    return duration<double>(steady_clock::now().time_since_epoch()).count();
}
extern "C" {
GK_API int gk_search_result_size(void) { return (int)sizeof(SearchResult); }
GK_API int gk_search_params_size(void) { return (int)sizeof(SearchParams); }
GK_API int gk_prior_table_size(void) { return (int)sizeof(PriorTable); }
GK_API int gk_policy_search(const void *root, const SearchParams *P, const PriorTable *prior, SearchResult *out) {
    Search *s = (Search *)malloc(sizeof(Search));
    if (!s) return -1;
    memset(out, 0, sizeof(SearchResult));
    s->root = (const Core *)root; s->P = P; s->prior = (prior && prior->has_prior) ? prior : 0; s->res = out;
    s->now = host_now; s->started = host_now();
    s->rules = 0;
    policy_search_native(s);
    free(s);
    return out->status;
}
// a timeline's rules (core_rules.h).
GK_API int gk_timeline_version(void) { return 3; }
GK_API int gk_rule_table_size(void) { return (int)sizeof(RuleTable); }
// gk_policy_search with rules; rules NULL or n == 0 is gk_policy_search.
GK_API int gk_policy_search_rules(const void *root, const SearchParams *P, const PriorTable *prior,
                                  const RuleTable *rules, SearchResult *out) {
    Search *s = (Search *)malloc(sizeof(Search));
    if (!s) return -1;
    memset(out, 0, sizeof(SearchResult));
    s->root = (const Core *)root; s->P = P; s->prior = (prior && prior->has_prior) ? prior : 0; s->res = out;
    s->now = host_now; s->started = host_now();
    s->rules = (rules && rules->n > 0) ? rules : 0;
    policy_search_native(s);
    free(s);
    return out->status;
}
}

// ---------------------------------------------------------------------------
// exact tail search (core_tail.h)
// ---------------------------------------------------------------------------
#include "core_tail.h"
#include "core_tail_mt.h"
#include <new>
extern "C" {
GK_API int gk_tail_result_size(void) { return (int)sizeof(TailResult); }
GK_API int gk_tail_params_size(void) { return (int)sizeof(TailParams); }
// A stop belongs to one call.
GK_API void *gk_tail_stop_new(void) { return new (std::nothrow) TailStop(false); }
GK_API void gk_tail_stop_set(void *handle) { static_cast<TailStop *>(handle)->store(true, std::memory_order_relaxed); }
GK_API void gk_tail_stop_free(void *handle) { delete static_cast<TailStop *>(handle); }
GK_API int gk_tail_exact_controlled(const void *root, const TailParams *P, const RuleTable *rules,
                                    TailResult *out, const void *handle) {
    const TailStop *signal = static_cast<const TailStop *>(handle);
    if (tail_cancelled(signal)) {
        memset(out, 0, sizeof(*out));
        out->best_score = -1;
        out->status = 2;
        return out->status;
    }
    if (rules && rules->n > 0) return tail_exact((const Core *)root, P, out, host_now, rules, signal);
    if (P->threads > 1) return tail_exact_mt((const Core *)root, P, out, host_now, P->threads, signal);
    return tail_exact((const Core *)root, P, out, host_now, nullptr, signal);
}
GK_API int gk_tail_exact(const void *root, const TailParams *P, TailResult *out) {
    return gk_tail_exact_controlled(root, P, nullptr, out, nullptr);
}
GK_API int gk_tail_exact_rules(const void *root, const TailParams *P, const RuleTable *rules, TailResult *out) {
    return gk_tail_exact_controlled(root, P, rules, out, nullptr);
}
}

// ---------------------------------------------------------------------------
// the produce forecast (core_produce.h)
// ---------------------------------------------------------------------------
#include "core_produce.h"
extern "C" {
// 1: 選抜 + 本戦 forecast (hif_start.Forecaster.forecast, left-to-right sums)
GK_API int gk_produce_version(void) { return 1; }
GK_API int64_t gk_produce_forecast(const int32_t *I, int64_t ni, const double *D, int64_t nd,
                                   double *out_d, int64_t out_d_cap, int32_t *out_i, int64_t out_i_cap) {
    return gkprod::forecast_call(I, ni, D, nd, out_d, out_i, out_d_cap, out_i_cap);
}
}
