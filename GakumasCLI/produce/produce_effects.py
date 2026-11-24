# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""master-driven model of Produce-layer abilities (support cards, memories, P-items).

    python -B produce_effects.py card S_CARD_ID LEVEL           # the abilities of one support card at a level
    python -B produce_effects.py check ACTIVATIONS.jsonl OUT.json   # model vs wire (produce_activations.py)
    python -B produce_effects.py counts ACTIVATIONS.jsonl OUT.json  # trigger occasions vs measured counts
    python -B produce_effects.py profile ACTIVATIONS.jsonl OUT.json # scenario profile per produce id
    python -B produce_effects.py sp ACTIVATIONS.jsonl OUT.json      # SP rate sources, SP lessons offered
    python -B produce_effects.py rating ACTIVATIONS.jsonl OUT.json  # H.I.F 本戦 rating vs End grade
"""
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path

MASTER_DIR = Path(__file__).resolve().parents[1] / "ro" / "master" / "tables"
SUPPORT_LEVEL_TABLES = ("Vocal", "Dance", "Visual", "Assist")
COUNT_RE = re.compile(r"-(\d{4})_(\d{4})$")


def _rows(table, master=MASTER_DIR):
    with open(Path(master) / f"{table}.jsonl", encoding="utf-8") as fh:
        return [json.loads(line)["decoded"] for line in fh if line.strip()]


_DIFFICULTY = {}


def difficulty_id(idol_card_id, master=MASTER_DIR):
    """The idol card's audition difficulty row: IdolCard.produceStepAuditionDifficultyId."""
    key = str(master)
    if key not in _DIFFICULTY:
        _DIFFICULTY[key] = {r["id"]: r.get("produceStepAuditionDifficultyId") for r in _rows("IdolCard", master)}
    return _DIFFICULTY[key].get(idol_card_id) or "p_step_audition_difficulty-" + str(idol_card_id)


def _name(v, prefix=""):
    s = v["name"] if isinstance(v, dict) else (v or "")
    return s[len(prefix):] if prefix and s.startswith(prefix) else s


@dataclass
class Skill:
    id: str
    level: int
    phase: str
    cond: dict
    effect_id: str
    effect_type: str
    value: tuple
    resource: str
    rate_permil: int
    cap: int | None
    held: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)


