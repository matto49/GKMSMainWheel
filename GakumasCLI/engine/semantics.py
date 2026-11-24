# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""neu.semantics - the symbolic layer between the master tables and the engine."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"

# --------------------------------------------------------------------------- symbols
with open(_DATA_DIR / "exam_enums.json", encoding="utf-8") as _fh:
    _ENUMS = {name: {int(k): v for k, v in table.items()} for name, table in json.load(_fh).items()}


def enum_name(enum, number, default=None):
    """Client enum name for ``number`` (e.g. ``enum_name("ProduceExamEffectType", 156)``)."""
    table = _ENUMS.get(enum) or {}
    if number is None:
        return default
    return table.get(int(number), default if default is not None else f"{enum}#{number}")


def enum_number(enum, name):
    table = _ENUMS.get(enum) or {}
    for number, label in table.items():
        if label == name or label.endswith("_" + name):
            return number
    raise KeyError(f"{enum}: {name}")


def enums():
    return _ENUMS


# --------------------------------------------------------------------------- records
@dataclass(frozen=True)
class EffectDef:
    id: str
    type_number: int
    type_name: str
    value1: int | None
    value2: int | None
    turn: int | None
    count: int | None
    card_search_id: str | None
    grow_effect_ids: tuple
    child_effect_ids: tuple
    effect_group_ids: tuple
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class TriggerDef:
    id: str
    phase_numbers: tuple
    phase_names: tuple
    phase_values: tuple
    field_status_numbers: tuple
    field_status_names: tuple
    field_status_values: tuple
    check_types: tuple
    effect_type_numbers: tuple
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class GrowDef:
    id: str
    type_number: int
    type_name: str
    value: int | None
    play_effect_id: str | None
    target_effect_ids: tuple
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class CustomizeDef:
    id: str
    count: int | None
    grow_effect_ids: tuple
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class EnchantDef:
    id: str
    trigger_id: str | None
    effect_ids: tuple
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class CardDef:
    id: str
    name: str
    upgrade_count: int
    plan_number: int | None
    plan_name: str
    category_number: int | None
    category_name: str
    stamina: int | None
    force_stamina: int | None
    cost_type_name: str | None
    cost_value: int | None
    play_effect_ids: tuple
    play_effect_trigger_ids: tuple
    customize_ids: tuple
    status_enchant_id: str | None
    effect_group_ids: tuple
    is_initial: bool
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class ItemDef:
    id: str
    name: str
    plan_name: str
    item_effect_ids: tuple
    enchant_ids: tuple
    raw: dict = field(compare=False, repr=False)


@dataclass(frozen=True)
class SourceEffectDef:
    id: str
    effect_id: str


def _num(node):
    if isinstance(node, dict):
        return node.get("number")
    return node


def _name(node):
    if isinstance(node, dict):
        return node.get("name") or ""
    return ""


