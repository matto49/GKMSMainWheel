# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""N.I.A (produce-004 プロ / produce-005 マスター) -- the rules that are N.I.A's own."""
from __future__ import annotations

PRODUCE_IDS = ("produce-004", "produce-005")
MODE_LABEL = "N.I.A"

STEP_LABELS = {"AUDITION_MID1": "1次试镜", "AUDITION_MID2": "2次试镜", "AUDITION_FINAL": "最终试镜"}
# localization.json produce.nia.audition_name.<key>; key = auditionType tail, lower case, no "_"
AUDITION_NAMES = {"mid1easy": "Music Order", "mid1normal": "Harmony To Night", "mid1hard": "节奏Bang！",
                  "mid2easy": "MELODY Journey", "mid2normal": "POP Fair", "mid2hard": "GAL★AXY音乐",
                  "finaleasy": "Single Cut", "finalnormal": "IDOL Big Up！", "finalhard": "QUARTET",
                  "finalveryhard": "FINALE"}
AUDITION_TYPE_PREFIX = "PRODUCE_STEP_AUDITION_TYPE_"

# no cap
OPEN_SECONDS = {"final": 360, "*": 180}

DRINK_NOTE = "最终试镜全用"


def is_nia(produce_id):
    return str(produce_id or "") in PRODUCE_IDS


class NiaRoundMissing(KeyError):
    """The master has no N.I.A row for this idol card, step and pick."""

    def __init__(self, idol_card_id, difficulty_id, step, produce_id, number=None):
        super().__init__(idol_card_id, difficulty_id, step, produce_id, number)
        self.idol_card_id, self.difficulty_id, self.step = idol_card_id, difficulty_id, step
        self.produce_id, self.number = produce_id, number

    def __str__(self):
        number = f" 第{self.number}档" if self.number is not None else ""
        return (f"master 里没有这张偶像卡的 N.I.A 审查行：{self.idol_card_id}"
                f"（{self.difficulty_id}，{self.produce_id} {self.step}{number}）")


class AuditionNumberMissing(KeyError):
    """The step offers several auditions and the round does not say which one
    the player picked."""

    def __init__(self, produce_id, step, numbers):
        super().__init__(produce_id, step, tuple(numbers))
        self.produce_id, self.step, self.numbers = produce_id, step, tuple(numbers)

    def __str__(self):
        return (f"这场审查有 {len(self.numbers)} 档可选（{self.produce_id} {self.step}），"
                f"采集里没有读到选的是哪一档，不跟随")


# What the workbench catches for an N.I.A round: shown as a notice, the produce stays followed.
ROUND_ERRORS = (NiaRoundMissing, AuditionNumberMissing)


def difficulty_row(catalog, idol_card_id, difficulty_id, step_tail, produce_id, audition_number):
    """The master row of the audition the player picked."""
    numbers = catalog.numbers(difficulty_id, step_tail, produce_id)
    if not numbers:
        raise NiaRoundMissing(idol_card_id, difficulty_id, step_tail, produce_id)
    number = int(audition_number) if audition_number not in (None, 0, "") else None
    if number is None:
        if len(numbers) > 1:
            raise AuditionNumberMissing(produce_id, step_tail, numbers)
        number = numbers[0]
    try:
        return catalog.difficulty(difficulty_id, step_tail, produce_id, number)
    except KeyError:
        raise NiaRoundMissing(idol_card_id, difficulty_id, step_tail, produce_id, number) from None


def audition_key(difficulty):
    """``PRODUCE_STEP_AUDITION_TYPE_FINAL_VERY_HARD`` -> ``finalveryhard``."""
    kind = str(((difficulty or {}).get("auditionType") or {}).get("name") or "")
    return kind.removeprefix(AUDITION_TYPE_PREFIX).replace("_", "").lower()


def round_label(step_tail, difficulty):
    name = AUDITION_NAMES.get(audition_key(difficulty), "")
    base = STEP_LABELS.get(step_tail, step_tail)
    return f"{base}·{name}" if name else base


def round_meta(step_tail, difficulty):
    """The meta keys an N.I.A round adds to (or overrides in) the H.I.F builder's."""
    return {
        "mode_label": MODE_LABEL,
        "round_label": round_label(step_tail, difficulty),
        "audition_number": int(difficulty.get("number") or 1),
        "audition_type": (difficulty.get("auditionType") or {}).get("name"),
        "vote_count_required": difficulty.get("voteCount"),
        "static_npc_score": bool(difficulty.get("isStaticNpcScore")),
        "drinks_all": step_tail == "AUDITION_FINAL",
        "drink_note": DRINK_NOTE if step_tail == "AUDITION_FINAL" else None,
    }


def round_budget(document):
    """(open_seconds, score_cap) for an N.I.A loadout document or round meta."""
    rnd = str((document or {}).get("round") or "").lower()
    final = rnd in ("final", "r2") if rnd else bool((document or {}).get("final_audition"))
    return (OPEN_SECONDS["final"] if final else OPEN_SECONDS["*"]), None


def to_loadout(round_data):
    """The H.I.F loadout document plus the picked audition."""
    from GakumasCLI.scenario.hif_replay_import import to_loadout as base
    document = base(round_data)
    document["audition_number"] = round_data.get("audition_number")
    return document
