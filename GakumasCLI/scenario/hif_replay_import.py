#!/usr/bin/env python3
# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import a captured ``Produce/History`` response as a replayable HIF corpus."""
import argparse
import json
from pathlib import Path

DRINK_FIELD, SUPPORT_FIELD, ACTION_FIELD = 60, 61, 100
_ACTION = {1: "play", 2: "drink", 3: "end", 4: "select"}


def _varint(buf, pos):
    result = shift = 0
    while True:
        byte = buf[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not byte & 0x80:
            return result, pos
        shift += 7


def walk(buf):
    out, pos = [], 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        elif wire == 2:
            length, pos = _varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        else:
            raise ValueError(f"wire type {wire}")
        out.append((number, wire, value))
    return out


def each(fields, number):
    return [value for num, _wire, value in fields if num == number]


def one(fields, number, default=None):
    hit = each(fields, number)
    return hit[0] if hit else default


def _text(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _card(buf):
    fields = walk(buf)
    card = {"produce_card_id": _text(one(fields, 1)),
            "current_upgrade_count": int(one(fields, 2, 0) or 0)}
    customizes = [{"id": _text(one(walk(c), 1)),
                   "customize_count": int(one(walk(c), 2, 1) or 1)}
                  for c in each(fields, 3)]
    if customizes:
        card["customizes"] = customizes
    return card


def extract(response_bytes):
    """Return one dict per audition round found in the response."""
    history = walk(one(walk(response_bytes), 1))
    rounds = []
    for audition in each(history, 12):
        audition_fields = walk(audition)
        blob = one(audition_fields, 99)
        if blob is None:
            continue
        situation = walk(blob)
        stage = walk(one(situation, 14))
        memory_rows = [walk(v) for v in each(stage, 110)]
        customize_rows = [walk(v) for v in each(stage, 111)]
        section = walk(one(stage, 10))
        player = walk(one(section, 3))
        rounds.append({
            "step_type": int(one(audition_fields, 1, 0) or 0),
            "rank": int(one(audition_fields, 3, 0) or 0),
            "recorded_score": int(one(audition_fields, 4, 0) or 0),
            "app_version": _text(one(situation, 1)),
            "master_version": _text(one(situation, 3)),
            "master_hash": _text(one(situation, 4)),
            "criteria": {1: int(one(situation, 10, 0) or 0),
                         2: int(one(situation, 11, 0) or 0),
                         3: int(one(situation, 12, 0) or 0)},
            "exam_setting_id": _text(one(situation, 13)),
            "gimmick_group_id": _text(one(stage, 2)),
            "npc_group_id": _text(one(stage, 4)),
            "limit_turn": int(one(section, 1, 0) or 0),
            "seed": int(one(player, 1, 0) or 0),
            "idol_card_id": _text(one(player, 11)),
            "exam_effect_type": int(one(player, 12, 0) or 0),
            "parameters": {1: int(one(player, 50, 0) or 0),
                           2: int(one(player, 51, 0) or 0),
                           3: int(one(player, 52, 0) or 0)},
            "score_bonus_permils": {1: int(one(player, 53, 0) or 0),
                                    2: int(one(player, 54, 0) or 0),
                                    3: int(one(player, 55, 0) or 0)},
            "max_stamina": int(one(player, 56, 0) or 0),
            "starting_stamina": int(one(player, 62, one(player, 56, 0)) or 0),
            "deck_cards": [_card(c) for c in each(player, 57)],
            "produce_item_ids": [_text(one(walk(i), 1)) for i in each(player, 58)],
            "produce_customize_item_ids": [_text(v) for v in each(player, 59)],
            "drink_ids": [_text(v) for v in each(player, DRINK_FIELD)],
            "support_cards": [{"support_card_id": _text(one(walk(v), 1)),
                               "permil": int(one(walk(v), 2, 0) or 0)}
                              for v in each(player, SUPPORT_FIELD)],
            "memory_ability_skill_ids": [_text(one(r, 3)) for r in memory_rows],
            "memory_ability_enchant_ids": [_text(one(r, 5)) for r in memory_rows],
            "customize_item_enchant_ids": [_text(one(r, 5)) for r in customize_rows],
            "actions": _actions(player),
        })
    return rounds


def _actions(player):
    out = []
    for raw in each(player, ACTION_FIELD):
        fields = walk(raw)
        kind = _ACTION.get(int(one(fields, 1, 0) or 0))
        if kind is None:
            raise ValueError(f"unknown ExamActionType {one(fields, 1)}")
        indexes = [b for value in each(fields, 2) for b in value]
        if kind == "end":
            out.append((kind,))
        elif kind == "select":
            out.append((kind, tuple(indexes)))
        else:
            out.append((kind, indexes[0] if indexes else 0))
    return out


def to_loadout(round_data):
    """The subset ``scenario.hif.build_hif_loadout_session`` consumes."""
    return {
        "schema": "gkms.hif_loadout.v1",
        "idol_card_id": round_data["idol_card_id"],
        "round": {16: "mid1", 17: "mid2"}.get(int(round_data["step_type"]), "final"),
        "produce_id": round_data.get("produce_id") or "produce-008",
        "step_type": int(round_data["step_type"]),
        "step_id": round_data.get("step_id"),
        "step_number": round_data.get("step_number"),
        "step_select_number": round_data.get("step_select_number"),
        "audition_number": round_data.get("audition_number"),
        "parameters": round_data["parameters"],
        "score_bonus_permils": round_data["score_bonus_permils"],
        "deck_cards": round_data["deck_cards"],
        "include_produce_default": False,
        "produce_item_ids": round_data["produce_item_ids"],
        "starting_stamina": round_data["starting_stamina"],
        "max_stamina": round_data["max_stamina"],
        "seed": round_data["seed"],
        "drink_ids": round_data["drink_ids"],
        "support_cards": round_data.get("support_cards") or [],
        "lesson_start_enchant_ids": round_data.get("memory_ability_enchant_ids") or [],
        "main_item_enchant_ids": round_data.get("customize_item_enchant_ids") or [],
        "research_card_ids": round_data.get("research_card_ids") or [],
    }


# the ProduceHistory captures native/survey.py read (the 74 player routes of the layout and search benches)
PLAIN_DIRS = ("TL214", "TL212", "TL213", "TL213b", "TL228", "TL229")


def load_plain_rounds(plain):
    """{(seed, step_type): round} of every ``ev*ProduceHistory.bin`` under ``plain``'s PLAIN_DIRS (actions as tuples)."""
    rows = {}
    for d in PLAIN_DIRS:
        for p in sorted(Path(plain, d).glob("ev*ProduceHistory.bin")):
            for r in extract(p.read_bytes()):
                r["actions"] = [tuple(a) if a[0] != "select" else ("select", tuple(a[1])) for a in r["actions"]]
                rows[(r["seed"], r["step_type"])] = r
    return rows


def build_plain_round(master, row):
    """The session of a load_plain_rounds round, its recorded picks queued."""
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    s, _meta = build_hif_loadout_session(master, to_loadout(row))
    s.state.select_index_queue = [list(a[1]) for a in row["actions"] if a[0] == "select"]
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("responses", nargs="+", help="plaintext Produce/History .bin files")
    ap.add_argument("--out-dir")
    args = ap.parse_args(argv)
    corpora = []
    for path in args.responses:
        for index, round_data in enumerate(extract(Path(path).read_bytes())):
            corpora.append({"source": Path(path).name, "index": index, **round_data})
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for item in corpora:
            name = f"{item['idol_card_id']}_{item['seed']}_{item['step_type']}"
            (out / f"{name}.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {len(corpora)} rounds to {out}")
    else:
        for item in corpora:
            print(json.dumps({k: v for k, v in item.items()
                              if k not in ("deck_cards", "actions")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
