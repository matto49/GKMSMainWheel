// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 7: exact tail search. Depth-first over every legal action from a state to the end of the
// audition. The exam is deterministic from the state (deck order and RNG are part of GkmsState), so the best finished
// line found by a complete enumeration is the optimum. A visited-state table keyed on the GkmsState bytes skips a
// state already expanded: an identical state has identical futures, and the line that reached it first is already
// scored. Two logically equal states whose unused bytes differ are only missed hits, never merged states.
#pragma once
#include "core_session.h"
#include "core_rules.h"
#include <atomic>

using TailStop = std::atomic<bool>;

static inline bool tail_cancelled(const TailStop *signal) {
    return signal && signal->load(std::memory_order_relaxed);
}

#define T_MAXPATH 256
#define T_MAXDEPTH 200

struct TailParams {
    int64_t node_cap;
    double deadline;
    int64_t tt_capacity;
    int32_t use_tt;
    int32_t threads;
    int32_t items_per_thread;
    int32_t pad;
};

struct TailStack { Core *lv[T_MAXDEPTH + 1]; };
static inline Core *tail_level(TailStack *s, int i) {
    if (!s->lv[i]) s->lv[i] = (Core *)malloc(sizeof(Core));
    return s->lv[i];
}
static inline void tail_stack_free(TailStack *s) {
    for (int i = 0; i <= T_MAXDEPTH; i++) { free(s->lv[i]); s->lv[i] = 0; }
}
static inline int tail_stack_levels(const TailStack *s) {
    int n = 0;
    for (int i = 0; i <= T_MAXDEPTH; i++) n += s->lv[i] != 0;
    return n;
}

struct TailResult {
    int32_t status;
    int32_t unsup_line, unsup_info;
    int32_t complete;
    int64_t best_score;
    int32_t n_path;
    uint16_t path[T_MAXPATH];
    int64_t nodes, leaves, tt_hits, tt_entries;
    int64_t best_major;
    int64_t greedy_choices;
    int64_t stack_levels;
};

struct TailCtx {
    const TailParams *P;
    TailResult *R;
    TailStack *stack;
    uint16_t path[T_MAXPATH];
    uint64_t *tt;
    uint64_t tt_mask;
    int stop;
    double (*now)(void);
    const TailStop *signal;
    const RuleTable *rules;
    RuleState *rs;
    int has_best;
};

static inline uint64_t tail_mix(uint64_t h) {
    h ^= h >> 33; h *= 0xff51afd7ed558ccdull; h ^= h >> 33; h *= 0xc4ceb9fe1a85ec53ull; h ^= h >> 33;
    return h;
}

