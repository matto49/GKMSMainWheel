# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idol Road adapter over ExamSession."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from GakumasCLI.engine.tower import TowerCatalog, DEFAULT_TOWER, VOCAL, DANCE, VISUAL
from GakumasCLI.scenario.exam_session import (
    ExamLoading, Master, ProduceState, SegmentConfig, greedy_policy, SCORE_OBJECTIVE_VERSION, open_exam,
)

NPC_COUNT = 5


_IDOL_CARDS = {}


def _idol_card_row(master_dir, idol_card_id):
    """The decoded IdolCard row of ``idol_card_id``."""
    path = Path(master_dir) / "IdolCard.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"IdolCard.jsonl not found: {path}")
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    rows = _IDOL_CARDS.get(key)
    if rows is None:
        rows = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            decoded = row.get("decoded") if isinstance(row, dict) else None
            if isinstance(decoded, dict) and decoded.get("id") is not None:
                rows.setdefault(decoded["id"], decoded)
        _IDOL_CARDS.clear()
        _IDOL_CARDS[key] = rows
    wanted = str(idol_card_id or "")
    if wanted not in rows:
        raise KeyError(f"IdolCard {wanted!r}")
    return wanted, rows[wanted]


def mechanic_for_idol_card(master_dir, idol_card_id: str) -> str:
    """Resolve the exam mechanic from the authoritative IdolCard row."""
    wanted, decoded = _idol_card_row(master_dir, idol_card_id)
    effect = decoded.get("examEffectType")
    name = effect.get("name") if isinstance(effect, dict) else effect
    prefix = "PRODUCE_EXAM_EFFECT_TYPE_"
    if not isinstance(name, str) or not name.startswith(prefix):
        raise ValueError(f"idol_card_exam_effect_unknown:{wanted}:{name!r}")
    mechanic = name[len(prefix):].lower()
    if not mechanic:
        raise ValueError(f"idol_card_exam_effect_empty:{wanted}")
    return mechanic


def plan_type_for_idol_card(master_dir, idol_card_id: str) -> int:
    """the authoritative plan type is IdolCard.planType, not a loadout field."""
    wanted, decoded = _idol_card_row(master_dir, idol_card_id)
    plan = decoded.get("planType")
    number = plan.get("number") if isinstance(plan, dict) else plan
    if not isinstance(number, int) or number <= 0:
        raise ValueError(f"idol_card_plan_type_unknown:{wanted}:{plan!r}")
    return int(number)


@dataclass
class IdolRoadStage:
    """Which stage is being played."""
    mechanic: str
    stage: int
    pattern: int = 0
    battle_config_id: str | None = None
    limit_turn: int | None = None
    tower: str = DEFAULT_TOWER
    gimmick_group_id: str | None = None
    npc_group_id: str | None = None

    @property
    def stage_id(self):
        return f"{self.tower}-{self.mechanic}-stage_{int(self.stage):03d}-p{self.pattern}"


@dataclass
class IdolRoadEntry:
    """What the memory loadout brings into the stage."""
    seed: int
    parameters: dict
    deck_cards: list
    main_item_enchant_ids: list = field(default_factory=list)
    starting_stamina: int = 0
    max_stamina: int = 0
    plan_type: int = 3
    score_bonus_permils: dict | None = None
    idol_card_id: str | None = None
    produce_item_ids: list = field(default_factory=list)
    include_stage_items: bool = False
    include_produce_default: bool = True
    drink_ids: list = field(default_factory=list)

    @classmethod
    def from_json(cls, path, **overrides):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        parameters = {int(k): int(v) for k, v in data["parameters"].items()}
        entry = cls(
            seed=int(data["seed"]),
            parameters=parameters,
            deck_cards=list(data["deck_cards"]),
            main_item_enchant_ids=list(data.get("main_item_enchant_ids") or ()),
            starting_stamina=int(data.get("starting_stamina") or 0),
            max_stamina=int(data.get("max_stamina") or 0),
            plan_type=int(data.get("plan_type") or 3),
            score_bonus_permils={int(k): int(v) for k,v in data["score_bonus_permils"].items()} if data.get("score_bonus_permils") else None,
            idol_card_id=data.get("idol_card_id"),
            produce_item_ids=list(data.get("produce_item_ids") or ()),
            include_stage_items=bool(data.get("include_stage_items", False)),
            include_produce_default=bool(data.get("include_produce_default", True)),
            drink_ids=list(data.get("drink_ids") or ()),
        )
        for name, value in overrides.items():
            if value is not None:
                setattr(entry, name, value)
        return entry


