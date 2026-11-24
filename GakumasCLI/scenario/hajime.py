# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hajime adaptation: the mid / final auditions, and the lessons as their own battles."""
from __future__ import annotations

PRODUCE_IDS = ("produce-001", "produce-002", "produce-003", "produce-006")
MODE_LABEL = "初"
ROUND_LABELS = {"AUDITION_MID1": "初·中期考试", "AUDITION_FINAL": "初·最终考试"}

LESSON_STEP_TYPES = {1: (1, "NORMAL"), 2: (1, "SP"), 3: (1, "HARD"),
                     4: (2, "NORMAL"), 5: (2, "SP"), 6: (2, "HARD"),
                     7: (3, "NORMAL"), 8: (3, "SP"), 9: (3, "HARD"),
                     29: (1, "NORMAL"), 30: (1, "SP"), 31: (2, "NORMAL"),
                     32: (2, "SP"), 33: (3, "NORMAL"), 34: (3, "SP")}
PARAMETER_NAMES = {1: "Vocal", 2: "Dance", 3: "Visual"}
PARAMETER_LABELS = {1: "Vo", 2: "Da", 3: "Vi"}
LESSON_BUDGET_SECONDS = 20


def is_hajime(produce_id):
    return str(produce_id or "") in PRODUCE_IDS


class HajimeRoundMissing(ValueError):
    pass


def lesson_step(progress):
    """(step_type, step_id) of the lesson battle the progress is in, or None (not a lesson step)."""
    p = progress or {}
    step_type, step_id = int(p.get("step_type") or 0), str(p.get("step_id") or "")
    if step_type in LESSON_STEP_TYPES and step_id.startswith("p_step_lesson"):
        return step_type, step_id
    effect_type, effect_id = int(p.get("effect_step_type") or 0), str(p.get("effect_step_id") or "")
    if step_type == 12 and effect_type in LESSON_STEP_TYPES and effect_id.startswith("p_step_lesson"):
        return effect_type, effect_id
    return None


def is_lesson_document(document):
    return is_hajime((document or {}).get("produce_id")) and str((document or {}).get("round") or "") == "lesson"


def difficulty_row(catalog, idol_card_id, difficulty_id, step_tail, produce_id, number=None):
    if step_tail not in ROUND_LABELS:
        raise HajimeRoundMissing("初模式的考试只有中期和最终考试")
    try:
        return catalog.difficulty(difficulty_id, step_tail, produce_id, number)
    except KeyError as exc:
        raise HajimeRoundMissing(
            f"master 中没有对应的初考试：{idol_card_id} / {produce_id} / {step_tail}") from exc


def round_meta(step_tail, difficulty):
    cap = int(difficulty.get("forceEndScore") or 0)
    return {"scenario": "hajime", "mode_label": MODE_LABEL,
            "round_label": ROUND_LABELS[step_tail],
            "audition_number": int(difficulty.get("number") or 1),
            "score_cap": cap if cap > 0 else None,
            "clear_score": int(difficulty.get("baseScore") or 0),
            "static_npc_score": bool(difficulty.get("isStaticNpcScore")),
            "drink_note": "按本场选择，可保留饮料" if step_tail == "AUDITION_MID1" else "本场全用"}


def round_budget(document):
    if str(document.get("round") or "") == "lesson":
        return LESSON_BUDGET_SECONDS, document.get("score_cap")
    final = bool(document.get("final_audition")) or str(document.get("round", "")).lower() in ("final", "r2")
    return (60 if final else 30), document.get("score_cap")


def lesson_level(master, step_id):
    """(ProduceStepLessonLevel row, ProduceStepLesson name) of a lesson step id."""
    catalog = master.lesson_catalog
    if step_id not in catalog.steps:
        raise HajimeRoundMissing(f"master 中没有这节课程：{step_id}")
    return catalog.level_of(step_id), catalog.steps[step_id].get("name")


