# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idol Road (tower) formation rules: what a memory deck brings into a layer."""
from __future__ import annotations

import json
from pathlib import Path

from GakumasCLI.engine.tower import DANCE, VISUAL, VOCAL, _DECK_THEME

SUB_SHARE_PERMIL = 200
PARAMS = ((VOCAL, "vocal"), (DANCE, "dance"), (VISUAL, "visual"))
EFFECT_PREFIX = "PRODUCE_EXAM_EFFECT_TYPE_"


def idol_cards(*paths):
    """{idol card id: {"mechanic", "effect_type", "plan", "character"}} from IdolCard.jsonl files (a later file
    only adds ids the earlier ones lack -- the workbench's master overlay)."""
    out = {}
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            row = row.get("decoded", row)
            card_id = row.get("id")
            if not card_id or card_id in out:
                continue
            effect = row.get("examEffectType")
            name = effect.get("name") if isinstance(effect, dict) else effect
            number = effect.get("number") if isinstance(effect, dict) else None
            plan = row.get("planType")
            plan = plan.get("number") if isinstance(plan, dict) else plan
            if not isinstance(name, str) or not name.startswith(EFFECT_PREFIX):
                continue
            out[card_id] = {"mechanic": name[len(EFFECT_PREFIX):].lower(), "effect_type": number,
                            "plan": plan, "character": row.get("characterId") or card_id.split("-")[1]}
    return out


def tower_character(tower_id):
    """``tower_001-hrnm`` -> ``hrnm``."""
    return str(tower_id or "").rsplit("-", 1)[-1]


def layer_identity(get_layer, request=None):
    """(tower id, layer number) of a ``Tower/GetLayer`` response: from its request when there is one, else from an
    exam row's NPC group id (``p_npc_group-tower_001-hrnm-exam_review-stage_001``)."""
    request = request or {}
    if request.get("tower_id") and request.get("number") is not None:
        return str(request["tower_id"]), int(request["number"])
    for exam in (get_layer or {}).get("exams") or ():
        npc = str(exam.get("produce_exam_battle_npc_group_id") or "")
        parts = npc.split("-")
        if len(parts) >= 5 and parts[0] == "p_npc_group" and parts[-1].startswith("stage_"):
            return f"{parts[1]}-{parts[2]}", int(parts[-1][len("stage_"):])
    return None, None


def exam_row(get_layer, effect_type):
    rows = [e for e in (get_layer or {}).get("exams") or () if int(e.get("exam_effect_type") or 0) == int(effect_type)]
    if len(rows) != 1:
        raise ValueError(f"tower:exam_row - {len(rows)} exams[] rows for exam_effect_type {effect_type}")
    return rows[0]


def eligible(memories, tower_id):
    """The memories a formation of this tower may use: the tower idol's (see the module note)."""
    chara = tower_character(tower_id)
    return [m for m in memories or () if (m.get("character_id") or str(m.get("idol_card_id") or "-").split("-")[1])
            == chara and m.get("exam_battle_produce_cards")]


def plan_of(memory, idols):
    """the plan (IdolCard ``planType`` number) of a memory's idol card, or None when not known."""
    return (idols.get((memory or {}).get("idol_card_id")) or {}).get("plan")


def same_plan(main, sub, idols):
    """whether ``sub`` may go with ``main`` -- the same plan (a plan not known does not refuse)."""
    a, b = plan_of(main, idols), plan_of(sub, idols)
    return a is None or b is None or a == b


def _share(value):
    return int(value or 0) * SUB_SHARE_PERMIL // 1000


def _kept_copies(memories, no_duplicate):
    """of a ``noDeckDuplication`` card held by more than one memory, the copy the game keeps, as (memory slot, card
    index): the most upgraded, then the most customized (sum of ``customize_count``), then the first in slot order --
    at that copy's own place in the deck.
    """
    best = {}
    for number, memory in enumerate(memories):
        for index, card in enumerate(memory.get("exam_battle_produce_cards") or ()):
            if not no_duplicate(card["id"]):
                continue
            rank = (int(card.get("upgrade_count") or 0),
                    sum(int(c.get("customize_count") or 0) for c in card.get("customizes") or ()))
            if card["id"] not in best or rank > best[card["id"]][0]:
                best[card["id"]] = (rank, (number, index))
    return {slot for _rank, slot in best.values()}


