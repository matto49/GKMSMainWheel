// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 4: card searches (src/neu/effects.py).
#pragma once
#include "core_exam.h"

// the rest go, in order, to the top of the deck (AddCard -> AddCard(..., DECK_FIRST)).
GK_FN void place_in_hand(Core *c, const int16_t *slots, int n) {
    if (c->R->synchro) {
        int over = 0;
        for (int k = 0; k < n; k++) {
            if (zlen(c, Z_HAND) < c->g.d_hand_limit || sy_fuse_target(c, slots[k]) >= 0) add_hand_difference(c, &slots[k], 1);
            else zinsert(c, Z_DECK, over++, slots[k]);
        }
        return;
    }
    int space = (int)imax(0, c->g.d_hand_limit - zlen(c, Z_HAND));
    int nh = n < space ? n : space;
    for (int k = nh; k < n; k++) zinsert(c, Z_DECK, k - nh, slots[k]);
    if (nh > 0) add_hand_difference(c, slots, nh);
}

struct SRow { int16_t slot; int8_t zone; int8_t pos; int16_t index; int16_t order; };
#define MAX_ROWS 80

GK_FN int pos_of_zone(int z) { return z == Z_HAND || z == Z_FUSED ? 2 : z == Z_DECK ? 3 : z == Z_GRAVE ? 4 : z == Z_LOST ? 5
                                              : z == Z_REMOVE ? 17 : 13; }

// effects.py _sortable_number: the DECK_ALL sort key is (0, int(number)) for the master's integer numbers and (1,
// str(number)) for generated "G<n>" handles -- integers first, then each group by value, never by decimal text.
GK_FN int cardno_cmp(const CardNo *a, const CardNo *b) {
    if (a->g != b->g) return a->g ? 1 : -1;
    if (!a->g) return a->n < b->n ? -1 : (a->n > b->n ? 1 : 0);
    char sa[24], sb[24]; int la = 0, lb = 0; int64_t va = a->n, vb = b->n;
    sa[la++] = 'G'; sb[lb++] = 'G';
    { char t[20]; int n = 0; do { t[n++] = (char)('0' + va % 10); va /= 10; } while (va); while (n) sa[la++] = t[--n]; }
    { char t[20]; int n = 0; do { t[n++] = (char)('0' + vb % 10); vb /= 10; } while (vb); while (n) sb[lb++] = t[--n]; }
    int m = la < lb ? la : lb; for (int i = 0; i < m; i++) if (sa[i] != sb[i]) return (unsigned char)sa[i] < (unsigned char)sb[i] ? -1 : 1;
    return la < lb ? -1 : la > lb ? 1 : 0;
}

