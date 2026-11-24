// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- (exam rules 63): シンクロ (ProducePlanType.Plan4) bundles in the hand.
#pragma once
#include "core_base.h"

// GetFusionCategoryOrder: Mental 0, Derive 1, Active 2, Trouble 3
GK_FN int sy_rank(const Core *c, int slot) {
    const Card *cd = &c->g.cards[slot];
    const CardRow *row = card_row(c, cd->produce_card_id, cd->current_upgrade_count);
    if (!row) row = card_row(c, cd->produce_card_id, 0);
    int k = row ? row->category : 0;
    return k == 2 ? 0 : k == 4 ? 1 : k == 1 ? 2 : k == 3 ? 3 : 4;
}

// EffectiveLimit(g) = max(0, Base(g) + delta g); Base = ExamSetting.examPlan4Group<g>FusionLimit, 4 when <= 0
GK_FN int64_t sy_limit(const Core *c, int g) {
    if (g < 1 || g > 4) return 0;
    int64_t base = c->M->es_fusion_limit[g - 1] > 0 ? c->M->es_fusion_limit[g - 1] : 4;
    int64_t v = base + c->g.st.fusion_delta[g - 1];
    return v < 0 ? 0 : v;
}

// the members of `top`'s bundle, in order
GK_FN int sy_members(const Core *c, int top, int16_t *out) {
    int n = 0;
    for (int i = 0; i < c->g.zone_n[Z_FUSED]; i++) {
        int s = c->g.zone[Z_FUSED][i];
        if (c->g.cards[s].fusion_top1 == top + 1) out[n++] = (int16_t)s;
    }
    return n;
}

GK_FN void sy_unlink(Core *c, int slot) {
    int i = zfind(c, Z_FUSED, slot);
    if (i >= 0) zpop(c, Z_FUSED, i);
    c->g.cards[slot].fusion_top1 = 0;
}

// RebuildFusionOrder(orderedTopToBottom): the cards of a bundle by category rank, cards of the same id kept together
// (at their first one's place), otherwise in their order; the first is the top, at hand index hi
GK_FN void sy_set_bundle(Core *c, int hi, const int16_t *list, int n) {
    int16_t ord[GKMS_MAX_CARDS]; int rank[GKMS_MAX_CARDS], first[GKMS_MAX_CARDS];
    for (int k = 0; k < n; k++) {
        sy_unlink(c, list[k]);
        rank[k] = sy_rank(c, list[k]);
        first[k] = k;
        for (int j = 0; j < k; j++)
            if (rank[j] == rank[k] && c->g.cards[list[j]].produce_card_id == c->g.cards[list[k]].produce_card_id) {
                first[k] = first[j]; break;
            }
        ord[k] = (int16_t)k;
    }
    for (int i = 1; i < n; i++) {
        int16_t x = ord[i]; int j = i - 1;
        while (j >= 0 && (rank[ord[j]] > rank[x] || (rank[ord[j]] == rank[x] && first[ord[j]] > first[x]))) {
            ord[j + 1] = ord[j]; j--;
        }
        ord[j + 1] = x;
    }
    int top = list[ord[0]];
    c->g.zone[Z_HAND][hi] = (int16_t)top;
    for (int k = 1; k < n; k++) {
        zappend(c, Z_FUSED, list[ord[k]]);
        c->g.cards[list[ord[k]]].fusion_top1 = (int16_t)(top + 1);
    }
}

// the hand index of the bundle `slot` (with its own members) joins: the first, from the left, of the same group (not
// Unknown) with base.FusionCount + target.FusionCount <= EffectiveLimit; -1 none
GK_FN int sy_fuse_target(const Core *c, int slot) {
    int g = sy_group(c, slot);
    if (!g) return -1;
    int need = sy_count(c, slot);
    int64_t lim = sy_limit(c, g);
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int t = zget(c, Z_HAND, i);
        if (t == slot || sy_group(c, t) != g) continue;
        if (sy_count(c, t) + need <= lim) return i;
    }
    return -1;
}

GK_FN void sy_merge(Core *c, int hi, int slot) {
    int16_t list[GKMS_MAX_CARDS]; int n = 0;
    int t = zget(c, Z_HAND, hi);
    list[n++] = (int16_t)t; n += sy_members(c, t, list + n);
    list[n++] = (int16_t)slot; n += sy_members(c, slot, list + n);
    sy_set_bundle(c, hi, list, n);
}

// a card entering the hand (OnHandAdd): into the first bundle it can join, else a slot of its own at the end
GK_FN void sy_enter_hand(Core *c, int slot) {
    int hi = sy_fuse_target(c, slot);
    if (hi >= 0) sy_merge(c, hi, slot);
    else zappend(c, Z_HAND, slot);
}

// PlanFusion over the whole hand (after a group change; RunFusionRecheckAfterLimitIncrease): the hand's cards in
// order -- each slot's top, then its members -- enter again from the left
GK_FN void sy_replan(Core *c) {
    int16_t all[GKMS_MAX_CARDS]; int n = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int t = zget(c, Z_HAND, i);
        all[n++] = (int16_t)t;
        n += sy_members(c, t, all + n);
    }
    c->g.zone_n[Z_HAND] = 0; c->g.zone_n[Z_FUSED] = 0;
    for (int k = 0; k < n; k++) c->g.cards[all[k]].fusion_top1 = 0;
    for (int k = 0; k < n; k++) sy_enter_hand(c, all[k]);
}

// a hand card leaving by itself (an effect's move): a member leaves its bundle; a top's members stay in its slot, the
// first of them by bundle order the new top (OnHandRemove's remainingSlotTopCardList)
GK_FN void sy_leave_hand(Core *c, int zone, int slot) {
    if (zone == Z_FUSED) { sy_unlink(c, slot); return; }
    int i = zfind(c, Z_HAND, slot);
    if (i < 0) { UNSUP(c, slot); return; }
    int16_t mem[GKMS_MAX_CARDS];
    int n = sy_members(c, slot, mem);
    if (!n) { zpop(c, Z_HAND, i); return; }
    sy_set_bundle(c, i, mem, n);
}

// the hand's members after each top, for the walks that take every hand card (the turn end, effect 98)
GK_FN int sy_hand_all(const Core *c, int16_t *out) {
    int n = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) {
        int t = zget(c, Z_HAND, i);
        out[n++] = (int16_t)t;
        if (c->R->synchro) n += sy_members(c, t, out + n);
    }
    return n;
}
GK_FN void sy_clear_fused(Core *c) {
    for (int i = 0; i < c->g.zone_n[Z_FUSED]; i++) c->g.cards[c->g.zone[Z_FUSED][i]].fusion_top1 = 0;
    c->g.zone_n[Z_FUSED] = 0;
}
