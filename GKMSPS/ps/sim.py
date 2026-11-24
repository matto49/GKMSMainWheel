# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The H.I.F produce simulator (produce-007)."""
from __future__ import annotations

import copy
import hashlib
import json

from GKMSPS.ps import seed as seedmod
from GKMSPS.ps.data import ATTRS, PARAM_TYPE, data, name
from GKMSPS.ps.main import MainMixin
from GKMSPS.ps.rng import LABELS_VERSION, Rng

SELECTION, MAIN = "produce-007", "produce-008"
FORMAT = "gkms.ps.produce.v1"
TROUBLE = "p_card-00-acc-0_002"
WAPPEN = "pitem_00-3-265-0"
SHORT = {"vocal": "vo", "dance": "da", "visual": "vi"}
LABEL = {"vocal": "Vo", "dance": "Da", "visual": "Vi"}
STEP_TAIL = {"mid1": "AUDITION_MID1", "mid2": "AUDITION_MID2", "final": "AUDITION_FINAL"}
STAGE = {(SELECTION, "mid1"): "sel1", (SELECTION, "mid2"): "sel2", (SELECTION, "final"): "sel3",
         (MAIN, "mid1"): "main1", (MAIN, "final"): "main2"}
# 選抜 day plan; every day but an audition also offers 休む (refresh)
SELECTION_DAYS = {
    1: ("shop", "present", "customize"), 2: ("lesson",), 3: ("school",), 4: ("lesson",), 5: ("activity", "shop"),
    6: ("school",), 7: ("audition:mid1",), 8: ("activity", "present"), 9: ("lesson",), 10: ("school",),
    11: ("lesson",), 12: ("shop", "customize"), 13: ("audition:mid2",), 14: ("activity", "present"), 15: ("lesson",),
    16: ("activity", "shop", "present"), 17: ("school",), 18: ("lesson",), 19: ("shop", "customize"),
    20: ("audition:final",)}
# 本戦 step plan per-visit shop price effects: (what they discount, +1 discount / -1 price rise)
SHOP_VISIT = {"SHOP_PRICE_DISCOUNT_MULTIPLE": ("all", 1), "SHOP_PRICE_UP_MULTIPLE": ("all", -1),
              "SHOP_PRODUCE_CARD_PRICE_DISCOUNT_MULTIPLE": ("card", 1),
              "SHOP_PRODUCE_DRINK_PRICE_DISCOUNT_MULTIPLE": ("drink", 1),
              "SHOP_PRODUCE_CARD_UPGRADE_PRICE_DISCOUNT_MULTIPLE": ("upgrade", 1),
              "SHOP_PRODUCE_CARD_DELETE_PRICE_DISCOUNT_MULTIPLE": ("delete", 1)}
MAIN_DAYS = {1: ("school",), 2: ("lesson",), 3: ("activity", "present"), 4: ("school",), 5: ("lesson",), 6: ("shop",),
             7: ("audition:mid1",), 8: ("interval",), 9: ("audition:final",)}
MAIN_LESSON_DAY = {2: ("04", "star"), 5: ("04", "parameter")}
# the 応援棒 the 本戦 hands out after its first audition, by the idol's exam type
CHEER_ITEM = {"PRODUCE_EXAM_EFFECT_TYPE_EXAM_PARAMETER_BUFF": "pitem_01-3-266-0",
              "PRODUCE_EXAM_EFFECT_TYPE_EXAM_LESSON_BUFF": "pitem_01-3-267-0",
              "PRODUCE_EXAM_EFFECT_TYPE_EXAM_REVIEW": "pitem_02-3-268-0",
              "PRODUCE_EXAM_EFFECT_TYPE_EXAM_CARD_PLAY_AGGRESSIVE": "pitem_02-3-269-0",
              "PRODUCE_EXAM_EFFECT_TYPE_EXAM_CONCENTRATION": "pitem_03-3-270-0",
              "PRODUCE_EXAM_EFFECT_TYPE_EXAM_FULL_POWER": "pitem_03-3-271-0"}
MAIN_MID1 = {"star_raw": 120, "star_line": 500000, "pp": 200, "pp_line": 400000}


