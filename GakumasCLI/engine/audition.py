# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""neu.audition - the 審査 (audition) score bonus, from master."""
from __future__ import annotations

import json
from pathlib import Path

# the engine's parameter_type numbering
VOCAL, DANCE, VISUAL = 1, 2, 3
COLUMNS = {VOCAL: "vocal", DANCE: "dance", VISUAL: "visual"}


def _rows(path):
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("decode_status", "decoded") != "decoded":
            continue
        yield row.get("decoded", row)


class AuditionCatalog:
    """The three master tables above, joined."""

    def __init__(self, difficulties, battle_configs, score_configs):
        self.difficulties = difficulties
        self.battle_configs = battle_configs
        self.score_configs = score_configs

    @classmethod
    def load(cls, master_dir):
        master_dir = Path(master_dir)
        difficulties = {}
        for row in _rows(master_dir / "ProduceStepAuditionDifficulty.jsonl"):
            step = (row.get("stepType") or {}).get("name", "")
            difficulties[(row["id"], row.get("produceId"), step, int(row.get("number") or 1))] = row
        battle_configs = {row["id"]: row for row in _rows(master_dir / "ProduceExamBattleConfig.jsonl")}
        score_configs = {}
        for row in _rows(master_dir / "ProduceExamBattleScoreConfig.jsonl"):
            score_configs.setdefault(row["id"], []).append(
                (int(row.get("parameter", 0)),
                 {"vocal": row.get("vocalPermil", 0), "dance": row.get("dancePermil", 0),
                  "visual": row.get("visualPermil", 0)}))
        for points in score_configs.values():
            points.sort(key=lambda item: item[0])
        return cls(difficulties, battle_configs, score_configs)

    def difficulty(self, difficulty_id, step_type, produce_id=None, number=None):
        """``step_type`` is the enum name or its tail, e.g. ``AUDITION_MID1``."""
        found = [row for (row_id, produce, name, num), row in self.difficulties.items()
                 if row_id == difficulty_id and name.endswith(step_type)
                 and (produce_id is None or produce == produce_id)
                 and (number is None or num == int(number))]
        if len(found) != 1:
            raise KeyError((difficulty_id, produce_id, step_type, number, len(found)))
        return found[0]

    def numbers(self, difficulty_id, step_type, produce_id):
        """The audition numbers the master offers at this step (sorted)."""
        return sorted(num for (row_id, produce, name, num) in self.difficulties
                      if row_id == difficulty_id and name.endswith(step_type) and produce == produce_id)

    def battle_config_of(self, difficulty_id, step_type, produce_id=None, number=None):
        difficulty = self.difficulty(difficulty_id, step_type, produce_id, number)
        return self.battle_configs[difficulty["produceExamBattleConfigId"]]

    def criteria(self, battle_config_id):
        """That audition's 審査基準 per parameter type."""
        row = self.battle_configs[battle_config_id]
        return {VOCAL: row.get("vocal"), DANCE: row.get("dance"), VISUAL: row.get("visual")}

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

    def _interpolate_up(self, score_config_id, column, parameter):
        """the interpolation in exact arithmetic, rounded up to an integer (base_permils)."""
        points = self.score_configs[score_config_id]
        if parameter <= points[0][0]:
            return int(points[0][1][column])
        if parameter >= points[-1][0]:
            return int(points[-1][1][column])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= parameter <= x1:
                num = int(y0[column]) * (x1 - x0) + (parameter - x0) * (int(y1[column]) - int(y0[column]))
                return -(-num // (x1 - x0))
        raise AssertionError("unreachable")

    def penalty(self, battle_config_id, parameters):
        """Σ of the shortfalls; 0 when every parameter meets its criterion."""
        total = 0.0
        for parameter_type, criterion in self.criteria(battle_config_id).items():
            value = parameters.get(parameter_type)
            if criterion and value is not None and value < criterion:
                total += 0.25 - (value / criterion) * 0.15
        return total

    def penalty_permil(self, battle_config_id, parameters):
        """Σ of the shortfalls in permil, each floored (250 - 150 x value / criterion)."""
        total = 0
        for parameter_type, criterion in self.criteria(battle_config_id).items():
            value = parameters.get(parameter_type)
            if criterion and value is not None and value < criterion:
                total += (250 * int(criterion) - 150 * int(value)) // int(criterion)
        return total

    def base_permils(self, battle_config_id, parameters):
        """``parameters`` maps parameter_type -> the idol's Vo/Da/Vi at the
        moment the audition opens.
        """
        battle_config = self.battle_configs[battle_config_id]
        score_config_id = battle_config["produceExamBattleScoreConfigId"]
        keep = 1000 - self.penalty_permil(battle_config_id, parameters)
        permils = {}
        for parameter_type, column in COLUMNS.items():
            raw = self._interpolate_up(score_config_id, column, int(parameters[parameter_type]))
            permils[parameter_type] = -(-(raw * keep // 1000 + 1000) // 10) * 10
        return permils

    def base_permils_for_difficulty(self, difficulty_id, step_type, parameters, produce_id=None, number=None):
        battle_config = self.battle_config_of(difficulty_id, step_type, produce_id, number)
        return self.base_permils(battle_config["id"], parameters)
