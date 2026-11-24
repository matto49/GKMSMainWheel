// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// native exam core -- part 7: one random rollout (bench policy), shared by the CPU entry points (api.cpp) and the
// CUDA kernel (gpu/kernel.cu), so both run the identical policy on the identical engine.
#pragma once
#include "core_session.h"

// xorshift64 policy stream; a non-end action with probability 0.9
GK_FN uint64_t pol_next(uint64_t *x) { uint64_t v = *x; v ^= v << 13; v ^= v >> 7; v ^= v << 17; *x = v; return v; }

// Plays one rollout from ``root`` in ``c``.
GK_FN uint64_t playable_hand(Core *c, int *n) {
    uint64_t m = 0; int k = 0;
    for (int i = 0; i < zlen(c, Z_HAND); i++) if (can_play(c, zget(c, Z_HAND, i))) { m |= uint64_t(1) << i; k++; }
    *n = k;
    return m;
}
GK_HEAVY int rollout_from(Core *c, const Core *root, const CoreSpan *t, int k, uint64_t *x, int64_t *steps) {
    core_copy_spans(c, root, t, k);
    int64_t n_steps = 0;
    while (!c->g.s_finished) {
        int nh;
        uint64_t hand = playable_hand(c, &nh);
        int np = nh + c->g.n_drinks;
        int a;
        if (np > 0 && (pol_next(x) % 1000) < 900) {
            int j = (int)(pol_next(x) % np);
            if (j < nh) { for (int i = 0; i < j; i++) hand &= hand - 1; a = (0 << 8) | GK_CTZ64(hand); }
            else a = (1 << 8) | (j - nh);
        }
        else a = 2 << 8;
        int rc = session_step(c, a >> 8, a & 0xFF);
        n_steps++;
        if (rc != 0) { *steps = n_steps; return rc; }
    }
    *steps = n_steps;
    return 0;
}
GK_HEAVY int rollout_one(Core *c, const Core *root, uint64_t *x, int64_t *steps) {
    CoreSpan t[CORE_MAX_SPANS];
    int k = core_tails(root, t, 0);
    return rollout_from(c, root, t, k, x, steps);
}
