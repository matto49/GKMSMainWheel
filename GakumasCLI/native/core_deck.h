// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 2: SegmentDeck (src/neu/deck.py).
#pragma once
#include "core_base.h"
#include "core_synchro_base.h"

GK_FN int tier_exists(const Core *c, int32_t card_sid, int64_t upg) {
    if (card_sid < 0 || upg < 0 || upg >= 8) return 0;
    return (c->R->tier_mask[card_sid] >> upg) & 1;
}

// shuffle.py shuffle_deck (deck.py _shuffle)
GK_FN void deck_shuffle(Core *c) {
    int n = zlen(c, Z_DECK);
    for (int size = n; size > 1; size--) {
        int idx = (int)rng_range(c, 0, size);
        int16_t t = c->g.zone[Z_DECK][size - 1];
        c->g.zone[Z_DECK][size - 1] = c->g.zone[Z_DECK][idx];
        c->g.zone[Z_DECK][idx] = t;
    }
}

GK_FN int support_used(const Core *c, int32_t row) {
    for (int i = 0; i < c->g.d_n_turn_used_support_ids; i++) if (c->g.d_turn_used_support_ids[i] == row) return 1;
    return 0;
}

// deck.py _consume_support_rolls
GK_FN void consume_support_rolls(Core *c, int slot) {
    if (c->R->n_supports == 0) return;
    if (c->support_rolls_off) return;
    if (!c->trigger_commands_enabled) return;
    Card *cd = &c->g.cards[slot];
    int64_t q = cd->current_upgrade_count;
    if (!tier_exists(c, cd->produce_card_id, q + 1)) return;
    for (int i = 0; i < c->R->n_supports; i++) {
        const Support *s = &c->R->supports[i];
        int pm = (s->parameter_type == 0 || s->parameter_type == -1 || s->parameter_type == c->g.d_current_parameter_type);
        if (!pm) continue;
        if (support_used(c, s->row)) continue;
        if (s->csp != 2) continue;
        int64_t roll = rng_range(c, 0, 1000);
        if (roll < s->permil) {
            if (c->g.d_n_turn_used_support_ids >= GKMS_MAX_SMALL) { UNSUP(c, 0); return; }
            c->g.d_turn_used_support_ids[c->g.d_n_turn_used_support_ids++] = s->row;
            if (tier_exists(c, cd->produce_card_id, cd->current_upgrade_count + 1)) {
                if (cd->n_support_upgrade_ids >= GKMS_MAX_SUPPORT_UP) { UNSUP(c, 0); return; }
                cd->support_upgrade_ids[cd->n_support_upgrade_ids++] = s->row;
                refresh_card(c, slot);
            }
        }
    }
}

GK_FN void queue_move_effects(Core *c, int slot) {
    const Card *cd = &c->g.cards[slot];
    const CardRow *row = card_row(c, cd->produce_card_id, cd->current_upgrade_count);
    if (!row) row = card_row(c, cd->produce_card_id, 0);
    if (!row || row->move_trigger != 5 || row->n_mv <= 0) return;
    GkmsState *g = &c->g;
    if (g->d_n_pending_move >= GKMS_MAX_PENDING_MOVE || row->n_mv > 4) { UNSUP(c, slot); return; }
    int i = g->d_n_pending_move++;
    g->d_pending_move_slot[i] = (int16_t)slot;
    g->d_pending_move_n[i] = row->n_mv;
    for (int k = 0; k < row->n_mv; k++) g->d_pending_move_eff[i][k] = P32(c, row->mv_off)[k];
}

// deck.py add_hand_difference (cards appended to the hand in order)
GK_FN void add_hand_difference(Core *c, const int16_t *slots, int n) {
    if (c->R->synchro) for (int i = 0; i < n; i++) sy_enter_hand(c, slots[i]);
    else for (int i = 0; i < n; i++) zappend(c, Z_HAND, slots[i]);
    for (int i = 0; i < n; i++) {
        Card *cd = &c->g.cards[slots[i]];
        if (c->g.d_get_card_upgrade_count > 0 && cd->base_upgrade_count + cd->temporary_upgrade_count == 0
            && tier_exists(c, cd->produce_card_id, 1)) {
            c->g.d_get_card_upgrade_count -= 1;
            cd->temporary_upgrade_count = 1;
            refresh_card(c, slots[i]);
        }
        consume_support_rolls(c, slots[i]);
        queue_move_effects(c, slots[i]);
    }
}

// deck.py _draw
GK_FN int deck_draw(Core *c, int count, int16_t *out) {
    int nd = 0;
    while (nd < count) {
        int take = count - nd;
        if (take > zlen(c, Z_DECK)) take = zlen(c, Z_DECK);
        for (int i = 0; i < take; i++) out[nd++] = (int16_t)zpop(c, Z_DECK, 0);
        if (nd < count) {
            int ng = zlen(c, Z_GRAVE);
            for (int i = 0; i < ng; i++) zappend(c, Z_DECK, zget(c, Z_GRAVE, i));
            c->g.zone_n[Z_GRAVE] = 0;
            deck_shuffle(c);
            if (zlen(c, Z_DECK) == 0) { UNSUP(c, count); return nd; }
        }
    }
    return nd;
}