// effects.py _source_cards: the rows of a search position, in source order.
GK_FN int source_keep(int position, int p) {
    if (position == 6) return 1;
    if (position == 2 || position == 3 || position == 4 || position == 5 || position == 13 || position == 8 || position == 11) return p == position;
    if (position == 14) return p == 3 || p == 4;
    if (position == 15) return p != 5;
    return 0;
}
// (TARGET uses Core::target_card when supplied)
GK_HEAVY int source_cards(Core *c, int position, int playing, SRow *rows) {
    static const int ZORD[5] = { Z_HAND, Z_DECK, Z_GRAVE, Z_LOST, Z_HOLD };
    int n = 0;
    if (c->R->synchro && position == 17) {
        for (int i = 0; i < zlen(c, Z_REMOVE); i++) {
            if (n >= MAX_ROWS) UNSUP_RET(c, n, 0);
            SRow *r = &rows[n];
            r->slot = (int16_t)zget(c, Z_REMOVE, i); r->zone = Z_REMOVE; r->pos = 17; r->index = (int16_t)i;
            r->order = (int16_t)n; n++;
        }
        return n;
    }
    if (position == 8 || position == 11 || (c->R->synchro && position == 16)) {
        int target = position == 8 ? playing : position == 16 ? c->sy_playing_top : c->target_card;
        if (target < 0) return 0;
        SRow r; r.slot = (int16_t)target; r.zone = -1; r.pos = (int8_t)position; r.index = 0; r.order = 0;
        for (int k = 0; k < 5 && r.zone < 0; k++) {
            int i = zfind(c, ZORD[k], target);
            if (i >= 0) { r.zone = (int8_t)ZORD[k]; r.index = (int16_t)i; }
        }
        if (r.zone < 0 && c->R->synchro) {
            int i = zfind(c, Z_FUSED, target);
            if (i >= 0) { r.zone = Z_FUSED; r.index = (int16_t)i; }
        }
        rows[0] = r;
        return 1;
    }
    int found_playing = 0, found_target = 0;
    for (int k = 0; k < 5; k++) {
        int z = ZORD[k], p = pos_of_zone(z), keep = source_keep(position, p);
        for (int i = 0; i < zlen(c, z); i++) {
            int s = zget(c, z, i);
            if (s == playing) found_playing = 1;
            if (s == c->target_card) found_target = 1;
            if (!keep) continue;
            if (n >= MAX_ROWS) UNSUP_RET(c, n, 0);
            SRow *r = &rows[n];
            r->slot = (int16_t)s; r->zone = (int8_t)z; r->pos = (int8_t)p; r->index = (int16_t)i; r->order = (int16_t)n;
            n++;
            if (z == Z_HAND && c->R->synchro) {
                for (int j = 0; j < zlen(c, Z_FUSED); j++) {
                    int m = zget(c, Z_FUSED, j);
                    if (c->g.cards[m].fusion_top1 != s + 1) continue;
                    if (m == playing) found_playing = 1;
                    if (m == c->target_card) found_target = 1;
                    if (n >= MAX_ROWS) UNSUP_RET(c, n, 0);
                    SRow *q = &rows[n];
                    q->slot = (int16_t)m; q->zone = Z_FUSED; q->pos = 2; q->index = (int16_t)j; q->order = (int16_t)n;
                    n++;
                }
            }
        }
    }
    if (playing >= 0 && !found_playing && source_keep(position, 8)) {
        if (n >= MAX_ROWS) UNSUP_RET(c, n, 0);
        SRow *r = &rows[n];
        r->slot = (int16_t)playing; r->zone = -1; r->pos = 8; r->index = 0; r->order = (int16_t)n;
        n++;
    }
    if (c->target_card >= 0 && !found_target && c->target_card != playing && source_keep(position, 11)) {
        if (n >= MAX_ROWS) UNSUP_RET(c, n, 0);
        SRow *r = &rows[n];
        r->slot = (int16_t)c->target_card; r->zone = -1; r->pos = 11; r->index = 0; r->order = (int16_t)n;
        n++;
    }
    if (position == 6) {
        for (int i = 1; i < n; i++) {
            SRow x = rows[i]; int j = i - 1;
            while (j >= 0) {
                const CardNo *a = &c->g.cards[rows[j].slot].number;
                const CardNo *b = &c->g.cards[x.slot].number;
                int cmp = cardno_cmp(a, b);
                if (cmp <= 0) break;
                rows[j + 1] = rows[j]; j--;
            }
            rows[j + 1] = x;
        }
        for (int i = 0; i < n; i++) rows[i].order = (int16_t)i;
    }
    return n;
}

// effects.py _matches_search
GK_FN int matches_search(const Core *c, int slot, const SearchRow *s);

// exam.py _card_search_count: rows of the search's position that match, capped at limitCount --
// GetSearchCardList.Count for status 51.
GK_OUTLINE int64_t card_search_count(Core *c, int32_t sid) {
    const SearchRow *sr = search_row(c, sid);
    if (!sr) UNSUP_RET(c, sid, 0);
    SRow rows[MAX_ROWS];
    int n = source_cards(c, sr->pos, ST(c)->playing_card, rows);
    if (c->unsup) return 0;
    int64_t cnt = 0;
    for (int i = 0; i < n; i++) if (matches_search(c, rows[i].slot, sr)) cnt++;
    if (sr->has_limit && cnt > sr->limit) cnt = sr->limit;
    return cnt;
}

// a card instance in a zone (effects.py _matches_search): every group, its upgrade count
GK_FN int matches_search(const Core *c, int slot, const SearchRow *s) {
    const Card *cd = &c->g.cards[slot];
    return search_match(c, s, &c->res[slot], cd->produce_card_id, cd->current_upgrade_count, 0, slot);
}

