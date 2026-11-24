// Copyright (C) 2026 花吹雪また一年P
// SPDX-License-Identifier: AGPL-3.0-or-later
// core_produce.h -- : the produce model (選抜 + 本戦 forecast) as a compiled step program over many runs.
#pragma once
#if defined(_MSC_VER)
#pragma fp_contract(off)
#endif
#include <math.h>
#include <stdint.h>
#include <string.h>

#include <algorithm>
#include <vector>

namespace gkprod {

enum { VO, DA, VI, ST, MAXST, STAR, PP, GVO, GDA, GVI, LIMIT, NCOL };
enum { K_NONE = 0, K_ATTR = 1, K_STAM = 2, K_PLAIN = 3 };
enum { OP_ADD = 1, OP_ADD_GROW = 2, OP_ADD_FRAC = 3, OP_ADD_REST = 4, OP_SHARES = 5, OP_AUD = 6,
       OP_SEL_GROWTH = 7, OP_FIRE = 8, OP_ITEM = 9 };
enum { MAGIC = 0x50524431, VERSION = 1, OUT_D = 40 };
enum { ST_OK = 0, ST_DIVERGED = 1, ST_BAD_INPUT = 2 };

// CPython float floor division (Objects/floatobject.c _float_div_mod)
static inline double py_floordiv(double vx, double wx) {
    double mod = fmod(vx, wx);
    double div = (vx - mod) / wx;
    if (mod) {
        if ((wx < 0) != (mod < 0)) div -= 1.0;
    }
    double fl;
    if (div) {
        fl = floor(div);
        if (div - fl > 0.5) fl += 1.0;
    } else {
        fl = copysign(0.0, vx / wx);
    }
    return fl;
}

// CPython round(float) with no digits: half to even
static inline double py_round(double x) {
    double r = round(x);
    if (fabs(x - r) == 0.5) r = 2.0 * round(x / 2.0);
    return r;
}

#if defined(_MSC_VER)
__declspec(noinline)
#else
__attribute__((noinline))
#endif
static double cube(double x) { return pow(x, 3.0); }

struct Rd {
    const int32_t *I; int64_t ni, pi;
    const double *D; int64_t nd, pd;
    bool bad;
    int32_t i() { if (pi >= ni) { bad = true; return 0; } return I[pi++]; }
    double d() { if (pd >= nd) { bad = true; return 0.0; } return D[pd++]; }
};

struct Skill { int32_t cond, kind, tcol; double cap, rate, val; };
struct OpRec { int32_t code, a, b; double x; };
struct Cand { int32_t mix, feas; double feas_x; int32_t a0, a1, b0, b1; };
struct Step { int32_t has_items_or_deck, c0, c1, e0, e1; };
struct Program {
    int32_t n_items = 0;
    std::vector<Step> steps;
    std::vector<Cand> cands;
    std::vector<OpRec> ops;
};
struct Entry { int32_t col, skill; };
struct Group {
    int32_t W = 0;
    std::vector<Entry> base;
    std::vector<uint8_t> held0;
    std::vector<int32_t> it0, it1;
    std::vector<Entry> items;
};
struct Form {
    double v0[NCOL], q[3];
    Group g7, g8;
    std::vector<int32_t> carry;
    std::vector<int32_t> ev_col;
    std::vector<double> ev_x;
};
struct Shared {
    std::vector<Skill> skills;
    int32_t n_sites = 0, n_conds = 0;
    std::vector<uint8_t> fires;
    Program p7, p8;
    int32_t k7 = 0, n7 = 0, k8 = 0;
    double star_clamp = 0;
    double p8_start[NCOL];
};

static void read_ops(Rd &r, Program &P, int32_t &o0, int32_t &o1) {
    int32_t n = r.i();
    o0 = (int32_t)P.ops.size();
    for (int32_t j = 0; j < n && !r.bad; j++) {
        OpRec op;
        op.code = r.i(); op.a = r.i(); op.b = r.i(); op.x = r.d();
        P.ops.push_back(op);
    }
    o1 = (int32_t)P.ops.size();
}

static void read_program(Rd &r, Program &P) {
    P.n_items = r.i();
    int32_t ns = r.i();
    for (int32_t s = 0; s < ns && !r.bad; s++) {
        Step st;
        st.has_items_or_deck = r.i();
        int32_t nc = r.i();
        st.c0 = (int32_t)P.cands.size();
        for (int32_t c = 0; c < nc && !r.bad; c++) {
            Cand cd;
            cd.mix = r.i(); cd.feas = r.i(); cd.feas_x = r.d();
            read_ops(r, P, cd.a0, cd.a1);
            if (cd.mix >= 0) read_ops(r, P, cd.b0, cd.b1);
            else cd.b0 = cd.b1 = 0;
            P.cands.push_back(cd);
        }
        st.c1 = (int32_t)P.cands.size();
        read_ops(r, P, st.e0, st.e1);
        P.steps.push_back(st);
    }
}

static void read_entries(Rd &r, std::vector<Entry> &out, int32_t n) {
    for (int32_t j = 0; j < n && !r.bad; j++) { Entry e; e.col = r.i(); e.skill = r.i(); out.push_back(e); }
}

static void read_group(Rd &r, Group &g, int32_t n_items) {
    g.W = r.i();
    read_entries(r, g.base, r.i());
    for (int32_t it = 0; it < n_items && !r.bad; it++) {
        g.held0.push_back((uint8_t)r.i());
        g.it0.push_back((int32_t)g.items.size());
        read_entries(r, g.items, r.i());
        g.it1.push_back((int32_t)g.items.size());
    }
}

// -- states of one run ---------------------------------------------------------------------------------------
struct States {
    int32_t W = 0, n = 0;
    std::vector<double> v, cnt, aud;
    std::vector<uint8_t> has;
    void resize(int32_t m) {
        n = m;
        v.resize((size_t)m * NCOL); cnt.resize((size_t)m * (W ? W : 1)); aud.resize((size_t)m * 12); has.resize((size_t)m * 3);
    }
    void copy_from(int32_t dst, const States &S, int32_t src) {
        memcpy(&v[(size_t)dst * NCOL], &S.v[(size_t)src * NCOL], sizeof(double) * NCOL);
        if (W) memcpy(&cnt[(size_t)dst * W], &S.cnt[(size_t)src * W], sizeof(double) * W);
        memcpy(&aud[(size_t)dst * 12], &S.aud[(size_t)src * 12], sizeof(double) * 12);
        memcpy(&has[(size_t)dst * 3], &S.has[(size_t)src * 3], 3);
    }
};

struct Run {
    const Group *g;
    std::vector<Entry> list;
    std::vector<uint8_t> held;
    void init(const Group *gg) { g = gg; list = gg->base; held = gg->held0; }
};

static inline void add(double *v, int col, double x) {
    if (col == VO || col == DA || col == VI) {
        double y = v[col] + x;
        v[col] = (y < v[LIMIT]) ? y : v[LIMIT];
    } else if (col == ST) {
        double y = v[ST] + x;
        double m = (y < v[MAXST]) ? y : v[MAXST];
        v[ST] = (m > 0.0) ? m : 0.0;
    } else {
        v[col] = v[col] + x;
    }
}

static inline double J(const double *v) {
    double lim = v[LIMIT];
    double t = 0.0;
    t = t + ((v[VO] < lim) ? v[VO] : lim);
    t = t + ((v[DA] < lim) ? v[DA] : lim);
    t = t + ((v[VI] < lim) ? v[VI] : lim);
    return 2.0 * t + 7.5 * v[STAR];
}

struct Ctx {
    const Shared *S;
    const Program *P;
    const double *q;
};

// the entries of a list prefix whose condition holds at a site (produce_effects.fires is state-free), kept for the
// step: a fire then visits only those, in list order -- the same additions as visiting the whole list
struct FireCache {
    const Shared *S = nullptr;
    const std::vector<Entry> *list = nullptr;
    std::vector<std::vector<Entry>> by_site;
    std::vector<int32_t> stamp, len;
    int32_t now = 0;
    void begin(const Shared *s, const std::vector<Entry> *l) {
        S = s; list = l; now++;
        if (by_site.size() != (size_t)s->n_sites) {
            by_site.assign(s->n_sites, std::vector<Entry>());
            stamp.assign(s->n_sites, 0); len.assign(s->n_sites, -1);
        }
    }
    const std::vector<Entry> &get(int32_t site, int32_t L) {
        if (stamp[site] != now || len[site] != L) {
            std::vector<Entry> &v = by_site[site];
            v.clear();
            const uint8_t *fm = &S->fires[(size_t)site * S->n_conds];
            for (int32_t j = 0; j < L; j++) {
                const Entry &e = (*list)[j];
                if (fm[S->skills[e.skill].cond]) v.push_back(e);
            }
            stamp[site] = now; len[site] = L;
        }
        return by_site[site];
    }
};

// hif_route.fire on the abilities that meet the site's event: each one's cap, then count + rate and the effect
static inline void fire(const Shared &S, double *v, double *cnt, const std::vector<Entry> &fl) {
    for (const Entry &e : fl) {
        const Skill &sk = S.skills[e.skill];
        double n = cnt[e.col];
        if (sk.cap != 0.0 && n >= sk.cap) continue;
        cnt[e.col] = n + sk.rate;
        if (sk.kind != K_NONE) add(v, sk.tcol, sk.val);
    }
}

// one op list on one state; `L` is the run's list length in effect
static void run_ops(const Ctx &C, double *v, double *cnt, double *aud, uint8_t *has, FireCache &fc,
                    int32_t L, int32_t o0, int32_t o1, double *shares) {
    for (int32_t k = o0; k < o1; k++) {
        const OpRec &op = C.P->ops[k];
        switch (op.code) {
            case OP_ADD: add(v, op.a, op.x); break;
            case OP_ADD_GROW: add(v, op.a, py_floordiv(op.x * (1000.0 + v[op.b]), 1000.0)); break;
            case OP_ADD_FRAC: add(v, op.a, py_floordiv(v[MAXST] * (double)op.b, 1000.0)); break;
            case OP_ADD_REST: add(v, op.a, py_round(v[MAXST] * op.x)); break;
            case OP_SHARES: {
                double p3[3], tot = 0.0;
                for (int a = 0; a < 3; a++) { double x = v[VO + a]; p3[a] = cube(x > 1.0 ? x : 1.0); }
                tot = tot + p3[0]; tot = tot + p3[1]; tot = tot + p3[2];
                for (int a = 0; a < 3; a++) shares[a] = p3[a] / tot;
            } break;
            case OP_AUD:
                aud[op.a * 4 + 0] = v[VO]; aud[op.a * 4 + 1] = v[DA]; aud[op.a * 4 + 2] = v[VI]; aud[op.a * 4 + 3] = v[STAR];
                has[op.a] = 1;
                break;
            case OP_SEL_GROWTH: {
                int64_t f = op.a, r = op.b, base[3];
                int big = 0;
                if (shares[1] > shares[big]) big = 1;
                if (shares[2] > shares[big]) big = 2;
                for (int a = 0; a < 3; a++) base[a] = f + (int64_t)floor((double)r * shares[a]);
                int64_t others = 0;
                for (int a = 0; a < 3; a++) if (a != big) others += base[a];
                base[big] = 3 * f + r - others;
                for (int a = 0; a < 3; a++) {
                    double b = (double)base[a];
                    add(v, VO + a, b + py_floordiv(b * v[GVO + a], 1000.0));
                }
            } break;
            case OP_FIRE: fire(*C.S, v, cnt, fc.get(op.a, L)); break;
            default: break;
        }
    }
}

// beam over one program for one run; returns false when the run left the Python path (a step without a feasible
// candidate that gains an item or changes the deck)
struct Hist { std::vector<int32_t> parent, cand; };

struct Work {
    States nxt, tmpb;
    FireCache fc;
    std::vector<double> jv;
    std::vector<int32_t> par, cid, ev_len, ev_on, grp, idx, keep, lut;
};

static bool run_program(const Ctx &C, Work &w, Run &R, States &cur, int32_t k, std::vector<Hist> &hist,
                        std::vector<int32_t> &step_used) {
    const Program &P = *C.P;
    States &nxt = w.nxt, &tmpb = w.tmpb;
    nxt.W = tmpb.W = cur.W;
    hist.clear(); step_used.assign(P.steps.size(), 0);
    for (size_t s = 0; s < P.steps.size(); s++) {
        const Step &st = P.steps[s];
        std::vector<Entry> list = R.list;
        std::vector<uint8_t> held = R.held;
        int32_t ne = st.e1 - st.e0;
        w.ev_len.assign(ne, 0); w.ev_on.assign(ne, 1);
        for (int32_t e = 0; e < ne; e++) {
            const OpRec &op = P.ops[st.e0 + e];
            if (op.code == OP_ITEM) {
                int32_t it = op.b;
                if (held[it]) { w.ev_on[e] = 0; continue; }
                const Group &g = *R.g;
                for (int32_t j = g.it0[it]; j < g.it1[it]; j++) list.push_back(g.items[j]);
                if (g.it1[it] > g.it0[it]) held[it] = 1;
            }
            w.ev_len[e] = (int32_t)list.size();
        }
        w.fc.begin(C.S, &list);
        int32_t L0 = (int32_t)R.list.size();
        int32_t nc = st.c1 - st.c0;
        nxt.resize(cur.n * (nc > 0 ? nc : 1)); tmpb.resize(1);
        w.jv.clear(); w.par.clear(); w.cid.clear();
        int32_t m = 0;
        double shares[3] = {0, 0, 0};
        for (int32_t i = 0; i < cur.n; i++) {
            const double *vi = &cur.v[(size_t)i * NCOL];
            for (int32_t c = 0; c < nc; c++) {
                const Cand &cd = P.cands[st.c0 + c];
                if (cd.feas == 1 && !(vi[ST] >= cd.feas_x)) continue;
                nxt.copy_from(m, cur, i);
                double *v = &nxt.v[(size_t)m * NCOL];
                double *cn = cur.W ? &nxt.cnt[(size_t)m * cur.W] : nullptr;
                double *au = &nxt.aud[(size_t)m * 12];
                uint8_t *hs = &nxt.has[(size_t)m * 3];
                if (cd.mix < 0) {
                    run_ops(C, v, cn, au, hs, w.fc, L0, cd.a0, cd.a1, shares);
                } else {
                    tmpb.copy_from(0, cur, i);
                    double *vb = &tmpb.v[0];
                    double *cb = cur.W ? &tmpb.cnt[0] : nullptr;
                    run_ops(C, v, cn, au, hs, w.fc, L0, cd.a0, cd.a1, shares);
                    run_ops(C, vb, cb, &tmpb.aud[0], &tmpb.has[0], w.fc, L0, cd.b0, cd.b1, shares);
                    double q = C.q[cd.mix];
                    for (int x = 0; x < NCOL; x++) v[x] = (1.0 - q) * v[x] + q * vb[x];
                    for (int32_t x = 0; x < cur.W; x++) cn[x] = (1.0 - q) * cn[x] + q * cb[x];
                }
                for (int32_t e = 0; e < ne; e++) {
                    if (!w.ev_on[e]) continue;
                    fire(*C.S, v, cn, w.fc.get(P.ops[st.e0 + e].a, w.ev_len[e]));
                }
                w.jv.push_back(J(v)); w.par.push_back(i); w.cid.push_back(c);
                m++;
            }
        }
        if (m == 0) {
            if (st.has_items_or_deck) return false;
            hist.push_back(Hist());
            continue;
        }
        step_used[s] = 1;
        R.list.swap(list); R.held.swap(held);
        w.grp.resize(m); w.idx.resize(m);
        int32_t ng = 0;
        for (int32_t j = 0; j < m; j++) {
            int64_t key = (int64_t)nxt.v[(size_t)j * NCOL + ST];
            if (key < 0 || key > 1000000) return false;
            if ((int64_t)w.lut.size() <= key) w.lut.resize((size_t)key + 1, -1);
            if (w.lut[key] < 0) w.lut[key] = ng++;
            w.grp[j] = w.lut[key]; w.idx[j] = j;
        }
        for (int32_t j = 0; j < m; j++) w.lut[(int64_t)nxt.v[(size_t)j * NCOL + ST]] = -1;
        const std::vector<double> &jv = w.jv;
        const std::vector<int32_t> &grp = w.grp;
        std::stable_sort(w.idx.begin(), w.idx.end(), [&](int32_t a, int32_t b) {
            if (grp[a] != grp[b]) return grp[a] < grp[b];
            return -jv[a] < -jv[b];
        });
        w.keep.clear();
        int32_t g_prev = -1, rank = 0;
        for (int32_t j : w.idx) {
            if (grp[j] != g_prev) { g_prev = grp[j]; rank = 0; }
            if (rank < k) w.keep.push_back(j);
            rank++;
        }
        cur.resize((int32_t)w.keep.size());
        Hist h;
        h.parent.resize(w.keep.size()); h.cand.resize(w.keep.size());
        for (size_t j = 0; j < w.keep.size(); j++) {
            cur.copy_from((int32_t)j, nxt, w.keep[j]);
            h.parent[j] = w.par[w.keep[j]]; h.cand[j] = w.cid[w.keep[j]];
        }
        hist.push_back(std::move(h));
    }
    return true;
}

// ranks the final states by -J (stable): positions in `cur`
static std::vector<int32_t> ranked(const States &cur) {
    std::vector<int32_t> idx(cur.n);
    std::vector<double> jv(cur.n);
    for (int32_t i = 0; i < cur.n; i++) { idx[i] = i; jv[i] = J(&cur.v[(size_t)i * NCOL]); }
    std::stable_sort(idx.begin(), idx.end(), [&](int32_t a, int32_t b) { return -jv[a] < -jv[b]; });
    return idx;
}

static void route_of(const std::vector<Hist> &hist, const std::vector<int32_t> &used, int32_t pos, int32_t *out) {
    int32_t ns = (int32_t)used.size();
    for (int32_t s = ns - 1; s >= 0; s--) {
        if (!used[s]) { out[s] = -1; continue; }
        out[s] = hist[s].cand[pos];
        pos = hist[s].parent[pos];
    }
}

static void forecast_one(const Shared &S, const Form &F, Work &w, double *od, int32_t *oi) {
    int32_t ns7 = (int32_t)S.p7.steps.size(), ns8 = (int32_t)S.p8.steps.size();
    for (int x = 0; x < OUT_D; x++) od[x] = 0.0;
    for (int32_t s = 0; s < ns7 + ns8; s++) oi[s] = -1;
    Ctx c7 = {&S, &S.p7, F.q}, c8 = {&S, &S.p8, F.q};
    Run r7; r7.init(&F.g7);
    States st7; st7.W = F.g7.W; st7.resize(1);
    memcpy(&st7.v[0], F.v0, sizeof(F.v0));
    if (st7.W) memset(&st7.cnt[0], 0, sizeof(double) * st7.W);
    memset(&st7.aud[0], 0, sizeof(double) * 12); memset(&st7.has[0], 0, 3);
    std::vector<Hist> h7; std::vector<int32_t> u7;
    if (!run_program(c7, w, r7, st7, S.k7, h7, u7)) { od[0] = ST_DIVERGED; return; }
    std::vector<int32_t> fin7 = ranked(st7);
    int32_t n_end = std::min<int32_t>((int32_t)fin7.size(), S.n7);
    bool have = false;
    double best_j = 0;
    for (int32_t e = 0; e < n_end; e++) {
        int32_t i7 = fin7[e];
        double v7[NCOL];
        memcpy(v7, &st7.v[(size_t)i7 * NCOL], sizeof(v7));
        for (size_t j = 0; j < F.ev_col.size(); j++) add(v7, F.ev_col[j], F.ev_x[j]);
        Run r8; r8.init(&F.g8);
        States st8; st8.W = F.g8.W; st8.resize(1);
        double *v8 = &st8.v[0];
        for (int x = 0; x < NCOL; x++) v8[x] = S.p8_start[x];
        v8[VO] = v7[VO]; v8[DA] = v7[DA]; v8[VI] = v7[VI]; v8[STAR] = v7[STAR];
        v8[MAXST] = v7[MAXST]; v8[ST] = v7[MAXST];
        v8[GVO] = v7[GVO]; v8[GDA] = v7[GDA]; v8[GVI] = v7[GVI];
        if (st8.W) memset(&st8.cnt[0], 0, sizeof(double) * st8.W);
        for (size_t j = 0; j + 1 < F.carry.size(); j += 2)
            st8.cnt[F.carry[j + 1]] = st7.cnt[(size_t)i7 * st7.W + F.carry[j]];
        memset(&st8.aud[0], 0, sizeof(double) * 12); memset(&st8.has[0], 0, 3);
        std::vector<Hist> h8; std::vector<int32_t> u8;
        if (!run_program(c8, w, r8, st8, S.k8, h8, u8)) { od[0] = ST_DIVERGED; return; }
        std::vector<int32_t> fin8 = ranked(st8);
        int32_t p = fin8[0];
        const double *w = &st8.v[(size_t)p * NCOL];
        double pre = st8.has[(size_t)p * 3 + 2] ? st8.aud[(size_t)p * 12 + 2 * 4 + 3] : w[STAR];
        double lim = w[LIMIT], t = 0.0;
        t = t + ((w[VO] < lim) ? w[VO] : lim);
        t = t + ((w[DA] < lim) ? w[DA] : lim);
        t = t + ((w[VI] < lim) ? w[VI] : lim);
        double j8 = 2.0 * t + 7.5 * ((pre < S.star_clamp) ? pre : S.star_clamp);
        if (!have || j8 > best_j) {
            have = true; best_j = j8;
            od[1] = j8;
            memcpy(od + 2, v7, sizeof(double) * NCOL);
            memcpy(od + 2 + NCOL, w, sizeof(double) * NCOL);
            memcpy(od + 2 + 2 * NCOL, &st8.aud[(size_t)p * 12], sizeof(double) * 12);
            for (int a = 0; a < 3; a++) od[2 + 2 * NCOL + 12 + a] = st8.has[(size_t)p * 3 + a];
            route_of(h7, u7, i7, oi);
            route_of(h8, u8, p, oi + ns7);
        }
    }
    od[0] = ST_OK;
}

// the whole call: input arrays -> per formation OUT_D doubles and (steps 選抜 + steps 本戦) route ints.
static int64_t forecast_call(const int32_t *I, int64_t ni, const double *D, int64_t nd, double *out_d,
                             int32_t *out_i, int64_t out_d_cap, int64_t out_i_cap) {
    Rd r = {I, ni, 0, D, nd, 0, false};
    if (r.i() != MAGIC || r.i() != VERSION) return -1;
    Shared S;
    int32_t nsk = r.i();
    for (int32_t j = 0; j < nsk && !r.bad; j++) {
        Skill k;
        k.cond = r.i(); k.kind = r.i(); k.tcol = r.i(); k.cap = r.d(); k.rate = r.d(); k.val = r.d();
        S.skills.push_back(k);
    }
    S.n_sites = r.i(); S.n_conds = r.i();
    if (r.bad || S.n_sites < 0 || S.n_conds < 0 || (int64_t)S.n_sites * S.n_conds > ni) return -1;
    S.fires.assign((size_t)S.n_sites * S.n_conds, 0);
    for (int64_t j = 0; j < (int64_t)S.n_sites * S.n_conds && !r.bad; j++) S.fires[j] = (uint8_t)r.i();
    read_program(r, S.p7);
    read_program(r, S.p8);
    S.k7 = r.i(); S.n7 = r.i(); S.k8 = r.i();
    S.star_clamp = r.d();
    for (int x = 0; x < NCOL; x++) S.p8_start[x] = r.d();
    int32_t nf = r.i();
    std::vector<Form> forms(nf > 0 ? nf : 0);
    for (int32_t f = 0; f < nf && !r.bad; f++) {
        Form &F = forms[f];
        for (int x = 0; x < NCOL; x++) F.v0[x] = r.d();
        for (int a = 0; a < 3; a++) F.q[a] = r.d();
        read_group(r, F.g7, S.p7.n_items);
        read_group(r, F.g8, S.p8.n_items);
        int32_t nc = r.i();
        for (int32_t j = 0; j < 2 * nc && !r.bad; j++) F.carry.push_back(r.i());
        int32_t ne = r.i();
        for (int32_t j = 0; j < ne && !r.bad; j++) { F.ev_col.push_back(r.i()); F.ev_x.push_back(r.d()); }
    }
    if (r.bad || r.pi != ni || r.pd != nd) return -1;
    for (const Skill &k : S.skills)
        if (k.cond < 0 || k.cond >= S.n_conds || k.tcol < 0 || k.tcol >= NCOL) return -1;
    for (const Program *P : {&S.p7, &S.p8}) {
        for (const OpRec &op : P->ops) {
            if (op.code < OP_ADD || op.code > OP_ITEM) return -1;
            if ((op.code == OP_FIRE || op.code == OP_ITEM) && (op.a < 0 || op.a >= S.n_sites)) return -1;
            if (op.code == OP_ITEM && (op.b < 0 || op.b >= P->n_items)) return -1;
            if ((op.code == OP_ADD || op.code == OP_ADD_GROW || op.code == OP_ADD_FRAC || op.code == OP_ADD_REST)
                && (op.a < 0 || op.a >= NCOL)) return -1;
            if (op.code == OP_ADD_GROW && (op.b < 0 || op.b >= NCOL)) return -1;
            if (op.code == OP_AUD && (op.a < 0 || op.a > 2)) return -1;
        }
        for (const Cand &c : P->cands) if (c.mix > 2) return -1;
    }
    for (const Form &F : forms) {
        for (const Group *g : {&F.g7, &F.g8}) {
            for (const Entry &e : g->base) if (e.col < 0 || e.col >= g->W || e.skill < 0 || e.skill >= nsk) return -1;
            for (const Entry &e : g->items) if (e.col < 0 || e.col >= g->W || e.skill < 0 || e.skill >= nsk) return -1;
        }
        for (size_t j = 0; j < F.carry.size(); j += 2)
            if (F.carry[j] < 0 || F.carry[j] >= F.g7.W || F.carry[j + 1] < 0 || F.carry[j + 1] >= F.g8.W) return -1;
        for (int32_t c : F.ev_col) if (c < 0 || c >= NCOL) return -1;
    }
    int64_t ns = (int64_t)S.p7.steps.size() + (int64_t)S.p8.steps.size();
    if ((int64_t)nf * OUT_D > out_d_cap || (int64_t)nf * ns > out_i_cap) return -1;
    Work w;
    for (int32_t f = 0; f < nf; f++) forecast_one(S, forms[f], w, out_d + (int64_t)f * OUT_D, out_i + (int64_t)f * ns);
    return nf;
}

}
