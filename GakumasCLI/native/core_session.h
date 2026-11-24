// Copyright (C) 2026 花吹雪また一年P and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 5: effect dispatch body, turn flow, ExamSession step (scenario/exam_session.py), C ABI.
#pragma once
#include "core_effects.h"
#include "core_card_growth.h"

GK_FN void apply_card_create_id(Core *c, const Eff *e);
GK_FN int64_t E_v1(const Eff *e) { return e->has_v1 ? e->v1 : 0; }
// exam.py _apply_effect_body (84) -- stamina_consumption_add_fix layers are additive ``(value, until_turn)`` entries
// and are independent of any tower layer id.
GK_FN int64_t add_fix_sum(const TupleList *L) {
    int64_t s = 0;
    for (int i = 0; i < L->n; i++) {
        if (L->v[i][0].tag != 0) return 0;
        s += L->v[i][0].i;
    }
    return s;
}
GK_FN int64_t E_v2(const Eff *e) { return e->has_v2 ? e->v2 : 0; }

GK_FN void tl_sum_total(const TupleList *L, double *out) {
    double t = f32(1.0);
    for (int i = 0; i < L->n; i++) {
        double e = num_as_double(&L->v[i][1]);
        int seen = 0;
        for (int j = 0; j < i && !seen; j++) seen = num_as_double(&L->v[j][1]) == e;
        if (seen) continue;
        double g = f32(num_as_double(&L->v[i][0]));
        for (int j = i + 1; j < L->n; j++)
            if (num_as_double(&L->v[j][1]) == e) g = f32(g + num_as_double(&L->v[j][0]));
        t = f32(t + g);
    }
    *out = t;
}

// Timed tower layers use a two-element tuple whose second value is an integer expiry (or -1 for the exam).
GK_FN int timed_layer_live(Core *c, const TupleList *L, int i, int64_t turn, int info) {
    if (i < 0 || i >= L->n || L->len[i] < 2) { UNSUP(c, info); return 0; }
    const Num *u = &L->v[i][1];
    if (u->tag == 0) return u->i == -1 || turn <= u->i;
    if (u->tag == 1 && isinf(u->f)) return 1;
    UNSUP(c, info); return 0;
}

GK_FN void remove_timed_layer(TupleList *L, int at) {
    for (int i = at; i + 1 < L->n; i++) {
        L->len[i] = L->len[i + 1];
        for (int j = 0; j < GKMS_MAX_TUPLE; j++) L->v[i][j] = L->v[i + 1][j];
    }
    if (L->n) L->n--;
}

GK_FN int fold_layer(Core *c, TupleList *L, int is_float, double fv, int64_t iv, int64_t duration, int f32sum) {
    ExamStateP *S = ST(c);
    for (int i = 0; i < L->n; i++) {
        if (L->len[i] < 2) { UNSUP(c, 0); return 0; }
        const Num *ex = &L->v[i][1];
        int ex_inf = (ex->tag == 1 && isinf(ex->f)) || ex->tag == 2;
        if (ex->tag == 1 && !isinf(ex->f)) { UNSUP(c, 0); return 0; }
        int match = duration <= 0 ? ex_inf
            : (!ex_inf && layer_left(c, ex->i, L->len[i] > 2 ? L->v[i][2].i : 0) == duration);
        if (!match) continue;
        Num *v = &L->v[i][0];
        if (is_float) { double a = num_as_double(v) + fv;
                        v->tag = 1; v->f = f32sum ? f32_div((double)(llround(num_as_double(v) * 1000.0) + llround(fv * 1000.0)), 1000.0) : a;
                        v->i = 0; }
        else { if (v->tag != 0) { UNSUP(c, 0); return 0; } v->i += iv; }
        return 1;
    }
    if (L->n >= GKMS_MAX_LAYERS) { UNSUP(c, 0); return 0; }
    int i = L->n++;
    memset(L->v[i], 0, sizeof(L->v[i]));
    L->len[i] = 3;
    if (is_float) { L->v[i][0].tag = 1; L->v[i][0].f = fv; } else { L->v[i][0].tag = 0; L->v[i][0].i = iv; }
    if (duration <= 0) { L->v[i][1].tag = 1; L->v[i][1].f = INFINITY; }
    else { L->v[i][1].tag = 0; L->v[i][1].i = S->turn + duration - S->_in_turn_start; }
    L->v[i][2].tag = 0; L->v[i][2].i = fresh_mark(c);
    return 1;
}

// exam.py _extend_enthusiastic -- 熱意延長 (189) adds ``turns`` turn ends to each 熱意 amount present: the kept (ends, 熱意)
// entries shift, the rest of 熱意 joins at ``turns``; equal ends merge, ends ascending, ends <= 0 dropped
GK_FN void extend_enthusiastic(Core *c, int64_t turns) {
    ExamStateP *S = ST(c);
    TupleList *L = &S->enthusiastic_kept;
    int64_t ends[GKMS_MAX_LAYERS + 1], amount[GKMS_MAX_LAYERS + 1], kept = 0;
    int n = 0;
    for (int i = 0; i < L->n; i++) {
        if (L->v[i][0].tag != 0 || L->v[i][1].tag != 0) { UNSUP(c, 189); return; }
        kept += L->v[i][1].i;
        int64_t e = L->v[i][0].i + turns;
        int j = 0;
        while (j < n && ends[j] != e) j++;
        if (j == n) { ends[n] = e; amount[n] = 0; n++; }
        amount[j] += L->v[i][1].i;
    }
    int64_t fresh = S->enthusiastic_buff - kept;
    if (fresh > 0) {
        int j = 0;
        while (j < n && ends[j] != turns) j++;
        if (j == n) { ends[n] = turns; amount[n] = 0; n++; }
        amount[j] += fresh;
    }
    for (int i = 1; i < n; i++)
        for (int j = i; j > 0 && ends[j - 1] > ends[j]; j--) {
            int64_t e = ends[j]; ends[j] = ends[j - 1]; ends[j - 1] = e;
            int64_t a = amount[j]; amount[j] = amount[j - 1]; amount[j - 1] = a;
        }
    int k = 0;
    for (int i = 0; i < n; i++) {
        if (ends[i] <= 0) continue;
        if (k >= GKMS_MAX_LAYERS) { UNSUP(c, 189); return; }
        memset(L->v[k], 0, sizeof(L->v[k]));
        L->len[k] = 2; L->v[k][0].i = ends[i]; L->v[k][1].i = amount[i];
        k++;
    }
    L->n = k;
}

// exam.py _sync_review_count_add -- the sum of the live 好印象 settlement statuses, ending with the last
GK_FN void sync_review_count_add(Core *c) {
    ExamStateP *S = ST(c);
    const TupleList *L = &S->review_count_add_layers;
    int64_t sum = 0, last = NONE;
    for (int i = 0; i < L->n; i++) {
        sum += L->v[i][0].i;
        int64_t u = L->v[i][1].tag == 0 ? L->v[i][1].i : NONE;
        if (L->v[i][1].tag != 0) { UNSUP(c, 195); return; }
        if (last == NONE || u > last) last = u;
    }
    S->review_count_add = sum;
    S->has_review_count_add_until_turn = last != NONE;
    S->review_count_add_until_turn = last;
}

GK_FN int64_t debuff_sequence(const Core *c, int32_t key) {
    const SmallStrIntMap *m = &ST(c)->debuff_since;
    for (int i = 0; i < m->n; i++) if (m->key[i] == key) return m->val[i];
    return 0;
}

GK_FN void debuff_sequence_remove(Core *c, int32_t key) {
    SmallStrIntMap *m = &ST(c)->debuff_since;
    for (int i = 0; i < m->n; i++) if (m->key[i] == key) {
        for (int j = i; j + 1 < m->n; j++) { m->key[j] = m->key[j + 1]; m->val[j] = m->val[j + 1]; }
        m->n--; return;
    }
}

GK_FN void clear_debuff_status(Core *c, int kind) {
    ExamStateP *S = ST(c);
    int32_t key = (kind >= 0 && kind < 220) ? c->M->sid_debuff_kind[kind] : -1;
    switch (kind) {
    case 18: S->block_restriction = 0; S->block_restriction_until_turn = NONE; break;
    case 73: S->block_add_down = 0; S->block_add_down_restriction = 0; S->block_add_down_until_turn = NONE; break;
    case 107: S->block_add_down_fix = 0; S->block_add_down_fix_restriction = 0; break;
    case 69: S->stamina_consumption_add = 0; S->stamina_consumption_add_active_from_turn = NONE;
              S->stamina_consumption_add_until_turn = NONE; break;
    case 84: S->stamina_consumption_add_fix = 0; S->stamina_consumption_add_fix_layers.n = 0; break;
    case 199: S->buff_consumption_add = 0; S->buff_consumption_add_active_from_turn = NONE;
              S->buff_consumption_add_until_turn = NONE; break;
    case 86: S->stamina_recover_restriction = 0; S->stamina_recover_restriction_until_turn = NONE; break;
    case 114: S->slump = 0; S->slump_until_turn = NONE; break;
    case 106: S->gimmick_parameter_debuff = 0; S->gimmick_parameter_debuff_until_turn = NONE; break;
    case 123: S->has_lesson_change_specify = 0; S->lesson_change_specify = NONE; break;
    case 141: S->stance_lock_turns = 0; S->stance_lock_passing_turn_start = 0; break;
    case 155: S->lesson_parameter_multiple_down = 0.0; S->lesson_parameter_multiple_down_layers.n = 0; break;
    case 105: S->lesson_debuff_layers.n = 0; S->lesson_debuff = 0; break;
    case 115: S->card_draw_down_layers.n = 0; break;
    case 78: S->has_panic_until_turn = 0; S->panic_until_turn = NONE; break;
    default: break;
    }
    if (key >= 0) debuff_sequence_remove(c, key);
}

// exam.py _apply_effect_body
struct DebuffItem { int64_t seq; int order; int kind; int row; };
GK_FN int debuff_live(const ExamStateP *S, int64_t until) {
    return until != NONE && (until == -1 || S->turn <= until);
}
#define GK_MAX_DEBUFF_ITEMS (GKMS_MAX_LAYERS + 16)
GK_FN int collect_debuffs(Core *c, DebuffItem *items) {
    ExamStateP *S = ST(c);
    int n = 0;
    const int kinds[] = {18, 73, 107, 69, 84, 199, 86, 114, 106, 123, 141, 155, 105, 115, 78};
    for (int j = 0; j < (int)(sizeof(kinds) / sizeof(kinds[0])); j++) {
        int k = kinds[j], active = 0;
        switch (k) {
        case 18: active = S->block_restriction || debuff_live(S, S->block_restriction_until_turn); break;
        case 73: active = S->block_add_down || S->block_add_down_restriction; break;
        case 107: active = S->block_add_down_fix || S->block_add_down_fix_restriction; break;
        case 69: active = S->stamina_consumption_add || debuff_live(S, S->stamina_consumption_add_until_turn); break;
        case 84: active = S->stamina_consumption_add_fix != 0; break;
        case 199: active = S->buff_consumption_add || debuff_live(S, S->buff_consumption_add_until_turn); break;
        case 86: active = S->stamina_recover_restriction || debuff_live(S, S->stamina_recover_restriction_until_turn); break;
        case 114: active = S->slump; break;
        case 106: active = S->gimmick_parameter_debuff; break;
        case 123: active = S->has_lesson_change_specify; break;
        case 141: active = S->stance_lock_turns > 0; break;
        case 155: active = S->lesson_parameter_multiple_down != 0.0; break;
        case 105: {
            for (int i = 0; i < S->lesson_debuff_layers.n; i++)
                if (timed_layer_live(c, &S->lesson_debuff_layers, i, S->turn, k)) { active = 1; break; }
            break;
        }
        case 115: {
            for (int i = 0; i < S->card_draw_down_layers.n; i++)
                if (timed_layer_live(c, &S->card_draw_down_layers, i, S->turn, k)) { active = 1; break; }
            break;
        }
        case 78: active = panic_active(c); break;
        }
        if (c->unsup) return -1;
        if (active && n < GK_MAX_DEBUFF_ITEMS) {
            int32_t key = (k >= 0 && k < 220) ? c->M->sid_debuff_kind[k] : -1;
            items[n++] = { debuff_sequence(c, key), j, k, -1 };
        }
    }
    for (int i = 0; i < S->play_card_limits.n; i++) {
        if (S->play_card_limits.len[i] < 2 || S->play_card_limits.v[i][0].tag != 0 || S->play_card_limits.v[i][1].tag != 0)
            UNSUP_RET(c, 148, -1);
        if (!timed_layer_live(c, &S->play_card_limits, i, S->turn, 148)) continue;
        if (n < GK_MAX_DEBUFF_ITEMS)
            items[n++] = { debuff_sequence(c, -(int32_t)S->play_card_limits.v[i][0].i - 2),
                           (int)(sizeof(kinds) / sizeof(kinds[0])) + i, 1000, (int)S->play_card_limits.v[i][0].i };
    }
    for (int i = 1; i < n; i++) {
        DebuffItem x = items[i]; int j = i - 1;
        while (j >= 0 && (items[j].seq < x.seq || (items[j].seq == x.seq && items[j].order < x.order))) {
            items[j + 1] = items[j]; j--;
        }
        items[j + 1] = x;
    }
    return n;
}

GK_FN int active_debuff_count(Core *c) {
    DebuffItem items[GK_MAX_DEBUFF_ITEMS];
    return collect_debuffs(c, items);
}

// Effect 147's triggers run outside a play (turn start / end): unchanged
GK_FN int64_t used_card_count(const Core *c) {
    const ExamStateP *S = ST(c);
    return S->play_count - ((c->card_own_depth || S->playing_card >= 0) ? 1 : 0);
}

