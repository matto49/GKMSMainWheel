// Copyright (C) 2026 花吹雪また一年P and contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 3: ExamState (src/neu/exam.py).
#pragma once
#include "core_deck.h"

#define ST(c) (&(c)->g.st)

GK_FN void sync_playable(ExamStateP *S) { S->playable = S->playable_add_stock + S->turn_playable_base; }
GK_FN void add_playable_value(ExamStateP *S, int64_t count) {
    if (S->playable_add_stock <= 0) { S->playable_add_stock = count; S->playable_add_passing = 0; }
    else S->playable_add_stock += count;
    S->turn_playable_adds += count;
    if (!S->turn_playable_base && S->playable_add_stock > 0) { S->turn_playable_base = 1; S->playable_add_stock -= 1; }
    sync_playable(S);
}
GK_FN void consume_card_play(ExamStateP *S) {
    if (S->playable_add_stock > 0) S->playable_add_stock -= 1;
    else S->turn_playable_base = 0;
    sync_playable(S);
}
GK_FN void mark_playable_turn_start(ExamStateP *S) { if (S->playable_add_stock > 0) S->playable_add_passing = 1; }
GK_FN void spend_playable_turn(ExamStateP *S) {
    if (S->playable_add_stock > 0 && S->playable_add_passing) S->playable_add_stock = 0;
    if (S->playable_add_stock <= 0) { S->playable_add_stock = 0; S->playable_add_passing = 0; }
    sync_playable(S);
}

#ifndef GK_STANCE_NONE
#define GK_STANCE_NONE 0
#define GK_STANCE_CONCENTRATION 1
#define GK_STANCE_PRESERVATION 2
#define GK_STANCE_FULL_POWER 3
#define GK_STANCE_LEISURELY 4
#define GK_STANCE_OVER_PRESERVATION 5
#endif

// a resolved card passed as ``playing_card`` to trigger code (a snapshot of the dict refresh_card returned; slot < 0
// = None)
struct CardView { int32_t slot; Resolved r; };

GK_FN int64_t cur_permil(const Core *c) {
    if (c->permil_depth <= 0) return NONE;
    return c->permil_none[c->permil_depth - 1] ? NONE : c->permil_stack[c->permil_depth - 1];
}

// exam.py _ambient_permil -- a dispatch raised inside an effect takes the effect's bonus, else the one of the turn's
// parameter type (exam_session.py parameter_permil)
GK_FN int64_t ambient_permil(const Core *c) {
    if (c->permil_depth > 0) return cur_permil(c);
    int64_t pt = c->g.st.current_parameter_type;
    if (pt <= 0 || pt > 3 || !c->R->has_permil[pt]) return NONE;
    return c->R->permil[pt];
}

// forward decls
GK_OUTLINE void apply_effect_body(Core *c, const Eff *e, int64_t permil);
GK_OUTLINE void dispatch_body(Core *c, int phase, const CardView *pc, int64_t phase_value, int64_t permil);
GK_OUTLINE void raise_direct_status_change_body(Core *c, int effect_type, int64_t diff);

// --------------------------------------------------------------------------- the trigger path's entry points are a
// test inlined at the call site plus a call of the body only when the test passes.
GK_GUARD int listens(const Core *c, int phase) { return (unsigned)phase >= 64 || c->phase_candidates[phase] != 0; }
GK_GUARD int effect_push(Core *c, int64_t permil) {
    if (c->permil_depth >= 64) UNSUP_RET(c, 0, 0);
    c->permil_none[c->permil_depth] = permil == NONE;
    c->permil_stack[c->permil_depth] = permil;
    c->permil_depth++;
    return 1;
}
#define dispatch(c, phase, pc, phase_value, permil) do { Core *c_d_ = (c); int ph_d_ = (phase); \
    if (listens(c_d_, ph_d_)) dispatch_body(c_d_, ph_d_, (pc), (phase_value), (permil)); } while (0)
#define run_triggers(c, sel, n, phase, pc, phase_value, permil) do { Core *c_r_ = (c); int n_r_ = (n), ph_r_ = (phase); \
    if (n_r_ != 0 || (ph_r_ == 3 && c_r_->phase_candidates[3])) \
        run_triggers_body(c_r_, (sel), n_r_, ph_r_, (pc), (phase_value), (permil)); } while (0)
GK_GUARD int64_t apply_effect_light(Core *c, const Eff *e);
#ifdef GK_NO_TRACE
#define GK_TRACE_HOOK(c, e)
#else
#define GK_TRACE_HOOK(c, e) if ((c)->blog) bind_trace((c), (e));
#endif
#define apply_effect(c, e, permil) do { Core *c_a_ = (c); const Eff *e_a_ = (e); int64_t pm_a_ = (permil); \
    GK_TRACE_HOOK(c_a_, e_a_) \
    if (effect_push(c_a_, pm_a_)) { int64_t up_a_ = apply_effect_light(c_a_, e_a_); \
        if (up_a_ < 0) apply_effect_body(c_a_, e_a_, pm_a_); \
        else if (up_a_ > 0) raise_direct_status_change(c_a_, 10, up_a_); \
        c_a_->permil_depth--; } } while (0)
// the card enchants of 「直接効果で<status>が増加後」 first (core_card_growth.h), then the triggers
GK_FN void card_enchant_on_status_change(Core *c, int effect_type);
#define raise_direct_status_change(c, effect_type, diff) do { Core *c_s_ = (c); \
    if (!c_s_->indirect_depth) { card_enchant_on_status_change(c_s_, (effect_type)); \
        if (!c_s_->unsup && ST(c_s_)->n_tes && (c_s_->phase_candidates[13] | c_s_->phase_candidates[52] | c_s_->phase_candidates[53])) \
            raise_direct_status_change_body(c_s_, (effect_type), (diff)); } } while (0)
#define count_up_interval(c, effect_type, permil) do { Core *c_u_ = (c); \
    if (c_u_->phase_candidates[52] | c_u_->phase_candidates[53]) count_up_interval_body(c_u_, (effect_type), (permil)); } while (0)

// ---------------------------------------------------------------------------
// snapshots (exam.py ExamState._play_snapshot, exam_session.py ExamSession.play)
// ---------------------------------------------------------------------------
GK_FN void snap_take(Core *c, Snap *s) {
    ExamStateP *S = ST(c);
    s->valid = 1;
    s->fpp = S->full_power_point; s->fpp_sum = S->full_power_point_sum; s->stance_cc = S->stance_change_count;
    s->conc_cc = S->concentration_change_count; s->pres_cc = S->preservation_change_count;
    s->fp_cc = S->full_power_change_count; s->lesson_buff = S->lesson_buff;
    s->parameter_buff = S->parameter_buff_remaining; s->pbmpt = S->parameter_buff_multiple_per_turn;
    s->review = S->review; s->aggressive = S->aggressive; s->block = S->block; s->stamina = S->stamina;
    s->stance = S->idol_status_type;
    s->stance_step = S->idol_status_step_count;
    s->pbm_turns = S->parameter_buff_multiple_until_turn == NONE ? 0
                 : imax(0, S->parameter_buff_multiple_until_turn - S->turn + 1);
    s->n_sc = 0;
}

// exam.py _parameter_buff_multiple_turns: zekkouchou turns left
GK_FN int64_t pbm_turns(Core *c) {
    ExamStateP *S = ST(c);
    if (S->parameter_buff_multiple_until_turn == NONE) return 0;
    return imax(0, S->parameter_buff_multiple_until_turn - S->turn + 1);
}

// exam.py _card_search_count; defined in core_effects.h
GK_OUTLINE int64_t card_search_count(Core *c, int32_t sid);