class Model:
    def __init__(self, master=MASTER_DIR):
        self.master = Path(master)
        self.triggers = {r["id"]: _name(r.get("phaseType"), "PRODUCE_PHASE_TYPE_") for r in _rows("ProduceTrigger", master)}
        self.effects = {r["id"]: r for r in _rows("ProduceEffect", master)}
        self.skills = {(r["id"], r.get("level")): r for r in _rows("ProduceSkill", master)}
        self.searches = {r["id"] for r in _rows("ProduceCardSearch", master)}
        self.support = {r["id"]: r for r in _rows("SupportCard", master)}
        self.levels = defaultdict(lambda: defaultdict(list))
        for t in SUPPORT_LEVEL_TABLES:
            for r in _rows(f"SupportCardProduceSkillLevel{t}", master):
                self.levels[r["supportCardId"]][r["order"]].append(
                    (r["supportCardLevel"], r["produceSkillId"], r["produceSkillLevel"]))
        self.memory_abilities = {r["id"]: r for r in _rows("MemoryAbility", master)}
        self.items = {r["id"]: r for r in _rows("ProduceItem", master)}
        self.item_effects = {r["id"]: r for r in _rows("ProduceItemEffect", master)}

    # -- trigger ids --------------------------------------------------------------------------
    def _search(self, text):
        """Longest ProduceCardSearch id at the start of text."""
        best = None
        parts = text.split("-")
        for i in range(len(parts), 0, -1):
            cand = "-".join(parts[:i])
            if cand in self.searches:
                best = cand
                rest = "-".join(parts[i:])
                return best, rest
        return None, text

    @lru_cache(maxsize=None)
    def parse_trigger(self, trigger_id):
        phase = self.triggers.get(trigger_id, "UNKNOWN")
        cond, held = {}, ""
        if not trigger_id:
            return phase, cond, "no trigger"
        body = re.sub(r"^p_trigger-", "", trigger_id)
        body = re.sub(r"^[a-z_0-9]+?(?=-|$)", "", body, count=1).lstrip("-")
        if body in ("", "initial", "no_description"):
            return phase, cond, held
        m = re.fullmatch(r"lesson(?:_(vocal|dance|visual))?(?:_(sp|normal))?(?:-produce_card_count-(\d{4})_(\d{4}))?", body)
        if m:
            if m.group(1):
                cond["lesson_attr"] = m.group(1)
            if m.group(2):
                cond["lesson_kind"] = m.group(2)
            if m.group(3):
                cond["deck_min"] = int(m.group(3))
            return phase, cond, held
        if body == "for_hif_memory":
            return phase, {"hif_memory": True}, held
        if body.startswith("produce_card_search_count-"):
            rest = body[len("produce_card_search_count-"):]
            mm = COUNT_RE.search(rest)
            search, tail = self._search(rest[:mm.start()] if mm else rest)
            if search and mm and not tail:
                cond["search_count"] = {"search": search, "min": int(mm.group(1)), "max": int(mm.group(2))}
                return phase, cond, held
            return phase, cond, body
        rest = re.sub(r"^\d{4}_\d{4}-", "", body)
        if rest.startswith("p_card_search-"):
            search, tail = self._search(rest)
            if search and not tail:
                cond["card_search"] = search
                return phase, cond, held
            return phase, cond, body
        return phase, cond, body

    # -- skills -------------------------------------------------------------------------------
    def skill(self, skill_id, level, source=""):
        r = self.skills.get((skill_id, level))
        if r is None:
            return None
        phase, cond, held = self.parse_trigger(r.get("produceTriggerId1"))
        e = self.effects.get(r.get("produceEffectId1")) or {}
        return Skill(id=skill_id, level=level, phase=phase, cond=cond, effect_id=r.get("produceEffectId1") or "",
                     effect_type=_name(e.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_"),
                     value=(e.get("effectValueMin"), e.get("effectValueMax")),
                     resource=_name(e.get("produceResourceType"), "PRODUCE_RESOURCE_TYPE_"),
                     rate_permil=r.get("activationRatePermil1") or 1000, cap=r.get("activationCount"),
                     held=held, source=source)

    def support_skills(self, card_id, level):
        out = []
        for order, rows in sorted(self.levels.get(card_id, {}).items()):
            ok = [x for x in rows if x[0] <= level]
            if ok:
                _, sid, slv = max(ok)
                out.append((order, self.skill(sid, slv, f"support:{card_id}")))
        return out

    def memory_skill(self, ability_id, level=None):
        a = self.memory_abilities.get(ability_id)
        if a is None:
            return None
        return self.skill(a["skillId"], level or a.get("level"), f"memory_ability:{ability_id}")

    def item_skills(self, item_id):
        it = self.items.get(item_id)
        if it is None:
            return []
        phase, cond, held = self.parse_trigger(it.get("produceTriggerId"))
        out = []
        for eid in it.get("produceItemEffectIds") or ():
            ie = self.item_effects.get(eid) or {}
            pe = self.effects.get(ie.get("produceEffectId")) or {}
            if not pe:
                continue
            out.append(Skill(id=item_id, level=0, phase=phase, cond=cond, effect_id=pe["id"],
                             effect_type=_name(pe.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_"),
                             value=(pe.get("effectValueMin"), pe.get("effectValueMax")),
                             resource=_name(pe.get("produceResourceType"), "PRODUCE_RESOURCE_TYPE_"),
                             rate_permil=1000, cap=it.get("fireLimit") or None, held=held,
                             source=f"item:{item_id}", extra={"fire_interval": it.get("fireInterval")}))
        return out


# -- occasions and trigger counts -------------------------------------------------------------
LESSON_RE = re.compile(r"(?:open_lesson|lesson|self_lesson|legend_lesson)_(vo|da|vi)(_sp)?(_hard)?(?:_star)?$")
ATTR = {"vo": "vocal", "da": "dance", "vi": "visual"}
SCHOOL_STEPS = {"event_school", "event_school_vo", "event_school_da", "event_school_vi", "business"}
START_PHASE = {"shop": "START_SHOP", "refresh": "START_REFRESH", "fan_present": "START_PRESENT",
               "present": "START_PRESENT", "customize": "START_CUSTOMIZE"}
AUDITION = {"audition_mid1": "MID1", "audition_mid2": "MID2", "audition_final": "FINAL"}


def lesson_of(step_type):
    m = LESSON_RE.match(step_type or "")
    if not m:
        return None
    return ATTR[m.group(1)], "sp" if m.group(2) else ("hard" if m.group(3) else "normal")


class CardSearch:
    def __init__(self, master=MASTER_DIR):
        self.rows = {r["id"]: r for r in _rows("ProduceCardSearch", master)}
        self.cards = {}
        for r in _rows("ProduceCard", master):
            self.cards[(r["id"], r.get("upgradeCount") or 0)] = r
        self._memo = {}

    def match(self, search_id, card_id, upgrade=0):
        key = (search_id, card_id, upgrade or 0)
        hit = self._memo.get(key)
        if hit is None:
            hit = self._memo[key] = self._match(search_id, card_id, upgrade)
        return hit

    def _match(self, search_id, card_id, upgrade=0):
        s = self.rows.get(search_id)
        c = self.cards.get((card_id, upgrade or 0)) or self.cards.get((card_id, 0))
        if s is None or c is None:
            return False
        if s.get("cardCategories") and _name(c.get("category")) not in {_name(x) for x in s["cardCategories"]}:
            return False
        if s.get("cardRarities") and _name(c.get("rarity")) not in {_name(x) for x in s["cardRarities"]}:
            return False
        if s.get("effectGroupIds") and not set(s["effectGroupIds"]) & set(c.get("effectGroupIds") or ()):
            return False
        if s.get("cardSearchTag") and c.get("searchTag") != s["cardSearchTag"]:
            return False
        if s.get("produceCardIds") and card_id not in s["produceCardIds"]:
            return False
        if s.get("planType") and _name(c.get("planType")) != _name(s["planType"]):
            return False
        return True


CHANGE_EFFECTS = {"PRODUCE_EFFECT_TYPE_PRODUCE_CARD_CHANGE", "PRODUCE_EFFECT_TYPE_PRODUCE_CARD_CHANGE_SELECT",
                  "PRODUCE_EFFECT_TYPE_PRODUCE_CARD_CHANGE_UPGRADE"}


def events_of(produce, effect_types=None):
    """(phase, attrs) per occasion of one produce_activations record, in order."""
    out, school_steps, open_lesson = [], set(), None
    by_src = {}
    for a in produce.get("activations") or []:
        if a["origin_type"] not in (3, 4):
            by_src.setdefault(a["src"], set()).add((effect_types or {}).get(a["effect_id"], a["effect_type"]))
    for o in produce.get("occasions") or []:
        rpc, deck = o["rpc"], o.get("deck") or []
        at = o.get("at_type") or o.get("step_type")
        base = {"step": o.get("at_step", o.get("step")), "deck": deck}
        if rpc == "Start":
            out.append(("PRODUCE_START", base))
            for i in o.get("items_gained") or []:
                out.append(("GET_PRODUCE_ITEM", dict(base, item=i)))
            continue
        lk = lesson_of(at)
        if lk and rpc.endswith("LessonStart"):
            out.append(("START_LESSON", dict(base, attr=lk[0], kind=lk[1])))
            open_lesson = (base["step"], lk)
        ended = open_lesson[1] if open_lesson and (rpc.endswith("LessonEnd") or o.get("step") != open_lesson[0]) else None
        if lk and rpc.endswith("LessonEnd"):
            ended = lk
        if ended:
            out.append(("END_LESSON", dict(base, attr=ended[0], kind=ended[1])))
            out.append(("END_LESSON_BEFORE_PRESENT", dict(base, attr=ended[0], kind=ended[1])))
            open_lesson = None
        if rpc == "Next" and o.get("selected") in START_PHASE:
            out.append((START_PHASE[o["selected"]], base))
        if at in SCHOOL_STEPS and base["step"] not in school_steps and rpc != "Next":
            school_steps.add(base["step"])
            out.append(("END_STEP_EVENT_SCHOOL", base))
        if rpc == "Next" and o.get("selected") == "event_activity":
            out.append(("END_STEP_EVENT_ACTIVITY", base))
        if rpc == "StepAuditionStart" and at in AUDITION:
            out.append(("START_AUDITION", base))
            out.append(("START_AUDITION_" + AUDITION[at], base))
        if rpc == "StepAuditionEnd" and at in AUDITION:
            out.append(("END_AUDITION", base))
            out.append(("END_AUDITION_" + AUDITION[at], base))
        changes = o.get("card_changes") or []
        new = [c for c in changes if c["kind"] == "new"]
        gone = [c for c in changes if c["kind"] == "deleted"]
        ctx = by_src.get(o["src"], set())
        if ctx & CHANGE_EFFECTS or (not ctx and rpc in ("StepPresentReceive", "Next", "StepIntervalBuy")):
            paired = min(len(new), len(gone))
        else:
            paired = 0
        for c in gone[:paired]:
            out.append(("CHANGE_PRODUCE_CARD", dict(base, card=c["before_id"] or c["id"], upgrade=c.get("upgrade"))))
        for c in new[paired:]:
            out.append(("GET_PRODUCE_CARD", dict(base, card=c["id"], upgrade=c.get("upgrade"))))
            if rpc in ("StepShopBuy", "StepIntervalBuy"):
                out.append(("BUY_SHOP_ITEM_PRODUCE_CARD", dict(base, card=c["id"], upgrade=c.get("upgrade"))))
        for c in gone[paired:]:
            out.append(("DELETE_PRODUCE_CARD", dict(base, card=c["before_id"] or c["id"], upgrade=c.get("upgrade"))))
        for c in changes:
            if c["kind"] == "upgraded":
                out.append(("UPGRADE_PRODUCE_CARD", dict(base, card=c["id"], upgrade=c.get("upgrade"))))
            elif c["kind"] == "changed":
                out.append(("CHANGE_PRODUCE_CARD", dict(base, card=c["before_id"] or c["id"], upgrade=c.get("upgrade"))))
            elif c["kind"] == "customized":
                out.append(("CUSTOMIZE_PRODUCE_CARD", dict(base, card=c["id"], upgrade=c.get("upgrade"))))
        for d in o.get("drinks_gained") or []:
            out.append(("GET_PRODUCE_DRINK", dict(base, drink=d)))
            if rpc in ("StepShopBuy", "StepIntervalBuy"):
                out.append(("BUY_SHOP_ITEM_PRODUCE_DRINK", dict(base, drink=d)))
        for i in o.get("items_gained") or []:
            out.append(("GET_PRODUCE_ITEM", dict(base, item=i)))
    return out


def fires(skill, phase, ev, cs):
    if skill.phase != phase:
        return False
    c = skill.cond
    if "lesson_attr" in c and ev.get("attr") != c["lesson_attr"]:
        return False
    if c.get("lesson_kind") == "sp" and ev.get("kind") != "sp":
        return False
    if c.get("lesson_kind") == "normal" and ev.get("kind") == "sp":
        return False
    if "deck_min" in c and len(ev["deck"]) < c["deck_min"]:
        return False
    if "card_search" in c and not cs.match(c["card_search"], ev.get("card"), ev.get("upgrade")):
        return False
    if "search_count" in c:
        n = sum(cs.match(c["search_count"]["search"], cid, up) for cid, up in ev["deck"])
        if n < c["search_count"]["min"] or (c["search_count"]["max"] and n > c["search_count"]["max"]):
            return False
    return True


def occasions_count(skill, events, cs):
    """How many times the skill's trigger condition is met (cap applied, rate not)."""
    n = sum(fires(skill, ph, ev, cs) for ph, ev in events)
    return min(n, skill.cap) if skill.cap else n


def coverage(produce):
    """full = the frames hold the produce's Start and End and every step in between."""
    occ = produce.get("occasions") or []
    steps = {o.get("at_step") for o in occ} | {o.get("step") for o in occ}
    last = max([x for x in steps if x] or [0])
    return ("full" if any(o["rpc"] == "Start" for o in occ) and any(o["rpc"] == "End" for o in occ)
            and all(i in steps for i in range(1, last + 1)) else "partial")


def counts(activations_path, out_path, master=MASTER_DIR):
    """Per produce and support / memory skill: how often its trigger condition was met in the
    produce's own occasions (`occasions`, cap applied; `expected` = occasions x rate) against
      summary  the 選抜 End's trigger_count -- the reference, for a fully covered produce;
      wire     effect_results rows -- a lower bound only: the server does not report every
               activation as a row."""
    M, cs = Model(master), CardSearch(master)
    etypes = {r["id"]: _name(r.get("produceEffectType")) for r in _rows("ProduceEffect", master)}
    rows = []
    for p in (json.loads(line) for line in open(activations_path, encoding="utf-8")):
        ev, cov = events_of(p, etypes), coverage(p)
        fired = {}
        for a in p.get("activations") or []:
            if a["origin_type"] in (3, 4) and a.get("origin_id"):
                key = (a["owner"], a["origin_id"])
                fired.setdefault(key, [0, a.get("origin_level")])[0] += 1
        summary = {}
        for sc in (p.get("summary") or {}).get("support_cards") or []:
            for s in sc.get("produce_skills") or []:
                summary[(sc["support_card_id"], s["id"])] = (s.get("trigger_count", 0), s.get("level"))
        for owner, sid in sorted(set(fired) | set(summary)):
            level = (summary.get((owner, sid)) or (None, None))[1] or fired.get((owner, sid), [0, None])[1]
            sk = M.skill(sid, level)
            if sk is None:
                continue
            n = occasions_count(sk, ev, cs)
            ref = (summary.get((owner, sid)) or (None,))[0] if cov == "full" else None
            wire = fired.get((owner, sid), [0])[0]
            verdict = ("exact" if n == ref else "DIFF") if ref is not None and sk.rate_permil == 1000 else (
                "bound_ok" if wire <= n else "BOUND_FAIL")
            rows.append({"produce": p["uuid"], "produce_id": p.get("produce_id"), "coverage": cov,
                         "owner": owner, "skill": sid, "level": level, "phase": sk.phase, "cond": sk.cond,
                         "cap": sk.cap, "rate": sk.rate_permil, "occasions": n,
                         "expected": round(n * sk.rate_permil / 1000, 3), "wire": wire,
                         "summary": (summary.get((owner, sid)) or (None,))[0], "verdict": verdict})
    json.dump(rows, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return rows


# -- scenario profiles ------------------------------------------------------------------------
def _phase_key(ph, ev):
    return f"{ph}:{ev['attr']}:{ev['kind']}" if "attr" in ev else ph


def profile(activations_path, master=MASTER_DIR):
    """Scenario profile per produce id from the fully covered produces: the schedule (offered and
    taken step types per step), every occasion event with what a trigger condition can read (step,
    card / drink / item, deck as [id, upgrade]), and per-phase counts with min / mean / max over
    the produces.
    """
    from GakumasCLI.produce.produce_ledger import STEP_TYPE
    from collections import Counter
    etypes = {r["id"]: _name(r.get("produceEffectType")) for r in _rows("ProduceEffect", master)}
    out = {}
    for p in (json.loads(line) for line in open(activations_path, encoding="utf-8")):
        if coverage(p) != "full":
            continue
        ev = events_of(p, etypes)
        sched = [{"step": r.get("step_number"), "offered": [STEP_TYPE.get(t, t) for t in r.get("step_types") or []],
                  "taken": STEP_TYPE.get(r.get("selected_step_type"), r.get("selected_step_type"))}
                 for r in p.get("schedule") or []]
        counts_ = Counter(_phase_key(ph, e) for ph, e in ev)
        out.setdefault(p.get("produce_id"), {"produces": []})["produces"].append({
            "uuid": p["uuid"], "idol_card_id": p.get("idol_card_id"), "plan_type": (p.get("summary") or {}).get("plan_type"),
            "schedule": sched, "counts": dict(sorted(counts_.items())),
            "events": [dict({"phase": ph}, **{k: v for k, v in e.items()}) for ph, e in ev]})
    for pid, d in out.items():
        keys = sorted({k for q in d["produces"] for k in q["counts"]})
        d["counts"] = {k: {"min": min(q["counts"].get(k, 0) for q in d["produces"]),
                           "mean": round(sum(q["counts"].get(k, 0) for q in d["produces"]) / len(d["produces"]), 2),
                           "max": max(q["counts"].get(k, 0) for q in d["produces"])} for k in keys}
    return out


# -- SP lessons --------------------------------------------------------------------------------
SP_ALL = "PRODUCE_EFFECT_TYPE_LESSON_SP_CHANGE_RATE_PERMIL_ADDITION"


def sp_check(activations_path, master=MASTER_DIR):
    """Per 選抜 produce: the SP rate per attribute built from the Start response's rate additions
    (idol card, support cards, growth panel) against the summary's final rate, and the SP lessons
    the schedule offered against offers x rate."""
    from GakumasCLI.produce.produce_ledger import STEP_TYPE
    etypes = {r["id"]: _name(r.get("produceEffectType")) for r in _rows("ProduceEffect", master)}
    out = []
    for p in (json.loads(line) for line in open(activations_path, encoding="utf-8")):
        s = p.get("summary") or {}
        if p.get("summary_kind") != "selection_memory":
            continue
        add, src = dict.fromkeys(("vocal", "dance", "visual"), 0), []
        for a in p.get("activations") or []:
            t = etypes.get(a["effect_id"], "")
            if "SP_CHANGE_RATE_PERMIL" not in t:
                continue
            for k in add:
                if t == SP_ALL or k.upper() in t:
                    add[k] += int(a.get("value") or 0)
            src.append({"origin_type": a["origin_type"], "owner": a["owner"], "type": t, "value": a.get("value")})
        final = {k: s.get(f"lesson_{k}_sp_change_rate_permil") for k in add}
        offered = {k: [0, 0] for k in add}
        for st in s.get("schedules") or []:
            for t in st.get("step_types") or []:
                lk = lesson_of(STEP_TYPE.get(t))
                if lk:
                    offered[lk[0]][0] += 1
                    offered[lk[0]][1] += lk[1] == "sp"
        out.append({"produce": p["uuid"], "start_in_frames": any(o["rpc"] == "Start" for o in p["occasions"]),
                    "rate_final": final, "rate_from_start": add, "sources": src,
                    "offered": {k: {"lessons": v[0], "sp": v[1], "expected_sp": round(v[0] * (final[k] or 0) / 1000, 2)}
                                for k, v in offered.items()}})
    return out


# -- H.I.F 本戦 rating ------------------------------------------------------------------------
# Community formula: surisuririsu/gakumas-tools utils/hif.js (constants copied as published);
# checked here against the End response's grade and the star the final audition paid out.
HIF_ROUND1 = [(300000, 0, 0), (700000, 0, 0.01), (1000000, 4000, 0.003), (1200000, 4900, 0.002),
              (1400000, 5300, 0.001), (float("inf"), 5500, 0)]
HIF_ROUND2 = [(600000, 0, 0), (900000, 0, 0.004), (1500000, 1200, 0.008), (2000000, 6000, 0.002),
              (2400000, 7000, 0.001), (float("inf"), 7400, 0)]
HIF_STAR_GAIN = [(400000, 0, 0.0001875), (600000, 75, 0.000225), (1000000, 120, 0.000075), (float("inf"), 150, 0)]
SELECTION_STAR_LINE = {"mid1": 14001, "mid2": 146364, "final": 390910}
SELECTION_STAR_BASE = {"mid1": 40, "mid2": 110, "final": 110}
STAR_BONUS_LINE = {"sel1": (50, 70), "sel2": (200, 145), "sel3": (400, 215), "main1": (600, 270), "main2": (800, 325)}


def star_bonus(stage, star):
    import math
    from fractions import Fraction
    bl, w = STAR_BONUS_LINE[stage]
    v = Fraction(200) + Fraction(800 * int(star), bl) if int(star) < bl else Fraction(1000) + Fraction(1000 * (int(star) - bl), w)
    return min(2000, math.ceil(v))


RANK_RATING = [("S5", 35000), ("S4+", 30000), ("S4", 26000), ("SSS+", 23000), ("SSS", 20000), ("SS+", 18000),
               ("SS", 16000), ("S+", 14500), ("S", 13000), ("A+", 11500), ("A", 10000), ("B+", 8000),
               ("B", 6000), ("C+", 4500), ("C", 3000)]
GRADE = {8: "A", 9: "A+", 10: "S", 11: "S+", 12: "SS", 13: "SS+", 14: "SSS", 15: "SSS+", 16: "S4", 17: "S4+",
         18: "S5", 19: "S5+"}


def _piecewise(v, regimes):
    prev = 0
    for th, base, mul in regimes:
        if v <= th:
            return base + (v - prev) * mul
        prev = th
    return 0


def hif_star_gain(round2, star_permil=500):
    """The stars the 本戦 Final pays for its score: ceil(hif.js's piecewise value) x (1000 + star_permil) / 1000, floored."""
    import math
    return math.ceil(_piecewise(round2, HIF_STAR_GAIN)) * (1000 + int(star_permil)) // 1000


def hif_rating(params, pre_round2_star, round1, round2, star_permil=500):
    """The H.I.F rating (the End response's user_produce_progress.produce_score): ``params`` at the 本戦 end, the stars
    before the Final, the Mid1 point (score x 1.2) and the Final score.
    """
    import math
    stats = sum(min(3200, max(0, x or 0)) for x in params)
    ps = math.floor(stats * 2 + (hif_star_gain(round2, star_permil) + min(1110, max(0, pre_round2_star))) * 7.5)
    r1 = math.floor(_piecewise(math.floor(round1 / 1.2), HIF_ROUND1))
    r2 = math.floor(_piecewise(round2, HIF_ROUND2))
    return ps + r1 + r2 - 2000


HIF_FULL_SCORES = (1_400_000, 2_400_000)


def hif_rating_full(params, pre_round2_star, star_permil=500):
    """The H.I.F rating with both 本戦 auditions at HIF_FULL_SCORES (hif_rating)."""
    r1, r2 = HIF_FULL_SCORES
    return hif_rating(params, pre_round2_star, r1 * 12 // 10, r2, star_permil)


def rank_of(rating):
    return next((r for r, t in RANK_RATING if rating >= t), "-")


def rating_check(activations_path):
    """H.I.F 本戦 (produce-008) produces with both auditions: the last attempt of each audition,
    parameters and star before the final, the rating, and the End grade."""
    out = []
    for p in (json.loads(line) for line in open(activations_path, encoding="utf-8")):
        if p.get("produce_id") != "produce-008":
            continue
        last = {}
        for a in p.get("auditions") or []:
            last[a["step_type"]] = a
        if "audition_mid1" not in last or "audition_final" not in last:
            continue
        r1, fin = last["audition_mid1"], last["audition_final"]
        params = [fin["before"][k] for k in ("vocal", "dance", "visual")]
        point1 = (r1.get("server") or {}).get("point") or r1["score"] * 12 // 10
        rating = hif_rating(params, fin["before"]["star"], point1, fin["score"])
        grade = (p.get("summary") or {}).get("grade")
        star_end = (p.get("last") or {}).get("star")
        out.append({"produce": p["uuid"], "round1": r1["score"], "round1_point": point1, "round2": fin["score"],
                    "params_before_final": params, "star_before_final": fin["before"]["star"],
                    "star_gain_formula": hif_star_gain(fin["score"]),
                    "star_gain_measured": None if star_end is None else star_end - fin["before"]["star"],
                    "rating": rating, "rank": rank_of(rating), "grade": grade, "grade_name": GRADE.get(grade),
                    "attempts": {k: sum(a["step_type"] == k for a in p["auditions"]) for k in last}})
    return out


# -- model vs wire ----------------------------------------------------------------------------
def check(activations_path, out_path, master=MASTER_DIR):
    M = Model(master)
    produces = [json.loads(line) for line in open(activations_path, encoding="utf-8")]
    res = {"support_level_skills": [0, 0, []], "support_trigger_counts": [], "activation_values": [0, 0, []],
           "memory_abilities": [0, 0, []], "held_triggers": {}}
    for p in produces:
        for sc in (p.get("summary") or {}).get("support_cards") or []:
            model = {s.id: s.level for _, s in M.support_skills(sc["support_card_id"], sc.get("level") or 0) if s}
            wire = {s["id"]: s.get("level") for s in sc.get("produce_skills") or []}
            res["support_level_skills"][1] += 1
            if model == wire:
                res["support_level_skills"][0] += 1
            else:
                res["support_level_skills"][2].append({"produce": p["uuid"], "card": sc["support_card_id"],
                                                       "level": sc.get("level"),
                                                       "only_model": sorted(set(model.items()) - set(wire.items())),
                                                       "only_wire": sorted(set(wire.items()) - set(model.items()))})
            for s in sc.get("produce_skills") or []:
                res["support_trigger_counts"].append({"produce": p["uuid"], "card": sc["support_card_id"],
                                                      "skill": s["id"], "level": s.get("level"),
                                                      "trigger_count": s.get("trigger_count", 0)})
        for a in p.get("activations") or []:
            if a["origin_type"] not in (3, 4) or not a.get("origin_id"):
                continue
            sk = M.skill(a["origin_id"], a.get("origin_level"))
            res["activation_values"][1] += 1
            ok = sk is not None and sk.effect_id == a.get("effect_id") and (
                a.get("value") is None or sk.value[0] == a["value"] == sk.value[1])
            if ok:
                res["activation_values"][0] += 1
            elif len(res["activation_values"][2]) < 30:
                res["activation_values"][2].append({"wire": a, "model": asdict(sk) if sk else None})
        for mem in (p.get("summary") or {}).get("memories") or []:
            for ab in (mem.get("memory") or {}).get("abilities") or []:
                sk = M.memory_skill(ab["id"], ab.get("level"))
                res["memory_abilities"][1] += 1
                if sk is not None:
                    res["memory_abilities"][0] += 1
                else:
                    res["memory_abilities"][2].append(ab)
    held = {}
    for card, orders in M.levels.items():
        for rows in orders.values():
            for _, sid, slv in rows:
                sk = M.skill(sid, slv)
                if sk and sk.held:
                    held[sk.held] = held.get(sk.held, 0) + 1
    for aid in M.memory_abilities:
        sk = M.memory_skill(aid)
        if sk and sk.held:
            held[sk.held] = held.get(sk.held, 0) + 1
    res["held_triggers"] = held
    json.dump(res, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return res


def main(argv):
    if argv[1] == "card":
        M = Model()
        for order, s in M.support_skills(argv[2], int(argv[3])):
            print(order, json.dumps(asdict(s), ensure_ascii=False))
    elif argv[1] == "counts":
        from collections import Counter
        rows = counts(argv[2], argv[3])
        print(json.dumps({f"{c}/{v}": n for (c, v), n in sorted(Counter((r["coverage"], r["verdict"]) for r in rows).items())}))
        for r in rows:
            if r["verdict"] in ("DIFF", "BOUND_FAIL"):
                print(r["verdict"], r["produce"][:8], r["owner"], r["skill"], r["occasions"], r["wire"], r["summary"])
    elif argv[1] == "profile":
        res = profile(argv[2])
        json.dump(res, open(argv[3], "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        for pid, d in sorted(res.items()):
            print(pid, len(d["produces"]), {k: v["mean"] for k, v in d["counts"].items()})
    elif argv[1] == "sp":
        res = sp_check(argv[2])
        json.dump(res, open(argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for r in res:
            print(r["produce"][:8], "start" if r["start_in_frames"] else "no-start", r["rate_final"],
                  r["rate_from_start"], {k: (v["lessons"], v["sp"], v["expected_sp"]) for k, v in r["offered"].items()})
    elif argv[1] == "rating":
        res = rating_check(argv[2])
        json.dump(res, open(argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for r in res:
            print(r["produce"][:8], r["rating"], r["rank"], "grade", r["grade"], r["grade_name"],
                  "star gain", r["star_gain_formula"], r["star_gain_measured"])
    elif argv[1] == "check":
        r = check(argv[2], argv[3])
        print(json.dumps({"support_level_skills": r["support_level_skills"][:2],
                          "activation_values": r["activation_values"][:2],
                          "memory_abilities": r["memory_abilities"][:2],
                          "support_trigger_rows": len(r["support_trigger_counts"]),
                          "held_triggers": r["held_triggers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv)
