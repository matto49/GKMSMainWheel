# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the produce forecast on the native core (GakumasCLI/native/core_produce.h).

    Python path then skips that step's events for that run only) runs Forecaster.forecast.
"""
from __future__ import annotations

import ctypes as C
import json
import os
import sys
import threading
from array import array
from pathlib import Path

HERE = Path(__file__).resolve().parent
import GakumasCLI.produce.formation as F  # noqa: E402
import GakumasCLI.produce.hif_route as H  # noqa: E402
import GakumasCLI.produce.produce_effects as pe  # noqa: E402
from GakumasCLI.produce.produce_ledger import STEP_TYPE  # noqa: E402

VERSION = 1
MAGIC = 0x50524431
COLS = ("vocal", "dance", "visual", "stamina", "max_stamina", "star", "pp", "g_vocal", "g_dance", "g_visual", "limit")
CI = {k: i for i, k in enumerate(COLS)}
ATTRS = H.ATTRS
OUT_D = 40
AUD_INDEX = {"audition_mid1": 0, "audition_mid2": 1, "audition_final": 2}
OP_ADD, OP_ADD_GROW, OP_ADD_FRAC, OP_ADD_REST, OP_SHARES, OP_AUD, OP_SEL_GROWTH, OP_FIRE, OP_ITEM = range(1, 10)
K_NONE, K_ATTR, K_STAM, K_PLAIN = range(4)
INF = float("inf")
REPO = HERE.parents[1]

_LIB = {}
_LOCK = threading.Lock()


class Unsupported(Exception):
    """A schedule the program cannot express (the Python forecast raises or runs it)."""


def library(path=None):
    """The loaded core with gk_produce_forecast at VERSION, else None (cached per process)."""
    if path is None and "lib" in _LIB:
        return _LIB["lib"]
    lib = None
    if path is not None or os.environ.get("GKMS_PRODUCE_NATIVE", "1") != "0":
        cands = [os.environ.get("GKMS_NATIVE_LIB")] if os.environ.get("GKMS_NATIVE_LIB") else []
        g = REPO / "GakumasCLI" / "native"
        cands += [str(g / "build" / "gkmscore.dll")] if os.name == "nt" else [str(g / "libgkmscore.so")]
        if path is not None:
            cands = [str(path)]
        for path in cands:
            if not path or not os.path.exists(path):
                continue
            try:
                h = C.CDLL(path)
                h.gk_produce_version.restype = C.c_int
                if h.gk_produce_version() != VERSION:
                    continue
                f = h.gk_produce_forecast
            except (OSError, AttributeError):
                continue
            f.restype = C.c_int64
            f.argtypes = [C.c_void_p, C.c_int64, C.c_void_p, C.c_int64, C.c_void_p, C.c_int64, C.c_void_p, C.c_int64]
            lib = h
            break
    _LIB["lib"] = lib
    return lib


def _key(src, sk):
    return (src, sk.id, sk.effect_id)


def _cond_class(sk):
    return (sk.phase, json.dumps(sk.cond, sort_keys=True))


class Program:
    """One recorded produce as a step program (hif_start.Forecaster.run_produce's schedule, candidates, events and
    decks): steps [(step number, candidate names, has_items_or_deck, [cand], events)], each cand
    (mix attr or -1, feasibility (kind, x), ops a, ops b), ops [(code, a, b, x)]; sites [(phase, event, deck)]."""

    def __init__(self, fc, p, deck0, sites):
        w = fc.w
        self.sites = sites
        self.items = []
        self.steps = []
        sched = H.schedule(p)
        events, decks = H.recorded_card_events(p, w), fc._decks(p)
        rec = {x: STEP_TYPE.get(sched[x].get("selected_step_type"), sched[x].get("selected_step_type")) for x in sched}
        pid = p["produce_id"]
        deck = deck0
        for st in sorted(sched):
            row = sched[st]
            cands = [STEP_TYPE.get(t, t) for t in row.get("step_types") or []]
            if H.card_step(row) and rec[st] is not None:
                cands = [rec[st]]
            cands = [ch for ch in cands if ch is not None and
                     (H.parse_choice(ch)[0] in ("lesson", "school", "refresh") or ch == rec[st])]
            if not cands:
                continue
            compiled = [self._cand(fc, pid, st, row, ch, deck) for ch in cands]
            evops, has_items = [], False
            for ph, ev in events.get(st, []):
                e3 = {kk: ev.get(kk) for kk in ("card", "upgrade", "item")}
                if ph == "GET_PRODUCE_ITEM":
                    if not ev.get("item"):
                        continue
                    if ev["item"] not in self.items:
                        self.items.append(ev["item"])
                    evops.append((OP_ITEM, self._site(ph, e3, deck), self.items.index(ev["item"]), 0.0))
                    has_items = True
                else:
                    evops.append((OP_FIRE, self._site(ph, e3, deck), 0, 0.0))
            changes_deck = bool(decks.get(st))
            self.steps.append((st, cands, has_items or changes_deck, compiled, evops))
            if decks.get(st):
                deck = decks[st]

    def _site(self, phase, ev, deck):
        key = (phase, tuple(sorted(ev.items())), id(deck))
        idx = self.sites["index"].get(key)
        if idx is None:
            idx = self.sites["index"][key] = len(self.sites["list"])
            self.sites["list"].append((phase, dict(ev), deck))
        return idx

    def _lesson(self, fc, pid, choice, lid, deck):
        """hif_route.do_step on a lesson: its ops (the lesson row `lid`)."""
        c = H.parse_choice(choice)
        les = fc.w.lessons.get(lid or "")
        if les is None:
            raise Unsupported(f"lesson {lid}")
        kind = "sp" if c[2] else "normal"
        ev = {"attr": c[1], "kind": kind}
        sub = H.SHORT[lid.rsplit("sub_", 1)[1]]
        return [(OP_ADD, CI["stamina"], 0, float(-les["stamina"])),
                (OP_FIRE, self._site("START_LESSON", ev, deck), 0, 0.0),
                (OP_ADD_GROW, CI[c[1]], CI["g_" + c[1]], float(les["mainParameter"])),
                (OP_ADD_GROW, CI[sub], CI["g_" + sub], float(les["subParameter"])),
                (OP_ADD, CI["star"], 0, float(les["star"] * (1000 + H.STAR_PERMIL) // 1000)),
                (OP_ADD, CI["pp"], 0, float(H.LESSON_PP[pid])),
                (OP_FIRE, self._site("END_LESSON", ev, deck), 0, 0.0),
                (OP_FIRE, self._site("END_LESSON_BEFORE_PRESENT", ev, deck), 0, 0.0)]

    def _cand(self, fc, pid, st, row, ch, deck):
        c = H.parse_choice(ch)
        if c[0] == "lesson":
            lid = row.get(H.lesson_key(c))
            les = fc.w.lessons.get(lid.replace("-sp-sub_", "-sub_")) if lid else None
            feas = (1, float(les["stamina"])) if les is not None else (1, INF)
            if lid is None:
                return (-1, feas, [], [])
            normal = lid.replace("-sp-sub_", "-sub_")
            spid = normal.replace("-sub_", "-sp-sub_")
            base = "open_lesson_" + {"vocal": "vo", "dance": "da", "visual": "vi"}[c[1]]
            n_name, s_name = base + ("_star" if c[3] else ""), base + "_sp" + ("_star" if c[3] else "")
            if les is None:
                return (-1, feas, [], [])
            return (ATTRS.index(c[1]), feas, self._lesson(fc, pid, n_name, normal, deck),
                    self._lesson(fc, pid, s_name, spid, deck))
        feas = (1, float(H.SCHOOL_STAMINA)) if c[0] == "school" else (0, 0.0)
        ops = []
        if c[0] == "school":
            b = H.SCHOOL_BASE.get((pid, st)) or (180 if pid == "produce-008" else (120 if st < 7 else 150))
            ops = [(OP_ADD, CI[c[1]], 0, float(b)), (OP_ADD, CI["stamina"], 0, float(-H.SCHOOL_STAMINA)),
                   (OP_FIRE, self._site("END_STEP_EVENT_SCHOOL", {}, deck), 0, 0.0)]
        elif c[0] == "refresh":
            ops = [(OP_FIRE, self._site("START_REFRESH", {}, deck), 0, 0.0),
                   (OP_ADD_FRAC, CI["stamina"], H.REFRESH_PERMIL, 0.0)]
        elif c[0] == "event_activity":
            ops = [(OP_ADD_FRAC, CI["stamina"], H.ACTIVITY_PERMIL, 0.0),
                   (OP_FIRE, self._site("END_STEP_EVENT_ACTIVITY", {}, deck), 0, 0.0)]
        elif c[0] in ("fan_present", "present"):
            ops = [(OP_FIRE, self._site("START_PRESENT", {}, deck), 0, 0.0), (OP_ADD, CI["pp"], 0, float(H.FAN_PRESENT_PP))]
        elif c[0] == "shop":
            ops = [(OP_FIRE, self._site("START_SHOP", {}, deck), 0, 0.0)]
        elif c[0] == "customize":
            ops = [(OP_FIRE, self._site("START_CUSTOMIZE", {}, deck), 0, 0.0)]
        elif c[0].startswith("audition_"):
            a = H.AUDITION.get((pid, c[0]))
            if a is None:
                raise Unsupported(f"{pid} {c[0]}")
            tail = pe.AUDITION[c[0]]
            ops = [(OP_SHARES, 0, 0, 0.0),
                   (OP_ADD_REST, CI["stamina"], 0, H.AUDITION_REST),
                   (OP_AUD, AUD_INDEX[c[0]], 0, 0.0),
                   (OP_FIRE, self._site("START_AUDITION", {}, deck), 0, 0.0),
                   (OP_FIRE, self._site("START_AUDITION_" + tail, {}, deck), 0, 0.0),
                   (OP_ADD, CI["stamina"], 0, float(a["stamina"]))]
            if a["growth"]:
                ops.append((OP_SEL_GROWTH, int(a["growth"][0]), int(a["growth"][1]), 0.0))
            ops += [(OP_ADD, CI["star"], 0, float(a["star"])), (OP_ADD, CI["pp"], 0, float(a["pp"])),
                    (OP_FIRE, self._site("END_AUDITION", {}, deck), 0, 0.0),
                    (OP_FIRE, self._site("END_AUDITION_" + tail, {}, deck), 0, 0.0)]
        return (-1, feas, ops, [])


class Compiled:
    """The two programs of one (選抜, 本戦) pair, the fire table cache and the start parts that do not change."""

    def __init__(self, fc, p7, p8):
        self.fc, self.p7, self.p8 = fc, p7, p8
        w = fc.w
        self.sites = {"index": {}, "list": []}
        self.v0, self.items0, self.deck0 = H.start_state(p7, w, 0)
        self.v8, self.items8, self.deck8 = H.start_state(p8, w, 0)
        self.P7 = Program(fc, p7, self.deck0, self.sites)
        self.P8 = Program(fc, p8, self.deck8, self.sites)
        self.fire_cache = {}
        self.item_skills = {}
        self.skills, self.skill_idx, self.conds, self.cond_idx, self.by_id = [], {}, [], {}, {}
        self._prog, self._bits = None, None

    def sk_index(self, sk):
        hit = self.by_id.get(id(sk))
        if hit is not None and hit[0] is sk:
            return hit[1]
        cls = _cond_class(sk)
        ci = self.cond_idx.get(cls)
        if ci is None:
            ci = self.cond_idx[cls] = len(self.conds)
            self.conds.append((cls, sk))
        row = (ci,) + _skill_row(sk)
        si = self.skill_idx.get(row)
        if si is None:
            si = self.skill_idx[row] = len(self.skills)
            self.skills.append(row)
        self.by_id[id(sk)] = (sk, si)
        return si

    def bits(self):
        if self._bits is None or self._bits[0] != len(self.conds):
            arr = array("i")
            for site in range(len(self.sites["list"])):
                arr.extend(1 if self.fires(site, cls, sk) else 0 for cls, sk in self.conds)
            self._bits = (len(self.conds), arr)
        return self._bits[1]

    def programs(self):
        if self._prog is None:
            e = _Enc()
            _encode_program(e, self.P7)
            _encode_program(e, self.P8)
            self._prog = (e.I, e.D)
        return self._prog

    def fires(self, site, cls, sk):
        k = (site, cls)
        hit = self.fire_cache.get(k)
        if hit is None:
            phase, ev, deck = self.sites["list"][site]
            hit = self.fire_cache[k] = bool(pe.fires(sk, phase, dict(ev, deck=deck), self.fc.w.cs))
        return hit

    def item_abil(self, it):
        out = self.item_skills.get(it)
        if out is None:
            out = self.item_skills[it] = [("item:" + it, sk) for sk in self.fc.w.M.item_skills(it)
                                          if sk.phase != "PRODUCE_START"]
        return out


class _Enc:
    def __init__(self):
        self.I, self.D = array("i"), array("d")

    def i(self, *xs):
        self.I.extend(int(x) for x in xs)

    def d(self, *xs):
        self.D.extend(float(x) for x in xs)


def _skill_row(sk):
    rate = (sk.rate_permil or 1000) / 1000
    val = (sk.value[0] or 0) * rate
    et = sk.effect_type
    if et in H.ADD:
        kind, tcol = K_ATTR, CI[H.ADD[et]]
    elif et == "STAR_ADDITION":
        kind, tcol, val = K_PLAIN, CI["star"], val * (1000 + H.STAR_PERMIL) / 1000
    elif et == "STAMINA_RECOVER_FIX":
        kind, tcol = K_STAM, CI["stamina"]
    elif et == "PRODUCE_POINT_ADDITION":
        kind, tcol = K_PLAIN, CI["pp"]
    else:
        kind, tcol = K_NONE, 0
    return kind, tcol, float(sk.cap or 0), float(rate), float(val)


def _group(cols, base, prog_items, item_abil):
    """cols: key -> column (filled in order: the list, then the extra keys the caller added, then each item's)."""
    held = {src for src, _ in base}
    rows = []
    for it in prog_items:
        ab = item_abil(it)
        for src, sk in ab:
            cols.setdefault(_key(src, sk), len(cols))
        rows.append((("item:" + it) in held, ab))
    return rows


def _encode_ops(e, ops):
    e.i(len(ops))
    for code, a, b, x in ops:
        e.i(code, a, b)
        e.d(x)


def _encode_program(e, P):
    e.i(len(P.items), len(P.steps))
    for _st, _names, flag, cands, evops in P.steps:
        e.i(1 if flag else 0, len(cands))
        for mix, (fk, fx), ops_a, ops_b in cands:
            e.i(mix, fk)
            e.d(fx)
            _encode_ops(e, ops_a)
            if mix >= 0:
                _encode_ops(e, ops_b)
        _encode_ops(e, evops)


def _setup(cp, form, mems, ref_supports, ref_memories):
    """Forecaster.forecast's start: v, sp, the 選抜 abilities (as in forecast)."""
    fc = cp.fc
    v = dict(cp.v0)
    up, cpart = fc.start_part(ref_supports, ref_memories), fc.start_part(form, mems)
    for key in ATTRS + ("star", "max_stamina"):
        v[key] = v.get(key, 0) - up.get(key, 0) + cpart.get(key, 0)
    v["stamina"] = v["max_stamina"]
    for a in ATTRS:
        v["g_" + a] = v["g_" + a] - up.get("g_" + a, 0) + cpart.get("g_" + a, 0)
    summ = cp.p7["summary"]
    sp = {a: (summ.get(f"lesson_{a}_sp_change_rate_permil") or 0) - up.get("sp_" + a, 0) + cpart.get("sp_" + a, 0)
          for a in ATTRS}
    return v, sp


def _compiled(fc, p7, p8):
    """The Compiled of (p7, p8), made once per Forecaster; None for a schedule the program does not express."""
    cache = fc.__dict__.setdefault("_native_programs", {})
    key = (id(p7), id(p8))
    hit = cache.get(key)
    if hit is None or hit[0] is not p7 or hit[1] is not p8:
        try:
            cp = Compiled(fc, p7, p8)
        except Unsupported:
            cp = None
        hit = cache[key] = (p7, p8, cp)
    return hit[2]


def forecast_many(fc, p7, p8, pairs, ref_supports, ref_memories, beam):
    """[Forecaster.forecast(p7, p8, form, mems, ref_supports, ref_memories, beam) for (form, mems) in pairs], or
    None when the core is not loaded or the schedule is one the program does not express."""
    with _LOCK:
        return _forecast_many(fc, p7, p8, pairs, ref_supports, ref_memories, beam)


def _forecast_many(fc, p7, p8, pairs, ref_supports, ref_memories, beam):
    lib = library()
    if lib is None:
        return None
    cp = _compiled(fc, p7, p8)
    if cp is None:
        return None
    if not pairs:
        return []
    sk_index = cp.sk_index

    forms = []
    for form, mems in pairs:
        form, mems = list(form), list(mems)
        v, sp = _setup(cp, form, mems, ref_supports, ref_memories)
        q = [1 - (1 - F.SP_BASE_P) * (1 - min(1000, max(0, sp[a])) / 1000) for a in ATTRS]
        abil7 = fc.abilities_of(form, mems, cp.items0)
        cols7 = {}
        for src, sk in abil7:
            cols7.setdefault(_key(src, sk), len(cols7))
        base7 = [(cols7[_key(src, sk)], sk_index(sk)) for src, sk in abil7]
        items7 = _group(cols7, abil7, cp.P7.items, cp.item_abil)
        items7 = [(h, [(cols7[_key(src, sk)], sk_index(sk)) for src, sk in ab]) for h, ab in items7]
        abil8 = fc.abilities_of(form, [], cp.items8)
        cols8 = {}
        for src, sk in abil8:
            cols8.setdefault(_key(src, sk), len(cols8))
        carried = [k for k in cols7 if k[0].startswith(("support:", "item:"))]
        for k in carried:
            cols8.setdefault(k, len(cols8))
        base8 = [(cols8[_key(src, sk)], sk_index(sk)) for src, sk in abil8]
        items8 = _group(cols8, abil8, cp.P8.items, cp.item_abil)
        items8 = [(h, [(cols8[_key(src, sk)], sk_index(sk)) for src, sk in ab]) for h, ab in items8]
        events = fc.support_events(form)
        if any(a not in CI for a in events):
            return None
        forms.append({"v": v, "sp": sp, "q": q, "g7": (len(cols7), base7, items7), "g8": (len(cols8), base8, items8),
                      "carry": [(cols7[k], cols8[k]) for k in carried],
                      "ev": [(CI[a], x) for a, x in events.items()]})
    e = _Enc()
    e.i(MAGIC, VERSION, len(cp.skills))
    for ci, kind, tcol, cap, rate, val in cp.skills:
        e.i(ci, kind, tcol)
        e.d(cap, rate, val)
    e.i(len(cp.sites["list"]), len(cp.conds))
    e.I.extend(cp.bits())
    pi, pd = cp.programs()
    e.I.extend(pi)
    e.D.extend(pd)
    e.i(beam[0], beam[1], 10)
    e.d(F.STAR_CLAMP)
    e.d(*[cp.v8[k] for k in COLS])
    e.i(len(forms))
    for f in forms:
        e.d(*[f["v"][k] for k in COLS])
        e.d(*f["q"])
        for W, base, items in (f["g7"], f["g8"]):
            e.i(W, len(base))
            for col, si in base:
                e.i(col, si)
            for held, ent in items:
                e.i(1 if held else 0, len(ent))
                for col, si in ent:
                    e.i(col, si)
        e.i(len(f["carry"]))
        for c7, c8 in f["carry"]:
            e.i(c7, c8)
        e.i(len(f["ev"]))
        for col, x in f["ev"]:
            e.i(col)
            e.d(x)
    n = len(forms)
    ns7, ns8 = len(cp.P7.steps), len(cp.P8.steps)
    od = (C.c_double * (n * OUT_D))()
    oi = (C.c_int32 * max(1, n * (ns7 + ns8)))()
    ib, ia = e.I.buffer_info()
    db, da = e.D.buffer_info()
    rc = lib.gk_produce_forecast(ib, ia, db, da, od, n * OUT_D, oi, max(1, n * (ns7 + ns8)))
    if rc != n:
        sys.stderr.write(f"produce_native: gk_produce_forecast returned {rc} for {n} formations\n")
        return None
    out = []
    for fi, (f, (form, mems)) in enumerate(zip(forms, pairs)):
        o = od[fi * OUT_D:(fi + 1) * OUT_D]
        if o[0] != 0:
            out.append(fc.forecast(p7, p8, list(form), list(mems), ref_supports, ref_memories, beam))
            continue
        v7, v8 = o[2:2 + 11], o[13:24]
        aud, has = o[24:36], o[36:39]
        r = oi[fi * (ns7 + ns8):(fi + 1) * (ns7 + ns8)]

        def audd(name):
            i = AUD_INDEX[name]
            vals = aud[i * 4:i * 4 + 4] if has[i] else [v8[CI[k]] for k in ATTRS + ("star",)]
            return {k: round(vals[j]) for j, k in enumerate(ATTRS + ("star",))}
        pre = aud[2 * 4 + 3] if has[2] else v8[CI["star"]]
        v = f["v"]
        out.append({"selection_end": {k: round(v7[CI[k]]) for k in ATTRS + ("star",)},
                    "final_end": {k: round(v8[CI[k]]) for k in ATTRS + ("star",)},
                    "star_before_final": round(pre), "growth": {a: v["g_" + a] for a in ATTRS}, "sp": f["sp"],
                    "max_stamina": v["max_stamina"], "mid1": audd("audition_mid1"), "final_aud": audd("audition_final"),
                    "route_selection": [cp.P7.steps[s][1][c] for s, c in enumerate(r[:ns7]) if c >= 0],
                    "route_final": [cp.P8.steps[s][1][c] for s, c in enumerate(r[ns7:]) if c >= 0]})
    return out