GK_OUTLINE int active_debuff_count(Core *c);

#define FS_FALSE (-2)
#define FS_UNKNOWN (-1)
GK_OUTLINE int field_status_one(Core *c, int64_t st, int64_t v, int neg, int32_t search, const Snap *s, int64_t lb) {
    ExamStateP *S = ST(c);
    int ok;
    switch ((int)st) {
    case 1: ok = (s ? s->parameter_buff : S->parameter_buff_remaining) >= 1; break;
    case 4: case 5: {
        if (!S->max_stamina) return FS_FALSE;
        int64_t have = (s ? s->stamina : S->stamina) * 1000, need = v * S->max_stamina;
        ok = st == 4 ? have >= need : have <= need;
        break;
    }
    case 9: ok = S->stamina_consumption_down != 0; break;
    case 11: case 12: case 13: case 46: {
        int stance = s ? s->stance : S->idol_status_type;
        ok = stance == (st == 11 ? GK_STANCE_CONCENTRATION : st == 12 ? GK_STANCE_PRESERVATION
                        : st == 13 ? GK_STANCE_FULL_POWER : GK_STANCE_NONE);
        if (st == 12 && stance == GK_STANCE_LEISURELY) ok = 1;
        if (ok && v && st != 46) ok = (s ? s->stance_step : S->idol_status_step_count) >= v;
        break;
    }
    case 18: ok = (s ? s->block : S->block) == 0; break;
    case 19: ok = S->last_played_card_category == 2; break;
    case 20: ok = S->last_played_card_category == 1; break;
    case 22: ok = S->turn > v; break;
    case 27: case 28: {
        int64_t scaled = S->score * c->M->setting_score_threshold_multiple;
        ok = st == 27 ? scaled >= v * 1000 : scaled <= v * 1000;
        break;
    }
    case 33: ok = (lb >= 0 ? lb : s ? s->lesson_buff : S->lesson_buff) >= v; break;
    case 34: ok = (s ? s->block : S->block) >= v; break;
    case 35: ok = (s ? s->review : S->review) >= v; break;
    case 38: ok = (s ? s->parameter_buff : S->parameter_buff_remaining) >= v; break;
    case 41:
        if (S->limit_turn == NONE) return FS_FALSE;
        ok = S->limit_turn + S->extra_turns - S->turn + 1 <= v;
        break;
    case 42: ok = (s ? s->aggressive : S->aggressive) >= v; break;
    case 44: ok = (s ? s->fpp : S->full_power_point) >= v; break;
    case 45: ok = (s ? s->fpp_sum : S->full_power_point_sum) >= v; break;
    case 47: ok = (s ? s->stance_cc : S->stance_change_count) >= v; break;
    case 48: ok = (s ? s->conc_cc : S->concentration_change_count) >= v; break;
    case 49: ok = (s ? s->pres_cc : S->preservation_change_count) >= v; break;
    case 50: ok = (s ? s->fp_cc : S->full_power_change_count) >= v; break;
    case 51: {
        if (search < 0) return FS_UNKNOWN;
        int k = 0;
        if (s) while (k < s->n_sc && s->sc_id[k] != search) k++;
        if (s && k < s->n_sc) { ok = s->sc_n[k] >= v; break; }
        ok = card_search_count(c, search) >= v;
        if (c->unsup) return 0;
        break;
    }
    case 53: ok = (s ? s->pbm_turns : pbm_turns(c)) >= v; break;
    case 58: {
        int n = active_debuff_count(c);
        if (n < 0) return 0;
        ok = n >= v;
        break;
    }
    default: return FS_UNKNOWN;
    }
    return neg ? !ok : ok;
}

// exam.py _field_statuses_hold: every master field status of a trigger on the live state; NOT (fieldStatusCheckTypes
// 1) applies to every type; an unknown type stops.
GK_OUTLINE int field_statuses_hold(Core *c, const TriggerRow *t, const Snap *s = 0) {
    const int64_t *types = P64(c, t->fst_off), *vals = P64(c, t->fsv_off), *chk = P64(c, t->fsc_off);
    const int32_t *sids = P32(c, t->fss_off);
    int next_search = 0;
    for (int i = 0; i < t->n_fst; i++) {
        int32_t search = -1;
        if (types[i] == 51) { search = next_search < t->n_fss ? sids[next_search] : -1; next_search++; }
        int r = field_status_one(c, types[i], i < t->n_fsv ? vals[i] : 0, i < t->n_fsc && chk[i] == 1, search, s, -1);
        if (c->unsup) return 0;
        if (r == FS_UNKNOWN) UNSUP_RET(c, types[i], 0);
        if (r != 1) return 0;
    }
    return 1;
}

// the searches (status 51) of the card's own effect conditions, counted into the snapshot when it is taken (before
// the cost, at card_play_select).
GK_OUTLINE void snap_card_searches(Core *c, Snap *s, const Resolved *r) {
    s->n_sc = 0;
    for (int i = 0; i < r->n_play; i++) {
        int32_t trig = r->play[i].trigger;
        if (trig < 0 || trig == c->M->sid_empty) continue;
        const TriggerRow *t = trig_row(c, trig);
        if (!t) continue;
        const int64_t *types = P64(c, t->fst_off);
        const int32_t *sids = P32(c, t->fss_off);
        int next_search = 0;
        for (int j = 0; j < t->n_fst; j++) {
            if (types[j] != 51) continue;
            int32_t sid = next_search < t->n_fss ? sids[next_search] : -1;
            next_search++;
            if (sid < 0 || !search_row(c, sid)) continue;
            int k = 0;
            while (k < s->n_sc && s->sc_id[k] != sid) k++;
            if (k < s->n_sc || s->n_sc >= 4) continue;
            int64_t n = card_search_count(c, sid);
            if (c->unsup) return;
            s->sc_id[s->n_sc] = sid; s->sc_n[s->n_sc] = n; s->n_sc++;
        }
    }
}

// exam.py _field_status_ok_at_play_start: 1 true, 0 false, -1 None
GK_OUTLINE int field_status_ok(Core *c, const TriggerRow *t, const Snap *snapshot) {
    const Snap *s = (snapshot && snapshot->valid) ? snapshot : (c->play_start.valid ? &c->play_start : 0);
    if (!s) return -1;
    const int64_t *types = P64(c, t->fst_off), *vals = P64(c, t->fsv_off), *chk = P64(c, t->fsc_off);
    const int32_t *sids = P32(c, t->fss_off);
    int next_search = 0;
    for (int i = 0; i < t->n_fst; i++) {
        int32_t search = -1;
        if (types[i] == 51) { search = next_search < t->n_fss ? sids[next_search] : -1; next_search++; }
        int r = field_status_one(c, types[i], i < t->n_fsv ? vals[i] : 0, i < t->n_fsc && chk[i] == 1, search, s, -1);
        if (c->unsup) return 0;
        if (r == FS_UNKNOWN) return -1;
        if (r != 1) return 0;
    }
    return 1;
}

GK_FN int card_matches_trigger_search(Core *c, const TriggerRow *t, const CardView *pc);

