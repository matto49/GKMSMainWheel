# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""live advice for H.I.F card, drink, shop and interval decisions, valued in the 本戦 rating.

    python -B hif_advice.py replay FRAMES.jsonl[.gz] LOADOUT_ROOT [--exclude-own] [--json OUT]
"""
from __future__ import annotations

import glob
import json
import math
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
import GakumasCLI.produce.produce_ledger as PL      # noqa: E402
import GakumasCLI.produce.produce_effects as pe     # noqa: E402
from GakumasCLI.produce.hif_decisions import apply, rating_change, BASIC, DRINK_CAP   # noqa: E402
# the N.I.A produces open decisions too (nia_advice values them); nothing else here changes for them
NIA_IDS = ("produce-004", "produce-005")

SELECTION, MAIN = "produce-007", "produce-008"
MAIN_STAGES = (("AuditionMid1", 7, "mid1"), ("AuditionFinal", 9, "final"))
USER_GET = "/client.api.User/Get"
VISIT_END = {"StepShopEnd": "shop", "StepIntervalEnd": "interval"}
VISIT_LIVE = {"StepShopStart": "shop", "StepShopBuy": "shop", "StepShopReroll": "shop",
              "StepIntervalStart": "interval", "StepIntervalBuy": "interval", "StepIntervalReroll": "interval"}
STEP_SHOP, STEP_INTERVAL = 13, 47
PRESENT_STEPS = (15, 27)
INTERVAL_STAMINA = 10
SEARCH_SEED = 182
# Final = Mid1 x 1.004.
GROWTH_MAIN = {1: 1.175, 2: 1.125, 3: 1.111, 4: 1.070, 5: 1.002, 6: 1.0}
GROWTH_SEL = {1: 4.663, 2: 4.181, 3: 3.55, 4: 3.444, 5: 3.031, 6: 2.949, 7: 2.772, 8: 2.48, 9: 2.393,
              10: 2.115, 11: 2.012, 12: 1.845, 13: 1.794, 14: 1.43, 15: 1.398, 16: 1.37, 17: 1.307,
              18: 1.278, 19: 1.19, 20: 1.0}
MAIN_FROM_SEL = 1.20
EFFECT_BONUS = 500
STAR_BONUS = {"mid1": 2000, "final": 1850}
LEVEL_SPREAD = 0.30
SEARCH_LEVEL = (1.25, 0.45)
REBUILD_STAGE = {"mid1": "main1", "final": "main2"}
ROUTE_AUDITION = {"mid1": "audition_mid1", "final": "audition_final"}
DRINKS_AT_START = {
    ("PRODUCE_PLAN_TYPE_PLAN1", "mid1"): (21, {"pdrink_00-2-008": 18, "pdrink_00-3-005": 16, "pdrink_00-2-010": 16,
                                               "pdrink_00-3-001": 11, "pdrink_01-3-012": 11}, {4: 15, 3: 6}),
    ("PRODUCE_PLAN_TYPE_PLAN1", "final"): (16, {"pdrink_00-3-005": 15, "pdrink_00-2-010": 9, "pdrink_00-3-001": 8,
                                                "pdrink_00-2-008": 6, "pdrink_01-3-012": 5}, {4: 7, 3: 5, 2: 3, 1: 1}),
    ("PRODUCE_PLAN_TYPE_PLAN2", "mid1"): (25, {"pdrink_02-3-010": 32, "pdrink_00-3-005": 17, "pdrink_00-3-001": 16,
                                               "pdrink_00-2-010": 13, "pdrink_00-2-008": 12}, {4: 22, 3: 3}),
    ("PRODUCE_PLAN_TYPE_PLAN2", "final"): (23, {"pdrink_00-3-005": 20, "pdrink_02-3-010": 16, "pdrink_00-3-001": 9,
                                                "pdrink_00-2-010": 7, "pdrink_00-2-008": 4}, {4: 7, 3: 11, 2: 2, 1: 1, 0: 2}),
    ("PRODUCE_PLAN_TYPE_PLAN3", "mid1"): (29, {"pdrink_03-3-011": 24, "pdrink_00-3-005": 21, "pdrink_00-2-010": 21,
                                               "pdrink_00-2-008": 18, "pdrink_00-3-001": 17}, {4: 29}),
    ("PRODUCE_PLAN_TYPE_PLAN3", "final"): (30, {"pdrink_00-3-005": 16, "pdrink_00-3-001": 12, "pdrink_03-3-011": 12,
                                                "pdrink_00-2-010": 8, "pdrink_00-2-008": 7}, {4: 9, 3: 5, 2: 6, 1: 6, 0: 4}),
}
_PLAN_OF = {}


def start_stamina(live, rnd, step):
    """the stamina a 本戦 audition ahead starts with, without a route card: full for the Mid1 (the 本戦 starts full); from
    step 9, what is left."""
    from GakumasCLI.produce.hif_route import AUDITION_REST
    mx = int(live["max_stamina"] or 0)
    if rnd != "final" or step < 8 or live["stamina"] is None:
        return mx
    if step == 8:
        return min(mx, int(live["stamina"]) + int(round(mx * AUDITION_REST)))
    return int(live["stamina"])


def plan_type(idol):
    if idol not in _PLAN_OF:
        row = next((r for r in pe._rows("IdolCard") if r.get("id") == idol), None) or {}
        pt = row.get("planType")
        _PLAN_OF[idol] = pt.get("name") if isinstance(pt, dict) else pt
    return _PLAN_OF[idol]


def expected_drinks(held, idol, rnd, room=0):
    """``held`` plus the drinks players of this idol's plan type held at the start of the 本戦 ``rnd`` audition
    (DRINKS_AT_START), up to the median count there, ``room`` slots fewer (a drink decision leaves one for the
    option): each next slot goes to the drink with the most expected copies still unheld (count / starts)."""
    row = DRINKS_AT_START.get((plan_type(idol), rnd))
    if row is None:
        return list(held)
    n, held_by, counts = row
    seq = sorted((k for k, c in counts.items() for _ in range(c)))
    target = max(0, seq[(len(seq) - 1) // 2] - int(room))
    out = list(held)
    left = {d: c / n - out.count(d) for d, c in held_by.items()}
    while len(out) < target and left:
        d = max(left, key=lambda k: (left[k], k))
        if left[d] <= 0:
            break
        out.append(d)
        left[d] -= 1
    return out


# -- following the produce ---------------------------------------------------------------------
CHANGE_SELECT = 132
SCHOOL_STEPS = (12, 48, 49, 50)


def zemi_marks(p):
    """(school_gained, honsen_gained) of produce P as deck card keys "<id>+<upgrade>" -- the cards the 本戦's first 授業
    change gave, and every card the 本戦 gave (a response after its Start brought it, or changed its id), in the order
    they came.
    """
    deck = {c.get("number"): c for c in p.deck()}

    def key(n):
        c = deck[n]
        return f"{c.get('produce_card_id')}+{int(c.get('upgrade_count') or 0)}"
    gained = sorted((n for n, (pid, step, _) in p.card_seen.items() if pid == MAIN and step is not None and n in deck),
                    key=lambda n: (p.card_seen[n][1], n))
    school = [key(n) for n in (p.school_gained or ()) if n in deck]
    return school, [key(n) for n in gained]


SUPPORT_ORIGINS = (3, "PRODUCE_RESOURCE_ORIGIN_TYPE_SUPPORT_CARD")


def zemi_origins(p):
    """the deck cards (keys) of produce P a support card gave (the produce card list's origin_type 3), and how many 授業
    changes the produce on now has made ({produce id: count})."""
    deck = {c.get("number"): c for c in p.deck()}
    support = sorted(f"{c.get('produce_card_id')}+{int(c.get('upgrade_count') or 0)}" for n, c in deck.items()
                     if getattr(p, "card_origin", {}).get(n) in SUPPORT_ORIGINS)
    pid = p.progress.get("produce_id")
    return support, int(getattr(p, "school_changes", {}).get(pid, 0))


class _Produce(PL.Produce):
    """produce_ledger.Produce plus what an audition loadout needs and the ledger does not keep."""

    def __init__(self, uuid):
        super().__init__(uuid)
        self.supports = {}
        self.visit = None
        self.visit_closed = set()
        self.store_step = {}
        self.scores = {}
        self.audition_snaps = {}
        self.enchants = None
        self.memories = {}
        self.customize_items = {}
        self.schedule_pid = {}
        self.event_seen = {}
        self.full = False
        self.ended = False
        self.card_seen = {}
        self.school_gained = None
        self.card_origin = {}
        self.school_changes = {}
        self.rerolls = {}

    def apply(self, common, carried=False):
        """``carried``: a Start / User/Get response, whose event list holds events fired earlier."""
        before_step = self.progress.get("step_type")
        changes = {n for n, e in self.effects.items() if e.get("type") == CHANGE_SELECT}
        super().apply(common)
        upd = (common or {}).get("updated_data") or {}
        deleted = (common or {}).get("deleted_data") or {}
        pid = self.progress.get("produce_id")
        fresh = []
        for c in upd.get("user_produce_progress_produce_card_list") or []:
            n, cid = c.get("number"), c.get("produce_card_id")
            if c.get("origin_type") is not None:
                self.card_origin[n] = c.get("origin_type")
            seen = self.card_seen.get(n)
            if seen is None or seen[2] != cid:
                self.card_seen[n] = (pid, None if carried else self.progress.get("step_number"), cid)
                if not carried and not c.get("deleted"):
                    fresh.append(n)
        resolved = changes - set(self.effects)
        if resolved and fresh and not carried and (before_step is None or before_step in SCHOOL_STEPS):
            self.school_changes[pid] = self.school_changes.get(pid, 0) + 1
        if (resolved and fresh and not carried and pid == MAIN and self.school_gained is None
                and (before_step is None or before_step in SCHOOL_STEPS)):
            self.school_gained = fresh
        for s in upd.get("user_produce_progress_schedule_list") or []:
            self.schedule_pid[s.get("step_number")] = pid
        for s in deleted.get("user_produce_progress_schedule_list") or []:
            self.schedule_pid.pop(s.get("step_number"), None)
        for e in upd.get("user_produce_progress_event_list") or []:
            did = e.get("produce_step_event_detail_id")
            if did and did not in self.event_seen:
                self.event_seen[did] = (pid, None if carried else self.progress.get("step_number"))
        for m in upd.get("user_produce_progress_memory_list") or []:
            self.memories[m.get("number")] = m
        prog = self.progress
        if prog.get("exam_status_enchants") or prog.get("exam_permanent_audition_status_enchants"):
            self.enchants = ([e.get("id") for e in prog.get("exam_status_enchants") or []],
                             [e.get("id") for e in prog.get("exam_permanent_audition_status_enchants") or []])
        for s in upd.get("user_produce_progress_support_card_list") or []:
            self.supports[s.get("number")] = s
        for c in upd.get("user_produce_progress_produce_customize_item_list") or []:
            self.customize_items[c.get("number")] = c
        for key, store in (("user_produce_progress_memory_list", self.memories),
                           ("user_produce_progress_support_card_list", self.supports),
                           ("user_produce_progress_produce_customize_item_list", self.customize_items)):
            for row in deleted.get(key) or []:
                store.pop(row.get("number"), None)
        step = self.progress.get("step_number")
        for key, kind in (("user_produce_progress_shop_list", "shop"),
                          ("user_produce_progress_interval_list", "interval")):
            if upd.get(key):
                self.store_step[kind] = step


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def snapshot(p):
    """what audition_eval.audition_loadout reads of produce ``p``, frozen (Frozen)."""
    import copy
    return {"step": p.progress.get("step_number"), "progress": copy.deepcopy(p.progress),
            "cards": copy.deepcopy(p.cards), "supports": copy.deepcopy(p.supports),
            "memories": copy.deepcopy(p.memories)}


class Frozen:
    """a snapshot read as a produce (progress, deck, supports, memories) by audition_eval."""

    def __init__(self, snap):
        self.progress, self.cards = snap["progress"], snap["cards"]
        self.supports, self.memories = snap["supports"], snap["memories"]
        self.enchants, self.full, self.scores = None, True, {}

    def deck(self):
        return [c for n, c in sorted(self.cards.items()) if not c.get("deleted")]


class Tracker:
    """The produce the player is in, followed from the decoded frames (both directions)."""

    def __init__(self):
        self.produces = {}
        self.current = None
        self.version = 0

    def feed(self, frame):
        if frame.get("status") != "decoded":
            return False
        dec = frame.get("decoded") or {}
        rpc, direction = frame.get("rpc") or "", frame.get("dir")
        if rpc == USER_GET and direction == "in" and dec.get("user_data"):
            data = dec["user_data"]
            if (data.get("user_produce_progress") or {}).get("produce_id"):
                p = _Produce(None)
                p.apply({"updated_data": {k: v for k, v in data.items() if k.startswith("user_produce_progress")}},
                        carried=True)
                p.full = True
                self.current = p
                self.version += 1
                return True
            return False
        if not PL.is_produce(frame):
            upd = ((dec.get("common_response") or {}).get("updated_data") or {})
            if (direction != "in" or self.current is None or self.current.ended
                    or not any(k.startswith("user_produce_progress") for k in upd)):
                return False
            self.current.apply(dec.get("common_response"))
            self.version += 1
            return True
        m = PL.method(frame)
        if direction == "out":
            if m == "Start":
                self.current = _Produce(None)
            else:
                uuid = dec.get("device_produce_uuid")
                if uuid and UUID.match(str(uuid)):
                    cur = self.current
                    if uuid not in self.produces and cur is not None and cur.uuid is None:
                        cur.uuid = uuid
                        self.produces[uuid] = cur
                    self.current = self.produces.setdefault(uuid, _Produce(uuid))
            cur = self.current
            if cur is not None and m == "StepAuditionEndExamBattle" and (dec.get("result") or {}).get("score") is not None:
                cur.scores[cur.progress.get("step_number")] = int(dec["result"]["score"])
            return False
        cur = self.current
        if m == "Start" and dec.get("device_produce_uuid"):
            uuid = dec["device_produce_uuid"]
            if cur is None or cur.ended or cur.uuid not in (None, uuid):
                cur = self.current = self.produces.get(uuid) or _Produce(None)
        if cur is None:
            return False
        if m == "Start" and dec.get("device_produce_uuid"):
            cur.uuid = dec["device_produce_uuid"]
            self.produces[cur.uuid] = cur
            cur.full = True
        cur.apply(dec.get("common_response"), carried=m == "Start")
        if m == "StepAuditionStart" and cur.full:
            cur.audition_snaps[cur.progress.get("step_type")] = snapshot(cur)
        if m == "End":
            cur.ended = True
        step = cur.progress.get("step_number")
        if m in VISIT_LIVE:
            cur.visit = (VISIT_LIVE[m], step)
            if m in ("StepShopReroll", "StepIntervalReroll"):
                key = (VISIT_LIVE[m], step)
                cur.rerolls[key] = cur.rerolls.get(key, 0) + 1
        elif m in VISIT_END:
            cur.visit_closed.add((VISIT_END[m], step))
            cur.visit = None
        self.version += 1
        return True

    # -- the decision open now ----------------------------------------------------------------
    def decision(self):
        """The decision the player faces after the last response, or None."""
        p = self.current
        if p is None or not p.progress or p.ended:
            return None
        prog = p.progress
        pid = prog.get("produce_id")
        if pid not in (SELECTION, MAIN) + NIA_IDS:
            return None
        step = int(prog.get("step_number") or 0)
        stype = prog.get("step_type")
        deck_sig = tuple((c.get("number"), c.get("produce_card_id"), c.get("upgrade_count", 0)) for c in p.deck())
        drinks = list(prog.get("produce_drink_ids") or [])
        pk = id(p)
        base = {"uuid": p.uuid, "produce_id": pid, "idol_card_id": prog.get("idol_card_id"), "step": step,
                "produce_point": int(prog.get("produce_point") or 0), "drinks": drinks}
        if pid in NIA_IDS:
            base.update(vote_count=int(prog.get("vote_count") or 0),
                        growth_permils=[int(prog.get(f"{k}_growth_rate_permil") or 0) for k in ("vocal", "dance", "visual")],
                        audition_growth_permil=int(prog.get("audition_parameter_growth_rate_permil") or 0))
        if len(drinks) > DRINK_CAP:
            return dict(base, kind="drink_discard", key=(pk, step, "discard", tuple(drinks)), candidates=[])
        pending = [e for e in p.effects.values() if e.get("pick_range_type") == 1 and e.get("rewards")]
        pending.sort(key=lambda e: (bool(e.get("is_next")), e.get("number") or 0))
        effect = pending[-1] if pending else None
        if effect is not None:
            cands = [PL.resource(r) for r in effect["rewards"]]
            types = {c["type"] for c in cands}
            if types in ({"card"}, {"drink"}):
                kind = "card_offer" if types == {"card"} else "drink_offer"
                return dict(base, kind=kind, source="effect", effect_type=PL.EFFECT_KIND.get(effect.get("type"), effect.get("type")),
                            produce_effect_id=effect.get("produce_effect_id"),
                            pick_min=int(effect.get("pick_count_min") or 0),
                            rerolls_left=int(prog.get("produce_card_remain_select_reroll_count") or 0)
                            if kind == "card_offer" else 0,
                            key=(pk, step, "effect", effect.get("number"), _sig(cands), deck_sig, tuple(drinks)),
                            candidates=cands)
        if stype in PRESENT_STEPS:
            for pos, pres in sorted(p.present.items()):
                if pres.get("received") or len(pres.get("rewards") or []) < 2:
                    continue
                cands = [PL.resource(r) for r in pres["rewards"]]
                types = {c["type"] for c in cands}
                if types in ({"card"}, {"drink"}):
                    kind = "card_offer" if types == {"card"} else "drink_offer"
                    return dict(base, kind=kind, source="present", position=pos,
                                key=(pk, step, "present", pos, _sig(cands), deck_sig, tuple(drinks)), candidates=cands)
                break
        for kind, store, st in (("shop", p.shop, STEP_SHOP), ("interval", p.interval, STEP_INTERVAL)):
            if stype == st and store and p.store_step.get(kind) == step and (kind, step) not in p.visit_closed:
                slots = PL.slots_view(store)
                return dict(base, kind=kind, key=(pk, step, kind, _sig(slots), deck_sig, tuple(drinks), base["produce_point"]),
                            candidates=slots, rerolls=p.rerolls.get((kind, step), 0))
        return None


def _sig(rows):
    return tuple((r.get("position"), r.get("type"), r.get("resource_id"), r.get("upgrade_count"), r.get("price"))
                 for r in rows)


# -- recorded 本戦 auditions (the templates) ---------------------------------------------------
def templates(loadout_root):
    """{(idol_card_id, round): [template, ...]} newest run first; a template is a recorded 本戦 audition
    loadout with its run and the player's score there (``report_*.json`` final.client, None when the
    round did not settle)."""
    out = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(str(loadout_root), "*", "loadout_*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("produce_id") != MAIN or not any(int(v or 0) for v in (d.get("score_bonus_permils") or {}).values()):
            continue
        score = None
        rep = f.replace("loadout_", "report_")
        if os.path.exists(rep):
            try:
                score = ((json.load(open(rep, encoding="utf-8")).get("final") or {}).get("client"))
            except (OSError, ValueError):
                score = None
        run = Path(f).parent.name
        out[(d.get("idol_card_id"), d.get("round"))].append({"path": f, "run": run, "loadout": d, "score": score})
    for k in out:
        out[k].sort(key=lambda t: (t["run"], t["score"] is not None, t["path"]), reverse=True)
    return dict(out)


def pick_template(index, idol, rnd, exclude_run=None):
    """The newest template; ``exclude_run``: a run or a set of runs left out (a replay of a recorded
    produce leaves out every run of that produce, so it sees only what an earlier day had)."""
    skip = {exclude_run} if isinstance(exclude_run, str) else set(exclude_run or ())
    rows = [t for t in index.get((idol, rnd), ()) if t["run"] not in skip]
    return rows[0] if rows else None


def level_of(index, idol, rnd, exclude_run=None):
    """The idol's recorded score in a 本戦 round: the geometric mean over its produces, each at its last settled attempt;
    (level, produces).
    """
    skip = {exclude_run} if isinstance(exclude_run, str) else set(exclude_run or ())
    by_produce = {}
    for t in index.get((idol, rnd), ()):
        if t["score"] and t["run"] not in skip:
            key = tuple(sorted((t["loadout"].get("parameters") or {}).items()))
            by_produce.setdefault(key, int(t["score"]))
    if not by_produce:
        return None, 0
    return int(math.exp(sum(math.log(v) for v in by_produce.values()) / len(by_produce))), len(by_produce)


# -- the loadouts ahead ------------------------------------------------------------------------
def live_parts(p):
    """What the live state gives an audition loadout."""
    prog = p.progress
    deck = []
    for c in p.deck():
        e = {"produce_card_id": c.get("produce_card_id"), "current_upgrade_count": int(c.get("upgrade_count") or 0)}
        if c.get("customizes"):
            e["customizes"] = [{"id": x["id"], "customize_count": int(x.get("customize_count") or 1)} for x in c["customizes"]]
        deck.append(e)
    supports = [{"support_card_id": r.get("support_card_id"), "permil": int(r.get("produce_card_upgrade_permil") or 0)}
                for _, r in sorted(p.supports.items(), key=lambda kv: kv[0] or 0)]
    return {"deck_cards": deck,
            "produce_item_ids": [i.get("produce_item_id") for i in prog.get("produce_items") or []],
            "drink_ids": list(prog.get("produce_drink_ids") or []),
            "support_cards": supports,
            "lesson_start_enchant_ids": [e.get("id") for e in prog.get("exam_status_enchants") or []],
            "main_item_enchant_ids": [e.get("id") for e in prog.get("exam_permanent_audition_status_enchants") or []],
            "parameters": {"1": int(prog.get("vocal") or 0), "2": int(prog.get("dance") or 0), "3": int(prog.get("visual") or 0)},
            "max_stamina": int(prog.get("max_stamina") or 0),
            "stamina": None if prog.get("stamina") is None else int(prog["stamina"])}


def predicted_params(p):
    """The parameters at the 本戦 auditions, from the live ones and the measured growth (one ratio for
    all three: the corpus is too small to split it)."""
    prog = p.progress
    step = int(prog.get("step_number") or 0)
    if prog.get("produce_id") == MAIN:
        r = GROWTH_MAIN.get(step, 1.0)
    else:
        r = GROWTH_SEL.get(step, GROWTH_SEL[1]) * MAIN_FROM_SEL
    return {k: int(round(int(prog.get(name) or 0) * r)) for k, name in (("1", "vocal"), ("2", "dance"), ("3", "visual"))}


def _battle_config(catalog, idol, rnd):
    row = catalog.difficulty(pe.difficulty_id(idol), "AUDITION_" + rnd.upper(), MAIN)
    return row["produceExamBattleConfigId"]


def rescale(lo, params, catalog):
    """The template's permils moved to other parameters: each by the catalog's base permil ratio
    (which carries the shortfall penalty); returns the permil-weighted ratio for the level."""
    try:
        cfg = _battle_config(catalog, lo["idol_card_id"], lo["round"])
        old = catalog.base_permils(cfg, {int(k): int(v) for k, v in lo["parameters"].items()})
        new = catalog.base_permils(cfg, {int(k): int(v) for k, v in params.items()})
    except Exception:              # noqa: BLE001
        return 1.0
    eff = {k: int(v) for k, v in lo["score_bonus_permils"].items()}
    out = {k: int(math.ceil(v * new[int(k)] / old[int(k)] / 10.0)) * 10 for k, v in eff.items()}
    lo["score_bonus_permils"] = out
    lo["parameters"] = dict(params)
    return sum(out.values()) / max(1, sum(eff.values()))


def stage_loadout(p, rnd, template, drinks_live, enchants_live, catalog=None, model=None):
    """One 本戦 audition built from the live state and a template (or the live state alone);
    returns (loadout, level factor).
    """
    live = live_parts(p)
    idol = p.progress.get("idol_card_id")
    factor = 1.0
    if template is not None:
        lo = json.loads(json.dumps(template["loadout"]))
        lo.pop("seed", None)
        if catalog is not None:
            factor = rescale(lo, predicted_params(p), catalog)
    else:
        lo = {"schema": "gkms.hif_loadout.v1", "idol_card_id": idol, "round": rnd, "produce_id": MAIN,
              "parameters": predicted_params(p), "include_produce_default": False,
              "effect_bonus_permils": {k: EFFECT_BONUS for k in ("1", "2", "3")}, "star_bonus_permil": STAR_BONUS[rnd],
              "starting_stamina": live["max_stamina"], "max_stamina": live["max_stamina"], "drink_ids": [],
              "lesson_start_enchant_ids": [], "main_item_enchant_ids": []}
    lo.update(idol_card_id=idol, round=rnd, produce_id=MAIN, deck_cards=live["deck_cards"],
              produce_item_ids=live["produce_item_ids"], support_cards=live["support_cards"])
    if drinks_live == "live":
        lo["drink_ids"] = live["drink_ids"]
    elif drinks_live == "fill":
        held, rest = list(live["drink_ids"]), list(lo.get("drink_ids") or [])
        for d in held:
            if d in rest:
                rest.remove(d)
        lo["drink_ids"] = held + rest[:max(0, len(lo.get("drink_ids") or []) - len(held))]
    if enchants_live and p.enchants is not None:
        lo["lesson_start_enchant_ids"], lo["main_item_enchant_ids"] = list(p.enchants[0]), list(p.enchants[1])
    elif enchants_live and model is not None and p.memories:
        lo["lesson_start_enchant_ids"] = memory_enchants(p, model)
        if template is None:
            lo["main_item_enchant_ids"] = live["main_item_enchant_ids"]
    elif template is None:
        lo["lesson_start_enchant_ids"] = live["lesson_start_enchant_ids"]
        lo["main_item_enchant_ids"] = live["main_item_enchant_ids"]
    if p.progress.get("produce_id") == MAIN and live["max_stamina"]:
        lo["max_stamina"] = live["max_stamina"]
        lo["starting_stamina"] = min(int(lo.get("starting_stamina") or live["max_stamina"]), live["max_stamina"])
        if rnd == "final" and int(p.progress.get("step_number") or 0) >= 8 and live["stamina"] is not None:
            lo["starting_stamina"] = max(1, min(live["stamina"], live["max_stamina"]))
    return lo, factor


HIF_MEMORY_TRIGGER = "start_audition-for_hif_memory"


def memory_enchants(p, model):
    """The H.I.F memory enchants a 本戦 audition starts with, from the produce's memory list: each memory's
    ``start_audition-for_hif_memory`` ability -> its ProduceEffect's produceExamStatusEnchantId, by memory number,
    each id once."""
    out = []
    for _n, mem in sorted(p.memories.items(), key=lambda kv: kv[0] or 0):
        for ab in mem.get("abilities") or []:
            if HIF_MEMORY_TRIGGER not in (ab.get("id") or ""):
                continue
            sk = model.memory_skill(ab["id"], ab.get("level"))
            eff = model.effects.get(sk.effect_id) if sk is not None else None
            enchant = (eff or {}).get("produceExamStatusEnchantId")
            if enchant and enchant not in out:
                out.append(enchant)
    return out


def _rarity(card_id):
    tail = (card_id or "").rsplit("-", 1)[-1]
    head = tail.split("_", 1)[0]
    return int(head) if head.isdigit() else 9


def removal_guess(deck):
    """The deck card a change select most likely gives up: the lowest rarity (the 眠気 card aside), the one held in most
    copies, the last of those in deck order.
    """
    cards = [c for c in deck if "-acc-0_" not in c["produce_card_id"]]
    if not cards:
        return None
    low = min(_rarity(c["produce_card_id"]) for c in cards)
    pool = [c for c in cards if _rarity(c["produce_card_id"]) == low]
    count = {}
    for c in pool:
        count[c["produce_card_id"]] = count.get(c["produce_card_id"], 0) + 1
    top = max(count.values())
    pick = [c for c in pool if count[c["produce_card_id"]] == top][-1]
    return {"id": pick["produce_card_id"], "upgrade": int(pick.get("current_upgrade_count") or 0)}


REMOVAL_CANDIDATES = 4
_CARD_ROWS = None


def _card_rows():
    global _CARD_ROWS
    if _CARD_ROWS is None:
        try:
            _CARD_ROWS = {r["id"]: r for r in pe._rows("ProduceCard")}
        except Exception:
            _CARD_ROWS = {}
    return _CARD_ROWS


def is_trouble(card_id):
    row = _card_rows().get(card_id) or {}
    cat = pe._name(row.get("category")) if row else ""
    return cat.endswith("TROUBLE") if cat else "-acc-0_" in (card_id or "")


def removal_candidates(deck, allow_trouble=True, k=REMOVAL_CANDIDATES):
    """the deck cards a change or a delete could give up, the likeliest first: trouble cards (when the effect allows
    them: an H.I.F school change select searches 「トラブルカード以外のスキルカード」,
    p_card_search-active_skill-mental_skill-deck_all), then the starter cards, then by the master's card evaluation
    and rarity, an unupgraded copy before an upgraded one; one per (id, upgrade), at most k.
    """
    rows = _card_rows()
    seen, out = set(), []
    for c in deck:
        cid, up = c.get("produce_card_id"), int(c.get("current_upgrade_count") or 0)
        if not cid or (cid, up) in seen or (not allow_trouble and is_trouble(cid)):
            continue
        if "-ido-" in cid or "_ido-" in cid:
            continue
        seen.add((cid, up))
        r = rows.get(cid) or {}
        rarity = (r.get("rarity") or {}).get("number") if isinstance(r.get("rarity"), dict) else None
        out.append(((0 if is_trouble(cid) else 1), (0 if any(x in cid for x in BASIC) else 1),
                    int(r.get("evaluation") or 0), rarity if rarity is not None else _rarity(cid), up,
                    {"id": cid, "upgrade": up}))
    out.sort(key=lambda x: x[:5])
    return [x[-1] for x in out[:k]]


def _removal(dec, deck, allow_trouble):
    """The change select's give-up candidates."""
    if dec.get("produce_id") in (SELECTION, MAIN):
        try:
            import GakumasCLI.produce.pickv as PV
            pack, _state = PV.pack_for(dec.get("idol_card_id"))
        except Exception:                         # noqa: BLE001
            pack = None
        if pack:
            return PV.removal_by_v(deck, pack, allow_trouble=allow_trouble)
    return removal_candidates(deck, allow_trouble=allow_trouble)


