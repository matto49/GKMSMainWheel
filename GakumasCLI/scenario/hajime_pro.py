# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""scenario.hajime_pro - the first hard-coded script: 定期公演 初 PRO, 秦谷美鈴
アノマリー, R21's loadout and route, driven end to end by the greedy policy
with no wire.

    python scenario/hajime_pro.py            # R21 seeds
    python scenario/hajime_pro.py 12345      # one base seed for all segments
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from GakumasCLI.engine.audition import AuditionCatalog  # noqa: E402
from GakumasCLI.engine.lessons import LessonCatalog  # noqa: E402
from GakumasCLI.engine.card_data import effective_audition_parameter_permil  # noqa: E402
from GakumasCLI.scenario.exam_session import ExamSession, Master, greedy_policy  # noqa: E402
from GakumasCLI.engine.providers.corpus import (  # noqa: E402
    R21_MEMORY_ABILITY,
    R21_MEMORIES,
    R21_PRIDE_CUSTOMIZE,
    default_r21_spec,
)
from GakumasCLI.engine.providers.self_drive import SelfDriveProvider  # noqa: E402
from GakumasCLI.engine.produce_run import ProduceIdentity  # noqa: E402

PRIDE_CUSTOMIZE = R21_PRIDE_CUSTOMIZE

MEMORY_CARDS = [
    {
        "produce_card_id": memory["produce_card"]["id"],
        "base_upgrade_count": int(memory["produce_card"].get("upgrade_count") or 0),
        "origin_type": 4,
        "customizes": list(memory.get("customizes") or ()),
    }
    for memory in R21_MEMORIES
]

ITEM_ENCHANTS = [
    "enchant-p_item_effect_03-3-133-0-enc01",
    "enchant-p_item_effect_00-3-011-0-enc01",
    "enchant-p_item_effect_03-3-007-0-enc01",
    "enchant-p_item_effect_00-3-010-0-enc01",
]

MEMORY_ABILITIES = [
    R21_MEMORY_ABILITY,
]

PRODUCE_ID = "produce-002"
AUDITION_EFFECT_BONUS_PERMIL = 500
AUDITION_PARAMETERS = {
    "AUDITION_MID1": {1: 552, 2: 302, 3: 526},
    "AUDITION_FINAL": {1: 1211, 2: 753, 3: 1282},
}
MASTER_TABLES = ROOT / "ro" / "master" / "tables"


def _decoded_rows(path):
    """Yield decoded rows without treating an absent table as observed data."""

    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("decode_status", "decoded")).lower() not in {"decoded", "decode"}:
                continue
            decoded = row.get("decoded", row)
            if isinstance(decoded, dict):
                yield decoded


def master_opening_deck(master_dir=MASTER_TABLES, idol_card_id="i_card-hmsz-3-000"):
    """Resolve the scenario opening card IDs from ``IdolCard.examInitialDeckId``."""

    master_dir = Path(master_dir)
    identity = ProduceIdentity.from_master(master_dir, idol_card_id, PRODUCE_ID)
    decks = {
        str(row.get("id")): row
        for row in _decoded_rows(master_dir / "ExamInitialDeck.jsonl")
        if row.get("id")
    }
    try:
        deck = decks[identity.initial_deck_id]
    except KeyError as exc:
        raise KeyError(
            f"ExamInitialDeck {identity.initial_deck_id!r} for IdolCard {idol_card_id!r}"
        ) from exc
    card_ids = deck.get("produceCardIds")
    if not isinstance(card_ids, list) or not card_ids:
        raise ValueError(f"ExamInitialDeck {identity.initial_deck_id!r} has no produceCardIds")
    upgrade_counts = deck.get("produceCardUpgradeCounts")
    result = []
    for index, card_id in enumerate(card_ids):
        item = {"produce_card_id": str(card_id)}
        if isinstance(upgrade_counts, list) and index < len(upgrade_counts) and upgrade_counts[index] is not None:
            item["base_upgrade_count"] = int(upgrade_counts[index])
        result.append(item)
    return result


OPENING_DECK = master_opening_deck()


def audition_setup(step_type, catalog=None, master_dir=None):
    """(clear line, cap, effective permils) for one audition, from master."""
    master_dir = master_dir or MASTER_TABLES
    identity = ProduceIdentity.from_master(master_dir, "i_card-hmsz-3-000", PRODUCE_ID)
    catalog = catalog or AuditionCatalog.load(master_dir)
    difficulty = catalog.difficulty(identity.difficulty_id, step_type, PRODUCE_ID)
    base = catalog.base_permils_for_difficulty(identity.difficulty_id, step_type,
                                               AUDITION_PARAMETERS[step_type], PRODUCE_ID)
    permils = {parameter_type: effective_audition_parameter_permil(value, AUDITION_EFFECT_BONUS_PERMIL)
               for parameter_type, value in base.items()}
    return difficulty["baseScore"], difficulty.get("forceEndScore") or difficulty["baseScore"], permils


