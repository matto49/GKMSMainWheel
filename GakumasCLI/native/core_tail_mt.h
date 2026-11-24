// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 7b: the exact tail search on several threads. CPU only (api.cpp). The tree is cut at the
// shallowest depth that gives at least TAIL_MT_ITEMS_PER_THREAD subtrees per thread; the subtrees, in depth-first
// order, are handed out to the threads one at a time and searched as tail_dfs does. Each subtree keeps the first line
// of its best score, and the result is the best score of all, from the earliest subtree in depth-first order -- the
// line the one-thread search returns, unless the table moved a shared state from one subtree to another (then another
// line of the same score).
#pragma once
#include "core_tail.h"
#include <atomic>
#if defined(_WIN32)
extern "C" {
__declspec(dllimport) void *__stdcall CreateThread(void *, size_t, unsigned long(__stdcall *)(void *), void *,
                                                   unsigned long, unsigned long *);
__declspec(dllimport) unsigned long __stdcall WaitForSingleObject(void *, unsigned long);
__declspec(dllimport) int __stdcall CloseHandle(void *);
}
#else
#include <pthread.h>
#endif

#define TAIL_MT_MAX_THREADS 64
#define TAIL_MT_ITEMS_PER_THREAD 8
#define TAIL_MT_MAX_PER_THREAD 256
#define TAIL_MT_MAX_ITEMS 8192
#define TAIL_MT_STACK_BYTES (16u << 20)

struct TailItem {
    int n;
    uint16_t path[T_MAXPATH];
    int64_t best;
    int n_best;
    uint16_t best_path[T_MAXPATH];
};

struct TailShared {
    const Core *root;
    const TailParams *P;
    const TailStop *signal;
    double (*now)(void);
    std::atomic<uint64_t> *tt;
    uint64_t tt_mask;
    std::atomic<int64_t> tt_entries;
    std::atomic<int64_t> nodes, leaves, tt_hits, greedy;
    std::atomic<int> stop;
    std::atomic<int> status;
    std::atomic<int32_t> unsup_line, unsup_info;
    TailItem *items;
    int n_items;
    std::atomic<int> next;
};

struct TailWorker {
    TailShared *S;
    TailStack *stack;
    uint16_t path[T_MAXPATH];
    TailItem *item;
    int base;
    int64_t nodes, flushed;
};

static inline void tail_mt_status(TailShared *S, int st) {
    int zero = 0;
    S->status.compare_exchange_strong(zero, st);
    S->stop.store(1, std::memory_order_relaxed);
}

// 1: newly inserted (expand it), 0: seen before, 2: table full (expand it)
static inline int tail_mt_visit(TailShared *S, const Core *c) {
    uint64_t a, b; tail_hash(c, &a, &b);
    uint64_t i = a & S->tt_mask;
    for (uint64_t probe = 0; probe <= S->tt_mask; probe++, i = (i + 1) & S->tt_mask) {
        std::atomic<uint64_t> *s = S->tt + 2 * i;
        uint64_t sa = s[0].load(std::memory_order_acquire);
        if (sa == 0) {
            if ((uint64_t)S->tt_entries.load(std::memory_order_relaxed) * 10 >= (S->tt_mask + 1) * 9) return 2;
            uint64_t expected = 0;
            if (s[0].compare_exchange_strong(expected, a, std::memory_order_acq_rel)) {
                s[1].store(b, std::memory_order_release);
                S->tt_entries.fetch_add(1, std::memory_order_relaxed);
                return 1;
            }
            sa = expected;
        }
        if (sa == a && s[1].load(std::memory_order_acquire) == b) return 0;
    }
    return 2;
}

static int64_t tail_mt_add_nodes(TailWorker *w) {
    int64_t total = w->S->nodes.fetch_add(w->nodes - w->flushed, std::memory_order_relaxed) + (w->nodes - w->flushed);
    w->flushed = w->nodes;
    return total;
}
static int tail_mt_flush(TailWorker *w) {
    TailShared *S = w->S;
    int64_t total = tail_mt_add_nodes(w);
    if (S->P->node_cap > 0 && total > S->P->node_cap) { tail_mt_status(S, 1); return 1; }
    const double deadline = S->P->deadline;
    if (deadline >= 0 && S->now() > deadline) { tail_mt_status(S, 2); return 1; }
    return S->stop.load(std::memory_order_relaxed);
}

