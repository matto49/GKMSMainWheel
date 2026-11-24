# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Moved here from engine/effects.py, deck.py and exam.py, which keep importing them from here until they go.
"""
import functools
import json
import math
import struct

from .rng import ExamRandom

_F32 = struct.Struct("<f")


def load_master_rows(path):
    rows = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["decode_status"] == "decoded":
                rows[row["decoded"]["id"]] = row["decoded"]
    return rows


_GROW_TO_EFFECT_TYPE = {50: 19, 51: 125, 52: 124, 5: 3, 22: 42, 7: 49, 18: 10, 20: 31}
_GROW_TO_EFFECT_TURN = {28: 5, 16: 2, 26: 129}
_GROW_TO_STATUS_COST = {
    40: ("EXAM_COST_TYPE_EXAM_LESSON_BUFF", -1),
    41: ("EXAM_COST_TYPE_EXAM_LESSON_BUFF", 1),
    42: ("EXAM_COST_TYPE_EXAM_REVIEW", -1),
    43: ("EXAM_COST_TYPE_EXAM_REVIEW", 1),
    44: ("EXAM_COST_TYPE_EXAM_CARD_PLAY_AGGRESSIVE", -1),
    45: ("EXAM_COST_TYPE_EXAM_CARD_PLAY_AGGRESSIVE", 1),
    46: ("EXAM_COST_TYPE_EXAM_PARAMETER_BUFF", -1),
    47: ("EXAM_COST_TYPE_EXAM_PARAMETER_BUFF", 1),
    53: ("EXAM_COST_TYPE_EXAM_PARAMETER_BUFF_MULTIPLE_PER_TURN", 1),
    54: ("EXAM_COST_TYPE_EXAM_PARAMETER_BUFF_MULTIPLE_PER_TURN", -1),
}


def resolve_card_customizes(card, resolved_card, card_customizes, grow_effects):
    resolved = dict(resolved_card)
    resolved["playEffects"] = list(resolved.get("playEffects", ()))
    lesson_add = 0
    cost_reduce = 0
    cost_full_power_point_add = 0
    cost_add = 0
    lesson_count_add = 0
    value_adds = {}
    turn_adds = {}
    status_cost_deltas = {}
    cost_full_power_point_reduce = 0
    cost_penetrate_reduce = 0
    cost_penetrate_add = 0
    initial_add = False
    grow_effect_ids = []
    for customize in card.get("customizes") or ():
        count = (
            customize["customize_count"]
            if "customize_count" in customize
            else customize["count"]
        )
        customize_row = card_customizes[(customize["id"], int(count))]
        grow_effect_ids.extend(customize_row["produceCardGrowEffectIds"])
    grow_effect_ids.extend(card.get("dynamic_grow_effect_ids") or ())
    for grow_effect_id in grow_effect_ids:
        if True:
            grow_effect = grow_effects[grow_effect_id]
            grow_type = _number(grow_effect["effectType"])
            if grow_type == 1:
                lesson_add += int(grow_effect["value"])
            elif grow_type == 12:
                cost_reduce += int(grow_effect["value"])
            elif grow_type == 49:
                cost_full_power_point_add += int(grow_effect["value"])
            elif grow_type == 3:
                lesson_count_add += int(grow_effect["value"])
            elif grow_type == 13:
                cost_add += int(grow_effect["value"])
            elif grow_type == 32:
                resolved["playEffects"].append(
                    {"produceExamEffectId": grow_effect["playProduceExamEffectId"]}
                )
                groups = list(resolved.get("effectGroupIds") or ())
                for group in grow_effect.get("effectGroupIds") or ():
                    if group not in groups:
                        groups.append(group)
                resolved["effectGroupIds"] = groups
            elif grow_type in _GROW_TO_EFFECT_TYPE:
                target = _GROW_TO_EFFECT_TYPE[grow_type]
                value_adds[target] = value_adds.get(target, 0) + int(grow_effect["value"])
            elif grow_type in _GROW_TO_EFFECT_TURN:
                target = _GROW_TO_EFFECT_TURN[grow_type]
                turn_adds[target] = turn_adds.get(target, 0) + int(grow_effect["value"])
            elif grow_type == 48:
                cost_full_power_point_reduce += int(grow_effect["value"])
            elif grow_type in _GROW_TO_STATUS_COST:
                cost_name, sign = _GROW_TO_STATUS_COST[grow_type]
                status_cost_deltas[cost_name] = (
                    status_cost_deltas.get(cost_name, 0)
                    + sign * int(grow_effect["value"])
                )
            elif grow_type == 14:
                cost_penetrate_reduce += int(grow_effect["value"])
            elif grow_type == 15:
                cost_penetrate_add += int(grow_effect["value"])
            elif grow_type == 39:
                initial_add = True
            elif grow_type == 34:
                targets = set(grow_effect.get("targetPlayProduceExamEffectIds") or ())
                replacement = grow_effect["playProduceExamEffectId"]
                resolved["playEffects"] = [
                    {"produceExamEffectId": replacement}
                    if link.get("produceExamEffectId") in targets
                    else link
                    for link in resolved["playEffects"]
                ]
                groups = list(resolved.get("effectGroupIds") or ())
                for group in grow_effect.get("effectGroupIds") or ():
                    if group not in groups:
                        groups.append(group)
                resolved["effectGroupIds"] = groups
            elif grow_type == 38:
                resolved["playMovePositionType"] = grow_effect["playMovePositionType"]
            elif grow_type == 35:
                resolved["produceCardStatusEnchantId"] = grow_effect[
                    "produceCardStatusEnchantId"
                ]
            elif grow_type == 36:
                targets = set(
                    grow_effect.get("targetPlayEffectProduceExamTriggerIds") or ()
                )
                if resolved.get("playProduceExamTriggerId") in targets:
                    resolved["playProduceExamTriggerId"] = grow_effect[
                        "playProduceExamTriggerId"
                    ]
            elif grow_type == 37:
                targets = set(
                    grow_effect.get("targetPlayEffectProduceExamTriggerIds") or ()
                )
                replacement = grow_effect.get("playEffectProduceExamTriggerId")
                resolved["playEffects"] = [
                    dict(link, produceExamTriggerId=replacement)
                    if link.get("produceExamTriggerId") in targets
                    else link
                    for link in resolved["playEffects"]
                ]
    resolved["customize_value_adds"] = value_adds
    resolved["customize_turn_adds"] = turn_adds
    resolved["customize_cost_full_power_point_reduce"] = cost_full_power_point_reduce
    resolved["customize_cost_penetrate_reduce"] = cost_penetrate_reduce
    resolved["customize_cost_penetrate_add"] = cost_penetrate_add
    resolved["customize_status_cost_deltas"] = status_cost_deltas
    if initial_add:
        resolved["isInitial"] = True
    resolved["customize_lesson_add"] = lesson_add
    resolved["customize_cost_reduce"] = cost_reduce
    resolved["customize_cost_full_power_point_add"] = cost_full_power_point_add
    resolved["customize_cost_add"] = cost_add
    resolved["customize_lesson_count_add"] = lesson_count_add
    return resolved


def _number(value):
    if isinstance(value, dict):
        return value["number"]
    return value


_TIERS_CACHE = {}


def _top_tier(card_master, card_id, count):
    """Highest master upgrade tier of ``card_id`` that is <= ``count``."""
    key = id(card_master)
    tiers = _TIERS_CACHE.get(key)
    if tiers is None:
        tiers = {}
        for (cid, upgrade) in card_master:
            tiers.setdefault(cid, []).append(int(upgrade))
        _TIERS_CACHE.clear()
        _TIERS_CACHE[key] = tiers
    return max(u for u in tiers[card_id] if u <= count)

class UnresolvedRandomness(RuntimeError):
    """Raised when a public observer requests hidden future randomness."""


class RealRandomnessProvider(ExamRandom):
    """The historical seed-backed provider used by replay and parity."""


class PublicRandomnessProvider:
    """Refusing provider for player-facing/public-only sessions."""

    def __init__(self, reason="future deck order is not publicly revealed"):
        self.reason = reason
        self.state = None

    def range(self, minimum, maximum):
        raise UnresolvedRandomness(self.reason)

    def next_u32(self):
        raise UnresolvedRandomness(self.reason)


def greedy_select_policy(card_master=None):
    """Stand-in for the player at a picker, for runs with no recorded pick."""
    def value_of(row):
        card = row.get("card") or {}
        resolved = row.get("resolved_card") or card.get("resolved_card") or {}
        total = 0
        for entry in (resolved.get("playEffects") or ()):
            if isinstance(entry, dict):
                try:
                    total += int(entry.get("effectValue1") or 0)
                except (TypeError, ValueError):
                    pass
        number = card.get("number")
        order = -number if isinstance(number, int) else 0
        return (total, int(resolved.get("current_upgrade_count") or card.get("current_upgrade_count") or 0),
                order, str(number))

    def policy(candidates, effect, search_id, count):
        ordered = sorted(candidates, key=value_of, reverse=True)
        return ordered[:count]

    policy.rule_name = "greedy_select_policy"
    return policy


@functools.lru_cache(maxsize=8192)
def _f32(value):
    return _F32.unpack(_F32.pack(value))[0]


def _f32_mul(left, right):
    return _f32(_f32(left) * _f32(right))


def _f32_div(left, right):
    return _f32(_f32(left) / _f32(right))


def ceil_ratio(value, permil):
    return (value * permil + 999) // 1000


def effective_audition_parameter_permil(
    base_permil, effect_bonus_permil, vote_bonus_permil=0, star_bonus_permil=0
):
    vote_bonus_permil = int(vote_bonus_permil) + int(star_bonus_permil)
    x = _f32_mul(_f32(1.0 + _f32_div(float(effect_bonus_permil), 1000.0)), float(base_permil))
    x = _f32_mul(x, _f32(1.0 + _f32_div(float(vote_bonus_permil), 1000.0)))
    return int(math.ceil(_f32_div(x, 10.0))) * 10