class MasterCatalog:
    TABLES = {
        "effects": "ProduceExamEffect",
        "triggers": "ProduceExamTrigger",
        "cards": "ProduceCard",
        "customizes": "ProduceCardCustomize",
        "grows": "ProduceCardGrowEffect",
        "enchants": "ProduceExamStatusEnchant",
        "items": "ProduceItem",
        "item_effects": "ProduceItemEffect",
        "gimmick_groups": "ProduceExamGimmickEffectGroup",
        "drink_effects": "ProduceDrinkEffect",
        "searches": "ProduceCardSearch",
        "exam_setting": "ExamSetting",
        "setting": "Setting",
        "produce_effects": "ProduceEffect",
        "produce_triggers": "ProduceTrigger",
        "memory_abilities": "MemoryAbility",
        "produce_skills": "ProduceSkill",
    }

    def __init__(self, master_dir, row_source=None):
        self.master_dir = Path(master_dir)
        self._rows = row_source or self._jsonl_rows
        self.effects = {}
        self.triggers = {}
        self.grows = {}
        self.customizes = {}
        self.enchants = {}
        self.cards = {}
        self.items = {}
        self.item_effects = {}
        self._status_enchant_effect_rows = defaultdict(list)
        self._status_enchant_item_owners = defaultdict(list)
        self.gimmick_groups = []
        self.drink_effects = []
        self.searches = {}
        self.exam_setting = {}
        self.setting = {}
        self.produce_effects = {}
        self.produce_triggers = {}
        self.memory_abilities = {}
        self.produce_skills = {}
        self._load()

    # -- row source ------------------------------------------------------
    def _jsonl_rows(self, table):
        path = self.master_dir / f"{table}.jsonl"
        if not path.exists():
            return []
        out = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("decode_status", "decoded") != "decoded":
                    continue
                out.append(row["decoded"])
        return out

    # -- loading ---------------------------------------------------------
    def _load(self):
        for row in self._rows(self.TABLES["effects"]):
            t = _num(row.get("effectType"))
            self.effects[row["id"]] = EffectDef(
                id=row["id"], type_number=int(t or 0), type_name=enum_name("ProduceExamEffectType", t, _name(row.get("effectType"))),
                value1=row.get("effectValue1"), value2=row.get("effectValue2"), turn=row.get("effectTurn"), count=row.get("effectCount"),
                card_search_id=row.get("produceCardSearchId"),
                grow_effect_ids=tuple(row.get("produceCardGrowEffectIds") or ()),
                child_effect_ids=tuple(row.get("produceExamEffectIds") or ()),
                effect_group_ids=tuple(row.get("effectGroupIds") or ()), raw=row)
        for row in self._rows(self.TABLES["triggers"]):
            phases = tuple(int(_num(p)) for p in row.get("phaseTypes") or ())
            fss = tuple(int(_num(p)) for p in row.get("fieldStatusTypes") or ())
            self.triggers[row["id"]] = TriggerDef(
                id=row["id"], phase_numbers=phases, phase_names=tuple(enum_name("ProduceExamPhaseType", p) for p in phases),
                phase_values=tuple(row.get("phaseValues") or ()), field_status_numbers=fss,
                field_status_names=tuple(enum_name("ProduceExamFieldStatusType", f) for f in fss),
                field_status_values=tuple(row.get("fieldStatusValues") or ()),
                check_types=tuple(int(_num(c)) for c in row.get("fieldStatusCheckTypes") or ()),
                effect_type_numbers=tuple(int(_num(e)) for e in row.get("effectTypes") or ()), raw=row)
        for row in self._rows(self.TABLES["grows"]):
            t = _num(row.get("effectType"))
            self.grows[row["id"]] = GrowDef(
                id=row["id"], type_number=int(t or 0), type_name=enum_name("ProduceCardGrowEffectType", t, _name(row.get("effectType"))),
                value=row.get("value"), play_effect_id=row.get("playProduceExamEffectId"),
                target_effect_ids=tuple(row.get("targetPlayProduceExamEffectIds") or ()), raw=row)
        for row in self._rows(self.TABLES["customizes"]):
            self.customizes[row["id"]] = CustomizeDef(id=row["id"], count=row.get("customizeCount"),
                                                      grow_effect_ids=tuple(row.get("produceCardGrowEffectIds") or ()), raw=row)
        for row in self._rows(self.TABLES["enchants"]):
            self.enchants[row["id"]] = EnchantDef(id=row["id"], trigger_id=row.get("produceExamTriggerId"),
                                                  effect_ids=tuple(row.get("produceExamEffectIds") or ()), raw=row)
        for row in self._rows(self.TABLES["cards"]):
            plan = _num(row.get("planType")); cat = _num(row.get("category"))
            up = int(row.get("upgradeCount") or 0)
            self.cards[(row["id"], up)] = CardDef(
                id=row["id"], name=row.get("name", ""), upgrade_count=up, plan_number=plan,
                plan_name=enum_name("ProducePlanType", plan, _name(row.get("planType"))), category_number=cat,
                category_name=enum_name("ProduceCardCategory", cat, _name(row.get("category"))),
                stamina=row.get("stamina"), force_stamina=row.get("forceStamina"), cost_type_name=_name(row.get("costType")) or None,
                cost_value=row.get("costValue"),
                play_effect_ids=tuple(pe.get("produceExamEffectId") for pe in row.get("playEffects") or ()),
                play_effect_trigger_ids=tuple(pe.get("produceExamTriggerId") for pe in row.get("playEffects") or ()),
                customize_ids=tuple(row.get("produceCardCustomizeIds") or ()), status_enchant_id=row.get("produceCardStatusEnchantId"),
                effect_group_ids=tuple(row.get("effectGroupIds") or ()), is_initial=bool(row.get("isInitial")), raw=row)
        for row in self._rows(self.TABLES["item_effects"]):
            self.item_effects[row["id"]] = row
            enchant_id = row.get("produceExamStatusEnchantId")
            if enchant_id:
                self._status_enchant_effect_rows[enchant_id].append(row["id"])
        for row in self._rows(self.TABLES["items"]):
            ie = tuple(row.get("produceItemEffectIds") or ())
            ench = tuple(self.item_effects.get(x, {}).get("produceExamStatusEnchantId") for x in ie)
            self.items[row["id"]] = ItemDef(id=row["id"], name=row.get("name", ""), plan_name=_name(row.get("planType")),
                                            item_effect_ids=ie, enchant_ids=tuple(e for e in ench if e), raw=row)
        for item in self.items.values():
            for item_effect_id in item.item_effect_ids:
                item_effect = self.item_effects.get(item_effect_id) or {}
                enchant_id = item_effect.get("produceExamStatusEnchantId")
                if enchant_id:
                    self._status_enchant_item_owners[enchant_id].append(item)
        for row in self._rows(self.TABLES["gimmick_groups"]):
            effect_id = row.get("produceExamEffectId")
            if effect_id:
                self.gimmick_groups.append(SourceEffectDef(row["id"], effect_id))
        for row in self._rows(self.TABLES["drink_effects"]):
            effect_id = row.get("produceExamEffectId")
            if effect_id:
                self.drink_effects.append(SourceEffectDef(row["id"], effect_id))
        for row in self._rows(self.TABLES["searches"]):
            self.searches[row["id"]] = row
        for row in self._rows(self.TABLES["exam_setting"]):
            self.exam_setting = row
            break
        for row in self._rows(self.TABLES["setting"]):
            self.setting = row
            break
        for row in self._rows(self.TABLES["produce_effects"]):
            self.produce_effects[row["id"]] = row
        for row in self._rows(self.TABLES["produce_triggers"]):
            self.produce_triggers[row["id"]] = row
        for row in self._rows(self.TABLES["memory_abilities"]):
            self.memory_abilities[row["id"]] = row
        for row in self._rows(self.TABLES["produce_skills"]):
            level = row.get("level")
            try:
                level = int(level) if level is not None else None
            except (TypeError, ValueError):
                pass
            self.produce_skills[(row["id"], level)] = row

    @staticmethod
    def _enchant_owner_id(enchant_id):
        owner_id = enchant_id.removeprefix("enchant-")
        stem, separator, suffix = owner_id.rpartition("-enc")
        if separator and suffix.isdigit():
            return stem
        return owner_id

    @staticmethod
    def _in_tower_scope(identifier):
        """Idol Road / Contest scope: the game names it ``tower*`` / ``contest*``."""
        return any(token in (identifier or "") for token in ("tower", "contest"))

    def _status_enchant_in_produce_scope(self, enchant):
        """Return whether a status enchant belongs to the default Produce scope."""
        enchant_id = enchant.id
        if any(scope in enchant_id for scope in ("tower", "contest")):
            return False
        if "challenge" in enchant_id:
            return False

        owner_id = self._enchant_owner_id(enchant_id)
        owner_item = self.items.get(owner_id)
        item_owners = list(self._status_enchant_item_owners.get(enchant_id, ()))
        if owner_item is not None:
            item_owners.append(owner_item)
        if any(item.raw.get("libraryHidden") is True for item in item_owners):
            return False

        owner_is_card = any(card.id == owner_id for card in self.cards.values())
        has_effect_pointer = bool(self._status_enchant_effect_rows.get(enchant_id))
        return owner_item is not None or owner_is_card or has_effect_pointer

    # -- symbolic helpers -------------------------------------------------
    def effect_tree(self, effect_id, depth=0, seen=None):
        """Yield (depth, EffectDef) for an effect and its nested child effects."""
        seen = seen if seen is not None else set()
        e = self.effects.get(effect_id)
        if e is None or effect_id in seen or depth > 4:
            return
        seen.add(effect_id)
        yield depth, e
        for child in e.child_effect_ids:
            yield from self.effect_tree(child, depth + 1, seen)

    def card_effect_types(self, card):
        out = []
        for eid in card.play_effect_ids:
            out.extend(e.type_number for _, e in self.effect_tree(eid))
        if card.status_enchant_id and card.status_enchant_id in self.enchants:
            for eid in self.enchants[card.status_enchant_id].effect_ids:
                out.extend(e.type_number for _, e in self.effect_tree(eid))
        return out

    def item_effect_types(self, item):
        out = []
        for item_effect_id in item.item_effect_ids:
            item_effect = self.item_effects.get(item_effect_id) or {}
            effect_id = item_effect.get("produceEffectId") or item_effect.get("produceExamEffectId")
            if effect_id:
                out.extend(e.type_number for _, e in self.effect_tree(effect_id))
        for enchant_id in item.enchant_ids:
            enchant = self.enchants.get(enchant_id)
            if not enchant:
                continue
            for effect_id in enchant.effect_ids:
                out.extend(e.type_number for _, e in self.effect_tree(effect_id))
        return out

    def enchant_effect_types(self, enchant):
        out = []
        for effect_id in enchant.effect_ids:
            out.extend(e.type_number for _, e in self.effect_tree(effect_id))
        return out

    def card_grow_types(self, card):
        out = []
        for cid in card.customize_ids:
            c = self.customizes.get(cid)
            if not c:
                continue
            for gid in c.grow_effect_ids:
                g = self.grows.get(gid)
                if g:
                    out.append(g.type_number)
        return out

    def describe_card(self, card_id, upgrade=0):
        """Human/LLM readable symbolic description of one card row."""
        card = self.cards[(card_id, upgrade)]
        lines = [f"{card.name} ({card.id}+{card.upgrade_count}) plan={card.plan_name} category={card.category_name} "
                 f"stamina={card.stamina} force={card.force_stamina} cost={card.cost_type_name}:{card.cost_value}"]
        for eid, tid in zip(card.play_effect_ids, card.play_effect_trigger_ids):
            cond = ""
            if tid and tid in self.triggers:
                t = self.triggers[tid]
                cond = " if " + ",".join(f"{n}={v}" for n, v in zip(t.field_status_names, t.field_status_values)) or ""
            for depth, e in self.effect_tree(eid):
                lines.append("  " * (depth + 1) + f"- {e.type_name}[{e.type_number}] v1={e.value1} v2={e.value2} turn={e.turn} count={e.count}"
                             + (f" search={e.card_search_id}" if e.card_search_id else "")
                             + (f" grows={list(e.grow_effect_ids)}" if e.grow_effect_ids else "") + cond)
        if card.status_enchant_id:
            en = self.enchants.get(card.status_enchant_id)
            if en:
                t = self.triggers.get(en.trigger_id or "")
                lines.append(f"  enchant {en.id} trigger={en.trigger_id} phases={t.phase_names if t else None}")
                for eid in en.effect_ids:
                    for depth, e in self.effect_tree(eid):
                        lines.append("  " * (depth + 2) + f"- {e.type_name}[{e.type_number}] v1={e.value1} v2={e.value2}")
        for cid in card.customize_ids:
            c = self.customizes.get(cid)
            if c:
                lines.append(f"  customize {cid} count={c.count}: " + ", ".join(
                    f"{self.grows[g].type_name}[{self.grows[g].type_number}]={self.grows[g].value}" for g in c.grow_effect_ids if g in self.grows))
        return "\n".join(lines)

    def memory_ability_activations(self, ability_id):
        """Return the fully joined activation triples for one memory ability."""
        from GakumasCLI.engine.produce_run import join_memory_ability
        return join_memory_ability({"memory_abilities": self.memory_abilities, "produce_skills": self.produce_skills,
                                    "produce_effects": self.produce_effects,
                                    "produce_triggers": self.produce_triggers}, ability_id)


