# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Corpus-backed Produce input providers."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from GakumasCLI.engine.produce_run import ProduceIdentity, ProduceRun, ProduceStep
from GakumasCLI.engine.server_draw import ServerDraw


R21_MEMORY_ABILITY = (
    "memory_ability-p_memory_skill-plan3-p_trigger-start_lesson-lesson_vocal-"
    "exam_status_enchant-exam_turn_timer-exam_add_grow_effect-02-001-001"
)
R21_PRIDE_CUSTOMIZE = (
    "p_card_custom-040_070-g_effect-effect_add-e_effect-exam_add_grow_effect-"
    "p_card_search-deck_all-all-0_0-g_effect-lesson_add-1-g_effect-effect_add-"
    "e_effect-exam_add_grow_effect-p_card_search-deck_all-all-0_0-g_effect-"
    "lesson_add-4"
)
R21_BLOCK_CUSTOMIZE = "p_card_custom-070-g_effect-block_add-9"


R21_BASE_DECK = (
    {"produce_card_id": "p_card-03-ido-3_077", "base_upgrade_count": 1, "origin_type": 2},
    {"produce_card_id": "p_card-00-act-0_001", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-00-act-0_001", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-00-men-0_003", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-00-men-0_003", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-03-men-0_015", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-03-men-0_013", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-03-men-0_013", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-03-act-0_021", "base_upgrade_count": 0, "origin_type": 1},
    {"produce_card_id": "p_card-03-act-2_063", "base_upgrade_count": 0, "origin_type": 0},
)

R21_SUPPORTS = (
    {"number": 1, "support_card_id": "s_card-3-0025", "level": 45},
    {"number": 2, "support_card_id": "s_card-3-0001", "level": 40},
    {"number": 3, "support_card_id": "s_card-3-0007", "level": 40},
    {"number": 4, "support_card_id": "s_card-3-0019", "level": 50},
    {"number": 5, "support_card_id": "s_card-3-0023", "level": 40},
    {"number": 6, "support_card_id": "s_card-3-0069", "level": 60, "is_rental": True},
)

R21_STEP_BLOCKS = (
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-normal-01",
        "step_type": "Lesson Vocal NORMAL",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VOCAL_NORMAL",
        "seed": 1221133133,
        "stamina": 43,
        "vocal": 182,
        "dance": 125,
        "visual": 269,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_normal-before_mid",
        "support_permil": {"s_card-3-0001": 68, "s_card-3-0023": 68},
    },
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-sp-01",
        "step_type": "Lesson Visual SP",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VISUAL_SP",
        "seed": 650592160,
        "stamina": 27,
        "vocal": 301,
        "dance": 165,
        "visual": 275,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_sp-before_mid",
        "support_permil": {"s_card-3-0025": 76, "s_card-3-0007": 73},
    },
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-hard-01",
        "step_type": "Lesson Vocal HARD",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VOCAL_HARD",
        "seed": 1287411125,
        "stamina": 32,
        "vocal": 301,
        "dance": 176,
        "visual": 410,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_hard-before_mid",
        "support_permil": {"s_card-3-0001": 74, "s_card-3-0023": 74},
    },
    {
        "step_id": "AuditionMid1",
        "step_type": "Audition Mid1",
        "wire_step_type": "PRODUCE_STEP_TYPE_AUDITION_MID1",
        "seed": 892421402,
        "stamina": 43,
        "vocal": 552,
        "dance": 302,
        "visual": 526,
        "bonus": 500,
        "gimmick": None,
        "support_permil": {
            "s_card-3-0025": 89,
            "s_card-3-0001": 88,
            "s_card-3-0007": 86,
            "s_card-3-0019": 48,
            "s_card-3-0023": 88,
            "s_card-3-0069": 87,
        },
    },
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-sp-02",
        "step_type": "Lesson Visual SP",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VISUAL_SP",
        "seed": 256530169,
        "stamina": 39,
        "vocal": 582,
        "dance": 407,
        "visual": 560,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_sp-after_mid",
        "support_permil": {"s_card-3-0025": 91, "s_card-3-0007": 88},
    },
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-normal-04",
        "step_type": "Lesson Vocal NORMAL",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VOCAL_NORMAL",
        "seed": 1072633962,
        "stamina": 39,
        "vocal": 592,
        "dance": 562,
        "visual": 796,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_normal-after_mid",
        "support_permil": {"s_card-3-0001": 90, "s_card-3-0023": 90},
    },
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-sp-04",
        "step_type": "Lesson Visual SP",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VISUAL_SP",
        "seed": 1750782030,
        "stamina": 40,
        "vocal": 761,
        "dance": 562,
        "visual": 798,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_sp-after_mid",
        "support_permil": {"s_card-3-0025": 101, "s_card-3-0007": 98},
    },
    {
        "step_id": "p_step_lesson-produce-002-exam_full_power_01-hard-02",
        "step_type": "Lesson Vocal HARD",
        "wire_step_type": "PRODUCE_STEP_TYPE_LESSON_VOCAL_HARD",
        "seed": 2012812433,
        "stamina": 43,
        "vocal": 771,
        "dance": 562,
        "visual": 1096,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-lesson_hard-after_mid",
        "support_permil": {"s_card-3-0001": 98, "s_card-3-0023": 98},
    },
    {
        "step_id": "AuditionFinal",
        "step_type": "Audition Final",
        "wire_step_type": "PRODUCE_STEP_TYPE_AUDITION_FINAL",
        "seed": 407116536,
        "stamina": 43,
        "vocal": 1211,
        "dance": 753,
        "visual": 1282,
        "bonus": 500,
        "gimmick": "p_exam_gimmick-produce-002-exam_full_power_01-audition-after_mid",
        "support_permil": {
            "s_card-3-0025": 101,
            "s_card-3-0001": 98,
            "s_card-3-0007": 98,
            "s_card-3-0019": 56,
            "s_card-3-0023": 98,
            "s_card-3-0069": 111,
        },
    },
)

