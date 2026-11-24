# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H.I.F start recommender: which 6 support cards and which 4 memories to take at the 選抜 Start of an idol
card, from the user's own pool, by expected H.I.F rating.  One formation and one memory set serve both
produces: the 本戦 Start sends only the 選抜 memory it continues from.

    python -B hif_start.py prepare   ACT LOADOUT_ROOT USER.json IDOL CACHE.json [--workers N]
    python -B hif_start.py recommend ACT LOADOUT_ROOT USER.json RENTALS.json CACHE.json OUT.json [--quick]
"""
from __future__ import annotations

import collections
import copy
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

import GakumasCLI.produce.formation as F  # noqa: E402
import GakumasCLI.produce.hif_advice as A  # noqa: E402
import GakumasCLI.produce.hif_route as H  # noqa: E402
import GakumasCLI.produce.produce_effects as pe  # noqa: E402
from GakumasCLI.produce.produce_ledger import STEP_TYPE  # noqa: E402

ATTRS = H.ATTRS
SELECTION, MAIN = "produce-007", "produce-008"
ROUNDS = ("mid1", "final")
# the label tier
SHAPE = {"budget": 20.0, "levels": 4, "iterations": 8, "stabilization": 3, "search_seed": 182}
SCREEN_SEEDS, CONFIRM_SEEDS, CONFIRM_TOP, SPLIT_SEEDS = 4, 16, 4, 16
BEAM_FAST, BEAM_FULL = (6, 10), (16, 60)
MIN_LEVEL = 20
RESTARTS = 6
CAP = {"mid1": pe.HIF_ROUND1[-2][0], "final": pe.HIF_ROUND2[-2][0]}
PLAN_PREFIX = "PRODUCE_PLAN_TYPE_"


def points(rnd, score):
    """One audition's share of the rating: round-1 points, or round-2 points + 7.5 x the final's star gain."""
    if rnd == "mid1":
        return pe._piecewise(score, pe.HIF_ROUND1)
    return pe._piecewise(score, pe.HIF_ROUND2) + 7.5 * pe.hif_star_gain(score)


def param_part(final_end, star_before_final):
    return 2 * sum(min(F.PARAM_CLAMP, final_end[k]) for k in ATTRS) + 7.5 * min(F.STAR_CLAMP, star_before_final)


def exam_seeds(key, rnd, n):
    rng = random.Random(f"{key}|{rnd}")
    return [rng.randrange(1, 2 ** 31 - 1) for _ in range(n)]


def _newest(produces):
    return sorted(produces, key=lambda p: min(p.get("runs") or [""]))[-1] if produces else None


# -- master side: idol cards -------------------------------------------------------------------------
class IdolCards:
    """IdolCard (local master) and the level-limit / potential tables (master_extra.json idol_cards)."""

    def __init__(self, master=pe.MASTER_DIR, extra=F.EXTRA):
        self.cards = {r["id"]: r for r in pe._rows("IdolCard", master)}
        x = json.load(open(extra, encoding="utf-8")).get("idol_cards") or {}
        self.status_up = x.get("level_limit_status_up") or []
        self.potential = x.get("potential") or []
        self.ll_skills = x.get("level_limit_produce_skill") or []
        self.skills = {(r["id"], r.get("level")): r for r in pe._rows("ProduceSkill", master)}
        self.effects = {r["id"]: r for r in pe._rows("ProduceEffect", master)}

    def plan(self, idol):
        return pe._name(self.cards[idol].get("planType"))

    def plan_number(self, idol):
        v = self.cards[idol].get("planType")
        return int(v.get("number")) if isinstance(v, dict) else 0

    def character(self, idol):
        return self.cards[idol].get("characterId")

    def base(self, idol, ranks):
        """Parameters, growth rates and stamina the idol card brings at the user's ranks (no growth panel,
        no character part, no formation)."""
        d = self.cards[idol]
        ll_rank, pot_rank = ranks
        out = {"vocal": d["produceVocal"], "dance": d["produceDance"], "visual": d["produceVisual"],
               "g_vocal": d["produceVocalGrowthRatePermil"], "g_dance": d["produceDanceGrowthRatePermil"],
               "g_visual": d["produceVisualGrowthRatePermil"], "max_stamina": d["produceStamina"]}
        for r in self.status_up:
            if r["id"] == d.get("idolCardLevelLimitStatusUpId") and r["rank"] <= ll_rank:
                for k in ATTRS:
                    out[k] += r[k]
                out["max_stamina"] += r["stamina"]
        for r in self.potential:
            if r["id"] == d.get("idolCardPotentialId") and r["rank"] <= pot_rank:
                for k in ATTRS:
                    out["g_" + k] += r["g_" + k]
                out["max_stamina"] += r["stamina"]
        return out

    def start_item(self, idol, ranks):
        d = self.cards[idol]
        changed = any(r["id"] == d.get("idolCardPotentialId") and r["rank"] <= ranks[1] and r["item_change"]
                      for r in self.potential)
        return d["afterProduceItemId"] if changed else d["beforeProduceItemId"]

    def sp_bonus(self, idol, ranks):
        """Lesson SP rate (all three) from the idol card's level-limit produce skills at the user's rank."""
        d = self.cards[idol]
        best = {}
        for r in self.ll_skills:
            if r["id"] == d.get("idolCardLevelLimitProduceSkillId") and r["rank"] <= ranks[0]:
                best[r["skill"]] = max(best.get(r["skill"], 0), r["level"])
        out = 0
        for sid, lv in best.items():
            e = self.effects.get((self.skills.get((sid, lv)) or {}).get("produceEffectId1") or "") or {}
            eid = e.get("id") or ""
            if "lesson_sp_change_rate_permil_addition" in eid and not any(a in eid for a in ATTRS):
                out += e.get("effectValueMin") or 0
        return out


def ranks_of(user, idol):
    for c in (user or {}).get("user_idol_card_list") or []:
        if c.get("idol_card_id") == idol:
            return int(c.get("level_limit_rank") or 0), int(c.get("potential_rank") or 0)
    return 0, 0


# -- the from-start forecast ----------------------------------------------------------------------------
def route_parts(p7):
    """(support cards [(id, level)], rental flags, memories, rental flags) of a recorded 選抜 (its End)."""
    s = p7.get("summary") or {}
    rental = {c["id"]: c["rental"] for c in (p7.get("formation") or {}).get("support_cards") or []}
    sup = [(c["support_card_id"], c.get("level") or 0) for c in s.get("support_cards") or []]
    mrental = {m["id"]: m["rental"] for m in (p7.get("formation") or {}).get("memories") or []}
    mems = [m["memory"] for m in s.get("memories") or [] if m.get("memory")]
    return sup, [rental.get(c, False) for c, _ in sup], mems, [mrental.get(m.get("user_memory_id"), False) for m in mems]


class Forecaster:
    """The 選抜 + 本戦 of a reference schedule with another formation and memories swapped in (hif_route's step
    model): Start = the reference Start minus the reference formation's and memories' PRODUCE_START parts plus the
    candidate's; lessons as the expectation over SP (formation.SP_BASE_P); route by beam over lesson / school /
    refresh, other steps as recorded; 選抜 audition shares params^3 / sum; support events x EVENT_P at the 選抜 end."""

    def __init__(self, world):
        self.w = world
        self.support_events_t = json.load(open(F.EXTRA, encoding="utf-8"))["support_events"]
        self.eff = {r["id"]: r for r in pe._rows("ProduceEffect", world.master)}
        self._sup, self._mem = {}, {}

    def support_skills(self, cid, lv):
        hit = self._sup.get((cid, lv))
        if hit is None:
            hit = self._sup[(cid, lv)] = [sk for _, sk in self.w.M.support_skills(cid, lv) if sk is not None]
        return hit

    def memory_skill(self, ability_id, level):
        k = (ability_id, level)
        if k not in self._mem:
            self._mem[k] = self.w.M.memory_skill(ability_id, level)
        return self._mem[k]

    def skills(self, supports, memories):
        for cid, lv in supports:
            for sk in self.support_skills(cid, lv):
                yield "support:" + cid, sk
        for i, m in enumerate(memories):
            for ab in m.get("abilities") or []:
                sk = self.memory_skill(ab["id"], ab.get("level"))
                if sk is not None:
                    yield f"memory:{i}", sk

    def start_part(self, supports, memories):
        out = collections.defaultdict(float)
        for _src, sk in self.skills(supports, memories):
            if sk.phase != "PRODUCE_START":
                continue
            v = (sk.value[0] or 0) * (sk.rate_permil or 1000) / 1000
            et = sk.effect_type
            if et in H.ADD:
                out[H.ADD[et]] += v
            elif et == "STAR_ADDITION":
                out["star"] += v * 1.5
            elif et == "MAX_STAMINA_ADDITION":
                out["max_stamina"] += v
            elif et in F.GROWTH_EFFECT:
                out["g_" + F.GROWTH_EFFECT[et]] += v
            elif et in F.SP_EFFECT:
                for a in F.SP_EFFECT[et]:
                    out["sp_" + a] += v
        return out

    def support_events(self, supports):
        out = collections.defaultdict(float)
        for cid, lv in supports:
            value_up = sum((sk.value[0] or 0) for sk in self.support_skills(cid, lv)
                           if sk.effect_type == "SUPPORT_CARD_EVENT_PARAMETER_ADDITION_VALUE_UP")
            for e in self.support_events_t.get(cid, []):
                if lv < e["level"]:
                    continue
                for eid in e["effects"]:
                    ef = self.eff.get(eid) or {}
                    et = pe._name(ef.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")
                    if et in F.PARAM_EFFECT:
                        out[F.PARAM_EFFECT[et]] += F.EVENT_P * ((ef.get("effectValueMin") or 0) * (1000 + value_up) // 1000)
        return out

    def abilities_of(self, supports, memories, items):
        out = list(self.skills(supports, memories))
        for it in items:
            for sk in self.w.M.item_skills(it):
                out.append(("item:" + it, sk))
        return [(src, sk) for src, sk in out if sk.phase != "PRODUCE_START"]

    @staticmethod
    def _mix(a, b, q):
        for k in set(a.v) | set(b.v):
            a.v[k] = (1 - q) * a.v.get(k, 0) + q * b.v.get(k, 0)
        for k in set(a.counts) | set(b.counts):
            a.counts[k] = (1 - q) * a.counts.get(k, 0) + q * b.counts.get(k, 0)
        a.aud = b.aud if b.aud else a.aud

    def _step(self, s, p, st, row, choice, sp):
        c = H.parse_choice(choice)
        if c[0] != "lesson":
            shares = None
            if c[0].startswith("audition_"):
                tot = H.lsum(max(1.0, s.v[k]) ** H.SHARE_POWER for k in ATTRS)
                shares = [max(1.0, s.v[k]) ** H.SHARE_POWER / tot for k in ATTRS]
            H.do_step(s, p["produce_id"], st, row, choice, self.w, shares)
            return
        lid = row[H.lesson_key(c)]
        normal = lid.replace("-sp-sub_", "-sub_")
        spid = normal.replace("-sub_", "-sp-sub_")
        q = 1 - (1 - F.SP_BASE_P) * (1 - min(1000, max(0, sp[c[1]])) / 1000)
        base = "open_lesson_" + {"vocal": "vo", "dance": "da", "visual": "vi"}[c[1]]
        n_name, s_name = base + ("_star" if c[3] else ""), base + "_sp" + ("_star" if c[3] else "")
        a, b = s.copy(), s.copy()
        H.do_step(a, p["produce_id"], st, {H.lesson_key(H.parse_choice(n_name)): normal}, n_name, self.w)
        H.do_step(b, p["produce_id"], st, {H.lesson_key(H.parse_choice(s_name)): spid}, s_name, self.w)
        self._mix(a, b, q)
        s.v, s.counts, s.log = a.v, a.counts, a.log

    def _feasible(self, s, row, ch):
        c = H.parse_choice(ch)
        if c[0] == "lesson":
            lid = row.get(H.lesson_key(c))
            les = self.w.lessons.get(lid.replace("-sp-sub_", "-sub_")) if lid else None
            return les is not None and s.v["stamina"] >= les["stamina"]
        if c[0] == "school":
            return s.v["stamina"] >= H.SCHOOL_STAMINA
        return True

    @staticmethod
    def _J(s):
        return 2 * H.lsum(min(s.v["limit"], s.v[k]) for k in ATTRS) + 7.5 * s.v["star"]

    @staticmethod
    def _decks(p):
        return {o["at_step"]: o["deck"] for o in p["occasions"] if o.get("at_step") and o.get("deck")}

    def _fire_events(self, t, evs):
        for ph, ev in evs:
            if ph == "GET_PRODUCE_ITEM":
                if not ev.get("item") or ("item:" + ev["item"]) in {src for src, _ in t.abil}:
                    continue
                t.abil = t.abil + [("item:" + ev["item"], sk) for sk in self.w.M.item_skills(ev["item"])
                                   if sk.phase != "PRODUCE_START"]
            H.fire(t, ph, {kk: ev.get(kk) for kk in ("card", "upgrade", "item")}, self.w)

    def run_produce(self, p, s0, sp, k, keep_all):
        sched = H.schedule(p)
        events, decks = H.recorded_card_events(p, self.w), self._decks(p)
        rec = {x: STEP_TYPE.get(sched[x].get("selected_step_type"), sched[x].get("selected_step_type")) for x in sched}
        states = [(s0, {})]
        for st in sorted(sched):
            row = sched[st]
            cands = [STEP_TYPE.get(t, t) for t in row.get("step_types") or []]
            if H.card_step(row) and rec[st] is not None:
                cands = [rec[st]]
            cands = [ch for ch in cands if ch is not None and
                     (H.parse_choice(ch)[0] in ("lesson", "school", "refresh") or ch == rec[st])]
            nxt = []
            for s, route in states:
                for ch in cands:
                    if not self._feasible(s, row, ch):
                        continue
                    t = s.copy()
                    self._step(t, p, st, row, ch, sp)
                    self._fire_events(t, events.get(st, []))
                    if decks.get(st):
                        t.deck = decks[st]
                    nxt.append((self._J(t), t, {**route, st: ch}))
            if not nxt:
                continue
            groups = collections.defaultdict(list)
            for val, t, route in nxt:
                groups[int(t.v["stamina"])].append((val, t, route))
            states = [(t, route) for g in groups.values() for _, t, route in sorted(g, key=lambda x: -x[0])[:k]]
        ranked = sorted(states, key=lambda x: -self._J(x[0]))
        return ranked if keep_all else ranked[0]

    def forecast(self, p7, p8, supports, memories, ref_supports, ref_memories, beam=BEAM_FULL):
        w = self.w
        v, items, deck = H.start_state(p7, w, 0)
        up, cp = self.start_part(ref_supports, ref_memories), self.start_part(supports, memories)
        for key in ATTRS + ("star", "max_stamina"):
            v[key] = v.get(key, 0) - up.get(key, 0) + cp.get(key, 0)
        v["stamina"] = v["max_stamina"]
        for a in ATTRS:
            v["g_" + a] = v["g_" + a] - up.get("g_" + a, 0) + cp.get("g_" + a, 0)
        summ = p7["summary"]
        sp = {a: (summ.get(f"lesson_{a}_sp_change_rate_permil") or 0) - up.get("sp_" + a, 0) + cp.get("sp_" + a, 0)
              for a in ATTRS}
        s0 = H.State(v, {}, self.abilities_of(supports, memories, items), deck)
        finals7 = self.run_produce(p7, s0, sp, beam[0], True)
        v8_, items8, deck8 = H.start_state(p8, w, 0)
        events = self.support_events(supports)
        abil8 = self.abilities_of(supports, [], items8)
        best = None
        for s7_, r7_ in finals7[:beam[1]]:
            s7 = s7_.copy()
            for a, x in events.items():
                H._add(s7, a, x)
            v8 = dict(v8_)
            for key in ATTRS + ("star",):
                v8[key] = s7.v[key]
            v8["max_stamina"] = v8["stamina"] = s7.v["max_stamina"]
            for a in ATTRS:
                v8["g_" + a] = s7.v["g_" + a]
            counts = {key: n for key, n in s7.counts.items() if key[0].startswith(("support:", "item:"))}
            s8, r8 = self.run_produce(p8, H.State(v8, counts, abil8, deck8), sp, 10, False)
            pre = s8.aud.get("audition_final") or {k: s8.v[k] for k in ATTRS + ("star",)}
            j8 = 2 * H.lsum(min(s8.v["limit"], s8.v[k]) for k in ATTRS) + 7.5 * min(F.STAR_CLAMP, pre["star"])
            if best is None or j8 > best[0]:
                best = (j8, s7, r7_, s8, r8, pre)
        _j8, s7, r7, s8, r8, pre = best
        aud = lambda name: {k: round((s8.aud.get(name) or s8.v)[k]) for k in ATTRS + ("star",)}  # noqa: E731
        return {"selection_end": {k: round(s7.v[k]) for k in ATTRS + ("star",)},
                "final_end": {k: round(s8.v[k]) for k in ATTRS + ("star",)},
                "star_before_final": round(pre["star"]), "growth": {a: v["g_" + a] for a in ATTRS}, "sp": sp,
                "max_stamina": v["max_stamina"], "mid1": aud("audition_mid1"), "final_aud": aud("audition_final"),
                "route_selection": [r7[x] for x in sorted(r7)], "route_final": [r8[x] for x in sorted(r8)]}

    def forecast_many(self, p7, p8, pairs, ref_supports, ref_memories, beam=BEAM_FULL):
        """[forecast(p7, p8, form, mems,...) for (form, mems) in pairs], on the native core when it is loaded
        (produce_native: the same floats), else one by one here."""
        import GakumasCLI.produce.produce_native as PN
        out = PN.forecast_many(self, p7, p8, pairs, ref_supports, ref_memories, beam)
        if out is None:
            out = [self.forecast(p7, p8, list(f), list(m), ref_supports, ref_memories, beam) for f, m in pairs]
        return out


# -- memories and the exam side ---------------------------------------------------------------------------
def mem_card(m):
    pc = m["produce_card"]
    e = {"produce_card_id": pc["id"], "current_upgrade_count": int(pc.get("upgrade_count") or 0)}
    if pc.get("customizes"):
        e["customizes"] = [{"id": x["id"], "customize_count": int(x.get("customize_count") or 1)} for x in pc["customizes"]]
    return e


def _ckey(card):
    return (card.get("produce_card_id") or card.get("id"),
            int(card.get("current_upgrade_count", card.get("upgrade_count", 0)) or 0),
            tuple(sorted(x["id"] for x in card.get("customizes") or [])))


def memory_enchants(mems, model):
    """The H.I.F audition enchants of a memory set (A.HIF_MEMORY_TRIGGER abilities), in memory order, distinct."""
    out = []
    for m in mems:
        for ab in m.get("abilities") or []:
            if A.HIF_MEMORY_TRIGGER not in (ab.get("id") or ""):
                continue
            sk = model.memory_skill(ab["id"], ab.get("level"))
            e = (model.effects.get(sk.effect_id) or {}).get("produceExamStatusEnchantId") if sk is not None else None
            if e and e not in out:
                out.append(e)
    return out


def is_hif_memory(m):
    return any(A.HIF_MEMORY_TRIGGER in (a.get("id") or "") for a in m.get("abilities") or [])


def memory_sig(m, model):
    """What the exam sees of a memory: its card (upgrade, customizes) and its H.I.F enchants."""
    return json.dumps([_ckey(mem_card(m)), memory_enchants([m], model)], sort_keys=True)


def memory_label(m, model):
    pc = m["produce_card"]
    return f"{pc['id']}{'+' if pc.get('upgrade_count') else ''}{'*' if pc.get('customizes') else ''}|" + ",".join(
        e.split("p_card-")[-1][:-6] for e in memory_enchants([m], model))


def with_memories(lo, old, new, model, notes=None):
    """The loadout with the memory cards of `old` taken out of the deck (the same card, else the same card id) and
    those of `new` put in; the H.I.F enchants recomputed from `new`.
    """
    lo = copy.deepcopy(lo)
    if old is not None:
        deck = list(lo["deck_cards"])
        for m in old:
            k = _ckey(mem_card(m))
            i = next((j for j, c in enumerate(deck) if _ckey(c) == k), None)
            if i is None:
                i = next((j for j, c in enumerate(deck) if _ckey(c)[0] == k[0]), None)
            if i is None:
                if notes is not None:
                    notes.append(f"memory card {k[0]} not in the template deck")
                continue
            deck.pop(i)
        deck += [mem_card(m) for m in new]
        lo["deck_cards"] = deck
    lo["lesson_start_enchant_ids"] = memory_enchants(new, model)
    return lo


class Supports:
    """Support card master rows: plan, exam upgrade permil, and the native string table's coverage."""

    def __init__(self, model, master=pe.MASTER_DIR, strings=None):
        self.M = model
        self.rows = {r["id"]: r for r in pe._rows("SupportCard", master)}
        self.strings = set(strings) if strings is not None else None

    def plan(self, cid):
        return pe._name((self.rows.get(cid) or {}).get("planType"))

    def exam_permil(self, cid, level):
        """floor(SupportCard.produceCardUpgradePermil x (1000 + upgrade-probability abilities) / 1000)
        (formation.Planner.card: 12/12 support_state rows)."""
        up = sum((sk.value[0] or 0) for _, sk in self.M.support_skills(cid, level)
                 if sk is not None and sk.effect_type == "SUPPORT_CARD_PRODUCE_CARD_UPGRADE_PROBABILITY_UP")
        return ((self.rows.get(cid) or {}).get("produceCardUpgradePermil") or 0) * (1000 + up) // 1000

    def native_ok(self, cid):
        return self.strings is None or cid in self.strings

    def name(self, cid):
        return (self.rows.get(cid) or {}).get("name") or cid