// stable sort helpers (insertion sort; n is small)
GK_FN void sort_rows_by_key(SRow *rows, int64_t *keys, int n) {
    for (int i = 1; i < n; i++) {
        SRow r = rows[i]; int64_t k = keys[i]; int j = i - 1;
        while (j >= 0 && keys[j] > k) { rows[j + 1] = rows[j]; keys[j + 1] = keys[j]; j--; }
        rows[j + 1] = r; keys[j + 1] = k;
    }
}

// effects.py _upgradable: a card a lesson upgrade (effect 11) can still raise
GK_FN int card_upgradable(const Core *c, int slot) {
    const Card *cd = &c->g.cards[slot];
    return cd->base_upgrade_count + cd->temporary_upgrade_count == 0 && tier_exists(c, cd->produce_card_id, 1);
}

// effects.py records the stand-in's pick in deck.select_choices (the binding reads it from Core.blog)
GK_BIND void bind_select(Core *c, const Eff *e, int64_t pmin, int64_t pmax, const SRow *f, int nf, const SRow *out, int m) {
    BindLog *b = c->blog;
    if (b->n_selects >= GK_BIND_SELECTS || nf > GK_BIND_CANDS) { b->overflow = 1; return; }
    BindSelect *x = &b->selects[b->n_selects++];
    x->effect = e->id; x->search = e->search; x->pick_min = pmin; x->pick_max = pmax;
    x->n_cand = nf; x->n_picked = m;
    for (int i = 0; i < nf; i++) x->cand[i] = c->g.cards[f[i].slot].number;
    for (int i = 0; i < m; i++) x->picked[i] = c->g.cards[out[i].slot].number;
}

GK_FN void select_queue_pop(SelectQueue *q) {
    for (int i = 0; i + 1 < q->n; i++) {
        q->len[i] = q->len[i + 1];
        for (int j = 0; j < GKMS_MAX_SMALL; j++) q->v[i][j] = q->v[i + 1][j];
    }
    q->n--;
}