def main_mid1_reward(score):
    """(star before the bonus, P points) of 本戦 Round 1 for its score."""
    sc = max(0, int(score))
    raw, line = MAIN_MID1["star_raw"], MAIN_MID1["star_line"]
    star = min(raw, -(-(raw * (line + sc)) // (2 * line)))
    pp = min(MAIN_MID1["pp"], -(-(125 * MAIN_MID1["pp_line"] + 75 * sc) // MAIN_MID1["pp_line"]))
    return star, pp
# lesson tier (ProduceStepOpenLesson id) and kind per lesson day; star lessons on 2 / 9 / 15
LESSON_DAY = {2: ("01", "star"), 4: ("01", "parameter"), 9: ("02", "star"), 11: ("02", "parameter"),
              15: ("03", "star"), 18: ("03", "parameter")}
SCHOOL_STAGE = {3: "before_1st", 6: "before_1st", 10: "before_3rd", 17: "before_3rd"}
ACTIVITY_STAGE = {5: "before_2nd", 8: "before_2nd", 14: "before_3rd", 16: "before_3rd"}
SHOP_DAY_KIND = {5: "5", 12: "12", 16: "16", 19: "19", 1: "5"}
SCHOOL_SUB = {"PLAN1": ("parameter_buff", "lesson_buff"), "PLAN2": ("review", "card_play_aggressive"),
              "PLAN3": ("concentration", "full_power")}
SELECTION_REWARD = {"mid1": (20, 80), "mid2": (80, 200), "final": (100, 220)}
SELECTION_STAR_LINE = {"mid1": 14001, "mid2": 146364, "final": 390910}
SELECTION_STAR_BASE = {"mid1": 40, "mid2": 110, "final": 110}
STAR_FIT = (577, 417)
LESSON_PP = {SELECTION: 30, MAIN: 50}
PRESENT_PP = 80
ADD = {"VOCAL_ADDITION": "vocal", "DANCE_ADDITION": "dance", "VISUAL_ADDITION": "visual"}
GROWTH = {"VOCAL_GROWTH_RATE_ADDITION": "vocal", "DANCE_GROWTH_RATE_ADDITION": "dance",
          "VISUAL_GROWTH_RATE_ADDITION": "visual"}
SP_ADD = {"LESSON_VOCAL_SP_CHANGE_RATE_PERMIL_ADDITION": ("vocal",),
          "LESSON_DANCE_SP_CHANGE_RATE_PERMIL_ADDITION": ("dance",),
          "LESSON_VISUAL_SP_CHANGE_RATE_PERMIL_ADDITION": ("visual",),
          "LESSON_SP_CHANGE_RATE_PERMIL_ADDITION": ATTRS}
ENCHANTS = ("EXAM_STATUS_ENCHANT", "EXAM_PERMANENT_AUDITION_STATUS_ENCHANT", "EXAM_PERMANENT_LESSON_STATUS_ENCHANT")


class SimError(ValueError):
    """An action the current decision does not offer, or a formation the master cannot build."""


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ruleset():
    from GakumasCLI.native.diffrun import MIN_EXAM_RULES
    d = data()
    return {"master": d.extra["source"]["commit"][:12], "exam_rules": int(MIN_EXAM_RULES), "ps_rules": seedmod.PS_RULES,
            "labels": LABELS_VERSION}


def selection_star(stage, score, star_permil):
    line, base = SELECTION_STAR_LINE[stage], SELECTION_STAR_BASE[stage]
    raw = base if score >= line else -(-base * (STAR_FIT[0] * line + STAR_FIT[1] * max(0, score)) // (1000 * line))
    return raw * (1000 + star_permil) // 1000


def selection_reward(f, r, scores):
    """final 256385 / 11910 / 187145: 123.9 / 5.8 / 90.4 floored to 123 / 5 / 90, 2 left for Vo and Vi: 224 / 105 / 191."""
    tot = sum(scores)
    if tot <= 0:
        scores, tot = [1, 1, 1], 3
    part = [r * x // tot for x in scores]
    left = r - sum(part)
    for i in sorted(range(3), key=lambda i: (-scores[i], i))[:left]:
        part[i] += 1
    return [f + x for x in part]


class Sim(MainMixin):
    # -- construction ----------------------------------------------------------------------------------------------
    def __init__(self, formation, *, timestamp_ms=None, root=None, engine=False, _state=None):
        self.d = data()
        self.trace = []
        if _state is not None:
            self.s = _state
            self.root = bytes.fromhex(_state["root"])
            self.rng = Rng(self.root, _state["rng"])
            self.exam = None
            if self.s["queue"] and self.s["queue"][0]["t"] == "exam":
                self._rebuild_exam()
            return
        formation = self._complete(formation)
        doc = seedmod.formation_document(formation, ruleset(), int(timestamp_ms or 0))
        self.root = seedmod.parse_root(root) if isinstance(root, str) else (root or seedmod.root_of(doc))
        self.rng = Rng(self.root)
        self.exam = None
        self.s = {"format": FORMAT, "root": self.root.hex(), "short": seedmod.short_code(self.root),
                  "fixed_root": root is not None, "document": doc, "formation": formation, "engine": bool(engine),
                  "actions": [], "log": [], "queue": []}
        self._start_selection()
        self._run()

    @classmethod
    def from_json(cls, text):
        st = json.loads(text) if isinstance(text, str) else copy.deepcopy(text)
        if st.get("format") != FORMAT:
            raise SimError("不是 PS 培育存档")
        return cls(None, _state=st)

    def to_json(self):
        st = dict(self.s)
        st["rng"] = self.rng.state()
        return _canon(st)

    def state_hash(self):
        st = {k: v for k, v in self.s.items() if k not in ("log", "ui")}
        st["rng"] = self.rng.state()
        return hashlib.sha256(_canon(st).encode("utf-8")).hexdigest()

    def _complete(self, f):
        f = copy.deepcopy(f)
        acc = self.d.default_account()
        a = f.get("account") or {}
        acc.update({k: a[k] for k in ("producer_level", "dearness_level", "true_ends") if k in a})
        acc["growth_panel"].update(a.get("growth_panel") or {})
        f["account"] = acc
        f.setdefault("memories", [])
        if f["idol"]["id"] not in self.d.idols.cards:
            raise SimError(f"主数据里没有偶像卡 {f['idol']['id']}")
        for sc in f["supports"]:
            if sc["id"] not in self.d.support_cards:
                raise SimError(f"主数据里没有支援卡 {sc['id']}")
        return f

    # -- logging and small helpers ---------------------------------------------------------------------------------
    def log(self, text, **kw):
        self.s["log"].append(dict(kw, step=self.s.get("step"), text=text))

    @property
    def st(self):
        return self.s

    def _add_param(self, attr, n):
        s = self.s
        before = s["params"][attr]
        s["params"][attr] = max(0, min(s["limit"], before + int(n)))
        return s["params"][attr] - before

    def _add_stamina(self, n):
        s = self.s
        s["stamina"] = max(0, min(s["max_stamina"], s["stamina"] + int(n)))

    def _add_star(self, raw):
        gain = int(raw) * (1000 + self.s["star_permil"]) // 1000
        self.s["star"] += gain
        return gain

    def _push(self, *tasks):
        """Tasks to run next, in this order, before anything already queued."""
        self.s["queue"][0:0] = list(tasks)

    def _deck(self):
        return [c for c in self.s["deck"] if not c.get("deleted")]

    def _card_by_number(self, n):
        for c in self.s["deck"]:
            if c["n"] == n and not c.get("deleted"):
                return c
        raise SimError(f"牌组里没有编号 {n} 的卡")

    def _new_card(self, cid, up=0, *, from_memory=False, customizes=None, fire=True):
        s = self.s
        s["next_card"] += 1
        c = {"n": s["next_card"], "id": cid, "up": int(up)}
        if customizes:
            c["cz"] = [list(x) for x in customizes]
        if from_memory:
            c["memory"] = True
        s["deck"].append(c)
        if fire:
            self._push({"t": "fire", "phase": "GET_PRODUCE_CARD", "ev": {"card": cid, "upgrade": int(up)}})
        self.log(f"获得技能卡 {self.card_name(cid, up)}", kind="card", card=cid, up=int(up))
        return c

    def card_name(self, cid, up=0):
        return (self.d.card(cid, 0).get("name") or cid) + ("+" * int(up or 0))

    def _upgradable(self, c):
        """A card is upgraded once in a produce (upgrade counts 2 and 3 are the exam's own growth)."""
        return c["up"] < 1 and (c["id"], 1) in self.d.cards

    # -- start -----------------------------------------------------------------------------------------------------
    def _start_selection(self):
        d, f, s = self.d, self.s["formation"], self.s
        idol = f["idol"]["id"]
        ranks = (int(f["idol"].get("level_limit_rank", 0)), int(f["idol"].get("potential_rank", 0)))
        base = dict(d.idols.base(idol, ranks))
        bonus = d.extra["true_end_bonus"].get(d.idols.character(idol)) or {}
        for te in f["account"].get("true_ends") or ():
            for k, v in (bonus.get(te) or {}).items():
                base["max_stamina" if k == "stamina" else k] += int(v)
        plan = d.plan_of(idol)
        s.update({
            "produce_id": SELECTION, "step": 0, "plan": plan, "idol": idol, "ranks": list(ranks),
            "character": d.idols.character(idol),
            "params": {k: int(base[k]) for k in ATTRS}, "growth": {k: int(base["g_" + k]) for k in ATTRS},
            "limit": int(d.setting(SELECTION).get("idolCardParameterGrowthLimit") or 3000),
            "max_stamina": int(base["max_stamina"]), "stamina": 0, "pp": 0, "star": 0, "star_permil": 0,
            "sp_rate": dict.fromkeys(ATTRS, 0), "audition_bonus": 0, "lesson_pp_up": 0,
            "deck": [], "next_card": 0, "items": [], "citems": [], "drinks": [],
            "drink_limit": int(d.setting(SELECTION)["produceDrinkPossessLimit"]),
            "drink_cap": int(d.setting(SELECTION)["produceDrinkPossessMaxLimit"]),
            "reroll": 1, "exclude": 1, "excluded": [],
            "shop": {"refresh": 0, "upgrade_count": 0, "delete_count": 0, "card_discount": 0, "drink_discount": 0,
                     "upgrade_discount": 0, "delete_discount": 0, "shelf": None},
            "rest_up": 0, "limit_up": 0, "event_up": {"param": 0, "pp": 0, "stamina": 0},
            "supports": [], "skills": [], "counts": {}, "memory_enchants": [], "schedule": {},
            "continues": int(d.setting(SELECTION)["continueCount"]), "auditions": {}, "done": False,
            "result": None, "wappen": 0})
        s["limit"] = 3000 if s["limit"] <= 0 else s["limit"]
        deck_id = d.initial_deck[(SELECTION, d.exam_effect_type(idol))]
        ini = d.exam_initial[deck_id]
        for cid, up in zip(ini["produceCardIds"], ini.get("produceCardUpgradeCounts") or [0] * 99):
            self._new_card(cid, up or 0, fire=False)
        self._new_card(d.idols.cards[idol]["produceCardId"], d.own_card_upgrade(idol, ranks[0]), fire=False)
        second = d.second_card(idol, ranks[0])
        if second:
            self._new_card(second[0], second[1], fire=False)
        s["items"].append({"id": d.idols.start_item(idol, ranks), "n": 0})
        s["memory_cards"] = {"1": [], "2": []}
        brought = set()
        for m in f.get("memories") or []:
            if m and m.get("produce_card"):
                pc = m["produce_card"]
                if pc["id"] in brought:
                    continue
                brought.add(pc["id"])
                phase = str(int(m.get("produce_card_phase_type") or 1))
                s["memory_cards"].setdefault(phase, []).append(
                    {"id": pc["id"], "up": int(pc.get("upgrade_count") or 0),
                     "cz": [[x["id"], x.get("customize_count", 1)] for x in pc.get("customizes") or ()]})
        acc = f["account"]
        start = []
        for sheet, lv in sorted(acc["growth_panel"].items()):
            r = d.panels.get((sheet, int(lv)))
            if r and name(r.get("produceSplitType")) not in ("PRODUCE_SPLIT_TYPE_FINAL",):
                start += [("panel:" + sheet, e) for e in r.get("produceEffectIds") or ()]
        dear = self._dearness_skills(s["character"], int(acc["dearness_level"]))
        for sid, lv in dear:
            start += self._skill_effects("dearness", sid, lv)
        for sid, lv in self._idol_skills(idol, ranks):
            start += self._skill_effects("idol", sid, lv)
        for i, sc in enumerate(f["supports"]):
            src = f"support:{i}:{sc['id']}"
            ups = 0
            for _order, sk in d.M.support_skills(sc["id"], int(sc["level"])):
                if sk is None:
                    continue
                if sk.phase == "PRODUCE_START":
                    if sk.effect_type == "SUPPORT_CARD_PRODUCE_CARD_UPGRADE_PROBABILITY_UP":
                        ups += int(sk.value[0] or 0)
                    start.append((src, sk.effect_id))
                else:
                    s["skills"].append([src, sk.id, sk.level])
            card = d.support_cards[sc["id"]]
            events = [[n, det] for n, lv, det in sorted(d.support_events.get(sc["id"], ())) if int(sc["level"]) >= lv]
            s["supports"].append({"id": sc["id"], "level": int(sc["level"]), "rental": bool(sc.get("rental")),
                                  "permil": int(card.get("produceCardUpgradePermil") or 0) * (1000 + ups) // 1000,
                                  "upgrade_type": PARAM_TYPE.get(name(card.get("produceCardUpgradeLessonParameterType"))),
                                  "events": events, "fired": []})
        for j, m in enumerate(f.get("memories") or []):
            if not m:
                continue
            for ab in m.get("abilities") or []:
                sk = d.M.memory_skill(ab["id"], ab.get("level"))
                if sk is None:
                    continue
                src = f"memory:{j}"
                if sk.phase == "PRODUCE_START":
                    start.append((src, sk.effect_id))
                elif sk.cond.get("hif_memory"):
                    ench = d.effect(sk.effect_id).get("produceExamStatusEnchantId")
                    if ench and ench not in s["memory_enchants"]:
                        s["memory_enchants"].append(ench)
                else:
                    s["skills"].append([src, sk.id, sk.level])
        for src, eid in start:
            self._apply_start(src, eid)
        s["stamina"] = s["max_stamina"]
        s["start_pp"] = s["pp"]
        s["at_start"] = {"params": dict(s["params"]), "growth": dict(s["growth"]), "max_stamina": s["max_stamina"],
                         "pp": s["pp"], "reroll": s["reroll"], "exclude": s["exclude"], "sp_rate": dict(s["sp_rate"]),
                         "star_permil": s["star_permil"], "audition_bonus": s["audition_bonus"]}
        self._roll_schedule()
        opening = [k for k in d.details if k.startswith("event-detail-p_story-003-produce-007-opening")]
        for k in sorted(opening)[:1]:
            for eid in d.details[k].get("produceEffectIds") or ():
                s["queue"].append({"t": "effect", "id": eid, "src": "story:opening"})
        s["queue"].append({"t": "memory_cards", "phase": "1"})
        s["queue"].append({"t": "next_step"})

    def _dearness_skills(self, character, level):
        table = self.d.extra["dearness_skills"].get(character) or {}
        best = None
        for lv, skills in table.items():
            if int(lv) <= level and (best is None or int(lv) > best[0]):
                best = (int(lv), skills)
        return [tuple(x) for x in best[1]] if best else []

    def _idol_skills(self, idol, ranks):
        d = self.d
        card = d.idols.cards[idol]
        out = {}
        for r in d.idols.ll_skills:
            if r["id"] == card.get("idolCardLevelLimitProduceSkillId") and r["rank"] <= ranks[0]:
                out[r["skill"]] = max(out.get(r["skill"], 0), r["level"])
        from GakumasCLI.produce import produce_effects as pe
        for r in pe._rows("IdolCardPotentialProduceSkill"):
            if r["id"] == card.get("idolCardPotentialProduceSkillId") and int(name(r.get("rank")).rsplit("_", 1)[-1] or 0) <= ranks[1]:
                out[r["produceSkillId"]] = max(out.get(r["produceSkillId"], 0), int(r.get("produceSkillLevel") or 1))
        return sorted(out.items())

    def _skill_effects(self, src, sid, lv):
        r = self.d.M.skills.get((sid, lv))
        if not r:
            return []
        return [(f"{src}:{sid}", r[f"produceEffectId{i}"]) for i in (1, 2, 3) if r.get(f"produceEffectId{i}")]

    def _apply_start(self, src, eid):
        """A produce-start effect: the start state's numbers (never a decision)."""
        s, d = self.s, self.d
        e = d.effect(eid)
        et = name(e.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")
        v = int(e.get("effectValueMin") or 0)
        if et in ADD:
            self._add_param(ADD[et], v)
        elif et in GROWTH:
            s["growth"][GROWTH[et]] += v
        elif et in SP_ADD:
            for a in SP_ADD[et]:
                s["sp_rate"][a] += v
        elif et in ("PRODUCE_POINT_ADDITION_DISABLE_TRIGGER", "PRODUCE_POINT_ADDITION"):
            s["pp"] += v
        elif et == "MAX_STAMINA_ADDITION":
            s["max_stamina"] += v
        elif et == "STAR_PERMIL_UP":
            s["star_permil"] += v
        elif et == "AUDITION_PARAMETER_BONUS_MULTIPLE":
            s["audition_bonus"] += v
        elif et == "PRODUCE_CARD_SELECT_REROLL_COUNT_UP":
            s["reroll"] += v
        elif et == "PRODUCE_CARD_EXCLUDE_COUNT_UP":
            s["exclude"] += v
        elif et == "SHOP_REROLL_COUNT_UP":
            s["shop"]["refresh"] += v
        elif et == "PRODUCE_DRINK_POSSESS_LIMIT_UP":
            s["drink_limit"] = min(s["drink_cap"], s["drink_limit"] + v)
        elif et == "BEFORE_AUDITION_REFRESH_STAMINA_UP":
            s["rest_up"] += v
        elif et == "PARAMETER_LIMIT_UP":
            s["limit_up"] += v
        elif et == "SHOP_PRODUCE_CARD_PRICE_DISCOUNT_MULTIPLE_PERMANENT":
            s["shop"]["card_discount"] += v
        elif et == "LESSON_PRESENT_PRODUCE_POINT_UP":
            s["lesson_pp_up"] += v
        elif et in ("SUPPORT_CARD_EVENT_PARAMETER_ADDITION_VALUE_UP", "SUPPORT_CARD_EVENT_PRODUCE_POINT_ADDITION_VALUE_UP",
                    "SUPPORT_CARD_EVENT_STAMINA_RECOVER_UP"):
            k = {"SUPPORT_CARD_EVENT_PARAMETER_ADDITION_VALUE_UP": "param",
                 "SUPPORT_CARD_EVENT_PRODUCE_POINT_ADDITION_VALUE_UP": "pp", "SUPPORT_CARD_EVENT_STAMINA_RECOVER_UP": "stamina"}[et]
            if src.startswith("support:"):
                sup = s["supports"][int(src.split(":")[1])]
                sup.setdefault("event_up", {"param": 0, "pp": 0, "stamina": 0})[k] += v
            else:
                s["event_up"][k] += v
        elif et in ("PRODUCE_CARD_UPGRADE", "PRODUCE_REWARD", "PRODUCE_REWARD_SET", "PRODUCE_CARD_CHANGE",
                    "PRODUCE_CARD_DELETE", "PRODUCE_CARD_DUPLICATE"):
            s["queue"].append({"t": "effect", "id": eid, "src": src})
        elif et == "IDOL_CARD_PRODUCE_CARD_CUSTOMIZE_ENABLE":
            s["idol_customize"] = True
        elif et == "PRODUCE_CARD_CHANGE_UPGRADE":
            new_id = d.change_upgrade_target(src.split(":", 1)[1] if ":" in src else "")
            search = (d.cs.rows.get(e.get("produceCardSearchId")) or {}).get("produceCardIds") or ()
            old = next((c for c in s["deck"] if not c.get("deleted") and c["id"] in search), None)
            if new_id and old is not None:
                old["deleted"] = True
                self._new_card(new_id, 1 if (new_id, 1) in d.cards else 0, fire=False)
            else:
                self.log(f"未建模的开局效果 {et}", kind="unmodelled", effect=eid, src=src)
        elif et in ("SUPPORT_CARD_PRODUCE_CARD_UPGRADE_PROBABILITY_UP", "AUDITION_VOTE_COUNT_UP",
                    "EVENT_BUSINESS_VOTE_COUNT_UP"):
            pass
        else:
            self.log(f"未建模的开局效果 {et}", kind="unmodelled", effect=eid, src=src)

    def _roll_schedule(self):
        """Every lesson candidate of the produce: SP or not, and its sub parameter."""
        s, d = self.s, self.d
        base = d.models["sp_lesson"]["base_permil"]
        hi = d.models["sub_attribute"]["higher_permil"]
        crit = self._criteria("final")
        days = LESSON_DAY if s["produce_id"] == SELECTION else MAIN_LESSON_DAY
        for day, (tier, kind) in sorted(days.items()):
            row = {}
            for a in ATTRS:
                sp = self.rng(f"{SHORT_P(s)}/schedule/sp").chance(min(1000, s["sp_rate"][a] + base))
                o = [x for x in ATTRS if x != a]
                if crit[o[0]] == crit[o[1]]:
                    sub = o[self.rng(f"{SHORT_P(s)}/schedule/sub").uniform(2)]
                else:
                    high = max(o, key=lambda x: crit[x])
                    sub = high if self.rng(f"{SHORT_P(s)}/schedule/sub").uniform(1000) < hi else [x for x in o if x != high][0]
                row[a] = f"p_step_open_lesson-produce_007-{tier}-{kind}{'-sp' if sp else ''}-sub_{SHORT[sub]}"
            s["schedule"][str(day)] = row

    def _criteria(self, stage):
        s, d = self.s, self.d
        r = d.difficulty(s["idol"], s["produce_id"], STEP_TAIL[stage])
        b = d.battle[r["produceExamBattleConfigId"]]
        return {"vocal": b["vocal"], "dance": b["dance"], "visual": b["visual"]}

    # -- the task loop ---------------------------------------------------------------------------------------------
    def _run(self):
        q = self.s["queue"]
        guard = 0
        while q and not self.s["done"]:
            guard += 1
            if guard > 100000:
                raise RuntimeError("task loop does not settle")
            t = q[0]
            handler = getattr(self, "_auto_" + t["t"], None)
            if handler is None:
                if t["t"] == "exam" and self.exam is None:
                    self._open_exam(t)
                return
            q.pop(0)
            handler(t)

    def options(self):
        """The current decision's choices: [{"label": text, ...}] (index = what ``act`` takes)."""
        if self.s["done"] or not self.s["queue"]:
            return []
        t = self.s["queue"][0]
        return getattr(self, "_opts_" + t["t"])(t)

    def decision(self):
        if self.s["done"] or not self.s["queue"]:
            return None
        t = self.s["queue"][0]
        return {"kind": t["t"], "title": t.get("title", ""), "options": self.options()}

    def act(self, index):
        """Take option ``index`` of the current decision; an exam action is recorded as the action itself
        (``{"exam": [...]}``), so a replay never depends on the option order of the exam screen."""
        if isinstance(index, dict) and "exam" in index:
            return self.act_exam(index["exam"])
        if isinstance(index, dict) and "exam_assume" in index:
            return self.exam_assume(*index["exam_assume"])
        opts = self.options()
        if not isinstance(index, int) or not 0 <= index < len(opts):
            raise SimError(f"没有第 {index} 个选项")
        o = opts[index]
        if o.get("disabled"):
            raise SimError(f"这个选项现在不能选：{o['label']}（{o['disabled']}）")
        t = self.s["queue"][0]
        if t["t"] == "exam":
            if o.get("auto"):
                return self.exam_auto(o.get("budget", 2.0))
            return self.act_exam(o["a"])
        self.s["actions"].append(index)
        getattr(self, "_do_" + t["t"])(t, o)
        self._run()
        return self._traced()

    def _traced(self):
        h = self.state_hash()
        self.trace.append(h)
        return h

    def act_exam(self, a):
        t = self.s["queue"][0] if self.s["queue"] else {}
        if t.get("t") != "exam":
            raise SimError("现在不在考试中")
        if self.exam is None:
            self._open_exam(t)
        a = list(a) if a[0] != "select" else ["select", [int(x) for x in a[1]]]
        if a[0] != "select" and tuple(a) not in {tuple(x) for x in self.exam.legal_actions()}:
            raise SimError(f"考试里现在不能 {a}")
        self.s["actions"].append({"exam": a})
        self._exam_step(t, a)
        self._run()
        return self._traced()

    def exam_auto(self, budget=2.0, search_seed=182, transitions=None):
        """The engine plays the rest of this exam (its line from the current state, under the exam's 控分 if set);
        each action is recorded.
        """
        from GKMSPS.ps import exam as X
        t = self.s["queue"][0]
        if self.exam is None:
            self._open_exam(t)
        h = None
        for a in X.plan(self.exam, budget, search_seed, self.exam_settings().get("split"), transitions=transitions):
            if self.exam is None or self.exam.finished:
                break
            try:
                h = self.act_exam(a)
            except SimError:
                break
        while self.exam is not None and not self.exam.finished:
            legal = [list(x) for x in self.exam.legal_actions()]
            h = self.act_exam(legal[-1] if legal else ["end"])
        return h

    # -- the exam's settings (短搜, 控分) ---------------------------------------------------------------------------
    def exam_task(self):
        t = self.s["queue"][0] if not self.s.get("done") and self.s["queue"] else None
        if not t or t["t"] != "exam":
            raise SimError("现在不在考试中")
        return t

    def exam_key(self, t=None):
        t = t or self.exam_task()
        return [self.s["produce_id"], t["stage"], int(t.get("try", 1))]

    def exam_settings(self):
        """This exam's settings: {short_search, split}, empty when none are set or the queue is not at an exam."""
        try:
            key = self.exam_key()
        except SimError:
            return {}
        ex = (self.s.get("ui") or {}).get("exam") or {}
        return ex if ex.get("key") == key else {}

    def _exam_settings_rw(self):
        key = self.exam_key()
        ui = self.s.setdefault("ui", {})
        if (ui.get("exam") or {}).get("key") != key:
            ui["exam"] = {"key": key, "short_search": False, "split": None}
        return ui["exam"]

    def set_short_search(self, on):
        ex = self._exam_settings_rw()
        ex["short_search"] = bool(on)
        if on:
            self.s["ui"]["engine_used"] = True
        self.log("本场打开 30 秒短搜" if on else "本场关闭短搜", kind="choice")

    def set_exam_split(self, split):
        """``split`` already checked (timeline.split_check's canonical form), None clears it."""
        from GakumasCLI.scenario import timeline as TL
        ex = self._exam_settings_rw()
        ex["split"] = split
        if split:
            self.s["ui"]["split_last"] = split
        self.log(f"本场控分：{TL.split_text(split)}" if split else "本场取消控分", kind="choice")

    def selection_gain_preview(self, stage, parts):
        """The parameters a 選抜 exam ending at ``parts`` would give (the reward split by the score's shares, then the
        growth rates, capped at the limit) -- what _auto_audition_reward adds."""
        s = self.s
        f, r = SELECTION_REWARD[stage]
        base = selection_reward(f, r, parts)
        return [max(0, min(s["limit"] - s["params"][a], base[i] + base[i] * s["growth"][a] // 1000))
                for i, a in enumerate(ATTRS)]

    # -- steps -----------------------------------------------------------------------------------------------------
    def _days(self):
        return SELECTION_DAYS if self.s["produce_id"] == SELECTION else MAIN_DAYS

    def _auto_next_step(self, t):
        s = self.s
        s["step"] += 1
        if s["step"] > len(self._days()):
            return
        s["shop"]["shelf"] = None
        kinds = self._days()[s["step"]]
        if kinds[0].startswith("audition:"):
            self._push({"t": "audition", "stage": kinds[0].split(":")[1]})
            return
        if kinds[0] == "interval":
            self._push({"t": "interval", "title": "インターバル", "rolls": 0, "upgrades": 0, "customized": 0},
                       {"t": "next_step"})
            return
        self._push({"t": "day", "title": f"第 {s['step']} {'天' if s['produce_id'] == SELECTION else '步'}"})

    def _opts_day(self, t):
        s, d = self.s, self.d
        out = []
        for k in self._days()[s["step"]]:
            if k == "lesson":
                for a in ATTRS:
                    les = d.open_lessons[s["schedule"][str(s["step"])][a]]
                    sp = "-sp-" in les["id"]
                    sub = PARAM_TYPE[name(les.get("subParameterType"))]
                    lab = (f"公開レッスン {LABEL[a]}{'（SP）' if sp else ''}：{LABEL[a]}+{les['mainParameter']} "
                           f"{LABEL[sub]}+{les['subParameter']} 星+{les['star']} 体力-{les['stamina']}")
                    out.append({"label": lab, "do": "lesson", "attr": a,
                                "disabled": "体力不足" if s["stamina"] < les["stamina"] else None})
            elif k == "school":
                for a in ATTRS:
                    out.append({"label": f"授業 {LABEL[a]}", "do": "school", "attr": a})
            elif k == "activity":
                out.append({"label": "おでかけ（回复最大体力 60%）", "do": "activity"})
            elif k == "present":
                out.append({"label": "差し入れ（P 点+80、饮料、技能卡）", "do": "present"})
            elif k == "shop":
                out.append({"label": "相談", "do": "shop"})
            elif k == "customize":
                out.append({"label": "特別指導（定制 2 张）", "do": "customize"})
        if s["stamina"] < s["max_stamina"]:
            n = s["max_stamina"] * int(d.setting(s["produce_id"])["refreshStaminaRecoveryPermil"]) // 1000
            out.append({"label": f"休む（体力+{min(n, s['max_stamina'] - s['stamina'])}）", "do": "refresh"})
        return out

    def _do_day(self, t, o):
        s = self.s
        s["queue"].pop(0)
        s["chosen"] = o["do"]
        self.log(f"第 {s['step']} 天：{o['label']}", kind="step")
        tail = [{"t": "support_roll", "which": "after"}, {"t": "next_step"}]
        body = {"lesson": [{"t": "lesson", "attr": o.get("attr")}],
                "school": [{"t": "school_open", "attr": o.get("attr")}],
                "activity": [{"t": "activity_open"}],
                "present": [{"t": "present"}],
                "shop": [{"t": "fire", "phase": "START_SHOP", "ev": {}}, {"t": "shop", "title": "相談"}],
                "customize": [{"t": "cz_visit"}, {"t": "fire", "phase": "START_CUSTOMIZE", "ev": {}},
                              {"t": "customize", "left": int(self.d.setting(s["produce_id"])["customizeProduceCardCount"]),
                               "title": "特別指導"}],
                "refresh": [{"t": "refresh"}]}[o["do"]]
        self._push({"t": "support_roll", "which": "before"}, *body, *tail)

    def _auto_lesson(self, t):
        s, d = self.s, self.d
        a = t["attr"]
        les = d.open_lessons[s["schedule"][str(s["step"])][a]]
        kind = "sp" if "-sp-" in les["id"] else "normal"
        sub = PARAM_TYPE[name(les.get("subParameterType"))]
        self._add_stamina(-int(les["stamina"]))
        g = s["growth"]
        main_gain = int(les["mainParameter"]) * (1000 + g[a]) // 1000
        sub_gain = int(les["subParameter"]) * (1000 + g[sub]) // 1000
        self._push({"t": "fire", "phase": "START_LESSON", "ev": {"attr": a, "kind": kind}},
                   {"t": "lesson_gain", "attr": a, "sub": sub, "main": main_gain, "subv": sub_gain, "kind": kind},
                   {"t": "fire", "phase": "END_LESSON_BEFORE_PRESENT", "ev": {"attr": a, "kind": kind}},
                   {"t": "lesson_present", "star": int(les["star"])},
                   {"t": "fire", "phase": "END_LESSON", "ev": {"attr": a, "kind": kind}})

    def _auto_lesson_gain(self, t):
        gm = self._add_param(t["attr"], t["main"])
        gs = self._add_param(t["sub"], t["subv"])
        self.log(f"{LABEL[t['attr']]}+{gm} {LABEL[t['sub']]}+{gs}", kind="gain")

    def _auto_lesson_present(self, t):
        s = self.s
        star = self._add_star(t["star"])
        pp = (LESSON_PP[s["produce_id"]] + s["lesson_pp_up"]) * (1000 + s.pop("lesson_pp_bonus", 0)) // 1000
        s["pp"] += pp
        self.log(f"星+{star} P点+{pp}", kind="gain")

    def _auto_refresh(self, t):
        s = self.s
        self._push({"t": "fire", "phase": "START_REFRESH", "ev": {}})
        n = s["max_stamina"] * int(self.d.setting(s["produce_id"])["refreshStaminaRecoveryPermil"]) // 1000
        self._add_stamina(n)
        self.log(f"休息：体力+{n}", kind="gain")

    def _school_detail(self, attr):
        s, d = self.s, self.d
        n = s["plan"][-1]
        stage = SCHOOL_STAGE.get(s["step"])
        prefix = (f"event-detail-school-{attr}-003-produce_007-plan{n}-{stage}" if s["produce_id"] == SELECTION
                  else f"event-detail-school-{attr}-003-produce_008-plan{n}")
        ids = sorted(k for k in d.details if k.startswith(prefix + "-"))
        if not ids:
            raise SimError(f"主数据里没有授業事件 {prefix}")
        return ids[0]

    def _auto_school_open(self, t):
        det = self._school_detail(t["attr"])
        self._push({"t": "suggest", "detail": det, "title": f"授業 {LABEL[t['attr']]}", "kind": "school",
                    "after": [{"t": "fire", "phase": "END_STEP_EVENT_SCHOOL", "ev": {}}]})

    def _auto_activity_open(self, t):
        s, d = self.s, self.d
        stage = ACTIVITY_STAGE.get(s["step"])
        prefix = (f"event-detail-activity-003-produce_007-{stage}" if s["produce_id"] == SELECTION
                  else "event-detail-activity-003-produce_008")
        ids = sorted(k for k in d.details if k.startswith(prefix + "-"))
        det = ids[self.rng(f"{SHORT_P(s)}/d{s['step']:02d}/activity/detail").uniform(len(ids))]
        self._push({"t": "suggest", "detail": det, "title": "おでかけ", "kind": "activity",
                    "after": [{"t": "fire", "phase": "END_STEP_EVENT_ACTIVITY", "ev": {}}]})

    def _opts_suggest(self, t):
        s, d = self.s, self.d
        out = []
        for sid in d.details[t["detail"]].get("produceStepEventSuggestionIds") or ():
            g = d.suggestions[sid]
            cost = []
            if g.get("stamina"):
                cost.append(f"体力-{g['stamina']}")
            if g.get("producePoint"):
                cost.append(f"P点-{g['producePoint']}")
            if g.get("produceCardId"):
                cost.append("获得" + self.card_name(g["produceCardId"]))
            text = self._describe_effects(g.get("produceEffectIds") or ())
            dis = None
            if g.get("producePoint") and s["pp"] < int(g["producePoint"]):
                dis = "P点不足"
            if t.get("kind") == "school" and g.get("stamina") and s["stamina"] < int(g["stamina"]):
                dis = "体力不足"
            out.append({"label": "；".join(x for x in (text, " ".join(cost)) if x), "sid": sid, "disabled": dis})
        return out

    def _describe_effects(self, eids):
        out = []
        for eid in eids:
            r = self.d.effect(eid)
            et = name(r.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")
            v = r.get("effectValueMin") or 0
            if et in ADD:
                out.append(f"{LABEL[ADD[et]]}+{v}")
            elif et == "STAMINA_RECOVER_MULTIPLE":
                out.append(f"回复最大体力{v // 10}%")
            elif et == "PRODUCE_REWARD_SET":
                res = name(r.get("produceResourceType"), "PRODUCE_RESOURCE_TYPE_")
                out.append({"PRODUCE_CARD": "技能卡" + ("（强化）" if "upgrade_1" in eid else "") + "三选一",
                            "PRODUCE_DRINK": "饮料三选一"}.get(res, res))
            elif et == "PRODUCE_CARD_CHANGE_SELECT":
                out.append("セレクトチェンジ")
            elif et == "PRODUCE_POINT_ADDITION":
                out.append(f"P点+{v}")
            elif et == "STAMINA_RECOVER_FIX":
                out.append(f"体力+{v}")
            elif et == "PRODUCE_REWARD":
                for rw in r.get("produceRewards") or ():
                    out.append("获得" + str(rw.get("resourceId")))
            else:
                out.append(et.lower())
        return "、".join(out)

    def _do_suggest(self, t, o):
        s, d = self.s, self.d
        s["queue"].pop(0)
        g = d.suggestions[o["sid"]]
        self.log(f"{t.get('title', '事件')}：{o['label']}", kind="choice")
        if g.get("stamina"):
            self._add_stamina(-int(g["stamina"]))
        if g.get("producePoint"):
            s["pp"] -= int(g["producePoint"])
        tasks = []
        if g.get("produceCardId"):
            tasks.append({"t": "gain_card", "id": g["produceCardId"],
                          "up": self._event_card_up(t.get("src", ""), g["produceCardId"], int(g.get("produceCardUpgradeCount") or 0))})
        eids = list(g.get("produceEffectIds") or ())
        if not g.get("alwaysSuccessful") and g.get("successProbabilityPermyriad"):
            ok = self.rng(f"event/{t['detail']}/success").uniform(10000) < int(g["successProbabilityPermyriad"])
            eids += list(g.get("successProduceEffectIds" if ok else "failProduceEffectIds") or ())
        src = t.get("src", "event")
        tasks += [{"t": "effect", "id": e, "src": src} for e in eids]
        self._push(*tasks, *t.get("after", ()))

    def _auto_memory_cards(self, t):
        for c in self.s["memory_cards"].get(t["phase"], ()):
            self._new_card(c["id"], c["up"], from_memory=True, customizes=c.get("cz"))
            self.log("回忆效果", kind="memory")

    def _auto_gain_card(self, t):
        self._new_card(t["id"], t.get("up", 0))

    # -- 差し入れ ---------------------------------------------------------------------------------------------------
    def _auto_present(self, t):
        s = self.s
        s["pp"] += PRESENT_PP
        self.log(f"差し入れ：P点+{PRESENT_PP}", kind="gain")
        self._push({"t": "fire", "phase": "START_PRESENT", "ev": {}},
                   {"t": "offer_make", "what": "drink", "kind": "present", "label": f"{SHORT_P(s)}/d{s['step']:02d}/present/drink"},
                   {"t": "offer_make", "what": "card", "kind": "present", "label": f"{SHORT_P(s)}/d{s['step']:02d}/present/card"},
                   {"t": "fire", "phase": "END_PRESENT", "ev": {}})

    # -- offers (技能卡 / 饮料 三选一) ------------------------------------------------------------------------------
    def _offer_kind(self, eid):
        s = self.s
        if s["produce_id"] == SELECTION:
            if "event_school-before_1st" in eid:
                return "school1"
            if "event_school-before_3rd" in eid:
                return "school3"
            if "event_activity-before_2nd" in eid:
                return "activity2"
            if "event_activity-before_3rd" in eid:
                return "activity3"
            return "present"
        if "event_school" in eid:
            return "school"
        if "event_activity" in eid:
            return "activity"
        return "present"

    def _offer_tag(self, eid):
        for tag in ("parameter_buff", "lesson_buff", "review", "card_play_aggressive", "concentration", "full_power"):
            if f"exam_{tag}" in eid:
                return tag
        return None

    def _pool(self, tag=None):
        s, d = self.s, self.d
        plv = int(s["formation"]["account"]["producer_level"])
        held = {c["id"] for c in self._deck()}
        out = []
        for cid in d.offer_pool[s["plan"]] + d.offer_pool["COMMON"]:
            row = d.card(cid)
            if int(row.get("unlockProducerLevel") or 0) > plv:
                continue
            cid2 = self._convert(cid, plv)
            row2 = d.card(cid2)
            if cid2 in s["excluded"] or (row2.get("noDeckDuplication") and cid2 in held):
                continue
            if tag and f"effect_group-visible-exam_{tag}-000" not in (row2.get("effectGroupIds") or ()):
                continue
            out.append(cid2)
        return sorted(set(out))

    def _convert(self, cid, plv):
        for r in self.d.conversions:
            if r["beforeProduceCardId"] == cid:
                need = int((r.get("conditionSetId") or "0").rsplit("-", 1)[-1] or 0)
                if plv >= need:
                    return r["afterProduceCardId"]
        return cid

    def _draw_cards(self, label, kind, n, tag=None, avoid=()):
        s, d = self.s, self.d
        table = d.models["offer_rarity"]["table"].get(s["produce_id"], {})
        weights = table.get(kind) or table.get("present") or {"R": 1, "SR": 1, "SSR": 1}
        pool = [c for c in self._pool(tag) if c not in avoid] or [c for c in self._pool(None) if c not in avoid]
        by = {r: [c for c in pool if name(d.card(c).get("rarity"), "PRODUCE_CARD_RARITY_") == r] for r in ("R", "SR", "SSR")}
        out = []
        rs = self.rng(label)
        while len(out) < n:
            avail = [r for r in ("R", "SR", "SSR") if [c for c in by[r] if c not in out]]
            if not avail:
                break
            r = avail[rs.weighted([weights.get(x, 0) or 1 for x in avail])] if any(weights.get(x) for x in avail) else rs.choice(avail)
            cands = [c for c in by[r] if c not in out]
            out.append(cands[rs.uniform(len(cands))])
        return out

    def _draw_drinks(self, label, n, rarity_weights=None):
        s, d = self.s, self.d
        plan = "PRODUCE_PLAN_TYPE_" + s["plan"]
        pool = sorted(k for k, r in d.drinks.items() if not r.get("libraryHidden")
                      and name(r.get("planType")) in ("PRODUCE_PLAN_TYPE_COMMON", plan))
        w = rarity_weights or d.models["shop"]["drink_rarity"][s["produce_id"]]
        by = {}
        for k in pool:
            by.setdefault(name(d.drinks[k].get("rarity"), "PRODUCE_DRINK_RARITY_"), []).append(k)
        out, rs = [], self.rng(label)
        while len(out) < n:
            avail = [r for r in ("R", "SR", "SSR") if [k for k in by.get(r, ()) if k not in out]]
            if not avail:
                break
            r = avail[rs.weighted([w.get(x, 0) or 1 for x in avail])]
            cands = [k for k in by[r] if k not in out]
            out.append(cands[rs.uniform(len(cands))])
        return out

    def _offer_ups(self, t, cands, label):
        """Each offered card's upgrade: all of them for an upgrade_1 effect; for the 差し入れ and the おでかけ choices
        each card on its own with the measured permil of its rarity (draw_models offer_upgrade: 選抜 おでかけ② R 904 /
        SR 783 / SSR 433, 差し入れ about 250); none for the 授業, セレクトチェンジ and effects that name upgrade_0."""
        d = self.d
        eff = t.get("effect") or ""
        if "upgrade_1" in eff:
            return [1 if (c, 1) in d.cards else 0 for c in cands]
        table = ((d.models.get("offer_upgrade") or {}).get("permil") or {}).get(self.s["produce_id"], {}).get(t.get("up_kind") or "")
        if not table:
            return [0] * len(cands)
        rs = self.rng(label + "/up")
        out = []
        for c in cands:
            p = int(table.get(name(d.card(c).get("rarity"), "PRODUCE_CARD_RARITY_"), 0))
            hit = rs.uniform(1000) < p
            out.append(1 if hit and (c, 1) in d.cards else 0)
        return out

    def _auto_offer_make(self, t):
        if t["what"] == "card":
            eff = t.get("effect") or ""
            if not eff and t["kind"] == "present":
                up_kind = "present_list"
            elif "event_activity" in eff and "upgrade_" not in eff:
                up_kind = t["kind"]
            else:
                up_kind = None
            cands = self._draw_cards(t["label"] + "#r0", t["kind"], t.get("n", 3), t.get("tag"))
            ups = self._offer_ups(dict(t, up_kind=up_kind), cands, t["label"] + "#r0")
            title = "技能卡三选一" + ("（强化）" if "upgrade_1" in eff else "")
            self._push({"t": "offer", "what": "card", "cands": [[c, u] for c, u in zip(cands, ups)], "kind": t["kind"],
                        "tag": t.get("tag"), "label": t["label"], "rolls": 0, "title": title,
                        "change": t.get("change"), "effect": eff, "up_kind": up_kind})
        else:
            cands = self._draw_drinks(t["label"], t.get("n", 3))
            self._push({"t": "offer", "what": "drink", "cands": [[c, 0] for c in cands], "label": t["label"],
                        "title": "饮料三选一"})

    def _opts_offer(self, t):
        s, d = self.s, self.d
        out = []
        for i, (cid, up) in enumerate(t["cands"]):
            if t["what"] == "card":
                out.append({"label": "拿 " + self.card_name(cid, up), "pick": i})
            else:
                out.append({"label": "拿 " + (d.drinks[cid].get("name") or cid), "pick": i})
        if t["what"] == "card":
            out.append({"label": f"重新抽选（剩 {s['reroll']} 次）", "reroll": True,
                        "disabled": None if s["reroll"] > 0 else "没有次数"})
            for i, (cid, up) in enumerate(t["cands"]):
                out.append({"label": f"除外 {self.card_name(cid, up)}（剩 {s['exclude']} 次）", "exclude": i,
                            "disabled": None if s["exclude"] > 0 else "没有次数"})
        elif len(s["drinks"]) >= s["drink_limit"]:
            out.append({"label": "不拿（饮料栏满了）", "decline": True})
        if t.get("back"):
            out.append({"label": "返回，重选要换掉的卡", "back": True})
        return out

    def _do_offer(self, t, o):
        s = self.s
        if o.get("back"):
            s["queue"].pop(0)
            back = dict(t["back"], cands=[list(x) for x in t["cands"]], rolls=t["rolls"])
            self._push(back)
            return
        if o.get("reroll"):
            s["reroll"] -= 1
            t["rolls"] += 1
            lab = f"{t['label']}#r{t['rolls']}"
            cards = self._draw_cards(lab, t["kind"], len(t["cands"]), t.get("tag"))
            t["cands"] = [[c, u] for c, u in zip(cards, self._offer_ups(t, cards, lab))]
            self.log("重新抽选", kind="choice")
            return
        if "exclude" in o:
            s["exclude"] -= 1
            cid, up = t["cands"][o["exclude"]]
            s["excluded"].append(cid)
            t["rolls"] += 1
            keep = [c for c, _ in t["cands"] if c != cid]
            lab = f"{t['label']}#x{t['rolls']}"
            new = self._draw_cards(lab, t["kind"], 1, t.get("tag"), avoid=keep)
            t["cands"][o["exclude"]] = [new[0], self._offer_ups(t, new[:1], lab)[0]] if new else t["cands"][o["exclude"]]
            if not new:
                t["cands"].pop(o["exclude"])
            self.log(f"除外 {self.card_name(cid)}", kind="choice")
            return
        s["queue"].pop(0)
        if o.get("decline"):
            self.log("饮料栏满了，不拿", kind="drink")
            return
        cid, up = t["cands"][o["pick"]]
        if t["what"] == "card":
            if t.get("change") is not None:
                self._change_card(t["change"], cid)
            else:
                self._new_card(cid, up)
        else:
            self._gain_drink(cid)

    def _gain_drink(self, did):
        """One drink handed over."""
        s = self.s
        if len(s["drinks"]) >= s["drink_limit"]:
            self._push({"t": "discard", "title": "饮料栏满了，丢掉一瓶", "new": did})
            return
        self._add_drink(did)

    def _add_drink(self, did):
        self.s["drinks"].append(did)
        self.log(f"获得饮料 {self.d.drinks[did].get('name') or did}", kind="drink")
        self._push({"t": "fire", "phase": "GET_PRODUCE_DRINK", "ev": {}})

    def _gain_drinks(self, dids):
        """The drinks of one reward."""
        dids = list(dids)
        if len(self.s["drinks"]) + len(dids) <= self.s["drink_limit"]:
            for did in dids:
                self._add_drink(did)
            return
        self._push(*[{"t": "drink_choice", "id": did, "last": i == len(dids) - 1, "of": dids,
                      "title": "饮料拿不下全部：拿这瓶吗"} for i, did in enumerate(dids)])

    def _opts_drink_choice(self, t):
        nm = self.d.drinks[t["id"]].get("name") or t["id"]
        return [{"label": "拿 " + nm, "take": True, "id": t["id"], "last": t["last"]},
                {"label": "不要 " + nm, "take": False, "id": t["id"], "last": t["last"]}]

    def _do_drink_choice(self, t, o):
        self.s["queue"].pop(0)
        if o["take"]:
            self._gain_drink(t["id"])
        else:
            self.log(f"不要饮料 {self.d.drinks[t['id']].get('name') or t['id']}", kind="drink")

    def _opts_change_confirm(self, t):
        c = self._card_by_number(t["n"])
        return [{"label": f"换：{self.card_name(c['id'], c['up'])} → {self.card_name(t['new'])}", "take": True},
                {"label": "不换", "take": False}]

    def _do_change_confirm(self, t, o):
        self.s["queue"].pop(0)
        if o["take"]:
            self._change_card(t["n"], t["new"])
        else:
            self.log(f"随机换卡没有换（{self.card_name(t['new'])}）", kind="choice")

    def _opts_discard(self, t):
        out = [{"label": "丢掉 " + (self.d.drinks[k].get("name") or k), "i": i} for i, k in enumerate(self.s["drinks"])]
        if t.get("new"):
            out.append({"label": "不要新的 " + (self.d.drinks[t["new"]].get("name") or t["new"]), "i": len(out),
                        "decline": True})
        return out

    def _do_discard(self, t, o):
        self.s["queue"].pop(0)
        if o.get("decline"):
            self.log(f"不要饮料 {self.d.drinks[t['new']].get('name') or t['new']}", kind="drink")
            return
        k = self.s["drinks"].pop(o["i"])
        self.log(f"丢掉饮料 {self.d.drinks[k].get('name') or k}", kind="drink")
        if t.get("new"):
            self._add_drink(t["new"])

    # -- deck targets (セレクトチェンジ, 强化, 删除, 定制) -------------------------------------------------------------
    def _deck_options(self, search=None, need=None):
        out = []
        for c in sorted(self._deck(), key=lambda c: c["n"]):
            if search and not self.d.cs.match(search, c["id"], c["up"]):
                continue
            if need == "upgrade" and not self._upgradable(c):
                continue
            if need == "change" and name(self.d.card(c["id"]).get("category")) == "PRODUCE_CARD_CATEGORY_TROUBLE":
                continue
            out.append(c)
        return out

    def _opts_target(self, t):
        cards = self._deck_options(t.get("search"), t.get("need"))
        out = [{"label": self.card_name(c["id"], c["up"]) + ("（定制）" if c.get("cz") else ""), "n": c["n"]} for c in cards]
        if t.get("purpose") == "change":
            out.append({"label": f"重新抽选候选（剩 {self.s['reroll']} 次）", "n": None, "reroll": True,
                        "disabled": None if self.s["reroll"] > 0 else "没有次数"})
            for i, (cid, up) in enumerate(t.get("cands") or ()):
                out.append({"label": f"除外候选 {self.card_name(cid, up)}（剩 {self.s['exclude']} 次）", "n": None,
                            "exclude": i, "disabled": None if self.s["exclude"] > 0 else "没有次数"})
        if t.get("optional"):
            out.append({"label": "不选", "n": None})
        return out

    def _do_target(self, t, o):
        s = self.s
        if o.get("reroll"):
            s["reroll"] -= 1
            t["rolls"] = t.get("rolls", 0) + 1
            lab = f"{t['label']}#r{t['rolls']}"
            t["cands"] = [[c, 0] for c in self._draw_cards(lab, t["kind"], len(t["cands"]), t.get("tag"))]
            names = "、".join(self.card_name(c) for c, _ in t["cands"])
            t["title"] = f"セレクトチェンジ：选要换掉的卡（可换成：{names}）"
            self.log("重新抽选", kind="choice")
            return
        if "exclude" in o:
            s["exclude"] -= 1
            cid, up = t["cands"][o["exclude"]]
            s["excluded"].append(cid)
            t["rolls"] = t.get("rolls", 0) + 1
            keep = [c for c, _ in t["cands"] if c != cid]
            new = self._draw_cards(f"{t['label']}#x{t['rolls']}", t["kind"], 1, t.get("tag"), avoid=keep)
            if new:
                t["cands"][o["exclude"]] = [new[0], 0]
            else:
                t["cands"].pop(o["exclude"])
            names = "、".join(self.card_name(c) for c, _ in t["cands"])
            t["title"] = f"セレクトチェンジ：选要换掉的卡（可换成：{names}）"
            self.log(f"除外 {self.card_name(cid)}", kind="choice")
            return
        s["queue"].pop(0)
        if o["n"] is None:
            return
        c = self._card_by_number(o["n"])
        p = t["purpose"]
        if p == "change":
            self._push({"t": "offer", "what": "card", "cands": [list(x) for x in t["cands"]], "kind": t["kind"],
                        "tag": t.get("tag"), "label": t["label"], "rolls": t.get("rolls", 0), "change": c["n"],
                        "back": {k: v for k, v in t.items()},
                        "title": f"セレクトチェンジ：把 {self.card_name(c['id'], c['up'])} 换成哪张（保留强化）"})
        elif p == "upgrade":
            self._upgrade_card(c, t.get("src"))
        elif p == "delete":
            self._delete_card(c, t.get("src"))
        elif p == "shop_upgrade":
            s["pp"] -= t["price"]
            s["shop"]["upgrade_count"] += 1
            self._upgrade_card(c, "shop")
        elif p == "shop_delete":
            s["pp"] -= t["price"]
            s["shop"]["delete_count"] += 1
            self._delete_card(c, "shop")
        elif p == "duplicate":
            self._new_card(c["id"], c["up"], customizes=c.get("cz"))
        elif p == "customize":
            self._push({"t": "customize_pick", "n": c["n"], "left": t["left"], "title": "选定制项"})

    def _change_card(self, n, cid):
        c = self._card_by_number(n)
        old = self.card_name(c["id"], c["up"])
        up = min(c["up"], 1 if (cid, 1) in self.d.cards else 0)
        c["deleted"] = True
        self._new_card(cid, up, fire=False)
        self.log(f"セレクトチェンジ：{old} → {self.card_name(cid, up)}", kind="card")
        self._push({"t": "fire", "phase": "CHANGE_PRODUCE_CARD", "ev": {"card": c["id"], "upgrade": c["up"]}})

    def _upgrade_card(self, c, src):
        if not self._upgradable(c):
            return
        c["up"] += 1
        self.log(f"强化 {self.card_name(c['id'], c['up'])}", kind="card")
        self._push({"t": "fire", "phase": "UPGRADE_PRODUCE_CARD", "ev": {"card": c["id"], "upgrade": c["up"]}})

    def _delete_card(self, c, src):
        c["deleted"] = True
        self.log(f"删除 {self.card_name(c['id'], c['up'])}", kind="card")
        self._push({"t": "fire", "phase": "DELETE_PRODUCE_CARD", "ev": {"card": c["id"], "upgrade": c["up"]}})

    # -- 相談 ------------------------------------------------------------------------------------------------------
    def _service_price(self, count, kind):
        prices = self.d.models["shop"]["service_price"]
        return self._shop_price(kind, prices[min(count, len(prices) - 1)])

    def _shop_price(self, kind, price, position=None):
        """The shelf's list price, then its discounts one after the other, each floored: the shelf's own sale
        (discount_permil), this visit's effects on every price (the rows' effect_discount_permils: P-item
        20% off), this visit's effects on the kind (support skills, custom P-items: drinks 50% off), then
        the growth panel's card discount (hidden in the rows, 30%).
        """
        s, m = self.s, self.d.models["shop"]
        p = price
        if position is not None and position in m["sale_positions"]:
            p = p * (1000 - m["sale_permil"]) // 1000
        visit = s["shop"].get("visit") or {}
        for x in list(visit.get("all", ())) + list(visit.get(kind, ())):
            p = p * (1000 - min(1000, x)) // 1000
        if kind == "card" and s["shop"]["card_discount"]:
            p = p * (1000 - min(1000, s["shop"]["card_discount"])) // 1000
        return p

    def _make_shelf(self, label):
        s, d = self.s, self.d
        m = d.models["shop"]
        kind = SHOP_DAY_KIND.get(s["step"], "19") if s["produce_id"] == SELECTION else "6"
        rw = m["card_rarity_by_step"][s["produce_id"]].get(kind) or {"R": 1, "SR": 1, "SSR": 1}
        rs = self.rng(label + "/rarity")
        pool = self._pool(None)
        by = {r: [c for c in pool if name(d.card(c).get("rarity"), "PRODUCE_CARD_RARITY_") == r] for r in ("R", "SR", "SSR")}
        cards = []
        while len(cards) < 4:
            avail = [r for r in ("R", "SR", "SSR") if [c for c in by[r] if c not in cards]]
            if not avail:
                break
            r = avail[rs.weighted([rw.get(x, 0) or 1 for x in avail])]
            cc = [c for c in by[r] if c not in cards]
            cards.append(cc[rs.uniform(len(cc))])
        shelf = []
        un, upg = m["card_unupgraded_by_rarity"], m["card_upgraded_by_rarity"]
        for i, cid in enumerate(cards):
            r = name(d.card(cid).get("rarity"), "PRODUCE_CARD_RARITY_")
            u0, u1 = un.get(r, 0), upg.get(r, 1)
            up = 1 if rs.uniform(u0 + u1) >= u0 else 0
            if not (cid, up) in d.cards:
                up = 0
            price = m["card_price"][r][up]
            shelf.append({"what": "card", "id": cid, "up": up, "price": self._shop_price("card", price, i + 1),
                          "list_price": price, "bought": False})
        for j, did in enumerate(self._draw_drinks(label + "/drinks", 4)):
            r = name(d.drinks[did].get("rarity"), "PRODUCE_DRINK_RARITY_")
            price = m["drink_price"][r]
            shelf.append({"what": "drink", "id": did, "price": self._shop_price("drink", price, j + 5),
                          "list_price": price, "bought": False})
        return shelf

    def _opts_shop(self, t):
        s, d = self.s, self.d
        if s["shop"]["shelf"] is None:
            s["shop"]["shelf"] = self._make_shelf(f"{SHORT_P(s)}/d{s['step']:02d}/shop#r{t.get('rolls', 0)}")
        out = []
        for i, it in enumerate(s["shop"]["shelf"]):
            nm = self.card_name(it["id"], it["up"]) if it["what"] == "card" else (d.drinks[it["id"]].get("name") or it["id"])
            dis = "已买" if it["bought"] else ("P点不足" if s["pp"] < it["price"] else None)
            if it["what"] == "drink" and not dis and len(s["drinks"]) >= s["drink_limit"]:
                dis = None
            out.append({"label": f"买 {nm}（{it['price']}P）", "buy": i, "disabled": dis})
        up = self._service_price(s["shop"]["upgrade_count"], "upgrade")
        de = self._service_price(s["shop"]["delete_count"], "delete")
        used = t.setdefault("used", [])
        out.append({"label": f"强化 1 张（{up}P）", "service": "upgrade", "price": up,
                    "disabled": "已用" if "upgrade" in used else ("P点不足" if s["pp"] < up else None)})
        out.append({"label": f"删除 1 张（{de}P）", "service": "delete", "price": de,
                    "disabled": "已用" if "delete" in used else ("P点不足" if s["pp"] < de else None)})
        out.append({"label": f"刷新货架（剩 {s['shop']['refresh']} 次）", "refresh": True,
                    "disabled": None if s["shop"]["refresh"] > 0 else "没有次数"})
        out.append({"label": "离开", "leave": True})
        return out

    def _do_shop(self, t, o):
        s = self.s
        if o.get("leave"):
            s["queue"].pop(0)
            s["shop"].pop("visit", None)
            return
        if o.get("refresh"):
            s["shop"]["refresh"] -= 1
            t["rolls"] = t.get("rolls", 0) + 1
            s["shop"]["shelf"] = None
            t["used"] = []
            self.log("刷新货架", kind="choice")
            return
        if "buy" in o:
            it = s["shop"]["shelf"][o["buy"]]
            s["pp"] -= it["price"]
            it["bought"] = True
            if it["what"] == "card":
                self._new_card(it["id"], it["up"])
                self._push({"t": "fire", "phase": "BUY_SHOP_ITEM_PRODUCE_CARD", "ev": {"card": it["id"], "upgrade": it["up"]}})
            else:
                self._gain_drink(it["id"])
                self._push({"t": "fire", "phase": "BUY_SHOP_ITEM_PRODUCE_DRINK", "ev": {}})
            return
        t["used"].append(o["service"])
        need = "upgrade" if o["service"] == "upgrade" else None
        self._push({"t": "target", "purpose": "shop_" + o["service"], "price": o["price"], "need": need,
                    "title": "选择要强化的卡" if need else "选择要删除的卡", "optional": True})

    # -- 特別指導 --------------------------------------------------------------------------------------------------
    def _customize_choices(self, c, raise_up=False):
        """(customize id, its next level, price) the card can still take: a card takes at most
        ProduceCard.maxCustomizeCount customize steps in all, spread over its options as the player likes; an option
        goes up to its highest level row, each level at its own row's producePoint.
        """
        d, s = self.d, self.s
        if c["id"] == d.idols.cards[s["idol"]].get("produceCardId") and not s.get("idol_customize"):
            return []
        if raise_up and c["up"] == 0 and (c["id"], 1) in d.cards:
            c = dict(c, up=1)
        row = d.card(c["id"], c["up"])
        have = {x[0]: int(x[1]) for x in c.get("cz") or ()}
        if sum(have.values()) >= int(row.get("maxCustomizeCount") or 1):
            return []
        out = []
        for cz in row.get("produceCardCustomizeIds") or ():
            levels = d.customize_levels.get(cz) or {}
            if not levels:
                continue
            n = have.get(cz, 0)
            if n >= max(levels):
                continue
            r = levels.get(n + 1) or levels[max(levels)]
            price = int(r.get("producePoint") or 0)
            price = price * (1000 - min(1000, s.get("cz_discount_now", 0))) // 1000
            out.append((cz, n + 1, price))
        return out

    def _auto_cz_visit(self, t):
        """A 特別指導 visit starts: a discount kept for the next visit applies to this one."""
        s = self.s
        s["cz_discount_now"] = s.pop("cz_discount_next", 0)

    def _opts_customize(self, t):
        cards = [c for c in sorted(self._deck(), key=lambda c: c["n"]) if self._customize_choices(c)]
        done = t.setdefault("cards", [])
        out = [{"label": f"定制 {self.card_name(c['id'], c['up'])}", "n": c["n"],
                "disabled": None if t["left"] > 0 or c["n"] in done else "本次的定制次数已用完"} for c in cards]
        out.append({"label": "离开", "leave": True})
        return out

    def _do_customize(self, t, o):
        if o.get("leave"):
            self.s["queue"].pop(0)
            self.s.pop("cz_discount_now", None)
            return
        self._push({"t": "customize_pick", "n": o["n"], "title": "选定制项"})

    def _opts_customize_pick(self, t):
        s = self.s
        c = self._card_by_number(t["n"])
        out = []
        for cz, lv, price in self._customize_choices(c, raise_up=bool(t.get("interval"))):
            row = self.d.customizes[cz]
            text = row.get("description") or cz.split("-g_effect-")[-1]
            out.append({"label": f"{text} Lv{lv}（{price}P）", "cz": cz, "lv": lv, "price": price,
                        "disabled": "P点不足" if s["pp"] < price else None})
        out.append({"label": "返回", "back": True})
        return out

    def _do_customize_pick(self, t, o):
        s = self.s
        s["queue"].pop(0)
        if o.get("back"):
            return
        c = self._card_by_number(t["n"])
        s["pp"] -= o["price"]
        if t.get("interval") and c["up"] == 0 and (c["id"], 1) in self.d.cards:
            c["up"] = 1
        cz = [x for x in c.get("cz") or () if x[0] != o["cz"]] + [[o["cz"], o["lv"]]]
        c["cz"] = cz
        if s["queue"] and s["queue"][0]["t"] in ("customize", "interval"):
            visit = s["queue"][0]
            done = visit.setdefault("cards", [])
            if c["n"] not in done:
                done.append(c["n"])
                if visit["t"] == "customize":
                    visit["left"] -= 1
                else:
                    visit["customized"] += 1
        self.log(f"定制 {self.card_name(c['id'], c['up'])}：{o['label']}", kind="card")
        self._push({"t": "fire", "phase": "CUSTOMIZE_PRODUCE_CARD", "ev": {"card": c["id"], "upgrade": c["up"]}})

    # -- effects ---------------------------------------------------------------------------------------------------
    def _auto_effect(self, t):
        s, d = self.s, self.d
        eid, src = t["id"], t.get("src", "")
        e = d.effect(eid)
        et = name(e.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")
        v = int(e.get("effectValueMin") or 0)
        pick = name(e.get("pickRangeType"), "PRODUCE_PICK_RANGE_TYPE_")
        res = name(e.get("produceResourceType"), "PRODUCE_RESOURCE_TYPE_")
        support = src.startswith("support_event")
        up = dict(s["event_up"])
        if support and ":" in src:
            own = (s["supports"][int(src.split(":")[1])].get("event_up") or {})
            up = {k: up.get(k, 0) + own.get(k, 0) for k in ("param", "pp", "stamina")}
        if et in ADD:
            if support:
                v = v * (1000 + up["param"]) // 1000
            g = self._add_param(ADD[et], v)
            self.log(f"{LABEL[ADD[et]]}+{g}", kind="gain", src=src)
        elif et in GROWTH:
            s["growth"][GROWTH[et]] += v
        elif et == "STAR_ADDITION":
            g = self._add_star(v)
            self.log(f"星+{g}", kind="gain", src=src)
        elif et in ("PRODUCE_POINT_ADDITION", "PRODUCE_POINT_ADDITION_DISABLE_TRIGGER"):
            if support:
                v = v * (1000 + up["pp"]) // 1000
            s["pp"] += v
            self.log(f"P点+{v}", kind="gain", src=src)
        elif et == "PRODUCE_POINT_REDUCE_FIX":
            s["pp"] = max(0, s["pp"] - v)
        elif et == "STAMINA_RECOVER_FIX":
            if support:
                v = v * (1000 + up["stamina"]) // 1000
            self._add_stamina(v)
            self.log(f"体力+{v}", kind="gain", src=src)
        elif et == "STAMINA_RECOVER_MULTIPLE":
            n = s["max_stamina"] * v // 1000
            self._add_stamina(n)
            self.log(f"体力+{n}", kind="gain", src=src)
        elif et == "STAMINA_REDUCE_FIX":
            self._add_stamina(-v)
        elif et == "MAX_STAMINA_ADDITION":
            s["max_stamina"] += v
            self._add_stamina(v)
        elif et == "LESSON_PRESENT_PRODUCE_POINT_UP":
            s["lesson_pp_bonus"] = s.get("lesson_pp_bonus", 0) + v
        elif et == "CUSTOMIZE_PRODUCE_CARD_PRODUCE_POINT_DOWN_MULTIPLE":
            s["cz_discount_next"] = s.get("cz_discount_next", 0) + v
        elif et in SHOP_VISIT:
            kind, sign = SHOP_VISIT[et]
            s["shop"].setdefault("visit", {}).setdefault(kind, []).append(sign * v)
        elif et == "SHOP_PRODUCE_CARD_PRICE_DISCOUNT_MULTIPLE_PERMANENT":
            s["shop"]["card_discount"] += v
        elif et == "SHOP_REROLL_COUNT_UP":
            s["shop"]["refresh"] += v
        elif et == "PRODUCE_REWARD":
            for rw in e.get("produceRewards") or ():
                self._reward(rw, src)
        elif et == "PRODUCE_REWARD_SET":
            rws = e.get("produceRewards") or []
            if res == "PRODUCE_ITEM" and "end_mid_audition" in eid:
                item = CHEER_ITEM.get(d.exam_effect_type(s["idol"]))
                if item:
                    self._reward({"resourceType": {"name": "PRODUCE_RESOURCE_TYPE_PRODUCE_ITEM"}, "resourceId": item}, src)
            elif res == "PRODUCE_CUSTOMIZE_ITEM":
                self._push({"t": "citem_pick", "title": "カスタムPアイテム 三选一", "cands": self._citem_bases()})
            elif rws and pick == "ALL":
                for rw in rws:
                    self._reward(rw, src)
            elif res == "PRODUCE_CARD":
                lab = f"{SHORT_P(s)}/d{s['step']:02d}/{src}/{self._offer_kind(eid)}/offer"
                self._push({"t": "offer_make", "what": "card", "kind": self._offer_kind(eid), "tag": self._offer_tag(eid),
                            "label": lab, "effect": eid, "n": 3})
            elif res == "PRODUCE_DRINK":
                if pick == "RANDOM":
                    self._gain_drinks(self._draw_drinks(f"{SHORT_P(s)}/d{s['step']:02d}/{src}/drink_random",
                                                        int(e.get("pickCountMax") or 1)))
                else:
                    self._push({"t": "offer_make", "what": "drink", "kind": "present",
                                "label": f"{SHORT_P(s)}/d{s['step']:02d}/{src}/drink"})
            else:
                self.log(f"未建模的奖励集 {eid}", kind="unmodelled")
        elif et == "PRODUCE_CARD_CHANGE_SELECT":
            lab = f"{SHORT_P(s)}/d{s['step']:02d}/{src}/change"
            kind, tag = self._offer_kind(eid), self._offer_tag(eid)
            cands = [[c, 0] for c in self._draw_cards(lab + "#r0", kind, 3, tag)]
            names = "、".join(self.card_name(c) for c, _ in cands)
            self._push({"t": "target", "purpose": "change", "search": e.get("produceCardSearchId"), "need": "change",
                        "kind": kind, "tag": tag, "effect": eid, "label": lab, "cands": cands,
                        "title": f"セレクトチェンジ：选要换掉的卡（可换成：{names}）"})
        elif et in ("PRODUCE_CARD_UPGRADE", "PRODUCE_CARD_DELETE", "PRODUCE_CARD_DUPLICATE", "PRODUCE_CARD_CHANGE"):
            purpose = {"PRODUCE_CARD_UPGRADE": "upgrade", "PRODUCE_CARD_DELETE": "delete",
                       "PRODUCE_CARD_DUPLICATE": "duplicate", "PRODUCE_CARD_CHANGE": "change"}[et]
            need = "upgrade" if purpose == "upgrade" else ("change" if purpose == "change" else None)
            count = int(e.get("pickCountMax") or e.get("effectValueMax") or 1)
            if pick == "SELECT":
                for _ in range(count):
                    self._push({"t": "target", "purpose": purpose, "search": e.get("produceCardSearchId"), "need": need,
                                "kind": self._offer_kind(eid), "src": src, "effect": eid,
                                "label": f"{SHORT_P(s)}/d{s['step']:02d}/{src}/{purpose}",
                                "title": {"upgrade": "选择要强化的卡", "delete": "选择要删除的卡",
                                          "duplicate": "选择要复制的卡", "change": "选择要换掉的卡"}[purpose]})
            else:
                cands = self._deck_options(e.get("produceCardSearchId"), need)
                chosen = self.rng(f"{SHORT_P(s)}/d{s['step']:02d}/{src}/{purpose}_random").sample(cands, count)
                confirm = []
                for c in chosen:
                    if purpose == "upgrade":
                        self._upgrade_card(c, src)
                    elif purpose == "delete":
                        self._delete_card(c, src)
                    elif purpose == "duplicate":
                        self._new_card(c["id"], c["up"], customizes=c.get("cz"))
                    else:
                        new = self._draw_cards(f"{SHORT_P(s)}/d{s['step']:02d}/{src}/change_random", "present", 1)
                        if new:
                            confirm.append({"t": "change_confirm", "n": c["n"], "new": new[0],
                                            "title": f"随机换卡：{self.card_name(c['id'], c['up'])} → {self.card_name(new[0])}"})
                self._push(*confirm)
        elif et == "PRODUCE_CUSTOMIZE_ITEM_UPGRADE":
            opts = [[p, ch] for p in s["citems"] for ch in self.d.citem_children.get(p, ())]
            if opts:
                self._push({"t": "citem_upgrade", "opts": opts, "title": "强化カスタムPアイテム"})
        elif et in ENCHANTS:
            pass
        elif et in ("AUDITION_NPC_ENHANCE", "AUDITION_NPC_WEAKEN", "VOTE_COUNT_ADDITION", "HIGH_SCORE_GOLD_ADDITION"):
            pass
        else:
            self.log(f"未建模的效果 {et}（{eid}）", kind="unmodelled", src=src)

    def _reward(self, rw, src):
        s, d = self.s, self.d
        rt = name(rw.get("resourceType"), "PRODUCE_RESOURCE_TYPE_")
        rid = rw.get("resourceId")
        q = int(rw.get("quantity") or 1)
        if rt == "PRODUCE_CARD":
            for _ in range(q):
                self._new_card(rid, self._event_card_up(src, rid, int(rw.get("resourceLevel") or 0)))
        elif rt == "PRODUCE_ITEM":
            if not any(it["id"] == rid for it in s["items"]):
                s["items"].append({"id": rid, "n": 0})
                self.log(f"获得P道具 {(d.items.get(rid) or {}).get('name') or rid}", kind="item")
                self._push({"t": "fire", "phase": "GET_PRODUCE_ITEM", "ev": {}})
        elif rt == "PRODUCE_DRINK":
            self._gain_drinks([rid] * q)
        elif rt == "PRODUCE_POINT":
            s["pp"] += q
        else:
            self.log(f"未建模的奖励 {rt} {rid}", kind="unmodelled", src=src)

    def _event_card_up(self, src, cid, up):
        """An SSR support card's event gives its card upgraded."""
        if up or not src.startswith("support_event:"):
            return up
        sup = self.s["supports"][int(src.split(":")[1])]
        rar = name(self.d.support_cards[sup["id"]].get("rarity"), "SUPPORT_CARD_RARITY_")
        return 1 if rar == "SSR" and (cid, 1) in self.d.cards else up

    def _citem_bases(self):
        s, d = self.s, self.d
        plan = "PRODUCE_PLAN_TYPE_" + s["plan"]
        return sorted(k for k, r in d.citems.items() if r.get("isBase") and name(r.get("planType")) == plan)

    def _opts_citem_pick(self, t):
        return [{"label": "拿 " + (self.d.citems[k].get("name") or k) + "：" + self._describe_effects(
                 [e for e in self.d.citems[k].get("produceEffectIds") or () if self.d.effect_type(e) not in ENCHANTS]),
                 "id": k} for k in t["cands"]]

    def _do_citem_pick(self, t, o):
        self.s["queue"].pop(0)
        self.s["citems"].append(o["id"])
        self.s["counts"]["citem:" + o["id"]] = 0
        self.log(f"获得カスタムPアイテム {self.d.citems[o['id']].get('name')}", kind="item")
        self._push({"t": "fire", "phase": "GET_PRODUCE_ITEM", "ev": {}})

    def _opts_citem_upgrade(self, t):
        return [{"label": f"{self.d.citems[p].get('name')} → {self.d.citems[ch].get('name')}：" + self._describe_effects(
                 [e for e in self.d.citems[ch].get("produceEffectIds") or () if self.d.effect_type(e) not in ENCHANTS]),
                 "p": p, "ch": ch} for p, ch in t["opts"]]

    def _do_citem_upgrade(self, t, o):
        s = self.s
        s["queue"].pop(0)
        s["citems"] = [o["ch"] if x == o["p"] else x for x in s["citems"]]
        s["counts"].pop("citem:" + o["p"], None)
        self.log(f"カスタムPアイテム强化为 {self.d.citems[o['ch']].get('name')}", kind="item")

    # -- abilities -------------------------------------------------------------------------------------------------
    def _abilities(self):
        s, d = self.s, self.d
        out = []
        from GKMSPS.ps.triggers import refine, _parse
        for src, sid, lv in s["skills"]:
            sk = d.M.skill(sid, lv, src)
            if sk is not None and s["produce_id"] == MAIN and src.startswith("memory:") and sk.phase == "PRODUCE_START":
                continue
            if sk is not None:
                sk = refine(d.M, sk, (d.M.skills.get((sid, lv)) or {}).get("produceTriggerId1"))
                out.append((f"{src}|{sid}", sk, sk.cap))
        for it in s["items"]:
            tid = (d.M.items.get(it["id"]) or {}).get("produceTriggerId")
            for sk in d.M.item_skills(it["id"]):
                out.append((f"item:{it['id']}", refine(d.M, sk, tid), sk.cap))
        for ci in s["citems"]:
            r = d.citems.get(ci) or {}
            phase, cond, held = _parse(d.M, r.get("produceTriggerId"))
            if held == "for_nia_master" and phase == "END_PRESENT":
                held = ""
            for eid in r.get("produceEffectIds") or ():
                if d.effect_type(eid) in ENCHANTS:
                    continue
                from GakumasCLI.produce.produce_effects import Skill
                sk = Skill(id=ci, level=0, phase=phase, cond=cond, effect_id=eid, effect_type=d.effect_type(eid),
                           value=(d.effect(eid).get("effectValueMin"), d.effect(eid).get("effectValueMax")),
                           resource="", rate_permil=1000, cap=int(r.get("produceEffectTriggerCount") or 0) or None,
                           held=held, source="citem:" + ci)
                out.append((f"citem:{ci}", sk, sk.cap))
        return out

    def _auto_fire(self, t):
        from GakumasCLI.produce import produce_effects as pe
        from GKMSPS.ps.triggers import limits_met
        s, d = self.s, self.d
        phase, ev = t["phase"], dict(t.get("ev") or {})
        ev["deck"] = [(c["id"], c["up"]) for c in self._deck()]
        tasks, counted = [], set()
        for key, sk, cap in self._abilities():
            if sk.phase != phase or sk.held:
                continue
            if not pe.fires(sk, phase, ev, d.cs):
                continue
            if sk.cond.get("limits") and not limits_met(sk.cond, s, len(ev["deck"])):
                continue
            ck = key if key.startswith(("citem:", "item:")) else f"{key}|{sk.effect_id}"
            n = s["counts"].get(ck, 0)
            if ck not in counted and cap and n >= cap:
                continue
            if sk.rate_permil and sk.rate_permil < 1000:
                if not self.rng(f"abil/{key}").chance(sk.rate_permil):
                    continue
            if ck not in counted:
                s["counts"][ck] = n + 1
                counted.add(ck)
                if key == f"item:{WAPPEN}":
                    s["wappen"] = n + 1
            tasks.append({"t": "effect", "id": sk.effect_id, "src": key})
        if tasks:
            self._push(*tasks)

    # -- support events --------------------------------------------------------------------------------------------
    def _auto_support_roll(self, t):
        s, d = self.s, self.d
        rate = d.models["support_event"]["rate_permil"]
        nums = (1,) if t["which"] == "before" else (2, 3)
        cands = []
        for i, sc in enumerate(s["supports"]):
            for n, det in sc["events"]:
                if n in nums and n not in sc["fired"]:
                    cands.append((i, n, det))
        if not cands:
            return
        weights = [rate[str(n)] for _i, n, _d in cands]
        rs = self.rng(f"sup/{SHORT_P(s)}/d{s['step']:02d}/{t['which']}")
        x = rs.uniform(1000)
        for (i, n, det), w in zip(cands, weights):
            if x < w:
                s["supports"][i]["fired"].append(n)
                card = d.support_cards[s["supports"][i]["id"]]
                self.log(f"支援卡事件：{card.get('name')} 事件{n}", kind="event")
                src = f"support_event:{i}"
                sugg = d.details[det].get("produceStepEventSuggestionIds") or ()
                pre = [{"t": "effect", "id": e, "src": src} for e in d.details[det].get("produceEffectIds") or ()]
                if len(sugg) > 1:
                    self._push(*pre, {"t": "suggest", "detail": det, "title": f"{card.get('name')} 事件{n}", "src": src})
                elif sugg:
                    g = d.suggestions[sugg[0]]
                    tasks = list(pre)
                    if g.get("produceCardId"):
                        tasks.append({"t": "gain_card", "id": g["produceCardId"],
                                      "up": self._event_card_up(src, g["produceCardId"], int(g.get("produceCardUpgradeCount") or 0))})
                    tasks += [{"t": "effect", "id": e, "src": src} for e in g.get("produceEffectIds") or ()]
                    self._push(*tasks)
                else:
                    self._push(*pre)
                return
            x -= w

    # -- auditions -------------------------------------------------------------------------------------------------
    def _auto_audition(self, t):
        s, d = self.s, self.d
        stage = t["stage"]
        rest = s["max_stamina"] * int(d.setting(s["produce_id"])["beforeAuditionRefreshStaminaRecoveryPermil"]) // 1000
        rest = rest * (1000 + s["rest_up"]) // 1000
        if s["produce_id"] == MAIN and stage == "final":
            rest = 0
        self._add_stamina(rest)
        mode = ({"mid1": "選抜試験①", "mid2": "選抜試験②", "final": "選抜試験③"} if s["produce_id"] == SELECTION
                else {"mid1": "本戦 Round 1", "final": "本戦 Round 2"})[stage]
        self.log(f"{mode}：试验前回复体力+{rest}" if rest else f"{mode}：本场之前不休息", kind="step")
        tail = {"mid1": "MID1", "mid2": "MID2", "final": "FINAL"}[stage]
        self._push({"t": "fire", "phase": "START_AUDITION", "ev": {}},
                   {"t": "fire", "phase": "START_AUDITION_" + tail, "ev": {}},
                   {"t": "exam_prepare", "stage": stage, "try": 0})

    def _auto_exam_prepare(self, t):
        from GKMSPS.ps import exam as X
        s = self.s
        stage = t["stage"]
        seed = self.rng(f"exam/{SHORT_P(s)}/{stage}/try{t['try']}").exam_seed()
        loadout = X.loadout(self, stage)
        npcs = X.npc_scores(self, stage, self.rng(f"exam/{SHORT_P(s)}/{stage}/npc#t{t['try']}"))
        self._push({"t": "exam", "stage": stage, "try": t["try"], "seed": seed, "loadout": loadout, "npcs": npcs,
                    "plays": [], "title": f"考试：{stage}", "before": {"stamina": s["stamina"], "drinks": list(s["drinks"])}})

    def _open_exam(self, t):
        from GKMSPS.ps import exam as X
        self.exam = X.open_session(t["loadout"], t["seed"])

    def _rebuild_exam(self):
        from GKMSPS.ps import exam as X
        t = self.s["queue"][0]
        self.exam = X.open_session(t["loadout"], t["seed"])
        for a in t["plays"]:
            X.step(self.exam, a)

    def _opts_exam(self, t):
        from GKMSPS.ps import exam as X
        if self.exam is None:
            self._open_exam(t)
        out = X.options(self.exam)
        if self.s.get("engine"):
            out.append({"label": "引擎自动打完本场", "auto": True, "budget": 2.0})
        return out

    def _exam_step(self, t, a):
        from GKMSPS.ps import exam as X
        X.step(self.exam, a)
        t["plays"].append(a)
        if not self.exam.finished:
            return
        res = X.result(self.exam)
        self.exam = None
        self._exam_finish(t, res)

    def exam_assume(self, score, parts):
        """the current exam ends with ``score`` (its Vo / Da / Vi ``parts``) without being played -- the 日程 forecast
        takes every audition at its full scores.
        """
        t = self.exam_task()
        self.exam = None
        self.s["actions"].append({"exam_assume": [int(score), [int(x) for x in parts]]})
        self._exam_finish(t, {"score": int(score), "parts": [int(x) for x in parts], "stamina": self.s["stamina"],
                              "drinks_left": list(self.s["drinks"])})
        self._run()
        return self._traced()

    def _exam_finish(self, t, res):
        s = self.s
        s["queue"].pop(0)
        stage = t["stage"]
        score = res["score"]
        rank = 1 + sum(1 for x in t["npcs"] if x > score)
        r = self.d.difficulty(s["idol"], s["produce_id"], STEP_TAIL[stage])
        threshold = int(r.get("rankThreshold") or 1)
        s["stamina"] = min(s["max_stamina"], max(0, res["stamina"]))
        s["drinks"] = res["drinks_left"]
        rec = {"score": score, "rank": rank, "threshold": threshold, "npcs": t["npcs"], "try": t["try"],
               "parts": res["parts"], "star_before": s["star"], "seed": t["seed"]}
        self.log(f"考试结束：{score:,} 分，第 {rank} 名（过关线第 {threshold} 名）", kind="exam")
        if rank <= threshold:
            self._passed(stage, rec, t["try"], t["before"])
        else:
            s["auditions"][stage + "_lost"] = rec
            s["stamina"], s["drinks"] = t["before"]["stamina"], list(t["before"]["drinks"])
            self._push({"t": "lost", "stage": stage, "try": t["try"], "title": "没有通过"})

    def _passed(self, stage, rec, try_, before):
        """A passed exam can still be retried while retries are left: the new exam's result is the one that counts (the
        run archive: Continue after a 1st place 17 times in the 選抜, 16 times at the 本戦 Round 1, 5 at Round 2)."""
        s = self.s
        if s["continues"] <= 0:
            s["auditions"][stage] = rec
            self._push({"t": "audition_reward", "stage": stage})
            return
        s["exam_pending"] = rec
        self._push({"t": "passed", "stage": stage, "try": try_, "before": before,
                    "title": f"通过：{rec['score']:,} 分，第 {rec['rank']} 名"})

    def _opts_passed(self, t):
        return [{"label": "接受这个结果", "accept": True},
                {"label": f"重新挑战（剩 {self.s['continues']} 次；结果以新的一场为准）", "retry": True}]

    def _do_passed(self, t, o):
        s = self.s
        s["queue"].pop(0)
        rec = s.pop("exam_pending")
        if o.get("accept"):
            s["auditions"][t["stage"]] = rec
            self._push({"t": "audition_reward", "stage": t["stage"]})
            return
        s["continues"] -= 1
        s["stamina"], s["drinks"] = t["before"]["stamina"], list(t["before"]["drinks"])
        self.log("放弃这个结果，重新挑战", kind="choice")
        self._push({"t": "exam_prepare", "stage": t["stage"], "try": t["try"] + 1})

    def _opts_lost(self, t):
        s = self.s
        out = [{"label": f"重新挑战（剩 {s['continues']} 次）", "retry": True,
                "disabled": None if s["continues"] > 0 else "没有次数"}]
        if s["produce_id"] == MAIN and t["stage"] == "final":
            out.append({"label": "接受这个结果，结算", "accept": True})
        else:
            out.append({"label": "放弃（培育结束，不留任何东西）", "retry": False})
        return out

    def _do_lost(self, t, o):
        s = self.s
        s["queue"].pop(0)
        if o.get("accept"):
            s["auditions"][t["stage"]] = s["auditions"].pop(t["stage"] + "_lost")
            self._push({"t": "audition_reward", "stage": t["stage"]})
            return
        if o["retry"]:
            s["continues"] -= 1
            self._push({"t": "exam_prepare", "stage": t["stage"], "try": t["try"] + 1})
            return
        s["done"], s["result"] = True, {"outcome": "lost", "stage": t["stage"]}
        s["queue"].clear()
        self.log("選抜失败，培育结束", kind="end")

    def _auto_audition_reward(self, t):
        s = self.s
        stage = t["stage"]
        rec = s["auditions"][stage]
        tail = {"mid1": "MID1", "mid2": "MID2", "final": "FINAL"}[stage]
        if s["produce_id"] == SELECTION:
            f, r = SELECTION_REWARD[stage]
            base = selection_reward(f, r, rec["parts"])
            gains = []
            for i, a in enumerate(ATTRS):
                gains.append(self._add_param(a, base[i] + base[i] * s["growth"][a] // 1000))
            star = selection_star(stage, rec["score"], s["star_permil"])
            s["star"] += star
            rec.update(param_base=base, param_gain=gains, star_gain=star)
            self.log(f"试验奖励：Vo+{gains[0]} Da+{gains[1]} Vi+{gains[2]} 星+{star}", kind="gain")
        elif stage == "mid1":
            raw, pp = main_mid1_reward(rec["score"])
            star = self._add_star(raw)
            s["pp"] += pp
            rec.update(star_gain=star, pp_gain=pp)
            self.log(f"本戦 Round 1：星+{star} P点+{pp}", kind="gain")
        else:
            from GakumasCLI.produce import produce_effects as pe
            rec["star_before"] = s["star"]
            star = pe.hif_star_gain(rec["score"], s["star_permil"])
            s["star"] += star
            rec.update(star_gain=star)
            self.log(f"本戦 Round 2：星+{star}", kind="gain")
        before, after = [], []
        if s["produce_id"] == SELECTION and stage == "mid1":
            before.append({"t": "memory_cards", "phase": "2"})
        story = sorted(k for k in self.d.details if k.startswith(
            f"event-detail-p_story-003-produce-{s['produce_id'][-3:]}-after_audition_{stage}"))
        for k in story[:1]:
            after += [{"t": "effect", "id": e, "src": f"story:{stage}"} for e in self.d.details[k].get("produceEffectIds") or ()]
        self._push(*before, {"t": "fire", "phase": "END_AUDITION", "ev": {}},
                   {"t": "fire", "phase": "END_AUDITION_" + tail, "ev": {}}, *after)
        if s["produce_id"] == SELECTION and stage == "final":
            s["queue"].append({"t": "selection_end"})
        elif s["produce_id"] == MAIN and stage == "final":
            s["queue"].append({"t": "main_end"})
        else:
            if s["produce_id"] == SELECTION:
                s["queue"].append({"t": "support_roll", "which": "after"})
            s["queue"].append({"t": "next_step"})

    def _auto_selection_end(self, t):
        s = self.s
        s["done"] = True
        s["queue"].clear()
        s["result"] = {"outcome": "selection_memory", "selection_memory": self.selection_memory()}
        self.log("選抜通过：生成选拔试验メモリー", kind="end")

    def selection_memory(self):
        """What the 本戦 starts from: no P points, no drinks, stamina refilled."""
        s = self.s
        f = s["formation"]
        return {
            "format": "gkms.ps.selection_memory.v1", "source": "simulated", "root": s["root"],
            "idol": f["idol"], "account": f["account"], "supports_formation": f["supports"],
            "memories": f.get("memories") or [], "memory_enchants": list(s["memory_enchants"]),
            "params": dict(s["params"]), "growth": dict(s["growth"]), "max_stamina": s["max_stamina"],
            "star": s["star"], "star_permil": s["star_permil"], "audition_bonus": s["audition_bonus"],
            "sp_rate": dict(s["sp_rate"]), "lesson_pp_up": s["lesson_pp_up"], "rest_up": s["rest_up"],
            "event_up": dict(s["event_up"]), "drink_limit": s["drink_limit"],
            "deck": [{k: v for k, v in c.items() if k != "n"} for c in sorted(self._deck(), key=lambda c: c["n"])],
            "items": [dict(i) for i in s["items"]], "citems": list(s["citems"]),
            "supports": [dict(x) for x in s["supports"]], "skills": [list(x) for x in s["skills"]],
            "counts": dict(s["counts"]), "reroll": s["reroll"], "exclude": s["exclude"],
            "excluded": list(s["excluded"]), "shop": {k: v for k, v in s["shop"].items() if k != "shelf"},
            "auditions": {k: v for k, v in s["auditions"].items() if not k.endswith("_lost")},
            "wappen": s["wappen"], "deck_types": {c["id"]: name(self.d.card(c["id"]).get("rarity")) for c in self._deck()},
            "idol_customize": bool(s.get("idol_customize")),
        }


def SHORT_P(s):
    return "sel" if s["produce_id"] == SELECTION else "hon"
