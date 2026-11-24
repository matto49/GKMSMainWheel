// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// persistent in-exam grows and card-local enchants (exam.py ExamState._card_enchant_spec / _card_enchant_fire, effect
// 156).
#pragma once

GK_FN const CardEnchantRow *card_enchant_spec(Core *c, int sid) {
    if (sid < 0) return 0;
    const CardEnchantRow *row = 0;
    GK_COUNT(card_enchant_spec_calls, 1);
    for (int i = 0; i < c->M->n_card_enchants; i++) {
        GK_COUNT(card_enchant_rows, 1);
        if (c->M->card_enchants[i].id == sid) { row = &c->M->card_enchants[i]; break; }
    }
    if (!row) return 0;
    ExamStateP *S = ST(c);
    S->has_card_enchant_specs = 1;
    int found = 0;
    for (int i = 0; i < S->n_card_enchant_specs; i++) if (S->card_enchant_specs[i].id == sid) found = 1;
    if (!found) {
        if (S->n_card_enchant_specs >= GKMS_MAX_ENCHANTS || row->n_grow > GKMS_MAX_EFFECT_IDS) UNSUP_RET(c, sid, 0);
        CardEnchantSpec *dst = &S->card_enchant_specs[S->n_card_enchant_specs++];
        memset(dst, 0, sizeof(*dst));
        dst->id = sid; dst->trigger = row->trigger; dst->limit = row->limit; dst->n_grow = row->n_grow;
        for (int i = 0; i < row->n_grow; i++) dst->grows[i] = P32(c, row->grow_off)[i];
    }
    return row->trigger < 0 ? 0 : row;
}

GK_FN void append_card_grows(Core *c, int slot, const int32_t *ids, int n) {
    Card *cd = &c->g.cards[slot];
    if (cd->n_grows + n > GKMS_MAX_GROWS) UNSUP_RET(c, 156, );
    int at = -1;
    GK_COUNT(grow_appends, 1);
    const int16_t seen = c->grow_reg_at[slot];
    if (seen >= 0 && seen < c->g.d_n_grow_registry) {
        const CardNo *k = &c->g.d_grow_registry[seen].number;
        if (k->n == cd->number.n && k->g == cd->number.g) at = seen;
    }
    for (int i = 0; at < 0 && i < c->g.d_n_grow_registry; i++) {
        GK_COUNT(grow_registry_rows, 1);
        CardNo *k = &c->g.d_grow_registry[i].number;
        if (k->n == cd->number.n && k->g == cd->number.g) at = i;
    }
    if (at < 0) {
        if (c->g.d_n_grow_registry >= GKMS_MAX_CARDS) UNSUP_RET(c, 156, );
        at = c->g.d_n_grow_registry++;
        memset(&c->g.d_grow_registry[at], 0, sizeof(c->g.d_grow_registry[at]));
        c->g.d_grow_registry[at].number = cd->number;
        c->g.d_grow_registry[at].n = 0;
    }
    c->grow_reg_at[slot] = (int16_t)at;
    GrowRegistry *reg = &c->g.d_grow_registry[at];
    if (reg->n + n > GKMS_MAX_GROWS) UNSUP_RET(c, 156, );
    c->g.d_has_grow_registry = cd->has_grows = 1;
    const int current = !c->unsup && resolve_is_current(c, slot);
    const int32_t info_before = c->unsup_info;
    for (int i = 0; i < n; i++) {
        cd->grows[cd->n_grows++] = ids[i];
        reg->ids[reg->n++] = ids[i];
    }
    if (current) {
        Resolved *r = &c->res[slot];
        const CardRow *row = &c->M->cards[r->row];
        int ok = 1;
        for (int i = 0; ok && i < n; i++) ok = resolve_grow(c, row, r, ids[i]);
        if (ok && !c->unsup) {
            GK_COUNT(grow_steps, n);
            refresh_card(c, slot);
            return;
        }
        c->unsup = 0; c->unsup_info = info_before;
    }
    c->resolved_valid &= ~(uint64_t(1) << slot);
    refresh_card(c, slot);
}