// exam.py _trigger_condition_matches
GK_FN int trigger_condition_matches(Core *c, const TriggerRow *t, const CardView *pc, int64_t phase_value,
                                    int phase, int64_t install_play_count) {
    ExamStateP *S = ST(c);
    const Snap *fs = ((phase == 2 || phase == 23 || phase == 25) && c->play_card.valid) ? &c->play_card : 0;
    if (t->n_fst && !field_statuses_hold(c, t, fs)) return 0;
    if (t->lesson_type >= 0) {
        int wanted = t->lesson_type == 5 ? 1 : t->lesson_type == 6 ? 2 : t->lesson_type == 7 ? 3 : 0;
        if (!(S->has_oikomi_threshold && S->score >= S->oikomi_threshold) && (!wanted || S->current_parameter_type != wanted)) return 0;
    }
    if (!card_matches_trigger_search(c, t, pc)) return 0;
    if (phase == 13 || phase == 34) {
        if (phase == 34) {
            int at = field_status_ok(c, t, c->consume_valid ? &c->consume : 0);
            if (at == 0) return 0;
        }
        if (phase == 13 && c->sc_diff_valid && t->n_pval && P64(c, t->pval_off)[0] > c->sc_diff) return 0;
        if (t->n_etype == 0) return 1;
        return in_list64(P64(c, t->etype_off), t->n_etype, phase_value);
    }
    if (phase == 37 || phase == 38) {
        int64_t step = t->n_pval ? P64(c, t->pval_off)[0] : 0;
        return step ? (phase_value & 0xff) == step : (int)((phase_value >> 8) & 1);
    }
    if (t->n_pval) {
        int64_t pv0 = P64(c, t->pval_off)[0];
        int timer = (phase == 21 || phase == 42);
        if (!(!timer && pv0 == 0)) {
            if (timer) { if (phase_value != pv0) return 0; }
            else {
                int64_t origin = phase == 44 ? install_play_count : 0;
                int64_t elapsed = phase_value - origin;
                if (phase == 35) elapsed *= 2;
                if (elapsed <= 0 || elapsed % pv0 != 0) return 0;
            }
        }
    }
    return 1;
}

// exam.py _card_matches_trigger_search (playing card = resolved)
GK_FN int card_matches_trigger_search(Core *c, const TriggerRow *t, const CardView *pc) {
    if (t->search < 0) return 1;
    if (!pc || pc->slot < 0) return 0;
    const SearchRow *sr = search_row(c, t->search);
    if (!sr) UNSUP_RET(c, t->search, 0);
    return search_match(c, sr, &pc->r, rrow(c, &pc->r)->id, -1, 1, pc->slot);
}

// Nonportable acceleration sidecar: phase_candidates[p] = the slots whose trigger lists phase p (p < 64), tes_row[i]
// = slot i's trigger row.
GK_FN void tes_index_slot(Core *c, int i) {
    const TriggerRow *t = trig_row(c, c->g.st.tes[i].trigger);
    c->tes_row[i] = t ? (int)(t - c->M->triggers) : -1;
    const uint64_t bit = uint64_t(1) << i;
    if (!t) {
        for (int p = 0; p < 64; p++) c->phase_candidates[p] |= bit;
    } else if (c->phase_masks) {
        for (uint64_t m = c->phase_masks[c->tes_row[i]]; m; m &= m - 1) c->phase_candidates[GK_CTZ64(m)] |= bit;
    } else {
        for (int j = 0; j < t->n_phase; j++) {
            int64_t p = P64(c, t->phase_off)[j];
            if (uint64_t(p) < 64) c->phase_candidates[p] |= bit;
        }
    }
}
GK_FN void rebuild_tes_index(Core *c) {
    GK_COUNT(tes_rebuilds, 1); GK_COUNT(tes_rebuild_slots, c->g.st.n_tes);
    for (int p = 0; p < 64; p++) c->phase_candidates[p] = 0;
    for (int i = 0; i < c->g.st.n_tes; i++) tes_index_slot(c, i);
}
GK_FN void tes_index_append(Core *c) {
    GK_COUNT(tes_appends, 1);
    tes_index_slot(c, c->g.st.n_tes - 1);
}

// exam.py select_trigger_effects.
GK_GUARD int select_triggers_body(Core *c, int phase, const CardView *pc, int64_t phase_value, int16_t *sel) {
    ExamStateP *S = ST(c);
    int n = 0;
    GK_COUNT(select_calls,1);
    GK_COUNT(tes_full_scan_slots,S->n_tes);
    uint64_t bits = (unsigned)phase < 64 ? c->phase_candidates[phase] : ((uint64_t(1)<<S->n_tes)-1);
    while(bits) {
        int i=GK_CTZ64(bits); bits &= bits-1;
        GK_COUNT(tes_actual_slots,1);
        TES *ts = &S->tes[i];
        if (ts->remaining_turns == 0) continue;
        const TriggerRow *t = c->tes_row[i] < 0 ? 0 : &c->M->triggers[c->tes_row[i]];
        if (!t) UNSUP_RET(c, ts->trigger, 0);
        if ((unsigned)phase >= 64 && !trig_has_phase(c, t, phase)) continue;
        if (ts->limit_count >= 0 && ts->spent_total >= ts->limit_count) continue;
        if (ts->limit_count_in_turn >= 0 && ts->spent_in_turn >= ts->limit_count_in_turn) continue;
        int64_t tpv = (phase == 23 || phase == 25) ? ts->phase_count : phase_value;
        if (phase == 22) tpv -= ts->interval22_origin;
        int64_t inst = ts->installed_play_count;
        if (phase == 44) { tpv = ts->has_interval44 ? ts->interval44_count : 0; inst = 0; }
        if (!trigger_condition_matches(c, t, pc, tpv, phase, inst)) continue;
        if (n >= MAX_SEL) UNSUP_RET(c, n, n);
        sel[n++] = (int16_t)i;
    }
    return n;
}
// a phase no installed trigger lists selects none (the loop above starts from an empty set)
GK_GUARD int select_triggers(Core *c, int phase, const CardView *pc, int64_t phase_value, int16_t *sel) {
    if ((unsigned)phase < 64 && !c->phase_candidates[phase]) return 0;
    return select_triggers_body(c, phase, pc, phase_value, sel);
}

// exam.py trigger command gate (select_trigger_effects)
GK_FN int gate_open(Core *c, int enabled) { int prev = c->trigger_commands_enabled; c->trigger_commands_enabled = prev && enabled; return prev; }
GK_FN void gate_close(Core *c, int prev) { c->trigger_commands_enabled = prev; }

GK_FN void map_set(StrIntMap *m, int32_t key, int64_t v, Core *c) {
    for (int i = 0; i < m->n; i++) if (m->key[i] == key) { m->val[i] = v; return; }
    if (m->n >= GKMS_MAX_MAP) { UNSUP(c, key); return; }
    m->key[m->n] = key; m->val[m->n] = v; m->n++;
}
GK_FN int map_has(const StrIntMap *m, int32_t key) { for (int i = 0; i < m->n; i++) if (m->key[i] == key) return 1; return 0; }
GK_FN int64_t map_get(const StrIntMap *m, int32_t key, int64_t dflt) { for (int i = 0; i < m->n; i++) if (m->key[i] == key) return m->val[i]; return dflt; }

// exam.py run_trigger_effects
GK_OUTLINE void run_triggers_body(Core *c, const int16_t *sel, int n, int phase, const CardView *pc, int64_t phase_value, int64_t permil) {
    ExamStateP *S = ST(c);
    if (phase == 3) {
        for (int i = 0; i < S->n_tes; i++) {
            TES *ts = &S->tes[i];
            if (ts->remaining_turns == 0) continue;
            const TriggerRow *t = trig_row(c, ts->trigger);
            if (!t || !trig_has_phase(c, t, phase)) continue;
            if (ts->skip_card_played_once) ts->skip_card_played_once = 0;
        }
    }
    for (int k = 0; k < n; k++) {
        int ti = sel[k];
        int32_t enchant = S->tes[ti].enchant_id;
        int32_t outer_origin = c->install_origin;
        c->install_origin = S->tes[ti].origin;
        int g = gate_open(c, 0);
        c->indirect_depth += 1;
        int outer_running = c->running_tes;
        c->running_tes = ti;
        int neff = S->tes[ti].n_effect_ids;
        for (int j = 0; j < neff; j++) {
            int32_t id = S->tes[ti].effect_ids[j];
            const Eff *e = eff_ptr(c, id);
            if (!e) { UNSUP(c, id); return; }
            if (phase == 4 && S->_wire_snapshot_turn) { UNSUP(c, phase); return; }
            apply_effect(c, e, permil);
            if (c->unsup) return;
        }
        c->running_tes = outer_running;
        c->indirect_depth -= 1;
        gate_close(c, g);
        c->install_origin = outer_origin;
        S->tes[ti].spent_total += 1;
        S->tes[ti].spent_in_turn += 1;
        if (map_has(&S->trigger_effect_limits, enchant))
            map_set(&S->trigger_effect_spent_totals, enchant, S->tes[ti].spent_total, c);
    }
}