// effects.py get_search_card_list.
GK_HEAVY int get_search_card_list(Core *c, const Eff *e, int playing, SRow *out, int upg_only = 0) {
    const SearchRow *s = search_row(c, e->search);
    if (!s) UNSUP_RET(c, e->search, 0);
    if (s->pos == 7) UNSUP_RET(c, 7, 0);
    SRow raw[MAX_ROWS];
    int nraw = source_cards(c, s->pos, playing, raw);
    if (c->unsup) return 0;
    SRow f[MAX_ROWS]; int nf = 0;
    for (int i = 0; i < nraw; i++) if (matches_search(c, raw[i].slot, s)) f[nf++] = raw[i];
    if (s->order_type == 2) {
        for (int i = 0; i < nf / 2; i++) { SRow t = f[i]; f[i] = f[nf - 1 - i]; f[nf - 1 - i] = t; }
    } else if (s->order_type == 3) {
        if (playing >= 0) for (int i = 0; i < nf; i++) rng_signed_key(c);
        int64_t keys[MAX_ROWS];
        for (int i = 0; i < nf; i++) keys[i] = rng_signed_key(c);
        sort_rows_by_key(f, keys, nf);
    }
    if (s->has_limit) {
        int64_t lim = s->limit;
        if (lim < 0) lim += nf;
        if (lim < 0) lim = 0;
        if (lim < nf) nf = (int)lim;
    }
    int pt = e->pick_range_type;
    if (pt == 1) {
        ExamStateP *S = ST(c);
        if (!nf && S->select_index_queue.n && !S->select_index_queue.len[0]) { select_queue_pop(&S->select_index_queue); return 0; }
        if (nf && !S->select_index_queue.n && c->select_cb) {
            int32_t slots[MAX_ROWS]; int64_t idx[GKMS_MAX_SMALL];
            for (int i = 0; i < nf; i++) slots[i] = f[i].slot;
            int len = c->select_cb(c, e->id, e->has_pick_min ? e->pick_min : 0, e->has_pick_max ? e->pick_max : 0,
                                   slots, nf, idx);
            if (len >= 0) {
                if (len > GKMS_MAX_SMALL) len = GKMS_MAX_SMALL;
                for (int j = 1; j < len; j++) {
                    int64_t v = idx[j]; int k = j - 1;
                    while (k >= 0 && idx[k] > v) { idx[k + 1] = idx[k]; k--; }
                    idx[k + 1] = v;
                }
                int m = 0;
                for (int j = 0; j < len; j++) if (idx[j] >= 0 && idx[j] < nf) out[m++] = f[idx[j]];
                if (c->blog) bind_select(c, e, e->has_pick_min ? e->pick_min : 0, e->has_pick_max ? e->pick_max : 0,
                                         f, nf, out, m);
                return m;
            }
        }
        if (nf && S->select_index_queue.n) {
            SelectQueue *q = &S->select_index_queue;
            int len = q->len[0]; int64_t idx[GKMS_MAX_SMALL];
            for (int j = 0; j < len; j++) idx[j] = q->v[0][j];
            select_queue_pop(q);
            for (int j = 1; j < len; j++) {
                int64_t v = idx[j]; int k = j - 1;
                while (k >= 0 && idx[k] > v) { idx[k + 1] = idx[k]; k--; }
                idx[k + 1] = v;
            }
            int m = 0;
            for (int j = 0; j < len; j++) if (idx[j] >= 0 && idx[j] < nf) out[m++] = f[idx[j]];
            if (m != len) return 0;
            return m;
        }
        int rank_req = c->sel_pick; c->sel_pick = 0;
        int64_t pmin = e->has_pick_min ? e->pick_min : 0;
        int64_t pmax = e->has_pick_max ? e->pick_max : 0;
        if (nf && (pmin > 0 || pmax > 0)) {
            if (!c->R->select_greedy) return 0;
            int64_t count = pmin > 0 ? imax(1, pmin) : pmax;
            int64_t mx = pmax ? pmax : count;
            count = imin(imin(count, mx), nf);
            int8_t fpos[MAX_ROWS];
            for (int i = 0; i < nf; i++) fpos[i] = (int8_t)i;
            SelKey keys[MAX_ROWS];
            for (int i = 0; i < nf; i++) keys[i] = sel_value_of(c, f[i].slot);
            for (int i = 1; i < nf; i++) {
                int8_t fp = fpos[i]; int j = i - 1;
                while (j >= 0 && selkey_cmp(&keys[fpos[j]], &keys[fp]) < 0) { fpos[j + 1] = fpos[j]; j--; }
                fpos[j + 1] = fp;
            }
            int m = (int)count;
            if (nf > m && rank_req <= 0) c->greedy_choices++;
            if (rank_req > 0 && m == 1 && nf > 1) {
                int rank = rank_req - 1; if (rank >= nf) rank = nf - 1;
                out[0] = f[fpos[rank]]; c->sel_taken = fpos[rank] + 1;
                return 1;
            }
            for (int i = 1; i < m; i++) {
                int8_t fp = fpos[i]; int j = i - 1;
                while (j >= 0 && fpos[j] > fp) { fpos[j + 1] = fpos[j]; j--; }
                fpos[j + 1] = fp;
            }
            for (int i = 0; i < m; i++) out[i] = f[fpos[i]];
            if (m == 0) UNSUP_RET(c, 0, 0);
            if (c->blog) bind_select(c, e, pmin, pmax, f, nf, out, m);
            return m;
        }
        return 0;
    } else if (pt == 2) {
        if (upg_only) {
            int m = 0;
            for (int i = 0; i < nf; i++) if (card_upgradable(c, f[i].slot)) f[m++] = f[i];
            nf = m;
        }
        if (!nraw || !nf) return 0;
        int64_t mn = e->has_pick_min ? e->pick_min : 0, mx = e->has_pick_max ? e->pick_max : 0;
        int64_t req = rng_range(c, mn, mx + 1);
        int64_t keys[MAX_ROWS];
        for (int i = 0; i < nf; i++) keys[i] = rng_signed_key(c);
        sort_rows_by_key(f, keys, nf);
        int m = (int)imin(req, nf);
        if (m < 0) m = 0;
        for (int i = 1; i < m; i++) {
            SRow r = f[i]; int j = i - 1;
            while (j >= 0 && f[j].order > r.order) { f[j + 1] = f[j]; j--; }
            f[j + 1] = r;
        }
        for (int i = 0; i < m; i++) out[i] = f[i];
        return m;
    }
    for (int i = 0; i < nf; i++) out[i] = f[i];
    return nf;
}