# -- the idol's context ------------------------------------------------------------------------------------
class Context:
    """One idol card: the reference 選抜 / 本戦 (own, else synthesized), the exam templates, the forecaster."""

    def __init__(self, idol, produces, user, index, master=pe.MASTER_DIR, strings=None):
        self.idol, self.user, self.notes = idol, user or {}, []
        self.world = H.World(master=master, produces=produces)
        self.cards = IdolCards(master)
        if idol not in self.cards.cards:
            raise KeyError(f"{idol}: not in the master's IdolCard")
        self.F = Forecaster(self.world)
        self.model = self.world.M
        self.sup = Supports(self.model, master, strings)
        self.ranks = ranks_of(user, idol)
        self._base(produces)
        self._templates(index)

    def _base(self, produces):
        cards, idol = self.cards, self.idol
        sels = [p for p in produces if p.get("produce_id") == SELECTION and p.get("summary_kind") == "selection_memory"
                and pe.coverage(p) == "full" and p.get("idol_card_id") in cards.cards]
        finals = [p for p in produces if p.get("produce_id") == MAIN and p.get("schedule") and p.get("occasions")]
        full = [p for p in finals if pe.coverage(p) == "full"]
        own = [p for p in sels if p["idol_card_id"] == idol]
        same = [p for p in sels if cards.character(p["idol_card_id"]) == cards.character(idol)]
        plan = [p for p in sels if cards.plan(p["idol_card_id"]) == cards.plan(idol)]
        ref, kind = (_newest(own), "own") if own else (_newest(same), "same_character") if same else \
            (_newest(plan or sels), "approximate")
        if ref is None:
            raise LookupError("no fully recorded H.I.F 選抜 with its End")
        after = [p for p in finals if p.get("selection_memory_id") == (ref["summary"] or {}).get("user_selection_memory_id")]
        mine = [p for p in full if p.get("idol_card_id") == idol]
        p8 = after[0] if (after and kind == "own") else (_newest(mine) or (after[0] if after else _newest(full)))
        if p8 is None:
            raise LookupError("no fully recorded H.I.F 本戦: its schedule is the forecast's")
        self.ref_supports, self.ref_rental, self.ref_memories, self.ref_mem_rental = route_parts(ref)
        self.base_kind, self.ref_uuid, self.final_uuid = kind, ref["uuid"], p8["uuid"]
        self.last_form, self.last_rental, self.last_mems = self.ref_supports, self.ref_rental, self.ref_memories
        self.last_mem_rental = self.ref_mem_rental
        own_any = [p for p in produces if p.get("idol_card_id") == idol and p.get("support_state")]
        if kind != "own" and own_any:
            q = _newest(own_any)
            self.last_form = [(r["support_card_id"], r.get("level") or 0) for r in q["support_state"]]
            self.last_rental = [bool(r.get("is_rental")) for r in q["support_state"]]
            sel = q if q.get("produce_id") == SELECTION and q.get("summary") else self.world.selection_of(q)
            if sel is not None and sel.get("summary"):
                _s, _r, self.last_mems, self.last_mem_rental = route_parts(sel)
            self.notes.append(f"last setup: the formation of {q['uuid'][:8]}")
        self.p7, self.p8 = ref, p8
        self.residual = None
        if kind != "own":
            self.p7, self.residual, d_sp = self.synth(ref)
            self.notes.append(f"Start synthesized from {ref['idol_card_id']} {ref['uuid'][:8]} ({kind}); SP {d_sp:+d}")
        if p8.get("idol_card_id") != idol:
            self.p8 = self._swap_start_item(p8, p8.get("idol_card_id"))
            self.notes.append(f"本戦 schedule of {p8.get('idol_card_id')} {p8['uuid'][:8]}")

    def _state_vec(self, st):
        return {"vocal": st["vocal"], "dance": st["dance"], "visual": st["visual"],
                "g_vocal": st["vocal_growth_rate_permil"], "g_dance": st["dance_growth_rate_permil"],
                "g_visual": st["visual_growth_rate_permil"], "max_stamina": st["max_stamina"]}

    def _parts(self, supports, memories):
        sp = self.F.start_part(supports, memories)
        return {k: sp.get(k, 0) for k in ATTRS + ("g_vocal", "g_dance", "g_visual", "max_stamina")}

    def _swap_start_item(self, p, from_idol):
        q = copy.deepcopy(p)
        ranks_from = ranks_of(self.user, from_idol)
        old = {self.cards.cards[from_idol].get("beforeProduceItemId"), self.cards.cards[from_idol].get("afterProduceItemId"),
               self.cards.start_item(from_idol, ranks_from)}
        new = self.cards.start_item(self.idol, self.ranks)
        o = q["occasions"][0]
        o["items_gained"] = [new if i in old else i for i in o.get("items_gained") or []]
        q["idol_card_id"] = self.idol
        return q

    def synth(self, ref):
        """The reference 選抜 with this idol card's Start: base (master + the user's ranks) + the reference's
        residual (growth panels and the character's own part) + the reference formation's and memories' parts
        (which the forecast swaps out), its start P-item and level-limit SP."""
        keys = ATTRS + ("g_vocal", "g_dance", "g_visual", "max_stamina")
        rec = self._state_vec(ref["occasions"][0]["state"])
        pp = self._parts(self.ref_supports, self.ref_memories)
        ref_idol = ref["idol_card_id"]
        b_ref = self.cards.base(ref_idol, ranks_of(self.user, ref_idol))
        res = {k: rec[k] - b_ref[k] - pp[k] for k in keys}
        b = self.cards.base(self.idol, self.ranks)
        p = self._swap_start_item(ref, ref_idol)
        st = p["occasions"][0]["state"]
        v = {k: b[k] + res[k] + pp[k] for k in keys}
        st["vocal"], st["dance"], st["visual"] = v["vocal"], v["dance"], v["visual"]
        st["vocal_growth_rate_permil"], st["dance_growth_rate_permil"], st["visual_growth_rate_permil"] = \
            v["g_vocal"], v["g_dance"], v["g_visual"]
        st["max_stamina"] = st["stamina"] = v["max_stamina"]
        d_sp = self.cards.sp_bonus(self.idol, self.ranks) - self.cards.sp_bonus(ref_idol, ranks_of(self.user, ref_idol))
        for a in ATTRS:
            k = f"lesson_{a}_sp_change_rate_permil"
            p["summary"][k] = (p["summary"].get(k) or 0) + d_sp
        return p, res, d_sp

    def _templates(self, index):
        self.templates, cat = {}, self.world.cat
        same_plan = sorted({i for i, _r in index if i in self.cards.cards and self.cards.plan(i) == self.cards.plan(self.idol)})
        chara = self.cards.character(self.idol)
        for rnd in ROUNDS:
            t, kind = A.pick_template(index, self.idol, rnd), "own"
            if t is None:
                rows = [x for i in same_plan for x in index.get((i, rnd), ())]
                key = lambda x: (self.cards.character(x["loadout"]["idol_card_id"]) == chara,  # noqa: E731
                                 self._template_memories(x["loadout"]["idol_card_id"], x["run"]) is not None,
                                 x["score"] is not None, x["run"])
                t = max(rows, key=key) if rows else None
                kind = "borrowed"
            if t is None:
                self.templates[rnd] = None
                self.notes.append(f"no recorded 本戦 {rnd} loadout of this plan: the exam terms are left out")
                continue
            lo = copy.deepcopy(t["loadout"])
            lo.pop("seed", None)
            src_idol = lo["idol_card_id"]
            mems = self._template_memories(src_idol, t["run"])
            if kind == "borrowed":
                lo = self._convert(lo, src_idol, cat)
                self.notes.append(f"{rnd} template borrowed from {src_idol} ({t['run']})")
            if mems is None:
                self.notes.append(f"{rnd} template {t['run']}: its 選抜 is not recorded, the deck's memory cards stay")
            self.templates[rnd] = {"loadout": lo, "memories": mems, "run": t["run"], "kind": kind, "idol": src_idol,
                                   "score": t.get("score")}

    def _template_memories(self, idol, run):
        """The memories of the produce a template audition belongs to: its 選抜 (by the 選抜 memory the 本戦 names,
        else the card's newest 選抜 with an End recorded no later than the 本戦's first run)."""
        for p in self.world.produces:
            if p.get("produce_id") == MAIN and p.get("idol_card_id") == idol and run in (p.get("runs") or []):
                sel = self.world.selection_of(p)
                if sel is None:
                    first = min(p.get("runs") or [run])
                    sel = _newest([q for q in self.world.produces if q.get("produce_id") == SELECTION
                                   and q.get("idol_card_id") == idol and q.get("summary")
                                   and max(q.get("runs") or [""]) <= first])
                if sel is not None:
                    return route_parts(sel)[2]
        return None

    def _convert(self, lo, src, cat):
        """Another idol's audition loadout as this idol's: its unique cards and start P-item swapped, the score
        bonus moved to this idol's audition config by the base-permil ratio (label kit record_loadout)."""
        fc, tc = self.cards.cards[src], self.cards.cards[self.idol]
        swap = {fc.get(k): tc.get(k) for k in ("produceCardId", "secondProduceCardId") if fc.get(k) and tc.get(k)}
        for c in lo["deck_cards"]:
            if c.get("produce_card_id") in swap:
                c["produce_card_id"] = swap[c["produce_card_id"]]
        old_items = {fc.get("beforeProduceItemId"), fc.get("afterProduceItemId")}
        new_item = self.cards.start_item(self.idol, self.ranks)
        lo["produce_item_ids"] = [new_item if i in old_items else i for i in lo.get("produce_item_ids") or []]
        prm = {int(k): int(v) for k, v in lo["parameters"].items()}
        try:
            old = cat.base_permils(A._battle_config(cat, src, lo["round"]), prm)
            new = cat.base_permils(A._battle_config(cat, self.idol, lo["round"]), prm)
            lo["score_bonus_permils"] = {k: int(math.ceil(int(v) * new[int(k)] / old[int(k)] / 10.0)) * 10
                                         for k, v in lo["score_bonus_permils"].items()}
        except Exception as exc:     # noqa: BLE001
            self.notes.append(f"score bonus not moved: {type(exc).__name__}")
        lo["idol_card_id"] = self.idol
        return lo

    def forecast(self, form, mems, fast=False):
        return self.forecast_many([(form, mems)], fast)[0]

    def forecast_many(self, pairs, fast=False):
        """forecast for several (formation, memories) pairs in one call."""
        return self.F.forecast_many(self.p7, self.p8, [(list(f), list(m)) for f, m in pairs], self.ref_supports,
                                    self.ref_memories, BEAM_FAST if fast else BEAM_FULL)

    def loadout(self, rnd, form, mems, prm):
        t = self.templates[rnd]
        lo = with_memories(t["loadout"], t["memories"], mems, self.model)
        A.rescale(lo, prm, self.world.cat)
        lo["parameters"] = dict(prm)
        lo["support_cards"] = [{"support_card_id": c, "permil": self.sup.exam_permil(c, lv)} for c, lv in form]
        return lo

    def loadouts(self, form, mems, f):
        out = {}
        for rnd, key in (("mid1", "mid1"), ("final", "final_aud")):
            if self.templates.get(rnd):
                out[rnd] = self.loadout(rnd, form, mems, {"1": int(f[key]["vocal"]), "2": int(f[key]["dance"]),
                                                          "3": int(f[key]["visual"])})
        return out

    def seeds(self, rnd, n):
        return exam_seeds("start|" + self.idol, rnd, n)

    @property
    def last_setup(self):
        return list(self.last_form), list(self.last_mems)


