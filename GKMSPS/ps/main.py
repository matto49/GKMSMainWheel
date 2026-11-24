# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the 本戦 (produce-008) of the simulator -- its start from a selection memory, the interval, the rating and the memory it
generates. Mixed into ps/sim.py's Sim."""
from __future__ import annotations

import copy

from GKMSPS.ps import seed as seedmod
from GKMSPS.ps.data import ATTRS, name
from GKMSPS.ps.rng import Rng

MAIN = "produce-008"
INTERVAL_SLOTS = (("card", 1), ("card", 2), ("change", 3), ("change", 4), ("card", 5), ("card", 6),
                  ("drink", 7), ("drink", 8), ("upgrade", 9), ("customize", 10), ("stamina", 11))
PHASE2_PERMIL = {"SSR": 1000, "SR": 648, "R": 100}
CARD_WEIGHT = {"support": 7, "pool": 3}
ABILITY_COUNTS = {
    "p_trigger-produce_start-initial-vocal_addition": 712, "p_trigger-produce_start-initial-dance_addition": 708,
    "p_trigger-produce_start-initial-visual_addition": 673,
    "p_trigger-produce_start-no_description-vocal_growth_rate_addition": 718,
    "p_trigger-produce_start-no_description-dance_growth_rate_addition": 725,
    "p_trigger-produce_start-no_description-visual_growth_rate_addition": 734,
    "p_trigger-produce_start-initial-produce_point_addition_disable_trigger": 683,
    "p_trigger-end_lesson-lesson_vocal-stamina_recover_fix": 130,
    "p_trigger-end_lesson-lesson_dance-stamina_recover_fix": 145,
    "p_trigger-end_lesson-lesson_visual-stamina_recover_fix": 144}
ABILITY_TIER_BY_GRADE = {
    10: {"01": 333, "02": 458, "03": 208}, 11: {"01": 254, "02": 521, "03": 225},
    12: {"01": 167, "02": 602, "03": 231}, 13: {"01": 166, "02": 542, "03": 292},
    14: {"01": 108, "02": 581, "03": 258, "04": 52}, 15: {"01": 95, "02": 570, "03": 278, "04": 56},
    16: {"01": 67, "02": 547, "03": 303, "04": 82}, 17: {"01": 57, "02": 537, "03": 312, "04": 94},
    18: {"01": 66, "02": 561, "03": 278, "04": 96}}
# where the H.I.F ability stands among the 4
HIF_POSITION = {3: 1389, 2: 368, 1: 42, 0: 3}