GK_OUTLINE void apply_effect_body(Core *c, const Eff *e, int64_t permil) {
    ExamStateP *S = ST(c);
    int t = e->type;
    int64_t value = E_v1(e);
    if (in_anti_debuff_gated(t)) { if (anti_debuff_gate(c, t)) return; if (c->unsup) return; }
    switch (t) {
    case 9: apply_card_move(c, e); return;
    case 11: apply_card_upgrade(c, e); return;
    case 21: apply_card_create_search(c, e, 0, 0); return;
    case 30: apply_card_duplicate(c, e); return;
    case 156: apply_card_grow(c, e); return;
    case 6: apply_card_create_id(c, e); return;
    case 4: { int16_t out[64]; c->effect_draws += draw_cards(c, value, out); return; }
    case 36: add_review(c, ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)S->review)), 1); return;
    case 45: case 46: {
        int step = value < 1 ? 1 : (int)value;
        stance_change(c, t == 45 ? GK_STANCE_CONCENTRATION : GK_STANCE_PRESERVATION, step);
        return;
    }
    case 120: {
        int64_t gained = ceil_f32(f32_div(f32((double)value * (double)S->lesson_buff), 1000.0));
        S->lesson_buff = imax(0, S->lesson_buff + gained);
        return;
    }
    case 13: {
        double factor = f32(1.0 + f32_div((double)value, 1000.0));
        int64_t delta = ceil_f32(f32_mul(factor, (double)S->block) - (double)S->block);
        int64_t applied = imax(-S->block, delta);
        S->block += applied;
        S->addition_block += applied;
        return;
    }
    case 15: case 182: {
        int64_t duration = e->has_turn ? e->turn : 0;
        TupleList *L = t == 15 ? &S->lesson_buff_multiple_layers : &S->review_multiple_layers;
        if (!fold_layer(c, L, 1, f32_div((double)value, 1000.0), 0, duration, 1)) return;
        double total; tl_sum_total(L, &total);
        if (t == 15) S->lesson_buff_multiple = total; else S->review_multiple = total;
        return;
    }
    case 174: case 175: case 176: case 177: {
        int k = t == 174 ? 0 : t == 175 ? 1 : t == 176 ? 2 : 3;
        int64_t duration = e->has_turn ? e->turn : 0;
        grant_timed_gain(c, k, 1, (double)value / 1000.0, 0, duration);
        return;
    }
    case 210: case 211: case 212: {
        int64_t duration = e->has_turn ? e->turn : 0;
        grant_timed_gain(c, t == 210 ? 7 : t == 211 ? 4 : 5, 0, 0.0, value, duration);
        return;
    }
    case 124: {
        int64_t source = S->review;
        int64_t added = ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)source) - 0.0001);
        int64_t cnt = e->has_count ? e->count : 1;
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, added, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        int64_t remaining = ceil_f32(f32_mul(1.0 - f32_div((double)E_v2(e), 1000.0), (double)source));
        if (S->review > 0 && remaining == 0) UNSUP_RET(c, t, );
        S->review = remaining;
        return;
    }
    case 131: {
        int64_t source = S->parameter_buff_remaining;
        if (source <= 0) return;
        int64_t added = ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)source) - 0.0001);
        int64_t cnt = (e->has_count && e->count) ? e->count : 1;
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, added, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        int64_t remaining = ceil_f32(f32_mul(1.0 - f32_div((double)E_v2(e), 1000.0), (double)source));
        S->parameter_buff_remaining = remaining;
        return;
    }
    case 147: {
        int64_t added = value + E_v2(e) * used_card_count(c);
        int64_t cnt = (e->has_count && e->count) ? e->count : 1;
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, added, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        return;
    }
    case 133: {
        int64_t raw = value + used_card_count(c) * E_v2(e);
        int64_t gain = calculate_add_block(c, raw, 1.0);
        if (c->unsup) return;
        int64_t applied = imax(-S->block, gain);
        S->block += applied;
        S->addition_block += applied;
        return;
    }
    case 49: {
        int64_t gain = add_full_power_point(c, value);
        if (gain > 0) raise_direct_status_change(c, 49, gain);
        return;
    }
    case 56: {
        int64_t extra = lesson_full_power_point_extra(c, E_v2(e));
        Eff q = *e;
        q.type = 1;
        q.v1 = value + extra;
        q.has_v1 = 1;
        apply_effect(c, &q, permil);
        return;
    }
    case 181: {
        int64_t turns = E_v1(e);
        if (!turns && e->has_turn) turns = e->turn;
        S->lesson_parameter_multiple_depend_review_or_aggressive = 1;
        S->has_lesson_parameter_multiple_depend_until_turn = 1;
        int64_t u = S->lesson_parameter_multiple_depend_until_turn;
        if (u != NONE && u >= S->turn) S->lesson_parameter_multiple_depend_until_turn = u + turns;
        else S->lesson_parameter_multiple_depend_until_turn = S->turn + turns - S->_in_turn_start;
        return;
    }
    case 24: case 187: {
        const SearchRow *sr = search_row(c, e->search);
        int owner = c->running_tes >= 0 ? S->tes[c->running_tes].owner_card : -1;
        if (sr && sr->is_self && owner >= 0) {
            if (ST(c)->n_force >= GKMS_MAX_CARDS) UNSUP_RET(c, 0, );
            ST(c)->force_has_resolved[ST(c)->n_force] = 1;
            ST(c)->force[ST(c)->n_force++] = (int16_t)owner;
            return;
        }
        if (!sr) UNSUP_RET(c, e->search, );
        if (sr->pos == 7) {
            int16_t made[256]; int nm = 0;
            c->support_rolls_off = 1;
            apply_card_create_search(c, e, made, &nm);
            c->support_rolls_off = 0;
            if (c->unsup) return;
            for (int i = 0; i < nm; i++) {
                int at = zfind(c, Z_HAND, made[i]);
                if (at >= 0) zpop(c, Z_HAND, at);
                else if ((at = zfind(c, Z_DECK, made[i])) >= 0) zpop(c, Z_DECK, at);
            }
            for (int i = 0; i < nm; i++) {
                if (t == 187) c->g.cards[made[i]].force_pays_cost = 1;
                if (ST(c)->n_force >= GKMS_MAX_CARDS) UNSUP_RET(c, 0, );
                ST(c)->force_has_resolved[ST(c)->n_force] = 1;
                ST(c)->force[ST(c)->n_force++] = made[i];
            }
            return;
        }
        SRow sel[MAX_ROWS];
        int n = get_search_card_list(c, e, S->playing_card, sel);
        if (c->unsup) return;
        for (int i = 0; i < n; i++) {
            if (t == 187) c->g.cards[sel[i].slot].force_pays_cost = 1;
            if (ST(c)->n_force >= GKMS_MAX_CARDS) UNSUP_RET(c, 0, );
            ST(c)->force_has_resolved[ST(c)->n_force] = 1;
            ST(c)->force[ST(c)->n_force++] = sel[i].slot;
        }
        return;
    }
    case 219: {
        if (e->enchant < 0 || !ench_row(c, e->enchant)) UNSUP_RET(c, t, );
        int64_t turn = e->has_turn ? e->turn : -1;
        int64_t count = e->has_count ? e->count : 0;
        int64_t in_turn = value;
        install_trigger(c, e->enchant, count > 0 ? count : -1, in_turn > 0 ? in_turn : -1,
                        turn <= 0 ? NONE : turn, 0, -1);
        if (c->unsup) return;
        if (S->n_tes) {
            if (S->playing_card < 0) UNSUP_RET(c, t, );
            S->tes[S->n_tes - 1].owner_card = S->playing_card;
        }
        return;
    }
    case 202:
        add_review(c, ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)S->review_consumption_sum)), 0);
        return;
    case 151: reduce_review(c, value); return;
    case 154: consume_parameter_buff_turns(c, value); return;
    case 1: {
        int64_t cnt = e->has_count ? e->count : 1;
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, value, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        return;
    }
    case 23: {
        int64_t cnt = e->has_count ? e->count : 1;
        int64_t bonus = ceil_f32(f32_div(f32_mul(S->lesson_buff_multiple, (double)(E_v2(e) * S->lesson_buff)), 1000.0));
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, bonus + value, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        return;
    }
    case 2: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t duration = add_parameter_buff_turns(c, e->turn);
        grant_parameter_buff_turns(c, duration);
        if (duration > 0) raise_direct_status_change(c, 2, duration);
        return;
    }
    case 19: case 125: {
        int64_t source = t == 19 ? S->block : S->aggressive;
        if (t == 19 && source <= 0) return;
        int64_t dep = ceil_f32(f32_div(f32_mul(f32((double)value), f32((double)source)), 1000.0));
        int64_t cnt = (e->has_count && e->count) ? e->count : 1;
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, dep, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        if (t == 19 && E_v2(e)) {
            int64_t consumed = ceil_f32(f32_div(f32_mul(f32((double)S->block), f32((double)E_v2(e))), 1000.0));
            consumed = imin(consumed, S->block);
            S->block -= consumed;
            S->block_consumption_sum += consumed;
        }
        return;
    }
    case 143: case 3: {
        double rate = t == 143 ? f32_div((double)(1000 + E_v2(e)), 1000.0) : 1.0;
        int64_t gain = calculate_add_block(c, value, rate);
        if (c->unsup) return;
        int64_t applied = imax(-S->block, gain);
        S->block += applied;
        S->addition_block += applied;
        if (applied > 0) raise_direct_status_change(c, 3, applied);
        return;
    }
    case 7: {
        int64_t before = S->stamina;
        S->stamina = imax(0, S->stamina - value);
        S->consumed_stamina += before - S->stamina;
        raise_stamina_reduced(c, 20, before - S->stamina, 0);
        return;
    }
    case 10: case 5: case 14: {
        int64_t up = apply_effect_light(c, e);
        if (up > 0) raise_direct_status_change(c, 10, up);
        return;
    }
    case 28: recover_stamina(c, value); return;
    case 59: {
        if (!e->has_count) UNSUP_RET(c, t, );
        if (S->n_play_card_cost_changes >= GKMS_MAX_SMALL) UNSUP_RET(c, t, );
        CostChange *cc = &S->play_card_cost_changes[S->n_play_card_cost_changes++];
        cc->value = value; cc->remaining = e->count; cc->search_id = e->search;
        return;
    }
    case 22: {
        int64_t ec = (e->has_count && e->count) ? e->count : -1;
        int64_t rem = (e->has_turn && e->turn >= 0) ? e->turn : NONE;
        if (e->enchant < 0) UNSUP_RET(c, t, );
        install_trigger(c, e->enchant, ec > 0 ? ec : -1, -1, rem, 0, S->_in_turn_start);
        return;
    }
    case 39: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t duration = e->turn;
        int grant_tc = S->_in_turn_start;
        int64_t expiry = duration == -1 ? INT_MAX32 : S->turn + duration - (grant_tc ? 1 : 0);
        int marker_none = grant_tc; int64_t marker = S->turn;
        int has_status = 0; int64_t status = 0;
        TupleList *L = &S->lesson_parameter_multiple_layers;
        for (int i = 0; i < L->n; i++) {
            const Num *o = L->v[i];
            if (o[1].tag != 0) UNSUP_RET(c, 0, );
            if (duration == -1) {
                if (o[1].i == INT_MAX32) {
                    if (L->len[i] > 3) { if (o[3].tag == 2) has_status = 0; else { has_status = 1; status = o[3].i; } }
                    break;
                }
                continue;
            }
            if (o[1].i == INT_MAX32) continue;
            int pt_none = !(L->len[i] > 2) || o[2].tag == 2;
            int64_t player_turn = pt_none ? 0 : o[2].i;
            int64_t remaining = o[1].i - S->turn + ((!pt_none && player_turn == S->turn) ? 0 : 1);
            if (remaining == duration) {
                expiry = o[1].i;
                marker_none = pt_none; marker = player_turn;
                if (L->len[i] > 3) { if (o[3].tag == 2) has_status = 0; else { has_status = 1; status = o[3].i; } }
                break;
            }
        }
        if (!has_status) {
            if (!S->has__lpm_status_seq) { S->has__lpm_status_seq = 1; S->_lpm_status_seq = 0; }
            S->_lpm_status_seq += 1;
            status = S->_lpm_status_seq;
        }
        if (L->n >= GKMS_MAX_LAYERS) UNSUP_RET(c, 0, );
        int i = L->n++;
        memset(L->v[i], 0, sizeof(L->v[i]));
        L->len[i] = 5;
        L->v[i][0].tag = 1; L->v[i][0].f = f32_div((double)value, 1000.0); L->v[i][0].i = 0;
        L->v[i][1].tag = 0; L->v[i][1].i = expiry; L->v[i][1].f = 0;
        if (marker_none) { L->v[i][2].tag = 2; L->v[i][2].i = 0; L->v[i][2].f = 0; }
        else { L->v[i][2].tag = 0; L->v[i][2].i = marker; L->v[i][2].f = 0; }
        L->v[i][3].tag = 0; L->v[i][3].i = status; L->v[i][3].f = 0;
        L->v[i][4].tag = 0; L->v[i][4].i = value; L->v[i][4].f = 0;
        if (S->_wire_snapshot_turn) UNSUP_RET(c, t, );
        S->lesson_parameter_multiple = lpm_from_statuses(c);
        int64_t cur = S->lesson_parameter_multiple_until_turn;
        int64_t a = (cur == NONE || cur == 0) ? expiry : cur;
        S->lesson_parameter_multiple_until_turn = a > expiry ? a : expiry;
        return;
    }
    case 18: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t duration = e->turn;
        int64_t bru = S->block_restriction_until_turn;
        int live = S->block_restriction && bru != NONE && (bru == -1 || bru >= S->turn);
        S->block_restriction_until_turn = debuff_stack(c, live ? bru : NONE,
            duration == -1 ? -1 : S->turn + duration - S->_in_turn_start, duration);
        S->block_restriction = 1;
        return;
    }
    case 31: add_review(c, value, 0); return;
    case 42: {
        int64_t gained = add_aggressive_value(c, value);
        S->aggressive = imax(0, S->aggressive + gained);
        if (gained > 0) raise_direct_status_change(c, 42, gained);
        return;
    }
    case 38: {
        if (!e->has_turn || !e->has_count) UNSUP_RET(c, t, );
        if (S->n_effect_play_count_buffs >= GKMS_MAX_SMALL) UNSUP_RET(c, t, );
        PlayCountBuff *b = &S->effect_play_count_buffs[S->n_effect_play_count_buffs++];
        b->search_id = e->search; b->repeat_count = value; b->remaining_uses = e->count;
        b->until_turn = e->turn == -1 ? -1 : S->turn + e->turn - S->_in_turn_start;
        return;
    }
    case 93: S->stamina_consumption_down_fix += value; return;
    case 98: {
        int64_t count = zlen(c, Z_HAND);
        int16_t all[GKMS_MAX_CARDS];
        int na = sy_hand_all(c, all);
        for (int i = 0; i < na; i++) {
            int s = all[i];
            if (c->g.cards[s].n_support_upgrade_ids) { c->g.cards[s].n_support_upgrade_ids = 0; refresh_card(c, s); }
        }
        if (c->R->synchro) sy_clear_fused(c);
        for (int i = 0; i < na; i++) zappend(c, Z_GRAVE, all[i]);
        c->g.zone_n[Z_HAND] = 0;
        int16_t out[64];
        c->effect_draws += draw_cards(c, count, out);
        return;
    }
    case 103: {
        if (e->chain == -1 && e->chain2 < 0) return;
        if (!e->has_v1 || e->chain < 0) UNSUP_RET(c, t, );
        int64_t tt = S->turn + e->v1;
        TimerQueue *q = &S->effect_timer_queue;
        int k = -1;
        for (int i = 0; i < q->n; i++) if (q->turn[i] == tt) k = i;
        if (k < 0) { if (q->n >= GKMS_MAX_SMALL) UNSUP_RET(c, t, ); k = q->n++; q->turn[k] = tt; q->nids[k] = 0; }
        if (q->nids[k] + (e->chain2 >= 0 ? 2 : 1) > GKMS_MAX_TIMER_IDS) UNSUP_RET(c, t, );
        int ind = c->indirect_depth != 0;
        int serial = ++c->forecast_serial;
        c->timer_serial[k][q->nids[k]] = serial;
        if (e->chain2 >= 0) c->timer_serial[k][q->nids[k] + 1] = serial;
        q->ids[k][q->nids[k]++] = ind ? ~e->chain : e->chain;
        if (e->chain2 >= 0) q->ids[k][q->nids[k]++] = ind ? ~e->chain2 : e->chain2;
        return;
    }
    case 190: {
        if (e->chain == -1 && e->chain2 < 0) return;
        if (e->chain < 0) UNSUP_RET(c, t, );
        TimerQueue *q = &S->end_turn_timer_queue;
        if (q->n < 0) q->n = 0;
        int k = -1;
        for (int i = 0; i < q->n; i++) if (q->turn[i] == S->turn) k = i;
        if (k < 0) { if (q->n >= GKMS_MAX_SMALL) UNSUP_RET(c, t, ); k = q->n++; q->turn[k] = S->turn; q->nids[k] = 0; }
        if (q->nids[k] + (e->chain2 >= 0 ? 2 : 1) > GKMS_MAX_TIMER_IDS) UNSUP_RET(c, t, );
        int ind = c->indirect_depth != 0;
        q->ids[k][q->nids[k]++] = ind ? ~e->chain : e->chain;
        if (e->chain2 >= 0) q->ids[k][q->nids[k]++] = ind ? ~e->chain2 : e->chain2;
        return;
    }
    case 189: {
        if (S->enthusiastic_buff > 0) extend_enthusiastic(c, value);
        return;
    }
    case 117: {
        int64_t rec = ceil_f32(f32_div(f32_mul((double)S->max_stamina, (double)value), 1000.0));
        recover_stamina(c, rec);
        return;
    }
    case 132: {
        int64_t cnt = e->has_count ? e->count : 1;
        double scale = f32_div((double)(1000 + E_v2(e)), 1000.0);
        if (!e->has_v2) UNSUP_RET(c, t, );
        for (int64_t i = 0; i < cnt; i++) {
            int64_t calc = calculate_adding_parameter(c, value, 1, scale);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        return;
    }
    case 119: S->block += value; S->addition_block += value; return;
    case 66: if (!e->has_count) UNSUP_RET(c, t, ); S->anti_debuff += e->count; return;
    case 69: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t duration = e->turn;
        int64_t u = S->stamina_consumption_add_until_turn;
        int live = S->stamina_consumption_add && u != NONE && (u == -1 || u >= S->turn);
        if (!live) S->stamina_consumption_add_active_from_turn = S->turn;
        S->stamina_consumption_add_until_turn = debuff_stack(c, live ? u : NONE,
            duration == -1 ? -1 : S->turn + duration - S->_in_turn_start, duration);
        S->stamina_consumption_add = 1;
        return;
    }
    case 199: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t duration = e->turn;
        int64_t u = S->buff_consumption_add_until_turn;
        int live = S->buff_consumption_add && u != NONE && (u == -1 || u >= S->turn);
        if (!live) S->buff_consumption_add_active_from_turn = S->turn;
        S->buff_consumption_add_until_turn = debuff_stack(c, live ? u : NONE,
            duration == -1 ? -1 : S->turn + duration - S->_in_turn_start, duration);
        S->buff_consumption_add = 1;
        return;
    }
    case 73: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t duration = e->turn;
        int64_t u = S->block_add_down_until_turn;
        int live = S->block_add_down && u != NONE && (u == -1 || u >= S->turn);
        S->block_add_down_until_turn = debuff_stack(c, live ? u : NONE,
            duration <= 0 ? -1 : debuff_until(c, duration), duration);
        S->block_add_down = 1;
        return;
    }
    case 78: {
        int64_t duration = e->has_turn ? e->turn : -1;
        int64_t until;
        if (duration == -1 || (S->has_panic_until_turn && S->panic_until_turn == -1)) {
            until = -1;
        } else if (!panic_active(c)) {
            until = debuff_until(c, duration);
        } else {
            int64_t remaining = imax(0, S->panic_until_turn - S->turn + 1);
            int64_t span = imax(remaining, duration);
            until = S->turn + span - 1;
        }
        S->has_panic_until_turn = 1;
        S->panic_until_turn = until;
        return;
    }
    case 86: {
        int64_t duration = e->has_turn ? e->turn : -1;
        int64_t current = S->stamina_recover_restriction_until_turn;
        int active = S->stamina_recover_restriction &&
            (current == NONE || current == -1 || S->turn <= current);
        int64_t until;
        if (duration == -1 || (active && current == -1)) until = -1;
        else if (active) {
            if (current == NONE) UNSUP_RET(c, t, );
            until = current + duration;
        } else until = S->turn + duration - S->_in_turn_start;
        S->stamina_recover_restriction = 1;
        S->has_stamina_recover_restriction_until_turn = 1;
        S->stamina_recover_restriction_until_turn = until;
        return;
    }
    case 105: {
        int64_t duration = e->has_turn ? e->turn : 0;
        int64_t until = duration <= 0 ? -1 : debuff_until(c, duration);
        TupleList *L = &S->lesson_debuff_layers;
        int at = -1;
        for (int i = 0; i < L->n; i++) {
            if (L->len[i] != 2 || L->v[i][0].tag != 0 || L->v[i][1].tag != 0)
                UNSUP_RET(c, t, );
            if (L->v[i][1].i == until) { at = i; break; }
        }
        if (at < 0) {
            if (L->n >= GKMS_MAX_LAYERS) UNSUP_RET(c, t, );
            at = L->n++; L->len[at] = 2;
            memset(L->v[at], 0, sizeof(L->v[at]));
            L->v[at][1].i = until;
        }
        L->v[at][0].i += value;
        S->lesson_debuff = add_fix_sum(L);
        return;
    }
    case 107: {
        S->block_add_down_fix = imax(0, S->block_add_down_fix + value);
        return;
    }
    case 106: {
        int64_t duration = e->has_turn ? e->turn : -1;
        int64_t u = S->gimmick_parameter_debuff_until_turn;
        int live = S->gimmick_parameter_debuff && u != NONE && (u == -1 || u >= S->turn);
        S->gimmick_parameter_debuff_until_turn = debuff_stack(c, live ? u : NONE,
            duration <= 0 ? -1 : debuff_until(c, duration), duration);
        S->gimmick_parameter_debuff = 1;
        return;
    }
    case 114: {
        int64_t duration = e->has_turn ? e->turn : -1;
        int64_t u = S->slump_until_turn;
        int live = S->slump && u != NONE && (u == -1 || u >= S->turn);
        S->slump_until_turn = debuff_stack(c, live ? u : NONE,
            duration <= 0 ? -1 : debuff_until(c, duration), duration);
        S->slump = 1;
        return;
    }
    case 115: {
        if (S->card_draw_down_layers.n >= GKMS_MAX_LAYERS) UNSUP_RET(c, t, );
        int64_t duration = e->has_turn ? e->turn : -1;
        int64_t until = duration <= 0 ? -1 : debuff_until(c, duration);
        TupleList *L = &S->card_draw_down_layers;
        memset(L->v[L->n], 0, sizeof(L->v[L->n]));
        L->len[L->n] = 2; L->v[L->n][0].tag = 0; L->v[L->n][0].i = value;
        L->v[L->n][1].tag = 0; L->v[L->n][1].i = until; L->n++;
        return;
    }
    case 113: {
        if (e->search < 0) UNSUP_RET(c, t, );
        int64_t duration = e->has_turn ? e->turn : 0;
        int64_t fresh = duration <= 0 ? -1 : debuff_until(c, duration);
        TupleList *L = &S->play_card_limits;
        int found = -1;
        int64_t old_until = NONE;
        for (int i = 0; i < L->n; i++) {
            if (L->len[i] < 2 || L->v[i][0].tag != 0 || L->v[i][1].tag != 0)
                UNSUP_RET(c, t, );
            if (L->v[i][0].i != e->search) continue;
            int64_t u = L->v[i][1].i;
            if (u == -1 || u >= S->turn) {
                if (found < 0 || u == -1 || (old_until != -1 && u > old_until)) {
                    found = i; old_until = u;
                }
            }
        }
        int64_t until = (found < 0) ? fresh : debuff_stack(c, old_until, fresh, duration);
        if (found >= 0) {
            for (int i = L->n - 1; i >= 0; i--)
                if (L->len[i] >= 2 && L->v[i][0].tag == 0 && L->v[i][0].i == e->search)
                    remove_timed_layer(L, i);
        }
        if (L->n >= GKMS_MAX_LAYERS) UNSUP_RET(c, t, );
        int i = L->n++;
        memset(L->v[i], 0, sizeof(L->v[i]));
        L->len[i] = 2;
        L->v[i][0].tag = 0; L->v[i][0].i = e->search; L->v[i][0].f = 0.0;
        L->v[i][1].tag = 0; L->v[i][1].i = until; L->v[i][1].f = 0.0;
        SmallStrIntMap *m = &S->debuff_since;
        if (found < 0) {
            int at = -1;
            for (int j = 0; j < m->n; j++) if (m->key[j] == -e->search - 2) { at = j; break; }
            if (at < 0) {
                if (m->n >= GKMS_MAX_MAP) UNSUP_RET(c, 113, );
                at = m->n++; m->key[at] = -e->search - 2;
            }
            S->_debuff_seq += 1; m->val[at] = S->_debuff_seq;
        }
        return;
    }
    case 148: {
        DebuffItem items[GK_MAX_DEBUFF_ITEMS];
        int n = collect_debuffs(c, items);
        if (n < 0) return;
        int take = value < 1 ? n : (value < n ? (int)value : n);
        for (int i = 0; i < take; i++) {
            if (items[i].kind == 1000) {
                int sid = items[i].row;
                for (int j = S->play_card_limits.n - 1; j >= 0; j--)
                    if (S->play_card_limits.v[j][0].i == sid) remove_timed_layer(&S->play_card_limits, j);
                debuff_sequence_remove(c, -sid - 2);
            } else {
                clear_debuff_status(c, items[i].kind);
            }
            if (c->unsup) return;
        }
        return;
    }
    case 129: {
        if (!e->has_turn) UNSUP_RET(c, t, );
        int64_t u = S->parameter_buff_multiple_until_turn;
        if (u != NONE && u >= S->turn) S->parameter_buff_multiple_until_turn = u + e->turn;
        else S->parameter_buff_multiple_until_turn = S->turn + e->turn - S->_in_turn_start;
        S->parameter_buff_multiple_per_turn = 1;
        return;
    }
    case 27: case 60: {
        int64_t amount = value;
        if (t == 60) {
            float r60 = (float)(value < 0 ? -value : value) / 1000.0f + 0.0001f;
            amount = (int64_t)(r60 * (float)S->max_stamina);
            if (value < 0) { recover_stamina(c, amount); return; }
            int64_t before60 = S->stamina;
            S->stamina = imin(S->max_stamina, imax(0, S->stamina - amount));
            S->consumed_stamina += before60 - S->stamina;
            if (!c->unsup) raise_stamina_reduced(c, 20, before60 - S->stamina, 0);
            return;
        }
        int64_t before = S->stamina;
        damage_stamina(c, amount, t == 60);
        if (!c->unsup) raise_stamina_reduced(c, 20, before - S->stamina, 0);
        return;
    }
    case 29: {
        int64_t count = e->has_count && e->count ? e->count : 1;
        for (int64_t i = 0; i < count; i++) add_parameter(c, value, permil);
        return;
    }
    case 48:
        if (S->idol_status_type != GK_STANCE_FULL_POWER) { S->idol_status_type = GK_STANCE_NONE; S->idol_status_step_count = 0; }
        return;
    case 51: case 207:
        if (t == 207 || !c->M->es_fp_reduce_cancel_disable) {
            if (anti_debuff_gate(c, 51) || c->unsup || S->full_power_point <= 0) return;
        }
        reduce_full_power_point(c, value);
        return;
    case 153: S->lesson_buff = imax(0, S->lesson_buff - value); return;
    case 180: consume_pbm_turns(c, value); return;
    case 152:
        for (int64_t i = 0, n = e->has_count && e->count ? e->count : 1; i < n; i++) S->aggressive = imax(0, S->aggressive - value);
        return;
    case 149: {
        int64_t gain = ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)S->aggressive));
        S->aggressive = imax(0, S->aggressive + gain);
        if (gain > 0) raise_direct_status_change(c, 42, gain);
        return;
    }
    case 130: {
        int64_t source = S->parameter_buff_remaining;
        int64_t gain = ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)source) - 0.0001);
        for (int64_t i = 0, n = e->has_count ? e->count : 1; i < n; i++)
            S->lesson_buff = imax(0, S->lesson_buff + add_lesson_buff_value(c, gain));
        S->parameter_buff_remaining = ceil_f32(f32_mul(1.0 - f32_div((double)E_v2(e), 1000.0), (double)source));
        return;
    }
    case 126: {
        int64_t source = S->block;
        int64_t gain = ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)source) - 0.0001);
        for (int64_t i = 0, n = e->has_count ? e->count : 1; i < n; i++) { add_review(c, gain, 0); if (c->unsup) return; }
        S->block = ceil_f32(f32_mul(1.0 - f32_div((double)E_v2(e), 1000.0), (double)source));
        return;
    }
    case 127: case 188: {
        int64_t raw = t == 127 ? ceil_f32(f32_div(f32_mul(f32((double)value), f32((double)S->review)), 1000.0))
            : ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)S->block_consumption_sum));
        int64_t gain = calculate_add_block(c, raw, 1.0);
        if (c->unsup) return;
        int64_t applied = imax(-S->block, gain);
        S->block += applied; S->addition_block += applied;
        return;
    }
    case 141: add_stance_lock(c, e->has_turn && e->turn ? e->turn : 1); return;
    case 142: case 144: case 186: {
        int64_t source = t == 142 ? S->stamina : t == 144 ? S->consumed_stamina : S->block_consumption_sum;
        int64_t added = ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)source) - (t == 186 ? 0.0 : 0.0001));
        for (int64_t i = 0, n = e->has_count ? e->count : 1; i < n; i++) {
            int64_t calc = calculate_adding_parameter(c, added, 0, 0.0);
            if (c->unsup) return;
            add_parameter(c, calc, permil);
        }
        if (t == 142) {
            int64_t remaining = ceil_f32(f32_mul(1.0 - f32_div((double)E_v2(e), 1000.0), (double)source));
            S->stamina = imax(0, imin(S->max_stamina, remaining));
            S->consumed_stamina += imax(0, source - S->stamina);
        }
        return;
    }
    case 158: case 159: case 163: {
        SRow rows[MAX_ROWS]; int n = get_search_card_list(c, e, S->playing_card, rows);
        if (c->unsup) return;
        if (t == 163) {
            double coef = f32(f32_div((double)value, 1000.0) + f32_mul(f32_div((double)E_v2(e), 1000.0), (double)n));
            int64_t added = ceil_f32(f32_mul(coef, (double)S->block) - 0.0001);
            for (int64_t i = 0, count = e->has_count ? e->count : 1; i < count; i++) {
                int64_t calc = calculate_adding_parameter(c, added, 0, 0.0);
                if (c->unsup) return;
                add_parameter(c, calc, permil);
            }
        } else {
            int64_t gain = value + ceil_f32(f32_mul(f32_div((double)E_v2(e), 1000.0), (double)n));
            if (t == 159) add_review(c, gain, 0);
            else S->lesson_buff = imax(0, S->lesson_buff + add_lesson_buff_value(c, gain));
        }
        return;
    }
    case 166: {
        SRow rows[MAX_ROWS]; int n = get_search_card_list(c, e, S->playing_card, rows);
        if (c->unsup) return;
        int64_t count = ceil_f32(f32_mul(f32_div((double)E_v2(e), 1000.0), (double)n));
        if (e->chain < 0 || count < 1) return;
        const Eff *chained = eff_ptr(c, e->chain); if (!chained) UNSUP_RET(c, e->chain, );
        for (int64_t i = 0; i < count; i++) { apply_effect(c, chained, permil); if (c->unsup) return; }
        return;
    }
    case 167: over_preservation(c); return;
    case 168: {
        int64_t source = S->lesson_buff;
        int64_t turns = add_parameter_buff_turns(c, ceil_f32(f32_mul(f32_div((double)value, 1000.0), (double)source) - 0.0001));
        grant_parameter_buff_turns(c, turns);
        S->lesson_buff = ceil_f32(f32_mul(1.0 - f32_div((double)E_v2(e), 1000.0), (double)source));
        return;
    }
    case 170: {
        TupleList *L = &S->enthusiastic_additive_layers;
        int endless = !e->has_turn || e->turn == -1;
        int64_t duration = endless ? -1 : e->turn;
        int gain = (int)E_v1(e);
        for (int i = 0; i < L->n; i++) {
            if (L->len[i] < 2 || L->v[i][0].tag != 0) UNSUP_RET(c, t, );
            const Num *u = &L->v[i][1];
            int match = endless ? u->tag == 2
                : (u->tag == 0 && u->i >= S->turn && layer_left(c, u->i, L->len[i] > 2 ? L->v[i][2].i : 0) == duration);
            if (match) { L->v[i][0].i += gain; return; }
        }
        if (L->n >= GKMS_MAX_LAYERS) UNSUP_RET(c, t, );
        int i = L->n++; L->len[i] = 3;
        memset(L->v[i], 0, sizeof(L->v[i]));
        L->v[i][0].i = gain;
        if (endless) L->v[i][1].tag = 2;
        else L->v[i][1].i = S->turn + duration - S->_in_turn_start;
        L->v[i][2].i = fresh_mark(c);
        return;
    }
    case 171: {
        int64_t duration = e->has_turn ? e->turn : 0;
        grant_timed_gain(c, 6, 0, 0.0, value, duration);
        return;
    }
    case 155: case 172: case 173: {
        TupleList *L = t == 155 ? &S->lesson_parameter_multiple_down_layers
                     : t == 172 ? &S->full_power_lesson_multiple_additive_layers
                                : &S->concentration_lesson_multiple_additive_layers;
        int64_t duration = e->has_turn && e->turn > 0 ? e->turn : 0;
        if (!fold_layer(c, L, 1, f32_div((double)value, 1000.0), 0, duration, 0)) return;
        double sum = 0.0; for (int j = 0; j < L->n; j++) sum += num_as_double(&L->v[j][0]);
        if (t == 155) S->lesson_parameter_multiple_down = f32(sum);
        else if (t == 172) S->full_power_lesson_multiple_additive = f32(sum);
        else S->concentration_lesson_multiple_additive = f32(sum);
        return;
    }
    case 178: {
        TupleList *L = &S->full_power_point_additive_layers;
        const int64_t turns = e->has_turn && e->turn > 0 ? e->turn : -1;
        for (int j = 0; j < L->n; j++) if (L->v[j][0].i == turns) {
            L->v[j][1].i += value;
            S->full_power_point_additive_permil += value;
            return;
        }
        if (L->n >= GKMS_MAX_LAYERS) UNSUP_RET(c, t, );
        int i = L->n++; L->len[i] = 3;
        memset(L->v[i], 0, sizeof(L->v[i]));
        L->v[i][0].i = turns;
        L->v[i][1].i = value;
        S->full_power_point_additive_permil += value;
        return;
    }
    case 183: {
        int64_t saved = S->enthusiastic_buff;
        S->enthusiastic_buff = ceil_f32(f32_mul(f32((double)saved), f32_div((double)(1000 + E_v2(e)), 1000.0)));
        Eff lesson = *e; lesson.type = 1; lesson.has_v2 = 0;
        apply_effect(c, &lesson, permil);
        S->enthusiastic_buff = saved;
        return;
    }
    case 150:
        for (int i = 0; i < S->n_tes; i++) {
            TES *ts = &S->tes[i];
            if (ts->limit_count >= 0 && in_list32(c->R->idol_enchants, c->R->n_idol_enchants, ts->enchant_id)) ts->limit_count += value;
        }
        return;
    case 195: {
        if (!e->has_turn || e->turn <= 0) UNSUP_RET(c, 195, );
        TupleList *L = &S->review_count_add_layers;
        if (!L->n && S->review_count_add) {
            int64_t u = S->review_count_add_until_turn;
            if (!S->has_review_count_add_until_turn || u == NONE) UNSUP_RET(c, 195, );
            memset(L->v[0], 0, sizeof(L->v[0]));
            L->n = 1; L->len[0] = 3; L->v[0][0].i = S->review_count_add; L->v[0][1].i = u;
        }
        if (!fold_layer(c, L, 0, 0.0, value, e->turn, 0)) return;
        sync_review_count_add(c);
        return;
    }

    case 63: S->extra_turns += 1; return;
    case 84: {
        int64_t duration = e->has_turn ? e->turn : 0;
        int64_t until = duration <= 0 ? -1 : debuff_until(c, duration);
        TupleList *L = &S->stamina_consumption_add_fix_layers;
        if (L->n >= GKMS_MAX_LAYERS) UNSUP_RET(c, t, );
        memset(L->v[L->n], 0, sizeof(L->v[L->n]));
        L->len[L->n] = 2;
        L->v[L->n][0].tag = 0; L->v[L->n][0].i = E_v1(e); L->v[L->n][0].f = 0.0;
        L->v[L->n][1].tag = 0; L->v[L->n][1].i = until; L->v[L->n][1].f = 0.0;
        L->n++;
        S->stamina_consumption_add_fix = add_fix_sum(L);
        return;
    }
    case 220: case 221: case 222: case 223:
        if (!c->R->synchro) UNSUP_RET(c, t, );
        return;
    case 224: case 225: case 226: case 227: case 228: case 229: case 230: case 231: {
        if (!c->R->synchro) UNSUP_RET(c, t, );
        int g = (t - 224) % 4;
        if (t < 228) { S->fusion_delta[g] += value; if (value > 0) sy_replan(c); }
        else S->fusion_delta[g] -= value;
        return;
    }
    case 233: case 234: case 235: case 236: {
        if (!c->R->synchro) UNSUP_RET(c, t, );
        SRow sel[MAX_ROWS];
        int n = get_search_card_list(c, e, S->playing_card, sel);
        if (c->unsup) return;
        for (int i = 0; i < n; i++) c->g.cards[sel[i].slot].p4_group = (int8_t)(t - 232);
        if (n) sy_replan(c);
        return;
    }
    case 237: {
        if (!c->R->synchro) UNSUP_RET(c, t, );
        if (S->meta_stage) return;
        int64_t total = (int64_t)c->R->limit_turn + S->extra_turns;
        S->meta_before_turns = (total * c->M->es_meta[0] + 500) / 1000;
        S->meta_stage = S->turn <= S->meta_before_turns ? 1 : 2;
        return;
    }
    }
    UNSUP_RET(c, t, );
}

