# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idol Road master catalog and explicitly sourced API stage bindings."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

TOWER_PREFIX = "tower_"
DEFAULT_TOWER = "tower_001"

VOCAL, DANCE, VISUAL = 1, 2, 3
COLUMNS = {VOCAL: "vocal", DANCE: "dance", VISUAL: "visual"}

# tower mechanic -> ExamInitialDeck theme suffix
_DECK_THEME = {
    "exam_card_play_aggressive": "aggressive",
    "exam_concentration": "concentration",
    "exam_full_power": "full_power",
    "exam_preservation": "full_power",
    "exam_lesson_buff": "lesson_buff",
    "exam_parameter_buff": "parameter_buff",
    "exam_review": "review",
}

# mechanic theme -> the status the stage is built around, as the engine names it
MECHANIC_STATUS = {
    "exam_lesson_buff": "集中",
    "exam_parameter_buff": "好調",
    "exam_review": "好印象",
    "exam_card_play_aggressive": "やる気",
    "exam_full_power": "全力",
    "exam_preservation": "温存",
    "exam_concentration": "集中(指針)",
}

_BATTLE_CONFIG = re.compile(
    r"^p_exam_battle_config-(?P<tower>tower_\d+)-"
    r"(?P<vo>\d+)_(?P<da>\d+)_(?P<vi>\d+)-"
    r"(?P<vo_ex>\d+)_(?P<da_ex>\d+)_(?P<vi_ex>\d+)-turn_(?P<turn>\d+)$")
_GIMMICK = re.compile(
    r"^p_exam_gimmick-(?P<tower>tower_\d+)-(?P<mechanic>.+?)-"
    r"(?P<variant>\d+)-stage_(?P<stage>\d+)$")
_NPC = re.compile(
    r"^p_npc_group-(?P<tower>tower_\d+)-(?P<idol>[^-]+)-(?P<mechanic>.+?)-"
    r"stage_(?P<stage>\d+)$")
_ITEM = re.compile(
    r"^pitem_(?P<tower>tower_\d+)-(?P<mechanic>.+?)-(?P<slot>\d+)-"
    r"stage_(?P<stage>\d+)(?:-(?P<part>\d+))?$")