// The tails' extents follow from counts inside the hashed bytes.
static inline void tail_hash(const Core *c, uint64_t *a, uint64_t *b) {
    const unsigned char *p = (const unsigned char *)&c->g;
    const size_t n = sizeof(GkmsState);
    CoreSpan t[CORE_MAX_SPANS];
    int k = core_tails(c, t, 1);
    uint64_t x0 = 0xc1b8a8de0fd041aaull, x1 = 0x3983498b2f1cb912ull, x2 = 0x06efde7d78815506ull, x3 = 0x2ffe9477079fe4bfull;
    uint64_t y0 = 0xb10232ff9c39c413ull, y1 = 0xed6bb5c08d1c27f2ull, y2 = 0x7fb7ef425f4836c0ull, y3 = 0xb9294bdc13ebb61aull;
    uint64_t total = 0;
    size_t at = 0;
    for (int s = 0; s <= k && at < n; s++) {
        size_t end = s < k ? t[s].a : n;
        if (end > n) end = n;
        size_t i = at;
        for (; i + 32 <= end; i += 32) {
            uint64_t w0, w1, w2, w3;
            memcpy(&w0, p + i, 8); memcpy(&w1, p + i + 8, 8); memcpy(&w2, p + i + 16, 8); memcpy(&w3, p + i + 24, 8);
            x0 = (x0 ^ w0) * 0x100000001b3ull; x1 = (x1 ^ w1) * 0x100000001b3ull;
            x2 = (x2 ^ w2) * 0x100000001b3ull; x3 = (x3 ^ w3) * 0x100000001b3ull;
            x0 ^= x0 >> 29; x1 ^= x1 >> 29; x2 ^= x2 >> 29; x3 ^= x3 >> 29;
            y0 = (y0 + w0) * 0x9ddfea08eb382d69ull; y1 = (y1 + w1) * 0x9ddfea08eb382d69ull;
            y2 = (y2 + w2) * 0x9ddfea08eb382d69ull; y3 = (y3 + w3) * 0x9ddfea08eb382d69ull;
            y0 ^= y0 >> 31; y1 ^= y1 >> 31; y2 ^= y2 >> 31; y3 ^= y3 >> 31;
        }
        for (; i + 8 <= end; i += 8) {
            uint64_t w; memcpy(&w, p + i, 8);
            x0 = (x0 ^ w) * 0x100000001b3ull; x0 ^= x0 >> 29;
            y0 = (y0 + w) * 0x9ddfea08eb382d69ull; y0 ^= y0 >> 31;
        }
        for (; i < end; i++) { x1 = (x1 ^ p[i]) * 0x100000001b3ull; y1 = (y1 + p[i]) * 0x9ddfea08eb382d69ull; }
        total += end > at ? end - at : 0;
        x2 ^= (uint64_t)end * 0x9e3779b97f4a7c15ull; y2 += (uint64_t)end;
        at = s < k ? t[s].b : n;
    }
    uint64_t h1 = tail_mix(x0) ^ tail_mix(x1 + 0xf87fab3f1c400ddfull) ^ tail_mix(x2 + 0x488070d630add05eull) ^ tail_mix(x3 + 0x4abbb6e83004410full);
    uint64_t h2 = tail_mix(y0) ^ tail_mix(y1 + 0xf87fab3f1c400ddfull) ^ tail_mix(y2 + 0x488070d630add05eull) ^ tail_mix(y3 + 0x4abbb6e83004410full);
    *a = tail_mix(h1) | 1ull;
    *b = tail_mix(h2 ^ total);
}

// 1: newly inserted (expand it), 0: seen before, 2: table full (expand it)
static inline int tail_visit(TailCtx *t, const Core *g, uint64_t salt) {
    uint64_t a, b; tail_hash(g, &a, &b);
    if (salt) { a = (a ^ tail_mix(salt)) | 1ull; b ^= tail_mix(salt + 0xb9679e846d143bdaull); }
    uint64_t i = a & t->tt_mask;
    for (uint64_t probe = 0; probe <= t->tt_mask; probe++, i = (i + 1) & t->tt_mask) {
        uint64_t *s = t->tt + 2 * i;
        if (s[0] == 0 && s[1] == 0) {
            if ((uint64_t)t->R->tt_entries * 10 >= (t->tt_mask + 1) * 9) return 2;
            s[0] = a; s[1] = b; t->R->tt_entries++; return 1;
        }
        if (s[0] == a && s[1] == b) return 0;
    }
    return 2;
}

// turns still to start, this one included (exam_session: limit_turn + extra_turns)
static inline int64_t tail_turns_left(const Core *c) {
    return (int64_t)c->R->limit_turn + ST(c)->extra_turns - c->g.s_turn + 1;
}
#ifndef TAIL_TT_RULE
#define TAIL_TT_RULE 0
#endif
static inline int tail_tt_here(const Core *child) {
    return TAIL_TT_RULE == 0 ? 1 : tail_turns_left(child) >= 2;
}

