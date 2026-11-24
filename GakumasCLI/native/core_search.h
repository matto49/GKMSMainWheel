// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 6: GNRPA search (scenario/policy_search.py). The policy RNG is Python's random.Random
// (MT19937 + init_by_array + random), so a transition-budgeted native search reproduces the Python search exactly.
#pragma once
#include "core_session.h"
#include "core_rules.h"

// ---------------------------------------------------------------------------
// MT19937 as in CPython Modules/_randommodule.c
// ---------------------------------------------------------------------------
struct MT { uint32_t mt[624]; int mti; };
GK_FN void mt_init_genrand(MT *m, uint32_t s) {
    m->mt[0] = s;
    for (int i = 1; i < 624; i++) m->mt[i] = 1812433253u * (m->mt[i - 1] ^ (m->mt[i - 1] >> 30)) + (uint32_t)i;
    m->mti = 624;
}
GK_FN void mt_init_by_array(MT *m, const uint32_t *key, int klen) {
    mt_init_genrand(m, 19650218u);
    int i = 1, j = 0, k = 624 > klen ? 624 : klen;
    for (; k; k--) {
        m->mt[i] = (m->mt[i] ^ ((m->mt[i - 1] ^ (m->mt[i - 1] >> 30)) * 1664525u)) + key[j] + (uint32_t)j;
        i++; j++;
        if (i >= 624) { m->mt[0] = m->mt[623]; i = 1; }
        if (j >= klen) j = 0;
    }
    for (k = 623; k; k--) {
        m->mt[i] = (m->mt[i] ^ ((m->mt[i - 1] ^ (m->mt[i - 1] >> 30)) * 1566083941u)) - (uint32_t)i;
        i++;
        if (i >= 624) { m->mt[0] = m->mt[623]; i = 1; }
    }
    m->mt[0] = 0x80000000u;
}
GK_FN void mt_seed_int(MT *m, int64_t seed) {
    uint64_t n = seed < 0 ? (uint64_t)0 - (uint64_t)seed : (uint64_t)seed;
    uint32_t key[2]; int klen = 0;
    if (n == 0) key[klen++] = 0;
    while (n) { key[klen++] = (uint32_t)(n & 0xFFFFFFFFu); n >>= 32; }
    mt_init_by_array(m, key, klen);
}
GK_FN uint32_t mt_next(MT *m) {
    static const uint32_t mag01[2] = { 0x0u, 0x9908b0dfu };
    uint32_t y;
    if (m->mti >= 624) {
        int kk;
        for (kk = 0; kk < 624 - 397; kk++) {
            y = (m->mt[kk] & 0x80000000u) | (m->mt[kk + 1] & 0x7fffffffu);
            m->mt[kk] = m->mt[kk + 397] ^ (y >> 1) ^ mag01[y & 1u];
        }
        for (; kk < 623; kk++) {
            y = (m->mt[kk] & 0x80000000u) | (m->mt[kk + 1] & 0x7fffffffu);
            m->mt[kk] = m->mt[kk + (397 - 624)] ^ (y >> 1) ^ mag01[y & 1u];
        }
        y = (m->mt[623] & 0x80000000u) | (m->mt[0] & 0x7fffffffu);
        m->mt[623] = m->mt[396] ^ (y >> 1) ^ mag01[y & 1u];
        m->mti = 0;
    }
    y = m->mt[m->mti++];
    y ^= (y >> 11); y ^= (y << 7) & 0x9d2c5680u; y ^= (y << 15) & 0xefc60000u; y ^= (y >> 18);
    return y;
}
GK_FN double mt_random(MT *m) {
    uint32_t a = mt_next(m) >> 5, b = mt_next(m) >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

// ---------------------------------------------------------------------------
// prior table (resolved weights, built in Python -- GakumasCLI/native/native_search.py)
// ---------------------------------------------------------------------------
struct PriorTable {
    int32_t has_prior;
    double end_w[4];
    const double *card_w;
    int32_t n_drink_keys;
    int32_t drink_sid[16]; int32_t drink_n[16]; double drink_w[16][4];
    double drink_wt[16][16];
};

struct SearchParams {
    int64_t seed;
    int32_t variant;
    int32_t levels, iterations, stabilization, repetition_limit, persist_policy;
    int64_t transitions;
    double deadline;
    int32_t lesson_stamina;
    int32_t pick_variants;
};

#define S_MAXA 16
#define S_MAXPATH 160
#define S_MAXK 2048
#define S_MAXCURVE 512

struct TraceStep { int32_t n, chosen; int32_t key[S_MAXA]; double bias[S_MAXA]; };
struct Completion { int64_t major; int64_t score; int64_t minor; int32_t n; int32_t has_trace; uint16_t path[S_MAXPATH]; TraceStep tr[S_MAXPATH]; };

struct SearchResult {
    int32_t status;
    int32_t unsup_line, unsup_info;
    int64_t best_score; int32_t n_path; uint16_t path[S_MAXPATH];
    int64_t expansions, rollouts, incomplete_rollouts, adaptation_choices, incumbent_updates,
            top_level_calls, top_level_adaptations, repetition_stops;
    int32_t n_curve; int64_t curve_score[S_MAXCURVE]; int64_t curve_transitions[S_MAXCURVE]; double curve_seconds[S_MAXCURVE];
    int32_t n_keys;
    int64_t best_major;
};

typedef double (*clock_fn)(void);

struct Search {
    const Core *root;
    const SearchParams *P;
    const PriorTable *prior;
    SearchResult *res;
    MT rng;
    Core node;
    uint64_t kcode[S_MAXK * 2]; int32_t kid[S_MAXK * 2]; int32_t n_keys;
    Completion best; int32_t has_best;
    Completion scratch;
    clock_fn now; double started;
    int32_t failed;
    Completion lv_local[6], lv_result[6], lv_cand[6];
    double lv_pol[6][S_MAXK], lv_tmp[6][S_MAXK];
    double zero[S_MAXK], top[S_MAXK], top_tmp[S_MAXK];
    Completion top_result;
    const RuleTable *rules;
    RuleState rs;
};

GK_FN int key_intern(Search *s, uint64_t code) {
    uint32_t h = (uint32_t)((code * 0xB95C3CA3226DEC2Full) >> 40) & (S_MAXK * 2 - 1);
    while (s->kid[h] >= 0) {
        if (s->kcode[h] == code) return s->kid[h];
        h = (h + 1) & (S_MAXK * 2 - 1);
    }
    if (s->n_keys >= S_MAXK) { s->failed = 2; return 0; }
    s->kcode[h] = code; s->kid[h] = s->n_keys;
    return s->n_keys++;
}

GK_FN int stopped(Search *s) {
    if (s->P->transitions >= 0 && s->res->expansions >= s->P->transitions) return 1;
    if (s->P->deadline >= 0 && s->now && s->now() - s->started >= s->P->deadline) return 1;
    return 0;
}

// action code: kind * 256 + index, kind 0 drink / 1 end / 2 play (Python tuple order)
GK_FN uint16_t acode(int kind, int idx) { return (uint16_t)(kind * 256 + (kind == 1 ? 0 : idx)); }

// scenario/exam_session.py greedy_policy -> hand index or -1
GK_FN int greedy_policy(Core *c) {
    int best = -1; int64_t best_value = -1;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int slot = zget(c, Z_HAND, i);
        if (!can_play(c, slot)) continue;
        const Resolved *r = &c->res[slot];
        int64_t value = 0;
        for (int k = 0; k < r->n_play; k++) {
            Eff e;
            if (!eff_load(c, r->play[k].effect, &e)) continue;
            int t = e.type;
            if (t == 1 || t == 56 || t == 23) value += (e.has_v1 ? e.v1 : 0) + r->lesson_add;
            else if (t == 49) value += 2 * (e.has_v1 ? e.v1 : 0);
        }
        if (value > best_value) { best = i; best_value = value; }
    }
    return best;
}