class MainMixin:
    # -- start -----------------------------------------------------------------------------------------------------
    @classmethod
    def honsen(cls, selection_memory, *, timestamp_ms=None, root=None, engine=False):
        """A 本戦 from a selection memory (the memory is not used up)."""
        from GKMSPS.ps.sim import FORMAT, ruleset, Sim
        sm = copy.deepcopy(selection_memory)
        if sm.get("format") != "gkms.ps.selection_memory.v1":
            raise ValueError("不是选拔试验メモリー")
        sim = Sim.__new__(Sim)
        from GKMSPS.ps.data import data
        sim.d = data()
        sim.trace, sim.exam = [], None
        formation = {"idol": sm["idol"], "supports": sm["supports_formation"], "memories": sm.get("memories") or [],
                     "account": sm["account"]}
        doc = seedmod.formation_document(formation, ruleset(), int(timestamp_ms or 0))
        doc["produce_id"] = MAIN
        doc["selection_memory"] = seedmod.memory_digest(sm)
        sim.root = seedmod.parse_root(root) if isinstance(root, str) else (root or seedmod.root_of(doc))
        sim.rng = Rng(sim.root)
        sim.s = {"format": FORMAT, "root": sim.root.hex(), "short": seedmod.short_code(sim.root),
                 "fixed_root": root is not None, "document": doc, "formation": formation, "engine": bool(engine),
                 "selection_memory": sm, "actions": [], "log": [], "queue": []}
        sim._start_main(sm)
        sim._run()
        return sim

    def _start_main(self, sm):
        sm = copy.deepcopy(sm)
        s, d = self.s, self.d
        idol = sm["idol"]["id"]
        s.update({
            "produce_id": MAIN, "step": 0, "plan": d.plan_of(idol), "idol": idol,
            "ranks": [sm["idol"].get("level_limit_rank", 0), sm["idol"].get("potential_rank", 0)],
            "character": d.idols.character(idol), "params": dict(sm["params"]), "growth": dict(sm["growth"]),
            "limit": int(d.setting(MAIN).get("idolCardParameterGrowthLimit") or 3000),
            "max_stamina": int(sm["max_stamina"]), "stamina": int(sm["max_stamina"]), "pp": 0,
            "star": int(sm["star"]), "star_permil": int(sm["star_permil"]), "sp_rate": dict(sm["sp_rate"]),
            "audition_bonus": int(sm["audition_bonus"]), "lesson_pp_up": int(sm.get("lesson_pp_up", 0)),
            "deck": [], "next_card": 0, "items": [dict(i) for i in sm["items"]], "citems": list(sm["citems"]),
            "drinks": [], "drink_limit": int(sm["drink_limit"]),
            "drink_cap": int(d.setting(MAIN)["produceDrinkPossessMaxLimit"]),
            "reroll": int(sm["reroll"]), "exclude": int(sm["exclude"]), "excluded": list(sm["excluded"]),
            "shop": dict(sm["shop"], shelf=None), "rest_up": int(sm.get("rest_up", 0)), "limit_up": 0,
            "event_up": dict(sm["event_up"]), "supports": [dict(x) for x in sm["supports"]],
            "skills": [list(x) for x in sm["skills"]],
            "counts": {k: v for k, v in sm["counts"].items() if not k.startswith("citem:")},
            "idol_customize": bool(sm.get("idol_customize")),
            "memory_enchants": list(sm["memory_enchants"]), "schedule": {}, "memory_cards": {},
            "continues": int(d.setting(MAIN)["continueCount"]), "auditions": {}, "done": False, "result": None,
            "wappen": int(sm.get("wappen", 0)), "selection_auditions": sm.get("auditions") or {}})
        s["limit"] = 3000 if s["limit"] <= 0 else s["limit"]
        for c in sm["deck"]:
            self._new_card(c["id"], c.get("up", 0), from_memory=bool(c.get("memory")), customizes=c.get("cz"), fire=False)
        prima = d.prima_card(idol) if sm["idol"].get("prima_stella") else None
        if prima:
            self._new_card(prima, 0)
        for sheet, lv in sorted(s["formation"]["account"]["growth_panel"].items()):
            r = d.panels.get((sheet, int(lv)))
            if r and name(r.get("produceSplitType")) == "PRODUCE_SPLIT_TYPE_FINAL":
                for e in r.get("produceEffectIds") or ():
                    self._apply_start("panel:" + sheet, e)
        s["limit"] += s["limit_up"]
        s["at_start"] = {"params": dict(s["params"]), "pp": s["pp"], "limit": s["limit"]}
        self._roll_schedule()
        s["queue"].append({"t": "next_step"})

    # -- インターバル ----------------------------------------------------------------------------------------------
    def _interval_shelf(self, label):
        s, d = self.s, self.d
        m = d.models["interval"]
        rs = self.rng(label)
        pool = self._pool(None)
        by = {r: [c for c in pool if name(d.card(c).get("rarity"), "PRODUCE_CARD_RARITY_") == r] for r in ("R", "SR", "SSR")}
        w = m["card_rarity_permil"]
        taken, shelf = [], []
        for kind, pos in INTERVAL_SLOTS:
            if kind in ("card", "change"):
                avail = [r for r in ("R", "SR", "SSR") if [c for c in by[r] if c not in taken]]
                if not avail:
                    continue
                r = avail[rs.weighted([w[x] for x in avail])]
                cc = [c for c in by[r] if c not in taken]
                cid = cc[rs.uniform(len(cc))]
                taken.append(cid)
                up = 1 if r in ("R", "SR") else int(rs.uniform(1000) < m["ssr_upgraded_permil"])
                if (cid, 1) not in d.cards:
                    up = 0
                price = m["card_price"][r][up] if kind == "card" else m["change_price"][r]
                shelf.append({"pos": pos, "what": kind, "id": cid, "up": up, "price": price, "bought": False})
            elif kind == "drink":
                avoid = [x["id"] for x in shelf if x["what"] == "drink"]
                ssr = rs.uniform(1000) < m["drink_ssr_permil"]
                dids = self._draw_drinks(f"{label}/drink{pos}", 4, {"R": 0, "SR": 0, "SSR": 1} if ssr else {"R": 6, "SR": 20, "SSR": 0})
                dids = [x for x in dids if x not in avoid] or dids
                did = dids[0]
                r = name(d.drinks[did].get("rarity"), "PRODUCE_DRINK_RARITY_")
                shelf.append({"pos": pos, "what": "drink", "id": did, "price": m["drink_price"][r], "bought": False})
            elif kind == "upgrade":
                shelf.append({"pos": pos, "what": "upgrade", "price": m["upgrade_price"][0], "bought": False})
            elif kind == "customize":
                shelf.append({"pos": pos, "what": "customize", "price": 0, "bought": False})
            else:
                shelf.append({"pos": pos, "what": "stamina", "price": 10, "bought": False})
        return shelf

    def _opts_interval(self, t):
        s, d = self.s, self.d
        if s["shop"]["shelf"] is None:
            s["shop"]["shelf"] = self._interval_shelf(f"hon/interval/shelf#r{t['rolls']}")
        out = []
        for i, it in enumerate(s["shop"]["shelf"]):
            k = it["what"]
            if k == "card":
                lab, dis = f"买 {self.card_name(it['id'], it['up'])}（{it['price']}P）", None
            elif k == "change":
                lab, dis = f"换成 {self.card_name(it['id'], it['up'])}（{it['price']}P，换掉牌组里 1 张，保留强化）", None
            elif k == "drink":
                lab, dis = f"买 {d.drinks[it['id']].get('name') or it['id']}（{it['price']}P）", None
            elif k == "upgrade":
                price = d.models["interval"]["upgrade_price"][min(t.get("up_bought", t["upgrades"]), 1)]
                lab = f"强化 1 张（{price}P）"
                dis = "本次已强化" if t["upgrades"] >= int(d.setting(MAIN)["stepIntervalUpgradeProduceCardCount"]) else None
                it["price"] = price
            elif k == "customize":
                lab = "定制（按定制项价格）"
                dis = "本次已定制 2 张" if t["customized"] >= int(d.setting(MAIN)["stepIntervalCustomizeProduceCardCount"]) else None
            else:
                lab = f"回复体力 +{d.models['interval']['stamina_per_10p']}（10P，可买多次）"
                dis = "体力已满" if s["stamina"] >= s["max_stamina"] else None
            if not dis and it.get("bought") and k in ("card", "change", "drink"):
                dis = "已买"
            if not dis and s["pp"] < it["price"]:
                dis = "P点不足"
            out.append({"label": lab, "slot": i, "disabled": dis})
        costs = d.models["interval"]["refresh_cost"]
        cost = costs[min(t["rolls"], len(costs) - 1)]
        out.append({"label": f"刷新货架（{cost}P）", "refresh": True, "cost": cost,
                    "disabled": "P点不足" if s["pp"] < cost else None})
        out.append({"label": "离开（进入 Round 2）", "leave": True})
        return out

    def _do_interval(self, t, o):
        s, d = self.s, self.d
        if o.get("leave"):
            s["queue"].pop(0)
            s["shop"]["shelf"] = None
            return
        if o.get("refresh"):
            s["pp"] -= o["cost"]
            t["rolls"] += 1
            s["shop"]["shelf"] = None
            t["upgrades"], t["customized"], t["cards"] = 0, 0, []
            self.log(f"刷新货架（{o['cost']}P）", kind="choice")
            return
        it = s["shop"]["shelf"][o["slot"]]
        k = it["what"]
        if k == "card":
            s["pp"] -= it["price"]
            it["bought"] = True
            self._new_card(it["id"], it["up"])
        elif k == "drink":
            s["pp"] -= it["price"]
            it["bought"] = True
            self._gain_drink(it["id"])
        elif k == "stamina":
            s["pp"] -= it["price"]
            self._add_stamina(d.models["interval"]["stamina_per_10p"])
            self.log(f"回复体力 +{d.models['interval']['stamina_per_10p']}", kind="gain")
        elif k == "change":
            self._push({"t": "interval_target", "slot": o["slot"], "purpose": "change", "need": None,
                        "title": "选择要换掉的卡（保留强化）"})
        elif k == "upgrade":
            self._push({"t": "interval_target", "slot": o["slot"], "purpose": "upgrade", "need": "upgrade",
                        "title": "选择要强化的卡"})
        else:
            self._push({"t": "customize_pick_card", "title": "选择要定制的卡"})

    def _opts_interval_target(self, t):
        cards = self._deck_options(None, t["need"])
        return [{"label": self.card_name(c["id"], c["up"]), "n": c["n"]} for c in cards] + [{"label": "返回", "n": None}]

    def _do_interval_target(self, t, o):
        s = self.s
        s["queue"].pop(0)
        if o["n"] is None:
            return
        iv = s["queue"][0]
        it = s["shop"]["shelf"][t["slot"]]
        c = self._card_by_number(o["n"])
        s["pp"] -= it["price"]
        if t["purpose"] == "change":
            it["bought"] = True
            old_up = c["up"]
            c["deleted"] = True
            up = old_up if (it["id"], old_up) in self.d.cards else 0
            self._new_card(it["id"], up, fire=False)
            self.log(f"换卡：{self.card_name(c['id'], old_up)} → {self.card_name(it['id'], up)}", kind="card")
            self._push({"t": "fire", "phase": "CHANGE_PRODUCE_CARD", "ev": {"card": c["id"], "upgrade": old_up}})
        else:
            iv["upgrades"] += 1
            iv["up_bought"] = iv.get("up_bought", 0) + 1
            self._upgrade_card(c, "interval")

    def _opts_customize_pick_card(self, t):
        cards = [c for c in sorted(self._deck(), key=lambda c: c["n"]) if self._customize_choices(c, raise_up=True)]
        return [{"label": f"定制 {self.card_name(c['id'], c['up'])}" + ("（会强化为+）" if c["up"] == 0 else ""), "n": c["n"]}
                for c in cards] + [{"label": "返回", "n": None}]

    def _do_customize_pick_card(self, t, o):
        s = self.s
        s["queue"].pop(0)
        if o["n"] is not None:
            self._push({"t": "customize_pick", "n": o["n"], "title": "选定制项", "interval": True})

    # -- the end: rating, grade, the memory --------------------------------------------------------------------------
    def _auto_main_end(self, t):
        from GakumasCLI.produce import produce_effects as pe
        s = self.s
        a1, a2 = s["auditions"].get("mid1") or {}, s["auditions"].get("final") or {}
        params = [s["params"][a] for a in ATTRS]
        round1 = int(a1.get("score", 0)) * 12 // 10
        rating = pe.hif_rating(params, a2.get("star_before", s["star"]), round1, int(a2.get("score", 0)), s["star_permil"])
        grade = pe.rank_of(rating)
        mem = self.generate_memory(rating, grade, "memory/gen#regen0")
        s["done"] = True
        s["queue"].clear()
        s["result"] = {"outcome": "memory", "rating": rating, "grade": grade, "round1_point": round1,
                       "round2": int(a2.get("score", 0)), "memory": mem}
        self.log(f"本戦结束：评价 {rating:,}（{grade}）", kind="end")

    def generate_memory(self, rating, grade, label):
        """The memory the 本戦 makes: the parameters and stamina, 6 exam cards from the final deck, a start card, the
        P-items with exam effects of the idol and the support cards, 3 ordinary abilities and one H.I.F ability of the
        idol's plan."""
        s, d = self.s, self.d
        rs = self.rng(label)
        idol_card = d.idols.cards[s["idol"]]["produceCardId"]
        eligible = []
        for c in sorted(self._deck(), key=lambda c: c["n"]):
            row = d.card(c["id"])
            rar = name(row.get("rarity"), "PRODUCE_CARD_RARITY_")
            if rar in ("N", "LEGEND") or name(row.get("category")) == "PRODUCE_CARD_CATEGORY_TROUBLE":
                continue
            eligible.append(c)
        picks = [c for c in eligible if c["id"] == idol_card][:1]
        rest = [c for c in eligible if c not in picks and c["id"] not in {p["id"] for p in picks}]
        while len(picks) < 6 and rest:
            ws = [CARD_WEIGHT["support" if d.card(c["id"]).get("originSupportCardId") else "pool"] for c in rest]
            c = rest.pop(rs.weighted(ws))
            if c["id"] not in {p["id"] for p in picks}:
                picks.append(c)
        start_pool = [c for c in eligible if c["id"] != idol_card] or eligible
        start = start_pool[rs.uniform(len(start_pool))] if start_pool else None
        phase = 1
        if start:
            rar = name(d.card(start["id"]).get("rarity"), "PRODUCE_CARD_RARITY_")
            phase = 2 if rs.uniform(1000) < PHASE2_PERMIL.get(rar, 0) else 1
        from GakumasCLI.produce import produce_effects as pe
        number = next((n for n, g in pe.GRADE.items() if g == grade), 10)
        tier_w = ABILITY_TIER_BY_GRADE[max(10, min(18, number))]
        fams = sorted(ABILITY_COUNTS)
        abilities = []
        while len(abilities) < 3 and fams:
            f = fams.pop(rs.weighted([ABILITY_COUNTS[x] for x in fams]))
            tiers = [x for x in sorted(tier_w)
                     if f"memory_ability-p_memory_skill-common-{f}-{x}-001-001" in d.M.memory_abilities]
            if tiers:
                tier = tiers[rs.weighted([tier_w[x] for x in tiers])]
                abilities.append({"id": f"memory_ability-p_memory_skill-common-{f}-{tier}-001-001", "level": 1})
        plan_no = s["plan"][-1]
        hif = sorted(a for a in d.M.memory_abilities if "for_hif_memory" in a and self._hif_plan(a) == plan_no)
        if hif:
            pos = sorted(HIF_POSITION)
            at = pos[rs.weighted([HIF_POSITION[x] for x in pos])]
            abilities.insert(min(at, len(abilities)), {"id": hif[rs.uniform(len(hif))], "level": 1})
        items = []
        for it in s["items"]:
            row = d.items.get(it["id"]) or {}
            if row.get("isExamEffect") and (row.get("originIdolCardId") or row.get("originSupportCardId")):
                items.append(it["id"])
        return {"format": "gkms.ps.memory.v1", "source": "simulated", "root": s["root"], "idol_card_id": s["idol"],
                "character_id": s["character"], "grade": grade, "rating": rating,
                "vocal": s["params"]["vocal"], "dance": s["params"]["dance"], "visual": s["params"]["visual"],
                "stamina": s["max_stamina"],
                "produce_card": ({"id": start["id"], "upgrade_count": start["up"],
                                  "customizes": [{"id": z[0], "customize_count": z[1]} for z in start.get("cz") or ()]}
                                 if start else None),
                "produce_card_phase_type": phase,
                "exam_battle_produce_cards": [{"id": c["id"], "upgrade_count": c["up"],
                                               "customizes": [{"id": z[0], "customize_count": z[1]} for z in c.get("cz") or ()]}
                                              for c in picks],
                "exam_battle_produce_item_ids": items, "abilities": abilities}

    def _hif_plan(self, ability_id):
        import re
        sk = self.d.M.memory_skill(ability_id)
        ench = self.d.effect(sk.effect_id).get("produceExamStatusEnchantId") if sk else ""
        m = re.search(r"p_card-(\d\d)-", ench or "")
        return m.group(1)[-1] if m else None