@dataclass
class ResolvedStage:
    stage: IdolRoadStage
    limit_turn: int
    gimmick_group_id: str
    battle_config_id: str | None
    criteria: dict
    stage_item_enchant_ids: list
    npc_count: int = NPC_COUNT
    binding_source: str = "explicit_override"
    npc_group_id: str | None = None


def resolve_stage(catalog: TowerCatalog, stage: IdolRoadStage) -> ResolvedStage:
    row = catalog.resolve_exam(stage.tower, stage.stage, stage.mechanic)
    config_id = stage.battle_config_id or (row or {}).get("produceExamBattleConfigId")
    if row:
        group_id = row["produceExamGimmickEffectGroupId"]
        if group_id not in catalog.gimmick_groups:
            raise ValueError(f"tower:gimmick_group - missing {group_id}")
        items = catalog.item_enchant_ids(row.get("produceItemIds", ()))
        npc_id = row["produceExamBattleNpcGroupId"]
        groups = [r for group in catalog.npc_groups.values() for r in group if r["id"] == npc_id]
        if not groups:
            raise ValueError(f"tower:npc_group - missing {npc_id}")
        npc_count = len(groups)
    elif stage.gimmick_group_id or stage.npc_group_id:
        group_id = stage.gimmick_group_id
        if group_id not in catalog.gimmick_groups:
            raise ValueError(f"tower:gimmick_group - missing {group_id}")
        theme = "exam_full_power" if stage.mechanic == "exam_preservation" else stage.mechanic
        items = catalog.stage_item_enchant_ids(theme, stage.stage)
        npc_id = stage.npc_group_id
        groups = [r for group in catalog.npc_groups.values() for r in group if r["id"] == npc_id]
        if not groups:
            raise ValueError(f"tower:npc_group - missing {npc_id}")
        npc_count = len(groups)
        if not config_id and stage.limit_turn is None:
            raise ValueError(f"tower:stage_table - wire binding without battle_config_id or limit_turn")
    else:
        if not config_id and stage.limit_turn is None:
            raise ValueError(f"tower:stage_table - no binding for ({stage.tower}, {stage.stage}, {stage.mechanic}); supply battle_config_id or limit_turn")
        group_id, _ = catalog.gimmick_schedule(stage.mechanic, stage.stage, stage.pattern)
        theme = "exam_full_power" if stage.mechanic == "exam_preservation" else stage.mechanic
        items = catalog.stage_item_enchant_ids(theme, stage.stage)
        npc_id, npc_count = None, NPC_COUNT
    criteria = catalog.criteria(config_id) if config_id else {}
    limit_turn = int(catalog.battle_configs[config_id]["turn"]) if config_id else int(stage.limit_turn)
    return ResolvedStage(stage, limit_turn, group_id, config_id, criteria, items,
                         npc_count, (row or {}).get("binding_source", "explicit_override"), npc_id)


def candidate_battle_configs(catalog: TowerCatalog, limit_turn=None, parameters=None, top=10):
    """Battle configs that could be this stage, narrowed by what the player
    can read off the stage screen (the turn limit, and where the three
    parameter lines sit).
    """
    rows = [row for row in catalog.battle_configs.values()
            if limit_turn is None or int(row["turn"]) == int(limit_turn)]
    if parameters:
        def distance(row):
            return sum(abs(int(parameters[p]) - int(row[c]))
                       for p, c in ((VOCAL, "vocal"), (DANCE, "dance"), (VISUAL, "visual")))
        rows.sort(key=distance)
    else:
        rows.sort(key=lambda row: row["id"])
    return rows[:top] if top else rows