// a held trigger (uses left) with an effect that picks by SELECT (pickRangeType 1)
GK_FN int held_select_trigger(Core *c) {
    const ExamStateP *S = ST(c);
    for (int i = 0; i < S->n_tes; i++) {
        const TES *ts = &S->tes[i];
        if (ts->remaining_turns == 0) continue;
        if (ts->limit_count >= 0 && ts->spent_total >= ts->limit_count) continue;
        for (int j = 0; j < ts->n_effect_ids; j++) {
            const Eff *e = eff_ptr(c, ts->effect_ids[j]);
            if (e && e->pick_range_type == 1) return 1;
        }
    }
    return 0;
}

// policy_search.py _choices -> sorted actions, key ids, bias
GK_FN int choices(Search *s, Core *c, int biased, uint16_t *acts, TraceStep *ts) {
    int32_t la[64];
    int n = legal_actions(c, la);
    int m = 0;
    for (int i = 0; i < n; i++) { int k = la[i] >> 8; if (k == 0 || k == 1 || k == 2) m++; }
    if (m > S_MAXA) { s->failed = 2; return 0; }
    m = 0;
    for (int i = 0; i < n; i++) if ((la[i] >> 8) == 1) acts[m++] = acode(0, la[i] & 0xFF);
    for (int i = 0; i < n; i++) if ((la[i] >> 8) == 2) acts[m++] = acode(1, 0);
    for (int i = 0; i < n; i++) if ((la[i] >> 8) == 0) acts[m++] = acode(2, la[i] & 0xFF);
    int preferred_all = -2;
    if (s->rules && biased) {
        int64_t best_value = -1;
        preferred_all = -1;
        for (int ai = 0; ai < m; ai++) if ((acts[ai] >> 8) == 2) {
            int idx = acts[ai] & 255, slot = zget(c, Z_HAND, idx);
            const Resolved *r = &c->res[slot]; int64_t value = 0;
            for (int k = 0; k < r->n_play; k++) {
                Eff e; if (!eff_load(c, r->play[k].effect, &e)) continue;
                int t = e.type;
                if (t == 1 || t == 56 || t == 23) value += (e.has_v1 ? e.v1 : 0) + r->lesson_add;
                else if (t == 49) value += 2 * (e.has_v1 ? e.v1 : 0);
            }
            if (value > best_value) { preferred_all = idx; best_value = value; }
        }
    }
    if (s->rules) {
        int kk[S_MAXA], ii[S_MAXA]; uint8_t keep[S_MAXA];
        for (int i = 0; i < m; i++) { kk[i] = acts[i] >> 8; ii[i] = acts[i] & 0xFF; }
        rt_filter(s->rules, &s->rs, c, m, kk, ii, keep);
        int w = 0;
        for (int i = 0; i < m; i++) if (keep[i]) acts[w++] = acts[i];
        m = w;
    }
    static int drink_macro = -1;
    if (drink_macro < 0) { const char *e = getenv("GKMS_NATIVE_DRINK_MACRO"); drink_macro = (e && e[0] == '1'); }
    if (drink_macro && !s->rules) {
        int plays = 0;
        for (int i = 0; i < m; i++) if ((acts[i] >> 8) == 2) plays++;
        uint16_t out[S_MAXA]; int mo = 0, fits = 1;
        for (int i = 0; i < m && fits; i++) {
            int kind = acts[i] >> 8, idx = acts[i] & 0xFF;
            int eligible = 0;
            if (kind == 0 && plays > 0 && idx < 16) {
                int32_t di = BY(c, 5, c->g.drinks[idx]);
                if (di >= 0) {
                    const DrinkRow *dr = &c->M->drinks[di];
                    for (int k = 0; k < dr->n_eff && !eligible; k++) {
                        Eff e; if (eff_load(c, P32(c, dr->eff_off)[k], &e) && (e.type == 38 || e.type == 59)) eligible = 1;
                    }
                }
            }
            if (!eligible) { if (mo >= S_MAXA) fits = 0; else out[mo++] = acts[i]; continue; }
            for (int j = 0; j < m; j++) if ((acts[j] >> 8) == 2 && (acts[j] & 0xFF) < 16) {
                if (mo >= S_MAXA) { fits = 0; break; }
                out[mo++] = acode(3, idx * 16 + (acts[j] & 0xFF));
            }
        }
        if (fits) { for (int i = 0; i < mo; i++) acts[i] = out[i]; m = mo; }
    }
    static int select_search = -1;
    if (select_search < 0) { const char *e = getenv("GKMS_NATIVE_SELECT_SEARCH"); select_search = (e && e[0] == '1'); }
    if (select_search && !s->rules) {
        int m0 = m;
        for (int i = 0; i < m0; i++) {
            int kind = acts[i] >> 8, idx = acts[i] & 0xFF;
            if ((kind != 0 && kind != 2) || idx >= 16) continue;
            int picks = 0;
            if (kind == 0) {
                int32_t di = BY(c, 5, c->g.drinks[idx]);
                if (di >= 0) {
                    const DrinkRow *dr = &c->M->drinks[di];
                    for (int k = 0; k < dr->n_eff && !picks; k++) {
                        Eff e; if (eff_load(c, P32(c, dr->eff_off)[k], &e) && e.pick_range_type == 1) picks = 1;
                    }
                }
            } else {
                const Resolved *r = &c->res[zget(c, Z_HAND, idx)];
                for (int k = 0; k < r->n_play && !picks; k++) {
                    Eff e; if (eff_load(c, r->play[k].effect, &e) && e.pick_range_type == 1) picks = 1;
                }
            }
            if (!picks) continue;
            for (int rank = 1; rank <= 2 && m < S_MAXA; rank++)
                acts[m++] = acode(4, ((kind == 2) << 7) | (idx << 3) | rank);
        }
    }
    if (s->P->pick_variants > 0 && !s->rules && !select_search && held_select_trigger(c)) {
        int hand = zlen(c, Z_HAND), m0 = m;
        int ranks = s->P->pick_variants < 7 ? s->P->pick_variants : 7;
        if (ranks > hand - 2) ranks = hand - 2;
        for (int rank = 1; rank <= ranks; rank++)
            for (int i = 0; i < m0 && m < S_MAXA; i++) {
                int kind = acts[i] >> 8, idx = acts[i] & 0xFF;
                if (kind != 2 || idx >= 16) continue;
                acts[m++] = acode(4, (1 << 7) | (idx << 3) | rank);
            }
    }
    int preferred=-1;
    if (preferred_all != -2) preferred = preferred_all;
    else if(biased) {
        int64_t best_value=-1;
        for(int ai=0;ai<m;ai++) if((acts[ai]>>8)==2) {
            int idx=acts[ai]&255, slot=zget(c,Z_HAND,idx);
            const Resolved *r=&c->res[slot]; int64_t value=0;
            for(int k=0;k<r->n_play;k++) {
                Eff e;if(!eff_load(c,r->play[k].effect,&e)) continue;
                int t=e.type;
                if(t==1 || t==56 || t==23) value+=(e.has_v1?e.v1:0)+r->lesson_add;
                else if(t==49) value+=2*(e.has_v1?e.v1:0);
            }
            if(value>best_value) {preferred=idx;best_value=value;}
        }
    }
    int64_t phase = (c->g.s_turn - 1) * 4 / (c->R->limit_turn > 1 ? c->R->limit_turn : 1);
    if (phase > 3) phase = 3;
    const PriorTable *pr = s->prior;
    static int drink_key_mode = -1;
    if (drink_key_mode < 0) {
        const char *e = getenv("GKMS_NATIVE_DRINK_KEY");
        drink_key_mode = !e ? 1 : (e[0] == 'p' ? 0 : (e[0] == 'h' ? 2 : (e[0] == 'm' ? 3 : 1)));
    }
    int drink_turns = drink_key_mode == 1 || drink_key_mode == 2 || (drink_key_mode == 3 && (s->P->seed & 1));
    int64_t drink_left = (int64_t)c->R->limit_turn + ST(c)->extra_turns - c->g.s_turn + 1;
    drink_left = drink_left < 0 ? 0 : (drink_left > 15 ? 15 : drink_left);
    int64_t drink_ctx = drink_turns ? drink_left : phase; uint64_t drink_hand = 0;
    if (drink_key_mode == 2) {
        int64_t top_value = -1; int32_t top_card = 0;
        for (int ai = 0; ai < m; ai++) if ((acts[ai] >> 8) == 2) {
            int slot = zget(c, Z_HAND, acts[ai] & 255);
            const Resolved *r = &c->res[slot]; int64_t value = 0;
            for (int k = 0; k < r->n_play; k++) {
                Eff e; if (!eff_load(c, r->play[k].effect, &e)) continue;
                if (e.type == 1 || e.type == 56 || e.type == 23) value += (e.has_v1 ? e.v1 : 0) + r->lesson_add;
                else if (e.type == 49) value += 2 * (e.has_v1 ? e.v1 : 0);
            }
            if (value > top_value) { top_value = value; top_card = c->g.cards[slot].produce_card_id; }
        }
        drink_hand = (((uint64_t)(uint32_t)top_card * 0x9E3779B97F4A7C15ull) >> 48) << 40;
    }
    ts->n = m;
    for (int i = 0; i < m; i++) {
        int kind = acts[i] >> 8, idx = acts[i] & 0xFF;
        uint64_t code; double b; double w = 0.0;
        if (kind == 1) {
            code = ((uint64_t)1 << 60) | ((uint64_t)phase << 56);
            b = biased ? -1.0 : 0.0;
            if (pr && pr->has_prior) w = pr->end_w[phase];
        } else if (kind == 0) {
            int32_t did = c->g.drinks[idx];
            code = ((uint64_t)2 << 60) | ((uint64_t)drink_ctx << 56) | drink_hand
                 | ((uint64_t)(uint32_t)did << 8) | (uint64_t)c->g.n_drinks;
            b = biased ? -1.0 : 0.0;
            if (pr && pr->has_prior) {
                int found = 0;
                for (int k = 0; k < pr->n_drink_keys; k++)
                    if (pr->drink_sid[k] == did && pr->drink_n[k] == c->g.n_drinks) {
                        w = drink_turns ? pr->drink_wt[k][drink_left] : NAN;
                        if (w != w) w = pr->drink_w[k][phase];
                        found = 1; break;
                    }
                if (!found) { s->failed = 3; return 0; }
            }
        } else if (kind == 4) {
            int isplay = idx >> 7, aidx = (idx >> 3) & 15, rank = idx & 7;
            uint64_t id = isplay ? (uint64_t)(uint32_t)c->g.cards[zget(c, Z_HAND, aidx)].produce_card_id
                                 : (uint64_t)(uint32_t)c->g.drinks[aidx];
            code = ((uint64_t)5 << 60) | ((uint64_t)phase << 56) | ((uint64_t)isplay << 52) | (id << 8) | (uint64_t)rank;
            b = biased ? -1.0 : 0.0;
        } else if (kind == 3) {
            int32_t did = c->g.drinks[idx >> 4];
            const Card *cd = &c->g.cards[zget(c, Z_HAND, idx & 15)];
            code = ((uint64_t)4 << 60) | ((uint64_t)drink_ctx << 56) | ((uint64_t)(uint32_t)did << 28)
                 | ((uint64_t)(uint32_t)cd->produce_card_id << 8) | (uint64_t)(cd->current_upgrade_count & 0xFF);
            b = biased ? -1.0 : 0.0;
            if (pr && pr->has_prior) {
                for (int k = 0; k < pr->n_drink_keys; k++)
                    if (pr->drink_sid[k] == did && pr->drink_n[k] == c->g.n_drinks) {
                        w = drink_turns ? pr->drink_wt[k][drink_left] : NAN;
                        if (w != w) w = pr->drink_w[k][phase];
                        break;
                    }
            }
        } else {
            const Card *cd = &c->g.cards[zget(c, Z_HAND, idx)];
            code = ((uint64_t)3 << 60) | ((uint64_t)phase << 56) | ((uint64_t)(uint32_t)cd->produce_card_id << 8)
                 | (uint64_t)(cd->current_upgrade_count & 0xFF);
            b = (biased && idx == preferred) ? 2.0 : 0.0;
            if (pr && pr->has_prior) {
                const int slot = zget(c, Z_HAND, idx);
                const CardRow *row = ((c->resolved_valid >> slot) & 1) ? &c->M->cards[c->res[slot].row] : 0;
                if (!row || row->id != cd->produce_card_id || row->upg != cd->current_upgrade_count)
                    row = card_row(c, cd->produce_card_id, cd->current_upgrade_count);
                if (!row) { s->failed = 3; return 0; }
                w = pr->card_w[(row - c->M->cards) * 4 + phase];
                if (w != w) { s->failed = 3; return 0; }
            }
        }
        ts->key[i] = key_intern(s, code);
        ts->bias[i] = (pr && pr->has_prior) ? b + w : b;
        if (s->rules && s->rules->has_prefer) ts->bias[i] = ts->bias[i] + rt_prefer(s->rules, c, kind, idx);
    }
    return m;
}

