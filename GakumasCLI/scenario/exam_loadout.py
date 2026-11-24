# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compatible transport and shared entry for produce auditions and 初 lessons."""
from __future__ import annotations

from GakumasCLI.scenario import hajime, nia, tower


class ExamInputError(ValueError):
    """An unsupported or incomplete round must not kill live capture/following."""


def scenario_name(produce_id):
    if hajime.is_hajime(produce_id):
        return "hajime"
    if nia.is_nia(produce_id):
        return "nia"
    if tower.is_tower(produce_id):
        return "tower"
    if str(produce_id or "produce-008") in ("produce-007", "produce-008"):
        return "hif"
    return "unknown"


def normalize_loadout(document):
    if not isinstance(document, dict):
        raise ExamInputError("loadout 必须是 JSON 对象")
    doc = dict(document)
    if doc.get("schema") not in ("gkms.hif_loadout.v1", "gkms.exam_loadout.v1"):
        raise ExamInputError("不支持的考试 loadout 格式")
    if doc["schema"] == "gkms.exam_loadout.v1" and not doc.get("produce_id"):
        raise ExamInputError("通用考试 loadout 必须提供 produce_id")
    doc["produce_id"] = str(doc.get("produce_id") or "produce-008")
    if scenario_name(doc["produce_id"]) == "unknown":
        raise ExamInputError(f"尚未支持的培养模式：{doc['produce_id']}")
    expected = {16: "mid1", 17: "mid2", 18: "final"}
    if doc.get('round') is not None:
        key = str(doc['round']).lower()
        doc['round'] = {'r1':'mid1', 'r2':'final'}.get(key, key)
    lesson = hajime.is_hajime(doc["produce_id"]) and (
        doc.get("round") == "lesson"
        or (doc.get("step_type") is not None and str(doc["step_type"]).lstrip("-").isdigit()
            and int(doc["step_type"]) in hajime.LESSON_STEP_TYPES))
    if lesson:
        doc["round"] = "lesson"
        try:
            if int(doc.get("step_type") or 0) not in hajime.LESSON_STEP_TYPES or not doc.get("step_id"):
                raise ValueError("缺少课程步骤")
        except (TypeError, ValueError) as exc:
            raise ExamInputError(f"初课程输入不完整：{exc}") from exc
    elif doc.get("step_type") is not None:
        try:
            round_name = expected[int(doc["step_type"])]
        except (KeyError, ValueError, TypeError) as exc:
            raise ExamInputError("此步骤不是考试，课程不进入出牌求解") from exc
        actual = {"r1": "mid1", "r2": "final"}.get(str(doc.get("round", "")).lower(), doc.get("round"))
        if actual and actual != round_name:
            raise ExamInputError("step_type 与 round 不一致")
        doc["round"] = round_name
    if hajime.is_hajime(doc["produce_id"]) and doc.get("round", "final") not in ("mid1", "final", "r1", "r2", "lesson"):
        raise ExamInputError("初模式只有课程、中期和最终考试")
    if hajime.is_hajime(doc["produce_id"]):
        try:
            if not doc.get("deck_cards"):
                raise ValueError("缺少完整卡组")
            maximum, stamina = int(doc["max_stamina"]), int(doc["starting_stamina"])
            if maximum <= 0 or not 0 <= stamina <= maximum:
                raise ValueError("开局体力不合理")
            if any(int(v) < 0 for v in doc.get("parameters", {}).values()):
                raise ValueError("属性不能为负数")
        except (KeyError, TypeError, ValueError) as exc:
            raise ExamInputError(f"初考试输入不完整：{exc}") from exc
    if doc.get("seed") is not None:
        try:
            value = int(doc["seed"])
            if isinstance(doc["seed"], bool) or value != float(doc["seed"]) or not 0 <= value < 1 << 32:
                raise ValueError("种子须为 0 至 4294967295 的整数")
        except (ValueError, TypeError, OverflowError) as exc:
            raise ExamInputError("种子须为 0 至 4294967295 的整数") from exc
        doc["seed"] = value
    try:
        number = int(doc.get("audition_number") or 0)
        selected = int(doc.get("step_select_number") or 0)
    except (ValueError, TypeError) as exc:
        raise ExamInputError("考试选择编号必须为整数") from exc
    if number and selected and number != selected:
        raise ExamInputError("audition_number 与 step_select_number 不一致")
    if number or selected:
        doc["audition_number"] = number or selected
    doc["schema"] = "gkms.hif_loadout.v1"
    return doc


def build_produce_exam_session(master, document, seed=None):
    from GakumasCLI.scenario.hif import _build_audition_loadout_session, HifDifficultyMissing
    from GakumasCLI.scenario.plan_support import check_loadout
    doc = normalize_loadout(document)
    refused = check_loadout(master, doc)
    if refused:
        raise ExamInputError(refused)
    try:
        if tower.is_tower(doc["produce_id"]):
            return tower.build_session(master, doc, seed)
        if hajime.is_lesson_document(doc):
            return hajime.build_lesson_session(master, doc, seed)
        return _build_audition_loadout_session(master, doc, seed)
    except (nia.ROUND_ERRORS + (ExamInputError, HifDifficultyMissing)):
        raise
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ExamInputError(f"无法构建这场考试：{exc}") from exc


def round_errors():
    from GakumasCLI.scenario.hif import HifDifficultyMissing
    return nia.ROUND_ERRORS + (ExamInputError, HifDifficultyMissing)
