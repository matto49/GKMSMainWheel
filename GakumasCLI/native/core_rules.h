// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 8: a timeline's rules (scenario/timeline.py RuleSet). A RuleTable is compiled in Python
// (GakumasCLI.native.native_search.rule_table) from a RoundPlan's rules. The search (core_search.h) and the exact
// tail (core_tail.h) apply it when the caller passes one; with no table (rules NULL) not one of these functions is
// called.
#pragma once
#include "core_session.h"

#define RT_MAX 64
#define RT_MAXKEYS 48

enum { RT_FORBID = 0, RT_REQUIRE = 1, RT_FIRST = 2, RT_PREFER = 3, RT_CAP = 4, RT_MS_HARD = 5, RT_MS_SOFT = 6,
       RT_NO_SKIP = 7, RT_MS_END = 8, RT_MS_GOAL = 9 };
#define RT_PEN_MAX ((int64_t)1 << 62)
enum { RT_T_DRINK = 0, RT_T_END = 1, RT_T_PLAY = 2, RT_T_NONE = 3 };
enum { RT_F_SCORE = 0, RT_F_STAMINA = 1, RT_F_BLOCK = 2, RT_F_LESSON_BUFF = 3, RT_F_REVIEW = 4,
       RT_F_AGGRESSIVE = 5, RT_F_FULL_POWER = 6,
       RT_F_LOST_SSR = 7, RT_F_DEBUFFS = 8,
       RT_F_SHARE_VO = 9, RT_F_SHARE_DA = 10, RT_F_SHARE_VI = 11 };

struct RuleOne {
    uint64_t abs_mask;
    uint64_t left_mask;
    int32_t mode, target;
    int32_t n_keys;
    int32_t key[RT_MAXKEYS];
    int32_t upg[RT_MAXKEYS];
    int32_t n;
    int32_t field, op;
    int64_t value;
    double weight;
    int64_t penalty;
};

struct RuleState {
    int64_t turn, acts, plays;
    uint64_t done, failed;
    int64_t pen;
};

struct RuleTable {
    int32_t n;
    int32_t version;
    int32_t has_prefer, has_milestones;
    RuleOne r[RT_MAX];
    RuleState root;
};

GK_FN int64_t rt_turn(const Core *c) { return c->g.s_turn; }
GK_FN int64_t rt_left(const Core *c) { return (int64_t)c->R->limit_turn + ST(c)->extra_turns - c->g.s_turn + 1; }

GK_FN int rt_active(const RuleOne *r, int64_t turn, int64_t left) {
    return (turn >= 0 && turn < 64 && ((r->abs_mask >> turn) & 1)) || (left >= 1 && left < 64 && ((r->left_mask >> left) & 1));
}

// active in a later turn of this round: absolute turns turn+1 ..
GK_FN int rt_later(const RuleOne *r, int64_t turn, int64_t left) {
    if (left <= 1) return 0;
    int64_t hi = turn + left - 1; if (hi > 63) hi = 63;
    if (turn + 1 <= hi) {
        uint64_t upto = hi >= 63 ? ~0ull : ((1ull << (hi + 1)) - 1);
        uint64_t below = (turn + 1) >= 64 ? ~0ull : ((1ull << (turn + 1)) - 1);
        if (r->abs_mask & (upto ^ below)) return 1;
    }
    uint64_t lm = left >= 64 ? (~0ull - 1) : ((1ull << left) - 2);
    return (r->left_mask & lm) != 0;
}

// kind 0 drink / 1 end / 2 play (the search's action kinds)
GK_FN int rt_match(const RuleOne *r, const Core *c, int kind, int idx) {
    if (r->target == RT_T_DRINK) {
        if (kind != 0) return 0;
        if (!r->n_keys) return 1;
        int32_t did = c->g.drinks[idx];
        for (int k = 0; k < r->n_keys; k++) if (r->key[k] == did) return 1;
        return 0;
    }
    if (r->target == RT_T_PLAY) {
        if (kind != 2) return 0;
        if (!r->n_keys) return 1;
        const Card *cd = &c->g.cards[zget(c, Z_HAND, idx)];
        for (int k = 0; k < r->n_keys; k++)
            if (r->key[k] == cd->produce_card_id && (r->upg[k] < 0 || r->upg[k] == cd->current_upgrade_count)) return 1;
        return 0;
    }
    if (r->target == RT_T_END) return kind == 1;
    return 0;
}