GK_FN void card_enchant_fire(Core *c, int slot, const CardEnchantRow *spec, int this_play = 1) {
    if (slot < 0) return;
    Card *cd = &c->g.cards[slot];
    cd->has_enchant_fired = 1;
    if (spec->limit >= 0 && cd->enchant_fired >= spec->limit) return;
    cd->enchant_fired += 1;
    const int32_t *ids = P32(c, spec->grow_off);
    append_card_grows(c, slot, ids, spec->n_grow);
    if (c->unsup) return;
    if (this_play && slot == ST(c)->playing_card && c->playing_resolved) {
        for (int i = 0; i < spec->n_grow; i++) {
            int gi = BY(c, 6, ids[i]);
            if (gi >= 0 && c->M->grows[gi].type == 1)
                c->playing_resolved->lesson_add += c->M->grows[gi].value;
        }
    }
}

// the card's enchant is one of the import's holder enchants (core_session.h); -1: any enchant
GK_FN int after_play_holder(const Core *c, int slot) {
    const Card *b = &c->g.cards[slot];
    const int sid = b->has_card_status_enchant ? b->card_status_enchant : c->res[slot].status_enchant;
    if (sid < 0) return 0;
    if (c->n_pa_sids < 0) return 1;
    for (int j = 0; j < c->n_pa_sids; j++) if (c->pa_sids[j] == sid) return 1;
    return 0;
}

// a card that holds one of them has been seen (a card made since the last play is looked at too)
GK_FN int enchant_holder_seen(Core *c) {
    if (!c->n_pa_sids) return 0;
    for (; c->pa_scanned < c->g.n_cards; c->pa_scanned++)
        if (after_play_holder(c, c->pa_scanned)) c->pa_hold = 1;
    return c->pa_hold;
}

GK_FN void card_enchant_play_interval(Core *c, const CardView *pc) {
    if (c->predict || !enchant_holder_seen(c)) return;
    const int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    const int playing = ST(c)->playing_card;
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        const int slot = zget(c, zones[k], i);
        if (slot == playing || !after_play_holder(c, slot)) continue;
        Card *b = &c->g.cards[slot];
        const int sid = b->has_card_status_enchant ? b->card_status_enchant : c->res[slot].status_enchant;
        const CardEnchantRow *row = 0;
        for (int j = 0; j < c->M->n_card_enchants; j++)
            if (c->M->card_enchants[j].id == sid) { row = &c->M->card_enchants[j]; break; }
        if (!row || row->trigger < 0) continue;
        const TriggerRow *t = trig_row(c, row->trigger);
        if (!t || !trig_has_phase(c, t, 23)) continue;
        if (row->limit >= 0 && b->enchant_fired >= row->limit) continue;
        if (!card_matches_trigger_search(c, t, pc)) continue;
        if (t->n_fst && field_status_ok(c, t, 0) == 0) continue;
        if (c->unsup) return;
        int64_t interval = t->n_pval ? P64(c, t->pval_off)[0] : 1;
        if (interval <= 0) interval = 1;
        b->has_stance_count_base = 1; b->stance_count_base += 1;
        if (b->stance_count_base % interval) continue;
        const CardEnchantRow *grow = card_enchant_spec(c, sid);
        if (c->unsup || !grow) return;
        card_enchant_fire(c, slot, grow, 0);
        if (c->unsup) return;
    }
}

GK_FN void card_enchant_after_play(Core *c, const CardView *pc) {
    const Card *bound = &c->g.cards[ST(c)->playing_card];
    const CardEnchantRow *spec = card_enchant_spec(c, bound->has_card_status_enchant ? bound->card_status_enchant : pc->r.status_enchant);
    if (spec) {
        const TriggerRow *t = trig_row(c, spec->trigger);
        if (t && trig_has_phase(c, t, 3) && trigger_condition_matches(c, t, pc, NONE, -1, 0))
            card_enchant_fire(c, ST(c)->playing_card, spec);
        if (c->unsup) return;
    }
    if (c->predict || !enchant_holder_seen(c)) return;
    const int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    const int playing = ST(c)->playing_card;
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        const int slot = zget(c, zones[k], i);
        if (slot == playing || !after_play_holder(c, slot)) continue;
        const Card *b = &c->g.cards[slot];
        const int sid = b->has_card_status_enchant ? b->card_status_enchant : c->res[slot].status_enchant;
        const CardEnchantRow *row = 0;
        for (int j = 0; j < c->M->n_card_enchants; j++)
            if (c->M->card_enchants[j].id == sid) { row = &c->M->card_enchants[j]; break; }
        if (!row || row->trigger < 0) continue;
        const TriggerRow *t = trig_row(c, row->trigger);
        if (!t || !trig_has_phase(c, t, 3)) continue;
        const SearchRow *sr = t->search >= 0 ? search_row(c, t->search) : 0;
        if (sr && sr->is_self) continue;
        if (!trigger_condition_matches(c, t, pc, NONE, -1, 0)) { if (c->unsup) return; continue; }
        const CardEnchantRow *grow = card_enchant_spec(c, sid);
        if (c->unsup || !grow) return;
        card_enchant_fire(c, slot, grow, 0);
        if (c->unsup) return;
    }
}