// policy_search.py _probabilities
GK_FN void probabilities(const TraceStep *ts, const double *pol, double *probs) {
    GK_COUNT(prob_calls,1); GK_COUNT(exp_calls,ts->n);
    double logits[S_MAXA], shift = 0.0;
    for (int i = 0; i < ts->n; i++) {
        logits[i] = pol[ts->key[i]] + ts->bias[i];
        if (i == 0 || logits[i] > shift) shift = logits[i];
    }
    double w[S_MAXA], total = 0.0;
    for (int i = 0; i < ts->n; i++) w[i] = exp(logits[i] - shift);
    for (int i = 0; i < ts->n; i++) total += w[i];
    for (int i = 0; i < ts->n; i++) probs[i] = w[i] / total;
}

GK_FN int better(const Completion *a, const Completion *b, int has_b) {
    if (!has_b) return 1;
    if (a->major != b->major) return a->major < b->major;
    if (a->score != b->score) return a->score > b->score;
    if (a->minor != b->minor) return a->minor > b->minor;
    int n = a->n < b->n ? a->n : b->n;
    for (int i = 0; i < n; i++) if (a->path[i] != b->path[i]) return a->path[i] < b->path[i];
    return a->n < b->n;
}
GK_FN int same_path(const Completion *a, const Completion *b) {
    if (a->n != b->n) return 0;
    for (int i = 0; i < a->n; i++) if (a->path[i] != b->path[i]) return 0;
    return 1;
}
GK_FN void copy_completion(Completion *dst, const Completion *src) {
    dst->major = src->major; dst->score = src->score; dst->minor = src->minor; dst->n = src->n; dst->has_trace = src->has_trace;
    memcpy(dst->path, src->path, sizeof(uint16_t) * src->n);
    GK_COUNT(trace_copy_bytes,sizeof(TraceStep)*src->n);
    memcpy(dst->tr, src->tr, sizeof(TraceStep) * src->n);
}