GK_FN void rt_sync(RuleState *rs, const Core *c) {
    int64_t turn = rt_turn(c);
    if (rs->turn != turn) { rs->turn = turn; rs->acts = 0; rs->plays = 0; }
}

// keep[i] for the n actions (kind[i], idx[i]); returns how many are kept (all when the rules would keep none)
GK_FN int rt_filter(const RuleTable *T, const RuleState *rs, const Core *c, int n, const int *kind, const int *idx,
                    uint8_t *keep) {
    int64_t turn = rt_turn(c), left = rt_left(c);
    for (int i = 0; i < n; i++) keep[i] = 1;
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if (r->mode == RT_FORBID && rt_active(r, turn, left)) {
            for (int i = 0; i < n; i++) if (keep[i] && rt_match(r, c, kind[i], idx[i])) keep[i] = 0;
        } else if (r->mode == RT_NO_SKIP && rt_active(r, turn, left)) {
            for (int i = 0; i < n; i++) if (kind[i] == 1) keep[i] = 0;
        }
    }
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if (r->mode == RT_CAP && rt_active(r, turn, left) && rs->plays >= r->n)
            for (int i = 0; i < n; i++) if (kind[i] == 2) keep[i] = 0;
    }
    if (rs->acts == 0) {
        for (int k = 0; k < T->n; k++) {
            const RuleOne *r = &T->r[k];
            if (r->mode != RT_FIRST || !rt_active(r, turn, left)) continue;
            int hit = 0;
            for (int i = 0; i < n; i++) if (keep[i] && rt_match(r, c, kind[i], idx[i])) hit++;
            if (hit) {
                for (int i = 0; i < n; i++) keep[i] = keep[i] && rt_match(r, c, kind[i], idx[i]);
                break;
            }
        }
    }
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if (r->mode != RT_REQUIRE || ((rs->done >> k) & 1) || !rt_active(r, turn, left) || rt_later(r, turn, left)) continue;
        int hit = 0;
        for (int i = 0; i < n; i++) if (keep[i] && rt_match(r, c, kind[i], idx[i])) hit++;
        if (hit) {
            for (int i = 0; i < n; i++) {
                int m = keep[i] && rt_match(r, c, kind[i], idx[i]);
                if (!m && r->target == RT_T_PLAY && keep[i] && kind[i] == 0) m = 1;
                keep[i] = (uint8_t)m;
            }
            break;
        }
    }
    int m = 0;
    for (int i = 0; i < n; i++) m += keep[i];
    if (!m) { for (int i = 0; i < n; i++) keep[i] = 1; m = n; }
    return m;
}

GK_FN double rt_prefer(const RuleTable *T, const Core *c, int kind, int idx) {
    int64_t turn = rt_turn(c), left = rt_left(c);
    double w = 0.0;
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if (r->mode == RT_PREFER && rt_active(r, turn, left) && rt_match(r, c, kind, idx)) w = w + r->weight;
    }
    return w;
}

// before the step; *turn / *left receive the turn the step starts in
GK_FN void rt_taken(const RuleTable *T, RuleState *rs, const Core *c, int kind, int idx, int64_t *turn_out, int64_t *left_out) {
    int64_t turn = rt_turn(c), left = rt_left(c);
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if (r->mode == RT_REQUIRE) {
            if (rt_active(r, turn, left) && rt_match(r, c, kind, idx)) rs->done |= 1ull << k;
        } else if (r->mode == RT_FIRST) {
            if (rs->acts == 0 && rt_active(r, turn, left) && rt_match(r, c, kind, idx)) rs->done |= 1ull << k;
        }
    }
    rs->acts++;
    if (kind == 2) rs->plays++;
    *turn_out = turn; *left_out = left;
}

