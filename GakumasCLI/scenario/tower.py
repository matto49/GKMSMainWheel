# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idol Road (tower) as a produce-exam scenario -- the same entry as N.I.A (scenario/nia.py) and 初 (scenario/hajime.py).
"""
from __future__ import annotations

from pathlib import Path

PRODUCE_ID = "tower"
MODE_LABEL = "偶像之路"
OPEN_ROUNDS = 7
ROUND_SECONDS = 25.0
PICK_VARIANTS = 7
JOB_TAIL_SECONDS = 5.0
OPEN_TAIL_SECONDS = 20.0
EFFECT_PREFIX = "PRODUCE_EXAM_EFFECT_TYPE_"
_CATALOGS = {}


def is_tower(produce_id):
    return str(produce_id or "") == PRODUCE_ID


def _catalog(master_dir):
    from GakumasCLI.engine.tower import TowerCatalog
    key = str(master_dir)
    if key not in _CATALOGS:
        _CATALOGS[key] = TowerCatalog.load(Path(master_dir))
    return _CATALOGS[key]


def _idol_card(master_dir, idol_card_id):
    from GakumasCLI.scenario.tower_formation import idol_cards
    key = ("idols", str(master_dir))
    if key not in _CATALOGS:
        _CATALOGS[key] = idol_cards(Path(master_dir) / "IdolCard.jsonl")
    row = _CATALOGS[key].get(idol_card_id)
    if row is None:
        raise KeyError(f"IdolCard {idol_card_id!r}")
    return row


def to_loadout(get_layer, start, *, master_dir, tower_id=None, layer=None, rounds=None):
    """The loadout document of a tower battle: ``get_layer`` / ``start`` are the decoded responses (the
    Start's ``seed`` may be a string, as the decoder writes int64)."""
    from GakumasCLI.scenario.tower_formation import exam_row, layer_identity
    if tower_id is None or layer is None:
        tower_id, layer = layer_identity(get_layer)
    if tower_id is None:
        raise ValueError("tower:layer - the Tower/GetLayer response names no tower and layer")
    idol = _idol_card(master_dir, start["idol_card_id"])
    exam = exam_row(get_layer, idol["effect_type"])
    return {
        "schema": "gkms.exam_loadout.v1", "produce_id": PRODUCE_ID, "round": "final",
        "tower_id": str(tower_id), "layer": int(layer), "mechanic": idol["mechanic"],
        "idol_card_id": start["idol_card_id"], "seed": int(start["seed"]),
        "max_stamina": int(start["max_stamina"]), "starting_stamina": int(start["max_stamina"]),
        "score_bonus_permils": {"1": int(start["vocal_permil"]), "2": int(start["dance_permil"]),
                                "3": int(start["visual_permil"])},
        "produce_cards": [dict(c) for c in start.get("produce_cards") or ()],
        "produce_item_ids": list(start.get("produce_item_ids") or ()),
        "exam": {k: exam.get(k) for k in ("exam_effect_type", "produce_exam_gimmick_effect_group_id",
                                          "produce_exam_battle_config_id", "produce_exam_battle_npc_group_id",
                                          "produce_item_ids")},
        "open_rounds": int(rounds or OPEN_ROUNDS), "drink_ids": [],
        "pick_variants": PICK_VARIANTS,
    }


def card_inputs(cards):
    """Tower/Start#105 in wire order, numbered from 1 (battle_live_controller.card_inputs)."""
    return [dict(number=i, produce_card_id=c["id"], base_upgrade_count=int(c.get("upgrade_count", 0)),
                 current_upgrade_count=int(c.get("upgrade_count", 0)), customizes=list(c.get("customizes") or []),
                 origin_type=0, guid="live-%d" % i)
            for i, c in enumerate(cards, start=1)]


def build_session(master, document, seed=None):
    """(session, meta) for a tower loadout -- the battle the workbench's Tower feed opens
    (battle_live_engine.LiveBattle.from_tower_start)."""
    from GakumasCLI.scenario.idol_road import IdolRoadEntry, IdolRoadStage, build_loading, resolve_stage
    from GakumasCLI.scenario.exam_session import open_exam
    from GakumasCLI.engine.card_data import greedy_select_policy
    catalog = _catalog(master.master_dir)
    exam = document["exam"]
    tower_id, layer, mechanic = document["tower_id"], int(document["layer"]), document["mechanic"]
    catalog.capture_stage_exams[(tower_id, layer)] = [{
        "examEffectType": EFFECT_PREFIX + mechanic.upper(),
        "produceExamGimmickEffectGroupId": exam["produce_exam_gimmick_effect_group_id"],
        "produceExamBattleConfigId": exam["produce_exam_battle_config_id"],
        "produceExamBattleNpcGroupId": exam["produce_exam_battle_npc_group_id"],
        "produceItemIds": list(exam.get("produce_item_ids") or [])}]
    cards = card_inputs(document["produce_cards"])
    first = cards[0]
    plan_type = master.cards[(first["produce_card_id"], first["current_upgrade_count"])]["planType"]["number"]
    stage = IdolRoadStage(mechanic, layer, tower=tower_id)
    entry = IdolRoadEntry(
        seed=int(document["seed"] if seed is None else seed), parameters={}, deck_cards=cards,
        starting_stamina=int(document["max_stamina"] if document.get("starting_stamina") is None
                             else document["starting_stamina"]),
        max_stamina=int(document["max_stamina"]), plan_type=plan_type, idol_card_id=document["idol_card_id"],
        produce_item_ids=list(document["produce_item_ids"]),
        score_bonus_permils={int(k): int(v) for k, v in document["score_bonus_permils"].items()},
        include_produce_default=False)
    loading = build_loading(master, catalog, stage, entry)
    loading.deck.select_policy = greedy_select_policy(master.cards)
    loading.deck.select_choices = []
    session = open_exam(loading, document, seed)
    return session, round_meta(document, catalog, resolve_stage(catalog, stage), session)


def opponents(catalog, tower_id, mechanic, layer):
    """The NPC rows (battle_engine.Engine.opponents): score interval per NPC, best first."""
    rows = catalog.npc_group(str(tower_id).split("-")[-1], mechanic, int(layer))
    out = [{"number": r["number"], "mob": r["produceExamBattleNpcMobId"], "score_min": r["scoreMin"],
            "score_max": r["scoreMax"]} for r in rows]
    out.sort(key=lambda r: r["score_max"], reverse=True)
    return out


DEBUFF_COUNT_FIELD = 58


def debuff_gate(catalog, group_id):
    """``{"debuffs": N, "turn": T, "effect": id}`` when the battle's gimmicks reward holding N 低下状態 at the start of turn
    T, else None.
    """
    best = None
    for e in catalog.gimmick_groups.get(group_id, ()):
        if (e.field_status_number == DEBUFF_COUNT_FIELD and not e.check_type and e.is_positive and int(e.value) > 0
                and "debuff_recover" not in str(e.effect_id)):
            if best is None or int(e.start_turn) < best["turn"]:
                best = {"debuffs": int(e.value), "turn": int(e.start_turn), "effect": e.effect_id}
    return best


def round_meta(document, catalog, resolved, session):
    rivals = opponents(catalog, document["tower_id"], document["mechanic"], document["layer"])
    rounds = int(document.get("open_rounds") or OPEN_ROUNDS)
    return {"scenario": "tower", "mode_label": MODE_LABEL, "round_label": f"第{int(document['layer'])}层",
            "produce_id": PRODUCE_ID, "tower_id": document["tower_id"], "layer": int(document["layer"]),
            "mechanic": document["mechanic"], "idol_card_id": document["idol_card_id"],
            "limit_turn": int(resolved.limit_turn), "gimmick_group_id": resolved.gimmick_group_id,
            "npc_group_id": resolved.npc_group_id, "rival_scores": rivals,
            "top_score_interval": [rivals[0]["score_min"], rivals[0]["score_max"]] if rivals else None,
            "final_audition": True, "drinks_all": True, "drink_note": None,
            "open_rounds": rounds, "er_job_seconds": ROUND_SECONDS - JOB_TAIL_SECONDS, "open_runs": 1,
            "debuff_gate": debuff_gate(catalog, resolved.gimmick_group_id)}


def round_budget(document):
    """(open_seconds, score_cap): ``open_rounds`` rounds of ROUND_SECONDS, the open tail included."""
    rounds = int((document or {}).get("open_rounds") or OPEN_ROUNDS)
    return rounds * ROUND_SECONDS - OPEN_TAIL_SECONDS, None


STAR_SEARCHES = {"p_card_search-ssr-lost": "lost_ssr"}


def star_triggers(layer, list_layers=None, master=None):
    """The trigger ids rank 3 of ``layer`` needs: from the ListLayers response when it has the layer, else the master's
    「…for_tower_stage<layer>」 triggers."""
    for row in (list_layers or {}).get("layers") or ():
        if int(row.get("number") or 0) == int(layer):
            for rank in row.get("ranks") or ():
                if int(rank.get("rank") or 0) == 3:
                    return [str(t["produce_exam_trigger_id"]) for t in rank.get("exam_trigger_counts") or ()
                            if int(t.get("count") or 1) >= 1]
            return []
    suffix = f"-for_tower_stage{int(layer)}"
    return sorted(t for t in (getattr(master, "triggers", None) or {}) if str(t).endswith(suffix))


def star_rules(master, layer, list_layers=None):
    """(rules, text) -- the three-star condition of ``layer`` as hard milestones of its last turn (timeline rule
    dicts), and the condition's text; (None, None) when the layer has none or one this cannot translate."""
    from GakumasCLI.scenario import timeline as TL
    ids = star_triggers(layer, list_layers, master)
    if not ids:
        return None, None
    rules, texts = [], []
    for tid in ids:
        trig = (getattr(master, "triggers", None) or {}).get(tid)
        if not trig or [p.get("number") for p in trig.get("phaseTypes") or ()] != [5]:
            return None, None
        types = [t.get("number") for t in trig.get("fieldStatusTypes") or ()]
        values = list(trig.get("fieldStatusValues") or ())
        checks = trig.get("fieldStatusCheckTypes") or ()
        searches = list(trig.get("fieldStatusProduceCardSearchIds") or ())
        if 41 not in types or int(values[types.index(41)]) != 1:
            return None, None
        for k, ftype in enumerate(types):
            if ftype == 41:
                continue
            negate = k < len(checks) and int((checks[k] or {}).get("number", 0)) == 1
            value = int(values[k])
            if ftype == 51 and searches and searches[0] in STAR_SEARCHES:
                field = STAR_SEARCHES[searches.pop(0)]
            elif ftype == 58:
                field = "debuffs"
            else:
                return None, None
            op, bound = ("<=", value - 1) if negate else (">=", value)
            rules.append({"mode": "ms_hard", "target": "none", "abs": 0, "left": 1 << 1, "field": field,
                          "op": op, "value": bound, "penalty": TL.HARD_BASE})
        texts.append("".join(d.get("text") or "" for d in trig.get("produceDescriptions") or ()) or tid)
    return rules, "；".join(texts)


STAR_SHAPING_WEIGHT = 100000


def star_shaping(rules):
    """The soft per-turn companions of ``rules``' 「<=」 count conditions (timeline rule dicts)."""
    from GakumasCLI.scenario import timeline as TL
    return [{"mode": "ms_soft", "target": "none", "abs": TL.ALL_TURNS, "left": 0, "field": r["field"], "op": "<=",
             "value": int(r["value"]), "weight": float(STAR_SHAPING_WEIGHT), "keys": []}
            for r in rules or () if r.get("mode") == "ms_hard" and r.get("op") == "<=" and r.get("field") in TL.COUNT_FIELDS]


def star_first(catalog, tower_id, layer):
    """The clear score that beats every NPC of ``layer`` (any plan): the best scoreMax + 1; None without NPC rows."""
    idol = str(tower_id).split("-")[-1]
    tops = [int(row["scoreMax"]) for (who, _plan, stage), rows in (getattr(catalog, "npc_groups", None) or {}).items()
            if who == idol and int(stage) == int(layer) for row in rows]
    return max(tops) + 1 if tops else None


def first_rule(score):
    """first place as a hard milestone of the last turn (timeline rule dict)."""
    from GakumasCLI.scenario import timeline as TL
    return {"mode": "ms_hard", "target": "none", "abs": 0, "left": 1 << 1, "field": "score", "op": ">=",
            "value": int(score), "penalty": TL.HARD_BASE}


def star_plan(rules, text, layer):
    """A RoundPlan (scenario.timeline) holding only the three-star rules -- LiveRound.apply_timeline takes it."""
    from GakumasCLI.scenario import timeline as TL
    return {"format": TL.PLAN_FORMAT, "stage": "tower", "rules": list(rules), "budget": {}, "keep": [],
            "drink_table": None, "dropped": [], "hash": TL.digest(rules)[:16],
            "timeline": {"id": f"tower-star3-L{int(layer)}", "name": f"三星条件：{text}"}}