// exam.py apply_card_effect
GK_FN void apply_card_effect(Core *c, const Resolved *card, const Eff *src, int64_t permil) {
    int t = src->type;
    int lesson_add = card->lesson_add && (t == 1 || t == 23 || t == 56 || t == 132 || t == 183);
    int64_t add = res_get(card->n_va, card->va_t, card->va_v, t);
    int64_t tadd = res_get(card->n_ta, card->ta_t, card->ta_v, t);
    int turn_add = tadd && src->has_turn && src->turn != -1;
    int count_add = card->lesson_count_add && (t == 1 || t == 56 || t == 132 || t == 183);
    c->card_own_depth++;
    if (!lesson_add && !add && !turn_add && !count_add) apply_effect(c, src, permil);
    else {
        Eff e = *src;
        if (lesson_add) { e.v1 = E_v1(&e) + card->lesson_add; e.has_v1 = 1; }
        if (add) { e.v1 = E_v1(&e) + add; e.has_v1 = 1; }
        if (turn_add) { e.turn = e.turn + tadd; }
        if (count_add) { e.count = ((e.has_count && e.count) ? e.count : 1) + card->lesson_count_add; e.has_count = 1; }
        apply_effect(c, &e, permil);
    }
    c->card_own_depth--;
}

// exam.py should_apply_card_effect
GK_FN int should_apply_card_effect(Core *c, const Link *L) {
    int32_t trig = L->trigger;
    int truthy = trig >= 0 && trig != c->M->sid_empty;
    if (truthy) {
        const TriggerRow *row = trig_row(c, trig);
        if (row) {
            int v = field_status_ok(c, row, &c->play_card);
            if (v >= 0) return v;
        }
    }
    if (trig == c->M->sid_trg_lb6) return ST(c)->lesson_buff >= 6;
    if (trig == c->M->sid_trg_trouble2) {
        int cnt = 0;
        static const int ZZ[4] = { Z_DECK, Z_HAND, Z_GRAVE, Z_HOLD };
        for (int k = 0; k < 4; k++)
            for (int i = 0; i < zlen(c, ZZ[k]); i++) if (rrow(c, &c->res[zget(c, ZZ[k], i)])->category == 3) cnt++;
        return cnt >= 2;
    }
    if (trig == c->M->sid_trg_pb4) UNSUP_RET(c, 0, 1);
    return 1;
}