def _basics(deck):
    return [c for c in deck if any(x in (c.get("produce_card_id") or "") for x in BASIC)]


def _card(c):
    return {"id": c.get("resource_id"), "upgrade": int(c.get("upgrade_count") or 0)}


def options_of(dec, p, drinks_valued=None):
    """[{label, ops, kind, price?, resource?, drink_stage?}]: each a change of the base loadout."""
    kind, cands = dec["kind"], dec["candidates"]
    main = dec["produce_id"] == MAIN if drinks_valued is None else bool(drinks_valued)
    out = []
    drinks = list(dec["drinks"])
    if kind == "card_offer" and dec.get("effect_type") == "card_change_select":
        effect_id = dec.get("produce_effect_id") or ""
        gone = _removal(dec, live_parts(p)["deck_cards"], "active_skill-mental_skill" not in effect_id)
        for i, c in enumerate(cands):
            for g in gone:
                out.append({"label": f"change {g['id']}+{g['upgrade']} -> {c['resource_id']}+{int(c.get('upgrade_count') or 0)}",
                            "kind": "card", "index": i, "resource": _card(c), "removed": g,
                            "ops": [{"op": "remove_card", "card": g}, {"op": "add_card", "card": _card(c)}]})
    elif kind == "card_offer":
        for i, c in enumerate(cands):
            out.append({"label": f"take {c['resource_id']}+{int(c.get('upgrade_count') or 0)}", "kind": "card",
                        "index": i, "resource": _card(c), "ops": [{"op": "add_card", "card": _card(c)}]})
    elif kind == "drink_offer" and main:
        for i, c in enumerate(cands):
            out.append({"label": f"take {c['resource_id']}", "kind": "drink", "index": i, "resource": c["resource_id"],
                        "ops": [{"op": "add_drink_any", "drink": c["resource_id"]}]})
        if len(drinks) >= DRINK_CAP:
            out += _drop_options(drinks)
    elif kind == "drink_discard" and main:
        out += _drop_options(drinks)
    elif kind in ("shop", "interval"):
        deck = live_parts(p)["deck_cards"]
        basics = _basics(deck)
        pp = dec["produce_point"]
        for s in cands:
            t, price = s.get("type"), s.get("price")
            if s.get("purchased") or not price:
                continue
            price = price * (1000 - min(1000, int(s.get("discount") or 0))) // 1000
            if price > pp:
                continue
            pos = s.get("position")
            if t == "card":
                out.append({"label": f"buy {s['resource_id']}+{int(s.get('upgrade_count') or 0)}", "kind": "card",
                            "position": pos, "price": price, "resource": _card(s), "ops": [{"op": "add_card", "card": _card(s)}]})
            elif t == "drink" and main:
                out.append({"label": f"buy {s['resource_id']}", "kind": "drink", "position": pos, "price": price,
                            "resource": s["resource_id"], "ops": [{"op": "add_drink_any", "drink": s["resource_id"]}]})
            elif t == "card_change":
                for g in _removal(dec, deck, True):
                    out.append({"label": f"change {g['id']}+{g['upgrade']} -> {s['resource_id']}", "kind": "card",
                                "position": pos, "price": price, "resource": _card(s), "removed": g,
                                "ops": [{"op": "remove_card", "card": g}, {"op": "add_card", "card": _card(s)}]})
            elif t == "card_delete":
                for g in _removal(dec, deck, True):
                    out.append({"label": f"delete {g['id']}+{g['upgrade']}", "kind": "card", "position": pos,
                                "price": price, "removed": g, "ops": [{"op": "remove_card", "card": g}]})
            elif t == "card_upgrade":
                seen = set()
                for c in reversed(deck):
                    cid = c["produce_card_id"]
                    if c["current_upgrade_count"] or cid in seen or c in basics:
                        continue
                    seen.add(cid)
                    out.append({"label": f"upgrade {cid}", "kind": "card", "position": pos, "price": price,
                                "resource": {"id": cid, "upgrade": 1},
                                "ops": [{"op": "upgrade_card", "card": {"id": cid, "upgrade": 0}}]})
                    if len(seen) >= 6:
                        break
            elif t == "stamina" and kind == "interval" and main:
                units = min(5, pp // price)
                gain = int(s.get("stamina_recover_value") or 2) * units
                out.append({"label": f"stamina +{gain}", "kind": "stamina", "position": pos, "price": price * units,
                            "ops": [{"op": "stamina", "delta": gain}]})
        if main and len(drinks) >= DRINK_CAP and any(o["kind"] == "drink" for o in out):
            out += _drop_options(drinks)
    return out


def _drop_options(drinks):
    return [{"label": f"discard {d}", "kind": "drop", "resource": d, "ops": [{"op": "remove_drink", "drink": d}]}
            for d in dict.fromkeys(drinks)]


def apply_ops(lo, ops):
    """hif_decisions.apply, plus a drink added over the cap (its value is paired with a discard)."""
    out, rest = lo, []
    for op in ops:
        if op["op"] == "add_drink_any":
            if out is None:
                return None
            out = json.loads(json.dumps(out))
            out["drink_ids"].append(op["drink"])
        else:
            rest.append(op)
    return apply(out, rest) if rest else out


def projection_of(route, p):
    """{round: {"params": {"1": vo, "2": da, "3": vi}, "star", "stamina"}} of the 本戦 auditions ahead on the route card's
    plan (ProduceAdvisor.route_view's ``auditions``, hif_route.audition_checks), or {} when the card has no plan for
    this produce."""
    if (not route or not route.get("auditions") or route.get("produce_id") != MAIN
            or (p.progress or {}).get("produce_id") != MAIN):
        return {}
    out = {}
    for a in route.get("auditions") or ():
        rnd = next((r for r, name in ROUTE_AUDITION.items() if name == a.get("audition")), None)
        if rnd is None or not a.get("params"):
            continue
        prm = a["params"]
        out[rnd] = {"params": {"1": int(prm.get("vocal") or 0), "2": int(prm.get("dance") or 0),
                               "3": int(prm.get("visual") or 0)},
                    "star": a.get("star"), "stamina": a.get("stamina")}
    return out


def rebuild_stages(dec, p, catalog, model, projection=None):
    """the 本戦 auditions after the decision's step, each built from the live state alone (audition_eval.audition_loadout)
    with the route card's parameters, stars and starting stamina for it when ``projection`` (projection_of) has them,
    else predicted_params and the live stars; returns (stages, notes)."""
    from GakumasCLI.produce import audition_eval as AE
    step = dec["step"]
    stages, notes, live = [], [], live_parts(p)
    proj = projection or {}
    for step_id, number, rnd in MAIN_STAGES:
        if number <= step:
            continue
        got = proj.get(rnd) or {}
        stamina = got["stamina"] if got.get("stamina") is not None else start_stamina(live, rnd, step)
        star = int(got["star"]) if got.get("star") is not None else int(p.progress.get("star") or 0)
        params = got.get("params") or predicted_params(p)
        lo = AE.audition_loadout(p, REBUILD_STAGE[rnd], catalog, model, params=params, star=star, stamina=stamina)
        if not (rnd == "final" and step >= 9):
            room = 1 if dec["kind"] in ("drink_offer", "drink_discard", "shop", "interval") else 0
            lo["drink_ids"] = expected_drinks(live["drink_ids"], p.progress.get("idol_card_id"), rnd, room)
        stages.append({"step_id": step_id, "round": rnd, "template": None, "template_run": None, "loadout": lo,
                       "level": None, "level_runs": 0, "level_factor": 1.0, "star": star,
                       "projection": "route" if got.get("params") else "growth"})
    if any(st["projection"] == "growth" for st in stages):
        notes.append("路线卡还没算出这场审查：参数按平均成长估，星按现在的值（偏低）")
    if step <= 1 and not live["main_item_enchant_ids"]:
        notes.append("定制道具的审查附魔第 2 步才到，这一步的估值没带它")
    if any(len(st["loadout"]["drink_ids"]) > len(live["drink_ids"]) for st in stages):
        notes.append("还没拿到的饮料按以前局同计划类型审查开始时最常带的补上（来源：以前局统计）")
    return stages, notes


def plan(dec, p, index, exclude_run=None, catalog=None, model=None, rebuild=False, projection=None, level=None):
    """The stages (loadouts ahead), options and rating levels for a decision; None when it is not valued."""
    if dec is None:
        return None
    main = dec["produce_id"] == MAIN
    step = dec["step"]
    notes = []
    if not main and dec["kind"] in ("drink_offer", "drink_discard"):
        return {"decision": dec, "stages": [], "options": [], "notes": ["选拔饮料只影响选拔审查能否过关，不带进本战，不估评价"],
                "skipped": "selection_drink"}
    ahead = [s for s in MAIN_STAGES if not main or s[1] > step]
    if not ahead:
        return None
    idol = dec["idol_card_id"]
    stages = []
    drink_decision = dec["kind"] in ("drink_offer", "drink_discard")
    if rebuild and main and catalog is not None:
        stages, notes = rebuild_stages(dec, p, catalog, model, projection)
        known = {"AuditionMid1": p.scores.get(7)} if step >= 7 else {}
        factor, source = level if level else (1.0, None)
        if source:
            notes.append(f"分数水平按本局{source['label']}校准：你的分 ÷ 搜索分 = {factor:.2f}")
        return _with_options(dec, p, stages, notes,
                             rebuild={"known": known, "star_permil": int(p.progress.get("star_permil") or 0),
                                      "level": round(float(factor), 4), "level_from": source})
    for i, (step_id, number, rnd) in enumerate(ahead):
        t = pick_template(index, idol, rnd, exclude_run)
        if not main:
            drinks = False
        elif drink_decision or step >= 8:
            drinks = "live"
        else:
            drinks = "fill" if t is not None else "live"
        lo, factor = stage_loadout(p, rnd, t, drinks_live=drinks, enchants_live=main, catalog=catalog, model=model)
        level, runs = level_of(index, idol, rnd, exclude_run)
        stages.append({"step_id": step_id, "round": rnd, "template": t and t["path"], "template_run": t and t["run"],
                       "loadout": lo, "level": int(level * factor) if level else None, "level_runs": runs,
                       "level_factor": round(factor, 3)})
    if any(s["template"] is None for s in stages):
        notes.append("没有这名偶像的本戦记录：参数按当前值和平均成长估、审查倍率按目录算，评价只作参考")
    return _with_options(dec, p, stages, notes)


def _with_options(dec, p, stages, notes, rebuild=None):
    opts = options_of(dec, p)
    for o in opts:
        if o["kind"] in ("drink", "drop"):
            o["stages"] = [stages[0]["step_id"]]
        elif o["kind"] == "stamina":
            o["stages"] = ["AuditionFinal"]
        else:
            o["stages"] = [s["step_id"] for s in stages]
    opts = [o for o in opts if any(apply_ops(s["loadout"], o["ops"]) is not None for s in stages if s["step_id"] in o["stages"])]
    if dec["kind"] in ("shop", "interval"):
        notes.append("每项单独估：只买这一项、其余不变")
    if dec["kind"] == "card_offer" and dec.get("effect_type") == "card_change_select":
        notes.append("换卡（セレクトチェンジ）：拿一张、同时选牌组里被换掉的一张；每种组合单独估"
                     + ("（这次只能换技能卡，トラブル卡不行）" if "active_skill-mental_skill" in (dec.get("produce_effect_id") or "") else ""))
    elif dec["kind"] in ("shop", "interval") and any(o.get("removed") for o in opts):
        notes.append("チェンジ / 删除：被换掉或删掉的那张由你选；按最可能的几张各估一次")
    out = {"decision": dec, "stages": stages, "options": opts, "notes": notes}
    if rebuild is not None:
        out["rebuild"] = rebuild
    return out


# -- : the 選抜 card indicator by swap-in (plan 选课选卡大改) ------------------------------------------
SWAP_ROUNDS = (("AuditionMid1", "mid1"), ("AuditionFinal", "final"))


def _incoming(o):
    """The card an option brings in ({id, upgrade}: its last add_card), or None."""
    adds = [op["card"] for op in o.get("ops") or () if op.get("op") == "add_card"]
    return adds[-1] if adds else None


def swap_plan(pl, pack):
    """a 選抜 card offer's plan with the 本戦 auditions ahead replaced by the idol card's pick pack templates -- each
    option puts its card into a template deck in place of that deck's lowest-V card (pickv.removal_by_v,
    trouble cards and the idol's own card never), base and options on the same seeds.
    """
    dec = (pl or {}).get("decision") or {}
    temps = (pack or {}).get("swap") or []
    if dec.get("produce_id") != SELECTION or dec.get("kind") != "card_offer" or not temps or not pl.get("options"):
        return None
    from GakumasCLI.produce import pickv as PV
    idol = dec.get("idol_card_id") or pack.get("idol")
    stages = []
    for step_id, rnd in SWAP_ROUNDS:
        rows = []
        for t in temps:
            r = t.get(rnd) or {}
            if not r.get("loadout"):
                continue
            lo = dict(r["loadout"], schema="gkms.hif_loadout.v1", idol_card_id=idol, round=rnd, produce_id=MAIN)
            out = PV.removal_by_v(lo.get("deck_cards") or [], pack, allow_trouble=False, k=1)
            if not out:
                continue
            rows.append({"record": t.get("record"), "seed": int(r["seed"]), "loadout": lo,
                         "out": {"id": out[0]["id"], "upgrade": int(out[0].get("upgrade") or 0)}})
        if rows:
            stages.append({"step_id": step_id, "round": rnd, "template": None, "template_run": None,
                           "loadout": rows[0]["loadout"], "level": None, "level_runs": 0, "level_factor": 1.0,
                           "swap": rows})
    if not stages:
        return None
    opts, alias, first = [], {}, {}
    for o in pl["options"]:
        card = _incoming(o)
        if card is None:
            continue
        key = (card["id"], int(card.get("upgrade") or 0))
        alias[o["label"]] = first.setdefault(key, o["label"])
        opts.append(dict(o, stages=[st["step_id"] for st in stages], swap_card=card))
    if not opts:
        return None
    notes = list(pl.get("notes") or ()) + [
        f"選抜的模拟指标：把卡放进前段 {len(stages[0]['swap'])} 副本戦卡组（各换掉其中 V 最低的一张），"
        "同种子比分数变化 %；只显示，不参与推荐"]
    live = next(((st.get("loadout") or {}).get("deck_cards") for st in pl.get("stages") or ()
                 if (st.get("loadout") or {}).get("deck_cards") is not None), None)
    return dict(pl, stages=stages, options=opts, notes=notes, live_deck=live,
                swap={"templates": len(stages[0]["swap"]), "alias": alias})


def _swap_job(st, k, key, o=None):
    """(loadout, exam seed) of seed slot K of a swap stage -- the base (O None) or option O's card in place of the
    template's lowest-V card; loadout None when the card does not go in."""
    rows = st["swap"]
    t = rows[k % len(rows)]
    seed = t["seed"] if k < len(rows) else exam_seeds(f"{key}|{t['record']}", st["step_id"], 1, k)[0]
    if o is None:
        return t["loadout"], seed
    return apply_ops(t["loadout"], [{"op": "remove_card", "card": t["out"]}, {"op": "add_card", "card": o["swap_card"]}]), seed


# -- jobs and the tally ------------------------------------------------------------------------
def exam_seeds(key, step_id, n, start=0):
    rng = random.Random(f"{key}|{step_id}")
    out = [rng.randrange(1, 2 ** 31 - 1) for _ in range(start + n)]
    return out[start:]


def jobs(pl, key, seeds, budget, start=0, options=None, search_seed=SEARCH_SEED):
    """One job per (stage, option or base, seed); options interleaved so a partial wave covers them all."""
    out = []
    alias = (pl.get("swap") or {}).get("alias") or {}
    for st in pl["stages"]:
        if st.get("swap"):
            queued = [o for o in pl["options"] if st["step_id"] in o["stages"] and alias.get(o["label"], o["label"]) == o["label"]
                      and (options is None or o["label"] in options or any(alias.get(x) == o["label"] for x in options))]
            if not queued:
                continue
            for k in range(start, start + seeds):
                lo, s = _swap_job(st, k, key)
                out.append({"kind": "produce_score", "stage": st["step_id"], "option": "base", "exam_seed": s,
                            "k": k, "loadout": lo, "budget": float(budget), "search_seed": search_seed})
                for o in queued:
                    lo, s = _swap_job(st, k, key, o)
                    if lo is not None:
                        out.append({"kind": "produce_score", "stage": st["step_id"], "option": o["label"],
                                    "exam_seed": s, "k": k, "loadout": lo, "budget": float(budget),
                                    "search_seed": search_seed})
            continue
        sd = exam_seeds(key, st["step_id"], seeds, start)
        labels = [("base", st["loadout"])]
        for o in pl["options"]:
            if st["step_id"] not in o["stages"] or (options is not None and o["label"] not in options):
                continue
            lo = apply_ops(st["loadout"], o["ops"])
            if lo is not None:
                labels.append((o["label"], lo))
        if len(labels) < 2:
            continue
        for i, s in enumerate(sd):
            for label, lo in labels:
                out.append({"kind": "produce_score", "stage": st["step_id"], "option": label, "exam_seed": s,
                            "k": start + i, "loadout": lo, "budget": float(budget), "search_seed": search_seed})
    return out


def run_job(master, job, build=None, search=None):
    """A ``produce_score`` job in this process (the pool worker runs the same)."""
    import time
    if build is None:
        from GakumasCLI.scenario.hif import build_hif_loadout_session as build
    if search is None:
        from GakumasCLI.scenario.policy_search import policy_search as search
    t0 = time.perf_counter()
    session, _ = build(master, job["loadout"], seed=int(job["exam_seed"]))
    best, path, stats = search(session, deadline=float(job["budget"]), seed=int(job.get("search_seed") or SEARCH_SEED))
    return {"status": "ok", "score": int(best.state.score), "native": bool((stats or {}).get("native")),
            "seconds": round(time.perf_counter() - t0, 3)}


def rating_delta(levels, rel, spread=0.0):
    """The rating change when the rounds in ``rel`` move by that fraction at ``levels`` ({"AuditionMid1": score,
    "AuditionFinal": score}); a round missing from ``rel`` stays (hif_decisions.rating_change); ``spread``: the level
    is a prediction, average over that log-normal spread."""
    return rating_change(levels, rel, spread)


def _ci(xs):
    n = len(xs)
    if n == 0:
        return None, None
    m = sum(xs) / n
    if n < 2:
        return m, None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, 1.96 * sd / math.sqrt(n)


class Tally:
    """Search results of one plan, as they come in; ``rows`` ranks the options by the rating change."""

    def __init__(self, pl, levels=None):
        self.pl = pl
        self.scores = defaultdict(dict)
        self.native = [0, 0]
        self.seconds = 0.0
        lv = dict(levels or {})
        for st in pl["stages"]:
            if st.get("level"):
                lv.setdefault(st["step_id"], st["level"])
        self.levels = lv
        self.level_source = "recorded" if any(st.get("level") for st in pl["stages"]) else "search"

    def add(self, job, result):
        if (result or {}).get("status") != "ok":
            return
        self.scores[(job["stage"], job["option"])][int(job["k"])] = int(result["score"])
        self.native[0 if result.get("native") else 1] += 1
        self.seconds += float(result.get("seconds") or 0)

    def rating(self, levels, rel, spread=0.0):
        """The rating change of one seed."""
        return rating_delta(levels, rel, spread)

    def _levels(self, base_mean):
        lv = {}
        for st in ("AuditionMid1", "AuditionFinal"):
            lv[st] = self.levels.get(st) or (base_mean.get(st) and base_mean[st] * SEARCH_LEVEL[0])
        if lv["AuditionMid1"] is None and lv["AuditionFinal"] is not None:
            lv["AuditionMid1"] = 0
        if lv["AuditionFinal"] is None and lv["AuditionMid1"] is not None:
            lv["AuditionFinal"] = 0
        return lv

    def _whole(self, base, o, k, sts):
        """option ``o``'s rating minus the base's on seed slot ``k`` -- produce_effects.hif_rating of each whole 本戦 (the
        Mid1 score known once it is played, the stages ahead the search's own scores on that seed, a stage the option
        leaves alone the base's), the parameters and stars of the Final's loadout."""
        known = (self.pl.get("rebuild") or {}).get("known") or {}
        fin = next((s for s in self.pl["stages"] if s["step_id"] == "AuditionFinal"), None)
        if fin is None:
            return None
        prm = [int(fin["loadout"]["parameters"][x]) for x in ("1", "2", "3")]
        star = int(fin.get("star") or 0)

        level = float((self.pl.get("rebuild") or {}).get("level") or 1.0)

        def score(st, label):
            if st in base:
                got = self.scores.get((st, label), {}) if label != "base" and st in sts else base[st]
                x = got.get(k)
                return None if x is None else x * level
            return known.get(st) or 0
        b1, b2 = score("AuditionMid1", "base"), score("AuditionFinal", "base")
        o1, o2 = score("AuditionMid1", o["label"]), score("AuditionFinal", o["label"])
        if None in (b1, b2, o1, o2):
            return None
        sp = int((self.pl.get("rebuild") or {}).get("star_permil", 500))
        return (pe.hif_rating(prm, star, int(o1) * 12 // 10, int(o2), sp)
                - pe.hif_rating(prm, star, int(b1) * 12 // 10, int(b2), sp))

    def rows(self):
        stages = [s["step_id"] for s in self.pl["stages"]]
        base = {st: self.scores.get((st, "base"), {}) for st in stages}
        base_mean = {st: (sum(v.values()) / len(v) if v else None) for st, v in base.items()}
        whole = self.pl.get("rebuild") is not None
        lv = base_mean if whole else self._levels(base_mean)
        spread = 0.0 if whole else LEVEL_SPREAD if self.level_source == "recorded" else SEARCH_LEVEL[1]
        alias = (self.pl.get("swap") or {}).get("alias")
        out = []
        for o in self.pl["options"]:
            sts = [st for st in o["stages"] if st in stages]
            d_score = {}
            slots = None
            for st in sts:
                got = self.scores.get((st, (alias or {}).get(o["label"], o["label"])), {})
                d_score[st] = {k: got[k] - base[st][k] for k in got if k in base[st]}
                slots = set(d_score[st]) if slots is None else slots & set(d_score[st])
            per_seed = {}
            for k in sorted(slots or ()):
                if alias is not None:
                    b = sum(base[st][k] for st in sts)
                    per_seed[k] = 100.0 * sum(d_score[st][k] for st in sts) / b if b else 0.0
                    continue
                r = (self._whole(base, o, k, sts) if whole
                     else self.rating(lv, {st: d_score[st][k] / (base_mean[st] or 1) for st in sts}, spread))
                if r is not None:
                    per_seed[k] = r
            n = len(per_seed)
            m, ci = _ci(list(per_seed.values()))
            row = {"label": o["label"], "kind": o["kind"], "n": n,
                   "rating": None if m is None else round(m, 2 if alias is not None else 1),
                   "ci95": None if ci is None else round(ci, 2 if alias is not None else 1),
                   "score_delta": {st: round(sum(v.values()) / len(v)) for st, v in d_score.items() if v},
                   "score_pct": {st: round(100 * (sum(v.values()) / len(v)) / (base_mean[st] or 1), 2)
                                 for st, v in d_score.items() if v},
                   "per_seed": per_seed}
            for k in ("price", "index", "position", "resource", "removed"):
                if o.get(k) is not None:
                    row[k] = o[k]
            if alias is not None:
                row["unit"] = "pct"
            if o.get("price") and m is not None:
                row["per_100p"] = round(100 * m / o["price"], 1)
            out.append(row)
        drops = [r for r in out if r["kind"] == "drop" and r["rating"] is not None]
        for r in out:
            if r["kind"] == "drink":
                r["rating_alone"] = r["rating"]
        if drops:
            best_drop = max(drops, key=lambda r: r["rating"])
            for r in out:
                if r["kind"] == "drink" and r["rating"] is not None and len(self.pl["decision"]["drinks"]) >= DRINK_CAP:
                    r["with_drop"] = best_drop["resource"]
                    r["per_seed"] = {k: v + best_drop["per_seed"][k] for k, v in r["per_seed"].items()
                                     if k in best_drop["per_seed"]}
                    m, ci = _ci(list(r["per_seed"].values()))
                    r["rating"] = None if m is None else round(m, 1)
                    r["ci95"] = None if ci is None else round(ci, 1)
                    r["n"] = len(r["per_seed"])
                    if r.get("price") and m is not None:
                        r["per_100p"] = round(100 * m / r["price"], 1)
        out.sort(key=lambda r: (r["rating"] is None, -(r["rating"] or 0)))
        return {"rows": out, "levels": {k: (int(v) if v else v) for k, v in lv.items()},
                "level_source": "search_whole" if whole else self.level_source,
                "spread": spread, "base_mean": {k: (round(v) if v else None) for k, v in base_mean.items()},
                "native": self.native, "job_seconds": round(self.seconds, 1)}


def paired_gap(lead, other):
    """(mean, 95 % half width, n) of the leader's advantage over another option on their common seeds."""
    ks = sorted(set(lead["per_seed"]) & set(other["per_seed"]))
    m, ci = _ci([lead["per_seed"][k] - other["per_seed"][k] for k in ks])
    return m, ci, len(ks)


def separated(rows):
    """True when the leader's paired advantage over every other option (and over doing nothing,
    which is 0 on every seed) excludes 0."""
    rs = [r for r in rows if r.get("per_seed")]
    if not rs:
        return False
    lead = max(rs, key=lambda r: r["rating"] or 0)
    if len(lead["per_seed"]) < 8:
        return False
    m, ci = _ci(list(lead["per_seed"].values()))
    if ci is None or m - ci <= 0:
        return False
    for r in rs:
        if r is lead:
            continue
        m, ci, n = paired_gap(lead, r)
        if n < 8 or ci is None or m - ci <= 0:
            return False
    return True


# -- replay a recorded run (no search) ---------------------------------------------------------
def replay(frames_path, loadout_root, exclude_own=False):
    index = templates(loadout_root)
    run_id = Path(frames_path).name.split(".")[0]
    tr = Tracker()
    seen, out = set(), []
    for f in PL.read_frames(str(frames_path)):
        if not tr.feed(f):
            continue
        d = tr.decision()
        if d is None or d["key"] in seen:
            continue
        seen.add(d["key"])
        pl = plan(d, tr.current, index, exclude_run=run_id if exclude_own else None)
        row = {"seq": f.get("seq"), "kind": d["kind"], "produce_id": d["produce_id"], "step": d["step"],
               "uuid": (d["uuid"] or "")[:8], "candidates": [c.get("resource_id") or c.get("type") for c in d["candidates"]],
               "pp": d["produce_point"], "drinks": len(d["drinks"])}
        if pl:
            row.update(stages=[(s["step_id"], s["template_run"], s["level"]) for s in pl["stages"]],
                       options=[o["label"] for o in pl["options"]], notes=pl["notes"],
                       jobs_per_seed=len(jobs(pl, "k", 1, 2.0)))
        out.append(row)
    return out


def main(argv):
    if len(argv) >= 4 and argv[1] == "replay":
        rows = replay(argv[2], argv[3], exclude_own="--exclude-own" in argv)
        if "--json" in argv:
            json.dump(rows, open(argv[argv.index("--json") + 1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