# --------------------------------------------------------------------------- registry
# status: calibrated = exercised by corpus grids; master_only = implemented from
# master + public text, never calibrated; partial = known incomplete route.
ENGINE_IMPLEMENTATION = {
    "effect_types": {
        **{t: "calibrated" for t in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 14, 18, 19, 21, 22, 23, 27, 28, 31, 38, 39, 42, 45, 46, 49,
                                     56, 59, 63, 66, 76, 86, 93, 98, 103, 117, 119, 125, 127, 129, 132, 141, 143, 152, 156,
                                     167, 171, 178)},
        69: "partial",
        36: "master_only",
        149: "master_only",
        120: "master_only",
        130: "master_only",
        15: "partial",
        182: "master_only",
        237: "master_only",
        174: "master_only",
        211: "master_only",
        176: "master_only",
        212: "master_only",
        177: "master_only",
        124: "master_only",
        131: "master_only",
        147: "master_only",
        142: "master_only",
        186: "master_only",
        133: "master_only",
        159: "master_only",
        175: "master_only",
        181: "master_only",
        60: "master_only",
        170: "master_only",
        183: "master_only",
        148: "master_only",
        155: "master_only",
        172: "master_only",
        173: "master_only",
        210: "master_only",
        199: "master_only",
        166: "master_only",
        144: "master_only",
        163: "master_only",
        126: "master_only",
        202: "master_only",
        168: "master_only",
        188: "master_only",
        189: "master_only",
        190: "master_only",
        13: "master_only",
        158: "master_only",
        153: "master_only",
        151: "master_only",
        154: "master_only",
        180: "master_only",
        195: "partial",
        30: "partial",
        107: "calibrated",
        29: "master_only",
        73: "master_only",
        84: "master_only",
        105: "calibrated",
        106: "calibrated",
        113: "calibrated",
        114: "calibrated",
        115: "calibrated",
        51: "master_only",
        219: "master_only",
    },
    "grow_types": {
        **{t: "calibrated" for t in (1, 3, 5, 12, 13, 32, 49, 50, 51, 52, 22)},
        7: "master_only",
        48: "master_only",
        14: "master_only",
        28: "master_only",
        39: "master_only",
        34: "master_only",
        38: "master_only",
        18: "master_only",
        20: "master_only",
        16: "master_only",
        26: "master_only",
        **{t: "master_only" for t in (40, 41, 42, 43, 44, 45, 46, 47, 53, 54)},
        35: "master_only",
        36: "master_only",
        37: "master_only",
    },
    "phases": {
        13: "partial",
        34: "partial",
        **{p: "calibrated" for p in (2, 3, 4, 5, 22, 23)},
        21: "calibrated",
        **{p: "master_only" for p in (6, 28, 35, 41, 43, 44)},
        25: "calibrated",
        37: "master_only",
        38: "master_only",
        39: "master_only",
        47: "master_only",
    },
    "state_neutral_effect_types": (4, 9, 11),
}