// ---------------------------------------------------------------------------
// costs (exam.py pay costs / card_play_select) and play select/run/played
// ---------------------------------------------------------------------------
GK_FN int64_t card_cost_type(const Core *c, const Resolved *r) { return rrow(c, r)->cost_type; }

// exam.py calculate_buff_cost (CalculateBuffCost) -- with 強化状態コスト増加 (199) on, a status cost grows by
// examBuffConsumptionAddPermil in f32 and is rounded up; 198 (the Down half) has no master rows
GK_FN int64_t calc_buff_cost(const Core *c, int64_t value) {
    if (!ST(c)->buff_consumption_add || value <= 0) return value;
    double v = f32((double)value);
    v = f32_mul(v, f32_div((double)(c->M->es_buff_add_permil + 1000), 1000.0));
    return ceil_f32(v);
}
GK_FN void pay_card_status_cost(Core *c, const Resolved *r) {
    ExamStateP *S = ST(c);
    const CardRow *row = rrow(c, r);
    int ct = row->cost_type;
    int64_t cv = row->has_cost_value ? row->cost_value : 0;
    cv = imax(0, cv + (ct > 0 && ct < 8 ? r->scd[ct] : 0));
    if (ct != 5) cv = calc_buff_cost(c, cv);
    if (ct == 4) consume_parameter_buff_turns(c, cv);
    else if (ct == 1) S->lesson_buff -= cv;
    else if (ct == 3) S->aggressive = imax(0, S->aggressive - cv);
    else if (ct == 5) S->full_power_point = imax(0, S->full_power_point
                                                   - calc_buff_cost(c, imax(0, cv + r->cost_fpp_add - r->cost_fpp_reduce)));
    else if (ct == 2) reduce_review(c, cv);
    else if (ct == 6) consume_pbm_turns(c, cv);
}

GK_FN void pay_card_stamina_cost(Core *c, const CardView *pc) {
    ExamStateP *S = ST(c);
    S->_in_turn_start = 0;
    snap_take(c, &c->pending_card);
    snap_card_searches(c, &c->pending_card, &pc->r);
    if (c->unsup) return;
    const Resolved *r = &pc->r;
    const CardRow *row = rrow(c, r);
    int64_t force = row->has_force ? row->force : 0;
    int64_t stam = row->has_stamina ? row->stamina : 0;
    if (!force && !stam && row->cost_type) {
        if (S->idol_status_type == GK_STANCE_CONCENTRATION && S->idol_status_step_count >= 2) {
            int64_t before = S->stamina;
            S->stamina = imax(0, S->stamina - c->M->es_conc_penetrate_reduce2);
            S->consumed_stamina += before - S->stamina;
        }
        return;
    }
    const Card *inst = &c->g.cards[pc->slot];
    if (inst->has_panic_stamina_fix) {
        int64_t extra = (S->idol_status_type == GK_STANCE_CONCENTRATION && S->idol_status_step_count >= 2)
                        ? c->M->es_conc_penetrate_reduce2 : 0;
        int64_t before = S->stamina;
        damage_stamina(c, inst->panic_stamina_fix, force != 0);
        int64_t lost = imax(0, before - S->stamina - extra);
        raise_stamina_reduced(c, 20, lost, 0);
        raise_stamina_reduced(c, 24, lost, pc);
        return;
    }
    int active = -1;
    for (int i = 0; i < S->n_play_card_cost_changes; i++) {
        if (S->play_card_cost_changes[i].remaining <= 0) continue;
        const SearchRow *only = search_row(c, S->play_card_cost_changes[i].search_id);
        if (only && !matches_search(c, pc->slot, only)) continue;
        active = i; break;
    }
    int64_t cost;
    if (active >= 0) {
        cost = S->play_card_cost_changes[active].value;
        S->play_card_cost_changes[active].remaining -= 1;
    } else {
        if (force) force = imax(0, force - r->cost_pen_reduce);
        cost = force ? force : stam;
        int fpp = row->cost_type == 5;
        cost = imax(0, cost - (force ? 0 : r->cost_reduce) + ((!force && !fpp) ? r->cost_add : 0));
    }
    int64_t extra = (S->idol_status_type == GK_STANCE_CONCENTRATION && S->idol_status_step_count >= 2)
                    ? c->M->es_conc_penetrate_reduce2 : 0;
    int64_t before = S->stamina;
    damage_stamina(c, cost, force != 0);
    int64_t lost = imax(0, before - S->stamina - extra);
    raise_stamina_reduced(c, 20, lost, 0);
    raise_stamina_reduced(c, 24, lost, pc);
}

// exam.py card_play_select -> (sel2, sel23, sel25)
GK_FN void card_play_select(Core *c, const CardView *pc, int16_t *sel2, int *n2, int16_t *sel23, int *n23,
                            int16_t *sel25, int *n25) {
    ExamStateP *S = ST(c);
    S->_in_turn_start = 0;
    snap_take(c, &c->play_start);
    snap_card_searches(c, &c->play_start, &pc->r);
    if (c->unsup) return;
    c->play_card = c->pending_card.valid ? c->pending_card : c->play_start;
    c->pending_card.valid = 0;
    S->current_card_effect_repeat = 0;
    int kept = 0, chosen = -1;
    for (int i = 0; i < S->n_effect_play_count_buffs; i++) {
        PlayCountBuff b = S->effect_play_count_buffs[i];
        if (b.until_turn != -1 && S->turn > b.until_turn) continue;
        const SearchRow *sr = search_row(c, b.search_id);
        if (!sr) UNSUP_RET(c, b.search_id, );
        S->effect_play_count_buffs[kept] = b;
        if (search_match(c, sr, &pc->r, rrow(c, &pc->r)->id, -1, 0, pc->slot)) {
            if (chosen < 0 || b.repeat_count > S->effect_play_count_buffs[chosen].repeat_count) chosen = kept;
        }
        kept++;
    }
    if (chosen >= 0) {
        PlayCountBuff *cb = &S->effect_play_count_buffs[chosen];
        S->current_card_effect_repeat = cb->repeat_count;
        cb->remaining_uses -= 1;
        if (cb->remaining_uses <= 0) {
            for (int i = chosen; i + 1 < kept; i++) S->effect_play_count_buffs[i] = S->effect_play_count_buffs[i + 1];
            kept--;
        }
    }
    S->n_effect_play_count_buffs = kept;
    *n2 = select_triggers(c, 2, pc, 0, sel2);
    if (c->unsup) return;
    S->play_count += 1;
    if (c->phase_candidates[44]) for (int i = 0; i < S->n_tes; i++) {
        const TriggerRow *t = trig_row(c, S->tes[i].trigger);
        if (!t) UNSUP_RET(c, 0, );
        if (trig_has_phase(c, t, 44) && (!t->n_fst || field_status_ok(c, t, 0) != 0)
            && card_matches_trigger_search(c, t, pc)) {
            S->tes[i].interval44_count = (S->tes[i].has_interval44 ? S->tes[i].interval44_count : 0) + 1;
            S->tes[i].has_interval44 = 1;
        }
    }
    if (c->phase_candidates[23]) for (int i = 0; i < S->n_tes; i++) {
        const TriggerRow *t = trig_row(c, S->tes[i].trigger);
        if (trig_has_phase(c, t, 23)) {
            if (!card_matches_trigger_search(c, t, pc)) continue;
            if (t->n_fst && field_status_ok(c, t, 0) == 0) continue;
            if (S->tes[i].limit_count >= 0 && S->tes[i].spent_total >= S->tes[i].limit_count) continue;
            S->tes[i].phase_count += 1;
        }
    }
    card_enchant_play_interval(c, pc); if (c->unsup) return;
    *n23 = select_triggers(c, 23, pc, S->play_count, sel23);
    if (c->phase_candidates[25]) for (int i = 0; i < S->n_tes; i++) {
        const TriggerRow *t = trig_row(c, S->tes[i].trigger);
        if (!t) UNSUP_RET(c, S->tes[i].trigger, );
        if (trig_has_phase(c, t, 25) && card_matches_trigger_search(c, t, pc)) S->tes[i].phase_count += 1;
        if (c->unsup) return;
    }
    *n25 = select_triggers(c, 25, pc, S->turn_play_count + 1, sel25);
}

// exam.py _clear_card_played_skips
GK_FN void clear_card_played_skips(Core *c) {
    ExamStateP *S = ST(c);
    for (uint64_t bits = c->phase_candidates[3]; bits; bits &= bits - 1) {
        int i = GK_CTZ64(bits);
        TES *ts = &S->tes[i];
        if (ts->remaining_turns == 0 || !ts->skip_card_played_once || c->tes_row[i] < 0) continue;
        ts->skip_card_played_once = 0;
    }
}

// the play's record -- the last played card's category (field 19 / 20, read by play-start triggers only: phases 2 and
// 27) and the turn's play count (phase 25 at play start).
GK_FN void card_play_record(Core *c, const CardView *pc) {
    ExamStateP *S = ST(c);
    S->last_played_card_category = rrow(c, &pc->r)->category;
    S->turn_play_count += 1;
}

// exam.py card_played; ``recorded``: card_play_record has run for this play (a hand play)
GK_FN void card_played(Core *c, const CardView *pc, int64_t permil, int recorded) {
    ExamStateP *S = ST(c);
    if (!recorded) S->last_played_card_category = rrow(c, &pc->r)->category;
    card_enchant_after_play(c, pc); if (c->unsup) return;
    if (!recorded) S->turn_play_count += 1;
    if (S->has_score_limit && S->score >= S->score_limit) { S->current_card_effect_repeat = 0; return; }
    int16_t sel3[MAX_SEL], sel44[MAX_SEL];
    int n3 = select_triggers(c, 3, pc, 0, sel3); if (c->unsup) return;
    int n44 = select_triggers(c, 44, pc, S->play_count, sel44); if (c->unsup) return;
    run_triggers(c, sel3, n3, 3, pc, 0, permil); if (c->unsup) return;
    run_triggers(c, sel44, n44, 44, pc, S->play_count, permil); if (c->unsup) return;
    clear_card_played_skips(c);
    S->current_card_effect_repeat = 0;
}