# -- the pool ------------------------------------------------------------------------------------------------
def pool(ctx, user, rentals=None, min_level=MIN_LEVEL):
    """Own support cards (best level per card, level >= min_level), the rental list, own H.I.F memories of the
    plan (one per distinct content); support cards of the idol's plan or common only, and only cards the native
    core's string table knows (a loadout with another one leaves the native search)."""
    plan = ctx.cards.plan(ctx.idol)
    ok = lambda cid: ctx.sup.plan(cid) in (PLAN_PREFIX + "COMMON", plan) and ctx.sup.native_ok(cid)  # noqa: E731
    own, skipped = {}, set()
    for r in (user or {}).get("user_support_card_list") or []:
        cid, lv = r.get("support_card_id"), int(r.get("level") or 1)
        if not cid or lv < min_level:
            continue
        if not ok(cid):
            if not ctx.sup.native_ok(cid):
                skipped.add(cid)
            continue
        if lv > own.get(cid, (0, 0))[0]:
            own[cid] = (lv, int(r.get("level_limit_rank") or 0))
    rent = {}
    for key in ("support_cards", "suggestion_support_cards", "npc_support_cards"):
        for r in (rentals or {}).get(key) or []:
            cid, lv = r.get("support_card_id"), int(r.get("level") or 1)
            if cid and ok(cid) and (cid, lv) not in rent:
                rent[(cid, lv)] = {"limit_rank": int(r.get("level_limit_rank") or 0), "list": key}
    prefix = "p_card-%02d-" % (ctx.cards.plan_number(ctx.idol) - 1)
    mems, seen = [], set()
    for m in (user or {}).get("user_memory_list") or []:
        if not is_hif_memory(m) or not (m.get("produce_card") or {}).get("id", "").startswith((prefix, "p_card-00-")):
            continue
        sig = memory_sig(m, ctx.model)
        if sig in seen:
            continue
        seen.add(sig)
        mems.append({k: v for k, v in m.items() if k not in ("image_path", "rental_public_user_id")})
    return {"own": sorted((c, lv) for c, (lv, _r) in own.items()),
            "own_limit": {c: r for c, (_lv, r) in own.items()},
            "rentals": sorted(rent), "rental_info": {f"{c}|{lv}": v for (c, lv), v in rent.items()},
            "memories": mems, "not_native": sorted(skipped)}