R21_MEMORIES = (
    {
        "number": 1,
        "user_memory_id": "61d484f1-1330-429d-beb6-d63a0d4b3747",
        "is_rental": False,
        "idol_card_id": "i_card-hmsz-3-000",
        "produce_card": {"id": "p_card-03-act-3_053", "upgrade_count": 0},
        "phase_type": "END_AUDITION_MID",
        "item_ids": ["pitem_03-3-133-0"],
        "abilities": [{"id": R21_MEMORY_ABILITY}],
    },
    {
        "number": 2,
        "user_memory_id": "8d01e3ed-7ecd-4e52-8f05-e9a23c6cb7b2",
        "is_rental": False,
        "idol_card_id": "i_card-hmsz-3-000",
        "produce_card": {"id": "p_card-03-act-3_054", "upgrade_count": 1},
        "phase_type": "END_AUDITION_MID",
        "item_ids": ["pitem_03-3-133-0"],
        "abilities": [],
    },
    {
        "number": 3,
        "user_memory_id": "bf5d6c24-43bd-41c8-82fc-c66cbec24553",
        "is_rental": False,
        "idol_card_id": "i_card-ssmk-3-002",
        "produce_card": {"id": "p_card-03-men-2_066", "upgrade_count": 1},
        "phase_type": "END_AUDITION_MID",
        "item_ids": ["pitem_03-3-048-0", "pitem_00-3-119-0", "pitem_03-3-065-0"],
        "abilities": [],
        "customizes": [{"id": R21_PRIDE_CUSTOMIZE, "count": 1}],
    },
    {
        "number": 4,
        "user_memory_id": "bc35334c-9b90-4e90-9c21-fb8b5a8562ce",
        "is_rental": True,
        "idol_card_id": "i_card-hmsz-3-016",
        "produce_card": {"id": "p_card-03-men-3_058", "upgrade_count": 1},
        "phase_type": "END_AUDITION_MID",
        "item_ids": ["pitem_03-3-303-0", "pitem_00-3-059-0", "pitem_00-3-010-0"],
        "abilities": [],
        "customizes": [{"id": R21_BLOCK_CUSTOMIZE, "count": 1}],
    },
)