// ---------------------------------------------------------------------------
// session helpers
// ---------------------------------------------------------------------------
GK_FN int64_t parameter_permil(Core *c) {
    int64_t pt = ST(c)->current_parameter_type;
    if (pt <= 0 || pt > 3 || !c->R->has_permil[pt]) return NONE;
    return c->R->permil[pt];
}

// the zones before as slot bit sets (a slot is < GKMS_MAX_CARDS = 64)
GK_FN int move_zone(int k) { return k == 0 ? Z_HAND : k == 1 ? Z_GRAVE : Z_LOST; }
GK_OUTLINE uint64_t zone_mask(const Core *c, int k) {
    uint64_t m = 0;
    for (int i = 0; i < zlen(c, move_zone(k)); i++) m |= uint64_t(1) << (c->g.zone[move_zone(k)][i] & 63);
    return m;
}
// exam_session.py _dispatch_card_moves, one zone.
GK_FN void dispatch_zone_moves(Core *c, int k, uint64_t before, int64_t permil, uint64_t quiet = 0) {
    int z = move_zone(k);
    if (!c->phase_candidates[29 + k]) {
        for (int i = 0; i < zlen(c, z); i++) {
            int s = zget(c, z, i);
            if (!((before >> (s & 63)) & 1)) { refresh_card(c, s); if (c->unsup) return; }
        }
        return;
    }
    int16_t entered[GKMS_MAX_ZONE]; int ne = 0;
    for (int i = 0; i < zlen(c, z); i++) {
        int s = zget(c, z, i);
        if (!((before >> (s & 63)) & 1)) entered[ne++] = (int16_t)s;
    }
    for (int i = 0; i < ne; i++) {
        CardView pv; pv.slot = entered[i];
        resolved_copy(&pv.r, refresh_card(c, entered[i]));
        if ((quiet >> (entered[i] & 63)) & 1) continue;
        dispatch(c, 29 + k, &pv, 0, permil);
        if (c->unsup) return;
    }
}
GK_FN void dispatch_card_moves(Core *c, uint64_t hand, uint64_t grave, uint64_t lost, int64_t permil) {
    uint64_t quiet = c->lost_by_enchant;
    c->lost_by_enchant = 0;
    dispatch_zone_moves(c, 0, hand, permil); if (c->unsup) return;
    dispatch_zone_moves(c, 1, grave, permil); if (c->unsup) return;
    dispatch_zone_moves(c, 2, lost, permil, quiet);
    if (c->unsup || !c->sy_removed) return;
    uint64_t moved = c->sy_removed;
    c->sy_removed = 0;
    for (int i = 0; i < zlen(c, Z_REMOVE); i++) {
        int s = zget(c, Z_REMOVE, i);
        if (!((moved >> (s & 63)) & 1)) continue;
        CardView pv; pv.slot = s;
        resolved_copy(&pv.r, refresh_card(c, s));
        dispatch(c, 62, &pv, 0, permil);
        if (c->unsup) return;
    }
}

// the effects run on the caller's view, not on a copy of it (688 bytes of local memory per play on the GPU).
GK_FN int once_play_effect_done(const Core *c, int slot, const Eff *e) {
    const ExamStateP *S = &c->g.st;
    for (int i = 0; i < S->n_tes; i++)
        if (S->tes[i].owner_card == slot && S->tes[i].enchant_id == e->enchant) return 1;
    return 0;
}
GK_FN void run_card_effects(Core *c, const CardView *pc, int64_t permil, int skip_once) {
    ExamStateP *S = ST(c);
    Resolved *view = const_cast<Resolved *>(&pc->r);
    int64_t lesson_add0 = view->lesson_add;
    Resolved *outer = c->playing_resolved;
    c->playing_resolved = view;
    int64_t reps = 1 + S->current_card_effect_repeat;
    for (int64_t rep = 0; rep < reps; rep++) {
        for (int i = 0; i < pc->r.n_play; i++) {
            const Link *L = &pc->r.play[i];
            if (L->once && (skip_once || rep > 0)) continue;
            const Eff *e = eff_ptr(c, L->effect);
            if (!e) UNSUP_RET(c, L->effect, );
            if (L->once && once_play_effect_done(c, pc->slot, e)) continue;
            if (!should_apply_card_effect(c, L)) continue;
            if (c->unsup) return;
            apply_card_effect(c, &pc->r, e, permil);
            if (c->unsup) return;
        }
    }
    c->playing_resolved = outer;
    view->lesson_add = lesson_add0;
}

GK_FN void force_play(Core *c, int slot, int turn_start);

// a card drawn by one of them queues its own and runs in this same loop
GK_FN void drain_move_effects(Core *c) {
    GkmsState *g = &c->g;
    if (g->d_n_pending_move <= 0) return;
    ST(c)->playing_card = -1;
    c->playing_resolved = 0;
    c->playing_resolved_valid = 0;
    int64_t permil = parameter_permil(c);
    while (g->d_n_pending_move > 0) {
        int32_t n = g->d_pending_move_n[0], eff[4];
        for (int k = 0; k < n && k < 4; k++) eff[k] = g->d_pending_move_eff[0][k];
        if (c->blog) { int32_t ms = g->d_pending_move_slot[0];
                       bind_event(c, 4, g->cards[ms].produce_card_id, (int32_t)g->cards[ms].current_upgrade_count); }
        for (int i = 0; i + 1 < g->d_n_pending_move; i++) {
            g->d_pending_move_slot[i] = g->d_pending_move_slot[i + 1];
            g->d_pending_move_n[i] = g->d_pending_move_n[i + 1];
            for (int k = 0; k < 4; k++) g->d_pending_move_eff[i][k] = g->d_pending_move_eff[i + 1][k];
        }
        g->d_n_pending_move--;
        for (int k = 0; k < n && k < 4; k++) {
            const Eff *e = eff_ptr(c, eff[k]);
            if (!e) UNSUP_RET(c, k, );
            apply_effect(c, e, permil);
            if (c->unsup) return;
        }
    }
}

// exam_session.py ExamSession ``turn_start`` -- start_turn's drain, whose plays are still in the turn-start flow
// (force_play)
GK_FN void drain_force_plays(Core *c, int turn_start) {
    while (ST(c)->n_force > 0) {
        int s = ST(c)->force[0];
        for (int i = 0; i + 1 < ST(c)->n_force; i++) {
            ST(c)->force[i] = ST(c)->force[i + 1];
            ST(c)->force_has_resolved[i] = ST(c)->force_has_resolved[i + 1];
        }
        ST(c)->n_force--;
        force_play(c, s, turn_start);
        if (c->unsup) return;
    }
}

// exam_session.py force_play
GK_FN void force_play(Core *c, int slot, int turn_start) {
    ExamStateP *S = ST(c);
    int keep_ts = turn_start && S->_in_turn_start;
    static const int ZO[5] = { Z_HAND, Z_DECK, Z_GRAVE, Z_LOST, Z_HOLD };
    int in_pile = 0;
    for (int k = 0; k < 5; k++) { int i = zfind(c, ZO[k], slot); if (i >= 0) { zpop(c, ZO[k], i); in_pile = 1; break; } }
    if (in_pile) c->g.cards[slot].type9_moved = -1;
    CardView pv; pv.slot = slot; resolved_copy(&pv.r, refresh_card(c, slot));
    int outer = S->playing_card;
    S->playing_card = slot;
    c->live_hand_valid = 1; c->n_live_hand = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int hs = zget(c, Z_HAND, i); if (hs == slot) continue;
        if (c->n_live_hand >= 16) UNSUP_RET(c, 156, );
        c->live_hand[c->n_live_hand++] = c->g.cards[hs].number;
    }
    uint64_t zb0 = zone_mask(c, 0), zb1 = zone_mask(c, 1), zb2 = zone_mask(c, 2);
    int64_t permil = parameter_permil(c);
    if (c->g.cards[slot].force_pays_cost > 0) {
        c->g.cards[slot].force_pays_cost = -1;
        pay_card_stamina_cost(c, &pv); if (c->unsup) return;
        if (keep_ts) S->_in_turn_start = 1;
        pay_card_status_cost(c, &pv.r); if (c->unsup) return;
    }
    int16_t s2[MAX_SEL], s23[MAX_SEL], s25[MAX_SEL]; int n2, n23, n25;
    card_play_select(c, &pv, s2, &n2, s23, &n23, s25, &n25); if (c->unsup) return;
    if (keep_ts) S->_in_turn_start = 1;
    run_triggers(c, s2, n2, 2, &pv, 0, permil); if (c->unsup) return;
    run_triggers(c, s25, n25, 25, &pv, S->turn_play_count + 1, permil); if (c->unsup) return;
    run_triggers(c, s23, n23, 23, &pv, S->play_count, permil); if (c->unsup) return;
    run_card_effects(c, &pv, permil, 1); if (c->unsup) return;
    card_played(c, &pv, permil, 0); if (c->unsup) return;
    if (in_pile) finish_play(c, slot);
    else reset_card_upgrades(c, slot);
    if (c->unsup) return;
    dispatch_card_moves(c, zb0, zb1, zb2, permil); if (c->unsup) return;
    S->playing_card = outer;
    if (c->blog) bind_event(c, 2, c->g.cards[slot].produce_card_id, (int32_t)c->g.cards[slot].current_upgrade_count);
}

// exam.py ExamState.end_turn
GK_FN void state_end_turn(Core *c, int explicit_, int64_t permil, int drain) {
    if (c->R->skip_clears_adds && explicit_ && (c->R->skip_clears_adds == 2 || ST(c)->turn_play_count == 0)) {
        ST(c)->playable_add_stock = 0;
        ST(c)->playable_add_passing = 0;
        ST(c)->turn_playable_adds = 0;
        sync_playable(ST(c));
    }
    ExamStateP *S = ST(c);
    int client_order = S->client_turn_end_order;
    int has_early = 0; int64_t early = 0;
    int score_clear = S->has_score_limit && S->score >= S->score_limit;
    if (client_order && explicit_ && !score_clear) {
        early = recover_stamina(c, c->M->es_turn_end_recovery); has_early = 1;
        if (c->unsup) return;
        dispatch(c, 41, 0, 0, permil); if (c->unsup) return;
    }
    dispatch(c, 5, 0, 0, permil); if (c->unsup) return;
    {
        TimerQueue *q = &S->end_turn_timer_queue;
        int k = -1;
        for (int i = 0; i < q->n; i++) if (q->turn[i] == S->turn) k = i;
        if (k >= 0) {
            int32_t ids[GKMS_MAX_TIMER_IDS]; int n = q->nids[k];
            for (int j = 0; j < n; j++) ids[j] = q->ids[k][j];
            for (int i = k; i + 1 < q->n; i++) {
                q->turn[i] = q->turn[i + 1]; q->nids[i] = q->nids[i + 1];
                for (int j = 0; j < GKMS_MAX_TIMER_IDS; j++) q->ids[i][j] = q->ids[i + 1][j];
            }
            q->n--;
            for (int j = 0; j < n; j++) {
                int ind = ids[j] < 0;
                int32_t id = ind ? ~ids[j] : ids[j];
                const Eff *e = eff_ptr(c, id); if (!e) UNSUP_RET(c, id, );
                if (ind) c->indirect_depth += 1;
                apply_effect(c, e, permil);
                if (ind) c->indirect_depth -= 1;
                if (c->unsup) return;
            }
        }
    }
    if (S->turn >= 2) { dispatch(c, 43, 0, S->turn, permil); if (c->unsup) return; }
    if (!client_order && S->turn_play_count == 0 && explicit_) { dispatch(c, 41, 0, 0, permil); if (c->unsup) return; }
    if (S->review >= 1) {
        int64_t v = ceil_f32(f32_mul(S->review_multiple, (double)S->review));
        int64_t n = 1 + (S->review_count_add > 0 ? S->review_count_add : 0);
        Eff e; memset(&e, 0, sizeof(e));
        e.id = -1; e.type = 1; e.v1 = v; e.has_v1 = 1; e.count = 1; e.has_count = 1;
        e.search = e.enchant = e.target_card = e.chain = e.chain2 = e.pick_ref = -1;
        e.pick_range_type = e.pick_count_type = e.move_pos = -1;
        for (int64_t k = 0; k < n; k++) {
            apply_effect(c, &e, permil);
            if (c->unsup) return;
        }
    }
    deck_end_turn(c);
    if (drain) { drain_force_plays(c, 0); if (c->unsup) return; }
    for (int i = 0; i < S->n_tes; i++) {
        if (S->tes[i].passing_turn_start && S->tes[i].remaining_turns != NONE) S->tes[i].remaining_turns -= 1;
        S->tes[i].passing_turn_start = 0;
    }
    int kept = 0;
    const int before = S->n_tes;
    for (int i = 0; i < S->n_tes; i++) if (S->tes[i].remaining_turns != 0) {
        c->tes_serial[kept] = c->tes_serial[i];
        S->tes[kept++] = S->tes[i];
    }
    S->n_tes = kept;
    GK_COUNT(turn_end_compactions, 1); GK_COUNT(turn_end_removed, before - kept);
    if (kept != before) rebuild_tes_index(c);
    int ka = 0;
    for (int i = 0; i < S->n_active_enchants; i++) {
        int32_t en = S->active_enchants[i], act = 0;
        for (int j = 0; j < S->n_tes; j++) if (S->tes[j].enchant_id == en) { act = 1; break; }
        if (act) S->active_enchants[ka++] = en;
    }
    S->n_active_enchants = ka;
    if (S->parameter_buff_passing_turn_start && S->parameter_buff_remaining > 0) S->parameter_buff_remaining -= 1;
    S->parameter_buff_passing_turn_start = 0;
    S->parameter_buff_until_turn = S->parameter_buff_remaining ? S->turn + S->parameter_buff_remaining : NONE;
    if (S->has_score_limit && S->score >= S->score_limit) {
        int64_t recovery = imax(0, c->R->limit_turn - S->turn + 1) * c->M->es_turn_end_recovery;
        S->stamina = imin(S->max_stamina, imax(0, S->stamina + recovery));
    }
    else if (explicit_ && has_early) {}
    else if (explicit_) { recover_stamina(c, c->M->es_turn_end_recovery); if (c->unsup) return; }
    if (S->review_turn_start > 0) {
        if (S->review_turn_end_reduce_lock_turn > 0) S->review_turn_end_reduce_lock_turn -= 1;
        else reduce_review(c, 1);
    }
    if (S->enthusiastic_kept.n && S->enthusiastic_buff > 0) {
        TupleList *L = &S->enthusiastic_kept;
        int64_t sum = 0;
        int k = 0;
        for (int i = 0; i < L->n; i++) {
            sum += L->v[i][1].i;
            if (L->v[i][0].i > 1) {
                int64_t e = L->v[i][0].i - 1, a = L->v[i][1].i;
                memset(L->v[k], 0, sizeof(L->v[k]));
                L->len[k] = 2; L->v[k][0].i = e; L->v[k][1].i = a;
                k++;
            }
        }
        S->enthusiastic_buff = sum;
        L->n = k;
    } else { S->enthusiastic_buff = 0; S->enthusiastic_kept.n = 0; }
    if (S->stance_lock_passing_turn_start && S->stance_lock_turns > 0) S->stance_lock_turns -= 1;
    S->stance_lock_passing_turn_start = 0;
    TupleList *F = &S->full_power_point_additive_layers;
    S->full_power_point_additive_permil = 0;
    for (int i = 0; i < F->n; ) {
        if (F->v[i][2].i && F->v[i][0].i > 0) F->v[i][0].i -= 1;
        if (F->v[i][0].i == 0) { remove_timed_layer(F, i); continue; }
        S->full_power_point_additive_permil += F->v[i][1].i;
        i++;
    }
    spend_playable_turn(S);
    (void)early;
}