# -- strong search jobs ----------------------------------------------------------------------------------------
def job(tag, rnd, lo, seed, shape=SHAPE):
    """A produce_score job (hif_route_search worker / run_job): one audition, one exam seed, the search shape;
    `split` asks for the per-parameter scores."""
    return {"kind": "produce_score", "tag": tag, "round": rnd, "loadout": lo, "exam_seed": int(seed),
            "budget": shape["budget"], "search_seed": shape["search_seed"], "levels": shape["levels"],
            "iterations": shape["iterations"], "stabilization": shape["stabilization"], "split": True}


def result_row(jb, outcome):
    """A pool worker's outcome (hif_route_search produce_score: status, score, split, native) as the row Prepare
    takes; None for a failed job (it stays in the next jobs())."""
    if not outcome or outcome.get("status") != "ok" or outcome.get("split") is None:
        return None
    return {"tag": jb["tag"], "round": jb["round"], "exam_seed": jb["exam_seed"], "score": int(outcome["score"]),
            "split": list(outcome["split"]), "native": bool(outcome.get("native")),
            "params": jb["loadout"]["parameters"], "bonus": jb["loadout"]["score_bonus_permils"],
            "seconds": outcome.get("seconds")}


def worker_init(lib=None):
    import os
    import GakumasCLI.produce.produce_value as PV
    PV._init(lib or os.environ.get("GKMS_NATIVE_LIB") or str(PV.G / "native" / "build" / "gkmscore.dll"))