// PlanCardsForHandLimitWithFusion -- the hand limit counts slots, and a card that joins a bundle takes none: the
// cards are drawn one by one while the hand has a slot or the next card joins a bundle
GK_FN int sy_draw_cards(Core *c, int64_t count, int16_t *out) {
    int n = 0;
    while (n < count) {
        if (zlen(c, Z_DECK) == 0) {
            if (zlen(c, Z_GRAVE) == 0) break;
            int ng = zlen(c, Z_GRAVE);
            for (int i = 0; i < ng; i++) zappend(c, Z_DECK, zget(c, Z_GRAVE, i));
            c->g.zone_n[Z_GRAVE] = 0;
            deck_shuffle(c);
        }
        int next = zget(c, Z_DECK, 0);
        if (zlen(c, Z_HAND) >= c->g.d_hand_limit && sy_fuse_target(c, next) < 0) break;
        int16_t s = (int16_t)zpop(c, Z_DECK, 0);
        out[n++] = s;
        add_hand_difference(c, &s, 1);
        if (c->unsup) return n;
    }
    return n;
}

// deck.py draw_cards
GK_FN int draw_cards(Core *c, int64_t count, int16_t *out) {
    if (c->R->synchro) return sy_draw_cards(c, count, out);
    int64_t dc = imin(count, (int64_t)zlen(c, Z_DECK) + zlen(c, Z_GRAVE));
    dc = imin(dc, imax(0, c->g.d_hand_limit - zlen(c, Z_HAND)));
    if (dc < 0) dc = 0;
    int n = deck_draw(c, (int)dc, out);
    add_hand_difference(c, out, n);
    return n;
}

// deck.py end_turn (deck side)
GK_FN void deck_end_turn(Core *c) {
    if (c->g.d_is_hand_hold) return;
    int16_t all[GKMS_MAX_CARDS];
    int n = sy_hand_all(c, all);
    if (c->R->synchro) sy_clear_fused(c);
    for (int i = 0; i < n; i++) {
        int s = all[i];
        Card *cd = &c->g.cards[s];
        if (cd->n_support_upgrade_ids) { cd->n_support_upgrade_ids = 0; refresh_card(c, s); }
        if (cd->is_end_turn_lost) zappend(c, Z_LOST, s);
        else { cd->grave_source = c->M->sid_reset_hand; zappend(c, Z_GRAVE, s); }
    }
    c->g.zone_n[Z_HAND] = 0;
}

// deck.py reset_card_upgrades
GK_FN void reset_card_upgrades(Core *c, int s) {
    Card *cd = &c->g.cards[s];
    if (cd->n_support_upgrade_ids) cd->n_support_upgrade_ids = 0;
    refresh_card(c, s);
}

// deck.py finish_play
GK_FN void finish_play(Core *c, int s) {
    reset_card_upgrades(c, s);
    Card *cd = &c->g.cards[s];
    if (cd->type9_moved >= 0) {
        int v = cd->type9_moved;
        cd->type9_moved = -1;
        if (v) return;
    }
    int mp = c->R->play_move_position_type_enabled ? cd->play_move_position_type : 5;
    if (mp == 1) { int16_t one = (int16_t)s; add_hand_difference(c, &one, 1); }
    else if (mp == 2) zinsert(c, Z_DECK, 0, s);
    else if (mp == 3) zappend(c, Z_DECK, s);
    else if (mp == 4) zinsert(c, Z_DECK, (int)rng_range(c, 0, zlen(c, Z_DECK)), s);
    else if (mp == 6) zappend(c, Z_LOST, s);
    else if (mp == 7) {
        if (zlen(c, Z_HOLD) >= c->g.d_hold_limit && zlen(c, Z_HOLD) > 0) zappend(c, Z_GRAVE, zpop(c, Z_HOLD, 0));
        zappend(c, Z_HOLD, s);
    } else if (mp == 8 && c->R->synchro) {
        zappend(c, Z_REMOVE, s); c->sy_removed |= uint64_t(1) << (s & 63);
    } else { cd->grave_source = c->M->sid_post_play; zappend(c, Z_GRAVE, s); }
}