// exam.py _apply_gimmick_rows_body.
GK_FN int gimmick_field_matches(Core *c, const GimmickRow *g) {
    if (g->field_status_type < 0) return 1;
    int r = field_status_one(c, g->field_status_type, g->field_status_value, g->field_status_check_type == 1,
                             g->field_status_search, 0, -1);
    if (c->unsup) return -1;
    if (r == FS_UNKNOWN) { UNSUP(c, g->field_status_type); return -1; }
    return r == 1;
}

GK_FN void apply_gimmick_rows(Core *c, int64_t turn, int pre_draw, int64_t permil) {
    if (c->R->n_gimmick_rows <= 0) return;
    if (!c->R->gimmick_rows) { UNSUP(c, turn); return; }
    int mode = c->blog ? bind_trace_mode(c, 3) : 0;
    c->indirect_depth += 1;
    for (int i = 0; i < c->R->n_gimmick_rows; i++) {
        const GimmickRow *g = &c->R->gimmick_rows[i];
        if (g->start_turn != turn || g->pre_draw != pre_draw) continue;
        int matched = gimmick_field_matches(c, g);
        if (c->unsup) break;
        if (!matched) continue;
        const Eff *e = eff_ptr(c, g->effect);
        if (!e) { UNSUP(c, g->effect); break; }
        apply_effect(c, e, permil);
        if (c->unsup) break;
        break;
    }
    c->indirect_depth -= 1;
    if (c->blog) bind_trace_mode(c, mode);
}

// exam.py start_turn (split="pre") + _refresh_tower_debuffs
GK_FN void state_start_turn_pre(Core *c, int64_t turn, int64_t cpt, int64_t permil) {
    ExamStateP *S = ST(c);
    S->_wire_snapshot_turn = 0;
    S->_in_turn_start = 1;
    S->turn = turn;
    if (S->meta_stage == 1 && turn > S->meta_before_turns) S->meta_stage = 2;
    clear_panic_stamina_fix(c);
    if (c->unsup) return;
    S->turn_playable_base = 1;
    S->turn_playable_adds = 0;
    sync_playable(S);
    tg_expire(c, turn); if (c->unsup) return;
    if (ano_turn_start_transition(c, permil) == 0 || c->unsup) return;
    S->stance_lock_passing_turn_start = 1;
    for (int i = 0; i < S->full_power_point_additive_layers.n; i++)
        S->full_power_point_additive_layers.v[i][2].i = 1;
    S->review_added_this_turn = 0;
    for (int i = 0; i < S->n_tes; i++) { S->tes[i].passing_turn_start = 1; S->tes[i].spent_in_turn = 0; }
    TupleList *lists[5] = { &S->lesson_buff_multiple_layers, &S->review_multiple_layers,
        &S->lesson_parameter_multiple_down_layers, &S->full_power_lesson_multiple_additive_layers,
        &S->concentration_lesson_multiple_additive_layers };
    double *targets[5] = { &S->lesson_buff_multiple, &S->review_multiple,
        &S->lesson_parameter_multiple_down, &S->full_power_lesson_multiple_additive,
        &S->concentration_lesson_multiple_additive };
    for (int k = 0; k < 5; k++) {
        TupleList *L = lists[k];
        if (L->n) {
            int kept = 0;
            for (int i = 0; i < L->n; i++) {
                const Num *ex = &L->v[i][1];
                int keep = ex->tag == 1 ? ((double)turn <= ex->f) : (turn <= ex->i);
                if (keep) { if (kept != i) { L->len[kept] = L->len[i]; for (int j = 0; j < GKMS_MAX_TUPLE; j++) L->v[kept][j] = L->v[i][j]; } kept++; }
            }
            L->n = kept;
            if (k < 2) tl_sum_total(L, targets[k]);
            else {
                double sum = 0.0;
                for (int i = 0; i < L->n; i++) sum += num_as_double(&L->v[i][0]);
                *targets[k] = f32(sum);
            }
        }
    }
    TupleList *P = &S->lesson_parameter_multiple_layers;
    if (P->n) {
        int kept = 0;
        for (int i = 0; i < P->n; i++) {
            if (P->v[i][1].tag != 0) UNSUP_RET(c, 0, );
            if (turn <= P->v[i][1].i) { if (kept != i) { P->len[kept] = P->len[i]; for (int j = 0; j < GKMS_MAX_TUPLE; j++) P->v[kept][j] = P->v[i][j]; } kept++; }
        }
        P->n = kept;
        S->lesson_parameter_multiple = lpm_from_statuses(c);
        int64_t mx = NONE;
        for (int i = 0; i < P->n; i++) if (mx == NONE || P->v[i][1].i > mx) mx = P->v[i][1].i;
        S->lesson_parameter_multiple_until_turn = mx;
    }
    S->parameter_buff_passing_turn_start = S->parameter_buff_remaining > 0;
    S->current_parameter_type = cpt;
    S->addition_block = 0; S->current_turn_total_add_parameter = 0;
    S->turn_play_count = 0;
    if (c->phase_candidates[25]) for (int i = 0; i < S->n_tes; i++) {
        const TriggerRow *t = trig_row(c, S->tes[i].trigger);
        if (t && trig_has_phase(c, t, 25)) S->tes[i].phase_count = 0;
    }
    S->stamina_consumption_down = S->stamina_consumption_down_until_turn != NONE && turn <= S->stamina_consumption_down_until_turn;
    S->stamina_consumption_add = S->stamina_consumption_add_active_from_turn != NONE && S->stamina_consumption_add_until_turn != NONE
        && S->stamina_consumption_add_active_from_turn <= turn
        && (S->stamina_consumption_add_until_turn == -1 || turn <= S->stamina_consumption_add_until_turn);
    S->buff_consumption_add = S->buff_consumption_add_active_from_turn != NONE && S->buff_consumption_add_until_turn != NONE
        && S->buff_consumption_add_active_from_turn <= turn
        && (S->buff_consumption_add_until_turn == -1 || turn <= S->buff_consumption_add_until_turn);
    S->block_restriction = S->block_restriction_until_turn == -1 ||
        (S->block_restriction_until_turn != NONE && turn <= S->block_restriction_until_turn);
    if (S->block_add_down_until_turn != NONE) {
        S->block_add_down = S->block_add_down_until_turn == -1 || turn <= S->block_add_down_until_turn;
        if (!S->block_add_down) S->block_add_down_until_turn = NONE;
    }
    if (S->slump_until_turn != NONE) {
        S->slump = S->slump_until_turn == -1 || turn <= S->slump_until_turn;
        if (!S->slump) S->slump_until_turn = NONE;
    }
    if (S->gimmick_parameter_debuff_until_turn != NONE) {
        S->gimmick_parameter_debuff = S->gimmick_parameter_debuff_until_turn == -1 || turn <= S->gimmick_parameter_debuff_until_turn;
        if (!S->gimmick_parameter_debuff) S->gimmick_parameter_debuff_until_turn = NONE;
    }
    TupleList *debuffs[4] = { &S->lesson_debuff_layers, &S->stamina_consumption_add_fix_layers,
        &S->card_draw_down_layers, &S->play_card_limits };
    for (int k = 0; k < 4; k++) {
        TupleList *L = debuffs[k];
        for (int i = 0; i < L->n; ) {
            int live = timed_layer_live(c, L, i, turn, k);
            if (c->unsup) return;
            if (!live) remove_timed_layer(L, i); else i++;
        }
    }
    S->lesson_debuff = add_fix_sum(&S->lesson_debuff_layers);
    S->stamina_consumption_add_fix = add_fix_sum(&S->stamina_consumption_add_fix_layers);
    S->lesson_parameter_multiple_depend_review_or_aggressive = S->has_lesson_parameter_multiple_depend_until_turn &&
        S->lesson_parameter_multiple_depend_until_turn != NONE && turn <= S->lesson_parameter_multiple_depend_until_turn;
    S->parameter_buff_multiple_per_turn = S->parameter_buff_multiple_until_turn != NONE && turn <= S->parameter_buff_multiple_until_turn;
    if (S->stamina_recover_add_until_turn != NONE && S->stamina_recover_add_until_turn != -1
        && turn > S->stamina_recover_add_until_turn) {
        S->stamina_recover_add = 0;
        S->stamina_recover_add_until_turn = NONE;
    }
    if (S->stamina_recover_restriction_until_turn != NONE
        && S->stamina_recover_restriction_until_turn != -1
        && turn > S->stamina_recover_restriction_until_turn) {
        S->stamina_recover_restriction = 0;
        S->stamina_recover_restriction_until_turn = NONE;
    } else if (S->stamina_recover_restriction_until_turn != NONE) {
        S->stamina_recover_restriction = S->stamina_recover_restriction_until_turn == -1
            || turn <= S->stamina_recover_restriction_until_turn;
    }
    if (S->review_count_add_layers.n) {
        TupleList *L = &S->review_count_add_layers;
        for (int i = L->n - 1; i >= 0; i--)
            if (L->v[i][1].tag == 0 && turn > L->v[i][1].i) remove_timed_layer(L, i);
        sync_review_count_add(c);
    } else if (S->review_count_add_until_turn != NONE && turn > S->review_count_add_until_turn) {
        S->review_count_add = 0;
        S->review_count_add_until_turn = NONE;
    }
    apply_gimmick_rows(c, turn, 0, permil);
    if (c->unsup) return;
    const int queued = S->n_force;
    dispatch(c, 4, 0, 0, permil);
    if (!c->unsup && S->n_force > queued) drain_force_plays(c, 1);
}

// exam_session.py start_turn (turn >= 2, split order)
GK_BIND void bind_turn_start(Core *c, int64_t turn) {
    BindLog *b = c->blog;
    const ExamStateP *S = ST(c);
    b->ts_valid = 1; b->ts_turn = turn; b->ts_score = S->score; b->ts_stamina = S->stamina; b->ts_review = S->review;
    b->ts_hand_n = zlen(c, Z_HAND);
    for (int i = 0; i < b->ts_hand_n; i++) b->ts_hand[i] = c->g.cards[zget(c, Z_HAND, i)].produce_card_id;
}

// exam.py load_start_statuses for the opening of a fresh state -- the status reset is a no-op there (measured on the
// fixtures), so only the rows run; the P items' TriggerEffect is all an opening holds
GK_FN void open_statuses(Core *c, const OpenDesc *d) {
    for (int i = 0; i < d->n_status; i++) {
        const OpenStatus *s = &d->status[i];
        if (s->type != 1) UNSUP_RET(c, s->type, );
        if (s->sid < 0) continue;
        install_trigger(c, s->sid, NONE, -1, s->a0 >= 0 ? s->a0 : NONE, 1, -1);
        if (c->unsup) return;
    }
}
// deck.py calculate_audition_turn_parameter_types (the opening's turn-type schedule, drawn from the RNG)
GK_FN void open_turn_types(Core *c, const OpenDesc *d) {
    if (d->n_values != 3) UNSUP_RET(c, d->n_values, );
    int64_t t[3], p[3];
    for (int i = 0; i < 3; i++) { t[i] = d->value_type[i]; p[i] = d->value_value[i]; }
    for (int i = 1; i < 3; i++)
        for (int j = i; j > 0 && p[j] > p[j - 1]; j--) {
            int64_t a = t[j]; t[j] = t[j - 1]; t[j - 1] = a; a = p[j]; p[j] = p[j - 1]; p[j - 1] = a;
        }
    int64_t limit = d->audition_limit_turn, mutable_turns = limit - 3, total = p[0] + p[1] + p[2];
    if (limit > GKMS_MAX_TURN_TYPES || limit < 0 || total <= 0 || p[1] + p[2] <= 0) UNSUP_RET(c, limit, );
    int64_t c0 = (int64_t)ceil(f32(f32(f32((double)mutable_turns) * f32((double)p[0])) / f32((double)total)));
    int64_t remaining = mutable_turns - c0;
    double c1v = f32(f32(f32((double)remaining) * f32((double)p[1])) / f32((double)(p[1] + p[2])));
    int64_t c1 = (int64_t)nearbyint(c1v);
    int64_t c2 = remaining - c1;
    if (c0 < 0 || c1 < 0 || c2 < 0 || c0 + c1 + c2 > GKMS_MAX_TURN_TYPES) UNSUP_RET(c, c0, );
    int64_t pool[GKMS_MAX_TURN_TYPES]; int np = 0;
    for (int64_t k = 0; k < c0; k++) pool[np++] = t[0];
    for (int64_t k = 0; k < c1; k++) pool[np++] = t[1];
    for (int64_t k = 0; k < c2; k++) pool[np++] = t[2];
    GkmsState *g = &c->g;
    for (int64_t i = 0; i < limit; i++) {
        int64_t rt = limit - i;
        if (rt <= 3) g->d_turn_parameter_types[i] = t[rt - 1];
        else {
            if (np <= 0) UNSUP_RET(c, i, );
            int idx = (int)rng_range(c, 0, np);
            g->d_turn_parameter_types[i] = pool[idx];
            for (int k = idx; k + 1 < np; k++) pool[k] = pool[k + 1];
            np--;
        }
    }
    g->d_has_turn_parameter_types = 1;
    g->d_n_turn_parameter_types = (int32_t)limit;
}
// exam.py dispatch_exam_start (phase 6 once; the split order raises 28 after the draw, as every turn)
GK_FN void open_dispatch_exam_start(Core *c) {
    ExamStateP *S = ST(c);
    if (S->_exam_start_dispatched) return;
    S->_exam_start_dispatched = 1;
    int64_t pt = S->current_parameter_type > 0 ? S->current_parameter_type : (c->R->has_cfg_pt ? c->R->cfg_pt : 0);
    int64_t pm = (pt >= 1 && pt <= 3 && c->R->has_permil[pt]) ? c->R->permil[pt] : NONE;
    dispatch(c, 6, 0, 0, pm);
}