def run_job(jb):
    """One job in this process (worker_init first): the native search, the Python search when the core refuses
    the state (native False on the row)."""
    import GakumasCLI.produce.produce_value as PV
    W = PV._W
    s, _ = W["build"](W["master"], jb["loadout"], seed=int(jb["exam_seed"]))
    kw = dict(deadline=float(jb["budget"]), seed=int(jb["search_seed"]), levels=int(jb["levels"]),
              iterations=int(jb["iterations"]), stabilization=int(jb["stabilization"]))
    t0 = time.perf_counter()
    native = True
    try:
        path, st = W["ns"].policy_search(s, **kw)
        ok = st["status"] == 0 and path
    except Exception:          # noqa: BLE001
        ok = False
    if ok:
        x = W["sv"].replay_session(s, path).state
    else:
        native = False
        best, _path, _st = W["py"](s, **kw)
        x = best.state
    return {"tag": jb["tag"], "round": jb["round"], "exam_seed": jb["exam_seed"], "score": int(x.score),
            "split": [int(x.score_vocal), int(x.score_dance), int(x.score_visual)], "native": native,
            "params": jb["loadout"]["parameters"], "bonus": jb["loadout"]["score_bonus_permils"],
            "seconds": round(time.perf_counter() - t0, 1)}


def _paired(rows):
    out = collections.defaultdict(dict)
    for o in rows:
        out[(o["tag"], o["round"])][o["exam_seed"]] = o["score"]
    return out


def _gain(sc, a, b, rounds):
    """Per-seed rating points of `a` minus `b` (paired seeds, both rounds, seed lists zipped in order)."""
    per = []
    lists = [sorted(set(sc[(a, r)]) & set(sc[(b, r)])) for r in rounds]
    for seeds in zip(*lists):
        per.append(sum(points(r, sc[(a, r)][s]) - points(r, sc[(b, r)][s]) for r, s in zip(rounds, seeds)))
    if len(per) < 2:
        return (per[0] if per else 0.0), None
    return statistics.mean(per), 1.96 * statistics.stdev(per) / math.sqrt(len(per))