def conversion_afters(*paths):
    """every ``afterProduceCardId`` of the ProduceCardConversion files (the reworked cards)."""
    out = set()
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            row = row.get("decoded", row)
            if row.get("afterProduceCardId"):
                out.add(row["afterProduceCardId"])
    return frozenset(out)


def enabled_conversions(rows):
    """the (before, after) pairs that are on, from either shape: User/Get ``user_produce_card_conversion_list`` rows
    (``before_produce_card_id`` / ``after_produce_card_id`` / ``enable``) or a ranking deck's ``conversions``
    (Tower/GetRankingDeck ``produce_card_conversions``: ``before`` / ``after``, the pairs that player has on).
    """
    out = set()
    for c in rows or ():
        if "before_produce_card_id" in c:
            before, after, on = c.get("before_produce_card_id"), c.get("after_produce_card_id"), bool(c.get("enable"))
        else:
            before, after, on = c.get("before"), c.get("after"), True
        if before and after and on:
            out.add((before, after))
    return out


def conversion_keep(conversions, afters):
    """``keep(card_id)`` -- what a memory's card brings into the battle under the player's card conversions (see
    ``compose``): an ``after`` card only when its conversion is on, a ``before`` card of a conversion that is on
    never, every other card as it is."""
    pairs = enabled_conversions(conversions)
    on_before, on_after = {b for b, _a in pairs}, {a for _b, a in pairs}
    return lambda card_id: card_id not in on_before and (card_id in on_after or card_id not in afters)


def compose(main, subs, get_layer, *, catalog, idols, no_duplicate, conversions=None, afters=frozenset()):
    """The ``Tower/Start`` message this formation gets, plus what it was built from."""
    idol = idols.get(main.get("idol_card_id"))
    if idol is None:
        raise KeyError(f"IdolCard {main.get('idol_card_id')!r}")
    row = exam_row(get_layer, idol["effect_type"])
    params = {k: int(main.get(name) or 0) + sum(_share(s.get(name)) for s in subs) for k, name in PARAMS}
    stamina = int(main.get("stamina") or 0) + sum(_share(s.get("stamina")) for s in subs)
    if conversions is not None:
        keep = conversion_keep(conversions, afters)
        main, *subs = [dict(m, exam_battle_produce_cards=[c for c in m.get("exam_battle_produce_cards") or ()
                                                          if keep(c["id"])]) for m in (main, *subs)]
    cards, kept = [], _kept_copies([main, *subs], no_duplicate)
    for number, memory in enumerate([main, *subs]):
        for index, card in enumerate(memory.get("exam_battle_produce_cards") or ()):
            if no_duplicate(card["id"]) and (number, index) not in kept:
                continue
            out = {"id": card["id"]}
            if int(card.get("upgrade_count") or 0):
                out["upgrade_count"] = int(card["upgrade_count"])
            if card.get("customizes"):
                out["customizes"] = [dict(c) for c in card["customizes"]]
            cards.append(out)
    theme = _DECK_THEME[idol["mechanic"]]
    base = catalog.initial_decks.get(f"initial_deck-{theme}")
    if base is None:
        raise ValueError(f"tower:initial_deck - no ExamInitialDeck initial_deck-{theme}")
    cards.extend({"id": card_id} for card_id in base)
    config = row["produce_exam_battle_config_id"]
    permils = catalog.score_bonus_permils(config, params)
    return {"idol_card_id": main["idol_card_id"], "max_stamina": stamina,
            "vocal_permil": permils[VOCAL], "dance_permil": permils[DANCE], "visual_permil": permils[VISUAL],
            "produce_cards": cards,
            "produce_item_ids": list(main.get("exam_battle_produce_item_ids") or ()) + list(row.get("produce_item_ids") or ()),
            "mechanic": idol["mechanic"], "parameters": params, "battle_config_id": config, "exam": row}


def start_fields(composed):
    """The keys a real ``Tower/Start`` carries (what ``compose`` is checked against)."""
    return {k: composed[k] for k in ("idol_card_id", "max_stamina", "vocal_permil", "dance_permil", "visual_permil",
                                     "produce_cards", "produce_item_ids")}