GK_FN int new_card(Core *c, int32_t pid, int64_t upg) {
    if (c->g.n_cards >= GKMS_MAX_CARDS) UNSUP_RET(c, 0, -1);
    int s = c->g.n_cards++;
    Card *cd = &c->g.cards[s];
    memset(cd, 0, sizeof(*cd));
    c->g.d_generated_card_count += 1;
    cd->number.n = (int32_t)c->g.d_generated_card_count; cd->number.g = 1;
    cd->guid = cd->number;
    cd->produce_card_id = pid;
    cd->base_upgrade_count = (int32_t)upg; cd->current_upgrade_count = (int32_t)upg;
    cd->temporary_upgrade_count = 0; cd->n_support_upgrade_ids = 0; cd->is_end_turn_lost = 0;
    cd->has_customizes = 0; cd->has_origin_type = 0; cd->has_is_initial = 0;
    cd->grave_source = -1; cd->play_trigger_id = -1; cd->type9_moved = -1;
    cd->panic_stamina_fix = NONE; cd->has_panic_stamina_fix = 0;
    cd->panic_stamina_until_turn = NONE; cd->has_panic_stamina_until_turn = 0;
    cd->force_pays_cost = -1;
    refresh_card(c, s);
    return s;
}

// deck.py create_card_deck_random / create_cards_deck_random (single card)
GK_FN void create_card_deck_random(Core *c, int32_t pid, int64_t upg) {
    int s = new_card(c, pid, upg);
    if (s < 0) return;
    int64_t before = zlen(c, Z_DECK);
    int64_t idx = rng_range(c, 0, before);
    zinsert(c, Z_DECK, (int)idx, s);
}

// deck.py:create_cards_deck_random: sample every insertion against the pre-batch deck length, then apply placements
// in ascending index order.
GK_HEAVY void create_cards_deck_random_batch(Core *c, const int32_t *pid, const int64_t *upg, int n) {
    if (n < 0 || n > GKMS_MAX_CARDS) { UNSUP(c, n); return; }
    int slots[GKMS_MAX_CARDS], pos[GKMS_MAX_CARDS], order[GKMS_MAX_CARDS];
    int before = zlen(c, Z_DECK);
    for (int i = 0; i < n; i++) {
        slots[i] = new_card(c, pid[i], upg[i]);
        if (slots[i] < 0 || c->unsup) return;
        pos[i] = (int)rng_range(c, 0, before); order[i] = i;
    }
    for (int i = 1; i < n; i++) {
        int x = order[i], j = i - 1;
        while (j >= 0 && pos[order[j]] > pos[x]) { order[j + 1] = order[j]; j--; }
        order[j + 1] = x;
    }
    for (int i = 0; i < n; i++) zinsert(c, Z_DECK, pos[order[i]], slots[order[i]]);
}

// deck.py create_card_hand
GK_FN int create_card_hand(Core *c, int32_t pid, int64_t upg) {
    int s = new_card(c, pid, upg);
    if (s < 0) return -1;
    int16_t one = (int16_t)s;
    add_hand_difference(c, &one, 1);
    return s;
}

// greedy_select_policy (deck.py greedy_select_policy): sort candidates by value_of desc, stable.
struct SelKey { int64_t total, upg, order; int32_t num_g; int32_t num_n; };
GK_FN int cmp_str_number(const SelKey *a, const SelKey *b) {
    char sa[24], sb[24];
    int la = 0, lb = 0;
    char tmp[24]; int t;
    if (a->num_g) sa[la++] = 'G';
    { int64_t v = a->num_n; int neg = v < 0; if (neg) v = -v; t = 0; do { tmp[t++] = (char)('0' + v % 10); v /= 10; } while (v);
      if (neg) sa[la++] = '-'; while (t) sa[la++] = tmp[--t]; }
    if (b->num_g) sb[lb++] = 'G';
    { int64_t v = b->num_n; int neg = v < 0; if (neg) v = -v; t = 0; do { tmp[t++] = (char)('0' + v % 10); v /= 10; } while (v);
      if (neg) sb[lb++] = '-'; while (t) sb[lb++] = tmp[--t]; }
    int m = la < lb ? la : lb;
    for (int i = 0; i < m; i++) if (sa[i] != sb[i]) return (unsigned char)sa[i] < (unsigned char)sb[i] ? -1 : 1;
    return la < lb ? -1 : (la > lb ? 1 : 0);
}
GK_FN int selkey_cmp(const SelKey *a, const SelKey *b) {
    if (a->total != b->total) return a->total < b->total ? -1 : 1;
    if (a->upg != b->upg) return a->upg < b->upg ? -1 : 1;
    if (a->order != b->order) return a->order < b->order ? -1 : 1;
    return cmp_str_number(a, b);
}
GK_FN SelKey sel_value_of(const Core *c, int slot) {
    SelKey k;
    const Resolved *r = &c->res[slot];
    k.total = 0;
    for (int i = 0; i < r->n_play; i++) k.total += r->play[i].v1;
    const Card *cd = &c->g.cards[slot];
    k.upg = cd->current_upgrade_count;
    k.num_g = cd->number.g; k.num_n = cd->number.n;
    k.order = cd->number.g ? 0 : -(int64_t)cd->number.n;
    return k;
}