class Prepare:
    """The prepare stages as job batches: first -> memories screen -> confirm -> split."""

    STAGES = ("first", "screen", "confirm", "split")

    def __init__(self, ctx, pl, cache=None):
        self.ctx, self.pool = ctx, pl
        self.cache = cache or {"idol": ctx.idol, "stage": "first", "rows": {s: [] for s in self.STAGES}}
        for s in self.STAGES:
            self.cache["rows"].setdefault(s, [])
        self.rounds = [r for r in ROUNDS if ctx.templates.get(r)]
        sup, mems = ctx.last_setup
        self.base_mems, self.base_form = mems, sup
        self.f_base = ctx.forecast(sup, mems)
        self.variants = self._variants()
        for name in self.cache.get("dropped") or ():
            self.variants.pop(name, None)
        self.error, self._f_split = None, None
        if cache is None:
            lv = {r: ctx.templates[r]["score"] for r in self.rounds}
            if len(lv) == 2 and all(v is not None and v >= CAP[r] for r, v in lv.items()):
                self.cache.update(skip_screen=True, screen=[], confirm=[], confirm_names=[], memories=self.base_mems)
                ctx.notes.append("both recorded scores above the caps: memory screening skipped")
            elif any(ctx.templates[r]["memories"] is None for r in self.rounds):
                self.cache.update(skip_screen=True, screen=[], confirm=[], confirm_names=[], memories=self.base_mems)
                ctx.notes.append("template memories unknown: memory screening skipped")

    def _variants(self):
        ctx, mems = self.ctx, self.base_mems
        base_ids = {m.get("user_memory_id") for m in mems}
        base_sigs = {memory_sig(m, ctx.model) for m in mems}
        out = {"base": mems}
        for i, rental in enumerate(ctx.last_mem_rental):
            if rental:
                continue
            for c in self.pool["memories"]:
                if c.get("user_memory_id") in base_ids or memory_sig(c, ctx.model) in base_sigs:
                    continue
                out[f"slot{i}:{memory_label(mems[i], ctx.model)} -> {memory_label(c, ctx.model)}|{c['user_memory_id']}"] = \
                    mems[:i] + [c] + mems[i + 1:]
        return out

    @property
    def stage(self):
        return self.cache["stage"]

    @property
    def done(self):
        return self.stage == "done"

    def _jobs_for(self, names, n, mems_of, stage, f=None):
        have = {(o["tag"], o["round"], o["exam_seed"]) for o in self.cache["rows"][stage]}
        dropped = set(self.cache.get("dropped") or ())
        out = []
        for name in names:
            if name in dropped:
                continue
            los = self.ctx.loadouts(self.base_form, mems_of(name), f or self.f_base)
            for rnd in self.rounds:
                for s in self.ctx.seeds(rnd, n):
                    if (name, rnd, s) not in have:
                        out.append(job(name, rnd, los[rnd], s))
        return out

    def jobs(self):
        st = self.stage
        if st == "first":
            return self._jobs_for(["base"], SPLIT_SEEDS, lambda _n: self.base_mems, st)
        if st == "screen":
            return self._jobs_for(list(self.variants), SCREEN_SEEDS, self.variants.get, st)
        if st == "confirm":
            return self._jobs_for(["base"] + self.cache["confirm_names"], CONFIRM_SEEDS, self.variants.get, st)
        if st == "split":
            if self._f_split is None:
                self._f_split = self.ctx.forecast(self.base_form, self.chosen())
            return self._jobs_for(["split"], SPLIT_SEEDS, lambda _n: self.chosen(), st, self._f_split)
        return []

    def absorb(self, results):
        for o in results:
            if o is None:
                continue
            if not o.get("native", True):
                if self.stage in ("screen", "confirm") and o.get("tag") not in ("base", "split"):
                    dropped = self.cache.setdefault("dropped", [])
                    if o.get("tag") not in dropped:
                        dropped.append(o.get("tag"))
                    self.variants.pop(o.get("tag"), None)
                    for stage in ("screen", "confirm"):
                        self.cache["rows"][stage] = [x for x in self.cache["rows"][stage] if x["tag"] != o.get("tag")]
                    continue
                self.error = f"Python search in {o.get('tag')} {o.get('round')}: the native core did not load"
                return False
            self.cache["rows"][self.stage].append(o)
        while not self.done and not self.jobs():
            self._advance()
        return True

    def _surrogate(self, rows, base):
        ctx = self.ctx
        return {"rows": [{k: o[k] for k in ("round", "exam_seed", "score", "split", "params", "bonus")} for o in rows],
                "base_mean": base,
                "level": {r: ctx.templates[r]["score"] for r in self.rounds},
                "template": {r: {k: ctx.templates[r][k] for k in ("run", "kind", "idol")} for r in self.rounds}}

    def _finish(self, rows, base):
        ctx = self.ctx
        self.cache["surrogate"] = self._surrogate(rows, base)
        self.cache.update(stage="done", usable=True, base_kind=ctx.base_kind, ref=ctx.ref_uuid,
                          final_ref=ctx.final_uuid, notes=list(ctx.notes), shape=SHAPE,
                          made=time.strftime("%Y-%m-%d %H:%M:%S"))

    def _advance(self):
        st, ctx = self.stage, self.ctx
        if st == "first":
            rows = self.cache["rows"]["first"]
            base = {r: statistics.mean(o["score"] for o in rows if o["round"] == r) for r in self.rounds
                    if any(o["round"] == r for o in rows)}
            if self.cache.get("skip_screen"):
                return self._finish(rows, base)
            self.cache["surrogate"] = self._surrogate(rows, base)
            self.cache.update(usable=True, memories=self.base_mems, base_kind=ctx.base_kind, ref=ctx.ref_uuid,
                              final_ref=ctx.final_uuid, notes=list(ctx.notes), shape=SHAPE,
                              made=time.strftime("%Y-%m-%d %H:%M:%S"))
            for stage, n in (("screen", SCREEN_SEEDS), ("confirm", CONFIRM_SEEDS)):
                keep = {(r, s) for r in self.rounds for s in ctx.seeds(r, n)}
                have = {(o["tag"], o["round"], o["exam_seed"]) for o in self.cache["rows"][stage]}
                self.cache["rows"][stage] += [dict(o, tag="base") for o in rows if (o["round"], o["exam_seed"]) in keep
                                              and ("base", o["round"], o["exam_seed"]) not in have]
            self.cache["stage"] = "screen"
        elif st == "screen":
            sc = _paired(self.cache["rows"]["screen"])
            gains = sorted(((_gain(sc, n, "base", self.rounds)[0], n) for n in self.variants if n != "base"), reverse=True)
            self.cache["screen"] = [{"gain": round(g, 1), "variant": n} for g, n in gains]
            self.cache["confirm_names"] = [n for g, n in gains[:CONFIRM_TOP] if g > 0]
            self.cache["stage"] = "confirm"
        elif st == "confirm":
            sc = _paired(self.cache["rows"]["confirm"])
            conf = []
            for n in self.cache["confirm_names"]:
                g, ci = _gain(sc, n, "base", self.rounds)
                conf.append({"gain": round(g, 1), "ci": round(ci or 0, 1), "variant": n})
            conf.sort(key=lambda r: -r["gain"])
            chosen, used_slots, used_ids = list(self.base_mems), set(), set()
            for r in conf:
                slot, mid = int(r["variant"].split(":")[0][4:]), r["variant"].rsplit("|", 1)[1]
                if r["gain"] - r["ci"] <= 0 or slot in used_slots or mid in used_ids:
                    continue
                chosen[slot] = next(m for m in self.pool["memories"] if m.get("user_memory_id") == mid)
                used_slots.add(slot)
                used_ids.add(mid)
            self.cache["confirm"] = conf
            self.cache["memories"] = chosen
            self.cache["stage"] = "split"
            first = self.cache["rows"]["first"]
            if first and [m.get("user_memory_id") for m in chosen] == [m.get("user_memory_id") for m in self.base_mems]:
                self._finish(first, self._confirm_base())
        elif st == "split":
            base = self._confirm_base()
            if not base:
                rows = self.cache["rows"]["split"]
                base = {r: statistics.mean(o["score"] for o in rows if o["round"] == r) for r in self.rounds
                        if any(o["round"] == r for o in rows)}
            self._finish(self.cache["rows"]["split"], base)

    def _confirm_base(self):
        sc = _paired(self.cache["rows"]["confirm"])
        return {r: statistics.mean(sc[("base", r)].values()) for r in self.rounds if sc[("base", r)]}

    def chosen(self):
        return self.cache.get("memories") or self.base_mems


def estimate_cache(ctx):
    """a cache with no search behind it, for an idol card not prepared yet: each template audition's live score split
    over Vo / Da / Vi by its score-bonus permils (the share a turn type's cards score at), at the template's own
    parameters, the last setup's memories.
    """
    rows = []
    for rnd in ROUNDS:
        t = ctx.templates.get(rnd)
        if not t or not t.get("score"):
            continue
        lo = t["loadout"]
        bonus = {k: int(v) for k, v in lo["score_bonus_permils"].items()}
        total = sum(bonus[str(i + 1)] for i in range(3)) or 1
        rows.append({"round": rnd, "exam_seed": 0, "score": int(t["score"]),
                     "split": [int(t["score"]) * bonus[str(i + 1)] / total for i in range(3)],
                     "params": dict(lo["parameters"]), "bonus": bonus})
    if not rows:
        raise LookupError("no recorded 本戦 audition score of this plan to estimate from")
    return {"idol": ctx.idol, "stage": "estimate", "usable": True, "estimate": True,
            "memories": ctx.last_setup[1], "base_kind": ctx.base_kind, "notes": list(ctx.notes),
            "surrogate": {"rows": rows, "base_mean": None, "level": None,
                          "template": {r: {k: ctx.templates[r][k] for k in ("run", "kind", "idol")}
                                       for r in ROUNDS if ctx.templates.get(r)}},
            "made": time.strftime("%Y-%m-%d %H:%M:%S")}


# -- the surrogate and the recommendation ------------------------------------------------------------------------
class Surrogate:
    """The two audition scores of a setup from its parameters: each split seed's Vo / Da / Vi score moved by the
    score-bonus permils of the new parameters (A.rescale), scaled to the recorded level (the template's live score
    over the base setup's mean search score; search scores run below live ones), averaged over the seeds in rating
    points."""

    def __init__(self, ctx, data, level=True):
        self.ctx, self.rows = ctx, collections.defaultdict(list)
        for o in data["rows"]:
            self.rows[o["round"]].append(o)
        self.scale = {}
        for rnd in self.rows:
            lv, bm = (data.get("level") or {}).get(rnd), (data.get("base_mean") or {}).get(rnd)
            self.scale[rnd] = (lv / bm) if (level and lv and bm) else None
        known = [v for v in self.scale.values() if v]
        for rnd in self.scale:
            if self.scale[rnd] is None:
                self.scale[rnd] = statistics.mean(known) if known else 1.0

    def predict(self, rnd, prm):
        """(mean rating points, mean predicted score) of round `rnd` at parameters {"1","2","3"}."""
        if rnd not in self.rows:
            return 0.0, None
        pts, scs = [], []
        for o in self.rows[rnd]:
            lo = {"idol_card_id": self.ctx.idol, "round": rnd, "parameters": dict(o["params"]),
                  "score_bonus_permils": dict(o["bonus"])}
            A.rescale(lo, prm, self.ctx.world.cat)
            s = self.scale[rnd] * sum(o["split"][i] * lo["score_bonus_permils"][str(i + 1)] / o["bonus"][str(i + 1)]
                                      for i in range(3))
            scs.append(s)
            pts.append(points(rnd, s))
        return statistics.mean(pts), statistics.mean(scs)

    def capped(self):
        """Both recorded levels above their caps (the setups compete on parameters and star only)."""
        lv = {r: self.scale[r] * statistics.mean(o["score"] for o in self.rows[r]) for r in self.rows}
        return len(lv) == 2 and all(lv[r] >= CAP[r] for r in lv)


def evaluate(ctx, sur, form, mems, fast=True, cache=None):
    return evaluate_many(ctx, sur, [(form, mems)], fast, cache)[0]