// exam.py dispatch_trigger_effects
GK_OUTLINE void dispatch_body(Core *c, int phase, const CardView *pc, int64_t phase_value, int64_t permil) {
    int16_t sel[MAX_SEL];
    int n = select_triggers_body(c, phase, pc, phase_value, sel);
    if (c->unsup) return;
    run_triggers(c, sel, n, phase, pc, phase_value, permil);
}

// exam.py _install_trigger_effect
GK_FN void install_trigger(Core *c, int32_t enchant_id, int64_t limit_count, int64_t limit_in_turn,
                           int64_t remaining_turns, int passing_turn_start, int installed_in_turn_start) {
    ExamStateP *S = ST(c);
    if (S->n_active_enchants >= GKMS_MAX_ENCHANTS) UNSUP_RET(c, 0, );
    S->active_enchants[S->n_active_enchants++] = enchant_id;
    if (limit_count == NONE) limit_count = map_get(&S->trigger_effect_limits, enchant_id, -1);
    int64_t spent_total = 0;
    if (map_has(&S->trigger_effect_limits, enchant_id))
        spent_total = map_get(&S->trigger_effect_spent_totals, enchant_id, spent_total);
    int skip = !(installed_in_turn_start < 0 ? passing_turn_start : installed_in_turn_start);
    const EnchantRow *en = ench_row(c, enchant_id);
    if (!en) UNSUP_RET(c, enchant_id, );
    if (S->n_tes >= GKMS_MAX_TES) UNSUP_RET(c, 0, );
    c->tes_serial[S->n_tes] = ++c->forecast_serial;
    TES *ts = &S->tes[S->n_tes++];
    memset(ts, 0, sizeof(*ts));
    ts->status_change_fired_turn = NONE;
    ts->enchant_id = enchant_id;
    ts->origin = c->install_origin;
    ts->installed_play_count = S->play_count;
    ts->trigger = en->trigger;
    if (en->n_eff > GKMS_MAX_EFFECT_IDS) UNSUP_RET(c, en->n_eff, );
    ts->n_effect_ids = en->n_eff;
    for (int i = 0; i < en->n_eff; i++) ts->effect_ids[i] = P32(c, en->eff_off)[i];
    ts->limit_count = limit_count;
    ts->limit_count_in_turn = limit_in_turn;
    ts->spent_total = spent_total;
    ts->spent_in_turn = 0;
    ts->phase_count = 0;
    ts->remaining_turns = remaining_turns;
    ts->passing_turn_start = (uint8_t)passing_turn_start;
    ts->skip_play_count_once = (uint8_t)skip;
    ts->skip_card_played_once = (uint8_t)skip;
    ts->has_interval44 = 0;
    ts->owner_card = -1;
    {
        const TriggerRow *tr = trig_row(c, ts->trigger);
        int64_t origin = S->_in_turn_start ? 0 : S->turn;
        ts->interval22_origin = (origin && tr && trig_has_phase(c, tr, 22)) ? origin : 0;
    }
    tes_index_append(c);
}

// ---------------------------------------------------------------------------
// stamina / damage
// ---------------------------------------------------------------------------
GK_FN double stance_lesson_multiplier(const Core *c) {
    const ExamStateP *S = ST(c);
    if (S->idol_status_type == GK_STANCE_CONCENTRATION) {
        double base = f32_div((double)(S->idol_status_step_count >= 2 ? c->M->es_conc_lesson2 : c->M->es_conc_lesson1), 1000.0);
        if (S->concentration_lesson_multiple_additive != 0.0) {
            double inner = f32(f32(base) + f32(-1.0));
            inner = f32(f32(S->concentration_lesson_multiple_additive) + inner);
            return f32(inner + f32(1.0));
        }
        return base;
    }
    if (S->idol_status_type == GK_STANCE_PRESERVATION)
        return f32_div((double)(S->idol_status_step_count >= 2 ? c->M->es_pres_lesson2 : c->M->es_pres_lesson1), 1000.0);
    if (S->idol_status_type == GK_STANCE_FULL_POWER)
        return f32(f32_div((double)c->M->es_fp_lesson, 1000.0) + S->full_power_lesson_multiple_additive);
    if (S->idol_status_type == GK_STANCE_LEISURELY) return f32(0.0);
    if (S->idol_status_type == GK_STANCE_OVER_PRESERVATION) return S->over_preservation_lesson_multiplier;
    return f32(1.0);
}

GK_FN double stance_stamina_multiplier(const Core *c) {
    const ExamStateP *S = ST(c);
    if (S->idol_status_type == GK_STANCE_CONCENTRATION)
        return f32_div((double)(S->idol_status_step_count >= 2 ? c->M->es_conc_stamina2 : c->M->es_conc_stamina1), 1000.0);
    if (S->idol_status_type == GK_STANCE_PRESERVATION)
        return f32_div((double)(S->idol_status_step_count >= 2 ? c->M->es_pres_stamina2 : c->M->es_pres_stamina1), 1000.0);
    if (S->idol_status_type == GK_STANCE_LEISURELY) return f32(0.0);
    if (S->idol_status_type == GK_STANCE_OVER_PRESERVATION) return S->over_preservation_stamina_multiplier;
    return f32(1.0);
}

// exam.py calculate_damage -> (stamina, block)
GK_FN void calculate_damage(Core *c, int64_t value, int ignore_block, int buff_active, int64_t *sd, int64_t *bd) {
    ExamStateP *S = ST(c);
    int64_t block = ignore_block ? 0 : S->block;
    double dmg = f32((double)value);
    dmg = f32_mul(dmg, stance_stamina_multiplier(c));
    if (S->stamina_consumption_down) {
        int64_t p = S->stamina_consumption_down_add ? c->M->es_sc_down_add_permil : c->M->es_sc_down_permil;
        dmg = f32_mul(dmg, f32_div((double)(1000 - p), 1000.0));
    }
    if (S->stamina_consumption_add) {
        int64_t p = S->stamina_consumption_add_down ? c->M->es_sc_add_down_permil : c->M->es_sc_add_permil;
        dmg = f32_mul(dmg, f32_div((double)(p + 1000), 1000.0));
    }
    int64_t d = ceil_f32(dmg);
    if (buff_active) {
        d += imax(0, S->stamina_consumption_add_fix);
        if (S->stamina_consumption_down_fix >= 1) d = imax(0, d - S->stamina_consumption_down_fix);
        if (S->stamina_reduce_change && d <= S->stamina_reduce_change_threshold) d = c->M->es_stamina_reduce_change;
    }
    *sd = imax(0, d - block);
    *bd = d - *sd;
    if (buff_active && S->idol_status_type == GK_STANCE_CONCENTRATION && S->idol_status_step_count >= 2)
        *sd += c->M->es_conc_penetrate_reduce2;
}