static void tail_mt_dfs(TailWorker *w, int depth) {
    TailShared *S = w->S;
    Core *c = w->stack->lv[depth - w->base];
    w->nodes++;
    if (tail_cancelled(S->signal)) { tail_mt_status(S, 2); return; }
    if ((w->nodes & 1023) == 0 && tail_mt_flush(w)) return;
    if (c->g.s_finished) {
        S->leaves.fetch_add(1, std::memory_order_relaxed);
        if (c->g.st.score > w->item->best) {
            w->item->best = c->g.st.score;
            w->item->n_best = depth;
            memcpy(w->item->best_path, w->path, sizeof(uint16_t) * (size_t)depth);
        }
        return;
    }
    if (depth + 1 >= T_MAXDEPTH || depth >= T_MAXPATH) { tail_mt_status(S, 4); return; }
    int32_t acts[256];
    int n = legal_actions(c, acts);
    for (int k = 0; k < n && !S->stop.load(std::memory_order_relaxed); k++) {
        Core *child = tail_level(w->stack, depth + 1 - w->base);
        if (!child) { tail_mt_status(S, 5); return; }
        core_copy(child, c);
        int rc = session_step(child, acts[k] >> 8, acts[k] & 0xFF);
        if (rc == 1 || child->unsup) {
            int32_t zero = 0;
            if (S->unsup_line.compare_exchange_strong(zero, child->unsup ? child->unsup : -1))
                S->unsup_info.store(child->unsup_info);
            tail_mt_status(S, 3);
            return;
        }
        if (rc == 2) continue;
        if (child->greedy_choices != c->greedy_choices) S->greedy.fetch_add(1, std::memory_order_relaxed);
        if (S->tt && !child->g.s_finished && tail_tt_here(child) && tail_mt_visit(S, child) == 0) {
            S->tt_hits.fetch_add(1, std::memory_order_relaxed);
            continue;
        }
        w->path[depth] = (uint16_t)acts[k];
        tail_mt_dfs(w, depth + 1);
    }
}

#if defined(_WIN32)
static unsigned long __stdcall tail_mt_thread(void *arg)
#else
static void *tail_mt_thread(void *arg)
#endif
{
    TailWorker *w = (TailWorker *)arg;
    TailShared *S = w->S;
    for (;;) {
        if (S->stop.load(std::memory_order_relaxed)) break;
        int i = S->next.fetch_add(1);
        if (i >= S->n_items) break;
        TailItem *it = &S->items[i];
        w->item = it; w->base = it->n;
        Core *c = tail_level(w->stack, 0);
        if (!c) { tail_mt_status(S, 5); break; }
        core_copy(c, S->root);
        int ok = 1;
        const int64_t greedy0 = c->greedy_choices;
        for (int d = 0; d < it->n; d++) {
            int rc = session_step(c, it->path[d] >> 8, it->path[d] & 0xFF);
            if (rc != 0 || c->unsup) { ok = 0; break; }
            w->path[d] = it->path[d];
        }
        if (!ok) { tail_mt_status(S, 3); break; }
        if (c->greedy_choices != greedy0) S->greedy.fetch_add(1, std::memory_order_relaxed);
        tail_mt_dfs(w, it->n);
    }
    tail_mt_add_nodes(w);
#if defined(_WIN32)
    return 0;
#else
    return 0;
#endif
}

// the cut: every line of length `depth` (or one that finishes earlier), depth-first order.
static int tail_mt_cut(const Core *root, int depth, TailItem *items, int cap, Core *stack, uint16_t *path, int d,
                       int *count, int32_t *unsup_line, int32_t *unsup_info, const TailStop *signal) {
    if (tail_cancelled(signal)) return -3;
    Core *c = &stack[d];
    if (d == depth || c->g.s_finished) {
        if (*count >= cap) return -1;
        TailItem *it = &items[(*count)++];
        it->n = d; memcpy(it->path, path, sizeof(uint16_t) * (size_t)d);
        it->best = -1; it->n_best = 0;
        return 0;
    }
    int32_t acts[256];
    int n = legal_actions(c, acts);
    for (int k = 0; k < n; k++) {
        Core *child = &stack[d + 1];
        core_copy(child, c);
        int rc = session_step(child, acts[k] >> 8, acts[k] & 0xFF);
        if (rc == 1 || child->unsup) { *unsup_line = child->unsup; *unsup_info = child->unsup_info; return -2; }
        if (rc == 2) continue;
        path[d] = (uint16_t)acts[k];
        int r = tail_mt_cut(root, depth, items, cap, stack, path, d + 1, count, unsup_line, unsup_info, signal);
        if (r < 0) return r;
    }
    return 0;
}

