// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// seed_solver.c -- recover a GKMS exam seed from the observed opening draws.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _OPENMP
#include <omp.h>
#endif

static inline uint32_t xs(uint32_t s) { s ^= s << 13; s ^= s >> 17; s ^= s << 5; return s; }

static int parse_list(const char *text, int *out, int cap) {
    int n = 0; const char *p = text;
    while (*p && n < cap) { out[n++] = (int)strtol(p, (char **)&p, 10); if (*p == ',') p++; }
    return n;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s k pre obs [lo] [hi] [min_L]\n", argv[0]); return 2; }
    int k = atoi(argv[1]);
    int pre[64], obs[64];
    int n = parse_list(argv[2], pre, 64), L = parse_list(argv[3], obs, 64);
    uint64_t lo = argc > 4 ? strtoull(argv[4], 0, 10) : 0;
    uint64_t hi = argc > 5 ? strtoull(argv[5], 0, 10) : (1ULL << 32);
    int min_L = argc > 6 ? atoi(argv[6]) : L;
    if (n < 2 || n > 64 || L < 1 || L > n || min_L < 1 || min_L > L) { fprintf(stderr, "bad sizes\n"); return 2; }
    int pool[32], turns[32], P = 0, M = 0;
    if (argc > 8 && strcmp(argv[7], "-") != 0) {
        P = parse_list(argv[7], pool, 32);
        M = parse_list(argv[8], turns, 32);
        if (M > P) { fprintf(stderr, "more turns than pool entries\n"); return 2; }
    }

    static uint32_t T[4][256];
    for (int b = 0; b < 4; b++)
        for (int v = 0; v < 256; v++) {
            uint32_t s = (uint32_t)v << (8 * b);
            for (int i = 0; i < k; i++) s = xs(s);
            T[b][v] = s;
        }

    long long found = 0;
    #pragma omp parallel for schedule(dynamic, 1 << 20) reduction(+:found)
    for (int64_t seed = (int64_t)lo; seed < (int64_t)hi; seed++) {
        uint32_t x = (uint32_t)seed;
        if (M) {
            int left[32], len = P, ok = 1;
            uint32_t t = x;
            memcpy(left, pool, sizeof(int) * (size_t)P);
            for (int i = 0; i < M; i++) {
                int j = (int)(((uint64_t)t * (uint32_t)len) >> 32);
                t = xs(t);
                if (left[j] != turns[i]) { ok = 0; break; }
                memmove(&left[j], &left[j + 1], sizeof(int) * (size_t)(len - j - 1));
                len--;
            }
            if (!ok) continue;
        }
        uint32_t s = T[0][x & 255] ^ T[1][(x >> 8) & 255] ^ T[2][(x >> 16) & 255] ^ T[3][x >> 24];
        uint8_t idx[64];
        for (int size = n; size >= 2; size--) { idx[size] = (uint8_t)(((uint64_t)s * (uint32_t)size) >> 32); s = xs(s); }
        int matched = 0;
        for (int p = 0; p < L; p++) {
            int q = p;
            for (int size = 2; size <= n; size++) { int j = idx[size]; if (q == size - 1) q = j; else if (q == j) q = size - 1; }
            if (pre[q] != obs[p]) break;
            matched++;
        }
        if (matched >= min_L) {
            found++;
            #pragma omp critical
            printf("%lld %d\n", (long long)seed, matched);
        }
    }
    printf("# scanned %llu seeds, %lld matched >= %d of %d observed, %d turns\n",
           (unsigned long long)(hi - lo), found, min_L, L, M);
    return 0;
}