// exam.py _raise_stamina_reduced
GK_FN void raise_stamina_reduced(Core *c, int phase, int64_t lost, const CardView *pc) {
    if (lost <= 0 || ST(c)->n_tes == 0) return;
    if (phase == 20 && c->indirect_depth) return;
    dispatch(c, phase, pc, 0, ambient_permil(c));
}

// exam.py damage_stamina
GK_FN int64_t damage_stamina(Core *c, int64_t cost, int ignore_block) {
    ExamStateP *S = ST(c);
    int64_t sd, bd;
    calculate_damage(c, cost, ignore_block, 1, &sd, &bd);
    S->block_consumption_sum += imin(bd, S->block);
    S->block = imax(0, S->block - bd);
    int64_t before = S->stamina;
    S->stamina = imin(S->max_stamina, imax(0, S->stamina - sd));
    S->consumed_stamina += before - S->stamina;
    return before - S->stamina;
}

// exam.py ExamState status adders
GK_FN int64_t recover_stamina(Core *c, int64_t value) {
    ExamStateP *S = ST(c);
    if (value < 1) return 0;
    if (S->stamina_recover_restriction && (!S->has_stamina_recover_restriction_until_turn || S->stamina_recover_restriction_until_turn == NONE || S->stamina_recover_restriction_until_turn == -1 || S->turn <= S->stamina_recover_restriction_until_turn)) return 0;
    if (S->stamina_recover_add >= 1 && (!S->has_stamina_recover_add_until_turn || S->stamina_recover_add_until_turn == NONE || S->stamina_recover_add_until_turn == -1 || S->turn <= S->stamina_recover_add_until_turn)) {
        int64_t extra = ceil_f32(f32(f32_mul(f32_div((double)S->stamina_recover_add, 1000.0), (double)value) - 0.0001));
        value += extra;
    }
    int64_t target = imin(S->max_stamina, imax(0, S->stamina + value));
    int64_t calc = target - S->stamina;
    int64_t before = S->stamina;
    S->stamina = imin(S->max_stamina, imax(0, S->stamina + calc));
    return S->stamina - before;
}

GK_FN int64_t meta_buff_value(const Core *c, int64_t v) {
    const ExamStateP *S = &c->g.st;
    if (!S->meta_stage || v <= 0) return v;
    return S->meta_stage == 1 ? v + c->M->es_meta[1] : imax(0, v - c->M->es_meta[3]);
}
GK_FN int64_t add_lesson_buff_value(Core *c, int64_t v) {
    ExamStateP *S = ST(c);
    v = meta_buff_value(c, v);
    return ceil_f32(f32_mul(S->lesson_buff_additive_multiple, (double)(S->lesson_buff_additive_fix + v)));
}
GK_FN int64_t add_aggressive_value(Core *c, int64_t v) {
    ExamStateP *S = ST(c);
    return ceil_f32(f32_mul(S->aggressive_additive_multiple, (double)(S->aggressive_additive_fix + v)));
}
GK_FN int64_t add_parameter_buff_turns(Core *c, int64_t v) {
    ExamStateP *S = ST(c);
    v = meta_buff_value(c, v);
    return ceil_f32(f32_mul(S->parameter_buff_additive_multiple, (double)(S->parameter_buff_additive_fix + v)));
}
GK_FN void grant_parameter_buff_turns(Core *c, int64_t d) {
    ExamStateP *S = ST(c);
    if (S->parameter_buff_remaining) S->parameter_buff_remaining += d;
    else { S->parameter_buff_remaining = d; S->parameter_buff_passing_turn_start = 0; }
    S->parameter_buff_until_turn = S->turn + S->parameter_buff_remaining - 1;
}
GK_FN void consume_parameter_buff_turns(Core *c, int64_t n) {
    ExamStateP *S = ST(c);
    S->parameter_buff_remaining = imax(0, S->parameter_buff_remaining - n);
    if (S->parameter_buff_remaining) S->parameter_buff_until_turn = S->turn + S->parameter_buff_remaining - 1;
    else { S->parameter_buff_until_turn = NONE; S->parameter_buff_passing_turn_start = 0; }
}
GK_FN void consume_pbm_turns(Core *c, int64_t n) {
    ExamStateP *S = ST(c);
    int64_t until = S->parameter_buff_multiple_until_turn;
    if (until != NONE) {
        int64_t rem = imax(0, until - S->turn + 1 - n);
        if (rem > 0) S->parameter_buff_multiple_until_turn = S->turn + rem - 1;
        else { S->parameter_buff_multiple_until_turn = NONE; S->parameter_buff_multiple_per_turn = 0; }
    }
}

// exam.py _debuff_until -- a status created in the turn-start flow (before the passing mark) lasts through turn + d -
// 1, one created after it through turn + d
GK_FN int64_t debuff_until(Core *c, int64_t duration) {
    ExamStateP *S = ST(c);
    return duration <= 0 ? -1 : S->turn + duration - S->_in_turn_start;
}
// exam.py _fresh_mark / _layer_left -- a layer granted after this turn's passing mark keeps the turn it was granted
// in (0 otherwise); its remaining turns are until - turn, another layer's until - turn + 1
GK_FN int64_t fresh_mark(Core *c) { ExamStateP *S = ST(c); return S->_in_turn_start ? 0 : S->turn; }
GK_FN int64_t layer_left(Core *c, int64_t until, int64_t fresh) {
    ExamStateP *S = ST(c);
    return until - S->turn + (fresh == S->turn ? 0 : 1);
}
// timed gains (exam.py _grant_timed_gain / _expire_timed_gain_layers)
GK_FN double *tg_attr_f(Core *c, int k) {
    ExamStateP *S = ST(c);
    switch (k) { case 0: return &S->lesson_buff_additive_multiple; case 1: return &S->parameter_buff_additive_multiple;
                 case 2: return &S->aggressive_additive_multiple; case 3: return &S->review_additive_multiple; }
    return 0;
}
GK_FN int64_t *tg_attr_i(Core *c, int k) {
    ExamStateP *S = ST(c);
    return k == 4 ? &S->lesson_buff_additive_fix : k == 5 ? &S->aggressive_additive_fix
         : k == 6 ? &S->enthusiastic_multiple_permil
         : k == 7 ? &S->parameter_buff_additive_fix : 0;
}
GK_FN int tg_kind_of(const Core *c, int32_t name) { for (int k = 0; k < 8; k++) if (c->M->sid_tg[k] == name) return k; return -1; }
GK_FN double num_as_double(const Num *n) { return n->tag == 1 ? n->f : (double)n->i; }
GK_FN void tg_recompute(Core *c, int slot) {
    TimedGain *T = &ST(c)->timed_gain_layers;
    int k = tg_kind_of(c, T->name[slot]);
    if (k < 0) UNSUP_RET(c, T->name[slot], );
    if (k < 4) {
        double s = 0.0;
        for (int j = 0; j < T->nl[slot]; j++) s += num_as_double(&T->value[slot][j]);
        *tg_attr_f(c, k) = f32(1.0 + s);
    } else {
        int64_t s = 0;
        for (int j = 0; j < T->nl[slot]; j++) {
            if (T->value[slot][j].tag != 0) UNSUP_RET(c, 0, );
            s += T->value[slot][j].i;
        }
        *tg_attr_i(c, k) = imax(0, s);
    }
}
// grant: value is a float (multiple, value/1000.0) or int (fix)
GK_FN void grant_timed_gain(Core *c, int k, int is_float, double fv, int64_t iv, int64_t duration) {
    ExamStateP *S = ST(c);
    TimedGain *T = &S->timed_gain_layers;
    int32_t name = c->M->sid_tg[k];
    int slot = -1;
    for (int i = 0; i < T->n; i++) if (T->name[i] == name) slot = i;
    if (slot < 0) {
        if (T->n >= GKMS_MAX_SMALL) UNSUP_RET(c, 0, );
        slot = T->n++; T->name[slot] = name; T->nl[slot] = 0;
    }
    int64_t expire = duration <= 0 ? NONE : S->turn + duration - S->_in_turn_start;
    int found = -1;
    for (int j = 0; j < T->nl[slot]; j++) {
        int64_t e = T->expire[slot][j];
        if (expire == NONE) { if (e == NONE) { found = j; break; } }
        else if (e != NONE && layer_left(c, e, T->fresh[slot][j]) == duration) { found = j; break; }
    }
    if (found >= 0) {
        Num *v = &T->value[slot][found];
        if (v->tag == 0 && !is_float) v->i += iv;
        else { double a = num_as_double(v); v->f = a + (is_float ? fv : (double)iv); v->tag = 1; v->i = 0; }
    } else {
        if (T->nl[slot] >= GKMS_MAX_TG_LAYERS) UNSUP_RET(c, 0, );
        int j = T->nl[slot]++;
        T->expire[slot][j] = expire;
        T->fresh[slot][j] = fresh_mark(c);
        Num *v = &T->value[slot][j];
        if (is_float) { v->tag = 1; v->f = fv; v->i = 0; } else { v->tag = 0; v->i = iv; v->f = 0; }
    }
    tg_recompute(c, slot);
}
GK_FN void tg_expire(Core *c, int64_t turn) {
    TimedGain *T = &ST(c)->timed_gain_layers;
    for (int i = 0; i < T->n; i++) {
        int kept = 0, n = T->nl[i];
        for (int j = 0; j < n; j++) {
            int64_t e = T->expire[i][j];
            if (e == NONE || turn <= e) {
                T->expire[i][kept] = e; T->value[i][kept] = T->value[i][j]; T->fresh[i][kept] = T->fresh[i][j]; kept++;
            }
        }
        if (kept != n) { T->nl[i] = kept; tg_recompute(c, i); }
    }
}

