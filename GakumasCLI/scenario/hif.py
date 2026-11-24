# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H.I.F 本戦 (``produce-008``) loadout entry over the same ExamSession engine."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from GakumasCLI.engine.card_data import greedy_select_policy
from GakumasCLI.engine.card_data import effective_audition_parameter_permil
from GakumasCLI.scenario.exam_session import ExamLoading, ProduceState, SegmentConfig, open_exam
from GakumasCLI.scenario.idol_road import plan_type_for_idol_card
from GakumasCLI.scenario import nia, hajime

PRODUCE_ID = "produce-008"
SELECTION_PRODUCE_ID = "produce-007"
PRODUCE_IDS = (SELECTION_PRODUCE_ID, PRODUCE_ID)
ROUNDS = {
    "mid1": ("AUDITION_MID1", False),
    "mid2": ("AUDITION_MID2", False),
    "final": ("AUDITION_FINAL", True),
    "r1": ("AUDITION_MID1", False),
    "r2": ("AUDITION_FINAL", True),
}
ROUND_LABELS = {
    ("produce-007", "AUDITION_MID1"): "选拔赛 1",
    ("produce-007", "AUDITION_MID2"): "选拔赛 2",
    ("produce-007", "AUDITION_FINAL"): "选拔赛 3",
    ("produce-008", "AUDITION_MID1"): "本战上半场",
    ("produce-008", "AUDITION_FINAL"): "本战下半场",
}


class HifDifficultyMissing(KeyError):
    """The master has no produce-008 audition row for this idol card."""

    def __init__(self, idol_card_id, difficulty_id, step, produce_id=PRODUCE_ID):
        super().__init__(idol_card_id, difficulty_id, step)
        self.idol_card_id, self.difficulty_id, self.step = idol_card_id, difficulty_id, step
        self.produce_id = produce_id

    def __str__(self):
        return (f"master 里没有这张偶像卡的 H.I.F 审查行：{self.idol_card_id}"
                f"（{self.difficulty_id}，{self.produce_id} {self.step}）")


@lru_cache(maxsize=8)
def _idol_difficulty_ids(master_dir):
    path = Path(master_dir) / "IdolCard.jsonl"
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        decoded = row.get("decoded") if isinstance(row, dict) else None
        if isinstance(decoded, dict) and decoded.get("id"):
            out[decoded["id"]] = decoded.get("produceStepAuditionDifficultyId")
    return out


def difficulty_id_for_idol_card(master_dir, idol_card_id):
    """an idol card names its audition difficulty row in ``IdolCard.produceStepAuditionDifficultyId``; the id is not
    always ``p_step_audition_difficulty-<idol card id>``.
    """
    found = _idol_difficulty_ids(str(master_dir)).get(str(idol_card_id))
    return found or f"p_step_audition_difficulty-{idol_card_id}"


def _fires_at_exam_start(master, enchant_id):
    enchant = (master.enchants or {}).get(enchant_id)
    if not enchant:
        return False
    trigger = (master.triggers or {}).get(enchant.get("produceExamTriggerId")) or {}
    return bool({row.get("number") for row in trigger.get("phaseTypes") or ()} & {6, 28})


def npc_count(master_dir, npc_group_id):
    """How many rivals the round has -- the RNG advance multiplier."""
    path = Path(master_dir) / "ProduceExamBattleNpcGroup.jsonl"
    try:
        stat = path.stat()
    except OSError:
        raise FileNotFoundError(f"ProduceExamBattleNpcGroup.jsonl not found: {path}") from None
    rivals = list(_npc_groups(str(path), stat.st_size, stat.st_mtime_ns).get(npc_group_id, ()))
    if not rivals:
        raise KeyError(f"npc group {npc_group_id!r}")
    return len(rivals), rivals


@lru_cache(maxsize=4)
def _npc_groups(path, _size, _mtime_ns):
    """The table's rows by group id, in file order."""
    groups = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        decoded = row.get("decoded") if isinstance(row, dict) else None
        decoded = decoded if isinstance(decoded, dict) else row
        groups.setdefault(decoded.get("id"), []).append(decoded)
    return {key: tuple(rows) for key, rows in groups.items()}


