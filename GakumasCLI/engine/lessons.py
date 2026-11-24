# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""neu.lessons - a lesson step's turn limit and clear line, from master."""
from __future__ import annotations

import json
from pathlib import Path


def _rows(path):
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("decode_status", "decoded") != "decoded":
            continue
        yield row.get("decoded", row)


class LessonCatalog:
    def __init__(self, steps, levels):
        self.steps = steps
        self.levels = levels

    @classmethod
    def load(cls, master_dir):
        master_dir = Path(master_dir)
        steps = {row["id"]: row for row in _rows(master_dir / "ProduceStepLesson.jsonl")}
        levels = {row["id"]: row for row in _rows(master_dir / "ProduceStepLessonLevel.jsonl")}
        return cls(steps, levels)

    def level_of(self, step_id):
        return self.levels[self.steps[step_id]["produceStepLessonLevelId"]]

    def setup(self, step_id):
        """(limit_turn, clear line, success threshold) for one lesson step."""
        level = self.level_of(step_id)
        return level["limitTurn"], level["resultTargetValueLimit"], level.get("successThreshold")

    def name(self, step_id):
        return self.steps[step_id].get("name")