GK_OUTLINE void count_up_interval_body(Core *c, int effect_type, int64_t permil) {
    ExamStateP *S = ST(c);
    int16_t sel52[MAX_SEL], sel53[MAX_SEL]; int n52 = 0, n53 = 0;
    for (int i = 0; i < S->n_tes; i++) {
        TES *ts = &S->tes[i];
        const TriggerRow *t = trig_row(c, ts->trigger);
        if (!t || ts->remaining_turns == 0) continue;
        int phase = 0;
        for (int j = 0; j < t->n_phase && !phase; j++) {
            int p = (int)P64(c, t->phase_off)[j];
            if (p == 52 || p == 53) phase = p;
        }
        if (!phase) continue;
        if (t->n_etype ? !in_list64(P64(c, t->etype_off), t->n_etype, effect_type)
                       : effect_type != (phase == 52 ? 31 : 42)) continue;
        int64_t *count = phase == 52 ? &ts->interval52_count : &ts->interval53_count;
        if (ts->limit_count >= 0 && ts->spent_total >= ts->limit_count) continue;
        if (ts->limit_count_in_turn >= 0 && ts->spent_in_turn >= ts->limit_count_in_turn) continue;
        *count += 1;
        if (!trigger_condition_matches(c, t, 0, *count, phase, 0)) continue;
        if ((phase == 52 ? n52 : n53) >= MAX_SEL) UNSUP_RET(c, i, );
        if (phase == 52) sel52[n52++] = (int16_t)i; else sel53[n53++] = (int16_t)i;
    }
    if (!c->unsup) run_triggers(c, sel52, n52, 52, 0, 0, permil);
    if (!c->unsup) run_triggers(c, sel53, n53, 53, 0, 0, permil);
}

GK_OUTLINE void raise_direct_status_change_body(Core *c, int effect_type, int64_t diff) {
    int64_t permil = cur_permil(c);
    int32_t pv = c->sc_diff_valid; int64_t pd = c->sc_diff;
    c->sc_diff_valid = 1; c->sc_diff = diff;
    dispatch(c, 13, 0, effect_type, permil);
    c->sc_diff_valid = pv; c->sc_diff = pd;
    if (c->unsup) return;
    count_up_interval(c, effect_type, permil);
}

GK_GUARD int64_t apply_effect_light(Core *c, const Eff *e) {
    ExamStateP *S = ST(c);
    switch (e->type) {
    case 10: {
        int64_t before = S->lesson_buff;
        S->lesson_buff = imax(0, S->lesson_buff + add_lesson_buff_value(c, e->has_v1 ? e->v1 : 0));
        return S->lesson_buff > before ? S->lesson_buff - before : 0;
    }
    case 5: {
        if (!e->has_turn) UNSUP_RET(c, 5, 0);
        int64_t until = S->stamina_consumption_down_until_turn;
        if (until != NONE && until >= S->turn) S->stamina_consumption_down_until_turn = until + e->turn;
        else S->stamina_consumption_down_until_turn = S->turn + e->turn - S->_in_turn_start;
        S->stamina_consumption_down = 1;
        return 0;
    }
    case 14:
        if (!e->has_count) UNSUP_RET(c, 14, 0);
        add_playable_value(S, e->count);
        return 0;
    }
    return -1;
}

// exam.py add_review
GK_FN void add_review(Core *c, int64_t value, int is_fix) {
    ExamStateP *S = ST(c);
    if (!is_fix) value = ceil_f32(f32_mul(S->review_additive_multiple, (double)(S->review_additive_fix + value)));
    S->review += value;
    S->review_added_this_turn += value;
    if (value > 0) raise_direct_status_change(c, 31, value);
}

// exam.py reduce_review
GK_FN int64_t reduce_review(Core *c, int64_t value) {
    ExamStateP *S = ST(c);
    int64_t before = S->review;
    S->review = imax(0, S->review - value);
    if (before > 0 && S->review == 0) S->review_turn_start = 0;
    int64_t consumed = before - S->review;
    S->review_consumption_sum += consumed;
    return consumed;
}

// exam.py _fire_status_change_triggers
GK_FN void fire_status_change_triggers(Core *c, int changed, int64_t permil) {
    ExamStateP *S = ST(c);
    if (S->has__in_status_change_trigger && S->_in_status_change_trigger) return;
    for (int i = 0; i < S->n_tes; i++) {
        TES *ts = &S->tes[i];
        const TriggerRow *t = trig_row(c, ts->trigger);
        if (!t) UNSUP_RET(c, 0, );
        if (!trig_has_phase(c, t, 13)) continue;
        if (t->n_etype && !in_list64(P64(c, t->etype_off), t->n_etype, changed)) continue;
        if (ts->remaining_turns == 0 || ts->status_change_fired_turn == S->turn) continue;
        ts->status_change_fired_turn = S->turn;
        S->has__in_status_change_trigger = 1;
        S->_in_status_change_trigger = 1;
        c->indirect_depth += 1;
        int n = ts->n_effect_ids;
        int32_t ids[GKMS_MAX_EFFECT_IDS];
        for (int j = 0; j < n; j++) ids[j] = ts->effect_ids[j];
        for (int j = 0; j < n && !c->unsup; j++) {
            const Eff *e = eff_ptr(c, ids[j]);
            if (!e) { UNSUP(c, ids[j]); break; }
            apply_effect(c, e, permil);
        }
        c->indirect_depth -= 1;
        S->_in_status_change_trigger = 0;
        if (c->unsup) return;
    }
}