GK_FN int64_t stance_count_base(Core *c, int slot, int direct) {
    Card *cd = &c->g.cards[slot];
    if (!cd->number.g) return 0;
    if (!cd->has_stance_count_base) {
        cd->has_stance_count_base = 1;
        cd->stance_count_base = ST(c)->direct_stance_change_count - (direct ? 1 : 0);
    }
    return cd->stance_count_base;
}

GK_FN void card_enchant_on_stance_change(Core *c, int new_type, int direct) {
    if (c->predict) return;
    const int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        int slot = zget(c, zones[k], i);
        const Card *bound = &c->g.cards[slot];
        const CardEnchantRow *spec = card_enchant_spec(c, bound->has_card_status_enchant ? bound->card_status_enchant : c->res[slot].status_enchant);
        if (!spec) continue;
        const TriggerRow *t = trig_row(c, spec->trigger);
        int fire = 0;
        if (trig_has_phase(c, t, 35)) {
            int64_t interval = t->n_pval ? P64(c, t->pval_off)[0] : 1;
            if (!interval) interval = 1;
            const int64_t count = ST(c)->direct_stance_change_count - stance_count_base(c, slot, direct);
            fire = direct && count > 0 && (2 * count) % interval == 0;
        } else {
            fire = spec->stance > 0 && spec->stance == new_type && (direct || new_type == GK_STANCE_FULL_POWER);
        }
        if (fire) card_enchant_fire(c, slot, spec);
        if (c->unsup) return;
    }
    const int playing = ST(c)->playing_card;
    if (!direct || playing < 0) return;
    for (int k = 0; k < 5; k++) if (zfind(c, zones[k], playing) >= 0) return;
    const Card *bound = &c->g.cards[playing];
    const CardEnchantRow *spec = card_enchant_spec(c, bound->has_card_status_enchant ? bound->card_status_enchant
                                                                                   : c->res[playing].status_enchant);
    if (c->unsup || !spec) return;
    const TriggerRow *t = trig_row(c, spec->trigger);
    if (!trig_has_phase(c, t, 35)) {
        if (spec->stance > 0 && spec->stance == new_type) card_enchant_fire(c, playing, spec, 0);
        return;
    }
    int64_t interval = t->n_pval ? P64(c, t->pval_off)[0] : 1;
    if (!interval) interval = 1;
    const int64_t count = ST(c)->direct_stance_change_count - stance_count_base(c, playing, direct);
    if (count > 0 && (2 * count) % interval == 0) card_enchant_fire(c, playing, spec, 0);
}

// the card being played grows for its later plays.
GK_FN void status_change_enchant(Core *c, int slot, int effect_type) {
    const Card *bound = &c->g.cards[slot];
    const int sid = bound->has_card_status_enchant ? bound->card_status_enchant : c->res[slot].status_enchant;
    if (sid < 0) return;
    const CardEnchantRow *row = 0;
    GK_COUNT(card_enchant_spec_calls, 1);
    for (int i = 0; i < c->M->n_card_enchants; i++) {
        GK_COUNT(card_enchant_rows, 1);
        if (c->M->card_enchants[i].id == sid) { row = &c->M->card_enchants[i]; break; }
    }
    if (!row || row->trigger < 0 || row->stance != -effect_type) return;
    const CardEnchantRow *spec = card_enchant_spec(c, sid);
    if (c->unsup || !spec) return;
    card_enchant_fire(c, slot, spec, 0);
}

GK_FN void card_enchant_on_status_change(Core *c, int effect_type) {
    if (c->predict) return;
    if (effect_type <= 0) return;
    if (effect_type < 64 && !((c->sce_mask >> effect_type) & 1)) return;
    const int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    const int playing = ST(c)->playing_card;
    int seen = 0;
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        const int slot = zget(c, zones[k], i);
        if (slot == playing) seen = 1;
        status_change_enchant(c, slot, effect_type);
        if (c->unsup) return;
    }
    if (playing >= 0 && !seen) status_change_enchant(c, playing, effect_type);
}