GK_FN int64_t rt_field(const Core *c, int f) {
    switch (f) {
    case RT_F_SCORE: return ST(c)->score;
    case RT_F_STAMINA: return ST(c)->stamina;
    case RT_F_BLOCK: return ST(c)->block;
    case RT_F_LESSON_BUFF: return ST(c)->lesson_buff;
    case RT_F_REVIEW: return ST(c)->review;
    case RT_F_AGGRESSIVE: return ST(c)->aggressive;
    case RT_F_FULL_POWER: return ST(c)->full_power_point;
    case RT_F_LOST_SSR: {
        int64_t n = 0;
        for (int i = 0; i < zlen(c, Z_LOST); i++) {
            const Card *cd = &c->g.cards[zget(c, Z_LOST, i)];
            const CardRow *row = card_row(c, cd->produce_card_id, cd->current_upgrade_count);
            if (!row) row = card_row(c, cd->produce_card_id, 0);
            if (row && row->rarity == 4) n++;
        }
        return n;
    }
    case RT_F_DEBUFFS: return active_debuff_count(const_cast<Core *>(c));
    case RT_F_SHARE_VO: case RT_F_SHARE_DA: case RT_F_SHARE_VI: {
        int64_t vo = ST(c)->score_vocal, da = ST(c)->score_dance, vi = ST(c)->score_visual;
        int64_t total = vo + da + vi;
        if (total <= 0) return 0;
        int64_t x = f == RT_F_SHARE_VO ? vo : f == RT_F_SHARE_DA ? da : vi;
        return x * 1000 / total;
    }
    }
    return 0;
}

// weight x shortfall truncated toward zero, held within +-RT_PEN_MAX (timeline.py _weighted)
GK_FN int64_t rt_weighted(double weight, int64_t shortfall) {
    double d = weight * (double)shortfall;
    if (d >= (double)RT_PEN_MAX) return RT_PEN_MAX;
    if (d <= -(double)RT_PEN_MAX) return -RT_PEN_MAX;
    return (int64_t)d;
}
GK_FN int64_t rt_sat_add(int64_t a, int64_t b) {
    int64_t x = a + b;
    return x > RT_PEN_MAX ? RT_PEN_MAX : x < -RT_PEN_MAX ? -RT_PEN_MAX : x;
}

GK_FN void rt_after(const RuleTable *T, RuleState *rs, const Core *c, int64_t turn, int64_t left) {
    if (!T->has_milestones || (!c->g.s_finished && rt_turn(c) == turn)) return;
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if (r->mode == RT_MS_END) {
            if (!c->g.s_finished) continue;
        } else if ((r->mode != RT_MS_HARD && r->mode != RT_MS_SOFT) || !rt_active(r, turn, left)) continue;
        int64_t x = rt_field(c, r->field);
        int64_t shortfall = r->op == 0 ? r->value - x : x - r->value;
        if (shortfall > 0) {
            if (r->mode == RT_MS_HARD) rs->failed |= 1ull << k;
            else rs->pen = rt_sat_add(rs->pen, rt_weighted(r->weight, shortfall));
        }
    }
}

// the minor key's penalty: the soft shortfalls
GK_FN int64_t rt_penalty(const RuleTable *T, const RuleState *rs) {
    (void)T;
    return rs->pen;
}

// the major key of a finished line ``c``: unmet hard rules, then the goal's shortfall (RT_MS_GOAL)
GK_FN int64_t rt_major(const RuleTable *T, const RuleState *rs, const Core *c) {
    int64_t p = 0;
    for (int k = 0; k < T->n; k++) {
        const RuleOne *r = &T->r[k];
        if ((r->mode == RT_REQUIRE || r->mode == RT_FIRST) && !((rs->done >> k) & 1)) p = rt_sat_add(p, r->penalty);
        else if (r->mode == RT_MS_HARD && ((rs->failed >> k) & 1)) p = rt_sat_add(p, r->penalty);
        else if (r->mode == RT_MS_GOAL && c->g.s_finished) {
            int64_t x = rt_field(c, r->field);
            int64_t shortfall = r->op == 0 ? r->value - x : x - r->value;
            if (shortfall > 0) p = rt_sat_add(p, rt_weighted(r->weight, shortfall));
        }
    }
    return p;
}

GK_FN uint64_t rt_state_hash(const RuleState *rs) {
    uint64_t h = rs->done * 0x9E3779B97F4A7C15ull;
    h ^= (rs->failed + 0x77699297D34AD475ull) * 0xC2B2AE3D27D4EB4Full;
    h ^= ((uint64_t)rs->acts * 0x100000001B3ull + (uint64_t)rs->plays * 0x9DDFEA08EB382D69ull + (uint64_t)rs->turn) * 0x94D049BB133111EBull;
    h ^= (uint64_t)rs->pen * 0xBF58476D1CE4E5B9ull;
    h ^= h >> 31;
    return h;
}