// ---------------------------------------------------------------------------
// exam.py calculate_adding_parameter / 2732 _battle_bonus / 2748 _add_parameter
// ---------------------------------------------------------------------------
GK_FN int64_t calculate_adding_parameter(Core *c, int64_t value, int has_scale, double scale) {
    ExamStateP *S = ST(c);
    if (S->slump) return 0;
    double pb = f32(1.0);
    int64_t pbt = S->parameter_buff_remaining;
    if (pbt) {
        double add = f32(f32_div((double)c->M->es_param_buff_permil, 1000.0) - f32(1.0));
        if (S->parameter_buff_multiple_per_turn) {
            int64_t per_turn = c->M->es_param_buff_multiple_per_turn_permil * pbt;
            add = f32(add + f32_div((double)per_turn, 1000.0));
        }
        if (has_scale) add = f32_mul(add, f32(scale));
        pb = f32(add + f32(1.0));
    }
    int64_t debuff_permil = S->gimmick_parameter_debuff ? c->M->es_gimmick_param_debuff_permil : 1000;
    double extra = f32(0.0);
    if (S->lesson_parameter_multiple_depend_review_or_aggressive) {
        double dep = f32_mul(f32_div((double)c->M->es_lpm_depend_permil, 1000.0),
                             (double)imin(S->review, S->aggressive));
        extra = dep < 0.0 ? f32(0.0) : dep;
        double mx = f32_div((double)c->M->es_lpm_depend_max_permil, 1000.0);
        if (extra > mx) extra = mx;
    }
    int64_t base = value;
    if (S->has_lesson_change_specify) base = imax(0, S->lesson_change_specify);
    int64_t lbt = ceil_f32(f32_mul(S->lesson_buff_multiple, (double)S->lesson_buff));
    int64_t inner = imax(0, lbt - S->lesson_debuff + S->enthusiastic_buff + base);
    double ls = f32(f32(1.0) - f32(S->lesson_parameter_multiple_down));
    double lesson_status = f32(0.0) > ls ? f32(0.0) : ls;
    double lesson_multiple = f32(f32(S->lesson_parameter_multiple) + f32(extra));
    double stance = stance_lesson_multiplier(c);
    double v = f32_mul(pb, (double)inner);
    v = f32_mul(f32_div((double)debuff_permil, 1000.0), v);
    v = f32_mul(lesson_multiple, v);
    v = f32_mul(lesson_status, v);
    v = f32_mul(stance, v);
    if (S->meta_stage == 1) v = f32_mul(f32_div((double)imax(0, c->M->es_meta[2]), 1000.0), v);
    else if (S->meta_stage == 2) v = f32_mul(f32_div((double)(1000 + c->M->es_meta[4]), 1000.0), v);
    v = f32(v + f32(-0.0001));
    return imin(ceil_f32(v), INT_MAX32);
}
GK_FN void add_parameter(Core *c, int64_t adding, int64_t permil) {
    ExamStateP *S = ST(c);
    if (permil != NONE) {
        double m = f32_div((double)permil, 1000.0);
        double v = f32_mul(m, (double)adding);
        adding = ceil_f32(f32(v + f32(-0.0001)));
    }
    adding = imin(adding, INT_MAX32);
    adding = imin(adding, INT_MAX32 - S->score);
    if (S->has_score_limit) adding = imin(adding, imax(0, S->score_limit - S->score));
    S->score += adding;
    if (c->blog) bind_split(c, adding);
    S->current_turn_total_add_parameter += adding;
    if (permil != NONE) {
        if (S->current_parameter_type == 1) S->score_vocal += adding;
        else if (S->current_parameter_type == 2) S->score_dance += adding;
        else if (S->current_parameter_type == 3) S->score_visual += adding;
    }
}

// exam.py _calculate_add_block
GK_FN int64_t calculate_add_block(Core *c, int64_t value, double rate) {
    ExamStateP *S = ST(c);
    if (value < 1) return 0;
    if (S->block_restriction) return 0;
    int64_t aa = ceil_f32(f32_mul((double)S->aggressive, rate));
    int64_t gain = value + aa;
    if (S->block_add_down && !S->block_add_down_restriction)
        gain -= (int64_t)floor((double)gain * (double)(1000 - c->M->es_block_add_down_permil) / 1000.0 + 0.0001);
    if (S->block_add_down_fix && !S->block_add_down_fix_restriction)
        gain -= S->block_add_down_fix;
    return imax(0, gain);
}

// exam.py _anti_debuff_gate: returns 1 when blocked in_anti_debuff_gated: gkms_tables.h (generated from exam.py
// ANTI_DEBUFF_GATED)
GK_FN int anti_debuff_gate(Core *c, int t) {
    ExamStateP *S = ST(c);
    if (S->anti_debuff > 0) {
        S->anti_debuff -= 1;
        return 1;
    }
    int32_t kind = (t >= 0 && t < 220) ? c->M->sid_debuff_kind[t] : -1;
    if (kind >= 0) {
        SmallStrIntMap *m = &S->debuff_since;
        int has = 0;
        for (int i = 0; i < m->n; i++) if (m->key[i] == kind) has = 1;
        if (!has) {
            S->_debuff_seq += 1;
            if (m->n >= GKMS_MAX_MAP) UNSUP_RET(c, 0, 0);
            m->key[m->n] = kind; m->val[m->n] = S->_debuff_seq; m->n++;
        }
    }
    return 0;
}
// exam.py _debuff_stack
GK_FN int64_t debuff_stack(Core *c, int64_t until, int64_t fresh_until, int64_t duration) {
    if (until == -1 || fresh_until == -1 || duration == -1) return -1;
    if (until != NONE && until >= ST(c)->turn) return until + duration;
    return fresh_until;
}

// exam.py _lesson_parameter_multiple_from_statuses
GK_FN double lpm_from_statuses(Core *c) {
    TupleList *L = &ST(c)->lesson_parameter_multiple_layers;
    int64_t keys[GKMS_MAX_LAYERS]; int64_t tot[GKMS_MAX_LAYERS]; int nk = 0;
    for (int i = 0; i < L->n; i++) {
        if (L->len[i] < 5) UNSUP_RET(c, L->len[i], 1.0);
        if (L->v[i][3].tag != 0 || L->v[i][4].tag != 0) UNSUP_RET(c, 0, 1.0);
        int64_t key = L->v[i][3].i, permil = L->v[i][4].i;
        int k = -1;
        for (int j = 0; j < nk; j++) if (keys[j] == key) k = j;
        if (k < 0) { k = nk++; keys[k] = key; tot[k] = 0; }
        tot[k] += permil;
    }
    double acc = f32(1.0);
    for (int j = 0; j < nk; j++) acc = f32(acc + f32_div((double)tot[j], 1000.0));
    return acc;
}