def default_r21_spec(run_seed: int | None = None) -> dict:
    """Return a mutable self-drive spec containing the observed R21 values."""

    return {
        "idol_card_id": "i_card-hmsz-3-000",
        "produce_id": "produce-002",
        "max_stamina": 43,
        "dearness_level": 37,
        "run_seed": run_seed,
        "_r21_observed": True,
        "supports": copy.deepcopy(list(R21_SUPPORTS)),
        "memories": copy.deepcopy(list(R21_MEMORIES)),
        "item_ids": [
            "pitem_03-3-133-0",
            "pitem_00-3-011-0",
            "pitem_03-3-007-0",
            "pitem_00-3-010-0",
        ],
        "deck_cards": copy.deepcopy(list(R21_BASE_DECK)),
        "steps": copy.deepcopy(list(R21_STEP_BLOCKS)),
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("decode_status", "decoded")).lower() not in {"decoded", "decode"}:
                continue
            rows.append(row)
    return rows


def _decoded(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("decoded", row)
    return value if isinstance(value, Mapping) else {}


def _updated_data(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("value") or {}
    response = value.get("common_response") if isinstance(value, Mapping) else {}
    updated = response.get("updated_data") if isinstance(response, Mapping) else {}
    return updated if isinstance(updated, Mapping) else {}


def _locator(name: str, detail: str) -> str:
    return f"{name}:{detail}"


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


class CorpusProvider:
    """Build a :class:`ProduceRun` from an explicitly supplied corpus copy."""

    def __init__(self, corpus_path: str | Path | None = None, *, corpus_dir: str | Path | None = None,
                 master_dir: str | Path | None = None):
        selected = corpus_path if corpus_path is not None else corpus_dir
        if selected is None:
            raise TypeError("CorpusProvider requires corpus_path")
        selected = Path(selected)
        if selected.is_file():
            selected = selected.parent
        self.corpus_path = selected
        self.corpus_dir = selected
        self.master_dir = Path(master_dir) if master_dir is not None else None
        self._run: ProduceRun | None = None
        self.server_draw: ServerDraw | None = None

    @property
    def run(self) -> ProduceRun:
        if self._run is None:
            if self.master_dir is None:
                raise RuntimeError("CorpusProvider.build(master_dir) is required before run is available")
            self.build(self.master_dir)
        assert self._run is not None
        return self._run

    def deck_cards_for(self, step_index: int) -> list[dict]:
        return self.run.deck_cards_for(step_index)

    def prepare_step(self, index: int, run: ProduceRun | None = None) -> ProduceStep:
        """Select the corpus-owned starting state for one segment."""

        run = run or self.run
        step = run.step(index)
        run.stamina = int(step.stamina)
        return step

    def _events(self) -> list[dict]:
        return _read_json(self.corpus_dir / "wire_selected_decoded.json")

    def _start_event(self, events: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
        for event in events:
            if int(event.get("event_sequence", -1)) == 7:
                return event
        raise KeyError("wire_selected_decoded event_sequence=7")

    def _history_event(self, events: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
        for event in events:
            if int(event.get("event_sequence", -1)) == 271:
                return event
        raise KeyError("wire_selected_decoded event_sequence=271")

    @staticmethod
    def _memory_rows(updated: Mapping[str, Any]) -> list[dict]:
        rows = updated.get("user_produce_progress_memory_list") or ()
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    @staticmethod
    def _support_rows(updated: Mapping[str, Any]) -> list[dict]:
        rows = updated.get("user_produce_progress_support_card_list") or ()
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def _wire_steps(self) -> list[dict]:
        rows = _read_jsonl(self.corpus_dir / "wire_step_starts.jsonl")
        out = []
        for row in rows:
            value = row.get("value") or {}
            progress = value.get("common_response", {}).get("updated_data", {}).get("user_produce_progress", {})
            if progress:
                out.append(progress)
        if len(out) != 9:
            raise ValueError(f"expected nine wire step starts, got {len(out)}")
        return out

    def _normalize_memory(self, row: Mapping[str, Any], roster_row: Mapping[str, Any] | None = None) -> dict:
        memory = dict(row.get("memory") or {})
        roster = dict(roster_row or {})
        roster_memory = dict(roster.get("memory") or {})
        produce_card = copy.deepcopy(memory.get("produce_card") or {})
        roster_card = roster_memory.get("produce_card") or {}
        if isinstance(roster_card, Mapping):
            for key in ("customizes", "upgrade_count"):
                if key in roster_card and key not in produce_card:
                    produce_card[key] = copy.deepcopy(roster_card[key])
        roster_abilities = roster["abilities"] if "abilities" in roster else memory.get("abilities")
        customizes = produce_card["customizes"] if "customizes" in produce_card else roster.get("customizes")
        result = {
            "number": int(row.get("number") or roster.get("number") or 0),
            "user_memory_id": str(row.get("user_memory_id") or memory.get("user_memory_id") or roster.get("user_memory_id") or ""),
            "is_rental": bool(row.get("is_rental") if "is_rental" in row else roster.get("is_rental")),
            "idol_card_id": memory.get("idol_card_id") or roster_memory.get("idol_card_id"),
            "produce_card": produce_card,
            "phase_type": memory.get("produce_card_phase_type") or roster_memory.get("produce_card_phase_type"),
            "produce_card_phase_type": memory.get("produce_card_phase_type") or roster_memory.get("produce_card_phase_type"),
            "item_ids": list(memory.get("exam_battle_produce_item_ids") or roster_memory.get("exam_battle_produce_item_ids") or ()),
            "abilities": copy.deepcopy(list(roster_abilities or ())),
            "customizes": copy.deepcopy(list(customizes or ())),
        }
        return result

    @staticmethod
    def _normalize_card(card: Mapping[str, Any]) -> dict:
        origin_type = card["origin_type"] if "origin_type" in card else 0
        result = {
            "number": int(card.get("number") or 0),
            "produce_card_id": str(card.get("produce_card_id") or ""),
            "base_upgrade_count": int(card.get("base_upgrade_count") or 0),
            "origin_type": None if origin_type is None else int(origin_type),
            "is_initial": bool(card.get("is_initial")),
            "customizes": copy.deepcopy(list(card.get("customizes") or ())),
        }
        for key in ("guid", "source", "customize_join_status", "current_upgrade_count"):
            if key in card:
                result[key] = copy.deepcopy(card[key])
        return result

    def _provenance(self, run: ProduceRun, memory_rows: list[dict], step_rows: list[dict]) -> None:
        for path in run.provenance_paths():
            if path.startswith("identity."):
                field_name = path.split(".", 1)[1]
                table = "IdolCard" if field_name in {
                    "idol_card_id", "plan_type", "exam_effect_type", "difficulty_id", "initial_deck_id",
                    "idol_produce_card_id", "base_vocal", "base_dance", "base_visual", "base_stamina",
                    "before_item_id", "after_item_id",
                } else "Produce"
                key = run.identity.idol_card_id if table == "IdolCard" else run.identity.produce_id
                run.provenance[path] = {"source": "a", "locator": f"master:{table}/{key} field={field_name}"}
            elif path.startswith("supports["):
                run.provenance[path] = {"source": "b", "locator": _locator("wire_selected_decoded.json", "event_sequence=7/updated_data.user_produce_progress_support_card_list")}
            elif path.startswith("memories["):
                detail = "event_sequence=7/updated_data.user_produce_progress_memory_list"
                if "customizes" in path:
                    detail += " unverified: identity, field 3 undecoded"
                elif "abilities" in path:
                    detail += " unverified: provider ability observed in scenario join"
                run.provenance[path] = {"source": "b", "locator": _locator("wire_selected_decoded.json", detail)}
            elif path.startswith("item_ids["):
                index = int(path.split("[", 1)[1].split("]", 1)[0])
                item = run.item_ids[index] if index < len(run.item_ids) else ""
                if item == "pitem_00-3-010-0":
                    locator = "event_sequence=271/produce_history plus inferred rental-memory item inclusion"
                else:
                    locator = "event_sequence=271/produce_history.produce_item_ids"
                run.provenance[path] = {"source": "b", "locator": _locator("wire_selected_decoded.json", locator)}
            elif path.startswith("deck_cards["):
                run.provenance[path] = {"source": "b", "locator": _locator("segment_deck.json", "segments[0]/cards")}
            elif path.startswith("deck_snapshots["):
                parts = path.split("[")
                segment_index = int(parts[1].split("]", 1)[0])
                card_index = int(parts[2].split("]", 1)[0])
                detail = f"segments[{segment_index}]/cards[{card_index}]"
                if "customizes" in path:
                    card_rows = run.deck_snapshots.get(segment_index, ())
                    card = card_rows[card_index] if card_index < len(card_rows) else {}
                    customizes = card.get("customizes") if isinstance(card, Mapping) else None
                    detail += "/customizes"
                    if customizes:
                        detail += " unverified: identity, field 3 undecoded"
                run.provenance[path] = {"source": "b", "locator": _locator("segment_deck.json", detail)}
            elif path.startswith("steps["):
                index = int(path.split("[", 1)[1].split("]", 1)[0])
                segment_id = step_rows[index].get("step_id", "") if index < len(step_rows) else ""
                if "support_permil" in path:
                    locator = f"segment_initial_state.json:records[{index}]/support_rows"
                else:
                    locator = f"wire_step_starts.jsonl:step_id={segment_id}"
                run.provenance[path] = {"source": "b", "locator": locator}
            elif path in {"stamina", "max_stamina", "vocal", "dance", "visual"}:
                run.provenance[path] = {"source": "b", "locator": "wire_step_starts.jsonl:step_id=p_step_lesson-produce-002-exam_full_power_01-normal-01/user_produce_progress"}
            else:
                raise AssertionError(f"unhandled corpus provenance path: {path}")

    def build(self, master_dir: str | Path | None = None, *, idol_card_id: str | None = None,
              produce_id: str | None = None) -> ProduceRun:
        selected_master = Path(master_dir or self.master_dir or (Path(__file__).resolve().parents[2] / "ro" / "master" / "tables"))
        events = self._events()
        start_event = self._start_event(events)
        history_event = self._history_event(events)
        start_updated = _updated_data(start_event)
        history = (history_event.get("value") or {}).get("produce_history") or {}
        memory_rows = self._memory_rows(start_updated)
        support_rows = self._support_rows(start_updated)
        roster_path = self.corpus_dir / "memory_list.json"
        roster_rows = {}
        if roster_path.exists():
            roster_data = _read_json(roster_path)
            for item in (roster_data.get("start", {}).get("memories", ()) if isinstance(roster_data, Mapping) else ()):
                if isinstance(item, Mapping) and item.get("user_memory_id"):
                    roster_rows[str(item["user_memory_id"])] = item
        wire_steps = self._wire_steps()
        first_progress = wire_steps[0]
        selected_idol_card_id = str(idol_card_id or first_progress.get("idol_card_id") or "")
        selected_produce_id = str(produce_id or first_progress.get("produce_id") or "")
        if not selected_idol_card_id or not selected_produce_id:
            raise KeyError("R21 step-start identity fields idol_card_id/produce_id are required")
        initial = _read_json(self.corpus_dir / "segment_initial_state.json")["records"]
        deck_segments = _read_json(self.corpus_dir / "segment_deck.json")["segments"]
        if len(initial) != 9 or len(deck_segments) != 9:
            raise ValueError("R21 corpus must contain nine segment records")

        identity = ProduceIdentity.from_master(selected_master, selected_idol_card_id, selected_produce_id)
        memories = [self._normalize_memory(row, roster_rows.get(str(row.get("user_memory_id") or "")))
                    for row in sorted(memory_rows, key=lambda row: int(row.get("number") or 0))]
        base_cards = [self._normalize_card(card) for card in deck_segments[0].get("cards", ())]
        steps: list[ProduceStep] = []
        observed: dict[tuple[str, Any], int] = {}
        for index, (progress, state) in enumerate(zip(wire_steps, initial), 1):
            if progress.get("step_id") != state.get("segment_id"):
                raise ValueError(f"wire/state step mismatch at {index}")
            support_permil = {str(row["support_row_id"]): int(row["permil"]) for row in state.get("support_rows", ()) if row.get("support_row_id")}
            step = ProduceStep(
                index=index,
                step_type=str(state.get("segment_type") or progress.get("step_type") or ""),
                step_id=str(progress.get("step_id") or state.get("segment_id") or ""),
                exam_seed_value=int(_first_present(progress.get("exam_seed_value"), state.get("exam_seed_value"), 0)),
                gimmick_group_id=progress.get("produce_exam_gimmick_effect_group_id"),
                stamina=int(_first_present(progress.get("stamina"), state.get("starting_stamina"), 0)),
                max_stamina=int(_first_present(progress.get("max_stamina"), history.get("max_stamina"), 0)),
                vocal=int(_first_present(progress.get("vocal"), 0)),
                dance=int(_first_present(progress.get("dance"), 0)),
                visual=int(_first_present(progress.get("visual"), 0)),
                support_permil=support_permil,
                audition_bonus_permil=int(_first_present(progress.get("audition_effect_parameter_bonus_permil"), 0)),
                vote_bonus_permil=int(_first_present(progress.get("vote_bonus_permil"), 0)),
            )
            steps.append(step)
            observed[("exam_seed", index)] = step.exam_seed_value

        items = list(history.get("produce_item_ids") or ())
        rental_items = [item for memory in memories if memory.get("is_rental") for item in memory.get("item_ids", ())]
        if "pitem_00-3-010-0" in rental_items and "pitem_00-3-010-0" not in items:
            items.append("pitem_00-3-010-0")

        run = ProduceRun(
            identity=identity,
            supports=copy.deepcopy(support_rows),
            memories=memories,
            item_ids=items,
            deck_cards=base_cards,
            stamina=steps[0].stamina,
            max_stamina=steps[0].max_stamina,
            vocal=steps[0].vocal,
            dance=steps[0].dance,
            visual=steps[0].visual,
            steps=steps,
        )
        snapshots = {}
        for index, segment in enumerate(deck_segments, 1):
            cards = [self._normalize_card(card) for card in segment.get("cards", ())]
            for card in cards:
                if card["produce_card_id"] == "p_card-03-men-2_066":
                    card["customizes"] = [{"id": R21_PRIDE_CUSTOMIZE, "count": 1}]
                elif card["produce_card_id"] == "p_card-03-men-3_058":
                    card["customizes"] = [{"id": R21_BLOCK_CUSTOMIZE, "count": 1}]
            snapshots[index] = cards
        run.deck_snapshots = snapshots
        run.dearness_level = first_progress.get("start_dearness_level")
        run.provenance["dearness_level"] = {
            "source": "b",
            "locator": "wire_step_starts.jsonl:step_index=1/user_produce_progress.start_dearness_level",
        }
        observed_ability = next((a.get("id") for a in memories[0].get("abilities", ())
                                 if isinstance(a, Mapping) and "exam_add_grow_effect-02" in str(a.get("id", ""))), None)
        if observed_ability:
            for index in (1, 6):
                observed[("memory_ability", (memories[0]["user_memory_id"], observed_ability, index))] = True
        self.server_draw = ServerDraw(0, observed)
        run.server_draw = self.server_draw
        run._provider = self
        self._provenance(run, memory_rows, wire_steps)
        run.assert_provenance_complete()
        self._run = run
        self.master_dir = selected_master
        return run


__all__ = [
    "CorpusProvider",
    "R21_MEMORY_ABILITY",
    "R21_PRIDE_CUSTOMIZE",
    "R21_BLOCK_CUSTOMIZE",
    "default_r21_spec",
]