@lru_cache(maxsize=4)
def _support_cards(master_dir):
    """SupportCard rows keyed by id (the exam-side columns only)."""
    path = Path(master_dir) / "SupportCard.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"SupportCard.jsonl not found: {path}")
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        decoded = row.get("decoded") if isinstance(row, dict) and "decoded" in row else row
        if isinstance(decoded, dict) and decoded.get("id"):
            out[decoded["id"]] = decoded
    return out


def resolve_support_cards(master, declared):
    """the captured support deck -> ``eligible_supports``."""
    table = _support_cards(str(master.master_dir))
    supports, unknown = [], []
    for row in declared or ():
        support_id = row.get("support_card_id") or row.get("support_row_id")
        card = table.get(support_id)
        if card is None:
            unknown.append(support_id)
            continue
        parameter = card.get("produceCardUpgradeLessonParameterType") or {}
        search_id = card.get("upgradeProduceCardSearchId") or ""
        supports.append({
            "support_row_id": support_id,
            "permil": (int(row["permil"]) if row.get("permil") is not None
                       else int(card.get("produceCardUpgradePermil") or 0)),
            "permil_source": "declared" if row.get("permil") is not None else "master_base",
            "parameter_type": int(parameter.get("number") or 0),
            "card_search_position_type": int(
                ((master.searches.get(search_id) or {}).get("cardPositionType") or {})
                .get("number") or 0),
        })
    return supports, unknown


def rival_scores(rivals):
    """The fixed rival scores of a static-NPC round, as a beat-this table."""
    return [{"character_id": r.get("characterId"), "number": r.get("number"),
             "score_min": r.get("scoreMin"), "score_max": r.get("scoreMax")}
            for r in sorted(rivals, key=lambda r: r.get("number") or 0)]


# ------------------------------------------------- HIF memory abilities
_HIF_TRIGGER = "p_trigger-start_audition-for_hif_memory"


@lru_cache(maxsize=4)
def _memory_tables(master_dir):
    def rows(name):
        path = Path(master_dir) / f"{name}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"{name}.jsonl not found: {path}")
        out = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            decoded = row.get("decoded") if isinstance(row, dict) and "decoded" in row else row
            if isinstance(decoded, dict) and decoded.get("id"):
                out[decoded["id"]] = decoded
        return out
    return rows("MemoryAbility"), rows("ProduceSkill"), rows("ProduceEffect")


def resolve_hif_memory_abilities(master, ability_ids):
    """MemoryAbility -> ProduceSkill -> ProduceEffect -> the enc02 enchant id."""
    abilities, skills, effects = _memory_tables(str(master.master_dir))
    enchant_ids, records = [], []
    for ability_id in ability_ids:
        ability = abilities.get(ability_id)
        if ability is None:
            raise KeyError(f"MemoryAbility {ability_id!r}")
        skill = skills.get(ability.get("skillId"))
        if skill is None:
            raise KeyError(f"ProduceSkill {ability.get('skillId')!r}")
        if skill.get("produceTriggerId1") != _HIF_TRIGGER:
            raise ValueError(
                f"not a HIF audition-start ability: {ability_id!r} "
                f"(trigger {skill.get('produceTriggerId1')!r})")
        effect = effects.get(skill.get("produceEffectId1"))
        if effect is None:
            raise KeyError(f"ProduceEffect {skill.get('produceEffectId1')!r}")
        enc02 = effect.get("produceExamStatusEnchantId")
        if not enc02:
            raise ValueError(f"ability {ability_id!r} carries no status enchant")
        enc01_row = master.enchants.get(enc02)
        install = (enc01_row or {}).get("produceExamEffectIds") or ()
        enc01 = (master.effects.get(install[0], {}) if install else {}).get(
            "produceExamStatusEnchantId")
        payload = master.enchants.get(enc01, {}) if enc01 else {}
        count = int((master.effects.get(install[0], {}) if install else {})
                    .get("effectCount") or 0)
        enchant_ids.append(enc02)
        records.append({
            "memory_ability_id": ability_id,
            "enchant_start": enc02,
            "enchant_on_play": enc01,
            "uses": count,
            "target_search_id": (master.triggers.get(
                payload.get("produceExamTriggerId", ""), {}) or {}).get("produceCardSearchId"),
            "effect_ids": list(payload.get("produceExamEffectIds") or ()),
        })
    return enchant_ids, records