// Tower stance transitions, reference exam.py ExamState stance transitions.
GK_FN void gain_enthusiastic(Core *c, int64_t base) {
    ExamStateP *S = ST(c);
    int64_t gain = base;
    const TupleList *L = &S->enthusiastic_additive_layers;
    for (int i = 0; i < L->n; i++)
        if (L->v[i][1].tag == 2 || L->v[i][1].i >= S->turn) gain += L->v[i][0].i;
    if (S->enthusiastic_multiple_permil) {
        const TimedGain *T = &S->timed_gain_layers;
        double m = f32(1.0);
        for (int i = 0; i < T->n; i++) if (T->name[i] == c->M->sid_tg[6])
            for (int j = 0; j < T->nl[i]; j++) m = f32(m + f32_div(num_as_double(&T->value[i][j]), 1000.0));
        gain = ceil_f32(f32_mul(f32((double)gain), m));
    }
    S->enthusiastic_buff += gain;
}
GK_FN void release_stance(Core *c, int prev, int step, int to_full_power) {
    ExamStateP *S = ST(c);
    if (prev == GK_STANCE_PRESERVATION) {
        gain_enthusiastic(c, step >= 2 ? c->M->es_pres_release_enth2 : c->M->es_pres_release_enth1);
        if (step >= 2) S->block += c->M->es_pres_release_block2;
        add_playable_value(S, step >= 2 ? c->M->es_pres_release_playable2 : c->M->es_pres_release_playable1);
    } else if (prev == GK_STANCE_LEISURELY) {
        if (to_full_power) leisurely_to_full_power_grow(c);
        else gain_enthusiastic(c, c->M->es_over_pres_release_enth);
        if (c->unsup) return;
        S->block += c->M->es_over_pres_block_add;
        add_playable_value(S, c->M->es_over_pres_release_playable);
    }
}
// exam.py _dispatch_stance_phase
GK_FN void stance_phase(Core *c, int new_type, int64_t step, int genre_changed) {
    dispatch(c, new_type == GK_STANCE_CONCENTRATION ? 37 : 38, 0, (step & 0xff) | ((int64_t)(genre_changed != 0) << 8),
             ambient_permil(c));
}
GK_FN int stance_change(Core *c, int new_type, int step) {
    ExamStateP *S = ST(c);
    int prev = S->idol_status_type, ps = S->idol_status_step_count;
    if (S->stance_lock_turns > 0 || prev == GK_STANCE_FULL_POWER) return 0;
    if (prev == new_type) {
        S->idol_status_step_count = step == 1 ? imin(2, ps + 1) : imin(2, imax(step, ps));
        if (S->idol_status_step_count != ps) stance_phase(c, new_type, S->idol_status_step_count, 0);
        return c->unsup ? 0 : 1;
    }
    if (prev == GK_STANCE_LEISURELY && new_type == GK_STANCE_PRESERVATION) return 1;
    if (new_type == GK_STANCE_CONCENTRATION) release_stance(c, prev, ps, 0);
    if (c->unsup) return 0;
    S->idol_status_type = new_type;
    S->idol_status_step_count = imin(2, imax(1, step));
    S->stance_change_count += 1;
    if (!c->indirect_depth) S->direct_stance_change_count += 1;
    if (new_type == GK_STANCE_CONCENTRATION) S->concentration_change_count += 1;
    if (new_type == GK_STANCE_PRESERVATION) S->preservation_change_count += 1;
    card_enchant_on_stance_change(c, new_type, !c->indirect_depth);
    if (c->unsup) return 0;
    if (!c->indirect_depth) dispatch(c, 35, 0, S->direct_stance_change_count, ambient_permil(c));
    if (c->unsup) return 0;
    stance_phase(c, new_type, S->idol_status_step_count, 1);
    return c->unsup ? 0 : 1;
}

// ``permil`` the turn's battle bonus (ano_turn_start_transition passes start_turn's)
GK_FN int enter_full_power(Core *c, int source, int consume, int64_t permil) {
    ExamStateP *S = ST(c);
    int prev = S->idol_status_type, ps = S->idol_status_step_count;
    if (source == 0) {
        if (consume) S->full_power_point -= 10;
        release_stance(c, prev, ps, 1);
        if (c->unsup) return 0;
        add_playable_value(S, c->M->es_fp_playable_add);
    } else if (prev == GK_STANCE_LEISURELY) {
        S->block += c->M->es_over_pres_block_add;
        leisurely_to_full_power_grow(c);
        if (c->unsup) return 0;
    }
    S->idol_status_type = GK_STANCE_FULL_POWER; S->idol_status_step_count = 1;
    S->stance_change_count += 1; S->direct_stance_change_count += 1; S->full_power_change_count += 1;
    card_enchant_on_stance_change(c, GK_STANCE_FULL_POWER, 1);
    if (c->unsup) return 0;
    dispatch(c, 35, 0, S->direct_stance_change_count, permil);
    if (!c->unsup) dispatch(c, 39, 0, 0, permil);
    return c->unsup ? 0 : 1;
}

GK_FN int64_t add_full_power_point(Core *c, int64_t value) {
    ExamStateP *S = ST(c);
    int64_t gain = value;
    if (S->full_power_point_additive_permil)
        gain = ceil_f32(f32_mul(f32((double)value), f32_div((double)(1000 + S->full_power_point_additive_permil), 1000.0)));
    S->full_power_point += gain; S->full_power_point_sum += gain;
    return gain;
}

GK_FN int64_t lesson_full_power_point_extra(const Core *c, int64_t value2) {
    return (int64_t)f32_div((double)(ST(c)->full_power_point_sum * value2), 1000.0);
}

GK_FN void over_preservation(Core *c) {
    ExamStateP *S = ST(c);
    if (S->stance_lock_turns > 0) return;
    if (S->idol_status_type != GK_STANCE_FULL_POWER && S->idol_status_type != GK_STANCE_LEISURELY) {
        if (S->idol_status_type != GK_STANCE_PRESERVATION) S->preservation_change_count += 1;
        S->idol_status_type = GK_STANCE_LEISURELY; S->idol_status_step_count = 1; S->stance_change_count += 1;
        if (!c->indirect_depth) S->direct_stance_change_count += 1;
    }
}

GK_FN void add_stance_lock(Core *c, int64_t turns) {
    ExamStateP *S = ST(c);
    int live = S->stance_lock_turns > 0;
    S->stance_lock_turns = imax(0, S->stance_lock_turns) + turns;
    if (!live) S->stance_lock_passing_turn_start = 0;
}

GK_FN void reduce_full_power_point(Core *c, int64_t value) {
    ST(c)->full_power_point = imax(0, ST(c)->full_power_point - value);
}

GK_FN int ano_turn_start_transition(Core *c, int64_t permil) {
    ExamStateP *S = ST(c);
    const int was_full = S->idol_status_type == GK_STANCE_FULL_POWER;
    if (was_full) { S->idol_status_type = GK_STANCE_NONE; S->idol_status_step_count = 0; }
    if (S->full_power_point >= 10 && S->stance_lock_turns == 0 && !enter_full_power(c, 0, 1, permil)) return 0;
    if (c->unsup) return 0;
    if (was_full) dispatch(c, 47, 0, 0, permil);
    return c->unsup ? 0 : 1;
}

GK_FN int panic_active(const Core *c) {
    if (!ST(c)->has_panic_until_turn) return 0;
    int64_t u = ST(c)->panic_until_turn;
    return u != NONE && (u == -1 || ST(c)->turn <= u);
}

GK_FN void clear_panic_stamina_fix(Core *c) {
    int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        Card *cd = &c->g.cards[zget(c, zones[k], i)];
        if (cd->has_panic_stamina_until_turn && ST(c)->turn > cd->panic_stamina_until_turn) {
            cd->panic_stamina_fix = NONE; cd->has_panic_stamina_fix = 0;
            cd->panic_stamina_until_turn = NONE; cd->has_panic_stamina_until_turn = 0;
        }
    }
}

GK_FN void apply_panic_command(Core *c) {
    if (!panic_active(c) || c->M->n_panic_candidates <= 0) return;
    const int64_t *v = P64(c, c->M->panic_candidates_off);
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        Card *cd = &c->g.cards[zget(c, Z_HAND, i)];
        int64_t j = rng_range(c, 0, c->M->n_panic_candidates);
        cd->panic_stamina_fix = v[j]; cd->has_panic_stamina_fix = 1;
        cd->panic_stamina_until_turn = ST(c)->turn; cd->has_panic_stamina_until_turn = 1;
    }
}