def evaluate_many(ctx, sur, pairs, fast=True, cache=None):
    """evaluate for several (formation, memories) pairs; the ones not in `cache` in one forecast call."""
    keys = [(tuple(sorted(form)), tuple(sorted(memory_sig(m, ctx.model) for m in mems)), fast) for form, mems in pairs]
    memo = cache if cache is not None else {}
    todo, seen = [], set()
    for i, k in enumerate(keys):
        if k not in memo and k not in seen:
            todo.append(i)
            seen.add(k)
    if todo:
        for i, f in zip(todo, ctx.forecast_many([pairs[i] for i in todo], fast)):
            memo[keys[i]] = _evaluated(sur, f)
    return [memo[k] for k in keys]


def _evaluated(sur, f):
    prm = lambda a: {"1": int(a["vocal"]), "2": int(a["dance"]), "3": int(a["visual"])}  # noqa: E731
    r1, s1 = sur.predict("mid1", prm(f["mid1"]))
    r2, s2 = sur.predict("final", prm(f["final_aud"]))
    v = param_part(f["final_end"], f["star_before_final"]) + r1 + r2 - 2000
    out = (v, {"final_end": f["final_end"], "star": f["star_before_final"], "mid1": s1, "final": s2,
               "param_part": param_part(f["final_end"], f["star_before_final"]), "r1": r1, "r2": r2,
               "selection_end": f["selection_end"], "route_selection": f["route_selection"],
               "route_final": f["route_final"]})
    return out


class Recommender:
    """Coordinate ascent over one idol's pool: 5 own slots then the rental slot, then (when both recorded scores sit
    above the caps) the own memory slots; fast beam while climbing, full beam for the finalists."""

    def __init__(self, ctx, pl, cache_data, level=True):
        self.ctx, self.pl = ctx, pl
        self.sur = Surrogate(ctx, cache_data["surrogate"], level)
        self.capped = self.sur.capped()
        self.mems0 = cache_data.get("memories") or ctx.last_setup[1]
        self.own, self.rentals = [tuple(x) for x in pl["own"]], [tuple(x) for x in pl["rentals"]]
        self.mem_slots = [i for i, r in enumerate(ctx.last_mem_rental) if not r] if self.capped else []
        self.memo = {}

    def value(self, form, mems, fast=True):
        return evaluate(self.ctx, self.sur, form, mems, fast, self.memo)

    def values(self, pairs, fast=True):
        return evaluate_many(self.ctx, self.sur, pairs, fast, self.memo)

    def starts(self, quick=False, restarts=RESTARTS, seed=23, extra=()):
        ctx = self.ctx
        last = [x for x, r in zip(ctx.last_form, ctx.last_rental) if not r] + \
               [x for x, r in zip(ctx.last_form, ctx.last_rental) if r]
        out = [("last", [tuple(x) for x in last])] + [(f"start{k}", [tuple(x) for x in s]) for k, s in enumerate(extra)]
        rng = random.Random(seed)
        if len(self.own) >= 5 and self.rentals:
            out += [(f"rand{k}", rng.sample(self.own, 5) + [rng.choice(self.rentals)])
                    for k in range(0 if quick else restarts)]
        return [(n, s0) for n, s0 in out if len(s0) == 6 and self.rentals]

    def ascend(self, s0):
        """each slot's candidates are valued in one call, then taken in list order when above the best so far -- the same
        climb as one by one: a candidate replaces only this slot, so its formation does not depend on an earlier
        candidate of the slot being taken (the old one, skipped here, is below the best by then)."""
        sig = lambda m: memory_sig(m, self.ctx.model)  # noqa: E731
        form, mems = list(s0), list(self.mems0)
        if form[5] not in self.rentals:
            vals = self.values([(form[:5] + [c], mems) for c in self.rentals])
            form = form[:5] + [max(zip(self.rentals, vals), key=lambda cv: cv[1][0])[0]]
        best = self.value(form, mems)[0]
        for _ in range(3):
            moved = False
            for i in range(6):
                ids = {x[0] for j, x in enumerate(form) if j != i}
                ts = [form[:i] + [c] + form[i + 1:] for c in (self.rentals if i == 5 else self.own)
                      if not (c[0] in ids or c == form[i])]
                for t, (v, _d) in zip(ts, self.values([(t, mems) for t in ts])):
                    if v > best + 1e-6:
                        best, form, moved = v, t, True
            for i in self.mem_slots:
                used = {sig(m) for j, m in enumerate(mems) if j != i}
                tms = [mems[:i] + [m] + mems[i + 1:] for m in self.pl["memories"] if sig(m) not in used]
                for t, (v, _d) in zip(tms, self.values([(form, t) for t in tms])):
                    if v > best + 1e-6:
                        best, mems, moved = v, t, True
            if not moved:
                break
        return form, mems

    def finish(self, climbed, top=3, current=None):
        ctx, found = self.ctx, {}
        self.values([(f, m) for _n, (f, m) in climbed] + [(s[0], s[1]) for s in (ctx.last_setup, current) if s], False)
        for name, (form, mems) in climbed:
            key = (tuple(sorted(map(tuple, form))), tuple(sorted(memory_sig(m, ctx.model) for m in mems)))
            found.setdefault(key, {"formation": [tuple(x) for x in form], "memories": mems, "starts": []})["starts"].append(name)
        rows = []
        for r in found.values():
            v, d = self.value(r["formation"], r["memories"], False)
            rows.append({**r, "rating": v, **d})
        rows.sort(key=lambda r: -r["rating"])
        ref = {}
        for name, setup in (("last", ctx.last_setup), ("current", current)):
            if setup:
                v, d = self.value(setup[0], setup[1], False)
                ref[name] = {"formation": setup[0], "memories": setup[1], "rating": v, **d}
        rows = [r for r in rows if "last" not in ref or r["rating"] >= ref["last"]["rating"]]
        return {"top": rows[:top], "reference": ref, "capped": self.capped, "evaluations": len(self.memo),
                "scale": self.sur.scale, "notes": list(ctx.notes)}


def recommend(ctx, pl, cache_data, *, quick=False, restarts=RESTARTS, top=3, seed=23, current=None, extra_starts=(),
              level=True, map_fn=None):
    """Top setups: [{formation [(id, level)] (5 own then the rental), memories, rating, the forecast...}], plus
    the user's last setup and `current` (formation, memories) evaluated the same way.
    """
    rec = Recommender(ctx, pl, cache_data, level)
    starts = rec.starts(quick, restarts, seed, extra_starts)
    if map_fn is None:
        climbed = [(n, rec.ascend(s0)) for n, s0 in starts]
    else:
        climbed = list(map_fn(_ascend_start, starts))
    return rec.finish(climbed, top, current)


_RW = {}


def recommend_worker_init(spec):
    """A restart worker: the same Context, pool and cache as the caller's (spec: idol, produces, user, index,
    rentals, cache, strings, level)."""
    ctx = Context(spec["idol"], spec["produces"], spec["user"], spec["index"], strings=spec.get("strings"))
    _RW["rec"] = Recommender(ctx, pool(ctx, spec["user"], spec.get("rentals")), spec["cache"], spec.get("level", True))


def _ascend_start(start):
    name, s0 = start
    return name, _RW["rec"].ascend(s0)


# -- the start advice's idol list --------------------------------------------------------------------
_OW = {}


def overview_summary(res, kind, base_kind):
    """One idol card's line in the list from a recommend() result: the best setup (else the last one)."""
    top = res["top"][0] if res["top"] else res["reference"]["last"]
    return {"kind": kind, "base_kind": base_kind, "rating": top["rating"],
            "last": res["reference"]["last"]["rating"], "capped": res["capped"],
            "formation": [list(x) for x in top["formation"]],
            "memories": [m.get("user_memory_id") for m in top["memories"]],
            "params": sum(top["final_end"][k] for k in ATTRS), "star": top["star"],
            "mid1": top.get("mid1"), "final": top.get("final")}


def overview_worker_init(spec):
    """A list worker: what every idol card shares (spec: produces, user, index, rentals, strings)."""
    _OW.clear()
    _OW.update(spec)


def overview_one(job):
    """One idol card's quick climb (job: idol, cache or None for the estimate, kind) -> overview_summary + idol,
    or {idol, error}."""
    idol = job["idol"]
    try:
        ctx = Context(idol, _OW["produces"], _OW["user"], _OW["index"], strings=_OW.get("strings"))
        cache, kind = job.get("cache"), job.get("kind")
        if cache is None:
            cache, kind = estimate_cache(ctx), "estimate"
        pl = pool(ctx, _OW["user"], _OW.get("rentals"))
        t0 = time.perf_counter()
        res = recommend(ctx, pl, cache, quick=True, extra_starts=[r["formation"] for r in cache.get("formations") or []])
        out = overview_summary(res, kind, ctx.base_kind)
        out.update(idol=idol, seconds=round(time.perf_counter() - t0, 1))
        return out
    except LookupError as exc:
        return {"idol": idol, "error": str(exc)}
    except Exception as exc:     # noqa: BLE001
        return {"idol": idol, "error": f"{type(exc).__name__}: {exc}"}