GK_FN void session_start_turn(Core *c) {
    ExamStateP *S = ST(c);
    c->effect_draws = 0;
    c->g.s_turn += 1;
    int64_t turn = c->g.s_turn;
    const OpenDesc *op = c->open;
    int full_power_turn = turn > 1 && S->full_power_point >= 10 && S->stance_lock_turns == 0;
    if (op) { open_statuses(c, op); if (c->unsup) return; }
    apply_gimmick_rows(c, turn, 1, NONE); if (c->unsup) return;
    c->g.d_current_turn = turn;
    int first_turn = !c->g.d_initialized;
    if (first_turn && !op) UNSUP_RET(c, 0, );
    if (first_turn && op->has_param_values) {
        open_turn_types(c, op); if (c->unsup) return;
        for (int64_t k = 0; k < op->npc_advances; k++) rng_next(c);
    }
    int64_t pt = NONE;
    if (turn - 1 < c->R->n_cfg_turn_pt && c->R->cfg_turn_pt[turn - 1] != NONE) pt = c->R->cfg_turn_pt[turn - 1];
    else if (c->R->has_cfg_pt) pt = c->R->cfg_pt;
    if (pt == NONE && c->g.d_has_turn_parameter_types) {
        if (turn - 1 < c->g.d_n_turn_parameter_types) pt = c->g.d_turn_parameter_types[turn - 1];
        else if (c->g.d_n_turn_parameter_types) pt = c->g.d_turn_parameter_types[c->g.d_n_turn_parameter_types - 1];
    }
    if (pt == NONE) UNSUP_RET(c, 0, );
    c->g.d_current_parameter_type = pt;
    c->g.d_n_turn_used_support_ids = 0;
    deck_end_turn(c);
    if (first_turn) {
        if (!op->has_param_values) for (int64_t k = 0; k < op->prefix_advances; k++) rng_next(c);
        deck_shuffle(c);
        c->g.d_initialized = 1;
        open_dispatch_exam_start(c); if (c->unsup) return;
    }
    S->n_turn_start_hand_numbers = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        if (i >= GKMS_MAX_SMALL) UNSUP_RET(c, i, );
        S->turn_start_hand_numbers[S->n_turn_start_hand_numbers++] = c->g.cards[zget(c, Z_HAND, i)].number;
    }
    state_start_turn_pre(c, turn, pt, (pt >= 1 && pt <= 3 && c->R->has_permil[pt]) ? c->R->permil[pt] : NONE);
    if (c->unsup) return;
    int64_t draw_down = 0;
    for (int i = 0; i < S->card_draw_down_layers.n; i++)
        if (timed_layer_live(c, &S->card_draw_down_layers, i, turn, 115))
            draw_down += S->card_draw_down_layers.v[i][0].i;
    if (c->unsup) return;
    int16_t out[64];
    int drawn;
    if (first_turn && op->initial_mode != 0 && op->n_initial > 0) {
        int16_t init[16]; int ni = 0;
        if (op->initial_mode == 1) {
            for (int i = zlen(c, Z_DECK) - 1; i >= 0; i--) {
                int s = zget(c, Z_DECK, i);
                for (int k = 0; k < op->n_initial; k++)
                    if (s == op->initial[k]) { if (ni >= 16) UNSUP_RET(c, ni, ); init[ni++] = (int16_t)s; break; }
            }
            for (int k = 0; k < ni; k++)
                for (int i = 0; i < zlen(c, Z_DECK); i++) if (zget(c, Z_DECK, i) == init[k]) { zpop(c, Z_DECK, i); break; }
        } else if (op->initial_mode == 2) {
            for (int k = 0; k < op->n_initial; k++) {
                int at = -1;
                for (int i = 0; i < zlen(c, Z_DECK); i++) if (c->g.cards[zget(c, Z_DECK, i)].produce_card_id == op->initial[k]) { at = i; break; }
                if (at < 0) UNSUP_RET(c, op->initial[k], );
                if (ni >= 16) UNSUP_RET(c, ni, );
                init[ni++] = (int16_t)zpop(c, Z_DECK, at);
            }
        } else UNSUP_RET(c, op->initial_mode, );
        int64_t want = imax(0, c->g.d_turn_start_distribute - draw_down - ni);
        int64_t dc = imin(want, (int64_t)zlen(c, Z_DECK) + zlen(c, Z_GRAVE));
        int16_t all[80]; int na = 0;
        for (int k = 0; k < ni; k++) all[na++] = init[k];
        if (dc > 60) UNSUP_RET(c, dc, );
        int nd = deck_draw(c, (int)dc, all + na); na += nd;
        if (c->unsup) return;
        add_hand_difference(c, all, na);
        drawn = na;
    } else {
        drawn = draw_cards(c, imax(0, c->g.d_turn_start_distribute - draw_down), out);
    }
    if (c->unsup) return;
    if (full_power_turn) {
        int n = zlen(c, Z_HOLD);
        for (int i = 0; i < n; i++) out[i] = zget(c, Z_HOLD, i);
        c->g.zone_n[Z_HOLD] = 0;
        add_hand_difference(c, out, n); if (c->unsup) return;
    }
    if (turn >= 2 && panic_active(c) && drawn >= 1) apply_panic_command(c);
    if (c->unsup) return;
    for (int i = 0; i < zlen(c, Z_HAND); i++) refresh_card(c, zget(c, Z_HAND, i));
    S->n_turn_start_hand_numbers = 0;
    for (int i = 0; i < zlen(c, Z_HAND) && i < GKMS_MAX_SMALL; i++)
        S->turn_start_hand_numbers[S->n_turn_start_hand_numbers++] = c->g.cards[zget(c, Z_HAND, i)].number;
    if (zlen(c, Z_HAND) > GKMS_MAX_SMALL) UNSUP_RET(c, 0, );
    if (c->blog) bind_turn_start(c, turn);
    drain_move_effects(c); if (c->unsup) return;
    if (first_turn) { dispatch(c, 28, 0, 0, parameter_permil(c)); if (c->unsup) return; }
    dispatch(c, 27, 0, 0, parameter_permil(c)); if (c->unsup) return;
    {
        int outer = c->install_origin;
        if (S->n_pending_installs) c->install_origin = c->M->sid_memory;
        for (int i = 0; i < S->n_pending_installs; i++) {
            install_trigger(c, S->pending_installs[i], NONE, -1, NONE, 1, -1);
            if (c->unsup) return;
        }
        c->install_origin = outer;
        S->n_pending_installs = 0;
    }
    dispatch(c, 21, 0, turn, parameter_permil(c)); if (c->unsup) return;
    {
        TimerQueue *q = &S->effect_timer_queue;
        int k = -1;
        for (int i = 0; i < q->n; i++) if (q->turn[i] == turn) k = i;
        if (k >= 0) {
            int32_t ids[GKMS_MAX_TIMER_IDS]; int n = q->nids[k];
            for (int j = 0; j < n; j++) ids[j] = q->ids[k][j];
            for (int i = k; i + 1 < q->n; i++) {
                q->turn[i] = q->turn[i + 1]; q->nids[i] = q->nids[i + 1];
                memcpy(c->timer_serial[i], c->timer_serial[i + 1], sizeof c->timer_serial[i]);
                for (int j = 0; j < GKMS_MAX_TIMER_IDS; j++) q->ids[i][j] = q->ids[i + 1][j];
            }
            q->n--;
            int64_t pm = parameter_permil(c);
            int mode = c->blog ? bind_trace_mode(c, 2) : 0;
            for (int j = 0; j < n; j++) {
                int ind = ids[j] < 0;
                int32_t id = ind ? ~ids[j] : ids[j];
                const Eff *e = eff_ptr(c, id); if (!e) UNSUP_RET(c, id, );
                int gate = 0;
                if (ind) { c->indirect_depth += 1; gate = gate_open(c, 0); }
                apply_effect(c, e, pm);
                if (ind) { gate_close(c, gate); c->indirect_depth -= 1; }
                if (c->unsup) return;
            }
            if (c->blog) bind_trace_mode(c, mode);
        }
        S->review_turn_start = S->review;
    }
    if (turn >= 2) {
        dispatch(c, 22, 0, turn, parameter_permil(c)); if (c->unsup) return;
    }
    drain_force_plays(c, 1); if (c->unsup) return;
    S->review_turn_start = S->review;
    mark_playable_turn_start(S);
    if (S->parameter_buff_remaining > 0) S->parameter_buff_passing_turn_start = 1;
    for (int i = 0; i < S->n_tes; i++) S->tes[i].passing_turn_start = 1;
    for (int i = 0; i < S->full_power_point_additive_layers.n; i++) S->full_power_point_additive_layers.v[i][2].i = 1;
    S->turn_playable_start = S->playable;
}

// exam_session.py end_turn
GK_FN void session_end_turn(Core *c, int explicit_) {
    ST(c)->playing_card = -1;
    c->playing_resolved = 0;
    c->playing_resolved_valid = 0;
    if (c->blog) { c->blog->te_valid = 1; c->blog->te_turn = c->g.s_turn; c->blog->te_score = ST(c)->score; }
    uint64_t zb0 = zone_mask(c, 0), zb1 = zone_mask(c, 1), zb2 = zone_mask(c, 2);
    state_end_turn(c, explicit_, parameter_permil(c), 1); if (c->unsup) return;
    dispatch_card_moves(c, zb0, zb1, zb2, parameter_permil(c)); if (c->unsup) return;
    if (c->R->has_score_cap && !c->R->final_audition && ST(c)->score >= c->R->score_cap) c->g.s_cleared = 1;
    if (c->g.s_cleared || c->g.s_turn >= c->R->limit_turn + ST(c)->extra_turns) c->g.s_finished = 1;
    if (c->g.s_finished) c->g.p_stamina = ST(c)->stamina;
    if (c->blog) {
        BindLog *b = c->blog;
        b->te_stamina = ST(c)->stamina; b->te_block = ST(c)->block;
        b->te_addition_block = ST(c)->addition_block; b->te_consumed = ST(c)->consumed_stamina;
    }
}

// exam.py effect 6: ExamCardCreateId honours movePositionType; targetUpgradeCount rides in v1 (mcompile),
// pickCountMax is the count.
GK_FN void apply_card_create_id(Core *c, const Eff *e) {
    int64_t upg = e->has_v1 ? e->v1 : 0;
    int n = (e->has_pick_max && e->pick_max > 1) ? (int)e->pick_max : 1;
    int mp = e->move_pos < 0 ? 4 : e->move_pos;
    if (mp == 4) {
        int32_t pids[GKMS_MAX_CARDS]; int64_t ups[GKMS_MAX_CARDS];
        if (n > GKMS_MAX_CARDS) UNSUP_RET(c, n, );
        for (int i = 0; i < n; i++) { pids[i] = e->target_card; ups[i] = upg; }
        create_cards_deck_random_batch(c, pids, ups, n);
        return;
    }
    if (mp == 1) {
        int16_t hs[GKMS_MAX_CARDS];
        if (n > GKMS_MAX_CARDS) UNSUP_RET(c, n, );
        for (int i = 0; i < n; i++) { int s = new_card(c, e->target_card, upg); if (s < 0) return; hs[i] = (int16_t)s; }
        place_in_hand(c, hs, n);
        return;
    }
    for (int i = 0; i < n; i++) {
        if (mp == 2) { int s = new_card(c, e->target_card, upg); if (s < 0) return; zinsert(c, Z_DECK, 0, s); }
        else if (mp == 5) { int s = new_card(c, e->target_card, upg); if (s < 0) return; zappend(c, Z_GRAVE, s); }
        else UNSUP_RET(c, mp, );
        if (c->unsup) return;
    }
}

// exam_session.py playability_reason (== None)
GK_FN int sy_can_play_bundle(Core *c, int top);
GK_FN int can_play(Core *c, int slot) {
    ExamStateP *S = ST(c);
    if (c->R->synchro && c->g.cards[slot].fusion_top1 == 0 && sy_count(c, slot) > 1) return sy_can_play_bundle(c, slot);
    const Resolved *r = &c->res[slot];
    const CardRow *row = rrow(c, r);
    if (S->playable <= 0) return 0;
    GK_COUNT(can_play_calls,1);
    for (int i = 0; i < S->play_card_limits.n; i++) {
        if (S->play_card_limits.len[i] < 2 || S->play_card_limits.v[i][0].tag != 0 || S->play_card_limits.v[i][1].tag != 0)
            UNSUP_RET(c, 113, 0);
        if (!timed_layer_live(c, &S->play_card_limits, i, S->turn, 113)) continue;
        const SearchRow *sr = search_row(c, (int32_t)S->play_card_limits.v[i][0].i);
        if (!sr) UNSUP_RET(c, (int32_t)S->play_card_limits.v[i][0].i, 0);
        if (search_match(c, sr, r, c->g.cards[slot].produce_card_id, -1, 0, slot)) return 0;
    }
    if (r->play_trigger >= 0) {
        const TriggerRow *pt = trig_row(c, r->play_trigger);
        if (!pt) UNSUP_RET(c, r->play_trigger, 0);
        if (pt->n_fst && !field_statuses_hold(c, pt)) return 0;
    }
    int ct = row->cost_type;
    int64_t need = (row->has_cost_value ? row->cost_value : 0) + (ct > 0 && ct < 8 ? r->scd[ct] : 0);
    if (ct == 5) need += r->cost_fpp_add - r->cost_fpp_reduce;
    need = calc_buff_cost(c, imax(0, need));
    if (ct == 5 && S->full_power_point < need) return 0;
    int64_t avail = ct == 1 ? S->lesson_buff : ct == 2 ? S->review : ct == 3 ? S->aggressive
                  : ct == 4 ? S->parameter_buff_remaining : ct == 6 ? pbm_turns(c) : 0;
    if (ct >= 1 && ct <= 6 && ct != 5 && avail < imax(0, need)) return 0;
    if (ct) return 1;
    int64_t force = row->has_force ? row->force : 0;
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
    int64_t avail2 = S->stamina + (pen ? 0 : S->block);
    return total <= avail2;
}

// --------------------------------------------------------------------------- a bundle's play
// (ExecuteFusionBundleCardCommandImpl).
GK_FN int sy_card_conditions(Core *c, int slot) {
    ExamStateP *S = ST(c);
    const Resolved *r = &c->res[slot];
    for (int i = 0; i < S->play_card_limits.n; i++) {
        if (S->play_card_limits.len[i] < 2 || S->play_card_limits.v[i][0].tag != 0 || S->play_card_limits.v[i][1].tag != 0)
            UNSUP_RET(c, 113, 0);
        if (!timed_layer_live(c, &S->play_card_limits, i, S->turn, 113)) continue;
        const SearchRow *sr = search_row(c, (int32_t)S->play_card_limits.v[i][0].i);
        if (!sr) UNSUP_RET(c, (int32_t)S->play_card_limits.v[i][0].i, 0);
        if (search_match(c, sr, r, c->g.cards[slot].produce_card_id, -1, 0, slot)) return 0;
    }
    if (r->play_trigger >= 0) {
        const TriggerRow *pt = trig_row(c, r->play_trigger);
        if (!pt) UNSUP_RET(c, r->play_trigger, 0);
        if (pt->n_fst && !field_statuses_hold(c, pt)) return 0;
    }
    return 1;
}
// a card's status cost (type, amount) and its stamina cost base and penetration, as can_play reads them
GK_FN void sy_card_cost(Core *c, int slot, int *ct_out, int64_t *need_out, int64_t *base_out, int *pen_out) {
    const Resolved *r = &c->res[slot];
    const CardRow *row = rrow(c, r);
    int ct = row->cost_type;
    int64_t need = (row->has_cost_value ? row->cost_value : 0) + (ct > 0 && ct < 8 ? r->scd[ct] : 0);
    if (ct == 5) need += r->cost_fpp_add - r->cost_fpp_reduce;
    *ct_out = ct; *need_out = imax(0, need);
    int64_t force = row->has_force ? row->force : 0;
    int pen = force > 0;
    int64_t base = 0;
    if (!ct) {
        if (c->g.cards[slot].has_panic_stamina_fix) base = c->g.cards[slot].panic_stamina_fix;
        else if (pen) base = imax(0, force - r->cost_pen_reduce);
        else base = imax(0, (row->has_stamina ? row->stamina : 0) - r->cost_reduce + r->cost_add);
    }
    *base_out = base; *pen_out = pen;
}
GK_FN int sy_playable_members(Core *c, int top, int16_t *out) {
    int16_t mem[GKMS_MAX_CARDS];
    int nm = sy_members(c, top, mem), n = 0;
    for (int k = 0; k < nm; k++) {
        refresh_card(c, mem[k]); if (c->unsup) return 0;
        if (sy_card_conditions(c, mem[k])) out[n++] = mem[k];
        if (c->unsup) return 0;
    }
    return n;
}
GK_FN int sy_can_play_bundle(Core *c, int top) {
    ExamStateP *S = ST(c);
    if (S->playable <= 0) return 0;
    if (!sy_card_conditions(c, top)) return 0;
    int16_t cards[GKMS_MAX_CARDS];
    cards[0] = (int16_t)top;
    int n = 1 + sy_playable_members(c, top, cards + 1);
    if (c->unsup) return 0;
    int64_t need[8] = {0, 0, 0, 0, 0, 0, 0, 0}, base[2] = {0, 0};
    for (int k = 0; k < n; k++) {
        int ct, pen; int64_t nd, b;
        sy_card_cost(c, cards[k], &ct, &nd, &b, &pen);
        if (ct > 0 && ct < 8) need[ct] += nd;
        base[pen] += b;
    }
    for (int ct = 1; ct <= 6; ct++) {
        if (!need[ct]) continue;
        int64_t v = calc_buff_cost(c, need[ct]);
        int64_t avail = ct == 1 ? S->lesson_buff : ct == 2 ? S->review : ct == 3 ? S->aggressive
                      : ct == 4 ? S->parameter_buff_remaining : ct == 5 ? S->full_power_point : pbm_turns(c);
        if (avail < v) return 0;
    }
    int64_t total = 0;
    for (int pen = 0; pen < 2; pen++) {
        if (!base[pen]) continue;
        int64_t sd, bd;
        calculate_damage(c, base[pen], pen, 1, &sd, &bd);
        total += sd + bd;
    }
    if (S->idol_status_type == GK_STANCE_CONCENTRATION && S->idol_status_step_count >= 2 && total > 0)
        total -= c->M->es_conc_penetrate_reduce2;
    int64_t avail2 = S->stamina + (base[0] ? S->block : 0);
    return total <= avail2;
}

