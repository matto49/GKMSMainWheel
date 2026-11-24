# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Everything the simulator reads, loaded once per process (the website shares one copy across sessions)."""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

from GakumasCLI.produce import produce_effects as pe

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "data"
ATTRS = ("vocal", "dance", "visual")
PARAM_TYPE = {"PRODUCE_PARAMETER_TYPE_VOCAL": "vocal", "PRODUCE_PARAMETER_TYPE_DANCE": "dance",
              "PRODUCE_PARAMETER_TYPE_VISUAL": "visual"}
TRUE_ENDS = ("FirstStar", "NextIdolAudition", "HatsuboshiIdolFestival")
PANEL_SHEETS = ("produce_growth_panel_sheet-hif-%02d" % i for i in range(1, 10))


def name(v, prefix=""):
    return pe._name(v, prefix)


class Data:
    def __init__(self, master=pe.MASTER_DIR):
        from GakumasCLI.produce.hif_start import IdolCards
        rows = lambda t: pe._rows(t, master)                         # noqa: E731
        self.M = pe.Model(master)
        self.cs = pe.CardSearch(master)
        self.idols = IdolCards(master)
        self.cards = {(r["id"], r.get("upgradeCount") or 0): r for r in rows("ProduceCard")}
        self.drinks = {r["id"]: r for r in rows("ProduceDrink")}
        self.items = self.M.items
        self.effects = self.M.effects
        self.details = {r["id"]: r for r in rows("ProduceStepEventDetail")}
        self.suggestions = {r["id"]: r for r in rows("ProduceStepEventSuggestion")}
        self.open_lessons = {r["id"]: r for r in rows("ProduceStepOpenLesson")}
        self.customizes = {r["id"]: r for r in rows("ProduceCardCustomize")}
        self.customize_levels = defaultdict(dict)
        for r in rows("ProduceCardCustomize"):
            self.customize_levels[r["id"]][int(r.get("customizeCount") or 1)] = r
        self.citems = {r["id"]: r for r in rows("ProduceCustomizeItem")}
        self.citem_children = defaultdict(list)
        for r in rows("ProduceCustomizeItemRelationship"):
            self.citem_children[r["parentProduceCustomizeItemId"]].append(r["childProduceCustomizeItemId"])
        self.support_cards = {r["id"]: r for r in rows("SupportCard")}
        self.support_events = defaultdict(list)
        for r in rows("ProduceEventSupportCard"):
            self.support_events[r["supportCardId"]].append((r["number"], r.get("supportCardLevel") or 0,
                                                            r["produceStepEventDetailId"]))
        self.ll_card_upgrade, self.ll_second_upgrade = {}, {}
        for r in rows("IdolCardLevelLimitStatusUp"):
            rank = int((r.get("rank") or {}).get("number") or 0) if isinstance(r.get("rank"), dict) else int(r.get("rank") or 0)
            kinds = {name(x) for x in r.get("effectTypes") or ()}
            if "IDOL_CARD_LEVEL_LIMIT_EFFECT_TYPE_PRODUCE_CARD_UPGRADE" in kinds:
                self.ll_card_upgrade[r["id"]] = min(rank, self.ll_card_upgrade.get(r["id"], 99))
            if "IDOL_CARD_LEVEL_LIMIT_EFFECT_TYPE_SECOND_PRODUCE_CARD_UPGRADE" in kinds:
                self.ll_second_upgrade[r["id"]] = min(rank, self.ll_second_upgrade.get(r["id"], 99))
        self.panels = {(r["id"], r["level"]): r for r in rows("ProduceGrowthPanel")}
        self.panel_max = defaultdict(int)
        for (pid, lv) in self.panels:
            self.panel_max[pid] = max(self.panel_max[pid], lv)
        self.conversions = list(rows("ProduceCardConversion"))
        self.settings = {r["id"]: r for r in rows("ProduceSetting")}
        self.initial_deck = {(r["produceId"], name(r.get("examEffectType"))): r["examInitialDeckId"]
                             for r in rows("ProduceInitialDeck")}
        self.exam_initial = {r["id"]: r for r in rows("ExamInitialDeck")}
        self.difficulty_rows = defaultdict(list)
        for r in rows("ProduceStepAuditionDifficulty"):
            self.difficulty_rows[r["id"]].append(r)
        self.npc_groups = defaultdict(list)
        for r in rows("ProduceExamBattleNpcGroup"):
            self.npc_groups[r["id"]].append(r)
        self.battle = {r["id"]: r for r in rows("ProduceExamBattleConfig")}
        with open(DATA / "master_ps.json", encoding="utf-8") as f:
            self.extra = json.load(f)
        with open(DATA / "draw_models.v1.json", encoding="utf-8") as f:
            self.models = json.load(f)
        from GakumasCLI.produce.offer_coverage import card_set
        support_set, _rows, _idols = card_set()
        self.offer_pool = defaultdict(list)
        for cid, v in sorted(support_set.items()):
            if "support_set" in v["sources"]:
                self.offer_pool[v["plan"]].append(cid)

    # -- lookups ----------------------------------------------------------------------------------------------------
    def card(self, cid, up=0):
        return self.cards.get((cid, up)) or self.cards.get((cid, 0)) or {}

    def effect(self, eid):
        return self.effects.get(eid) or {}

    def effect_type(self, eid):
        return name(self.effect(eid).get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")

    def setting(self, produce_id):
        return self.settings["p_setting-" + produce_id[-1]]

    def difficulty(self, idol_card_id, produce_id, step_tail):
        did = pe.difficulty_id(idol_card_id)
        for r in self.difficulty_rows.get(did, ()):
            if r.get("produceId") == produce_id and name(r.get("stepType")).endswith(step_tail):
                return r
        raise KeyError((idol_card_id, produce_id, step_tail))

    def panel_effects(self, sheet, level):
        r = self.panels.get((sheet, level))
        return list(r.get("produceEffectIds") or ()) if r else []

    def own_card_upgrade(self, idol_card_id, ll_rank):
        row = self.idols.cards[idol_card_id]
        need = self.ll_card_upgrade.get(row.get("idolCardLevelLimitStatusUpId"))
        return 1 if need is not None and ll_rank >= need else 0

    def second_card(self, idol_card_id, ll_rank):
        """(the second unique card, its upgrade) or None."""
        row = self.idols.cards[idol_card_id]
        cid = row.get("secondProduceCardId")
        if not cid:
            return None
        need = self.ll_second_upgrade.get(row.get("idolCardLevelLimitStatusUpId"))
        return cid, (1 if need is not None and ll_rank >= need and (cid, 1) in self.cards else 0)

    def change_upgrade_target(self, skill_id):
        """The card a PRODUCE_CARD_CHANGE_UPGRADE skill turns a starter into: the last card its description names
        (「AかBをCにチェンジして強化」)."""
        if "change_targets" not in self.__dict__:
            self.change_targets = {}
            for r in pe._rows("ProduceSkill"):
                if "produce_card_change_upgrade" not in r["id"]:
                    continue
                named = [x.get("target_id") for x in r.get("produceDescriptions") or ()
                         if name(x.get("produce_description_type")) == "PRODUCE_DESCRIPTION_TYPE_PRODUCE_CARD"]
                if named:
                    self.change_targets[r["id"]] = named[-1]
        return self.change_targets.get(skill_id)

    def prima_card(self, idol_card_id):
        """The プリマステラ card of an idol card (ProduceCard.originPrimaStellaIdolCardId), or None."""
        if "prima" not in self.__dict__:
            self.prima = {}
            for (cid, up), r in self.cards.items():
                if up == 0 and r.get("originPrimaStellaIdolCardId"):
                    self.prima[r["originPrimaStellaIdolCardId"]] = cid
        return self.prima.get(idol_card_id)

    def plan_of(self, idol_card_id):
        return name(self.idols.cards[idol_card_id].get("planType"), "PRODUCE_PLAN_TYPE_")

    def exam_effect_type(self, idol_card_id):
        return name(self.idols.cards[idol_card_id].get("examEffectType"))

    def default_account(self):
        """The default account: producer level, affection and every growth panel at their maximum."""
        return {"producer_level": 90, "dearness_level": 37, "true_ends": list(TRUE_ENDS),
                "growth_panel": {k: v for k, v in sorted(self.panel_max.items())}}


_LOCK = threading.Lock()
_DATA = {}


def data():
    with _LOCK:
        if "d" not in _DATA:
            _DATA["d"] = Data()
        return _DATA["d"]