_HIF_CALIBRATED = {
    "effect_types": {15: 42, 36: 29, 124: 31, 151: 31, 154: 40, 174: 2, 176: 19,
                     177: 31, 182: 29, 202: 31, 211: 42, 219: 31},
    "phases": {6: 73, 13: 31, 25: 8, 27: 62, 44: 40},
}
for _kind, _entries in _HIF_CALIBRATED.items():
    for _number in _entries:
        ENGINE_IMPLEMENTATION[_kind][_number] = "calibrated"

for _number in (48, 78):
    ENGINE_IMPLEMENTATION["effect_types"].setdefault(_number, "master_only")
for _number in (20, 24, 53):
    ENGINE_IMPLEMENTATION["phases"].setdefault(_number, "master_only")
# phase 52 (好印象N回増加, the 53 rule on 好印象; 初星ライト（オレンジ） pitem_02-0-002-0-023).
ENGINE_IMPLEMENTATION["phases"].setdefault(52, "master_only")

for _number in (29, 30, 31):
    ENGINE_IMPLEMENTATION["phases"].setdefault(_number, "master_only")
ENGINE_IMPLEMENTATION["effect_types"].setdefault(24, "partial")
for _number in (150, 187):
    ENGINE_IMPLEMENTATION["effect_types"].setdefault(_number, "master_only")