GK_FN void apply_card_grow(Core *c, const Eff *e) {
    const SearchRow *search = search_row(c, e->search);
    if (!search) return;
    ExamStateP *S = ST(c);
    const int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    int16_t picked[MAX_ROWS]; int n_picked = -1;
    if (e->pick_range_type == 1) {
        SRow sel[MAX_ROWS];
        n_picked = get_search_card_list(c, e, S->playing_card, sel);
        if (c->unsup) return;
        for (int i = 0; i < n_picked; i++) picked[i] = sel[i].slot;
    }
    int16_t slots[GKMS_MAX_CARDS]; int n = 0, found_playing = 0;
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        int slot = zget(c, zones[k], i);
        if (n >= GKMS_MAX_CARDS) UNSUP_RET(c, 156, );
        slots[n++] = (int16_t)slot;
        if (slot == S->playing_card) found_playing = 1;
    }
    if (search->pos == 13) {
        n = 0; found_playing = 1;
        for (int i = 0; i < zlen(c, Z_HOLD); i++) slots[n++] = (int16_t)zget(c, Z_HOLD, i);
    }
    if (search->pos != 2 && S->playing_card >= 0 && !found_playing) {
        if (n >= GKMS_MAX_CARDS) UNSUP_RET(c, 156, );
        slots[n++] = (int16_t)S->playing_card;
    }
    for (int i = 0; i < n; i++) {
        int slot = slots[i];
        if (n_picked >= 0) {
            int hit = 0;
            for (int j = 0; j < n_picked; j++) if (picked[j] == slot) hit = 1;
            if (!hit) continue;
        }
        const Card *cd = &c->g.cards[slot];
        if (search->pos == 2) {
            int found = 0;
            if (S->_in_turn_start && S->client_turn_end_order) {
                for (int j = 0; j < zlen(c, Z_HAND); j++) if (zget(c, Z_HAND, j) == slot) found = 1;
            } else {
                const CardNo *numbers = !S->_in_turn_start && c->live_hand_valid ? c->live_hand : S->turn_start_hand_numbers;
                int nn = !S->_in_turn_start && c->live_hand_valid ? c->n_live_hand : S->n_turn_start_hand_numbers;
                for (int j = 0; j < nn; j++) if (numbers[j].n == cd->number.n && numbers[j].g == cd->number.g) found = 1;
            }
            if (!found) continue;
        }
        if (!matches_search(c, slot, search)) continue;
        int32_t ids[GKMS_MAX_GROWS]; int ng = 0;
        for (int j = 0; j < e->n_grow; j++) {
            int32_t sid = P32(c, e->grow_off)[j];
            int gi = BY(c, 6, sid);
            int gt = gi < 0 ? 0 : c->M->grows[gi].type;
            if (gt == 50 || gt == 51 || gt == 52 || gt == 5 || gt == 22) {
                const Resolved *card = refresh_card(c, slot);
                if (c->unsup || !card) return;
                int found = 0, target = grow_to_effect_type(gt);
                for (int k = 0; k < card->n_play; k++) {
                    const Eff *effect = eff_ptr(c, card->play[k].effect);
                    if (effect && effect->type == target) found = 1;
                }
                if (!found) continue;
            }
            if (ng >= GKMS_MAX_GROWS) UNSUP_RET(c, 156, );
            ids[ng++] = sid;
        }
        if (ng) append_card_grows(c, slot, ids, ng);
        if (c->unsup) return;
    }
}

GK_FN void leisurely_to_full_power_grow(Core *c) {
    int32_t gid = c->M->sid_leisurely_grow;
    if (gid < 0) return;
    if (c->blog) bind_trace_row(c, -2, 156, gid);
    const int zones[5] = { Z_DECK, Z_HAND, Z_GRAVE, Z_LOST, Z_HOLD };
    int found_playing = 0;
    for (int k = 0; k < 5; k++) for (int i = 0; i < zlen(c, zones[k]); i++) {
        int slot = zget(c, zones[k], i);
        if (slot == ST(c)->playing_card) found_playing = 1;
        append_card_grows(c, slot, &gid, 1);
        if (c->unsup) return;
    }
    if (ST(c)->playing_card >= 0 && !found_playing) append_card_grows(c, ST(c)->playing_card, &gid, 1);
}
