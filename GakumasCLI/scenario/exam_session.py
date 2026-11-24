# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""scenario.exam_session - one Lesson / Audition driven by master + seed + a
policy, with **no wire**.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
from dataclasses import dataclass, field
from pathlib import Path

from GakumasCLI.engine.exam_loading import LoadingDeck, LoadingState
from GakumasCLI.engine.card_data import load_master_rows
from GakumasCLI.engine.audition import AuditionCatalog
from GakumasCLI.engine.lessons import LessonCatalog
from GakumasCLI.engine.produce_run import ProduceRun


OBSERVE_SCHEMA_VERSION = "gkms.exam.observe.v2"
SCORE_OBJECTIVE_VERSION = "score-only-v1"


# ------------------------------------------------------------------ inputs
@dataclass
class SegmentConfig:
    segment_id: str
    segment_type: str
    limit_turn: int
    score_cap: int | None
    parameter_type: int | None
    seed: int
    gimmick_group_id: str | None = None
    audition_permils: dict | None = None
    audition_parameter_values: tuple | None = None
    turn_start_distribute: int | None = None
    final_audition: bool = False
    eligible_supports: tuple | list = ()
    upgrade_tiers: set | frozenset | tuple = ()
    initial_card_id: str | None = None
    initial_card_ids: tuple | list | None = None
    resolve_initial_cards: bool = False
    segment_start_rng_advances: int = 0
    audition_npc_rng_advances: int = 0
    turn_parameter_types: dict | None = None
    lesson_start_enchant_ids: tuple | list = ()
    opening_status_carry: tuple | list = ()
    opening_statuses_are_observed: bool = False
    objective_id: str = SCORE_OBJECTIVE_VERSION
    drink_ids: tuple | list = ()
    skip_clears_playable_adds: int = 0


@dataclass
class ProduceState:
    """The cross-segment state a produce carries into each exam."""
    stamina: int
    max_stamina: int
    deck_cards: list
    item_enchant_ids: list
    memory_ability_ids: list = field(default_factory=list)
    plan_type: int = 3
    trace: list = field(default_factory=list)
    research_card_ids: tuple = ()
    item_enchant_limits: dict = field(default_factory=dict)


def _optional_rows(path):
    """load a master table if the snapshot has it, else {}."""
    return load_master_rows(path) if Path(path).exists() else {}