GK_FN void commit(Search *s, const Completion *r) {
    if (better(r, &s->best, s->has_best)) {
        copy_completion(&s->best, r);
        s->has_best = 1;
        SearchResult *R = s->res;
        R->incumbent_updates++;
        if (R->n_curve < S_MAXCURVE) {
            R->curve_score[R->n_curve] = r->score;
            R->curve_transitions[R->n_curve] = R->expansions;
            R->curve_seconds[R->n_curve] = s->now ? s->now() - s->started : 0.0;
            R->n_curve++;
        }
    }
}

// playout: returns 1 with s->scratch filled, 0 incomplete / failure
GK_FN int playout(Search *s, const double *pol, int greedy) {
    Core *c = &s->node;
    core_copy(c, s->root);
    Completion *r = &s->scratch;
    r->n = 0; r->has_trace = 1;
    int biased = s->P->variant == 2;
    if (s->rules) s->rs = s->rules->root;
    while (!c->g.s_finished) {
        if (!greedy && stopped(s)) { s->res->incomplete_rollouts++; return 0; }
        if (r->n >= S_MAXPATH) { s->failed = 2; return 0; }
        TraceStep *ts = &r->tr[r->n];
        uint16_t acts[S_MAXA];
        if (s->rules) rt_sync(&s->rs, c);
        int m = choices(s, c, biased, acts, ts);
        if (s->failed) return 0;
        if (m == 0) { s->failed = 2; return 0; }
        int chosen;
        if (greedy) {
            int idx = greedy_policy(c);
            uint16_t want = idx < 0 ? acode(1, 0) : acode(2, idx);
            chosen = -1;
            for (int i = 0; i < m; i++) if (acts[i] == want) { chosen = i; break; }
            if (chosen < 0 && s->rules) chosen = 0;
            if (chosen < 0) { s->failed = 2; return 0; }
        } else {
            double probs[S_MAXA];
            probabilities(ts, pol, probs);
            double draw = mt_random(&s->rng), total = 0.0;
            chosen = m - 1;
            for (int i = 0; i < m; i++) { total += probs[i]; if (draw < total) { chosen = i; break; } }
        }
        ts->chosen = chosen;
        int kind = acts[chosen] >> 8, idx = acts[chosen] & 0xFF;
        uint16_t taken = acts[chosen];
        int rc;
        if (kind == 3) {
            int d = idx >> 4, slot = zget(c, Z_HAND, idx & 15);
            rc = session_step(c, 1, d);
            taken = acode(0, d);
            if (rc == 0 && !c->g.s_finished) {
                int np = -1;
                for (int h = 0; h < zlen(c, Z_HAND); h++) if (zget(c, Z_HAND, h) == slot) { np = h; break; }
                if (np >= 0 && np < 16 && can_play(c, slot)) {
                    rc = session_step(c, 0, np);
                    taken = acode(3, d * 16 + np);
                }
            }
        } else if (kind == 4) {
            int isplay = idx >> 7, aidx = (idx >> 3) & 15;
            c->sel_pick = (idx & 7) + 1; c->sel_taken = 0;
            rc = session_step(c, isplay ? 0 : 1, aidx);
            taken = acode(isplay ? 2 : 0, aidx);
            if (c->sel_taken > 0 && c->sel_taken <= 240) taken = acode(8 + c->sel_taken - 1, (isplay << 7) | aidx);
            else if (c->sel_taken > 240) { s->failed = 2; return 0; }
            c->sel_pick = 0; c->sel_taken = 0;
        } else if (s->rules) {
            int64_t tb, lb;
            rt_taken(s->rules, &s->rs, c, kind, idx, &tb, &lb);
            rc = session_step(c, kind == 0 ? 1 : kind == 1 ? 2 : 0, idx);
            if (rc == 0) rt_after(s->rules, &s->rs, c, tb, lb);
        } else {
            rc = session_step(c, kind == 0 ? 1 : kind == 1 ? 2 : 0, idx);
        }
        if (rc != 0) {
            s->failed = rc == 1 ? 1 : 2;
            s->res->unsup_line = c->unsup; s->res->unsup_info = c->unsup_info;
            return 0;
        }
        s->res->expansions++;
        r->path[r->n] = taken;
        r->n++;
    }
    s->res->rollouts++;
    r->score = c->g.st.score;
    r->major = 0;
    r->minor = s->P->lesson_stamina ? c->g.st.stamina : 0;
    if (s->rules) {
        r->score -= rt_penalty(s->rules, &s->rs);
        r->major = rt_major(s->rules, &s->rs, c);
    }
    commit(s, r);
    return 1;
}