// effects.py _remove_card (by identity from its source zone)
GK_FN void remove_row_card(Core *c, const SRow *r) {
    if (r->zone < 0) return;
    if (c->R->synchro && (r->zone == Z_HAND || r->zone == Z_FUSED)) { sy_leave_hand(c, r->zone, r->slot); return; }
    int i = zfind(c, r->zone, r->slot);
    if (i < 0) { UNSUP(c, r->slot); return; }
    zpop(c, r->zone, i);
}

// effects.py _drop_support_upgrade, : a card an effect moves out of the hand loses its support upgrade (client
// MoveCard to HOLD resets it).
GK_FN void drop_support_upgrade(Core *c, int s) {
    if (c->g.cards[s].n_support_upgrade_ids) reset_card_upgrades(c, s);
}

// effects.py apply_exam_card_move
GK_HEAVY void apply_card_move(Core *c, const Eff *e) {
    if (e->move_pos == 1 && zlen(c, Z_HAND) >= c->g.d_hand_limit) {
        ExamStateP *S = ST(c);
        if (e->pick_range_type == 1 && S->select_index_queue.n) select_queue_pop(&S->select_index_queue);
        return;
    }
    int playing = ST(c)->playing_card;
    SRow sel[MAX_ROWS];
    int n = get_search_card_list(c, e, playing, sel);
    if (c->unsup) return;
    int mp = e->move_pos;
    if (mp == 1) {
        for (int i = 0; i < n; i++) remove_row_card(c, &sel[i]);
        int16_t slots[MAX_ROWS];
        for (int i = 0; i < n; i++) slots[i] = sel[i].slot;
        place_in_hand(c, slots, n);
        for (int i = 0; i < n; i++) if (sel[i].slot == playing) c->g.cards[playing].type9_moved = 1;
        return;
    }
    if (mp == 2 && n > 1) {
        SRow g[MAX_ROWS]; int ng = 0;
        int8_t seen[8]; int ns = 0;
        for (int i = 0; i < n; i++) {
            int z = sel[i].zone, known = 0;
            for (int k = 0; k < ns; k++) if (seen[k] == z) known = 1;
            if (!known) seen[ns++] = (int8_t)z;
        }
        for (int k = 0; k < ns; k++) for (int i = 0; i < n; i++) if (sel[i].zone == seen[k]) g[ng++] = sel[i];
        for (int i = 0; i < ng; i++) { remove_row_card(c, &g[i]); drop_support_upgrade(c, g[i].slot); }
        for (int i = ng - 1; i >= 0; i--) zinsert(c, Z_DECK, 0, g[i].slot);
        for (int i = 0; i < ng; i++) if (g[i].slot == playing) c->g.cards[playing].type9_moved = 1;
        return;
    }
    if (mp == 4 && n > 1) {
        SRow g[MAX_ROWS]; int ng = 0;
        int8_t seen[8]; int ns = 0;
        for (int i = 0; i < n; i++) {
            int z = sel[i].zone, known = 0;
            for (int k = 0; k < ns; k++) if (seen[k] == z) known = 1;
            if (!known) seen[ns++] = (int8_t)z;
        }
        for (int k = 0; k < ns; k++) for (int i = 0; i < n; i++) if (sel[i].zone == seen[k]) g[ng++] = sel[i];
        for (int i = 0; i < ng; i++) { remove_row_card(c, &g[i]); drop_support_upgrade(c, g[i].slot); }
        int before = zlen(c, Z_DECK), pos[MAX_ROWS], order[MAX_ROWS];
        for (int i = 0; i < ng; i++) { pos[i] = (int)rng_range(c, 0, before); order[i] = i; }
        for (int i = 1; i < ng; i++) {
            int x = order[i], j = i - 1;
            while (j >= 0 && pos[order[j]] > pos[x]) { order[j + 1] = order[j]; j--; }
            order[j + 1] = x;
        }
        for (int i = 0; i < ng; i++) zinsert(c, Z_DECK, pos[order[i]], g[order[i]].slot);
        for (int i = 0; i < ng; i++) if (g[i].slot == playing) c->g.cards[playing].type9_moved = 1;
        return;
    }
    for (int i = 0; i < n; i++) {
        remove_row_card(c, &sel[i]);
        drop_support_upgrade(c, sel[i].slot);
        if (mp == 2) zinsert(c, Z_DECK, 0, sel[i].slot);
        else if (mp == 3) zappend(c, Z_DECK, sel[i].slot);
        else if (mp == 4) zinsert(c, Z_DECK, (int)rng_range(c, 0, zlen(c, Z_DECK)), sel[i].slot);
        else if (mp == 5) zappend(c, Z_GRAVE, sel[i].slot);
        else if (mp == 6) {
            zappend(c, Z_LOST, sel[i].slot);
            if (c->running_tes >= 0) c->lost_by_enchant |= uint64_t(1) << (sel[i].slot & 63);
        }
        else if (mp == 7) {
            if (zlen(c, Z_HOLD) >= c->g.d_hold_limit && zlen(c, Z_HOLD) > 0) zappend(c, Z_GRAVE, zpop(c, Z_HOLD, 0));
            zappend(c, Z_HOLD, sel[i].slot);
        }
        else if (mp == 8 && c->R->synchro) {
            zappend(c, Z_REMOVE, sel[i].slot); c->sy_removed |= uint64_t(1) << (sel[i].slot & 63);
        }
        else UNSUP_RET(c, mp, );
        if (sel[i].slot == playing) c->g.cards[playing].type9_moved = 1;
    }
}