# -- verification: the recommended setups searched like the cache was --------------------------------------------
def verify_jobs(ctx, setups, n=CONFIRM_SEEDS):
    """Strong-search jobs for {name: (formation, memories)} on the same exam seeds (the full forecast's
    parameters); returns (jobs, info)."""
    jobs, info = [], {}
    for name, (form, mems) in setups.items():
        f = ctx.forecast(form, mems)
        info[name] = {"formation": [tuple(x) for x in form], "memories": [m.get("user_memory_id") for m in mems],
                      "final_end": f["final_end"], "star": f["star_before_final"]}
        for rnd, lo in ctx.loadouts(form, mems, f).items():
            jobs += [job(name, rnd, lo, s) for s in ctx.seeds(rnd, n)]
    return jobs, info


def verify_summary(info, rows, scale=None, base="last"):
    """Per setup: mean audition scores, expected rating (each seed's score at the recorded level, as the
    surrogate does), and the difference from `base` with the 95 % interval of its audition part."""
    scale = scale or {}
    sc = _paired(rows)
    out = {}
    for name, d in info.items():
        rounds = [r for r in ROUNDS if sc[(name, r)]]
        pp = param_part(d["final_end"], d["star"]) - 2000
        pts = {r: {s: points(r, scale.get(r, 1.0) * v) for s, v in sc[(name, r)].items()} for r in rounds}
        out[name] = {**d, "rating": pp + sum(statistics.mean(pts[r].values()) for r in rounds),
                     "pts": pts, "param_part": pp,
                     **{r: statistics.mean(sc[(name, r)].values()) for r in rounds}}
    b = out.get(base)
    for name, o in out.items():
        if b is None or name == base:
            continue
        lists = [sorted(o["pts"][r]) for r in o["pts"]]
        per = [sum(o["pts"][r][s] - b["pts"][r][s] for r, s in zip(o["pts"], seeds)) for seeds in zip(*lists)]
        o["delta"] = o["rating"] - b["rating"]
        o["ci"] = 1.96 * statistics.stdev(per) / math.sqrt(len(per)) if len(per) > 1 else None
    for o in out.values():
        o.pop("pts")
    return out


# -- command line --------------------------------------------------------------------------------------------------
def _load_produces(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def _strings():
    p = Path(pe.MASTER_DIR).resolve().parents[2] / "data" / "strings.json"
    if not p.exists():
        return None
    s = json.load(open(p, encoding="utf-8"))
    return s if isinstance(s, list) else list(s)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prepare")
    for x in ("act", "loadout_root", "user", "idol", "cache"):
        a.add_argument(x)
    a.add_argument("--workers", type=int, default=2)
    a.add_argument("--lib")
    b = sub.add_parser("recommend")
    for x in ("act", "loadout_root", "user", "rentals", "cache", "out"):
        b.add_argument(x)
    b.add_argument("--quick", action="store_true")
    b.add_argument("--workers", type=int, default=1)
    c = sub.add_parser("verify")
    for x in ("act", "loadout_root", "user", "cache", "recommended", "out"):
        c.add_argument(x)
    c.add_argument("--top", type=int, default=2)
    c.add_argument("--workers", type=int, default=2)
    c.add_argument("--lib")
    x = ap.parse_args(argv)
    produces = _load_produces(x.act)
    user = json.load(open(x.user, encoding="utf-8"))
    index = A.templates(x.loadout_root)
    if x.cmd == "prepare":
        from multiprocessing import Pool
        ctx = Context(x.idol, produces, user, index, strings=_strings())
        pl = pool(ctx, user)
        cache = json.load(open(x.cache, encoding="utf-8")) if Path(x.cache).exists() else None
        prep = Prepare(ctx, pl, cache)
        print("base", ctx.base_kind, ctx.ref_uuid[:8], ctx.final_uuid[:8], "variants", len(prep.variants),
              "notes", ctx.notes, flush=True)
        with Pool(x.workers, initializer=worker_init, initargs=(x.lib,)) as p:
            while not prep.done:
                jobs = prep.jobs()
                print(prep.stage, len(jobs), "searches", flush=True)
                got = []
                for o in p.imap_unordered(run_job, jobs):
                    got.append(o)
                    if len(got) == 20:
                        if not prep.absorb(got):
                            raise SystemExit(prep.error)
                        got = []
                        json.dump(prep.cache, open(x.cache, "w", encoding="utf-8"), ensure_ascii=False)
                if not prep.absorb(got):
                    raise SystemExit(prep.error)
                json.dump(prep.cache, open(x.cache, "w", encoding="utf-8"), ensure_ascii=False)
        for r in prep.cache["confirm"]:
            print(f"confirm {r['gain']:+8.0f} +- {r['ci']:5.0f}  {r['variant']}")
        return
    cache = json.load(open(x.cache, encoding="utf-8"))
    if x.cmd == "verify":
        from multiprocessing import Pool
        ctx = Context(cache["idol"], produces, user, index, strings=_strings())
        rec = json.load(open(x.recommended, encoding="utf-8"))
        setups = {"last": ctx.last_setup, "last+memories": (ctx.last_setup[0], cache.get("memories") or ctx.last_setup[1])}
        for k, r in enumerate(rec["top"][:x.top]):
            setups[f"rec{k}"] = ([tuple(c) for c in r["formation"]], r["memories"])
        jobs, info = verify_jobs(ctx, setups)
        done = Path(x.out + ".rows.jsonl")
        rows = [json.loads(line) for line in open(done, encoding="utf-8")] if done.exists() else []
        have = {(o["tag"], o["round"], o["exam_seed"]) for o in rows}
        todo = [j for j in jobs if (j["tag"], j["round"], j["exam_seed"]) not in have]
        print(len(todo), "searches", flush=True)
        with Pool(x.workers, initializer=worker_init, initargs=(x.lib,)) as p, open(done, "a", encoding="utf-8") as fh:
            for o in p.imap_unordered(run_job, todo):
                rows.append(o)
                fh.write(json.dumps(o) + "\n")
                fh.flush()
        res = verify_summary(info, rows, Surrogate(ctx, cache["surrogate"]).scale)
        json.dump(res, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for name, o in res.items():
            print(f"{name:14s} params {sum(o['final_end'][k] for k in ATTRS):,} star {o['star']:,} "
                  f"Mid1 {o.get('mid1', 0) / 1e4:6.1f}万 Final {o.get('final', 0) / 1e4:6.1f}万 rating {o['rating']:,.0f}"
                  + (f"  {o['delta']:+,.0f} (audition ±{o['ci']:,.0f})" if o.get("delta") is not None else ""))
        return
    rentals = json.load(open(x.rentals, encoding="utf-8"))
    ctx = Context(cache["idol"], produces, user, index, strings=_strings())
    pl = pool(ctx, user, rentals)
    t0 = time.time()
    extra = [] if not x.quick else [r["formation"] for r in cache.get("formations") or []]
    if x.workers > 1 and not x.quick:
        from multiprocessing import Pool
        spec = {"idol": cache["idol"], "produces": produces, "user": user, "index": index, "rentals": rentals,
                "cache": cache, "strings": _strings()}
        with Pool(x.workers, initializer=recommend_worker_init, initargs=(spec,)) as p:
            res = recommend(ctx, pl, cache, quick=x.quick, extra_starts=extra, map_fn=p.map)
    else:
        res = recommend(ctx, pl, cache, quick=x.quick, extra_starts=extra)
    res["seconds"] = round(time.time() - t0, 1)
    if not x.quick:
        cache["formations"] = [{"formation": r["formation"], "rating": r["rating"]} for r in res["top"]]
        json.dump(cache, open(x.cache, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(res, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    base = res["reference"]["last"]["rating"]
    for name, r in [("last", res["reference"]["last"])] + [(f"#{i + 1}", r) for i, r in enumerate(res["top"])]:
        print(f"{name:5s} {r['rating']:9,.0f} ({r['rating'] - base:+7,.0f})  params {sum(r['final_end'][k] for k in ATTRS):,}"
              f" star {r['star']:,} Mid1 {(r['mid1'] or 0) / 1e4:6.1f}万 Final {(r['final'] or 0) / 1e4:6.1f}万  "
              + " / ".join(f"{ctx.sup.name(c)} Lv{lv}" for c, lv in r["formation"]))
    print("capped" if res["capped"] else "", res["evaluations"], "evaluations", res["seconds"], "s", res["notes"])


if __name__ == "__main__":
    main(sys.argv[1:])