def _rows(path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("decode_status", "decoded") != "decoded":
                continue
            yield row.get("decoded", row)


@dataclass(frozen=True)
class GimmickEntry:
    """One row of a stage's 応援 / トラブル schedule."""
    group_id: str
    start_turn: int
    priority: int
    is_positive: bool | None
    field_status: str
    field_status_number: int | None
    value: int
    effect_id: str
    check_type: object = None


@dataclass(frozen=True)
class StageRating:
    """The ×/○/◎ tier of one entry parameter against a stage's criteria."""
    parameter_type: int
    value: int
    bad: int
    normal: int
    excellent: int

    @property
    def tier(self) -> str:
        if self.value >= self.excellent:
            return "excellent"
        if self.value >= self.normal:
            return "normal"
        if self.value >= self.bad:
            return "bad"
        return "below_bad"


class TowerCatalog:
    """The Idol Road slice of the master tables, keyed the way stages are."""

    def __init__(self, tower=DEFAULT_TOWER):
        self.tower = tower
        self.tower_family = re.match(r"tower_\d+", tower).group()
        self.towers = {}
        self.layers = {}
        self.stage_exams = {}
        self.capture_stage_exams = {}
        self.battle_configs: dict = {}
        self.score_configs: dict = {}
        self.gimmick_groups: dict = {}
        self.gimmick_variants: dict = {}
        self.npc_groups: dict = {}
        self.stage_items: dict = {}
        self.items: dict = {}
        self.item_effects: dict = {}
        self.initial_decks: dict = {}

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, master_dir, tower=DEFAULT_TOWER):
        master_dir = Path(master_dir)
        self = cls(tower)
        tower = self.tower_family
        token = f"-{tower}-"

        for row in _rows(master_dir / "ProduceExamBattleConfig.jsonl"):
            match = _BATTLE_CONFIG.match(row.get("id", ""))
            if match and match.group("tower") == tower:
                self.battle_configs[row["id"]] = row

        for row in _rows(master_dir / "ProduceExamBattleScoreConfig.jsonl"):
            row_id = row.get("id", "")
            if token not in row_id:
                continue
            self.score_configs.setdefault(row_id, []).append(
                (int(row.get("parameter", 0)),
                 {"vocal": row.get("vocalPermil", 0),
                  "dance": row.get("dancePermil", 0),
                  "visual": row.get("visualPermil", 0)}))
        for points in self.score_configs.values():
            points.sort(key=lambda item: item[0])

        for row in _rows(master_dir / "ProduceExamGimmickEffectGroup.jsonl"):
            match = _GIMMICK.match(row.get("id", ""))
            if not match or match.group("tower") != tower:
                continue
            status = row.get("fieldStatusType") or {}
            entry = GimmickEntry(
                group_id=row["id"],
                start_turn=int(row.get("startTurn") or 0),
                priority=int(row.get("priority") or 0),
                is_positive=row.get("isPositive"),
                field_status=status.get("name", ""),
                field_status_number=status.get("number"),
                value=int(row.get("fieldStatusValue") or 0),
                effect_id=row.get("produceExamEffectId", ""),
                check_type=row.get("fieldStatusCheckType"),
            )
            self.gimmick_groups.setdefault(row["id"], []).append(entry)
            key = (match.group("mechanic"), int(match.group("stage")))
            variants = self.gimmick_variants.setdefault(key, [])
            if row["id"] not in variants:
                variants.append(row["id"])
        for entries in self.gimmick_groups.values():
            entries.sort(key=lambda e: (e.start_turn, e.priority))
        for variants in self.gimmick_variants.values():
            variants.sort()

        for row in _rows(master_dir / "ProduceExamBattleNpcGroup.jsonl"):
            match = _NPC.match(row.get("id", ""))
            if not match or match.group("tower") != tower:
                continue
            key = (match.group("idol"), match.group("mechanic"), int(match.group("stage")))
            self.npc_groups.setdefault(key, []).append(row)
        for group in self.npc_groups.values():
            group.sort(key=lambda r: int(r.get("number") or 0))

        for row in _rows(master_dir / "ProduceItem.jsonl"):
            match = _ITEM.match(row.get("id", ""))
            if not match or match.group("tower") != tower:
                continue
            self.items[row["id"]] = row
            key = (match.group("mechanic"), int(match.group("stage")))
            self.stage_items.setdefault(key, []).append(row["id"])
        for ids in self.stage_items.values():
            ids.sort()

        for row in _rows(master_dir / "ExamInitialDeck.jsonl"):
            self.initial_decks[row["id"]] = list(row.get("produceCardIds") or ())

        wanted = {effect_id
                  for item in self.items.values()
                  for effect_id in (item.get("produceItemEffectIds") or ())}
        for row in _rows(master_dir / "ProduceItemEffect.jsonl"):
            if row.get("id") in wanted:
                self.item_effects[row["id"]] = row
        for row in _rows(master_dir / "Tower.jsonl"):
            self.towers[row["id"]] = row
        for row in _rows(master_dir / "TowerLayer.jsonl"):
            self.layers[(row["towerId"], int(row["number"]))] = row
        for row in _rows(master_dir / "TowerLayerExam.jsonl"):
            key = (row["towerId"], int(row["number"]))
            self.stage_exams.setdefault(key, []).append(row)
        captured = master_dir.parent / "tower_capture_bindings.json"
        if captured.exists():
            for row in json.loads(captured.read_text(encoding="utf-8"))["exams"]:
                key = (row["towerId"], int(row["number"]))
                self.capture_stage_exams.setdefault(key, []).append(row)
        return self

    def resolve_exam(self, tower_id, layer, mechanic):
        """Resolve one of seven exam types; native master rows take priority."""
        key = (tower_id, int(layer))
        for source, rows in (("master:TowerLayerExam", self.stage_exams.get(key, ())),
                             ("capture:Tower/GetLayer", self.capture_stage_exams.get(key, ()))):
            for row in rows:
                effect = row["examEffectType"]
                name = effect["name"] if isinstance(effect, dict) else effect
                if name.removeprefix("PRODUCE_EXAM_EFFECT_TYPE_").lower() == mechanic:
                    return dict(row, binding_source=source)
        return None

    def item_enchant_ids(self, item_ids):
        out = []
        for item_id in item_ids:
            item = self.items[item_id]
            for effect_id in item.get("produceItemEffectIds", ()):
                enchant = self.item_effects[effect_id].get("produceExamStatusEnchantId")
                if enchant and enchant not in out:
                    out.append(enchant)
        return out

    def produce_default_deck(self, mechanic):
        """The 8 Produce starter cards an Idol Road deck always carries."""
        row = self.initial_decks.get(f"initial_deck-produce_default-{_DECK_THEME[mechanic]}")
        return list(row or ())

    def contest_deck(self, idol_card_id):
        return list(self.initial_decks.get(f"initial_deck-contest-{idol_card_id}") or ())

    def stage_item_enchant_ids(self, mechanic, stage):
        """Enchant ids of the 初星ライト P items the stage itself grants."""
        out = []
        for item_id in self.stage_items.get((mechanic, int(stage)), ()):
            item = self.items[item_id]
            for effect_id in item.get("produceItemEffectIds") or ():
                effect = self.item_effects.get(effect_id) or {}
                enchant_id = effect.get("produceExamStatusEnchantId")
                if enchant_id and enchant_id not in out:
                    out.append(enchant_id)
        return out

    # ------------------------------------------------------------- indexes
    @property
    def mechanics(self):
        return sorted({mechanic for mechanic, _ in self.gimmick_variants} | {"exam_preservation"})

    @property
    def stages(self):
        return sorted({stage for _, stage in self.gimmick_variants})

    @property
    def idols(self):
        return sorted({idol for idol, _, _ in self.npc_groups})

    def gimmick_schedule(self, mechanic, stage, variant=0):
        """The 応援 / トラブル timetable of one (mechanic, stage) pattern."""
        theme = "exam_full_power" if mechanic == "exam_preservation" else mechanic
        variants = self.gimmick_variants.get((theme, int(stage)), [])
        if not variants:
            raise KeyError((mechanic, stage))
        group_id = variants[min(int(variant), len(variants) - 1)]
        return group_id, list(self.gimmick_groups[group_id])

    @staticmethod
    def _stage_parts(stage):
        if isinstance(stage, dict):
            return (stage.get("tower", DEFAULT_TOWER), stage["stage"], stage["mechanic"], stage.get("pattern", 0))
        return (
            getattr(stage, "tower", DEFAULT_TOWER),
            getattr(stage, "stage"),
            getattr(stage, "mechanic"),
            getattr(stage, "pattern", 0),
        )

    def stage_briefing(self, stage, parameters=None):
        """Return the player-visible stage briefing from the bound exam row."""
        tower, layer, mechanic, pattern = self._stage_parts(stage)
        bound = self.resolve_exam(tower, layer, mechanic)
        if bound:
            group_id = bound["produceExamGimmickEffectGroupId"]
            config_id = bound.get("produceExamBattleConfigId")
            source = bound.get("binding_source", "master:TowerLayerExam")
        else:
            group_id, _ = self.gimmick_schedule(mechanic, layer, pattern)
            config_id = None
            source = "explicit_override"
        entries = list(self.gimmick_groups[group_id])
        criteria = self.criteria(config_id) if config_id else {}
        result = {
            "tower": tower,
            "layer": int(layer),
            "mechanic": mechanic,
            "turn_limit": int(self.battle_configs[config_id]["turn"]) if config_id else None,
            "criteria": criteria,
            "gimmick_group_id": group_id,
            "gimmick_schedule": [
                {
                    "start_turn": row.start_turn,
                    "priority": row.priority,
                    "is_positive": row.is_positive,
                    "field_status": row.field_status,
                    "field_status_number": row.field_status_number,
                    "value": row.value,
                    "effect_id": row.effect_id,
                    "check_type": row.check_type,
                }
                for row in entries
            ],
            "binding_source": source,
        }
        if parameters is not None:
            params = {int(key): int(value) for key, value in parameters.items()}
            if config_id:
                result["score_bonus_permils"] = self.score_bonus_permils(config_id, params)
                result["ratings"] = {
                    str(key): {
                        "value": rating.value,
                        "bad": rating.bad,
                        "normal": rating.normal,
                        "excellent": rating.excellent,
                        "tier": rating.tier,
                    }
                    for key, rating in self.ratings(config_id, params).items()
                }
        return result

    def opponents(self, stage):
        """Return the bound NPC interval and score/parameter split rows."""
        tower, layer, mechanic, _ = self._stage_parts(stage)
        bound = self.resolve_exam(tower, layer, mechanic)
        npc_group_id = (bound or {}).get("produceExamBattleNpcGroupId")
        if not npc_group_id:
            raise KeyError((tower, int(layer), mechanic, "npc_group"))
        rows = [
            row for group in self.npc_groups.values()
            for row in group
            if row.get("id") == npc_group_id
        ]
        rows.sort(key=lambda row: int(row.get("number") or 0))
        output = []
        for row in rows:
            output.append({
                "number": int(row["number"]),
                "npc_id": row["produceExamBattleNpcMobId"],
                "score_interval": [int(row["scoreMin"]), int(row["scoreMax"])],
                "split_permils": {
                    "op": int(row["opScorePermil"]),
                    "mid": int(row["midScorePermil"]),
                    "ed": int(row["edScorePermil"]),
                },
                "parameter_permils": {
                    "vocal": int(row["vocalPermil"]),
                    "dance": int(row["dancePermil"]),
                    "visual": int(row["visualPermil"]),
                },
            })
        return {
            "tower": tower,
            "layer": int(layer),
            "mechanic": mechanic,
            "npc_group_id": npc_group_id,
            "rows": output,
            "top_row_interval": output[0]["score_interval"] if output else None,
            "top_row_npc_id": output[0]["npc_id"] if output else None,
            "binding_source": (bound or {}).get("binding_source", "explicit_override"),
        }

    def npc_group(self, idol, mechanic, stage):
        return list(self.npc_groups[(idol, mechanic, int(stage))])

    def turn_limits(self):
        """Every turn limit the tower's battle configs use."""
        return sorted({int(row["turn"]) for row in self.battle_configs.values()})

    def criteria(self, battle_config_id):
        row = self.battle_configs[battle_config_id]
        return {VOCAL: row.get("vocal"), DANCE: row.get("dance"), VISUAL: row.get("visual")}

    def ratings(self, battle_config_id, parameters):
        """Per-parameter ×/○/◎ tier for the memory entering this stage."""
        row = self.battle_configs[battle_config_id]
        out = {}
        for parameter_type, column in COLUMNS.items():
            out[parameter_type] = StageRating(
                parameter_type=parameter_type,
                value=int(parameters.get(parameter_type) or 0),
                bad=int(row[f"{column}Bad"]),
                normal=int(row[column]),
                excellent=int(row[f"{column}Excellent"]),
            )
        return out

    # ------------------------------------------------------------- scoring
    def _interpolate(self, score_config_id, column, parameter):
        points = self.score_configs[score_config_id]
        if parameter <= points[0][0]:
            return float(points[0][1][column])
        if parameter >= points[-1][0]:
            return float(points[-1][1][column])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= parameter <= x1:
                low, high = float(y0[column]), float(y1[column])
                return low + (parameter - x0) * (high - low) / (x1 - x0)
        raise AssertionError("unreachable")

    def penalty(self, battle_config_id, parameters):
        """Same shortfall rule as a Produce audition: 10% at the criterion,
        25% at parameter 0, summed over the parameters that fall short and
        applied to all three."""
        total = 0.0
        for parameter_type, criterion in self.criteria(battle_config_id).items():
            value = parameters.get(parameter_type)
            if criterion and value is not None and value < criterion:
                total += 0.25 - (value / criterion) * 0.15
        return total

    def score_bonus_permils(self, battle_config_id, parameters):
        """``{parameter_type: permil}`` -- the スコアボーナス the stage grants."""
        battle_config = self.battle_configs[battle_config_id]
        score_config_id = battle_config["produceExamBattleScoreConfigId"]
        factor = 1.0 - self.penalty(battle_config_id, parameters)
        permils = {}
        for parameter_type, column in COLUMNS.items():
            raw = self._interpolate(score_config_id, column, parameters[parameter_type])
            permils[parameter_type] = int(math.ceil((raw * factor + 1000) / 10.0)) * 10
        return permils

    # ------------------------------------------------------------- reports
    def unresolved(self):
        """What master cannot answer for Idol Road, named precisely."""
        gaps = []
        if not self.stage_exams:
            gaps.extend([
                "tower:stage_table - TowerLayerExam is empty in the 2026-09-11 cache; "
                "only explicitly captured (tower_id, layer, exam) bindings are available",
                "tower:stage_pattern - covered captures bind exact groups; unobserved layers remain unknown",
            ])
        gaps.extend([
            "tower:memory_card_set - supply the complete TowerStartResponse#105 deck in wire order",
            "tower:memory_binding - main/sub memory contributions require player-specific entry data",
        ])
        return gaps

    def stage_rows(self):
        """One row per (mechanic, stage, pattern) with everything master knows."""
        out = []
        for (mechanic, stage), variants in sorted(self.gimmick_variants.items()):
            for index, group_id in enumerate(variants):
                entries = self.gimmick_groups[group_id]
                out.append({
                    "mechanic": mechanic,
                    "status": MECHANIC_STATUS.get(mechanic, ""),
                    "stage": stage,
                    "pattern": index,
                    "gimmick_group_id": group_id,
                    "gimmick_count": len(entries),
                    "gimmick_turns": [e.start_turn for e in entries],
                    "positive": sum(1 for e in entries if e.is_positive),
                    "negative": sum(1 for e in entries if not e.is_positive),
                    "stage_items": list(self.stage_items.get((mechanic, stage), ())),
                    "idols_with_npc": sum(
                        1 for idol in self.idols
                        if (idol, mechanic, stage) in self.npc_groups),
                })
        return out