// effects.py apply_exam_card_duplicate.
GK_HEAVY void apply_card_duplicate(Core *c, const Eff *e) {
    if (!e->has_v1 || e->v1 != 1 || e->move_pos != 3) { UNSUP(c, e->move_pos); return; }
    SRow sel[MAX_ROWS];
    int n = get_search_card_list(c, e, ST(c)->playing_card, sel);
    if (c->unsup) return;
    for (int i = 0; i < n; i++) {
        const Card *src = &c->g.cards[sel[i].slot];
        int s = new_card(c, src->produce_card_id, src->current_upgrade_count);
        if (s < 0 || c->unsup) return;
        CardNo number = c->g.cards[s].number, guid = c->g.cards[s].guid;
        Card *dst = &c->g.cards[s];
        *dst = *src;
        dst->number = number; dst->guid = guid;
        dst->n_support_upgrade_ids = 0;
        dst->type9_moved = -1;
        refresh_card(c, s);
        zappend(c, Z_DECK, s);
    }
}

GK_FN int search_count(Core *c, int32_t sid) {
    const SearchRow *s = search_row(c, sid);
    if (!s) { UNSUP(c, sid); return 0; }
    SRow rows[MAX_ROWS]; int saved_target = c->target_card; c->target_card = -1;
    int n = source_cards(c, s->pos, -1, rows); c->target_card = saved_target;
    int m = 0;
    for (int i = 0; i < n; i++) if (matches_search(c, rows[i].slot, s)) m++;
    return m;
}

// effects.py apply_exam_card_upgrade
GK_HEAVY void apply_card_upgrade(Core *c, const Eff *e) {
    SRow sel[MAX_ROWS];
    int n = get_search_card_list(c, e, ST(c)->playing_card, sel, 1);
    if (c->unsup) return;
    for (int i = 0; i < n; i++) {
        Card *cd = &c->g.cards[sel[i].slot];
        if (!card_upgradable(c, sel[i].slot)) continue;
        cd->temporary_upgrade_count = 1;
        refresh_card(c, sel[i].slot);
    }
}

