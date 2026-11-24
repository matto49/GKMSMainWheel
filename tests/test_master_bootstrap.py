# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""tools/master_bootstrap.py: the master tables built from a gakumasu-diff checkout alone, with the schema (keys,
field kinds, enum numbers) written from decoded tables -- a two-row table both ways, on synthetic rows."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = next(p for p in Path(__file__).resolve().parents if (p / "GakumasCLI" / "paths.py").is_file())
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from GakumasCLI.tools import master_bootstrap as MB  # noqa: E402

LOCAL = [
    {"decode_status": "decoded", "outer": {"Id": "p_card-x-1", "UpgradeCount": 0},
     "decoded": {"id": "p_card-x-1", "planType": {"name": "PRODUCE_PLAN_TYPE_PLAN1", "number": 2},
                 "category": {"name": "PRODUCE_CARD_CATEGORY_ACTIVE_SKILL", "number": 1}, "stamina": 4,
                 "order": 13000010000002, "playEffects": [{"produce_exam_effect_id": "e-1", "hide_icon": True}]}},
    {"decode_status": "decoded", "outer": {"Id": "p_card-x-1", "UpgradeCount": 1},
     "decoded": {"id": "p_card-x-1", "upgradeCount": 1, "planType": {"name": "PRODUCE_PLAN_TYPE_PLAN1", "number": 2},
                 "category": {"name": "PRODUCE_CARD_CATEGORY_MENTAL_SKILL", "number": 2}, "stamina": 3,
                 "order": 13000010000002, "playEffects": [{"produce_exam_effect_id": "e-2"}]}},
]
MIRROR = """- id: p_card-x-1
  upgradeCount: 0
  planType: ProducePlanType_Plan1
  category: ProduceCardCategory_ActiveSkill
  stamina: 4
  order: "13000010000002"
  playEffects:
  - produceExamEffectId: e-1
    hideIcon: true
- id: p_card-x-1
  upgradeCount: 1
  planType: ProducePlanType_Plan1
  category: ProduceCardCategory_MentalSkill
  stamina: 3
  order: "13000010000002"
  playEffects:
  - produceExamEffectId: e-2
    hideIcon: false
"""


class MasterBootstrap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bootstrap-"))
        self.local = self.tmp / "local"
        self.local.mkdir()
        (self.local / "ProduceCard.jsonl").write_text("".join(json.dumps(r) + "\n" for r in LOCAL), encoding="utf-8")
        self.mirror = self.tmp / "mirror"
        self.mirror.mkdir()
        (self.mirror / "ProduceCard.yaml").write_text(MIRROR, encoding="utf-8")

    def test_the_built_rows_equal_the_decoded_ones(self):
        schema = self.tmp / "schema.json"
        MB.write_schema(self.local, schema)
        doc = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(doc["tables"]["ProduceCard"]["outer"], ["Id", "UpgradeCount"])
        self.assertNotIn("decoded", json.dumps(doc["tables"]))
        self.assertIn("producecardcategorymentalskill", doc["enums"])
        _source, done = MB.build(self.mirror, self.tmp / "built", MB.load_schema(schema), log=lambda *_: None)
        self.assertEqual(done, {"ProduceCard": 2})
        built = [json.loads(line) for line in (self.tmp / "built" / "ProduceCard.jsonl").read_text(
            encoding="utf-8").splitlines()]
        self.assertEqual([r["decoded"] for r in built], [r["decoded"] for r in LOCAL])
        self.assertEqual([r["outer"] for r in built], [r["outer"] for r in LOCAL])
        res = MB.compare(self.local, self.tmp / "built")["ProduceCard"]
        self.assertEqual((res["equal"], res["missing"], res["extra"]), (2, 0, 0))

    def test_an_enum_member_without_a_number_stops_the_build(self):
        schema = self.tmp / "schema.json"
        MB.write_schema(self.local, schema)
        (self.mirror / "ProduceCard.yaml").write_text(
            MIRROR.replace("ProduceCardCategory_MentalSkill", "ProduceCardCategory_NeverSeen"), encoding="utf-8")
        loaded = MB.load_schema(schema)
        loaded["tables"]["ProduceCard"]["kinds"][("category",)] = "enum"
        with self.assertRaises(ValueError):
            MB.build(self.mirror, self.tmp / "built", loaded, log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