# ------------------------------------------------------------------ CLI
def _default_master():
    return Path(__file__).resolve().parents[1] / "ro" / "master" / "tables"


def main(argv=None):
    import argparse
    import csv

    parser = argparse.ArgumentParser(description="Idol Road (tower) master scope")
    parser.add_argument("command", choices=["summary", "stages", "gimmick", "npc", "unresolved"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("--master", default=None)
    parser.add_argument("--tower", default=DEFAULT_TOWER)
    parser.add_argument("--csv", default=None, help="write the stage table here")
    options = parser.parse_args(argv)

    catalog = TowerCatalog.load(options.master or _default_master(), options.tower)

    if options.command == "summary":
        print(f"tower={catalog.tower}")
        print(f"battle_configs={len(catalog.battle_configs)} "
              f"score_configs={len(catalog.score_configs)} "
              f"gimmick_groups={len(catalog.gimmick_groups)} "
              f"npc_groups={len(catalog.npc_groups)} "
              f"stage_items={len(catalog.items)}")
        print(f"mechanics={len(catalog.mechanics)} stages={len(catalog.stages)} "
              f"idols={len(catalog.idols)}")
        print(f"turn_limits={catalog.turn_limits()}")
        for mechanic in catalog.mechanics:
            theme = "exam_full_power" if mechanic == "exam_preservation" else mechanic
            pairs = [k for k in catalog.gimmick_variants if k[0] == theme]
            two = sum(1 for k in pairs if len(catalog.gimmick_variants[k]) > 1)
            print(f"  {mechanic:<28} {MECHANIC_STATUS.get(mechanic,''):<10} "
                  f"stages={len(pairs):<3} two_pattern={two}")
        return 0

    if options.command == "stages":
        rows = catalog.stage_rows()
        if options.csv:
            path = Path(options.csv)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: (";".join(map(str, v)) if isinstance(v, list) else v)
                                     for k, v in row.items()})
            print(path)
        print(f"{'mechanic':<28}{'stage':>6}{'pat':>4}{'gim':>5}{'+':>4}{'-':>4}  turns")
        for row in rows:
            print(f"{row['mechanic']:<28}{row['stage']:>6}{row['pattern']:>4}"
                  f"{row['gimmick_count']:>5}{row['positive']:>4}{row['negative']:>4}  "
                  f"{row['gimmick_turns']}")
        return 0

    if options.command == "gimmick":
        mechanic = options.args[0]
        stage = int(options.args[1])
        variant = int(options.args[2]) if len(options.args) > 2 else 0
        group_id, entries = catalog.gimmick_schedule(mechanic, stage, variant)
        print(group_id)
        for entry in entries:
            sign = "+" if entry.is_positive else "-"
            print(f"  turn {entry.start_turn:>3} {sign} prio={entry.priority:<3} "
                  f"{entry.field_status:<52} value={entry.value:<6} {entry.effect_id}")
        return 0

    if options.command == "npc":
        idol, mechanic, stage = options.args[0], options.args[1], int(options.args[2])
        for row in catalog.npc_group(idol, mechanic, stage):
            print(f"  #{row['number']} {row['produceExamBattleNpcMobId']:<20} "
                  f"score {row['scoreMin']}-{row['scoreMax']} "
                  f"vo/da/vi {row['vocalPermil']}/{row['dancePermil']}/{row['visualPermil']} "
                  f"op/mid/ed {row['opScorePermil']}/{row['midScorePermil']}/{row['edScorePermil']}")
        return 0

    if options.command == "unresolved":
        for line in catalog.unresolved():
            print(line)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