static void tail_dfs(TailCtx *t, int depth) {
    Core *c = t->stack->lv[depth];
    TailResult *R = t->R;
    R->nodes++;
    if (t->P->node_cap > 0 && R->nodes > t->P->node_cap) { t->stop = 1; if (!R->status) R->status = 1; return; }
    const double deadline = t->P->deadline;
    if (tail_cancelled(t->signal) || (deadline >= 0 && (R->nodes & 1023) == 0 && t->now() > deadline)) {
        t->stop = 1; if (!R->status) R->status = 2; return;
    }
    if (c->g.s_finished) {
        R->leaves++;
        int64_t v = c->g.st.score, major = 0;
        if (t->rules) { v -= rt_penalty(t->rules, &t->rs[depth]); major = rt_major(t->rules, &t->rs[depth], c); }
        if (!t->has_best || major < R->best_major || (major == R->best_major && v > R->best_score)) {
            t->has_best = 1;
            R->best_score = v;
            R->best_major = major;
            R->n_path = depth;
            memcpy(R->path, t->path, sizeof(uint16_t) * (size_t)depth);
        }
        return;
    }
    if (depth + 1 >= T_MAXDEPTH || depth >= T_MAXPATH) { t->stop = 1; if (!R->status) R->status = 4; return; }
    int32_t acts[256];
    int n = legal_actions(c, acts);
    uint8_t keep[256];
    int rk[256];
    if (t->rules) {
        int ri[256];
        rt_sync(&t->rs[depth], c);
        for (int k = 0; k < n; k++) { int kd = acts[k] >> 8; rk[k] = kd == 0 ? 2 : kd == 1 ? 0 : 1; ri[k] = acts[k] & 0xFF; }
        rt_filter(t->rules, &t->rs[depth], c, n, rk, ri, keep);
    }
    for (int k = 0; k < n && !t->stop; k++) {
        if (t->rules && !keep[k]) continue;
        Core *child = tail_level(t->stack, depth + 1);
        if (!child) { t->stop = 1; if (!R->status) R->status = 5; return; }
        core_copy(child, c);
        int64_t tb = 0, lb = 0;
        if (t->rules) { t->rs[depth + 1] = t->rs[depth]; rt_taken(t->rules, &t->rs[depth + 1], c, rk[k], acts[k] & 0xFF, &tb, &lb); }
        int rc = session_step(child, acts[k] >> 8, acts[k] & 0xFF);
        if (rc == 1 || child->unsup) {
            t->stop = 1; R->status = 3; R->unsup_line = child->unsup; R->unsup_info = child->unsup_info; return;
        }
        if (rc == 2) continue;
        if (child->greedy_choices != c->greedy_choices) R->greedy_choices++;
        uint64_t salt = 0;
        if (t->rules) { rt_after(t->rules, &t->rs[depth + 1], child, tb, lb); salt = rt_state_hash(&t->rs[depth + 1]) | 1ull; }
        if (t->tt && !child->g.s_finished && tail_tt_here(child) && tail_visit(t, child, salt) == 0) { R->tt_hits++; continue; }
        t->path[depth] = (uint16_t)acts[k];
        tail_dfs(t, depth + 1);
    }
}

static int tail_exact(const Core *root, const TailParams *P, TailResult *R, double (*now)(void),
                      const RuleTable *rules, const TailStop *signal) {
    memset(R, 0, sizeof(TailResult));
    R->best_score = -1;
    TailCtx t; memset(&t, 0, sizeof(t));
    t.P = P; t.R = R; t.now = now; t.signal = signal;
    t.stack = (TailStack *)calloc(1, sizeof(TailStack));
    if (!t.stack || !tail_level(t.stack, 0)) { if (t.stack) tail_stack_free(t.stack); free(t.stack); R->status = 5; return R->status; }
    if (rules) {
        t.rs = (RuleState *)malloc(sizeof(RuleState) * (size_t)(T_MAXDEPTH + 1));
        if (!t.rs) { tail_stack_free(t.stack); free(t.stack); R->status = 5; return R->status; }
        t.rules = rules;
        t.rs[0] = rules->root;
    }
    if (P->use_tt && P->tt_capacity > 0) {
        uint64_t cap = 1; while (cap < (uint64_t)P->tt_capacity) cap <<= 1;
        t.tt = (uint64_t *)calloc((size_t)cap * 2, sizeof(uint64_t));
        if (t.tt) t.tt_mask = cap - 1;
    }
    memcpy(t.stack->lv[0], root, sizeof(Core));
    tail_dfs(&t, 0);
    R->stack_levels = tail_stack_levels(t.stack);
    tail_stack_free(t.stack);
    free(t.stack); free(t.tt); free(t.rs);
    R->complete = (R->status == 0);
    return R->status;
}