def implemented_effect_types(include=("calibrated", "master_only", "partial")):
    return {t for t, s in ENGINE_IMPLEMENTATION["effect_types"].items() if s in include}


def implemented_grow_types(include=("calibrated", "master_only", "partial")):
    return {t for t, s in ENGINE_IMPLEMENTATION["grow_types"].items() if s in include}


def implemented_phases(include=("calibrated", "master_only", "partial")):
    return {p for p, s in ENGINE_IMPLEMENTATION["phases"].items() if s in include}


# --------------------------------------------------------------------------- coverage
def coverage_report(catalog, plans=("PRODUCE_PLAN_TYPE_PLAN1", "PRODUCE_PLAN_TYPE_PLAN2", "PRODUCE_PLAN_TYPE_PLAN3", "PRODUCE_PLAN_TYPE_COMMON"), all_scopes=False, scope=None):
    """Per plan: effect / grow / phase types used by cards or items but absent
    from ENGINE_IMPLEMENTATION, with example names.
    """
    scope = scope or ("all" if all_scopes else "produce")
    if scope not in ("produce", "tower", "all"):
        raise ValueError(f"unknown scope {scope!r}")
    all_scopes = scope == "all"
    eff_ok = implemented_effect_types()
    grow_ok = implemented_grow_types()
    phase_ok = implemented_phases()
    report = {}
    from GakumasCLI.scenario.plan_support import unsupported_row, reach_split
    new_cards = [c for c in catalog.cards.values() if unsupported_row(c.raw)]
    new_items = [i for i in catalog.items.values() if unsupported_row(i.raw)]
    deferred = reach_split(catalog.master_dir)[1] if (new_cards or new_items or any(
        unsupported_row(r) for r in _content_rows(catalog))) else set()
    if scope == "tower":
        scoped_items = [
            item for item in catalog.items.values()
            if item.plan_name in plans and catalog._in_tower_scope(item.id) and not unsupported_row(item.raw)
        ]
    else:
        scoped_items = [
            item for item in catalog.items.values()
            if item.plan_name in plans and (all_scopes or item.raw.get("libraryHidden") is not True)
            and not unsupported_row(item.raw)
        ]
    for plan in plans:
        cards = [c for c in catalog.cards.values() if c.plan_name == plan and not unsupported_row(c.raw)]
        miss_e, miss_g, master_only_e = Counter(), Counter(), Counter()
        ex_e, ex_g = defaultdict(set), defaultdict(set)
        for c in cards:
            for t in catalog.card_effect_types(c):
                if t not in eff_ok:
                    miss_e[t] += 1; ex_e[t].add(c.name)
                elif ENGINE_IMPLEMENTATION["effect_types"].get(t) == "master_only":
                    master_only_e[t] += 1
            for t in catalog.card_grow_types(c):
                if t not in grow_ok:
                    miss_g[t] += 1; ex_g[t].add(c.name)
        report[plan] = {
            "cards": len(cards),
            "missing_effect_types": [(t, enum_name("ProduceExamEffectType", t), n, sorted(ex_e[t])[:4]) for t, n in miss_e.most_common()],
            "master_only_effect_types": [(t, enum_name("ProduceExamEffectType", t), n) for t, n in master_only_e.most_common()],
            "missing_grow_types": [(t, enum_name("ProduceCardGrowEffectType", t), n, sorted(ex_g[t])[:4]) for t, n in miss_g.most_common()],
        }
    item_miss_e, item_master_e = Counter(), Counter()
    item_ex = defaultdict(set)
    for it in scoped_items:
        for t in catalog.item_effect_types(it):
            if t not in eff_ok:
                item_miss_e[t] += 1; item_ex[t].add(it.name)
            elif ENGINE_IMPLEMENTATION["effect_types"].get(t) == "master_only":
                item_master_e[t] += 1
    report["items_missing_effects"] = [(t, enum_name("ProduceExamEffectType", t), n, sorted(item_ex[t])[:4]) for t, n in item_miss_e.most_common()]
    report["items_master_only_effects"] = [(t, enum_name("ProduceExamEffectType", t), n) for t, n in item_master_e.most_common()]

    status_enchant_miss_e, status_enchant_master_e = Counter(), Counter()
    status_enchant_ex = defaultdict(set)
    if scope == "tower":
        scoped_enchants = [
            enchant for enchant in catalog.enchants.values()
            if catalog._in_tower_scope(enchant.id)
        ]
    else:
        scoped_enchants = [
            enchant for enchant in catalog.enchants.values()
            if all_scopes or catalog._status_enchant_in_produce_scope(enchant)
        ]
    scoped_enchants = [e for e in scoped_enchants if e.id not in deferred]
    for enchant in scoped_enchants:
        for t in catalog.enchant_effect_types(enchant):
            if t not in eff_ok:
                status_enchant_miss_e[t] += 1; status_enchant_ex[t].add(enchant.id)
            elif ENGINE_IMPLEMENTATION["effect_types"].get(t) == "master_only":
                status_enchant_master_e[t] += 1
    report["status_enchants_missing_effects"] = [
        (t, enum_name("ProduceExamEffectType", t), n, sorted(status_enchant_ex[t])[:4])
        for t, n in status_enchant_miss_e.most_common()
    ]
    report["status_enchants_master_only_effects"] = [
        (t, enum_name("ProduceExamEffectType", t), n)
        for t, n in status_enchant_master_e.most_common()
    ]

    def _source_missing(source_rows):
        missing, examples = Counter(), defaultdict(set)
        for source in source_rows:
            for _, effect in catalog.effect_tree(source.effect_id):
                t = effect.type_number
                if t not in eff_ok:
                    missing[t] += 1; examples[t].add(source.id)
        return [(t, enum_name("ProduceExamEffectType", t), n, sorted(examples[t])[:4]) for t, n in missing.most_common()]

    if scope == "tower":
        gimmicks = [
            source for source in catalog.gimmick_groups
            if len(source.id.split("-")) > 1
            and source.id.split("-")[1].startswith(("tower", "contest"))
        ]
    else:
        gimmicks = catalog.gimmick_groups if all_scopes else [
            source for source in catalog.gimmick_groups
            if len(source.id.split("-")) <= 1
            or not source.id.split("-")[1].startswith(("tower", "contest"))
        ]
    report["scope"] = scope
    report["gimmicks_missing_effects"] = _source_missing(gimmicks)
    report["drinks_missing_effects"] = _source_missing([d for d in catalog.drink_effects if d.id not in deferred])

    phase_use, phase_ex = Counter(), defaultdict(set)
    for it in scoped_items:
        for enid in it.enchant_ids:
            en = catalog.enchants.get(enid)
            t = catalog.triggers.get((en.trigger_id or "") if en else "")
            if not t:
                continue
            for p in t.phase_numbers:
                if p not in phase_ok:
                    phase_use[p] += 1; phase_ex[p].add(it.name)
    report["items_missing_phases"] = [(p, enum_name("ProduceExamPhaseType", p), n, sorted(phase_ex[p])[:3]) for p, n in phase_use.most_common()]
    report["unsupported_content"] = _unsupported_content(catalog, new_cards, new_items, deferred, eff_ok, grow_ok,
                                                         phase_ok)
    return report