GK_FN void sy_session_play_bundle(Core *c, int index) {
    ExamStateP *S = ST(c);
    int top = zget(c, Z_HAND, index);
    int16_t mem[GKMS_MAX_CARDS], cards[GKMS_MAX_CARDS];
    int nm = sy_members(c, top, mem);
    cards[0] = (int16_t)top;
    int n = 1 + sy_playable_members(c, top, cards + 1);
    if (c->unsup) return;
    CardView pv; pv.slot = top; resolved_copy(&pv.r, refresh_card(c, top));
    S->playing_card = top;
    c->sy_playing_top = top;
    c->live_hand_valid = 1; c->n_live_hand = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int hs = zget(c, Z_HAND, i); if (hs == top) continue;
        if (c->n_live_hand >= 16) UNSUP_RET(c, 156, );
        c->live_hand[c->n_live_hand++] = c->g.cards[hs].number;
    }
    uint64_t zb0 = zone_mask(c, 0), zb1 = zone_mask(c, 1), zb2 = zone_mask(c, 2);
    zpop(c, Z_HAND, index);
    for (int k = 0; k < nm; k++) sy_unlink(c, mem[k]);
    c->g.cards[top].type9_moved = -1;
    for (int k = 0; k < nm; k++) c->g.cards[mem[k]].type9_moved = -1;
    int64_t permil = parameter_permil(c);
    for (int k = n - 1; k >= 0; k--) {
        CardView pk; pk.slot = cards[k]; resolved_copy(&pk.r, refresh_card(c, cards[k]));
        pay_card_stamina_cost(c, &pk); if (c->unsup) return;
    }
    int16_t s2[MAX_SEL], s23[MAX_SEL], s25[MAX_SEL]; int n2, n23, n25;
    card_play_select(c, &pv, s2, &n2, s23, &n23, s25, &n25); if (c->unsup) return;
    int64_t pbm0 = S->parameter_buff_multiple_until_turn == NONE ? 0 : imax(0, S->parameter_buff_multiple_until_turn - S->turn + 1);
    int64_t b0 = S->lesson_buff, b1 = S->parameter_buff_remaining, b2 = S->review, b3 = S->aggressive,
            b4 = S->full_power_point, b5 = pbm0;
    for (int k = n - 1; k >= 0; k--) {
        pay_card_status_cost(c, &c->res[cards[k]]); if (c->unsup) return;
    }
    snap_take(c, &c->consume);
    c->consume.fpp_sum = c->play_start.fpp_sum; c->consume.stance_cc = c->play_start.stance_cc;
    c->consume.conc_cc = c->play_start.conc_cc; c->consume.pres_cc = c->play_start.pres_cc;
    c->consume.fp_cc = c->play_start.fp_cc;
    c->consume.pbm_turns = c->play_start.pbm_turns;
    c->consume_valid = 1;
    int64_t pbm1 = S->parameter_buff_multiple_until_turn == NONE ? 0 : imax(0, S->parameter_buff_multiple_until_turn - S->turn + 1);
    int dropped = (S->lesson_buff < b0) | (S->parameter_buff_remaining < b1) << 1 | (S->review < b2) << 2 |
                  (S->aggressive < b3) << 3 | (S->full_power_point < b4) << 4 | (pbm1 < b5) << 5;
    static const int ET[6] = { 10, 2, 18, 22, 49, 129 };
    run_triggers(c, s2, n2, 2, &pv, 0, permil); if (c->unsup) return;
    run_triggers(c, s25, n25, 25, &pv, S->turn_play_count + 1, permil); if (c->unsup) return;
    run_triggers(c, s23, n23, 23, &pv, S->play_count, permil); if (c->unsup) return;
    dispatch(c, 60, &pv, 0, permil); if (c->unsup) return;
    for (int k = 0; k < n; k++) {
        S->playing_card = cards[k];
        if (k == 0) { run_card_effects(c, &pv, permil, 0); if (c->unsup) return; continue; }
        CardView pk; pk.slot = cards[k]; resolved_copy(&pk.r, refresh_card(c, cards[k]));
        run_card_effects(c, &pk, permil, 0); if (c->unsup) return;
    }
    S->playing_card = top;
    dispatch(c, 61, &pv, 0, permil); if (c->unsup) return;
    if (c->phase_candidates[34])
        for (int k = 0; k < 6; k++) if ((dropped >> k) & 1) { dispatch(c, 34, &pv, ET[k], permil); if (c->unsup) return; }
    c->consume_valid = 0;
    consume_card_play(S);
    finish_play(c, top); if (c->unsup) return;
    for (int k = 0; k < nm; k++) { finish_play(c, mem[k]); if (c->unsup) return; }
    card_play_record(c, &pv);
    drain_force_plays(c, 0); if (c->unsup) return;
    card_played(c, &pv, permil, 1); if (c->unsup) return;
    dispatch_card_moves(c, zb0, zb1, zb2, permil); if (c->unsup) return;
    if (c->blog) bind_event(c, 1, c->g.cards[top].produce_card_id, pv.r.row >= 0 ? (int32_t)c->M->cards[pv.r.row].upg : 0);
    drain_force_plays(c, 0); if (c->unsup) return;
    c->sy_playing_top = -1;
    if (c->R->has_score_cap && !c->R->final_audition && S->score >= c->R->score_cap) c->g.s_cleared = 1;
}

// exam_session.py play
GK_FN void session_play(Core *c, int index) {
    ExamStateP *S = ST(c);
    if (c->R->synchro && sy_count(c, zget(c, Z_HAND, index)) > 1) { sy_session_play_bundle(c, index); return; }
    int slot = zget(c, Z_HAND, index);
    CardView pv; pv.slot = slot; resolved_copy(&pv.r, refresh_card(c, slot));
    S->playing_card = slot;
    c->live_hand_valid = 1; c->n_live_hand = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int hs = zget(c, Z_HAND, i); if (hs == slot) continue;
        if (c->n_live_hand >= 16) UNSUP_RET(c, 156, );
        c->live_hand[c->n_live_hand++] = c->g.cards[hs].number;
    }
    uint64_t zb0 = zone_mask(c, 0), zb1 = zone_mask(c, 1), zb2 = zone_mask(c, 2);
    zpop(c, Z_HAND, index);
    c->g.cards[slot].type9_moved = -1;
    int64_t permil = parameter_permil(c);
    pay_card_stamina_cost(c, &pv); if (c->unsup) return;
    int16_t s2[MAX_SEL], s23[MAX_SEL], s25[MAX_SEL]; int n2, n23, n25;
    card_play_select(c, &pv, s2, &n2, s23, &n23, s25, &n25); if (c->unsup) return;
    int64_t pbm0 = S->parameter_buff_multiple_until_turn == NONE ? 0 : imax(0, S->parameter_buff_multiple_until_turn - S->turn + 1);
    int64_t b0 = S->lesson_buff, b1 = S->parameter_buff_remaining, b2 = S->review, b3 = S->aggressive,
            b4 = S->full_power_point, b5 = pbm0;
    pay_card_status_cost(c, &pv.r); if (c->unsup) return;
    snap_take(c, &c->consume);
    c->consume.fpp_sum = c->play_start.fpp_sum; c->consume.stance_cc = c->play_start.stance_cc;
    c->consume.conc_cc = c->play_start.conc_cc; c->consume.pres_cc = c->play_start.pres_cc;
    c->consume.fp_cc = c->play_start.fp_cc;
    c->consume.pbm_turns = c->play_start.pbm_turns;
    c->consume_valid = 1;
    int64_t pbm1 = S->parameter_buff_multiple_until_turn == NONE ? 0 : imax(0, S->parameter_buff_multiple_until_turn - S->turn + 1);
    int dropped = (S->lesson_buff < b0) | (S->parameter_buff_remaining < b1) << 1 | (S->review < b2) << 2 |
                  (S->aggressive < b3) << 3 | (S->full_power_point < b4) << 4 | (pbm1 < b5) << 5;
    static const int ET[6] = { 10, 2, 18, 22, 49, 129 };
    run_triggers(c, s2, n2, 2, &pv, 0, permil); if (c->unsup) return;
    run_triggers(c, s25, n25, 25, &pv, S->turn_play_count + 1, permil); if (c->unsup) return;
    run_triggers(c, s23, n23, 23, &pv, S->play_count, permil); if (c->unsup) return;
    run_card_effects(c, &pv, permil, 0); if (c->unsup) return;
    if (c->phase_candidates[34])
        for (int k = 0; k < 6; k++) if ((dropped >> k) & 1) { dispatch(c, 34, &pv, ET[k], permil); if (c->unsup) return; }
    c->consume_valid = 0;
    consume_card_play(S);
    finish_play(c, slot); if (c->unsup) return;
    card_play_record(c, &pv);
    drain_force_plays(c, 0); if (c->unsup) return;
    card_played(c, &pv, permil, 1); if (c->unsup) return;
    dispatch_card_moves(c, zb0, zb1, zb2, permil); if (c->unsup) return;
    if (c->blog) bind_event(c, 1, c->g.cards[slot].produce_card_id, pv.r.row >= 0 ? (int32_t)c->M->cards[pv.r.row].upg : 0);
    drain_force_plays(c, 0); if (c->unsup) return;
    if (c->R->has_score_cap && !c->R->final_audition && S->score >= c->R->score_cap) c->g.s_cleared = 1;
}

// exam_session.py use_drink
GK_FN void session_use_drink(Core *c, int index) {
    ST(c)->playing_card = -1;
    c->playing_resolved = 0;
    c->playing_resolved_valid = 0;
    int32_t drink = c->g.drinks[index];
    int32_t di = BY(c, 5, drink);
    if (di < 0) UNSUP_RET(c, drink, );
    int64_t permil = parameter_permil(c);
    uint64_t zb0 = zone_mask(c, 0), zb1 = zone_mask(c, 1), zb2 = zone_mask(c, 2);
    for (int i = index; i + 1 < c->g.n_drinks; i++) c->g.drinks[i] = c->g.drinks[i + 1];
    c->g.n_drinks--;
    ExamStateP *S = ST(c);
    S->_in_turn_start = 0;
    int32_t outer = c->install_origin;
    c->install_origin = c->M->sid_drink;
    const DrinkRow *d = &c->M->drinks[di];
    for (int i = 0; i < d->n_eff; i++) {
        const Eff *e = eff_ptr(c, P32(c, d->eff_off)[i]); if (!e) UNSUP_RET(c, 0, );
        apply_effect(c, e, permil);
        if (c->unsup) return;
    }
    c->install_origin = outer;
    dispatch(c, 17, 0, 0, permil); if (c->unsup) return;
    dispatch_card_moves(c, zb0, zb1, zb2, permil); if (c->unsup) return;
    drain_force_plays(c, 0); if (c->unsup) return;
    if (c->blog) bind_event(c, 3, drink, 0);
    if (c->R->has_score_cap && !c->R->final_audition && S->score >= c->R->score_cap) c->g.s_cleared = 1;
}

// the 移動時効果 of the cards a play or a drink moved into the hand, then the forced plays they queued, until neither is
// left -- the client re-plays a card whose 再演 a move effect set off at once, before the next action.
GK_FN void drain_moves_and_forced(Core *c) {
    while (c->g.d_n_pending_move > 0 || ST(c)->n_force > 0) {
        drain_move_effects(c); if (c->unsup) return;
        drain_force_plays(c, 0); if (c->unsup) return;
    }
}

// exam_session.py step.
GK_FN int session_step(Core *c, int kind, int index) {
    GK_COUNT(steps, 1);
    if (c->g.s_finished) return 2;
    int turn_over = 0;
    if (kind == 0) {
        if (index < 0 || index >= zlen(c, Z_HAND) || !can_play(c, zget(c, Z_HAND, index))) return c->unsup ? 1 : 2;
        session_play(c, index);
        if (c->unsup) return 1;
        drain_moves_and_forced(c);
        if (c->unsup) return 1;
        if (c->g.s_cleared || ST(c)->playable <= 0) { session_end_turn(c, 0); turn_over = 1; }
    } else if (kind == 1) {
        if (index < 0 || index >= c->g.n_drinks) return 2;
        session_use_drink(c, index);
        if (c->unsup) return 1;
        drain_moves_and_forced(c);
        if (c->unsup) return 1;
        if (c->g.s_cleared || ST(c)->playable <= 0) { session_end_turn(c, 0); turn_over = 1; }
    } else if (kind == 2) {
        session_end_turn(c, ST(c)->playable > 0);
        turn_over = 1;
    } else return 2;
    if (c->unsup) return 1;
    if (turn_over && !c->g.s_finished) session_start_turn(c);
    return c->unsup ? 1 : 0;
}

// exam_session.py legal_actions -> encoded (kind<<8 | index) the hand at its current resolution first, as the
// binding's list does (native_session legal_actions: gk_card_info refreshes each hand card before gk_legal).
GK_FN int legal_actions(Core *c, int32_t *out) {
    int n = 0;
    if (c->g.s_finished) return 0;
    int32_t prev_unsup = c->unsup, prev_info = c->unsup_info;
    for (int i = 0; i < zlen(c, Z_HAND); i++) refresh_card(c, zget(c, Z_HAND, i));
    if (c->R->synchro) for (int i = 0; i < zlen(c, Z_FUSED); i++) refresh_card(c, zget(c, Z_FUSED, i));
    c->unsup = prev_unsup; c->unsup_info = prev_info;
    for (int i = 0; i < zlen(c, Z_HAND); i++) if (can_play(c, zget(c, Z_HAND, i))) out[n++] = (0 << 8) | i;
    for (int i = 0; i < c->g.n_drinks; i++) out[n++] = (1 << 8) | i;
    out[n++] = 2 << 8;
    return n;
}

// import: portable state (+ recompute derived caches, reset transients)
GK_FN void core_import(Core *c, const GkmsState *g, const MasterDesc *M, const RoundDesc *R) {
    memcpy(&c->g, g, sizeof(GkmsState));
    c->forecast_serial = 0;
    memset(c->tes_serial, 0, sizeof c->tes_serial);
    memset(c->timer_serial, 0, sizeof c->timer_serial);
    c->M = M; c->R = R;
    c->phase_masks = GK_PHASE_MASKS(M);
    memset(c->grow_reg_at, 0xFF, sizeof(c->grow_reg_at));
    rebuild_tes_index(c);
    c->resolved_valid = 0;
    c->install_origin = -1; c->target_card = -1; c->indirect_depth = 0; c->permil_depth = 0; c->card_own_depth = 0; c->running_tes = -1;
    c->trigger_commands_enabled = 1; c->live_hand_valid = 0; c->playing_resolved = 0;
    c->support_rolls_off = 0;
    c->play_start.valid = 0; c->consume.valid = 0; c->consume_valid = 0;
    c->play_card.valid = 0; c->pending_card.valid = 0;
    c->unsup = 0; c->unsup_info = 0;
    c->sy_playing_top = -1; c->sy_removed = 0;
    c->lost_by_enchant = 0;
    c->sce_mask = 0;
    for (int i = 0; i < M->n_card_enchants; i++) {
        const CardEnchantRow *row = &M->card_enchants[i];
        if (row->trigger >= 0 && row->stance < 0 && -row->stance < 64) c->sce_mask |= uint64_t(1) << (-row->stance);
    }
    c->n_pa_sids = 0; c->pa_scanned = 0; c->pa_hold = 0;
    for (int i = 0; i < M->n_card_enchants && c->n_pa_sids >= 0; i++) {
        const CardEnchantRow *row = &M->card_enchants[i];
        if (row->trigger < 0) continue;
        const TriggerRow *t = trig_row(c, row->trigger);
        if (!t) continue;
        if (!trig_has_phase(c, t, 23)) {
            if (!trig_has_phase(c, t, 3)) continue;
            const SearchRow *sr = t->search >= 0 ? search_row(c, t->search) : 0;
            if (sr && sr->is_self) continue;
        }
        if (c->n_pa_sids >= 16) c->n_pa_sids = -1;
        else c->pa_sids[c->n_pa_sids++] = row->id;
    }
    for (int s = 0; s < c->g.n_cards; s++) {
        Card keep = c->g.cards[s];
        refresh_card(c, s);
        c->g.cards[s] = keep;
    }
}