// policy_search.py adapt.
GK_FN void adapt(Search *s, const double *pol, const Completion *r, double *out) {
    GK_COUNT(adapt_calls,1); GK_COUNT(policy_copy_bytes,sizeof(double)*S_MAXK);
    memcpy(out, pol, sizeof(double) * S_MAXK);
    for (int k = 0; k < r->n; k++) {
        if (stopped(s)) { memcpy(out, pol, sizeof(double) * S_MAXK); return; }
        const TraceStep *ts = &r->tr[k];
        double probs[S_MAXA];
        probabilities(ts, pol, probs);
        for (int i = 0; i < ts->n; i++) {
            out[ts->key[i]] = out[ts->key[i]] + ((i == ts->chosen ? 1.0 : 0.0) - probs[i]);
            s->res->adaptation_choices++;
        }
    }
}

// policy_search.py nested.
GK_FN int nested(Search *s, int level, const double *policy_in, Completion *out) {
    if (level == 0) {
        if (!playout(s, policy_in, 0)) return 0;
        copy_completion(out, &s->scratch);
        return 1;
    }
    if (level > 5) { s->failed = 2; return 0; }
    double *policy = s->lv_pol[level];
    double *tmp = s->lv_tmp[level];
    memcpy(policy, policy_in, sizeof(double) * S_MAXK);
    Completion *local = &s->lv_local[level];
    Completion *result = &s->lv_result[level];
    Completion *cand = &s->lv_cand[level];
    int has_local = 0, repeats = 0;
    for (int it = 0; it < s->P->iterations; it++) {
        if (stopped(s)) break;
        int has_result = 0;
        if (level == 1 && s->P->stabilization > 1) {
            for (int k = 0; k < s->P->stabilization; k++) {
                if (!nested(s, 0, policy, cand)) break;
                if (better(cand, result, has_result)) { copy_completion(result, cand); has_result = 1; }
            }
            if (s->failed) return 0;
        } else {
            has_result = nested(s, level - 1, policy, result);
            if (s->failed) return 0;
        }
        if (!has_result) break;
        if (has_local && same_path(result, local)) repeats++;
        else repeats = 0;
        if (better(result, local, has_local)) { copy_completion(local, result); has_local = 1; }
        adapt(s, policy, local, tmp);
        { double *p = policy; policy = tmp; tmp = p; }
        if (s->P->repetition_limit && repeats >= s->P->repetition_limit) { s->res->repetition_stops++; break; }
    }
    if (!has_local) return 0;
    copy_completion(out, local);
    return 1;
}