static int tail_exact_mt(const Core *root, const TailParams *P, TailResult *R, double (*now)(void), int threads,
                         const TailStop *signal) {
    if (threads > TAIL_MT_MAX_THREADS) threads = TAIL_MT_MAX_THREADS;
    memset(R, 0, sizeof(TailResult));
    R->best_score = -1;
    TailItem *items = (TailItem *)malloc(sizeof(TailItem) * TAIL_MT_MAX_ITEMS);
    Core *cut_stack = (Core *)malloc(sizeof(Core) * 16);
    if (!items || !cut_stack) { free(items); free(cut_stack); R->status = 5; return R->status; }
    memcpy(&cut_stack[0], root, sizeof(Core));
    uint16_t cut_path[16];
    const int per = (P->items_per_thread >= 1 && P->items_per_thread <= TAIL_MT_MAX_PER_THREAD)
                    ? P->items_per_thread : TAIL_MT_ITEMS_PER_THREAD;
    int want = threads * per, count = 0;
    for (int depth = 1; depth < 15; depth++) {
        count = 0;
        int r = tail_mt_cut(root, depth, items, TAIL_MT_MAX_ITEMS, cut_stack, cut_path, 0, &count,
                            &R->unsup_line, &R->unsup_info, signal);
        bool overflow = r == -1;
        if (overflow) {
            count = 0;
            r = tail_mt_cut(root, depth - 1, items, TAIL_MT_MAX_ITEMS, cut_stack, cut_path, 0, &count,
                            &R->unsup_line, &R->unsup_info, signal);
        }
        if (r < 0) {
            free(items); free(cut_stack);
            R->status = r == -3 ? 2 : 3;
            return R->status;
        }
        if (overflow) break;
        int deeper = 0;
        for (int i = 0; i < count; i++) if (items[i].n == depth) { deeper = 1; break; }
        if (count >= want || !deeper) break;
    }
    free(cut_stack);
    TailShared S;
    S.root = root; S.P = P; S.now = now; S.signal = signal;
    S.tt = 0; S.tt_mask = 0; S.tt_entries = 0; S.nodes = 0; S.leaves = 0; S.tt_hits = 0; S.greedy = 0;
    S.stop = 0; S.status = 0; S.unsup_line = 0; S.unsup_info = 0;
    S.items = items; S.n_items = count; S.next = 0;
    if (P->use_tt && P->tt_capacity > 0) {
        uint64_t cap = 1; while (cap < (uint64_t)P->tt_capacity) cap <<= 1;
        S.tt = (std::atomic<uint64_t> *)calloc((size_t)cap * 2, sizeof(std::atomic<uint64_t>));
        if (S.tt) S.tt_mask = cap - 1;
    }
    TailWorker W[TAIL_MT_MAX_THREADS];
    int started = 0;
    int64_t levels = 0;
#if defined(_WIN32)
    void *H[TAIL_MT_MAX_THREADS];
#else
    pthread_t H[TAIL_MT_MAX_THREADS];
    pthread_attr_t attr; pthread_attr_init(&attr); pthread_attr_setstacksize(&attr, TAIL_MT_STACK_BYTES);
#endif
    for (int i = 0; i < threads; i++) {
        W[i].S = &S; W[i].nodes = 0; W[i].flushed = 0; W[i].item = 0; W[i].base = 0;
        W[i].stack = (TailStack *)calloc(1, sizeof(TailStack));
        if (!W[i].stack) break;
#if defined(_WIN32)
        H[i] = CreateThread(0, TAIL_MT_STACK_BYTES, tail_mt_thread, &W[i], 0, 0);
        if (!H[i]) { free(W[i].stack); break; }
#else
        if (pthread_create(&H[i], &attr, tail_mt_thread, &W[i]) != 0) { free(W[i].stack); break; }
#endif
        started++;
    }
#if !defined(_WIN32)
    pthread_attr_destroy(&attr);
#endif
    if (started == 0) {
        TailWorker w; w.S = &S; w.nodes = 0; w.flushed = 0; w.item = 0; w.base = 0;
        w.stack = (TailStack *)calloc(1, sizeof(TailStack));
        if (w.stack) {
            tail_mt_thread(&w);
            levels = tail_stack_levels(w.stack);
            tail_stack_free(w.stack); free(w.stack);
        } else tail_mt_status(&S, 5);
    }
    for (int i = 0; i < started; i++) {
#if defined(_WIN32)
        WaitForSingleObject(H[i], 0xFFFFFFFFul); CloseHandle(H[i]);
#else
        pthread_join(H[i], 0);
#endif
        int64_t lv = tail_stack_levels(W[i].stack);
        if (lv > levels) levels = lv;
        tail_stack_free(W[i].stack);
        free(W[i].stack);
    }
    for (int i = 0; i < count; i++) {
        if (items[i].best > R->best_score) {
            R->best_score = items[i].best;
            R->n_path = items[i].n_best;
            memcpy(R->path, items[i].best_path, sizeof(uint16_t) * (size_t)items[i].n_best);
        }
    }
    int st = S.status.load();
    if (st == 0 && S.next.load() < count) st = 2;
    R->status = st;
    if (st == 3) { R->unsup_line = S.unsup_line.load(); R->unsup_info = S.unsup_info.load(); }
    R->nodes = S.nodes.load(); R->leaves = S.leaves.load(); R->tt_hits = S.tt_hits.load();
    R->tt_entries = S.tt_entries.load();
    R->greedy_choices = S.greedy.load();
    R->stack_levels = levels;
    R->complete = (R->status == 0);
    free(items); free(S.tt);
    return R->status;
}