def apply_recorded_actions(session, actions):
    """Replay a recorded HIF action list onto a built session."""
    rows, seen_turn = [], None
    for action in actions:
        if isinstance(action, dict):
            raise ValueError("ExamActionType numbering unconfirmed; pass engine tuples")
        action = tuple(action)
        if session.finished:
            raise ValueError(f"recorded route outlives the segment at {action!r}")
        if action not in session.legal_actions():
            raise ValueError(f"recorded action illegal: {action!r} at turn {session.turn}")
        if session.turn != seen_turn:
            seen_turn = session.turn
            rows.append({"turn": session.turn,
                         "start_hand": [c["produce_card_id"] for c in session.deck.hand],
                         "start_hold": [c["produce_card_id"] for c in session.deck.hold],
                         "start_stamina": session.state.stamina,
                         "start_score": session.state.score,
                         "actions": []})
        rows[-1]["actions"].append(action)
        session.step(action)
        rows[-1].update(score=session.state.score, stamina=session.state.stamina,
                        block=session.state.block,
                        idol_status_type=session.state.idol_status_type)
    return {"finished": session.finished, "score": session.state.score,
            "turns": rows, "drinks_left": list(session.drinks)}


_STARTER_CATALOGS = {}


def _build_audition_loadout_session(master, document, seed=None):
    """Assemble one H.I.F 本戦 round from a declared loadout."""
    if document.get("schema") != "gkms.hif_loadout.v1":
        raise ValueError("unsupported hif loadout schema")
    if any(k in document for k in ("battle_record", "action_path", "future_hands")):
        raise ValueError("loadout contains route/record data")
    catalog = master.audition_catalog
    if catalog is None:
        raise ValueError("audition master catalog unavailable")

    idol_card_id = document["idol_card_id"]
    key = str(document.get("round", "final")).lower()
    if key not in ROUNDS:
        raise ValueError(f"round must be one of {sorted(ROUNDS)}")
    step_tail, final = ROUNDS[key]
    produce_id = str(document.get("produce_id") or PRODUCE_ID)
    nia_round = nia.is_nia(produce_id)
    hajime_round = hajime.is_hajime(produce_id)
    if not nia_round and not hajime_round:
        if produce_id not in PRODUCE_IDS:
            raise ValueError(f"produce_id must be one of {PRODUCE_IDS}")
        if step_tail == "AUDITION_MID2" and produce_id != SELECTION_PRODUCE_ID:
            raise ValueError("round mid2 exists only in the 選抜試験 (produce-007)")
    difficulty_id = document.get("difficulty_id") or difficulty_id_for_idol_card(master.master_dir, idol_card_id)
    if nia_round:
        difficulty = nia.difficulty_row(catalog, idol_card_id, difficulty_id, step_tail, produce_id,
                                        document.get("audition_number"))
    elif hajime_round:
        difficulty = hajime.difficulty_row(catalog, idol_card_id, difficulty_id, step_tail, produce_id,
                                          document.get("audition_number"))
    else:
        try:
            difficulty = catalog.difficulty(difficulty_id, step_tail, produce_id)
        except KeyError:
            raise HifDifficultyMissing(idol_card_id, difficulty_id, step_tail, produce_id) from None
    battle = catalog.battle_configs[difficulty["produceExamBattleConfigId"]]
    limit_turn = int(battle["turn"])

    parameters = {int(k): int(v) for k, v in (document.get("parameters") or {}).items()}
    if set(parameters) != {1, 2, 3}:
        raise ValueError("HIF parameters must contain Vo/Da/Vi keys 1,2,3")

    base_permils = catalog.base_permils(battle["id"], parameters)
    effect_bonus = {int(k): int(v) for k, v in (document.get("effect_bonus_permils") or {}).items()}
    star_permil = int(document.get("star_bonus_permil") or 0)
    vote_permil = int(document.get("vote_bonus_permil") or 0)
    star_unknown = bool(document.get("star")) and not document.get("star_bonus_permil")
    captured = {int(k): int(v)
                for k, v in (document.get("score_bonus_permils") or {}).items()}
    if captured:
        permils = {kind: captured[kind] for kind in (1, 2, 3)}
        star_unknown = False
    else:
        permils = {kind: effective_audition_parameter_permil(
                       base_permils[kind], effect_bonus.get(kind, 0),
                       vote_permil, star_permil)
                   for kind in (1, 2, 3)}

    rivals_n, rivals = npc_count(master.master_dir, difficulty["produceExamBattleNpcGroupId"])

    eligible_supports, unknown_supports = resolve_support_cards(
        master, document.get("support_cards"))

    plan_type = plan_type_for_idol_card(master.master_dir, idol_card_id)
    declared_plan = document.get("plan_type")
    plan_mismatch = (None if declared_plan is None or int(declared_plan) == plan_type
                     else {"declared": int(declared_plan), "master": plan_type})

    hif_memory_enchants, hif_memory_records = resolve_hif_memory_abilities(
        master, document.get("memory_ability_ids") or ())

    item_enchant_ids = list(document.get("main_item_enchant_ids") or ())
    exam_start_ids = [e for e in (document.get("lesson_start_enchant_ids") or ())
                      if _fires_at_exam_start(master, e)]
    exam_start_set = set(exam_start_ids)
    item_enchant_ids += exam_start_ids
    unknown_items = []
    for item_id in document.get("produce_item_ids") or ():
        item = master.items.get(item_id)
        if item is None:
            unknown_items.append(item_id)
            continue
        for effect_id in item.get("produceItemEffectIds", ()):
            enchant = master.item_effects[effect_id].get("produceExamStatusEnchantId")
            if enchant and enchant not in item_enchant_ids:
                item_enchant_ids.append(enchant)

    cards = []
    if document.get("include_produce_default"):
        from GakumasCLI.engine.tower import TowerCatalog
        from GakumasCLI.scenario.idol_road import DEFAULT_TOWER, mechanic_for_idol_card
        tower = document.get("tower") or DEFAULT_TOWER
        key = (str(master.master_dir), tower)
        theme_source = _STARTER_CATALOGS.get(key)
        if theme_source is None:
            _STARTER_CATALOGS.clear()
            theme_source = _STARTER_CATALOGS[key] = TowerCatalog.load(master.master_dir, tower)
        mechanic = mechanic_for_idol_card(master.master_dir, idol_card_id)
        for card_id in theme_source.produce_default_deck(mechanic):
            cards.append({"produce_card_id": card_id, "base_upgrade_count": 0})
    cards += [dict(card) for card in document["deck_cards"]]
    for number, card in enumerate(cards, 1):
        card["number"] = number

    if seed is None:
        seed = document.get("seed")
    if seed is None:
        raise ValueError("HIF loadout must provide seed")

    session_unresolved = []
    produce = ProduceState(
        stamina=int(document["starting_stamina"]),
        max_stamina=int(document["max_stamina"]),
        deck_cards=cards,
        item_enchant_ids=item_enchant_ids,
        memory_ability_ids=list(document.get("memory_ability_ids") or ()),
        plan_type=plan_type,
        research_card_ids=tuple(document.get("research_card_ids") or ()),
        item_enchant_limits=master.item_enchant_limits(document.get("produce_item_ids") or ()),
    )
    config = SegmentConfig(
        segment_id=f"{difficulty_id}:{step_tail}",
        segment_type="Audition Final" if final else ("Audition Mid2" if step_tail == "AUDITION_MID2" else "Audition Mid1"),
        limit_turn=limit_turn,
        score_cap=(int(difficulty.get("forceEndScore") or 0) or None) if hajime_round else None,
        parameter_type=None,
        seed=int(seed),
        gimmick_group_id=difficulty.get("produceExamGimmickEffectGroupId"),
        audition_permils=permils,
        audition_parameter_values={
            int(k): int(v)
            for k, v in (document.get("audition_parameter_values")
                         or catalog.criteria(battle["id"]) or parameters).items()
        },
        audition_npc_rng_advances=int(document.get("npc_rng_advances", rivals_n * limit_turn)),
        turn_parameter_types=document.get("turn_parameter_types"),
        lesson_start_enchant_ids=[e for e in list(document.get("lesson_start_enchant_ids") or ())
                                  + hif_memory_enchants if e not in exam_start_set],
        initial_card_id=document.get("initial_card_id"),
        initial_card_ids=document.get("initial_card_ids"),
        resolve_initial_cards=bool(document.get("resolve_initial_cards", True)),
        upgrade_tiers=set(master.cards),
        eligible_supports=eligible_supports,
        final_audition=final,
        skip_clears_playable_adds=2 if nia_round else int(bool(hajime_round)),
        drink_ids=tuple(document.get("drink_ids") or ()),
    )
    for item_id in unknown_items:
        session_unresolved.append(f"unknown_produce_item:{item_id}")
    for support_id in unknown_supports:
        session_unresolved.append(f"unknown_support_card:{support_id}")
    meta = {
        "produce_id": produce_id,
        "round_label": ROUND_LABELS.get((produce_id, step_tail), step_tail),
        "unknown_produce_item_ids": list(unknown_items),
        "difficulty_id": difficulty_id,
        "step_type": step_tail,
        "battle_config_id": battle["id"],
        "gimmick_group_id": config.gimmick_group_id,
        "npc_group_id": difficulty["produceExamBattleNpcGroupId"],
        "npc_count": rivals_n,
        "eligible_supports": eligible_supports,
        "unknown_support_card_ids": list(unknown_supports),
        "rival_scores": rival_scores(rivals),
        "limit_turn": limit_turn,
        "score_cap": config.score_cap,
        "final_audition": final,
        "rank_threshold": int(difficulty.get("rankThreshold") or 0) or None,
        "criteria": catalog.criteria(battle["id"]),
        "base_permils": base_permils,
        "effective_permils": permils,
        "star_score_bonus_base_line": difficulty.get("starScoreBonusBaseLine"),
        "star_bonus_permil": star_permil,
        "vote_bonus_permil": vote_permil,
        "plan_type": plan_type,
        "research_card_ids": list(produce.research_card_ids),
        "round1_display_multiplier": 1.2 if (produce_id == PRODUCE_ID and step_tail == "AUDITION_MID1") else None,
        "unverified": (["hif_star_bonus_unknown"] if star_unknown else [])
                      + (["plan_type_mismatch"] if plan_mismatch else [])
                      + [f"unknown_produce_item:{i}" for i in unknown_items]
                      + [f"unknown_support_card:{i}" for i in unknown_supports],
        "plan_type_mismatch": plan_mismatch,
        "hif_memory_abilities": hif_memory_records,
    }
    if nia_round:
        meta.update(nia.round_meta(step_tail, difficulty))
    elif hajime_round:
        meta.update(hajime.round_meta(step_tail, difficulty))
    session = ExamLoading(master, produce, config)
    session.state.unresolved_effects.extend(session_unresolved)
    session.deck.select_policy = greedy_select_policy(master.cards)
    session.deck.select_choices = []
    return open_exam(session, document, seed), meta


def build_hif_loadout_session(master, document, seed=None):
    """Backward-compatible entry used by live, node, replay and seed-search callers."""
    from GakumasCLI.scenario.exam_loadout import build_produce_exam_session
    return build_produce_exam_session(master, document, seed)