def build_loading(master: Master, catalog: TowerCatalog,
                  stage: IdolRoadStage, entry: IdolRoadEntry) -> ExamLoading:
    """the stage's loading (ExamLoading); build_session opens it."""
    resolved = resolve_stage(catalog, stage)

    item_enchant_ids = list(entry.main_item_enchant_ids)
    for item_id in entry.produce_item_ids:
        for effect_id in master.items[item_id].get("produceItemEffectIds", ()):
            enchant = master.item_effects[effect_id].get("produceExamStatusEnchantId")
            if enchant and enchant not in item_enchant_ids:
                item_enchant_ids.append(enchant)
    if entry.include_stage_items:
        for enchant_id in resolved.stage_item_enchant_ids:
            if enchant_id not in item_enchant_ids:
                item_enchant_ids.append(enchant_id)

    permils = None
    if entry.score_bonus_permils is not None:
        permils = dict(entry.score_bonus_permils)
    elif resolved.battle_config_id:
        permils = catalog.score_bonus_permils(resolved.battle_config_id, entry.parameters)

    deck_cards = []
    if entry.include_produce_default:
        for card_id in catalog.produce_default_deck(stage.mechanic):
            deck_cards.append({"produce_card_id": card_id, "base_upgrade_count": 0})
    deck_cards.extend(dict(card) for card in entry.deck_cards)
    for number, card in enumerate(deck_cards, 1):
        card["number"] = number

    produce = ProduceState(
        stamina=int(entry.starting_stamina),
        max_stamina=int(entry.max_stamina),
        deck_cards=deck_cards,
        item_enchant_ids=item_enchant_ids,
        plan_type=int(entry.plan_type),
        item_enchant_limits=master.item_enchant_limits(entry.produce_item_ids),
    )
    segment = SegmentConfig(
        segment_id=stage.stage_id,
        segment_type=f"Idol Road {stage.mechanic}",
        limit_turn=resolved.limit_turn,
        score_cap=None,
        parameter_type=None,
        seed=int(entry.seed),
        gimmick_group_id=resolved.gimmick_group_id,
        audition_permils=permils,
        audition_parameter_values=dict(resolved.criteria or entry.parameters),
        audition_npc_rng_advances=resolved.npc_count * resolved.limit_turn,
        segment_start_rng_advances=0,
        resolve_initial_cards=True,
        upgrade_tiers=set(master.cards),
        final_audition=True,
        objective_id=SCORE_OBJECTIVE_VERSION,
        drink_ids=tuple(entry.drink_ids or ()),
    )
    return ExamLoading(master, produce, segment)


def build_session(master, catalog: TowerCatalog, stage: IdolRoadStage, entry: IdolRoadEntry):
    """The stage's session, opened by the core."""
    return open_exam(build_loading(master, catalog, stage, entry))


def build_loadout_session(master, catalog, document, seed):
    """Build from configuration only; no recorded hands/actions are accepted."""
    if document.get("schema") != "gkms.loadout.v1":
        raise ValueError("unsupported loadout schema")
    if any(k in document for k in ("battle_record", "action_path", "future_hands")):
        raise ValueError("loadout contains route/record data")
    mechanic = mechanic_for_idol_card(master.master_dir, document["idol_card_id"])
    plan_type = plan_type_for_idol_card(master.master_dir, document["idol_card_id"])
    declared_plan = document.get("plan_type")
    if declared_plan is not None and int(declared_plan) != plan_type:
        document = dict(document)
        document["plan_type_mismatch"] = {"declared": int(declared_plan),
                                          "master": plan_type}
    if document.get("mechanic", mechanic) != mechanic:
        raise ValueError("mechanic_mismatch: loadout disagrees with IdolCard.examEffectType")
    if "include_produce_default" not in document:
        raise ValueError("loadout must declare include_produce_default")
    params = {int(k): int(v) for k, v in (document.get("parameters") or {}).items()}
    if params and set(params) != {1, 2, 3}:
        raise ValueError("parameters must contain Vo/Da/Vi keys 1,2,3")
    if any(abs(v) >= 10000 for v in params.values()):
        raise ValueError("parameters contains permil-magnitude values; use score_bonus_permils")
    permils = document.get("score_bonus_permils")
    if permils is not None:
        permils = {int(k): int(v) for k, v in permils.items()}
        if set(permils) != {1, 2, 3} or any(v < 0 for v in permils.values()):
            raise ValueError("invalid score_bonus_permils")
    elif not params:
        raise ValueError("supply attributes or explicit score_bonus_permils")
    starting, maximum = int(document["starting_stamina"]), int(document["max_stamina"])
    if not 0 <= starting <= maximum or maximum <= 0:
        raise ValueError("invalid starting/max stamina")
    stage = IdolRoadStage(mechanic, int(document["layer"]),
                         int(document.get("pattern", 0)), document.get("battle_config_id"),
                         document.get("limit_turn"), document.get("tower", DEFAULT_TOWER))
    resolved = resolve_stage(catalog, stage)
    if not params and permils is None and not resolved.criteria:
        raise ValueError("missing attributes and battle-config criteria")
    entry = IdolRoadEntry(
        seed=int(seed), parameters=params, deck_cards=list(document["deck_cards"]),
        starting_stamina=starting, max_stamina=maximum,
        plan_type=plan_type, idol_card_id=document["idol_card_id"],
        score_bonus_permils=permils, produce_item_ids=list(document["produce_item_ids"]),
        main_item_enchant_ids=list(document.get("main_item_enchant_ids", ())),
        include_stage_items=bool(document.get("include_stage_items", False)),
        include_produce_default=bool(document["include_produce_default"]),
        drink_ids=list(document.get("drink_ids") or ()))
    return build_session(master, catalog, stage, entry), resolved