_ROUTE_TEMPLATE = [
    ("p_step_lesson-produce-002-exam_full_power_01-normal-01", "Lesson Vocal NORMAL", None, None, 1, 1221133133, None, None, None, False),
    ("p_step_lesson-produce-002-exam_full_power_01-sp-01", "Lesson Visual SP", None, None, 3, 650592160, None, None, None, False),
    ("p_step_lesson-produce-002-exam_full_power_01-hard-01", "Lesson Vocal HARD", None, None, 1, 1287411125, None, None, None, False),
    ("AuditionMid1", "Audition Mid1", 9, "AUDITION_MID1", None, 892421402, None, None, {1: 300, 2: 200, 3: 250}, False),
    ("p_step_lesson-produce-002-exam_full_power_01-sp-02", "Lesson Visual SP", None, None, 3, 256530169, None, None, None, False),
    ("p_step_lesson-produce-002-exam_full_power_01-normal-04", "Lesson Vocal NORMAL", None, None, 1, 1072633962, None, None, None, False),
    ("p_step_lesson-produce-002-exam_full_power_01-sp-04", "Lesson Visual SP", None, None, 3, 1750782030, None, None, None, False),
    ("p_step_lesson-produce-002-exam_full_power_01-hard-02", "Lesson Vocal HARD", None, None, 1, 2012812433, None, None, None, False),
    ("AuditionFinal", "Audition Final", 12, "AUDITION_FINAL", None, 407116536, None, None, {1: 700, 2: 400, 3: 600}, True),
]


_ROUTE_CACHE = {}


def resolve_route(master_dir=None):
    """`_ROUTE_TEMPLATE` with the audition rows filled in from master."""
    master_dir = Path(master_dir or MASTER_TABLES)
    key = str(master_dir)
    if key not in _ROUTE_CACHE:
        catalog = AuditionCatalog.load(master_dir)
        identity = ProduceIdentity.from_master(master_dir, "i_card-hmsz-3-000", PRODUCE_ID)
        lessons = LessonCatalog.load(master_dir)
        rows = []
        for row in _ROUTE_TEMPLATE:
            if isinstance(row[3], str):
                _clear_line, cap, permils = audition_setup(row[3], catalog)
                difficulty = catalog.difficulty(identity.difficulty_id, row[3], PRODUCE_ID)
                gimmick = row[6] or difficulty.get("produceExamGimmickEffectGroupId")
                row = row[:2] + (row[2], cap) + row[4:6] + (gimmick,) + (permils,) + row[8:]
            else:
                limit_turn, clear_line, _threshold = lessons.setup(row[0])
                row = row[:2] + (limit_turn, clear_line) + row[4:]
            rows.append(row)
        _ROUTE_CACHE[key] = rows
    return _ROUTE_CACHE[key]


def __getattr__(name):
    if name == "ROUTE":
        return resolve_route()
    raise AttributeError(name)


def run_with_provider(base_seed=None, master_dir=ROOT / "ro" / "master" / "tables", policy=greedy_policy, verbose=True):
    """Run the nine-segment scenario and expose its provider state."""

    spec = default_r21_spec(run_seed=None if base_seed is None else int(base_seed))
    spec["deck_cards"] = [dict(card) for card in master_opening_deck(master_dir)]
    provider = SelfDriveProvider(spec, master_dir=master_dir)
    produce = provider.build(master_dir)
    for provenance_path in list(produce.provenance):
        if provenance_path.startswith("deck_cards["):
            produce.provenance[provenance_path] = {
                "source": "a",
                "locator": "master:IdolCard/i_card-hmsz-3-000.examInitialDeckId -> "
                           "master:ExamInitialDeck/initial_deck-full_power.produceCardIds",
            }
    produce.assert_provenance_complete()
    master = Master.load(master_dir, exam_setting_id=produce.identity.exam_setting_id)
    results = []
    for index in range(1, len(produce.steps) + 1):
        provider.prepare_step(index)
        config = produce.segment_config(master, index)
        session = ExamSession(master, produce, config)
        result = session.run(policy)
        results.append(result)
        if verbose:
            print(f"s{index} {config.segment_type:20} turns={result['turns']:>2} score={result['score']:>6} cleared={str(result['cleared']):5} "
                  f"stamina={result['stamina']:>2} block={result['block']:>2} plays={result['plays']:>2} unresolved={len(result['unresolved'])}")
    return produce, provider, results


def run(base_seed=None, master_dir=ROOT / "ro" / "master" / "tables", policy=greedy_policy, verbose=True):
    """Compatibility wrapper returning the historical result list."""

    _produce, _provider, results = run_with_provider(base_seed, master_dir, policy, verbose)
    return results


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