// policy_search.py policy_search (no initial_route, no cancel)
GK_FN void policy_search_native(Search *s) {
    SearchResult *R = s->res;
    if (s->P->levels < 1 || s->P->levels > 5 || s->P->iterations < 1 || s->P->stabilization < 1
        || s->P->repetition_limit < 0 || (s->P->transitions < 0 && s->P->deadline < 0)) { R->status = 2; return; }
    mt_seed_int(&s->rng, s->P->seed);
    for (int i = 0; i < S_MAXK * 2; i++) s->kid[i] = -1;
    s->n_keys = 0; s->has_best = 0; s->failed = 0;
    memset(s->zero, 0, sizeof(s->zero));
    memset(s->top, 0, sizeof(s->top));
    double *zero = s->zero, *top = s->top, *tmp = s->top_tmp;
    Completion *result = &s->top_result;
    playout(s, zero, 1);
    while (!s->failed && !s->root->g.s_finished && !stopped(s)) {
        if (s->P->variant == 0) { playout(s, zero, 0); continue; }
        R->top_level_calls++;
        int has = nested(s, s->P->levels, s->P->persist_policy ? top : zero, result);
        if (s->failed) break;
        if (s->P->persist_policy && has) {
            const Completion *target = s->best.has_trace ? &s->best : result;
            adapt(s, top, target, tmp);
            { double *p = top; top = tmp; tmp = p; }
            R->top_level_adaptations++;
        }
    }
    R->status = s->failed == 0 ? 0 : s->failed == 1 ? 1 : 2;
    if (s->has_best) {
        R->best_score = s->best.score;
        R->best_major = s->best.major;
        R->n_path = s->best.n;
        memcpy(R->path, s->best.path, sizeof(uint16_t) * s->best.n);
    }
    R->n_keys = s->n_keys;
}