def _content_rows(catalog):
    """The idol card rows (the other content a plan check reads is the catalog's cards and items)."""
    path = Path(catalog.master_dir) / "IdolCard.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line).get("decoded") or {} for line in fh if line.strip()]


def _unsupported_content(catalog, cards, items, deferred, eff_ok, grow_ok, phase_ok):
    """per plan (a category for a supported plan's card): the rows a round's opening refuses and the effect / grow /
    phase types of theirs the engine has no branch for -- the engine work the plan waits for."""
    from GakumasCLI.scenario.plan_support import SUPPORTED_PLANS
    groups = defaultdict(lambda: {"cards": 0, "items": 0, "effects": Counter(), "grows": Counter(),
                                  "phases": Counter(), "examples": defaultdict(set)})

    def group(raw, plan_name, category):
        number = _num(raw.get("planType"))
        return plan_name if number not in SUPPORTED_PLANS else f"{plan_name} category {category}"
    for c in cards:
        g = groups[group(c.raw, c.plan_name, c.category_name)]
        g["cards"] += 1
        for t in catalog.card_effect_types(c):
            if t not in eff_ok:
                g["effects"][t] += 1; g["examples"][("e", t)].add(c.name or c.id)
        for t in catalog.card_grow_types(c):
            if t not in grow_ok:
                g["grows"][t] += 1; g["examples"][("g", t)].add(c.name or c.id)
    for it in items:
        g = groups[it.plan_name]
        g["items"] += 1
        for t in catalog.item_effect_types(it):
            if t not in eff_ok:
                g["effects"][t] += 1; g["examples"][("e", t)].add(it.name or it.id)
    for t in catalog.triggers.values():
        if getattr(t, "id", None) in deferred:
            for p in t.phase_numbers:
                if p not in phase_ok:
                    g = groups["reached only from them"]
                    g["phases"][p] += 1; g["examples"][("p", p)].add(t.id)
    idols = Counter(_name(r.get("planType")) for r in _content_rows(catalog)
                    if _num(r.get("planType")) not in SUPPORTED_PLANS and r.get("planType") is not None)
    out = {}
    for name, g in groups.items():
        ex = g["examples"]
        out[name] = {"cards": g["cards"], "items": g["items"], "idol_cards": idols.pop(name, 0),
                     "missing_effect_types": [(t, enum_name("ProduceExamEffectType", t), n, sorted(ex[("e", t)])[:3])
                                              for t, n in g["effects"].most_common()],
                     "missing_grow_types": [(t, enum_name("ProduceCardGrowEffectType", t), n, sorted(ex[("g", t)])[:3])
                                            for t, n in g["grows"].most_common()],
                     "missing_phases": [(p, enum_name("ProduceExamPhaseType", p), n, sorted(ex[("p", p)])[:3])
                                        for p, n in g["phases"].most_common()]}
    for name, n in idols.items():
        out[name] = {"cards": 0, "items": 0, "idol_cards": n, "missing_effect_types": [], "missing_grow_types": [],
                     "missing_phases": []}
    return out