def run_policy(master_dir, stage: IdolRoadStage, entry: IdolRoadEntry,
               policy=greedy_policy, catalog: TowerCatalog | None = None,
               master: Master | None = None) -> dict:
    master_dir = Path(master_dir)
    master = master or Master.load(master_dir)
    catalog = catalog or TowerCatalog.load(master_dir, stage.tower)
    session = build_session(master, catalog, stage, entry)
    deck_size = len(session.deck.deck) + len(session.deck.hand)
    result = session.run(policy)
    result["stage_id"] = stage.stage_id
    result["mechanic"] = stage.mechanic
    result["stage"] = stage.stage
    result["pattern"] = stage.pattern
    result["gimmick_group_id"] = session.config.gimmick_group_id
    result["limit_turn"] = session.config.limit_turn
    resolved = resolve_stage(catalog, stage)
    result["battle_config_id"] = resolved.battle_config_id
    result["binding_source"] = resolved.binding_source
    result["score_bonus_permils"] = session.config.audition_permils
    result["turn_parameter_types"] = list(session.deck.turn_parameter_types or ())
    result["deck_size"] = deck_size
    result["unresolved_effects"] = list(result.get("unresolved", ()))
    result["unresolved_gimmick_conditions"] = [event for event in session.state.gimmick_events
        if isinstance(event.get("result"), dict) and "unimplemented_field_status_type" in event["result"]]
    if stage.battle_config_id:
        result["ratings"] = {
            parameter: rating.tier
            for parameter, rating in catalog.ratings(stage.battle_config_id,
                                                     entry.parameters).items()
        }
    return result


run_greedy = run_policy


# ------------------------------------------------------------------ CLI
def _default_master():
    return ROOT / "ro" / "master" / "tables"


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="self-driven Idol Road stage")
    parser.add_argument("mechanic")
    parser.add_argument("stage", type=int)
    parser.add_argument("--tower", default=DEFAULT_TOWER)
    parser.add_argument("--pattern", type=int, default=0)
    parser.add_argument("--battle-config", default=None)
    parser.add_argument("--limit-turn", type=int, default=None)
    parser.add_argument("--entry", default=None, help="JSON loadout (see scenario/fixtures)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--master", default=None)
    parser.add_argument("--candidates", action="store_true",
                        help="list the battle configs matching --limit-turn and exit")
    parser.add_argument("--schedule", action="store_true",
                        help="print the stage's 応援/トラブル timetable and exit")
    parser.add_argument("--briefing", action="store_true",
                        help="print the bound stage briefing and exit")
    parser.add_argument("--opponents", action="store_true",
                        help="print the bound opponent rows and exit")
    parser.add_argument("--parameters", nargs=3, type=int, metavar=("VOCAL", "DANCE", "VISUAL"),
                        help="entry parameters for briefing ratings and score bonuses")
    options = parser.parse_args(argv)

    master_dir = Path(options.master or _default_master())
    catalog = TowerCatalog.load(master_dir)

    stage = IdolRoadStage(options.mechanic, options.stage, options.pattern,
                          options.battle_config, options.limit_turn, options.tower)

    if options.briefing:
        params = None
        if options.parameters:
            params = {1: options.parameters[0], 2: options.parameters[1], 3: options.parameters[2]}
        print(json.dumps(catalog.stage_briefing(stage, params), ensure_ascii=False, indent=2))
        return 0

    if options.opponents:
        print(json.dumps(catalog.opponents(stage), ensure_ascii=False, indent=2))
        return 0

    if options.schedule:
        group_id, entries = catalog.gimmick_schedule(options.mechanic, options.stage, options.pattern)
        print(group_id)
        for item in entries:
            print(f"  turn {item.start_turn:>3} {'+' if item.is_positive else '-'} "
                  f"{item.field_status:<52} value={item.value:<6} {item.effect_id}")
        return 0

    if options.candidates:
        for row in candidate_battle_configs(catalog, options.limit_turn):
            print(f"{row['id']}  turn={row['turn']:<3} "
                  f"vo {row['vocalBad']}/{row['vocal']}/{row['vocalExcellent']}  "
                  f"da {row['danceBad']}/{row['dance']}/{row['danceExcellent']}  "
                  f"vi {row['visualBad']}/{row['visual']}/{row['visualExcellent']}")
        return 0

    if not options.entry:
        print("--entry is required: Idol Road assembles its deck client-side "
              "(the 8 Produce initial cards plus the main/sub memory card sets), "
              "so master cannot supply it.  tower:initial_deck stays unresolved.",
              file=sys.stderr)
        return 2

    entry = IdolRoadEntry.from_json(options.entry, seed=options.seed)
    result = run_policy(master_dir, stage, entry, catalog=catalog)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