def build_lesson_session(master, document, seed=None):
    """One 初 lesson from a ``gkms.hif_loadout.v1`` document with ``round: "lesson"`` (hajime_follow.to_lesson_loadout).
    """
    from GakumasCLI.engine.card_data import greedy_select_policy
    from GakumasCLI.scenario.exam_session import ExamLoading, ProduceState, SegmentConfig, open_exam
    from GakumasCLI.scenario.hif import _fires_at_exam_start, resolve_support_cards
    from GakumasCLI.scenario.idol_road import plan_type_for_idol_card

    step_type = int(document["step_type"])
    if step_type not in LESSON_STEP_TYPES:
        raise HajimeRoundMissing(f"不是课程步骤：step_type {step_type}")
    parameter_type, kind = LESSON_STEP_TYPES[step_type]
    level, name = lesson_level(master, document["step_id"])
    if seed is None:
        seed = document.get("seed")
    if seed is None:
        raise ValueError("课程 loadout 缺少种子")
    limit_turn = int(level["limitTurn"]) + int(document.get("exam_extra_turn") or 0)
    score_cap = int(level["resultTargetValueLimit"]) + int(document.get("lesson_limit_up_score") or 0)
    starts = list(document.get("lesson_start_enchant_ids") or ())
    exam_start = [e for e in starts if _fires_at_exam_start(master, e)]
    item_enchant_ids = list(document.get("main_item_enchant_ids") or ()) + exam_start
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
    cards = [dict(card) for card in document["deck_cards"]]
    for number, card in enumerate(cards, 1):
        card["number"] = number
        if "current_upgrade_count" in card and "base_upgrade_count" not in card:
            card["base_upgrade_count"] = card.pop("current_upgrade_count")
    eligible, unknown_supports = resolve_support_cards(master, document.get("support_cards"))
    plan_type = plan_type_for_idol_card(master.master_dir, document["idol_card_id"])
    produce = ProduceState(stamina=int(document["starting_stamina"]), max_stamina=int(document["max_stamina"]),
                           deck_cards=cards, item_enchant_ids=item_enchant_ids, memory_ability_ids=[],
                           plan_type=plan_type, research_card_ids=tuple(document.get("research_card_ids") or ()),
                           item_enchant_limits=master.item_enchant_limits(document.get("produce_item_ids") or ()))
    exam_start_set = set(exam_start)
    config = SegmentConfig(segment_id=document["step_id"],
                           segment_type=f"Lesson {PARAMETER_NAMES[parameter_type]} {kind}",
                           limit_turn=limit_turn, score_cap=score_cap, parameter_type=parameter_type,
                           seed=int(seed), gimmick_group_id=document.get("gimmick_group_id"),
                           lesson_start_enchant_ids=[e for e in starts if e not in exam_start_set],
                           eligible_supports=eligible, upgrade_tiers=set(master.cards),
                           resolve_initial_cards=True, skip_clears_playable_adds=1,
                           drink_ids=tuple(document.get("drink_ids") or ()))
    session = ExamLoading(master, produce, config)
    if kind == "HARD" and level.get("successThreshold") is not None:
        session.state.oikomi_threshold = int(level["successThreshold"])
    session.state.unresolved_effects.extend([f"unknown_produce_item:{i}" for i in unknown_items]
                                            + [f"unknown_support_card:{i}" for i in unknown_supports])
    session.deck.select_policy = greedy_select_policy(master.cards)
    session.deck.select_choices = []
    label = name or "课程"
    meta = {"scenario": "hajime", "mode_label": MODE_LABEL, "lesson": True,
            "round_label": f"初·{PARAMETER_LABELS[parameter_type]} {label}",
            "produce_id": document.get("produce_id"), "step_type": step_type, "step_id": document["step_id"],
            "lesson_kind": kind, "parameter_type": parameter_type, "limit_turn": limit_turn,
            "score_cap": score_cap, "clear_score": int(level.get("successThreshold") or 0),
            "final_audition": False, "gimmick_group_id": config.gimmick_group_id,
            "eligible_supports": eligible, "unknown_produce_item_ids": unknown_items,
            "unknown_support_card_ids": list(unknown_supports), "rival_scores": [], "plan_type": plan_type,
            "research_card_ids": list(produce.research_card_ids),
            "drink_note": "课程默认不用饮料（可在饮料栏改成全喝）",
            "unverified": [f"unknown_produce_item:{i}" for i in unknown_items]
                          + [f"unknown_support_card:{i}" for i in unknown_supports]}
    return open_exam(session, document, seed), meta