def format_coverage(report, all_scopes=False, scope=None):
    lines = []
    scope = scope or report.get("scope") or ("all" if all_scopes else "produce")
    if scope == "tower":
        item_scope = gimmick_scope = status_enchant_scope = "tower scope (Idol Road / Contest rows only)"
    elif scope == "all":
        item_scope = gimmick_scope = status_enchant_scope = "all scopes"
    else:
        item_scope = "produce scope (libraryHidden=True items excluded)"
        gimmick_scope = "produce scope (tower*/contest* gimmick groups excluded)"
        status_enchant_scope = "produce scope (tower*/contest*/challenge/hidden-owner/orphan status enchants excluded)"
    for plan, rep in report.items():
        if plan == "scope":
            continue
        if plan == "items_missing_effects":
            lines.append(f"items - {item_scope} - effect types without a dispatch branch:")
            for t, name, n, ex in rep:
                lines.append(f"  effect {t} {name} x{n} e.g. {ex}")
            continue
        if plan == "items_master_only_effects":
            lines.append(f"items - {item_scope} - master-only effect types:")
            for t, name, n in rep:
                lines.append(f"  effect {t} {name} x{n}")
            continue
        if plan == "status_enchants_missing_effects":
            lines.append(f"status enchants - {status_enchant_scope} - effect types without a dispatch branch:")
            for t, name, n, ex in rep:
                lines.append(f"  effect {t} {name} x{n} e.g. {ex}")
            continue
        if plan == "status_enchants_master_only_effects":
            lines.append(f"status enchants - {status_enchant_scope} - master-only effect types:")
            for t, name, n in rep:
                lines.append(f"  effect {t} {name} x{n}")
            continue
        if plan == "gimmicks_missing_effects":
            lines.append(f"gimmicks - {gimmick_scope} - effect types without a dispatch branch:")
            for t, name, n, ex in rep:
                lines.append(f"  effect {t} {name} x{n} e.g. {ex}")
            continue
        if plan == "drinks_missing_effects":
            lines.append("drinks - effect types without a dispatch branch:")
            for t, name, n, ex in rep:
                lines.append(f"  effect {t} {name} x{n} e.g. {ex}")
            continue
        if plan == "items_missing_phases":
            lines.append(f"items - {item_scope} - phases without a dispatch point:")
            for p, name, n, ex in rep:
                lines.append(f"  phase {p} {name} x{n} e.g. {ex}")
            continue
        if plan == "unsupported_content":
            lines.append("unsupported content - listed, not gated (a round holding it is refused at its "
                         "opening, scenario/plan_support.py):" + ("" if rep else " none"))
            for name, g in rep.items():
                lines.append(f"  {name}: {g['idol_cards']} idol cards, {g['cards']} card rows, {g['items']} P items")
                for t, ename, n, ex in g["missing_effect_types"]:
                    lines.append(f"    effect {t} {ename} x{n} e.g. {ex}")
                for t, ename, n, ex in g["missing_grow_types"]:
                    lines.append(f"    grow {t} {ename} x{n} e.g. {ex}")
                for p, ename, n, ex in g["missing_phases"]:
                    lines.append(f"    phase {p} {ename} x{n} e.g. {ex}")
            continue
        lines.append(f"{plan}: {rep['cards']} card rows")
        for t, name, n, ex in rep["missing_effect_types"]:
            lines.append(f"  MISSING effect {t} {name} x{n} e.g. {ex}")
        for t, name, n in rep["master_only_effect_types"]:
            lines.append(f"  master-only effect {t} {name} x{n}")
        for t, name, n, ex in rep["missing_grow_types"]:
            lines.append(f"  MISSING grow {t} {name} x{n} e.g. {ex}")
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("coverage", "card"):
        print("usage: python -m neu.semantics coverage [--all-scopes|--scope produce|tower|all] --master <dir> | card --master <dir> <card_id> [upgrade]")
        return 2
    master = None
    if "--master" in argv:
        master = argv[argv.index("--master") + 1]
    master = master or os.environ.get("NEU_MASTER") or str(Path(__file__).resolve().parents[1] / "ro" / "master" / "tables")
    cat = MasterCatalog(master)
    if argv[0] == "coverage":
        all_scopes = "--all-scopes" in argv
        scope = argv[argv.index("--scope") + 1] if "--scope" in argv else None
        report = coverage_report(cat, all_scopes=all_scopes, scope=scope)
        print(format_coverage(report))
    else:
        rest = [a for a in argv[1:] if not a.startswith("--") and a != master]
        print(cat.describe_card(rest[0], int(rest[1]) if len(rest) > 1 else 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