@dataclass
class Master:
    cards: dict
    effects: dict
    triggers: dict
    enchants: dict
    searches: dict
    customizes: dict
    grow_effects: dict
    exam_setting: dict
    gimmicks: dict
    trigger_effect_limits: dict
    items: dict
    item_effects: dict
    card_pools: dict = field(default_factory=dict)
    drinks: dict = field(default_factory=dict)
    drink_effects: dict = field(default_factory=dict)
    lesson_catalog: LessonCatalog | None = None
    audition_catalog: AuditionCatalog | None = None
    master_dir: Path | None = None
    setting: dict = field(default_factory=dict)
    card_status_enchants: dict = field(default_factory=dict)

    def _memory_ability_tables(self):
        tables = getattr(self, "_memory_tables", None)
        if tables is None:
            from GakumasCLI.engine.produce_run import MEMORY_ABILITY_TABLES, load_memory_ability_tables
            tables = (load_memory_ability_tables(self.master_dir) if self.master_dir is not None
                      else {name: {} for name in MEMORY_ABILITY_TABLES})
            object.__setattr__(self, "_memory_tables", tables)
        return tables

    memory_abilities = property(lambda self: self._memory_ability_tables()["memory_abilities"])
    produce_skills = property(lambda self: self._memory_ability_tables()["produce_skills"])
    produce_effects = property(lambda self: self._memory_ability_tables()["produce_effects"])
    produce_triggers = property(lambda self: self._memory_ability_tables()["produce_triggers"])

    def memory_ability_activations(self, ability_id):
        from GakumasCLI.engine.produce_run import join_memory_ability
        return join_memory_ability(self._memory_ability_tables(), ability_id)

    def item_enchant_limits(self, item_ids):
        """enchant -> 発動回数 from the given P items' own ProduceItemEffect rows."""
        out = {}
        for item_id in item_ids or ():
            item = (self.items or {}).get(item_id) or {}
            effect_ids = list(item.get("produceItemEffectIds") or ()) or [
                s.get("produceItemEffectId") for s in item.get("skills") or () if s.get("produceItemEffectId")]
            for effect_id in effect_ids:
                effect = (self.item_effects or {}).get(effect_id) or {}
                enchant = effect.get("produceExamStatusEnchantId")
                if enchant and enchant not in out:
                    count = int(effect.get("effectCount") or -1)
                    out[enchant] = count if count > 0 else -1
        return out

    def idol_unique_item_enchant_ids(self):
        """enchant ids of every idol-unique P item (ProduceItem.originIdolCardId set), for effect 150."""
        cached = getattr(self, "_idol_unique_item_enchants", None)
        if cached is None:
            out = set()
            for item in (self.items or {}).values():
                if not item.get("originIdolCardId"):
                    continue
                for effect_id in item.get("produceItemEffectIds") or ():
                    enchant = (self.item_effects.get(effect_id) or {}).get("produceExamStatusEnchantId")
                    if enchant:
                        out.add(enchant)
            cached = frozenset(out)
            object.__setattr__(self, "_idol_unique_item_enchants", cached)
        return cached

    @classmethod
    def load(cls, master_dir, exam_setting_id=None):
        """The exam tables and the lesson / audition catalogs of ``master_dir``."""
        master_dir = Path(master_dir)
        cards = {}
        with open(master_dir / "ProduceCard.jsonl", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("decode_status") == "decoded":
                    cards[(row["decoded"]["id"], int(row["outer"]["UpgradeCount"]))] = row["decoded"]
        customizes = {}
        with open(master_dir / "ProduceCardCustomize.jsonl", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("decode_status") == "decoded":
                    customizes[(row["decoded"]["id"], int(row["decoded"]["customizeCount"]))] = row["decoded"]
        gimmicks = {}
        trigger_effect_limits = {}
        item_effect_path = master_dir / "ProduceItemEffect.jsonl"
        if item_effect_path.exists():
            with item_effect_path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get("decode_status", "decoded") != "decoded":
                        continue
                    decoded = row.get("decoded", row)
                    enchant_id = decoded.get("produceExamStatusEnchantId")
                    if enchant_id:
                        count = int(decoded.get("effectCount") or -1)
                        trigger_effect_limits[enchant_id] = count if count > 0 else -1
        gpath = master_dir / "ProduceExamGimmickEffectGroup.jsonl"
        if gpath.exists():
            with open(gpath, encoding="utf-8") as fh:
                for line in fh:
                    row = json.loads(line)
                    if row.get("decode_status") == "decoded":
                        gimmicks.setdefault(row["decoded"]["id"], []).append(row["decoded"])
        settings = load_master_rows(master_dir / "ExamSetting.jsonl")
        setting = settings.get(exam_setting_id) if exam_setting_id else None
        setting = setting or next(iter(settings.values()))
        global_settings = load_master_rows(master_dir / "Setting.jsonl") if (master_dir / "Setting.jsonl").exists() else {}
        global_setting = next(iter(global_settings.values()), {})
        card_pool_path = master_dir / "ProduceCardPool.jsonl"
        card_pools = load_master_rows(card_pool_path) if card_pool_path.exists() else {}
        lesson_catalog = LessonCatalog.load(master_dir)
        audition_catalog = AuditionCatalog.load(master_dir)
        return cls(
            cards=cards,
            effects=load_master_rows(master_dir / "ProduceExamEffect.jsonl"),
            triggers=load_master_rows(master_dir / "ProduceExamTrigger.jsonl"),
            enchants=load_master_rows(master_dir / "ProduceExamStatusEnchant.jsonl"),
            searches=load_master_rows(master_dir / "ProduceCardSearch.jsonl"),
            customizes=customizes,
            grow_effects=load_master_rows(master_dir / "ProduceCardGrowEffect.jsonl"),
            exam_setting=setting,
            setting=global_setting,
            gimmicks=gimmicks,
            trigger_effect_limits=trigger_effect_limits,
            items=load_master_rows(master_dir / "ProduceItem.jsonl"),
            item_effects=load_master_rows(master_dir / "ProduceItemEffect.jsonl"),
            card_pools=card_pools,
            drinks=_optional_rows(master_dir / "ProduceDrink.jsonl"),
            drink_effects=_optional_rows(master_dir / "ProduceDrinkEffect.jsonl"),
            lesson_catalog=lesson_catalog,
            audition_catalog=audition_catalog,
            master_dir=master_dir,
            card_status_enchants=_optional_rows(master_dir / "ProduceCardStatusEnchant.jsonl"),
        )

    load_exam = load


# ------------------------------------------------------------------ session
_DEFER_OPEN = contextvars.ContextVar("gkms_defer_open", default=False)


@contextlib.contextmanager
def deferred_open():
    """kept for the callers that still say so."""
    token = _DEFER_OPEN.set(True)
    try:
        yield
    finally:
        _DEFER_OPEN.reset(token)


class ExamSession:
    """An exam session."""

    def __new__(cls, master, produce, config):
        return open_exam(ExamLoading(master, produce, config))


def open_exam(loading, document=None, seed=None):
    """``loading`` (an ExamLoading) opened in the core -- a NativeExamSession; inside deferred_open the loading itself.
    """
    if _DEFER_OPEN.get():
        return loading
    from GakumasCLI.native.native_session import open_loading
    return open_loading(loading, document, seed)


OBSERVE_PREVIEW_VERSION = "gkms.exam.preview.v1"


def preview_action(session, action, *, trace=False):
    """What one action does to the score, measured on forks of `session`."""
    return session.preview_action(action, trace=trace)


def preview_hand(session, *, trace=False):
    """`preview_action` for every legal action plus every unplayable card."""
    return session.preview_hand(trace=trace)


def greedy_policy(session, hand):
    """Play the affordable card with the largest immediate score gain estimate
    (lesson value * stance multiplier); skip when nothing is affordable."""
    best, best_value = None, -1
    for index, instance in enumerate(hand):
        if not session.can_play(instance):
            continue
        card = instance.get("resolved_card") or session.deck.refresh_card(instance)
        value = 0
        for link in card.get("playEffects", []):
            effect = session.master.effects.get(link["produceExamEffectId"], {})
            t = (effect.get("effectType") or {}).get("number")
            if t in (1, 56, 23):
                value += int(effect.get("effectValue1") or 0) + int(card.get("customize_lesson_add", 0))
            elif t == 49:
                value += 2 * int(effect.get("effectValue1") or 0)
        if value > best_value:
            best, best_value = index, value
    return best


class ExamLoading:
    """an exam's loading -- ExamSession's constructor up to turn 1, on the data classes of engine/exam_loading.py (no
    exam rule).
    """

    def __init__(self, master: Master, produce: ProduceState | ProduceRun, config: SegmentConfig):
        self.master = master
        self.produce = produce
        self.config = config
        self.is_produce_run = isinstance(produce, ProduceRun)
        self.step_index = None
        if self.is_produce_run:
            for candidate in produce.steps:
                if candidate.step_id == config.segment_id:
                    self.step_index = candidate.index
                    break
            if self.step_index is None:
                raise KeyError(f"ProduceRun step {config.segment_id!r}")
            deck_specs = produce.deck_cards_for(self.step_index)
            plan_type = produce.identity.plan_type
            item_enchant_ids = produce.item_enchant_ids(master)
            step_input = produce.step(self.step_index)
            starting_stamina = step_input.stamina
            max_stamina = step_input.max_stamina
            selected_setting = produce.identity.exam_setting_id
            if selected_setting and master.exam_setting.get("id") == selected_setting:
                produce.provenance["exam_setting"] = {
                    "source": "a",
                    "locator": f"master:ExamSetting/{selected_setting} selected by identity",
                }
            else:
                produce.provenance["exam_setting"] = {
                    "source": "a",
                    "locator": f"master:ExamSetting/{master.exam_setting.get('id', '<first>')} fallback: identity setting id unavailable",
                }
        else:
            deck_specs = produce.deck_cards
            plan_type = produce.plan_type
            item_enchant_ids = list(produce.item_enchant_ids)
            starting_stamina = produce.stamina
            max_stamina = produce.max_stamina
        instances = []
        for number, spec in enumerate(deck_specs, 1):
            card_number = spec.get("number", number)
            instances.append({
                "number": int(card_number),
                "guid": spec.get("guid") or f"scenario-{config.seed}-{card_number}",
                "produce_card_id": spec["produce_card_id"],
                "base_upgrade_count": int(spec.get("base_upgrade_count")
                                          or spec.get("current_upgrade_count") or 0),
                "current_upgrade_count": int(spec.get("current_upgrade_count", spec.get("base_upgrade_count") or 0) or 0),
                "temporary_upgrade_count": 0,
                "support_upgrade_ids": [],
                "customizes": list(spec.get("customizes") or ()),
                "origin_type": spec.get("origin_type", 0),
                "is_initial": bool(spec.get("is_initial")),
                "play_move_position_type": 5,
            })
        setting = getattr(master, "exam_setting", None) or {}
        self.deck = LoadingDeck(
            instances,
            config.seed,
            turn_start_distribute=(config.turn_start_distribute if config.turn_start_distribute is not None
                                   else int(setting.get("turnStartDistribute") or 3)),
            initial_card_id=config.initial_card_id,
            initial_card_ids=config.initial_card_ids,
            resolve_initial_cards=config.resolve_initial_cards,
            card_master=master.cards,
            card_customizes=master.customizes,
            card_grow_effects=master.grow_effects,
            audition_parameter_values=config.audition_parameter_values,
            audition_limit_turn=config.limit_turn if config.audition_parameter_values else None,
            segment_start_rng_advances=config.segment_start_rng_advances,
            eligible_supports=config.eligible_supports,
            upgrade_tiers=config.upgrade_tiers,
            audition_npc_rng_advances=config.audition_npc_rng_advances,
        )
        self.deck.hand_limit = int(setting.get("handLimit") or 5)
        for card in self.deck.deck:
            self.deck.refresh_card(card)
        gimmick_rows = master.gimmicks.get(config.gimmick_group_id or "", ())
        if self.is_produce_run:
            item_limits = master.item_enchant_limits(getattr(produce, "item_ids", ()) or ())
        else:
            item_limits = dict(getattr(produce, "item_enchant_limits", None) or {})
        self.state = LoadingState(
            starting_stamina,
            None if config.final_audition else config.score_cap,
            deck=self.deck,
            card_searches=master.searches,
            card_pools=master.card_pools,
            plan_type=plan_type,
            gimmick_rows=gimmick_rows,
            exam_effects=master.effects,
            status_enchants=master.enchants,
            exam_triggers=master.triggers,
            trigger_effect_limits={
                e: item_limits.get(e, master.trigger_effect_limits.get(e, -1)) for e in item_enchant_ids
            },
            exam_setting=master.exam_setting,
            setting=master.setting,
            max_stamina=max_stamina,
        )
        self.state.limit_turn = config.limit_turn
        self.state.card_status_enchants = master.card_status_enchants
        for card in self.deck.deck:
            row = master.cards.get((card.get("produce_card_id"), 0)) or {}
            trigger = row.get("moveEffectTriggerType")
            trigger = trigger.get("number") if isinstance(trigger, dict) else trigger
            if row.get("moveProduceExamEffectIds") and trigger != self.deck.MOVE_TRIGGER_HAND:
                self.state.unresolved_effects.append({"reason": "card move effect (移動時効果) not implemented",
                                                      "card_id": card.get("produce_card_id")})
        self.state.idol_unique_item_enchant_ids = master.idol_unique_item_enchant_ids()
        self.state.research_card_ids = frozenset(getattr(produce, "research_card_ids", ()) or ())
        self.state.audition_permils = config.audition_permils or None
        self.state.client_turn_end_order = not config.opening_statuses_are_observed
        for enchant_id in config.lesson_start_enchant_ids or ():
            if enchant_id in master.enchants:
                self.state.pending_trigger_installs.append(enchant_id)
        self.turn = 0
        self.drinks = [str(d) for d in (config.drink_ids or ())]
        self.log = []
        self.turn_start_reads = {}
        self.finished = False
        self.cleared = False

    def opening_statuses(self):
        if self.is_produce_run:
            enchant_ids = self.produce.opening_enchant_ids(
                self.master,
                self.step_index,
                getattr(self.produce, "server_draw", None),
            )
        else:
            enchant_ids = self.produce.item_enchant_ids
        statuses = [{"status_type": "TriggerEffect", "args": [-1, enchant_id]}
                    for enchant_id in enchant_ids]
        statuses.extend(dict(row) for row in self.config.opening_status_carry or ())
        return statuses