// effects.py apply_exam_card_create_search ``made`` (when not null) receives the slots created into the hand, in
// creation order (初星スペシャル青汁X)
GK_HEAVY void apply_card_create_search(Core *c, const Eff *e, int16_t *made, int *n_made) {
    if (n_made) *n_made = 0;
    const SearchRow *s = search_row(c, e->search);
    if (!s) UNSUP_RET(c, e->search, );
    int32_t pi = BY(c, 4, s->pool);
    if (pi < 0) UNSUP_RET(c, s->pool, );
    const PoolRow *pool = &c->M->pools[pi];
    struct Cand { int32_t card; int64_t upg; };
    static const int MAXC = 256;
    Cand cand[256]; int nc = 0;
    for (int k = 0; k < pool->n_ent; k++) {
        const int64_t *ent = P64(c, pool->ent_off + 3 * k);
        int32_t cid = (int32_t)ent[0]; int64_t ratio = ent[1], upg = ent[2];
        const CardRow *row = card_row(c, cid, (int32_t)upg);
        if (!row) UNSUP_RET(c, cid, );
        if (!(row->plan == 1 || row->plan == ST(c)->plan_type)) {
            int research = 0;
            for (int q = 0; q < c->R->n_research_cards && !research; q++) research = c->R->research_cards[q] == cid;
            if (!research) continue;
        }
        for (int64_t r = 0; r < ratio; r++) {
            if (nc >= MAXC) UNSUP_RET(c, nc, );
            cand[nc].card = cid; cand[nc].upg = upg; nc++;
        }
    }
    int shortage = e->pick_count_type == 2 && e->pick_ref >= 0;
    int held = 0;
    if (shortage) {
        held = search_count(c, e->pick_ref);
        if (c->unsup) return;
    }
    int64_t k2[256];
    for (int i = 0; i < nc; i++) rng_signed_key(c);
    int nothing = shortage && imax(0, e->pick_max - held) == 0;
    if (!nothing) for (int i = 0; i < nc; i++) k2[i] = rng_signed_key(c);
    int64_t mn = shortage ? imax(0, e->pick_min - held) : e->pick_min;
    int64_t mx = shortage ? imax(0, e->pick_max - held) : e->pick_max;
    int64_t count = nothing ? 0 : rng_range(c, mn, mx + 1);
    count = imin(count, nc);
    int mp = e->move_pos;
    int order[256];
    for (int i = 0; i < nc; i++) order[i] = i;
    for (int i = 1; i < nc; i++) {
        int o = order[i], j = i - 1;
        while (!nothing && j >= 0 && k2[order[j]] > k2[o]) { order[j + 1] = order[j]; j--; }
        order[j + 1] = o;
    }
    if (shortage && mp == 4 && !nothing) {
        int64_t fk[256]; int picked[256]; int selected[256];
        for (int j = 0; j < nc; j++) { fk[j] = rng_signed_key(c); picked[j] = 0; selected[j] = 0; }
        for (int k = 0; k < count; k++) {
            int best = -1;
            for (int j = 0; j < nc; j++) if (!picked[j] && (best < 0 || fk[j] < fk[best])) best = j;
            if (best < 0) break;
            picked[best] = 1; selected[order[best]] = 1;
        }
        if (made) UNSUP_RET(c, mp, );
        int32_t pids[256]; int64_t ups[256]; int ns = 0;
        for (int i = 0; i < nc; i++) if (selected[i]) { pids[ns] = cand[i].card; ups[ns] = cand[i].upg; ns++; }
        create_cards_deck_random_batch(c, pids, ups, ns);
        return;
    }
    int16_t hand_bound[256]; int nhb = 0;
    for (int i = 0; i < count; i++) {
        rng_signed_key(c);
        const Cand *cd = &cand[order[i]];
        if (mp == 4) { if (made) UNSUP_RET(c, mp, ); create_card_deck_random(c, cd->card, cd->upg); }
        else if (mp == 0 || mp == 1) {
            int s = new_card(c, cd->card, cd->upg);
            if (s < 0) return;
            if (nhb >= 256) UNSUP_RET(c, nhb, );
            hand_bound[nhb++] = (int16_t)s;
        }
        else { UNSUP_RET(c, mp, ); }
        if (c->unsup) return;
    }
    if (nhb) place_in_hand(c, hand_bound, nhb);
    if (made) { for (int i = 0; i < nhb; i++) made[i] = hand_bound[i]; *n_made = nhb; }
}
